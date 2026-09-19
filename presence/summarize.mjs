// summarize.mjs — "thinks for the average user": turns raw sensor state into
// the one line a human would actually want to read. No dashboards, no feeds.

const MIN = 60_000;

function minsSince(at, now) {
  const t = at instanceof Date ? at : new Date(at);
  if (Number.isNaN(t.getTime())) return null;
  return Math.round((now - t.getTime()) / MIN);
}

function activityWord(s) {
  if (s.activity === "sleeping") return "sleeping";
  if (s.activity === "transit") return "on the move";
  if (s.activity === "walking") return "out walking";
  if (s.activity === "active") return "active";
  if (s.activity === "still") return "still";
  return null;
}

export function oneLiner(member, now = Date.now()) {
  const name = member.name || "someone";
  const s = member.status;
  if (!s) return `${name} hasn't checked in yet.`;

  const mins = minsSince(s.at, now);
  if (mins === null) return `${name}'s signal is garbled.`;

  const where = s.location && s.location !== "unknown" ? ` at ${s.location}` : "";
  const bits = [];
  const act = activityWord(s);
  if (act) bits.push(act);
  if (s.headphones) bits.push("headphones on");
  if (typeof s.battery === "number") {
    if (s.battery < 15) bits.push("battery dying");
    else if (s.battery < 30) bits.push("low battery");
    else if (s.charging) bits.push("charging");
  }

  if (mins > 90) {
    const h = Math.round(mins / 60);
    return `${name} — last seen ${h}h ago${where}.`;
  }
  return `${name}${where} — ${bits.length ? bits.join(", ") : "quiet"}.`;
}

export function circleSummary(members) {
  const seen = members.filter((m) => m.status);
  if (seen.length === 0) return "Nobody has checked in yet. The circle is dark.";

  const byPlace = {};
  for (const m of seen) {
    const p = m.status.location && m.status.location !== "unknown" ? m.status.location : "out there";
    byPlace[p] = (byPlace[p] || 0) + 1;
  }

  const places = Object.entries(byPlace)
    .sort((a, b) => b[1] - a[1])
    .map(([p, n]) => (n === 1 ? `1 at ${p}` : `${n} at ${p}`));

  const awake = seen.filter((m) => m.status.activity !== "sleeping").length;
  const sleeping = seen.length - awake;
  const night = sleeping > 0 ? `, ${sleeping} asleep` : "";
  const missing = members.length - seen.length;
  const tail = missing > 0 ? ` (${missing} offline)` : "";

  return `${seen.length} of ${members.length} of your people are here${night} — ${places.join(", ")}${tail}.`;
}