// End-to-end MCP smoke test: boots dist/index.js, performs the MCP
// initialize handshake over streamable HTTP, lists tools, and calls one.
// Responses may arrive as JSON or SSE; both are handled.
import { spawn } from "node:child_process";
import { once } from "node:events";

const PORT = process.env.E2E_PORT ?? "8091";
const BASE = `http://localhost:${PORT}`;

function wait(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function waitForHealth() {
  for (let i = 0; i < 40; i++) {
    try {
      const res = await fetch(`${BASE}/health`);
      if (res.ok) return await res.json();
    } catch {
      // not up yet
    }
    await wait(250);
  }
  throw new Error("server did not become healthy in time");
}

/** POST an MCP message; parse JSON or SSE response bodies into the JSON-RPC result. */
async function mcpPost(headers, body) {
  const res = await fetch(`${BASE}/mcp`, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "application/json, text/event-stream", ...headers },
    body: JSON.stringify(body)
  });
  const contentType = res.headers.get("content-type") ?? "";
  const sessionId = res.headers.get("mcp-session-id");
  const text = await res.text();
  let json = null;
  if (contentType.includes("text/event-stream")) {
    // SSE: take the last data: line that parses as JSON
    for (const line of text.split("\n")) {
      if (line.startsWith("data:")) {
        const payload = line.slice(5).trim();
        try {
          json = JSON.parse(payload);
        } catch {
          // keep last good parse
        }
      }
    }
  } else {
    json = text.trim() ? JSON.parse(text) : null; // 202 notifications return empty bodies
  }
  return { res, sessionId, json };
}

const server = spawn(process.execPath, ["dist/index.js"], {
  env: { ...process.env, PORT },
  stdio: ["ignore", "pipe", "pipe"]
});
server.stderr.on("data", d => process.stderr.write(`[server] ${d}`));

try {
  const health = await waitForHealth();
  console.log("health:", JSON.stringify(health));

  // 1. MCP initialize
  const init = await mcpPost({}, {
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: {
      protocolVersion: "2025-03-26",
      capabilities: {},
      clientInfo: { name: "psn-e2e", version: "0.0.1" }
    }
  });
  console.log("initialize:", init.json?.result?.serverInfo?.name, init.json?.result?.serverInfo?.version, "session:", init.sessionId ? "ok" : "stateless (none)");

  // No session header needed in stateless mode

  // 2. notifications/initialized
  await mcpPost({}, { jsonrpc: "2.0", method: "notifications/initialized" });

  // 3. tools/list
  const list = await mcpPost({}, { jsonrpc: "2.0", id: 2, method: "tools/list" });
  console.log("tools/list RAW:", JSON.stringify(list.json).slice(0, 300));
  const toolNames = list.json?.result?.tools?.map(t => t.name) ?? [];
  console.log(`tools/list: ${toolNames.length} tools`);
  console.log("  ", toolNames.join(", "));

  // 4. tools/call: issue a quantum pass
  const call = await mcpPost({}, {
    jsonrpc: "2.0",
    id: 3,
    method: "tools/call",
    params: {
      name: "passionstate_issue_quantum_pass",
      arguments: { humanId: "shawn_robertson", jurisdiction: "GLOBAL" }
    }
  });
  const sc = call.json?.result?.structuredContent;
  console.log("tools/call passionstate_issue_quantum_pass:");
  console.log("  pass:", sc?.quantumPass?.quantumPassId);
  console.log("  archiveReceipt:", sc?.archiveReceipt);

  assert(sc?.quantumPass?.quantumPassId, "expected a quantum pass id");
  assert(sc?.archiveReceipt, "expected an archive receipt");
  console.log("\nE2E PASS: full MCP round-trip works over HTTP");
} catch (err) {
  console.error("E2E FAIL:", err.message);
  process.exitCode = 1;
} finally {
  server.kill();
  await once(server, "exit").catch(() => {});
}

function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}
