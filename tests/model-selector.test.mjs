import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  chooseVertexModel,
  detectGoogleCloudContext,
  discoverVertexModels,
  formatModelList,
  loadModelPreference,
  normalizeVertexModels,
  saveModelPreference
} from "../model-selector.mjs";

const models = [
  { name: "publishers/google/models/gemini-2.0-flash", displayName: "Gemini 2 Flash" },
  { name: "publishers/google/models/gemini-2.5-pro", displayName: "Gemini 2.5 Pro" },
  { name: "publishers/google/models/embedding-001", supportedActions: ["embedContent"] }
];

assert.deepEqual(normalizeVertexModels({ publisherModels: models }).map(model => model.id), [
  "gemini-2.0-flash",
  "gemini-2.5-pro"
]);
assert.equal(chooseVertexModel(normalizeVertexModels({ publisherModels: models })), "gemini-2.5-pro");
assert.equal(chooseVertexModel([{ id: "gemini-2.5-flash-001" }], "gemini-2.5-flash"), "gemini-2.5-flash-001");
assert.match(formatModelList([{ id: "gemini-2.5-pro" }], "gemini-2.5-pro"), /\* \[1\] gemini-2\.5-pro/);

const calls = [];
const context = detectGoogleCloudContext({
  env: { GCP_REGION: "europe-west4" },
  execSync(command) {
    calls.push(command);
    return command.includes("access-token") ? "token-123\n" : "project-456\n";
  }
});
assert.deepEqual(context, { token: "token-123", projectId: "project-456", region: "europe-west4" });
assert.deepEqual(calls, ["gcloud auth print-access-token", "gcloud config get-value project"]);

const discovered = await discoverVertexModels({
  context,
  fetch: async (url, options) => {
    assert.match(url, /europe-west4-aiplatform\.googleapis\.com\/v1\//);
    assert.equal(options.headers.Authorization, "Bearer token-123");
    return { ok: true, async json() { return { publisherModels: models }; } };
  }
});
assert.equal(discovered.models.length, 2);

const dir = mkdtempSync(join(tmpdir(), "bro-model-selector-"));
const preferencePath = join(dir, "model.json");
saveModelPreference(preferencePath, "gemini-2.5-pro");
assert.equal(loadModelPreference(preferencePath), "gemini-2.5-pro");
rmSync(dir, { recursive: true, force: true });

console.log("model-selector: 7 assertions passed");
