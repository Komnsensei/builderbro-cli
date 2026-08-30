// bro-build.mjs — idea-driven build wizard. The LLM (passed in from cli.mjs's
// askChat) suggests ideas at the entry point and generates the actual project
// files. No more picking between two hardcoded templates regardless of idea.
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "fs";
import { execSync } from "child_process";
import { join, resolve, dirname } from "path";
import { homedir } from "os";
import readline from "readline";

const c = {
  rose: "\x1b[38;2;220;100;255m", gold: "\x1b[38;2;255;215;0m",
  cyan: "\x1b[38;2;80;220;255m",  green: "\x1b[38;2;80;255;120m",
  red: "\x1b[38;2;255;80;100m",   gray: "\x1b[38;2;140;140;160m",
  R: "\x1b[0m", B: "\x1b[1m", D: "\x1b[2m"
};
const stripAnsi = s => s.replace(/\x1b\[[0-9;]*m/g, "");
function box(title, lines, color = c.rose) {
  const w = Math.max(title.length + 4, ...lines.map(l => stripAnsi(l).length)) + 4;
  console.log(`\n  ${color}╭${"─".repeat(w-2)}╮${c.R}`);
  console.log(`  ${color}│${c.R} ${c.B}${title}${c.R}${" ".repeat(w-title.length-4)} ${color}│${c.R}`);
  console.log(`  ${color}├${"─".repeat(w-2)}┤${c.R}`);
  for (const l of lines) {
    const pad = Math.max(0, w - stripAnsi(l).length - 4);
    console.log(`  ${color}│${c.R} ${l}${" ".repeat(pad)} ${color}│${c.R}`);
  }
  console.log(`  ${color}╰${"─".repeat(w-2)}╯${c.R}\n`);
}
const step = (n,t,l) => console.log(`  ${c.gold}[${n}/${t}]${c.R} ${c.B}${l}${c.R}`);
const ok   = m => console.log(`        ${c.green}✓${c.R} ${c.D}${m}${c.R}`);
const fail = m => console.log(`        ${c.red}✗${c.R} ${m}`);
const info = m => console.log(`        ${c.cyan}→${c.R} ${c.D}${m}${c.R}`);
const ask  = (rl,q) => new Promise(r => rl.question(`  ${c.cyan}?${c.R} ${q} `, a => r(a.trim())));

function loadBuildHistory() {
  try {
    const logPath = join(homedir(), ".bro", "builds.json");
    return JSON.parse(readFileSync(logPath, "utf8")).slice(0, 10);
  } catch { return []; }
}

function stripJsonFences(text) {
  var t = text.trim();
  t = t.replace(/^```(?:json)?\s*/i, "").replace(/```\s*$/, "");
  return t.trim();
}

async function askLLM(askChatFn, prompt) {
  var resp = await askChatFn([{ role: "user", parts: [{ text: prompt }] }]);
  return resp.content;
}

async function getSuggestions(askChatFn, history) {
  var histText = history.length
    ? history.map(h => `- "${h.idea}" (${h.template || h.stack || "?"})`).join("\n")
    : "No prior builds yet.";
  var prompt = `You are helping brainstorm what to build next. Recent build history (most recent first):\n${histText}\n\nSuggest exactly 3 specific, creative, buildable project ideas for a solo developer to build RIGHT NOW as a small, complete, single-session project. Each should be one punchy sentence. Make them genuinely different from each other and from the history above - don't just repeat past ideas.\n\nRespond with ONLY a JSON array of exactly 3 strings, nothing else, no markdown fences, no explanation. Example: ["A CLI tool that tracks daily water intake with fun streaks","A browser extension that mutes autoplay videos site-wide","A tiny HTTP server that turns a folder of markdown files into a wiki"]`;
  try {
    var raw = await askLLM(askChatFn, prompt);
    var arr = JSON.parse(stripJsonFences(raw));
    if (Array.isArray(arr) && arr.length) return arr.slice(0, 3);
  } catch (e) { /* fall through to null - caller handles no-suggestions case */ }
  return null;
}

async function generatePlan(askChatFn, idea) {
  var prompt = `Generate a complete, small, genuinely working project for this idea:\n"${idea}"\n\nRequirements:\n- Actually implement the idea's core functionality - not a generic placeholder page.\n- Pick the simplest stack that works as a single static/local project with no external paid services (vanilla HTML/CSS/JS, or a small Node.js script, or a small Python script - whatever fits the idea best).\n- Keep it to 2-6 files total.\n- Include a short README.md explaining what it does and how to run it.\n\nRespond with ONLY valid JSON in exactly this shape, nothing else, no markdown fences, no explanation before or after:\n{"name":"kebab-case-project-name","summary":"one sentence summary of what was built","stack":"vanilla|node|python","files":[{"path":"relative/path.ext","content":"FULL file contents as a plain string"}],"nextSteps":"one short sentence on how to run or open it"}`;
  var raw = await askLLM(askChatFn, prompt);
  var plan = JSON.parse(stripJsonFences(raw));
  if (!plan || !Array.isArray(plan.files) || !plan.files.length) throw new Error("LLM returned an empty or malformed plan");
  return plan;
}

// Bare-minimum fallback if no LLM is available (missing credentials) or generation fails -
// not meant to be the normal path, just keeps /build from being a hard dead end.
function fallbackPlan(idea, name) {
  return {
    name,
    summary: "Static starter page (AI generation unavailable - see message above)",
    stack: "vanilla",
    files: [
      { path: "index.html", content: `<!doctype html><html><head><meta charset="utf-8"><title>${name}</title><link rel="stylesheet" href="style.css"></head><body><main><h1>${name}</h1><p>${idea}</p></main></body></html>` },
      { path: "style.css", content: `body{font-family:system-ui;background:linear-gradient(135deg,#0a0a14,#1a0a2e);color:#fff;min-height:100vh;display:grid;place-items:center;margin:0}h1{font-size:3rem;background:linear-gradient(90deg,#dc64ff,#80dcff);-webkit-background-clip:text;color:transparent}` },
      { path: "README.md", content: `# ${name}\n\n${idea}\n\nThis is a bare-minimum fallback page - AI generation wasn't available when this was built. Run /build again once credentials are set up for a real, idea-specific project.\n` }
    ],
    nextSteps: "open index.html in your browser"
  };
}

export async function buildWizard(initialIdea = "", askChatFn = null, sharedRl = null) {
  const rl = sharedRl || readline.createInterface({ input: process.stdin, output: process.stdout });
  const ownsRl = !sharedRl;
  console.log("");
  box("\u{1F339} BRO /build", [
    askChatFn ? `${c.D}I'll suggest ideas and generate a real project for you.${c.R}` : `${c.D}${c.red}No LLM available - falling back to a bare static template.${c.R}`
  ], c.rose);

  let idea = initialIdea;

  if (!idea) {
    var history = loadBuildHistory();
    var suggestions = askChatFn ? await (async () => { info("thinking of ideas..."); return getSuggestions(askChatFn, history); })() : null;

    if (suggestions) {
      console.log(`\n  ${c.B}A few ideas based on what you've built before:${c.R}`);
      suggestions.forEach((s, i) => console.log(`    ${c.gold}${i+1}${c.R} ${s}`));
      console.log(`    ${c.gold}0${c.R} ${c.D}...or just type your own idea below${c.R}`);
      var pick = await ask(rl, `Pick [0-${suggestions.length}] or type your own idea:`);
      var n = parseInt(pick);
      if (!isNaN(n) && n >= 1 && n <= suggestions.length) {
        idea = suggestions[n - 1];
      } else if (pick && pick !== "0") {
        idea = pick; // they typed a free-form idea directly instead of a number
      } else {
        idea = await ask(rl, `What are we building?`);
      }
    } else {
      idea = await ask(rl, `What are we building?`);
    }
  }
  if (!idea) { if (ownsRl) rl.close(); return; }

  const defName = idea.toLowerCase().replace(/[^a-z0-9\s-]/g,"").replace(/\s+/g,"-").slice(0,30) || "bro-app";
  const nameInput = await ask(rl, `Project name [${defName}]:`);
  const name = (nameInput || defName).replace(/[^a-z0-9-_]/gi,"-").toLowerCase();
  const targetDir = resolve(process.cwd(), name);

  if (existsSync(targetDir)) {
    const ow = await ask(rl, `${c.red}${name}/ exists. Overwrite? [y/N]${c.R}`);
    if (ow.toLowerCase() !== "y") { if (ownsRl) rl.close(); return; }
  }

  var plan;
  if (askChatFn) {
    info("generating your project...");
    try {
      plan = await generatePlan(askChatFn, idea);
      plan.name = name; // keep the name the user actually confirmed
    } catch (e) {
      fail("AI generation failed: " + e.message.substring(0, 200));
      info("falling back to a bare static starter instead");
      plan = fallbackPlan(idea, name);
    }
  } else {
    plan = fallbackPlan(idea, name);
  }

  box("\u{1F4CB} BUILD PLAN", [
    `${c.B}${plan.summary}${c.R}`, "",
    `${c.cyan}Idea:${c.R}     ${idea}`,
    `${c.cyan}Stack:${c.R}    ${plan.stack || "?"}`,
    `${c.cyan}Target:${c.R}   ${targetDir}`,
    `${c.cyan}Files:${c.R}    ${plan.files.length}`, "",
    ...plan.files.map(f => `  ${c.green}•${c.R} ${f.path}`)
  ], c.gold);

  const go = await ask(rl, `Approve and build? [Y/n]`);
  if (go.toLowerCase() === "n") { if (ownsRl) rl.close(); return; }

  const t0 = Date.now();
  console.log("");
  step(1, 3, "Creating directory");
  mkdirSync(targetDir, { recursive: true });
  ok(`${name}/ created`);

  step(2, 3, `Writing ${plan.files.length} files`);
  for (const f of plan.files) {
    try {
      const fp = join(targetDir, f.path);
      mkdirSync(dirname(fp), { recursive: true });
      writeFileSync(fp, f.content, "utf8");
      ok(f.path);
    } catch (e) { fail(`${f.path}: ${e.message}`); }
  }

  step(3, 3, "Logging build");
  try {
    const memDir = join(homedir(), ".bro");
    mkdirSync(memDir, { recursive: true });
    const logPath = join(memDir, "builds.json");
    let log = [];
    try { log = JSON.parse(readFileSync(logPath, "utf8")); } catch {}
    log.unshift({ ts: Date.now(), idea, stack: plan.stack, name, dir: targetDir, files: plan.files.length });
    writeFileSync(logPath, JSON.stringify(log.slice(0, 50), null, 2));
    ok(`logged (${log.length} builds total)`);
  } catch {}

  const dur = ((Date.now()-t0)/1000).toFixed(1);
  console.log("");
  box(`\u{1F680} SHIPPED in ${dur}s`, [
    `${c.green}${c.B}${name}${c.R} ready at:`,
    `  ${c.cyan}${targetDir}${c.R}`, "",
    `${c.gold}${plan.files.length}${c.R} files written`, "",
    `${c.B}Next:${c.R}`,
    `  ${c.cyan}cd ${name}${c.R}${plan.nextSteps ? `  ${c.D}# ${plan.nextSteps}${c.R}` : ""}`
  ], c.green);

  if (ownsRl) rl.close();
}

if (typeof process.argv[1] === "string" && import.meta.url === `file://${process.argv[1].replace(/\\\\/g, "/")}`) {
  buildWizard(process.argv.slice(2).join(" ")).catch(e => { console.error(c.red + "ERR: " + c.R + e.message); process.exit(1); });
}
