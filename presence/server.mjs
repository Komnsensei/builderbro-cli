// server.mjs — zero-dependency server: JSON file store + one circle page + API.
// Run: node server.mjs   (Node 18+). Data lives in presence/data/store.json.
//
// The page never shows raw sensor data. It shows one line per person and the
// circle's honest consensus state. "Thinks for the average user."

import { createServer } from "node:http";
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { signatureFor, signatureForMember } from "./signature.mjs";
import { oneLiner, circleSummary } from "./summarize.mjs";
import { createQuestion, vote, state } from "./consensus.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const PORT = process.env.PORT || 8787;
const DATA_DIR = join(__dirname, "data");
const STORE = join(DATA_DIR, "store.json");

const DEFAULT_CIRCLE = [
  { id: "you", name: "You" },
  { id: "mara", name: "Mara" },
  { id: "dax", name: "Dax" },
  { id: "luz", name: "Luz" },
  { id: "sam", name: "Sam" },
];

// ---------- store ----------
function load() {
  if (!existsSync(STORE)) {
    return { members: DEFAULT_CIRCLE.map((m) => ({ ...m, history: [], status: null })), questions: [] };
  }
  try {
    return JSON.parse(readFileSync(STORE, "utf8"));
  } catch {
    return { members: DEFAULT_CIRCLE.map((m) => ({ ...m, history: [], status: null })), questions: [] };
  }
}

function save(db) {
  mkdirSync(DATA_DIR, { recursive: true });
  writeFileSync(STORE, JSON.stringify(db, null, 2));
}

let db = load();
const saveDb = () => save(db);

// ---------- helpers ----------
function readBody(req) {
  return new Promise((resolve) => {
    let raw = "";
    req.on("data", (c) => (raw += c));
    req.on("end", () => {
      try {
        resolve(raw ? JSON.parse(raw) : {});
      } catch {
        resolve({});
      }
    });
  });
}

function json(res, code, obj) {
  res.writeHead(code, { "content-type": "application/json" });
  res.end(JSON.stringify(obj));
}

function memberView(m, now = Date.now()) {
  const history = m.history || [];
  return {
    id: m.id,
    name: m.name,
    signature: signatureForMember(history),
    line: oneLiner(m, now),
    online: !!(m.status && now - new Date(m.status.at).getTime() < 90 * 60_000),
    status: m.status || null,
  };
}

// ---------- page ----------
const PAGE = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>the circle</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: system-ui, sans-serif; background: #0b0e14; color: #e6e9ef; padding: 24px; }
  main { max-width: 720px; margin: 0 auto; }
  h1 { font-size: 20px; letter-spacing: 2px; text-transform: uppercase; opacity: .7; }
  p.lead { opacity: .6; }
  .member { display: flex; align-items: center; gap: 16px; padding: 14px 0; border-bottom: 1px solid #1c2230; }
  .sig { display: flex; flex-direction: column; align-items: center; min-width: 64px; }
  .sig .glyph { font-size: 30px; line-height: 1; }
  .sig .name { font-size: 11px; opacity: .55; margin-top: 4px; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: #3a4152; margin-right: 8px; display: inline-block; }
  .dot.on { background: #06d6a0; box-shadow: 0 0 8px #06d6a080; }
  .line { flex: 1; }
  .off { opacity: .35; }
  .summary { margin: 20px 0; padding: 14px 16px; border-radius: 10px; background: #121826; }
  .question { margin: 20px 0; padding: 16px; border-radius: 10px; background: #121826; }
  .choices { display: flex; gap: 8px; margin-top: 12px; }
  button { background: #1c2230; color: #e6e9ef; border: 1px solid #2a3247; border-radius: 8px; padding: 8px 14px; cursor: pointer; }
  button:hover { background: #273149; }
  .counts { margin-top: 12px; font-size: 13px; opacity: .75; }
  footer { margin-top: 40px; opacity: .4; font-size: 12px; }
</style>
</head>
<body>
<main>
  <h1>the circle</h1>
  <p class="lead" id="summary">loading…</p>
  <div id="members"></div>
  <h1 style="margin-top:32px">one question</h1>
  <div id="questions"></div>
  <footer>one person, one voice. no bots. no feed — just people.</footer>
</main>
<script>
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function refresh() {
  const [mres, qres] = await Promise.all([fetch("/api/members"), fetch("/api/questions")]);
  const { members, summary } = await mres.json();
  const questions = await qres.json();
  document.getElementById("summary").textContent = summary;
  document.getElementById("members").innerHTML = members.map((m) => {
    const colors = m.signature.palette;
    const dot = m.online ? '<span class="dot on"></span>' : '<span class="dot"></span>';
    return \`<div class="member \${m.online ? "" : "off"}">
      <div class="sig">
        <span class="glyph" style="color:\${colors[0]}">\${esc(m.signature.glyphs[0]?.glyph || "◌")}</span>
        <span class="name">\${esc(m.signature.name)}</span>
      </div>
      <div class="line">\${dot}\${esc(m.line)}</div>
    </div>\`;
  }).join("");
  document.getElementById("questions").innerHTML = questions.length ? questions.map((q) => {
    const heard = q.heard, eligible = q.eligible, agree = q.agreement;
    const counts = q.counts ? Object.entries(q.counts).map(([c, n]) => \`\${n} \${c}\`).join(" · ") : "";
    return \`<div class="question">
      <strong>\${esc(q.title)}</strong> <span style="opacity:.5">— proposed by \${esc(q.proposer)}</span>
      <div class="choices">\${q.choices.map((c) => \`<button onclick="cast('\${q.id}','\${c}')">\${esc(c)}</button>\`).join("")}</div>
      <div class="counts">\${heard} of \${eligible} voices heard · \${agree}% agreement · \${counts}</div>
    </div>\`;
  }).join("") : "<p class='off'>No question on the floor yet.</p>";
}
async function cast(qid, choice) {
  await fetch(\`/api/questions/\${qid}/vote\`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ memberId: "you", choice }),
  });
  refresh();
}
refresh();
setInterval(refresh, 15_000);
</script>
</body>
</html>`;

// ---------- routes ----------
const routes = {
  async "GET /"(req, res) {
    res.writeHead(200, { "content-type": "text/html" });
    res.end(PAGE);
  },
  "GET /api/members"(req, res) {
    const now = Date.now();
    json(res, 200, {
      members: db.members.map((m) => memberView(m, now)),
      summary: circleSummary(db.members.map((m) => memberView(m, now))),
    });
  },
  async "POST /api/members/:id/status"(req, res, params) {
    const body = await readBody(req);
    const m = db.members.find((x) => x.id === params.id);
    if (!m) return json(res, 404, { error: "no such member" });
    const now = Date.now();
    const status = {
      activity: body.activity || "still",
      location: body.location || "unknown",
      headphones: !!body.headphones,
      battery: typeof body.battery === "number" ? body.battery : 100,
      charging: !!body.charging,
      at: body.at || new Date(now).toISOString(),
    };
    m.status = status;
    m.history = m.history || [];
    m.history.push({ at: status.at, activity: status.activity });
    if (m.history.length > 2000) m.history = m.history.slice(-2000);
    saveDb();
    json(res, 200, memberView(m));
  },
  "GET /api/signature/:id"(req, res, params) {
    const m = db.members.find((x) => x.id === params.id);
    if (!m) return json(res, 404, { error: "no such member" });
    json(res, 200, { id: m.id, name: m.name, signature: signatureForMember(m.history || []) });
  },
  async "POST /api/questions"(req, res) {
    const body = await readBody(req);
    if (!body.title) return json(res, 400, { error: "title required" });
    const q = createQuestion({
      title: body.title,
      choices: body.choices || ["agree", "disagree", "abstain"],
      proposer: body.proposer || "you",
    });
    db.questions.push(q);
    saveDb();
    json(res, 201, state(q, db.members.length));
  },
  "GET /api/questions"(req, res) {
    json(res, 200, db.questions.map((q) => state(q, db.members.length)));
  },
  async "POST /api/questions/:id/vote"(req, res, params) {
    const q = db.questions.find((x) => x.id === params.id);
    if (!q) return json(res, 404, { error: "no such question" });
    const body = await readBody(req);
    if (!body.memberId) return json(res, 400, { error: "memberId required" });
    try {
      vote(q, { memberId: body.memberId, choice: body.choice });
    } catch (e) {
      return json(res, 400, { error: e.message });
    }
    saveDb();
    json(res, 200, state(q, db.members.length));
  },
  "GET /api/health"(req, res) {
    json(res, 200, { ok: true, members: db.members.length, questions: db.questions.length });
  },
};

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  const key = `${req.method} ${url.pathname}`;
  for (const [route, handler] of Object.entries(routes)) {
    const [method, pattern] = route.split(" ");
    if (req.method !== method) continue;
    const parts = pattern.split("/").filter(Boolean);
    const pathParts = url.pathname.split("/").filter(Boolean);
    if (parts.length !== pathParts.length) continue;
    const params = {};
    let ok = true;
    for (let i = 0; i < parts.length; i++) {
      if (parts[i].startsWith(":")) params[parts[i].slice(1)] = decodeURIComponent(pathParts[i]);
      else if (parts[i] !== pathParts[i]) { ok = false; break; }
    }
    if (ok) return handler(req, res, params);
  }
  json(res, 404, { error: "not found" });
});

server.listen(PORT, () => {
  console.log(`the circle is live → http://localhost:${PORT}`);
  console.log(`simulate sensors:  node simulate.mjs   (or curl the /status endpoints)`);
});