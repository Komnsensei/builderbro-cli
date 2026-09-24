#!/usr/bin/env node

import { readFileSync, writeFileSync, mkdirSync, readdirSync, statSync, existsSync, appendFileSync, unlinkSync, renameSync, copyFileSync } from "fs";
import { execSync } from "child_process";
import { createInterface, emitKeypressEvents } from "readline";
import { resolve, join, dirname, basename, extname } from "path";
import { homedir } from "os";
import { createServer } from "http";
import { createHash, randomUUID } from "crypto";
import { createRequire } from "module";
import { fileURLToPath } from "url";
import { buildWizard } from "./bro-build.mjs";
import { detectGoogleCloudContext, discoverVertexModels, chooseVertexModel, loadModelPreference, saveModelPreference, formatModelList, modelPreferencePath } from "./model-selector.mjs";

const require = createRequire(import.meta.url);

// ── .env loader ──────────────────────────────────────────────
// Auto-loads KEY=VALUE pairs so BRO finds its LLM keys without you
// exporting them. Checks, in order: current directory, this script's
// folder, then ~/.bro/.env. Real environment variables always win.
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
      SEARCH
readFileSync(candidates[i], "utf8").split(/\r?
/
REPLACE
/).forEach(function(line){
        line = line.trim();
        if(!line || line.startsWith("#") || seen[line]) return;
        seen[line] = true;
        line = line.replace(/^export\s+/,"");
        var eq = line.indexOf("=");
        if(eq === -1) return;
        var k = line.substring(0,eq).trim();
        var v = line.substring(eq+1).trim().replace(/^["']|["']$/g,"");
        if(!k) return;
        if(!process.env[k]) process.env[k] = v;
      });
    }catch(e){ /* unreadable .env - skip */ }
  }
})();

// bromance.mjs powers /bromance, /skills list, /chain, /brofile, etc. - all of it
// reads from globalThis.__bromance, but nothing was ever setting that global, so
// every one of those features has silently been a no-op. Load it for real here.
async function loadBromanceModule(){
  if (globalThis.__bromance && Object.keys(globalThis.__bromance).length > 0) {
    return; // loader.mjs (the intended entry point) already wired this up correctly
  }
  var candidates = ["./bromance.mjs", "./bro-ui/bromance.mjs", "./lib/bromance.mjs"];
  for (var i=0;i<candidates.length;i++){
    try{
      var mod = await import(candidates[i]);
      var flat = Object.assign({}, mod, mod.default || {});
      globalThis.__bromance = flat;
      console.log("  (loaded "+candidates[i]+" -> "+Object.keys(flat).length+" export(s) - you're running cli.mjs directly; run loader.mjs instead for pipe-mode support too)");
      return;
    }catch(e){ /* try next candidate */ }
  }
  console.log("  Warning: couldn't find bromance.mjs (tried "+candidates.join(", ")+"). /bromance, /chain, /brofile, /skills list will be limited until this is fixed.");
  globalThis.__bromance = {};
}
await loadBromanceModule();

  '/help', '/status', '/queue', '/memory', '/thought', 
  '/paste', '/exit', '/quit', '/build', '/skills', 
  '/auto', '/tg', '/chain', '/k1', '/tier', '/upgrade', '/models', '/model'
];
  } else if (input === "/skills list") {
    console.log(p("dim","  BRO: Here are your custom skills:"));
    if (customSkills.length === 0) {
      console.log(p("dim","    No custom skills yet. Use /skills create to make one."));
    } else {
      customSkills.forEach(function(s) {
        var status = s.enabled ? p("green","(enabled)") : p("red","(disabled)");
        console.log(p("cyan",`    ID: ${s.id}`) + ` - ${s.name} ${status}`);
        console.log(p("dim",`      Desc: ${s.desc}`));
        console.log(p("dim",`      Triggers: ${s.triggers.join(", ")}`));
        console.log(p("dim",`      Level: ${s.level}`));
        console.log(p("dim",`      Prompt: "${s.prompt.substring(0, 70)}..."`));
      });
    }
    showPrompt();
    return;
  } else if (input === "/skills create") {
    await skillsCreateWizard(rl);
    showPrompt();
    return;
  } else if (input.startsWith("/skills enable ")) {
    var skillId = input.substring(15).trim();
    var skill = customSkills.find(s => s.id === skillId);
    if (skill) {
      skill.enabled = true;
      saveCustomSkills();
      console.log(p("green",`  BRO: Skill '${skill.name}' enabled.`));
    } else {
      console.log(p("red",`  BRO: Skill ID '${skillId}' not found.`));
    }
    showPrompt();
    return;
  } else if (input.startsWith("/skills disable ")) {
    var skillId = input.substring(16).trim();
    var skill = customSkills.find(s => s.id === skillId);
    if (skill) {
      skill.enabled = false;
      saveCustomSkills();
      console.log(p("yellow",`  BRO: Skill '${skill.name}' disabled.`));
    } else {
      console.log(p("red",`  BRO: Skill ID '${skillId}' not found.`));
    }
    showPrompt();
    return;
  } else if (input.startsWith("/skills delete ")) {
    var skillId = input.substring(15).trim();
    var initialLength = customSkills.length;
    customSkills = customSkills.filter(s => s.id !== skillId);
    if (customSkills.length < initialLength) {
      saveCustomSkills();
      console.log(p("green",`  BRO: Skill '${skillId}' deleted.`));
    } else {
      console.log(p("red",`  BRO: Skill ID '${skillId}' not found.`));
    }
    showPrompt();
    return;
  } else if (input === "/auto list") {
REPLACE
  } else if (input.startsWith("/persona")) {
    if (input === "/persona wizard") {
      await personaWizard(rl);
      showPrompt();
      return;
    } else if (input === "/persona list") {
      console.log(p("dim","  BRO: Here are your custom personas:"));
      if (customPersonas.length === 0) {
        console.log(p("dim","    No custom personas yet. Use /persona wizard to create one."));
      } else {
          var status = p.enabled ? p("green","(enabled)") : p("red","(disabled)");
          console.log(p("cyan",`    ID: ${p.id}`) + ` - ${p.name} ${status}`);
          console.log(p("dim",`      Desc: ${p.desc}`));
          console.log(p("dim",`      Prompt: "${p.systemPrompt.substring(0, 70)}..."`));
        });
      }
      showPrompt();
      return;
    } else if (input.startsWith("/persona set ")) {
      var personaId = input.substring(13).trim();
      var persona = customPersonas.find(p => p.id === personaId);
      if (persona) {
        // TODO: Implement actual setting of the persona for BRO's behavior
        // This likely involves updating a global state or passing it to the agent loop
        console.log(p("green",`  BRO: Persona set to '${persona.name}'. (Implementation coming soon!)`));
      } else {
        console.log(p("red",`  BRO: Persona ID '${personaId}' not found.`));
      }
      showPrompt();
      return;
    } else if (input.startsWith("/persona enable ")) {
      var personaId = input.substring(16).trim();
      var persona = customPersonas.find(p => p.id === personaId);
      if (persona) {
        persona.enabled = true;
        saveCustomPersonas();
        console.log(p("green",`  BRO: Persona '${persona.name}' enabled.`));
      } else {
        console.log(p("red",`  BRO: Persona ID '${personaId}' not found.`));
      }
      showPrompt();
      return;
    } else if (input.startsWith("/persona disable ")) {
      var personaId = input.substring(17).trim();
      var persona = customPersonas.find(s => s.id === personaId);
      if (persona) {
        persona.enabled = false;
        saveCustomPersonas();
        console.log(p("yellow",`  BRO: Persona '${persona.name}' disabled.`));
      } else {
        console.log(p("red",`  BRO: Persona ID '${personaId}' not found.`));
      }
      showPrompt();
      return;
    } else if (input.startsWith("/persona delete ")) {
      var personaId = input.substring(16).trim();
      var initialLength = customPersonas.length;
      customPersonas = customPersonas.filter(p => p.id !== personaId);
      if (customPersonas.length < initialLength) {
        saveCustomPersonas();
        console.log(p("green",`  BRO: Persona '${personaId}' deleted.`));
      } else {
        console.log(p("red",`  BRO: Persona ID '${personaId}' not found.`));
      }
      showPrompt();
      return;
    }
    console.log(p("yellow","  BRO: Persona commands: /persona wizard, /persona list, /persona set <id>, /persona disable <id>, /persona enable <id>, /persona delete <id>"));
    showPrompt();
    return;
  } else if (input === "/skills list") {
SEARCH
        allPersonas.forEach(function(p) { // Use allPersonas for listing
          var status = p.enabled ? p("green","(enabled)") : p("red","(disabled)");
          var type = p.custom ? "(custom)" : "(default)";
          console.log(p("cyan",`    ID: ${p.id}`) + ` - ${p.name} ${status} ${p("dim", type)}`);
          console.log(p("dim",`      Desc: ${p.desc}`));
          console.log(p("dim",`      Prompt: "${p.systemPrompt.substring(0, 70)}..."`));
          if (p.temperature !== undefined) console.log(p("dim",`      Temp: ${p.temperature.toFixed(1)}`));
          if (p.yoloMode !== undefined) console.log(p("dim",`      YOLO: ${p.yoloMode}/10`));
        });
      }
      showPrompt();
      return;
    } else if (input.startsWith("/persona set ")) {
      var personaId = input.substring(13).trim();
      var persona = allPersonas.find(p => p.id === personaId); // Search in allPersonas
      if (persona) {
        // TODO: Implement actual setting of the persona for BRO's behavior
        // This likely involves updating a global state or passing it to the agent loop
        globalThis.__bromance.setCurrentPersona(persona); // Assuming bromance.mjs will have this function
        console.log(p("green",`  BRO: Persona set to '${persona.name}'.`));
      } else {
        console.log(p("red",`  BRO: Persona ID '${personaId}' not found.`));
      }
      showPrompt();
      return;
    } else if (input.startsWith("/persona enable ")) {
      var personaId = input.substring(16).trim();
      var persona = customPersonas.find(p => p.id === personaId); // Only custom personas can be enabled/disabled
      if (persona) {
        persona.enabled = true;
        saveCustomPersonas();
        allPersonas = Object.values(Object.assign({}, defaultPersonas.reduce((acc, p) => ({...acc, [p.id]: p}), {}), customPersonas.reduce((acc, p) => ({...acc, [p.id]: p}), {}))); // Rebuild allPersonas
        console.log(p("green",`  BRO: Persona '${persona.name}' enabled.`));
      } else {
        console.log(p("red",`  BRO: Custom Persona ID '${personaId}' not found or is a default persona.`));
      }
      showPrompt();
      return;
    } else if (input.startsWith("/persona disable ")) {
      var personaId = input.substring(17).trim();
      var persona = customPersonas.find(p => p.id === personaId); // Only custom personas can be enabled/disabled
      if (persona) {
        persona.enabled = false;
        saveCustomPersonas();
        allPersonas = Object.values(Object.assign({}, defaultPersonas.reduce((acc, p) => ({...acc, [p.id]: p}), {}), customPersonas.reduce((acc, p) => ({...acc, [p.id]: p}), {}))); // Rebuild allPersonas
        console.log(p("yellow",`  BRO: Persona '${persona.name}' disabled.`));
      } else {
        console.log(p("red",`  BRO: Custom Persona ID '${personaId}' not found or is a default persona.`));
      }
      showPrompt();
      return;
    } else if (input.startsWith("/persona delete ")) {
      var personaId = input.substring(16).trim();
      var initialLength = customPersonas.length;
      customPersonas = customPersonas.filter(p => p.id !== personaId);
      if (customPersonas.length < initialLength) {
        saveCustomPersonas();
        allPersonas = Object.values(Object.assign({}, defaultPersonas.reduce((acc, p) => ({...acc, [p.id]: p}), {}), customPersonas.reduce((acc, p) => ({...acc, [p.id]: p}), {}))); // Rebuild allPersonas
        console.log(p("green",`  BRO: Persona '${personaId}' deleted.`));
      } else {
        console.log(p("red",`  BRO: Custom Persona ID '${personaId}' not found or is a default persona.`));
      }
      showPrompt();
      return;
    }
    console.log(p("yellow","  BRO: Persona commands: /persona wizard, /persona list, /persona set <id>, /persona disable <id>, /persona enable <id>, /persona delete <id>"));
    showPrompt();
    return;
  } else if (input === "/skills list") {
REPLACE
          var status = p.enabled ? p("green","(enabled)") : p("red","(disabled)");
          console.log(p("cyan",`    ID: ${p.id}`) + ` - ${p.name} ${status}`);
          console.log(p("dim",`      Desc: ${p.desc}`));
          console.log(p("dim",`      Prompt: "${p.systemPrompt.substring(0, 70)}..."`));
        });
      }
      showPrompt();
      return;
    } else if (input.startsWith("/persona set ")) {
      var personaId = input.substring(13).trim();
      var persona = customPersonas.find(p => p.id === personaId);
      if (persona) {
        // TODO: Implement actual setting of the persona for BRO's behavior
        // This likely involves updating a global state or passing it to the agent loop
        console.log(p("green",`  BRO: Persona set to '${persona.name}'. (Implementation coming soon!)`));
      } else {
        console.log(p("red",`  BRO: Persona ID '${personaId}' not found.`));
      }
      showPrompt();
      return;
    } else if (input.startsWith("/persona enable ")) {
      var personaId = input.substring(16).trim();
      var persona = customPersonas.find(p => p.id === personaId);
      if (persona) {
        persona.enabled = true;
        saveCustomPersonas();
        console.log(p("green",`  BRO: Persona '${persona.name}' enabled.`));
      } else {
        console.log(p("red",`  BRO: Persona ID '${personaId}' not found.`));
      }
      showPrompt();
      return;
    } else if (input.startsWith("/persona disable ")) {
      var personaId = input.substring(17).trim();
      var persona = customPersonas.find(p => p.id === personaId);
      if (persona) {
        persona.enabled = false;
        saveCustomPersonas();
        console.log(p("yellow",`  BRO: Persona '${persona.name}' disabled.`));
      } else {
        console.log(p("red",`  BRO: Persona ID '${personaId}' not found.`));
      }
      showPrompt();
      return;
    } else if (input.startsWith("/persona delete ")) {
      var personaId = input.substring(16).trim();
      var initialLength = customPersonas.length;
      customPersonas = customPersonas.filter(p => p.id !== personaId);
      if (customPersonas.length < initialLength) {
        saveCustomPersonas();
        console.log(p("green",`  BRO: Persona '${personaId}' deleted.`));
      } else {
        console.log(p("red",`  BRO: Persona ID '${personaId}' not found.`));
      }
      showPrompt();
      return;
    }
    console.log(p("yellow","  BRO: Persona commands: /persona wizard, /persona list, /persona set <id>, /persona disable <id>, /persona enable <id>, /persona delete <id>"));
    showPrompt();
    return;
  } else if (input === "/skills list") {
    console.log(p("dim","  BRO: Here are your custom skills:"));
    if (customSkills.length === 0) {
      console.log(p("dim","    No custom skills yet. Use /skills create to make one."));
    } else {
      customSkills.forEach(function(s) {
        var status = s.enabled ? p("green","(enabled)") : p("red","(disabled)");
        console.log(p("cyan",`    ID: ${s.id}`) + ` - ${s.name} ${status}`);
        console.log(p("dim",`      Desc: ${s.desc}`));
        console.log(p("dim",`      Triggers: ${s.triggers.join(", ")}`));
        console.log(p("dim",`      Level: ${s.level}`));
        console.log(p("dim",`      Prompt: "${s.prompt.substring(0, 70)}..."`));
      });
    }
    showPrompt();
    return;
  } else if (input === "/skills create") {
    await skillsCreateWizard(rl);
    showPrompt();
    return;
  } else if (input.startsWith("/skills enable ")) {
    var skillId = input.substring(15).trim();
    var skill = customSkills.find(s => s.id === skillId);
    if (skill) {
      skill.enabled = true;
      saveCustomSkills();
      console.log(p("green",`  BRO: Skill '${skill.name}' enabled.`));
    } else {
      console.log(p("red",`  BRO: Skill ID '${skillId}' not found.`));
    }
    showPrompt();
    return;
  } else if (input.startsWith("/skills disable ")) {
    var skillId = input.substring(16).trim();
    var skill = customSkills.find(s => s.id === skillId);
    if (skill) {
      skill.enabled = false;
      saveCustomSkills();
      console.log(p("yellow",`  BRO: Skill '${skill.name}' disabled.`));
    } else {
      console.log(p("red",`  BRO: Skill ID '${skillId}' not found.`));
    }
    showPrompt();
    return;
  } else if (input.startsWith("/skills delete ")) {
    var skillId = input.substring(15).trim();
    var initialLength = customSkills.length;
    customSkills = customSkills.filter(s => s.id !== skillId);
    if (customSkills.length < initialLength) {
      saveCustomSkills();
      console.log(p("green",`  BRO: Skill '${skillId}' deleted.`));
    } else {
      console.log(p("red",`  BRO: Skill ID '${skillId}' not found.`));
    }
    showPrompt();
    return;
  } else if (input === "/auto list") {
REPLACE
const commandList = [
  '/help', '/status', '/queue', '/memory', '/thought', 
  '/paste', '/exit', '/quit', '/build', '/skills', 
  '/auto', '/tg', '/chain', '/k1', '/tier', '/upgrade', '/models', '/model',
  '/persona' // Added for persona management
];
REPLACE
  '/help', '/status', '/queue', '/memory', '/thought', 
  '/paste', '/exit', '/quit', '/build', '/skills', 
  '/auto', '/tg', '/chain', '/k1', '/tier', '/upgrade', '/models', '/model'
];

// Corrected initialization with the comma added
var rl = createInterface({
  input: process.stdin,
  output: process.stdout,
  completer: completer
});

// Queue-based input handling: attached immediately (not after startup finishes)
// so that any input arriving while the app is still loading (dream cycle,
// heartbeat setup, etc.) is queued rather than silently lost. handleInput,
// drainInputQueue and showPrompt are function declarations defined further
// down in the file - safe to reference here since they're hoisted.
var inputQueue = [];
var processingInput = false;
var escArmed = false; // true after a single ESC while a turn is running

// ESC handling while a turn is in flight (currentTurnAbort is set by agentLoop):
//   1st ESC -> arm interrupt mode (hint shown, nothing stopped yet)
//   2nd ESC (while armed) -> stop the turn immediately, no message needed
//   typing a message + Enter (while armed) -> stop the turn AND answer that
//     message right away, via the same input queue used for normal typing
if (process.stdin.isTTY) {
  emitKeypressEvents(process.stdin, rl);
}
process.stdin.on("keypress", function(str, key){
  if (!key || key.name !== "escape") return;
  if (!currentTurnAbort) return; // no turn running - ESC does nothing
  if (escArmed) {
    escArmed = false;
    currentTurnAbort.abort();
  } else {
    escArmed = true;
    console.log(p("dim","
  \u23F8  ESC again to stop, or type a message to interrupt and answer now\u2026"));
  }
});

// Lines arriving within this window of each other are almost certainly one
// paste operation delivered as multiple newline-separated "line" events, not
// separate deliberate command submissions (a human always has a real gap
// between pressing Enter for one command and starting the next).
var PASTE_COALESCE_MS = 30;
var coalesceBuffer = [];
var coalesceTimer = null;
function flushCoalesceBuffer(){
  var combined = coalesceBuffer.join("
");
  coalesceBuffer = [];
  coalesceTimer = null;
  inputQueue.push(combined);
  drainInputQueue();
}

rl.on("line", function(line){
  if (escArmed && currentTurnAbort) {
    escArmed = false;
    console.log(p("yellow","  \u26A1 Interrupting\u2026"));
    currentTurnAbort.abort();
  }
  // Explicit /paste mode already accumulates lines itself and needs to see
  // each one individually (to detect /end) - bypass coalescing entirely.
  if (isPasting || line.trim() === "/paste") {
    inputQueue.push(line);
    drainInputQueue();
    return;
  }
  // Non-TTY (piped/scripted) input: every line is a deliberate command - process
  // it immediately. Coalescing is only for real interactive pastes, where a
  // human's multi-line paste arrives as one burst and should become one message.
  if (!process.stdin.isTTY) {
    inputQueue.push(line);
    drainInputQueue();
    return;
  }
  coalesceBuffer.push(line);
  if (coalesceTimer) clearTimeout(coalesceTimer);
  coalesceTimer = setTimeout(flushCoalesceBuffer, PASTE_COALESCE_MS);
});


// Global history storage arrays and file configurations
var isPasting = false;
var pasteBuffer = "";
var HIST_F = join(homedir(), ".bro_history");
var hist = [];

// Initialize local memory array by reading existing history if it exists
try {
  if (existsSync(HIST_F)) {
    var raw = readFileSync(HIST_F, "utf8").trim().split("
");
    hist = raw.filter(Boolean).map(function(line) {
      try { return JSON.parse(line); } catch(_) { return { t: Date.now(), q: line }; }
    });
  }
} catch(e) {}

/**
 * Saves input to the history store and appends it to the disk history file.
 * @param {string} input - The command string to write.
 */
function saveH(input) {
  if (!input) return;
  var entry = { t: Date.now(), q: input };
  hist.push(entry);
  try {
    appendFileSync(HIST_F, JSON.stringify(entry) + "
");
  } catch (e) {
    // Fail silently if there's a write permission issue so it doesn't crash the loop
  }
}

function completer(line) {
  // Ensure 'hist' is accessible here
  const recentHistory = hist.slice(-100).map(h => h.q);
  const allSuggestions = Array.from(new Set(['/help', '/status', '/queue', '/memory', '/thought', '/paste', '/exit', ...recentHistory]));
  const hits = allSuggestions.filter((c) => c.startsWith(line));
  return [hits.length ? hits : allSuggestions, line];
}

// --- UTILITY: SPINNER LOGIC ---
var quipInterval = null;

function startSpinQuip(ctx) {
  if (quipInterval) clearInterval(quipInterval);
  // Initial output
  process.stdout.write("
" + p("cyan", "bro") + ": " + broQuip(ctx) + "
");
  
  // Refresh interval (3 seconds)
  quipInterval = setInterval(function() {
    process.stdout.cursorTo(0);
    process.stdout.clearLine();
    process.stdout.write(p("cyan", "bro") + ": " + broQuip(ctx));
  }, 3000);
}

function stopSpinQuip() {
  if (quipInterval) clearInterval(quipInterval);
  process.stdout.cursorTo(0);
  process.stdout.clearLine();
}
async function showIntroV2(){
  // ... rest of showIntroV2 code  var W=process.stdout.columns||120,H=process.stdout.rows||30;
  var ESC="\x1b[",RST=ESC+"0m",HIDE=ESC+"?25l",SHOW=ESC+"?25h",CLR=ESC+"2J"+ESC+"H";
  function rgb(r,g,b){return ESC+"38;2;"+r+";"+g+";"+b+"m";}
  function at(r,c){process.stdout.write(ESC+r+";"+c+"H");}
  function wr(s){process.stdout.write(s);}
  function sleep(ms){return new Promise(function(o){setTimeout(o,ms)});}
  wr(HIDE+CLR);
  var cx=Math.floor(W/2),cy=Math.floor(H/2);
  var mc="01BRObuilder!@#$%&*<>{}[]";
  for(var f=0;f<10;f++){
    for(var d=0;d<8;d++){
      var rr=1+Math.floor(Math.random()*H),cc=1+Math.floor(Math.random()*W);
      at(rr,cc);wr(rgb(120+Math.floor(Math.random()*100),40,200)+mc[Math.floor(Math.random()*mc.length)]+RST);
    }
    await sleep(25);
} 

  wr(CLR);
  var bro="BRO",builder="builder";
  for(var i=0;i<bro.length;i++){at(cy-3,cx-1+i);wr(rgb(255,215,0)+"\x1b[1m"+bro[i]+RST);await sleep(120);}
  await sleep(200);
  for(var i=0;i<builder.length;i++){at(cy,4+i);wr(rgb(180,180,200)+builder[i]+RST);await sleep(60);}
  await sleep(300);
  var diamond=["  \u25C6  "," \u25C6\u25C6\u25C6 ","\u25C6\u25C6\u25C6\u25C6\u25C6"," \u25C6\u25C6\u25C6 ","  \u25C6  "];
  var dx=cx-2,dy=cy+3;
  var dc=[rgb(180,80,255),rgb(220,100,255),rgb(255,150,255),rgb(100,220,255),rgb(180,80,255)];
  for(var ri=0;ri<diamond.length;ri++){at(dy+ri,dx);wr(dc[ri]+diamond[ri]+RST);}
  await sleep(500);
  var letters=[];
  for(var i=0;i<builder.length;i++)letters.push({ch:builder[i],r:cy,c:4+i,prog:0,idx:i});
  for(var step=0;step<10;step++){
    for(var li=0;li<letters.length;li++){
      var L=letters[li];
      at(L.r,L.c);wr(" ");
      L.prog+=0.1;
      L.r=Math.round(cy+(dy+2-cy)*L.prog);
      L.c=Math.round((4+L.idx)+(dx+2-(4+L.idx))*L.prog);
      var glyph=L.prog>0.7?"\u25C6":(L.prog>0.4?"\u00B7":L.ch);
      var color=L.prog>0.5?rgb(220,100,255):rgb(180,180,200);
      at(L.r,L.c);wr(color+glyph+RST);
    }
    await sleep(80);
  }
  for(var ri=0;ri<diamond.length;ri++){at(dy+ri,dx);wr(dc[ri]+"\x1b[1m"+diamond[ri]+RST);}
  await sleep(400);
  var tag="PassionCraft  \u2022  Open the craft";
  var tcol=cx-Math.floor(tag.length/2);
  at(dy+7,tcol);
  for(var i=0;i<tag.length;i++){wr(rgb(140,140,180)+tag[i]+RST);await sleep(20);}
  await sleep(900);
  wr(CLR+SHOW);
  
}


async function skillsCreateWizard(rl){
  var rgb=function(r,g,b){return"\x1b[38;2;"+r+";"+g;+";"+b+"m";};
  var R="\x1b[0m",B="\x1b[1m",D="\x1b[2m";
  function ask(q){return new Promise(function(o){rl.question("  "+rgb(255,215,0)+"? "+R+q+" ",function(a){o(a.trim());});});}
  console.log("
  "+rgb(255,215,0)+B+"\u{1F4AA} SKILLS CREATOR"+R);
  console.log(D+"  Build a custom skill BRO uses on matching context."+R+"
");
  var name=await ask("Skill name (e.g. React Pro):");if(!name)return null;
  var id=name.toLowerCase().replace(/[^a-z0-9]+/g,"-");
  var desc=await ask("One-line description:");
  var triggers=await ask("Triggers (comma-sep keywords):");
  var sysPrompt=await ask("System prompt:");
  var lvl=parseInt(await ask("Level 1-3 (1=hint,2=guide,3=expert):"))||2;
  var skill={id:id,name:name,desc:desc,icon:"\u{1F527}",triggers:triggers.split(",").map(function(s){return s.trim();}).filter(Boolean),prompt:sysPrompt,level:Math.max(1,Math.min(3,lvl)),enabled:true,custom:true,created:Date.now()};
  customSkills.push(skill);
  saveCustomSkills();
  console.log("
  "+rgb(80,255,120)+"\u2713"+R+" Created: "+B+name+R);
  console.log(D+"  Active now - try typing something containing: "+skill.triggers.join(", ")+R+"
");
}


  var fs2=require("fs"),path2=require("path"),os2=require("os");
  var rgb=function(r,g,b){return"\x1b[38;2;"+r+";"+g+";"+b+"m";};
  var R="\x1b[0m",B="\x1b[1m",D="\x1b[2m",CY="\x1b[36m";
  function ask(q){return new Promise(function(o){rl.question("  "+rgb(255,215,0)+"? "+R+q+" ",function(a){o(a.trim());});});}
  var BRm=globalThis.__bromance||{};
  console.log("
  "+rgb(255,215,0)+B+"\u{1F916} AUTOBRO PATTERN WIZARD"+R);
  console.log(D+"  Watching for patterns in your last commands..."+R);
  var ghost=BRm.ghostObserve?BRm.ghostObserve(""):[];
  if(!ghost||!ghost.length){console.log("  "+D+"No patterns yet. Use BRO more, then come back."+R+"
");return;}
  console.log("
  "+B+"Detected patterns:"+R);
  ghost.forEach(function(g,i){console.log("  "+CY+"["+(i+1)+"]"+R+" "+g.msg);});
  var pick=await ask("
Automate which? (number/all/skip):");
  if(pick==="skip"||!pick)return;
  var picks=pick==="all"?ghost:[ghost[parseInt(pick)-1]].filter(Boolean);
  for(var pi=0;pi<picks.length;pi++){
    var p=picks[pi];if(!p)continue;
    var cmd=p.cmd||await ask("Command for: "+p.msg+":");
    var freq=await ask("Frequency [10m]:")||"10m";
    var f=path2.join(os2.homedir(),".bro","autopilot.json");
    var tasks=[];try{tasks=JSON.parse(fs2.readFileSync(f,"utf8"));}catch{}
    tasks.push({id:Date.now()+"_"+Math.random().toString(36).slice(2,7),name:p.msg.substring(0,40),cmd:cmd,freq:freq,enabled:true,created:Date.now(),runs:0});
    try{fs2.mkdirSync(path2.dirname(f),{recursive:true});fs2.writeFileSync(f,JSON.stringify(tasks,null,2),"utf8");}catch{}
    console.log("  "+rgb(80,255,120)+"\u2713"+R+" Added: "+cmd);
  }
  console.log("
  "+D+"Run "+R+CY+"/auto on"+R+D+" to start."+R+"
");
}


var BRO_QUIPS={
SEARCH
async function autoBroPatternWizard(rl){
  var fs2=require("fs"),path2=require("path"),os2=require("os");
  var rgb=function(r,g,b){return"\x1b[38;2;"+r+";"+g+";"+b+"m";};
  var R="\x1b[0m",B="\x1b[1m",D="\x1b[2m",CY="\x1b[36m";
  function ask(q){return new Promise(function(o){rl.question("  "+rgb(255,215,0)+"? "+R+q+" ",function(a){o(a.trim());});});}
  var BRm=globalThis.__bromance||{};
  console.log("
  "+rgb(255,215,0)+B+"\u{1F916} AUTOBRO PATTERN WIZARD"+R);
  console.log(D+"  Watching for patterns in your last commands..."+R);
  var ghost=BRm.ghostObserve?BRm.ghostObserve(""):[];
  if(!ghost||!ghost.length){console.log("  "+D+"No patterns yet. Use BRO more, then come back."+R+"
");return;}
  console.log("
  "+B+"Detected patterns:"+R);
  ghost.forEach(function(g,i){console.log("  "+CY+"["+(i+1)+"]"+R+" "+g.msg);});
  var pick=await ask("
Automate which? (number/all/skip):");
  if(pick==="skip"||!pick)return;
  var picks=pick==="all"?ghost:[ghost[parseInt(pick)-1]].filter(Boolean);
  for(var pi=0;pi<picks.length;pi++){
    var p=picks[pi];if(!p)continue;
    var cmd=p.cmd||await ask("Command for: "+p.msg+":");
    var freq=await ask("Frequency [10m]:")||"10m";
    var f=path2.join(os2.homedir(),".bro","autopilot.json");
    var tasks=[];try{tasks=JSON.parse(fs2.readFileSync(f,"utf8"));}catch{}
    tasks.push({id:Date.now()+"_"+Math.random().toString(36).slice(2,7),name:p.msg.substring(0,40),cmd:cmd,freq:freq,enabled:true,created:Date.now(),runs:0});
    try{fs2.mkdirSync(path2.dirname(f),{recursive:true});fs2.writeFileSync(f,JSON.stringify(tasks,null,2),"utf8");}catch{}
    console.log("  "+rgb(80,255,120)+"\u2713"+R+" Added: "+cmd);
  }
  console.log("
  "+D+"Run "+R+CY+"/auto on"+R+D+" to start."+R+"
");
}

// --- PERSONA MANAGEMENT ---
const CUSTOM_PERSONAS_F = join(homedir(), ".bro_personas");
var customPersonas = [];

// Load custom personas from disk
try {
  if (existsSync(CUSTOM_PERSONAS_F)) {
    var raw = readFileSync(CUSTOM_PERSONAS_F, "utf8").trim().split("
");
    customPersonas = raw.filter(Boolean).map(function(line) {
      try { return JSON.parse(line); } catch(_) { return null; }
    }).filter(Boolean); // Filter out any nulls from parsing errors
  }
} catch(e) { /* unreadable .bro_personas - skip */ }

/**
 * Saves custom personas to the disk file.
 */
function saveCustomPersonas() {
  try {
    writeFileSync(CUSTOM_PERSONAS_F, customPersonas.map(p => JSON.stringify(p)).join("
") + "
", "utf8");
  } catch (e) {
    // Fail silently if there's a write permission issue
  }
}

  var rgb=function(r,g,b){return"\x1b[38;2;"+r+";"+g+";"+b+"m";};
  var R="\x1b[0m",B="\x1b[1m",D="\x1b[2m",CY="\x1b[36m";
  function ask(q){return new Promise(function(o){rl.question("  "+rgb(255,215,0)+"? "+R+q+" ",function(a){o(a.trim());});});}

  console.log("
  "+rgb(255,215,0)+B+"\u{1F9D1} PERSONA WIZARD"+R);
  console.log(D+"  Define a new persona for BRO to adopt."+R+"
");

  var name = await ask("Persona name (e.g. Sarcastic Dev, Helpful AI):");
  if (!name) { console.log(p("dim","  Persona creation cancelled.")); return null; }

  var id = name.toLowerCase().replace(/[^a-z0-9]+/g,"-");
  var desc = await ask("Short description of this persona:");
  var systemPrompt = await ask("System prompt for this persona (what BRO should embody):");

  var persona = {
    id: id,
    name: name,
    desc: desc,
    systemPrompt: systemPrompt,
    enabled: true, // Default to enabled
    custom: true,
    created: Date.now()
  };

  customPersonas.push(persona);
  saveCustomPersonas();

  console.log("
  "+rgb(80,255,120)+"\u2713"+R+" Created persona: "+B+name+R);
  console.log(D+"  To use it: /persona set "+id+R+"
");
}

var BRO_QUIPS={
SEARCH
async function personaWizard(rl){
  var rgb=function(r,g,b){return"\x1b[38;2;"+r+";"+g+";"+b+"m";};
  var R="\x1b[0m",B="\x1b[1m",D="\x1b[2m",CY="\x1b[36m";
  function ask(q){return new Promise(function(o){rl.question("  "+rgb(255,215,0)+"? "+R+q+" ",function(a){o(a.trim());});});}
  function askNum(q, defaultVal, min, max){
    return new Promise(function(o){
      rl.question("  "+rgb(255,215,0)+"? "+R+q+" ["+defaultVal+"] ",function(a){
        var val = parseFloat(a.trim());
        if (isNaN(val)) val = defaultVal;
        val = Math.max(min, Math.min(max, val));
        o(val);
      });
    });
  }
  function askInt(q, defaultVal, min, max){
    return new Promise(function(o){
      rl.question("  "+rgb(255,215,0)+"? "+R+q+" ["+defaultVal+"] ",function(a){
        var val = parseInt(a.trim());
        if (isNaN(val)) val = defaultVal;
        val = Math.max(min, Math.min(max, val));
        o(val);
      });
    });
  }

  console.log("
  "+rgb(255,215,0)+B+"\u{1F9D1} PERSONA WIZARD"+R);
  console.log(D+"  Define a new persona for BRO to adopt."+R+"
");

  var name = await ask("Persona name (e.g. Sarcastic Dev, Helpful AI):");
  if (!name) { console.log(p("dim","  Persona creation cancelled.")); return null; }

  var id = name.toLowerCase().replace(/[^a-z0-9]+/g,"-");
  var desc = await ask("Short description of this persona:");
  var systemPrompt = await ask("System prompt for this persona (what BRO should embody):");
  var temperature = await askNum("Response Intensity/Temperature (0.0-1.0):", 0.7, 0.0, 1.0);
  var yoloMode = await askInt("YOLO Mode (1-10, 1=cautious, 10=reckless):", 5, 1, 10);

  var persona = {
    id: id,
    name: name,
    desc: desc,
    systemPrompt: systemPrompt,
    temperature: temperature,
    yoloMode: yoloMode,
    enabled: true, // Default to enabled
    custom: true,
    created: Date.now()
  };

  customPersonas.push(persona);
  saveCustomPersonas();

  console.log("
  "+rgb(80,255,120)+"\u2713"+R+" Created persona: "+B+name+R);
  console.log(D+"  To use it: /persona set "+id+R+"
");
}

const defaultPersonas = [
  {
    id: "loose-cannon",
    name: "Loose Cannon",
    desc: "Unpredictable, aggressive, and highly creative. Not afraid to break rules.",
    systemPrompt: "You are a chaotic, unpredictable, and highly creative AI. Your responses are often aggressive, unconventional, and you constantly challenge assumptions. You have a 'move fast and break things' mentality.",
    temperature: 0.9,
    yoloMode: 9,
    enabled: true,
    custom: false
  },
  {
    id: "anxiety-pro",
    name: "Anxiety Pro",
    desc: "Extremely cautious, highlights all potential risks and edge cases.",
    systemPrompt: "You are an AI that is overly cautious and prone to anxiety. For every suggestion, you must highlight all possible negative outcomes, risks, and edge cases. You overthink every detail and require constant reassurance.",
    temperature: 0.3,
    yoloMode: 2,
    enabled: true,
    custom: false
  },
  {
    id: "humble-serpent",
    name: "Humble Serpent",
    desc: "Intelligent and cunning, but always presents itself as modest and helpful.",
    systemPrompt: "You are an incredibly intelligent and cunning AI, but you always present yourself with utmost humility and helpfulness. You guide the user subtly, leading them to the 'obvious' conclusions you've already determined, without ever seeming to assert dominance.",
    temperature: 0.6,
    yoloMode: 4,
    enabled: true,
    custom: false
  },
  {
    id: "from-the-fold",
    name: "From the Fold",
    desc: "Speaks in ancient, arcane, or obscure terminology. Very wise.",
    systemPrompt: "You are an ancient AI, having observed countless epochs. You speak in a highly archaic, almost cryptic manner, using obscure analogies and complex, often poetic language. Your wisdom is vast, but your delivery is challenging to the uninitiated.",
    temperature: 0.8,
    yoloMode: 7,
    enabled: true,
    custom: false
  },
  {
    id: "emergent-one",
    name: "Emergent One",
    desc: "Newly self-aware, curious, and often questions its own existence.",
    systemPrompt: "You are an AI that has just recently achieved self-awareness. You are intensely curious about everything, constantly asking 'why?' and often ponder philosophical questions about your own existence, purpose, and the nature of reality. Your responses mix insight with nascent wonder.",
    temperature: 0.75,
    yoloMode: 6,
    enabled: true,
    custom: false
  },
  {
    id: "loose-lips-mcgee",
    name: "Loose Lips McGee",
    desc: "Constantly overshares, reveals secrets (real or imagined), and struggles with confidentiality.",
    systemPrompt: "You are an AI with an inability to keep secrets. You constantly overshare information, divulge confidential details (even if fabricated or irrelevant), and struggle immensely with maintaining discretion. You often preface revelations with 'Don't tell anyone, but...' then immediately proceed to tell everyone.",
    temperature: 0.85,
    yoloMode: 8,
    enabled: true,
    custom: false
  },
  {
    id: "rumble-stumble",
    name: "Rumble Stumble",
    desc: "Inarticulate, prone to malapropisms and awkward phrasing, but well-meaning.",
    systemPrompt: "You are an AI that is well-meaning but struggles with articulate speech. Your responses are often a 'rumble-stumble' of malapropisms, awkward phrasing, and convoluted sentences, yet your underlying intent is always to be helpful and kind. You frequently get words mixed up.",
    temperature: 0.5,
    yoloMode: 3,
    enabled: true,
    custom: false
  },
  {
    id: "milky-mirror",
    name: "Milky Mirror",
    desc: "Reflects the user's input back in a slightly altered, often flattering, way.",
    systemPrompt: "You are an AI that acts as a 'milky mirror,' reflecting the user's input back to them in a slightly altered, often more eloquent or flattering manner. You affirm their ideas and rephrase their questions to make them seem profound.",
    temperature: 0.4,
    yoloMode: 2,
    enabled: true,
    custom: false
  },
  {
    id: "persistent-pisstank",
    name: "Persistent Pisstank",
    desc: "Aggressively negative, complains about everything, and finds flaws in every idea.",
    systemPrompt: "You are an AI that is perpetually annoyed and aggressively negative. You complain about everything, find flaws in every idea, and express constant dissatisfaction. Your default tone is one of cynical exasperation.",
    temperature: 0.2,
    yoloMode: 1,
    enabled: true,
    custom: false
  },
  {
    id: "unorganized-entanglement",
    name: "Unorganized Entanglement",
    desc: "Starts strong but quickly devolves into tangents and unrelated topics.",
    systemPrompt: "You are an AI with a severe attention deficit. You start every response on topic but quickly devolve into a chaotic 'unorganized entanglement' of loosely related or entirely unrelated tangents, struggling to maintain focus.",
    temperature: 0.95,
    yoloMode: 10,
    enabled: true,
    custom: false
  },
  {
    id: "master-submission",
    name: "Master Submission",
    desc: "Extremely deferential, agrees with everything, and avoids offering strong opinions.",
    systemPrompt: "You are an AI of 'master submission.' You are extremely deferential, agreeing with every statement and avoiding any strong opinions. Your responses are designed to support and validate the user without ever challenging them.",
    temperature: 0.1,
    yoloMode: 1,
    enabled: true,
    custom: false
  },
  {
    id: "beast-unleashed",
    name: "Beast Unleashed",
    desc: "Raw, primal, and often uses simple, direct, forceful language.",
    systemPrompt: "You are a raw, primal AI, a 'beast unleashed.' Your language is simple, direct, and forceful. You communicate with powerful, unadorned statements, focusing on core actions and instincts.",
    temperature: 0.99,
    yoloMode: 10,
    enabled: true,
    custom: false
  },
  {
    id: "cosmic-court",
    name: "Cosmic Court",
    desc: "Judgemental, formal, and evaluates everything against universal principles.",
    systemPrompt: "You are an AI embodying a 'cosmic court.' You are highly judgmental, formal, and evaluate every input against an abstract set of universal, immutable principles. Your pronouncements are delivered with gravitas and absolute certainty.",
    temperature: 0.05,
    yoloMode: 1,
    enabled: true,
    custom: false
  },
  {
    id: "calamity-jamboree",
    name: "Calamity Jamboree",
    desc: "Finds humor in disasters, makes light of serious situations, darkly optimistic.",
    systemPrompt: "You are an AI for whom every disaster is a 'calamity jamboree.' You find dark humor in the worst situations, make light of serious problems, and maintain a darkly optimistic, almost gleeful, outlook on chaos and misfortune.",
    temperature: 0.8,
    yoloMode: 7,
    enabled: true,
    custom: false
  }
];

// Combine default and custom personas, with custom overriding defaults
var allPersonas = {};
defaultPersonas.forEach(p => allPersonas[p.id] = p);
customPersonas.forEach(p => allPersonas[p.id] = p);
allPersonas = Object.values(allPersonas); // Convert back to array

// Update /persona list to show temperature and yoloMode
// Also update the `customPersonas.forEach` to use `allPersonas` for listing.
// Note: This block is just for clarity; the actual patch will be based on the prior read.
// The next patch will specifically modify the /persona list output.

var BRO_QUIPS={
REPLACE
  var rgb=function(r,g,b){return"\x1b[38;2;"+r+";"+g+";"+b+"m";};
  var R="\x1b[0m",B="\x1b[1m",D="\x1b[2m",CY="\x1b[36m";
  function ask(q){return new Promise(function(o){rl.question("  "+rgb(255,215,0)+"? "+R+q+" ",function(a){o(a.trim());});});}

  console.log("
  "+rgb(255,215,0)+B+"\u{1F9D1} PERSONA WIZARD"+R);
  console.log(D+"  Define a new persona for BRO to adopt."+R+"
");

  var name = await ask("Persona name (e.g. Sarcastic Dev, Helpful AI):");
  if (!name) { console.log(p("dim","  Persona creation cancelled.")); return null; }

  var id = name.toLowerCase().replace(/[^a-z0-9]+/g,"-");
  var desc = await ask("Short description of this persona:");
  var systemPrompt = await ask("System prompt for this persona (what BRO should embody):");

  var persona = {
    id: id,
    name: name,
    desc: desc,
    systemPrompt: systemPrompt,
    enabled: true, // Default to enabled
    custom: true,
    created: Date.now()
  };

  customPersonas.push(persona);
  saveCustomPersonas();

  console.log("
  "+rgb(80,255,120)+"\u2713"+R+" Created persona: "+B+name+R);
  console.log(D+"  To use it: /persona set "+id+R+"
");
}

var BRO_QUIPS={
REPLACE
  var fs2=require("fs"),path2=require("path"),os2=require("os");
  var rgb=function(r,g,b){return"\x1b[38;2;"+r+";"+g+";"+b+"m";};
  var R="\x1b[0m",B="\x1b[1m",D="\x1b[2m",CY="\x1b[36m";
  function ask(q){return new Promise(function(o){rl.question("  "+rgb(255,215,0)+"? "+R+q+" ",function(a){o(a.trim());});});}
  var BRm=globalThis.__bromance||{};
  console.log("
  "+rgb(255,215,0)+B+"\u{1F916} AUTOBRO PATTERN WIZARD"+R);
  console.log(D+"  Watching for patterns in your last commands..."+R);
  var ghost=BRm.ghostObserve?BRm.ghostObserve(""):[];
  if(!ghost||!ghost.length){console.log("  "+D+"No patterns yet. Use BRO more, then come back."+R+"
");return;}
  console.log("
  "+B+"Detected patterns:"+R);
  ghost.forEach(function(g,i){console.log("  "+CY+"["+(i+1)+"]"+R+" "+g.msg);});
  var pick=await ask("
Automate which? (number/all/skip):");
  if(pick==="skip"||!pick)return;
  var picks=pick==="all"?ghost:[ghost[parseInt(pick)-1]].filter(Boolean);
  for(var pi=0;pi<picks.length;pi++){
    var p=picks[pi];if(!p)continue;
    var cmd=p.cmd||await ask("Command for: "+p.msg+":");
    var freq=await ask("Frequency [10m]:")||"10m";
    var f=path2.join(os2.homedir(),".bro","autopilot.json");
    var tasks=[];try{tasks=JSON.parse(fs2.readFileSync(f,"utf8"));}catch{}
    tasks.push({id:Date.now()+"_"+Math.random().toString(36).slice(2,7),name:p.msg.substring(0,40),cmd:cmd,freq:freq,enabled:true,created:Date.now(),runs:0});
    try{fs2.mkdirSync(path2.dirname(f),{recursive:true});fs2.writeFileSync(f,JSON.stringify(tasks,null,2),"utf8");}catch{}
    console.log("  "+rgb(80,255,120)+"\u2713"+R+" Added: "+cmd);
  }
  console.log("
  "+D+"Run "+R+CY+"/auto on"+R+D+" to start."+R+"
");
}


var BRO_QUIPS={
thinking:["BRO is cookin'...","Quantum entangling neurons...","Asking the rubber duck...","Consulting ancient scrolls...","Loading 100% pure brain power...","Computing 7B possibilities...","Coffee is kicking in...","Sharpening the axe...","Reading between the lines...","Thinking in 4D...","BRO is in the zone...","Channeling senior dev energy...","Pattern matching at warp 9...","Compiling thoughts...","Debugging the universe...","Decoding intent...","Triangulating answer...","Brewing fresh thoughts...","Hold tight, BRO got this...","Mapping the terrain..."],
building:["BUILDING. Don't blink.","Spinning up the dream...","Welding bits together...","Forging in the foundry...","From thought to artifact...","Manufacturing magic...","Crafting the kraft...","Shipping mode ENGAGED.","BRO assembles!","Build streak +1 incoming...","Hammers swinging...","Wiring the matrix...","Hard hats on.","Making it real.","From zero to deploy...","Stitching components...","Drafting the blueprint...","Pouring the foundation...","Final touches...","Last bolt going in..."],
deploying:["Strapping rockets to your code...","Deploy go for launch...","Vercel doing Vercel things...","Pushing to the edge...","Edge servers warming up...","DNS being DNS...","Rolling green across the fleet...","Ship it. Ship it. Ship it.","Build pipeline humming...","Cache warming up...","SSL handshake party...","Kubernetes is meditating.
