# Delivery starter-stack

Goal: **every client job ships in 1–2 days** by starting from a proven template,
not from zero. This folder (and `starter/`) is the reusable core. Copy it per
client, swap the config, add the client's specifics.

## The stack

| Piece | What it is | Where |
|---|---|---|
| Telegram bot skeleton | Zero-dependency polling bot: FAQ answers + human escalation | `starter/telegram-bot/` |
| FAQ config | Client's Q&A in a JSON file, no code changes needed | `starter/telegram-bot/faq.json` |
| Scraper/automation base | For Offer 2 — script + scheduler + log pattern | (build on first Offer 2 job, then templatize) |

## Per-client delivery loop (the actual 7 days)

**Day 1:** Deposit received → copy starter folder → rename → collect FAQ + escalation chat from client.
**Day 2:** Bot answers work; test with client in a private group.
**Day 3:** Escalation wired; payment/booking add-on if sold.
**Day 4:** Deploy (see below). Give client the bot username.
**Day 5:** Client feedback round.
**Day 6:** Fixes + polish.
**Day 7:** Handover doc (login, how to edit FAQ, how to restart) + invoice balance.

## Deployment (offers 1–2)

- **Zero-dependency rule first:** prefer `node bot.mjs` somewhere always-on
  (a $5 VPS, Replit, Railway free tier, or a spare phone/Termux box in a pinch).
- Vercel serverless works too for the Telegram **webhook** variant — qrbtc-api
  already proves you know the Vercel flow; use the same muscle.
- Never deploy with the token hardcoded — read from env (`BOT_TOKEN`).

## Delivery quality bar (non-negotiable, it's what gets testimonials)

1. Client can edit FAQ themselves (JSON is fine if they're technical; else a
   `/admin` command on the bot that lets them add answers in chat).
2. Escalation always works — never silently drop a real customer question.
3. Errors are logged with a timestamp so support is possible after handover.
4. You have 14 days of free-fix buffer — use it, it converts to referrals.

## After 3 jobs: templatize harder

- Turn your best-delivered bot into the default skeleton (this folder).
- Write a 1-page case study per client (anonymized ok) — it becomes the
  social proof on the landing page (replace the generic stats with real numbers).
- Raise prices per README rules.