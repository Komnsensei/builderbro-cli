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
