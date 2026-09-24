#!/usr/bin/env node
// bro-loader.mjs — the real entry point (point your 'bro' alias here).
//   Interactive:  node loader.mjs            -> wires bromance.mjs into
//                    globalThis.__bromance, then runs cli1.mjs (the agent).
//   Pipe mode:    echo "some code" | bro "fix this"  -> sends the piped
//                    content to the LLM brain (Google Cloud Vertex -> Base44 -> Groq -> OpenAI)
//                    with the instruction, prints the reply, exits.
// Keys come from the environment or a .env file (cwd, script dir, ~/.bro).
// No secrets are hardcoded here — gcloud credentials are preferred; fallback keys are optional.

import { readFileSync, existsSync } from "fs";
import { execSync } from "child_process";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import { homedir } from "os";
import { detectGoogleCloudContext, discoverVertexModels, chooseVertexModel, loadModelPreference, saveModelPreference, modelPreferencePath, localModelConfig, chatLocal } from "./model-selector.mjs";

// Minimal .env loader (same rules as cli1.mjs: real env vars always win).
(function loadDotEnv(){
  var candidates = [
    join(process.cwd(), ".env"),
    join(dirname(fileURLToPath(import.meta.url)), ".env"),
    join(homedir(), ".bro", ".env")
  ];
  for (var i=0;i<candidates.length;i++){
    var seen = {};
    try{
      if(!existsSync(candidates[i])) continue;
      readFileSync(candidates[i], "utf8").split(/\r?\n/).forEach(function(line){
        line = line.trim();
        if(!line || line.startsWith("#") || seen[line]) return;
        seen[line] = true;
        line = line.replace(/^export\s+/,"");
        var eq = line.indexOf("=");
        if(eq === -1) return;
        var k = line.substring(0,eq).trim();
        var v = line.substring(eq+1).trim().replace(/^["']|["']$/g,"");
        if(k && !process.env[k]) process.env[k] = v;
      });
    }catch(e){ /* unreadable .env - skip */ }
  }
})();

var APP  = process.env.BASE44_APP_ID  || "69d81ac3ffa24327b49b171a";
var CONV = process.env.BASE44_CONV_ID || "69f9a8f3e048816e89717604";
var TOKEN = process.env.BASE44_TOKEN   || "";
// "GROQ_KEY" is the legacy name; .env and .env.example document GROQ_API_KEY
// (with numbered siblings _2.._9 and an optional csv GROQ_API_KEYS). Only the
// legacy name used to be read here, so a correctly-configured .env was ignored
// and the loader silently fell through to the next provider.
var GROQ_KEY = [process.env.GROQ_KEY, process.env.GROQ_API_KEYS, process.env.GROQ_API_KEY]
  .concat([2,3,4,5,6,7,8,9].map(function(n){ return process.env["GROQ_API_KEY_"+n]; }))
  .map(function(v){ return String(v||"").split(",")[0].trim(); })
  .filter(Boolean)[0] || "";
var OPENAI_KEY = process.env.OPENAI_API_KEY || "";
var MODEL_PREFERENCE_F = modelPreferencePath(join(homedir(), ".bro"));
var SELECTED_VERTEX_MODEL = process.env.GCP_MODEL || loadModelPreference(MODEL_PREFERENCE_F) || "";
var MODEL_DISCOVERY_PROMISE = null;
async function activeVertexModel(){
  if(SELECTED_VERTEX_MODEL) return SELECTED_VERTEX_MODEL;
  if(MODEL_DISCOVERY_PROMISE) return MODEL_DISCOVERY_PROMISE;
  MODEL_DISCOVERY_PROMISE=(async function(){
    var context=detectGoogleCloudContext();
    if(!context.token||!context.projectId) return "gemini-2.5-flash";
    try{
      var result=await discoverVertexModels({context:context});
      SELECTED_VERTEX_MODEL=chooseVertexModel(result.models,process.env.GCP_MODEL||"");
      if(!process.env.GCP_MODEL)saveModelPreference(MODEL_PREFERENCE_F,SELECTED_VERTEX_MODEL);
      return SELECTED_VERTEX_MODEL;
    }catch(_){ return "gemini-2.5-flash"; }
  })();
  return MODEL_DISCOVERY_PROMISE;
}

function sleep(ms){ return new Promise(function(ok){ setTimeout(ok, ms); }); }

// gcloud / Vertex AI — the user's preferred brain. Auto-detects token + project
// from the gcloud CLI (or VERTEX_OAUTH_TOKEN / GCP_PROJECT_ID env vars).
var VERTEX_TOKEN = process.env.VERTEX_OAUTH_TOKEN || "";
var GCP_PROJECT = process.env.GCP_PROJECT_ID || "";
function gcloudToken(){ try{ return execSync("gcloud auth print-access-token", {encoding:"utf8", timeout:15000, stdio:["ignore","pipe","ignore"]}).trim(); }catch(e){ return ""; } }
function gcloudProject(){ try{ var p = execSync("gcloud config get-value project", {encoding:"utf8", timeout:15000, stdio:["ignore","pipe","ignore"]}).trim(); return (p && p !== "(unset)") ? p : ""; }catch(e){ return ""; } }
async function askVertex(prompt, ret){
  if(!VERTEX_TOKEN) VERTEX_TOKEN = gcloudToken();
  if(!GCP_PROJECT) GCP_PROJECT = gcloudProject();
  if(!VERTEX_TOKEN || !GCP_PROJECT) throw new Error("gcloud not configured - run 'gcloud auth login' && 'gcloud config set project YOUR_PROJECT_ID'");
  var REGION = process.env.GCP_REGION || "us-central1";
  var MODEL = await activeVertexModel();
  var url = "https://"+REGION+"-aiplatform.googleapis.com/v1/projects/"+GCP_PROJECT+"/locations/"+REGION+"/publishers/google/models/"+MODEL+":generateContent";
  for(var i=0;i<ret;i++){
    try{
      var r = await fetch(url, {
        method:"POST",
        headers:{"Content-Type":"application/json","Authorization":"Bearer "+VERTEX_TOKEN},
        body:JSON.stringify({ contents:[{ role:"user", parts:[{ text: prompt }] }] }),
        signal: AbortSignal.timeout(120000)
      });
      if(r.status===401){ var fresh = gcloudToken(); if(fresh){ VERTEX_TOKEN = fresh; continue; } }
      if(r.status===429||r.status>=500){ await sleep((i+1)*3000); continue; }
      if(!r.ok) throw new Error("Vertex API "+r.status);
      var d = await r.json();
      return (d.candidates && d.candidates[0] && d.candidates[0].content && d.candidates[0].content.parts[0]) ? d.candidates[0].content.parts[0].text : "No response";
    }catch(e){ if(i===ret-1) throw e; await sleep((i+1)*2000); }
  }
  throw new Error("Vertex failed");
}

async function askBase44(prompt, ret){
  for(var i=0;i<ret;i++){
    try{
      var r = await fetch("https://base44.app/api/apps/"+APP+"/agents/conversations/v2/"+CONV+"/messages", {
        method:"POST",
        headers:{"Content-Type":"application/json","X-App-Id":APP,"Authorization":"Bearer "+TOKEN},
        body:JSON.stringify({ role:"user", content: prompt.substring(0,50000) }),
        signal: AbortSignal.timeout(120000)
      });
      if(r.status===429||r.status>=500){ await sleep((i+1)*3000); continue; }
      if(!r.ok) throw new Error("Base44 API "+r.status);
      var d = await r.json();
      return d.content || d.message || "No response";
    }catch(e){ if(i===ret-1) throw e; await sleep((i+1)*2000); }
  }
  throw new Error("Base44 failed");
}

async function askOpenAICompat(baseUrl, key, prompt, ret, model){
  // The model has to match the provider being called: sending OpenAI's
  // "gpt-4o-mini" to the Groq endpoint is a 404/400, not an answer.
  var chosenModel = model || process.env.OPENAI_MODEL || "gpt-4o-mini";
  for(var i=0;i<ret;i++){
    try{
      var r = await fetch(baseUrl.replace(/\/$/,"")+"/chat/completions", {
        method:"POST",
        headers:{"Content-Type":"application/json","Authorization":"Bearer "+key},
        body:JSON.stringify({ model: chosenModel, messages:[{role:"user",content:prompt}] }),
        signal: AbortSignal.timeout(120000)
      });
      if(r.status===429||r.status>=500){ await sleep((i+1)*3000); continue; }
      if(!r.ok) throw new Error("API "+r.status);
      var d = await r.json();
      return (d.choices && d.choices[0] && d.choices[0].message && d.choices[0].message.content) || "No response";
    }catch(e){ if(i===ret-1) throw e; await sleep((i+1)*2000); }
  }
  throw new Error("API failed");
}

// Local open-weight backend (Ollama / vLLM / llama.cpp) — fully offline,
// zero cloud API calls. Same OpenAI-compatible contract as askOpenAICompat.
async function askLocal(prompt, ret){
  var cfg = localModelConfig();
  if(!cfg) throw new Error("LOCAL_MODEL_URL not set");
  for(var i=0;i<ret;i++){
    try{
      return (await chatLocal(cfg, [{role:"user", parts:[{text:prompt}]}], { maxTokens: 4096 })).content;
    }catch(e){ if(i===ret-1) throw e; await sleep((i+1)*2000); }
  }
  throw new Error("Local model failed");
}

async function pipeAnswer(instruction, content){
  var prompt = "User piped content through BRO with instruction: "+instruction+"\n\nCONTENT:\n"+content.substring(0,30000);
  var lastErr = null;
  // Priority: local open-weight first when configured (fully offline, zero
  // cloud calls — Phase 0 of FREE-BRAIN.md), then gcloud/Vertex, Base44, Groq, OpenAI.
  if (localModelConfig())     { try { return await askLocal(prompt, 2); } catch(e){ lastErr = e; } }
  try{ return await askVertex(prompt, 2); }catch(e){ lastErr = e; }
  if (TOKEN)            { try { return await askBase44(prompt, 2); } catch(e){ lastErr = e; } }
  if (GROQ_KEY)         { try { return await askOpenAICompat("https://api.groq.com/openai/v1", GROQ_KEY, prompt, 2, process.env.GROQ_MODEL || "openai/gpt-oss-120b"); } catch(e){ lastErr = e; } }
  if (OPENAI_KEY)       { try { return await askOpenAICompat(process.env.OPENAI_BASE_URL || "https://api.openai.com/v1", OPENAI_KEY, prompt, 2); } catch(e){ lastErr = e; } }
  console.error("No LLM brain available. Start a local open-weight server and set LOCAL_MODEL_URL (see .env.example), set up gcloud (gcloud auth login && gcloud config set project ID), or set BASE44_TOKEN / GROQ_API_KEY / OPENAI_API_KEY (env or .env). "+(lastErr?("Last error: "+lastErr.message):""));
  process.exit(1);
}

// Pipe mode only when there's a real (non-flag) instruction argument -
// flags like --skip-intro belong to the interactive agent below.
var pipeInstruction = (process.argv[2] && !process.argv[2].startsWith("-")) ? process.argv[2] : null;

if (!process.stdin.isTTY && pipeInstruction){
  var chunks = [];
  process.stdin.on("data", function(d){ chunks.push(d); });
  process.stdin.on("end", async function(){
    var piped = Buffer.concat(chunks).toString();
    try{
      var reply = await pipeAnswer(pipeInstruction, piped);
      console.log(reply);
    }catch(e){
      console.error("Error: "+e.message);
      process.exit(1);
    }
    process.exit(0);
  });
} else {
  // Store bromance globals for the main script, then run it.
  var bromance = await import("./bromance.mjs");
  globalThis.__bromance = Object.assign({}, bromance, bromance.default || {});
  await import("./cli.mjs");
}
