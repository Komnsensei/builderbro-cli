// simulate.mjs — fake sensors so the circle demos without phones.
// Pushes a believable day for each member: morning walks, work, lunch out,
// headphones on, battery draining, sleep at night. Run alongside server.mjs.

const BASE = process.env.BASE || "http://localhost:8787";

const SCENES = {
  you:  [
    { at: "08:20", activity: "active", location: "home", headphones: true, battery: 91, charging: false },
    { at: "09:05", activity: "walking", location: "streets", headphones: true, battery: 88, charging: false },
    { at: "09:40", activity: "active", location: "studio", headphones: false, battery: 84, charging: false },
    { at: "12:30", activity: "walking", location: "streets", headphones: true, battery: 71, charging: false },
    { at: "13:10", activity: "active", location: "cafe", headphones: true, battery: 66, charging: false },
    { at: "17:45", activity: "still", location: "home", headphones: false, battery: 42, charging: true },
    { at: "22:00", activity: "sleeping", location: "home", headphones: false, battery: 88, charging: true },
  ],
  mara: [
    { at: "07:50", activity: "active", location: "home", headphones: false, battery: 97, charging: false },
    { at: "08:30", activity: "transit", location: "metro", headphones: true, battery: 93, charging: false },
    { at: "09:15", activity: "active", location: "office", headphones: false, battery: 90, charging: false },
    { at: "12:45", activity: "walking", location: "riverfront", headphones: true, battery: 78, charging: false },
    { at: "14:00", activity: "active", location: "office", headphones: false, battery: 72, charging: false },
    { at: "18:30", activity: "still", location: "gym", headphones: true, battery: 51, charging: false },
    { at: "21:40", activity: "active", location: "home", headphones: true, battery: 38, charging: true },
    { at: "23:10", activity: "sleeping", location: "home", headphones: false, battery: 71, charging: true },
  ],
  dax:  [
    { at: "06:40", activity: "walking", location: "trail", headphones: true, battery: 88, charging: false },
    { at: "08:00", activity: "active", location: "garage", headphones: true, battery: 82, charging: false },
    { at: "11:30", activity: "still", location: "garage", headphones: true, battery: 64, charging: false },
    { at: "13:00", activity: "walking", location: "market", headphones: false, battery: 58, charging: false },
    { at: "15:20", activity: "transit", location: "road", headphones: false, battery: 47, charging: false },
    { at: "19:10", activity: "active", location: "home", headphones: false, battery: 31, charging: true },
    { at: "23:30", activity: "sleeping", location: "home", headphones: false, battery: 63, charging: true },
  ],
  luz:  [
    { at: "08:00", activity: "active", location: "home", headphones: false, battery: 95, charging: false },
    { at: "09:20", activity: "walking", location: "school", headphones: true, battery: 90, charging: false },
    { at: "12:00", activity: "active", location: "library", headphones: true, battery: 76, charging: false },
    { at: "15:30", activity: "walking", location: "streets", headphones: true, battery: 61, charging: false },
    { at: "16:15", activity: "active", location: "cafe", headphones: true, battery: 55, charging: false },
    { at: "19:00", activity: "still", location: "home", headphones: true, battery: 39, charging: true },
    { at: "22:40", activity: "sleeping", location: "home", headphones: false, battery: 74, charging: true },
  ],
  sam:  [
    { at: "09:10", activity: "active", location: "home", headphones: false, battery: 89, charging: false },
    { at: "10:00", activity: "still", location: "home", headphones: false, battery: 86, charging: false },
    { at: "13:20", activity: "transit", location: "metro", headphones: true, battery: 70, charging: false },
    { at: "14:05", activity: "active", location: "office", headphones: false, battery: 65, charging: false },
    { at: "18:50", activity: "walking", location: "streets", headphones: true, battery: 44, charging: false },
    { at: "20:00", activity: "active", location: "home", headphones: false, battery: 37, charging: true },
    { at: "23:50", activity: "sleeping", location: "home", headphones: false, battery: 66, charging: true },
  ],
};

// Rebase the demo times onto the current clock: each scene's "HH:MM" becomes
// the nearest matching wall-clock time today, so whoever runs this at 2pm sees
// an afternoon, not a frozen script.
function rebase(scene) {
  const now = new Date();
  return scene.map((s) => {
    const [h, m] = s.at.split(":").map(Number);
    const t = new Date(now);
    t.setHours(h, m, 0, 0);
    if (t > now) t.setDate(t.getDate() - 1); // keep everything in the past
    return { ...s, at: t.toISOString() };
  });
}

async function push(memberId, s) {
  const res = await fetch(`${BASE}/api/members/${memberId}/status`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(s),
  });
  if (!res.ok) throw new Error(`${memberId}: HTTP ${res.status} — ${await res.text()}`);
  const m = await res.json();
  console.log(`  ${memberId.padEnd(4)} ${m.line}`);
}

export async function runSimulation() {
  for (const [id, scene] of Object.entries(SCENES)) {
    console.log(`\n${id}:`);
    for (const s of rebase(scene)) await push(id, s);
  }
  console.log("\ndone. open the circle →", BASE);
}

const isMain = process.argv[1] && import.meta.url === `file://${process.argv[1]}`;
if (isMain) {
  runSimulation().catch((e) => {
    console.error("simulation failed:", e.message);
    console.error("is server.mjs running? start it with: node server.mjs");
    process.exit(1);
  });
}