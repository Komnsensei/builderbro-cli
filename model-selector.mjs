#!/usr/bin/env node
import { execSync } from "child_process";
import { existsSync, readFileSync, writeFileSync, mkdirSync } from "fs";
import { dirname, join } from "path";

export const DEFAULT_VERTEX_MODEL = "gemini-2.5-flash";
export const MODEL_PREFERENCE_ORDER = [
  "gemini-2.5-pro",
  "gemini-2.5-flash",
  "gemini-2.0-flash",
  "gemini-1.5-pro",
  "gemini-1.5-flash"
];

function command(commandRunner, command) {
  try {
    return String(commandRunner(command, {
      encoding: "utf8",
      timeout: 15000,
      stdio: ["ignore", "pipe", "ignore"]
    }) || "").trim();
  } catch (_) {
    return "";
  }
}

export function detectGoogleCloudContext(options = {}) {
  const env = options.env || process.env;
  const commandRunner = options.execSync || execSync;
  const token = env.VERTEX_OAUTH_TOKEN || command(commandRunner, "gcloud auth print-access-token");
  const projectId = env.GCP_PROJECT_ID || env.GOOGLE_CLOUD_PROJECT || command(commandRunner, "gcloud config get-value project");
  const region = env.GCP_REGION || env.GOOGLE_CLOUD_LOCATION || "us-central1";
  return {
    token,
    projectId: projectId && projectId !== "(unset)" ? projectId : "",
    region
  };
}

function modelId(model) {
  const raw = model && (model.name || model.model || model.id || "");
  return String(raw).split("/").pop();
}

function supportsTextGeneration(model) {
  const actions = model && (model.supportedActions || model.supportedGenerationMethods || model.supportedActionsInfo);
  if (!actions) return true;
  const text = JSON.stringify(actions);
  return /generateContent|streamGenerateContent|textGeneration|generate/i.test(text);
}

export function normalizeVertexModels(payload) {
  const raw = payload && (payload.publisherModels || payload.models || payload.data || []);
  const list = Array.isArray(raw) ? raw : [];
  const seen = new Set();
  return list.map(function(model) {
    const id = modelId(model);
    if (!id || seen.has(id) || !supportsTextGeneration(model)) return null;
    seen.add(id);
    return {
      id,
      name: model.displayName || model.name || id,
      description: model.description || "",
      raw: model
    };
  }).filter(Boolean);
}

function endpoint(context, version) {
  return "https://" + context.region + "-aiplatform.googleapis.com/" + version +
    "/projects/" + encodeURIComponent(context.projectId) +
    "/locations/" + encodeURIComponent(context.region) +
    "/publishers/google/models?pageSize=100";
}

export async function discoverVertexModels(options = {}) {
  const context = options.context || detectGoogleCloudContext(options);
  const fetchImpl = options.fetch || globalThis.fetch;
  if (!context.token || !context.projectId) {
    throw new Error("Google Cloud credentials or project are not configured");
  }
  if (typeof fetchImpl !== "function") throw new Error("fetch is unavailable");

  let lastError;
  for (const version of ["v1", "v1beta1"]) {
    try {
      const response = await fetchImpl(endpoint(context, version), {
        method: "GET",
        headers: { Authorization: "Bearer " + context.token }
      });
      if (!response.ok) {
        throw new Error("Vertex model discovery API " + response.status);
      }
      const models = normalizeVertexModels(await response.json());
      return { context, models, endpointVersion: version };
    } catch (error) {
      lastError = error;
    }
  }
  throw lastError || new Error("Vertex model discovery failed");
}

export function chooseVertexModel(models, requested) {
  const available = Array.isArray(models) ? models : [];
  if (requested) {
    const exact = available.find(function(model) {
      return model.id === requested || model.id.startsWith(requested + "-");
    });
    if (exact) return exact.id;
    if (!available.length) return requested;
  }
  for (const preferred of MODEL_PREFERENCE_ORDER) {
    const match = available.find(function(model) {
      return model.id === preferred || model.id.startsWith(preferred + "-");
    });
    if (match) return match.id;
  }
  return available.length ? available[0].id : (requested || DEFAULT_VERTEX_MODEL);
}

export function loadModelPreference(filePath) {
  if (!filePath || !existsSync(filePath)) return "";
  try {
    const value = JSON.parse(readFileSync(filePath, "utf8"));
    return typeof value.model === "string" ? value.model.trim() : "";
  } catch (_) {
    return "";
  }
}

export function saveModelPreference(filePath, model) {
  if (!filePath || !model) return;
  mkdirSync(dirname(filePath), { recursive: true });
  writeFileSync(filePath, JSON.stringify({ model, updatedAt: new Date().toISOString() }, null, 2) + "\n", "utf8");
}

export function formatModelList(models, activeModel) {
  if (!models || !models.length) return "  No Vertex text models were returned.";
  return models.map(function(model, index) {
    const marker = model.id === activeModel ? "*" : " ";
    return "  " + marker + " [" + (index + 1) + "] " + model.id;
  }).join("\n");
}

export function modelPreferencePath(dataDir) {
  return join(dataDir, "model.json");
}

// ─────────────────────────────────────────────────────────────────────
// Local open-weight backend (Ollama / vLLM / llama.cpp)
// Any OpenAI-compatible /chat/completions server works. When configured,
// BRO runs entirely offline — zero cloud calls. This is Phase 0 of the
// Free Brain architecture (see FREE-BRAIN.md): the seam where a local
// open-weight brain replaces the proprietary Vertex/OpenAI dependency.
// ─────────────────────────────────────────────────────────────────────

export const LOCAL_MODEL_DEFAULT_URL = "http://127.0.0.1:11434/v1";

// Resolve local backend config from env. Returns null when not configured.
// LOCAL_MODEL_URL wins; otherwise OPENAI_BASE_URL is honored when it targets
// localhost (a local server, not a cloud provider).
export function localModelConfig(env = process.env) {
  var url = (env.LOCAL_MODEL_URL || "").trim();
  var fallback = (env.OPENAI_BASE_URL || "").trim();
  var base = url || (/^(https?:\/\/)?(localhost|127\.0\.0\.1|\[::1\])/i.test(fallback) ? fallback : "");
  if (!base) return null;
  return {
    url: base.replace(/\/+$/, ""),
    model: (env.LOCAL_MODEL || env.OPENAI_MODEL || "").trim(),
    key: (env.LOCAL_API_KEY || "local").trim(),
    timeoutMs: parseInt(env.LOCAL_MODEL_TIMEOUT_MS || "180000", 10)
  };
}

export function localModelLabel(cfg) {
  if (!cfg) return "";
  return cfg.url.replace(/^https?:\/\//, "") + (cfg.model ? " / " + cfg.model : "");
}

// Vertex chat history is [{role, parts:[{text}]}]; OpenAI wants [{role, content}].
// Normalize both shapes (and plain strings), prepending the system instruction.
export function toOpenAIMessages(chatHistory, system) {
  var out = [];
  if (system) out.push({ role: "system", content: system });
  (Array.isArray(chatHistory) ? chatHistory : []).forEach(function(m) {
    if (typeof m === "string") { out.push({ role: "user", content: m }); return; }
    if (m && Array.isArray(m.parts)) {
      var text = m.parts.map(function(p){ return p && p.text != null ? p.text : ""; }).join("\n");
      out.push({ role: m.role === "assistant" ? "assistant" : "user", content: text });
    } else if (m && typeof m.content === "string") {
      out.push({ role: m.role === "assistant" ? "assistant" : "user", content: m.content });
    }
  });
  return out;
}

// One chat completion against a local OpenAI-compatible server. Returns the
// same shape as cli.mjs askChat: { content, elapsed, tokens }.
export async function chatLocal(cfg, chatHistory, options = {}) {
  var body = {
    messages: toOpenAIMessages(chatHistory, options.system),
    temperature: options.temperature != null ? options.temperature : 0.2,
    max_tokens: options.maxTokens || 2048,
    stream: false
  };
  if (cfg.model) body.model = cfg.model;
  var timeoutSignal = AbortSignal.timeout(cfg.timeoutMs);
  var started = Date.now();
  var r;
  try {
    r = await fetch(cfg.url + "/chat/completions", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + cfg.key },
      body: JSON.stringify(body),
      signal: options.signal ? AbortSignal.any([options.signal, timeoutSignal]) : timeoutSignal
    });
  } catch (e) {
    var err = new Error("Local model unreachable at " + cfg.url + " (" + e.message + "). Start Ollama/vLLM/llama.cpp, or unset LOCAL_MODEL_URL to go back to the cloud path.");
    err.local = true;
    throw err;
  }
  if (!r.ok) {
    var detail = "";
    try { detail = await r.text(); } catch (_) {}
    var err2 = new Error("Local model HTTP " + r.status + (detail ? " - " + detail.slice(0, 300) : "") + (cfg.model ? "" : " (no LOCAL_MODEL set — the server may require a model name)"));
    err2.local = true;
    throw err2;
  }
  var d = await r.json();
  var content = (d.choices && d.choices[0] && d.choices[0].message && d.choices[0].message.content) || "";
  var tokens = (d.usage && (d.usage.completion_tokens || 0)) || 0;
  return { content: content || "No response", elapsed: ((Date.now() - started) / 1000).toFixed(1), tokens: tokens };
}

// Health check for the heartbeat / status display: GET {url}/models.
export async function pingLocal(cfg, signal) {
  try {
    var r = await fetch(cfg.url + "/models", {
      headers: { "Authorization": "Bearer " + cfg.key },
      signal: signal || AbortSignal.timeout(10000)
    });
    var ok = r.status < 400;
    var models = [];
    if (ok) {
      try {
        var d = await r.json();
        models = (d.data || []).map(function(m){ return m.id; });
      } catch (_) {}
    }
    return { ok: ok, status: r.status, models: models.slice(0, 8) };
  } catch (e) {
    return { ok: false, status: 0, models: [] };
  }
}
