#!/usr/bin/env node
// model-selector-test.mjs — zero-dependency tests for the local open-weight
// backend added to model-selector.mjs (Phase 0 of FREE-BRAIN.md).
//   node model-selector-test.mjs
// No network beyond a localhost stub server; no disk writes outside a tmp dir.
import { createServer } from "http";
import { localModelConfig, chatLocal, pingLocal, toOpenAIMessages } from "./model-selector.mjs";

var pass = 0, fail = 0;
function ok(name, cond) {
  if (cond) { pass++; }
  else { fail++; console.log("  FAIL: " + name); }
}

// ── config resolution ──────────────────────────────────────────────
function withEnv(env, fn) {
  var prev = {};
  Object.keys(env).forEach(function(k){ prev[k] = process.env[k]; });
  Object.keys(env).forEach(function(k){
    if (env[k] === null || env[k] === undefined) delete process.env[k];
    else process.env[k] = env[k];
  });
  try { return fn(); }
  finally {
    Object.keys(prev).forEach(function(k){
      if (prev[k] === undefined) delete process.env[k];
      else process.env[k] = prev[k];
    });
  }
}

ok("no config when nothing set", withEnv({ LOCAL_MODEL_URL: null, OPENAI_BASE_URL: null, LOCAL_MODEL: null, OPENAI_MODEL: null }, function(){
  return localModelConfig() === null;
}));

var cfg1 = withEnv({ LOCAL_MODEL_URL: "http://127.0.0.1:11434/v1/", OPENAI_BASE_URL: null, LOCAL_MODEL: "qwen2.5-coder:7b" }, function(){
  return localModelConfig();
});
ok("explicit LOCAL_MODEL_URL is used (trailing slash stripped)", cfg1 && cfg1.url === "http://127.0.0.1:11434/v1" && cfg1.model === "qwen2.5-coder:7b");

var cfg2 = withEnv({ LOCAL_MODEL_URL: null, OPENAI_BASE_URL: "http://localhost:8797/v1", OPENAI_MODEL: "local-model" }, function(){
  return localModelConfig();
});
ok("localhost OPENAI_BASE_URL is honored as local", cfg2 && cfg2.url === "http://localhost:8797/v1" && cfg2.model === "local-model");

var cfg3 = withEnv({ LOCAL_MODEL_URL: null, OPENAI_BASE_URL: "https://api.openai.com/v1" }, function(){
  return localModelConfig();
});
ok("remote OPENAI_BASE_URL is NOT treated as local", cfg3 === null);

// ── message normalization ──────────────────────────────────────────
var msgs = toOpenAIMessages([
  { role: "user", parts: [{ text: "hello" }, { text: " world" }] },
  { role: "assistant", parts: [{ text: "hi" }] },
  { role: "user", content: "already openai" },
  "plain string"
], "SYS");
ok("system instruction is first", msgs[0] && msgs[0].role === "system" && msgs[0].content === "SYS");
ok("parts[] joined into content", msgs[1] && msgs[1].role === "user" && msgs[1].content === "hello\n world");
ok("assistant role preserved", msgs[2] && msgs[2].role === "assistant" && msgs[2].content === "hi");
ok("openai-shaped messages pass through", msgs[3] && msgs[3].role === "user" && msgs[3].content === "already openai");
ok("plain strings become user messages", msgs[4] && msgs[4].role === "user" && msgs[4].content === "plain string");

// ── stub OpenAI-compatible server ──────────────────────────────────
var seenBodies = [];
function startStub(handler) {
  return new Promise(function(resolve){
    var server = createServer(function(req, res){
      var chunks = [];
      req.on("data", function(d){ chunks.push(d); });
      req.on("end", function(){
        var body = Buffer.concat(chunks).toString();
        if (body) { try { seenBodies.push(JSON.parse(body)); } catch(_) {} }
        handler(req, res, body);
      });
    });
    server.listen(0, "127.0.0.1", function(){
      resolve({ server: server, port: server.address().port });
    });
  });
}

(async function(){
  // chatLocal happy path
  var stub = await startStub(function(req, res){
    if (req.url.endsWith("/chat/completions")) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({
        choices: [{ message: { role: "assistant", content: "local reply" } }],
        usage: { completion_tokens: 42 }
      }));
    } else {
      res.writeHead(404); res.end();
    }
  });
  var cfg = { url: "http://127.0.0.1:" + stub.port + "/v1", model: "test-model", key: "local", timeoutMs: 5000 };
  var reply = await chatLocal(cfg, [{ role: "user", parts: [{ text: "ping" }] }], { system: "S" });
  ok("chatLocal returns content", reply.content === "local reply");
  ok("chatLocal returns tokens", reply.tokens === 42);
  ok("chatLocal returns elapsed as string", typeof reply.elapsed === "string");
  ok("request sent model + system + max_tokens", seenBodies[0] && seenBodies[0].model === "test-model" && seenBodies[0].messages[0].role === "system" && seenBodies[0].max_tokens === 2048);

  // chatLocal error path
  var stubErr = await startStub(function(req, res){ res.writeHead(400, { "Content-Type": "text/plain" }); res.end("bad request"); });
  var threw = false;
  try {
    await chatLocal({ url: "http://127.0.0.1:" + stubErr.port + "/v1", model: "", key: "local", timeoutMs: 5000 }, [{ role: "user", parts: [{ text: "x" }] }]);
  } catch (e) { threw = /HTTP 400/.test(e.message) && /LOCAL_MODEL/.test(e.message); }
  ok("chatLocal 400 throws with LOCAL_MODEL hint", threw);

  // pingLocal happy path
  var stubPing = await startStub(function(req, res){
    if (req.url.endsWith("/models")) {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ data: [{ id: "a" }, { id: "b" }] }));
    } else { res.writeHead(404); res.end(); }
  });
  var ping = await pingLocal({ url: "http://127.0.0.1:" + stubPing.port + "/v1", key: "local" });
  ok("pingLocal ok with model list", ping.ok && ping.models.length === 2 && ping.models[0] === "a");

  // pingLocal failure path (nothing listening)
  var pingDown = await pingLocal({ url: "http://127.0.0.1:9/v1", key: "local" });
  ok("pingLocal reports down cleanly", pingDown.ok === false);

  [stub, stubErr, stubPing].forEach(function(s){ s.server.close(); });

  console.log("\n  " + pass + " passed, " + fail + " failed" + (fail ? "" : " — all green") + "\n");
  process.exit(fail ? 1 : 0);
})();