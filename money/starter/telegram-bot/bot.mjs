// Telegram FAQ bot — zero-dependency starter (Node 18+ has global fetch)
// Usage: BOT_TOKEN=... ADMIN_CHAT_ID=... node bot.mjs
// - Answers FAQ questions from faq.json (keyword match)
// - Escalates anything it doesn't answer to ADMIN_CHAT_ID via forward
// - Never stores state; survives restarts (offset comes from /health-check
//   by just re-polling with last offset lost — acceptable for starter scale)

import { readFileSync } from "node:fs";

const TOKEN = process.env.BOT_TOKEN;
const ADMIN = process.env.ADMIN_CHAT_ID;
if (!TOKEN || !ADMIN) {
  console.error("Set BOT_TOKEN and ADMIN_CHAT_ID env vars.");
  process.exit(1);
}

const API = `https://api.telegram.org/bot${TOKEN}`;
const FAQ = JSON.parse(readFileSync(new URL("./faq.json", import.meta.url), "utf8"));

async function call(method, body) {
  const r = await fetch(`${API}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) console.error(`API ${method} failed:`, r.status, await r.text());
  return r.json().catch(() => ({}));
}

function answerFor(text) {
  const q = (text || "").toLowerCase();
  for (const item of FAQ) {
    if (item.keywords.some((k) => q.includes(k.toLowerCase()))) return item.answer;
  }
  return null;
}

async function handle(update) {
  const msg = update.message;
  if (!msg || !msg.text) return;
  const { chat, text, message_id, from } = msg;

  if (text.startsWith("/start")) {
    return call("sendMessage", {
      chat_id: chat.id,
      text: FAQ._welcome || "Hi! Ask me anything — I answer instantly. For anything else, a human jumps in.",
    });
  }

  const answer = answerFor(text);
  if (answer) {
    return call("sendMessage", { chat_id: chat.id, text: answer, reply_to_message_id: message_id });
  }

  // Escalate: forward real requests to the human. Never drop them.
  await call("forwardMessage", {
    chat_id: ADMIN,
    from_chat_id: chat.id,
    message_id,
  });
  await call("sendMessage", {
    chat_id: chat.id,
    text: FAQ._escalation || "A human has been notified — they'll reply shortly.",
  });
  console.log(`ESCALATED from ${chat.id} (${from?.username || "no-user"}): ${text.slice(0, 80)}`);
}

async function loop() {
  let offset = 0;
  console.log("FAQ bot running…");
  for (;;) {
    try {
      const r = await fetch(`${API}/getUpdates?timeout=25&offset=${offset}`);
      const data = await r.json();
      for (const u of data.result || []) {
        offset = u.update_id + 1;
        await handle(u).catch((e) => console.error("handler error:", e));
      }
    } catch (e) {
      console.error("poll error:", e.message);
      await new Promise((ok) => setTimeout(ok, 3000));
    }
  }
}

loop();