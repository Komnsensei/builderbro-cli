import express from "express";
import { timingSafeEqual } from "node:crypto";
import { env } from "./config/env.js";
import { createMcpServer, createHttpTransport } from "./server.js";
import { ActionableError } from "./utils/errors.js";

/** Constant-time bearer-token check; empty configured token disables auth. */
function tokenOk(req: express.Request): boolean {
  const expected = env.mcpToken;
  if (!expected) return true; // auth disabled (local dev / stdio)
  const provided = req.headers.authorization ?? "";
  if (!provided.startsWith("Bearer ")) return false;
  const got = provided.slice(7).trim();
  const a = Buffer.from(got, "utf8");
  const b = Buffer.from(expected, "utf8");
  return a.length === b.length && timingSafeEqual(a, b);
}

async function main() {
  const mode = process.env.NEXUS_TRANSPORT ?? "http";

  if (mode === "stdio") {
    const { StdioServerTransport } = await import(
      "@modelcontextprotocol/sdk/server/stdio.js"
    );
    const server = createMcpServer();
    await server.connect(new StdioServerTransport());
    console.error("PassionState Nexus listening on stdio");
    return;
  }

  const app = express();
  app.use(express.json({ limit: "2mb" }));

  // Stateless mode: each request gets its own transport + server instance.
  app.post("/mcp", async (req, res) => {
    if (!tokenOk(req)) {
      res.status(401).json({ ok: false, error: "unauthorized" });
      return;
    }
    try {
      const transport = createHttpTransport();
      const server = createMcpServer();
      await server.connect(transport as Parameters<typeof server.connect>[0]);
      await transport.handleRequest(req, res, req.body);
      res.on("close", () => {
        transport.close();
        server.close();
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Unknown error";
      const status = error instanceof ActionableError ? 422 : 500;
      res.status(status).json({ ok: false, error: message });
    }
  });

  // Honest health: states whether auth guards /mcp and whether the archive
  // ledger is durable or in-process mock. No capability is overstated.
  app.get("/health", (_req, res) => {
    res.json({
      ok: true,
      service: "passionstate-nexus",
      version: "0.1.0",
      constitutionalLayer: "active",
      archiveLedgerMode: env.archiveLedgerMode,
      mcpAuth: env.mcpToken ? "bearer" : "disabled",
      transport: "http",
    });
  });

  app.listen(env.port, () => {
    console.log(`PassionState Nexus listening on http://localhost:${env.port}`);
  });
}

main().catch((err) => {
  console.error("Fatal startup error:", err);
  process.exit(1);
});
