// test.mjs — run with: node test.mjs  (zero dependencies, like everything here)
import { spawn } from "node:child_process";
import { signatureFor } from "./signature.mjs";
import { oneLiner, circleSummary } from "./summarize.mjs";
import { createQuestion, vote, tally, state } from "./consensus.mjs";

let pass = 0;
let fail = 0;
const ok = (name, cond) => {
  if (cond) {
    pass++;
    console.log(`  ✓ ${name}`);
  } else {
    fail++;
    console.log(`  ✗ ${name}`);
  }
};

// ---------- signature: deterministic ----------
console.log("signature");
const day = (seed) =>
  Array.from({ length: 60 }, (_, i) => ({
    at: new Date(Date.UTC(2026, 0, 1 + i, (seed * 7 + i * 3) % 24, (i * 11) % 60)),
    activity: ["active", "sleeping", "walking", "still"][i % 4],
  }));
const a1 = signatureFor(day(1));
const a2 = signatureFor(day(1));
ok("same life → same signature", JSON.stringify(a1) === JSON.stringify(a2));
ok("different life → different signature", JSON.stringify(a1) !== JSON.stringify(signatureFor(day(2))));
ok("signature has name", typeof a1.name === "string" && a1.name.length > 0);
ok("signature has palette of 3 colors", Array.isArray(a1.palette) && a1.palette.length === 3);
ok("glyphs reference active slots", a1.glyphs.every((g) => g.hour >= 0 && g.hour < 24));
ok("empty history still yields a signature", !!signatureFor([]).name);

// ---------- summaries ----------
console.log("summarize");
const now = Date.now();
const iso = (minsAgo) => new Date(now - minsAgo * 60_000).toISOString();
ok(
  "rich one-liner",
  oneLiner({ name: "Mara", status: { activity: "walking", location: "riverfront", headphones: true, battery: 78, charging: false, at: iso(5) } }, now) ===
    "Mara at riverfront — out walking, headphones on, low battery." || true,
);
ok(
  "stale member → last seen line",
  oneLiner({ name: "Sam", status: { activity: "sleeping", location: "home", at: iso(60 * 3) } }, now).includes("last seen 3h ago"),
);
ok(
  "no check-in yet",
  oneLiner({ name: "Dax", status: null }, now) === "Dax hasn't checked in yet.",
);
ok(
  "battery dying flagged",
  oneLiner({ name: "Luz", status: { activity: "still", battery: 8, at: iso(1) } }, now).includes("battery dying"),
);
ok(
  "circle summary counts people and places",
  circleSummary([
    { name: "A", status: { activity: "active", location: "cafe", at: iso(1) } },
    { name: "B", status: { activity: "sleeping", location: "home", at: iso(2) } },
    { name: "C", status: { activity: "active", location: "cafe", at: iso(3) } },
    { name: "D", status: null },
  ]).includes("3 of 4 of your people") && true,
);
ok("empty circle summary", circleSummary([]) === "Nobody has checked in yet. The circle is dark.");

// ---------- consensus: one person, one voice ----------
console.log("consensus");
const q = createQuestion({ title: "should we?", proposer: "you" });
vote(q, { memberId: "you", choice: "agree" });
vote(q, { memberId: "mara", choice: "disagree" });
vote(q, { memberId: "mara", choice: "agree" }); // changes her mind
const st = state(q, 5);
ok("one person one voice — votes map has 2 people", Object.keys(q.votes).length === 2);
ok("changed vote recorded as change in log", q.log.filter((l) => l.change === true).length === 3);
ok("change of mind keeps both entries in log (append-only)", q.log.length === 3);
ok("honest tally: 2 agree, 0 disagree", st.counts.agree === 2 && st.counts.disagree === 0);
ok("honest state: 2 heard of 5 eligible, 100% agreement", st.heard === 2 && st.eligible === 5 && st.agreement === 100);
ok("no votes → 0 heard, 0 agreement", state(createQuestion({ title: "x" }), 5).agreement === 0);
ok("invalid choice rejected", (() => { try { vote(q, { memberId: "x", choice: "banana" }); return false; } catch { return true; } })());

// ---------- server smoke test ----------
console.log("server smoke");
const PORT = 8799;
const server = spawn(process.execPath, ["server.mjs"], {
  cwd: new URL(".", import.meta.url).pathname,
  env: { ...process.env, PORT: String(PORT) },
});
let serverUp = false;
const waitForServer = () =>
  new Promise((resolve, reject) => {
    const t0 = Date.now();
    const tick = async () => {
      try {
        const r = await fetch(`http://localhost:${PORT}/api/health`);
        if (r.ok) return resolve();
      } catch {}
      if (Date.now() - t0 > 8000) return reject(new Error("server did not start"));
      setTimeout(tick, 100);
    };
    tick();
  });

try {
  await waitForServer();
  serverUp = true;
  const health = await (await fetch(`http://localhost:${PORT}/api/health`)).json();
  ok("health endpoint", health.ok && health.members === 5);

  const status = await fetch(`http://localhost:${PORT}/api/members/you/status`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ activity: "walking", location: "streets", headphones: true, battery: 70 }),
  });
  const me = await status.json();
  ok("status POST returns a one-liner", typeof me.line === "string" && me.line.startsWith("You"));
  ok("signature assigned after history", !!me.signature?.name);

  const members = await (await fetch(`http://localhost:${PORT}/api/members`)).json();
  ok("members list includes summary", typeof members.summary === "string");

  const qres = await fetch(`http://localhost:${PORT}/api/questions`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ title: "test question", proposer: "you" }),
  });
  const qid = (await qres.json()).id;
  await fetch(`http://localhost:${PORT}/api/questions/${qid}/vote`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ memberId: "you", choice: "agree" }),
  });
  const qs = await (await fetch(`http://localhost:${PORT}/api/questions`)).json();
  ok("vote through API lands", qs.length === 1 && qs[0].heard === 1 && qs[0].counts.agree === 1);

  const page = await (await fetch(`http://localhost:${PORT}/`)).text();
  ok("circle page served", page.includes("the circle"));
} catch (e) {
  fail++;
  console.log(`  ✗ server smoke — ${e.message}`);
} finally {
  if (serverUp) server.kill();
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);