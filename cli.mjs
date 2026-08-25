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
      readFileSync(candidates[i], "utf8").split(/\r?
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

const commandList = [
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


var BRO_QUIPS={
thinking:["BRO is cookin'...","Quantum entangling neurons...","Asking the rubber duck...","Consulting ancient scrolls...","Loading 100% pure brain power...","Computing 7B possibilities...","Coffee is kicking in...","Sharpening the axe...","Reading between the lines...","Thinking in 4D...","BRO is in the zone...","Channeling senior dev energy...","Pattern matching at warp 9...","Compiling thoughts...","Debugging the universe...","Decoding intent...","Triangulating answer...","Brewing fresh thoughts...","Hold tight, BRO got this...","Mapping the terrain..."],
building:["BUILDING. Don't blink.","Spinning up the dream...","Welding bits together...","Forging in the foundry...","From thought to artifact...","Manufacturing magic...","Crafting the kraft...","Shipping mode ENGAGED.","BRO assembles!","Build streak +1 incoming...","Hammers swinging...","Wiring the matrix...","Hard hats on.","Making it real.","From zero to deploy...","Stitching components...","Drafting the blueprint...","Pouring the foundation...","Final touches...","Last bolt going in..."],
deploying:["Strapping rockets to your code...","Deploy go for launch...","Vercel doing Vercel things...","Pushing to the edge...","Edge servers warming up...","DNS being DNS...","Rolling green across the fleet...","Ship it. Ship it. Ship it.","Build pipeline humming...","Cache warming up...","SSL handshake party...","Kubernetes is meditating.
