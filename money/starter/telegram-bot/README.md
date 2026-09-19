# Telegram FAQ bot — starter

Zero-dependency support bot: answers FAQ keywords instantly, escalates
everything else to a human. Built for Offer 1 delivery (7-day productized bot).

## Run it

```bash
BOT_TOKEN=<token-from-@BotFather> ADMIN_CHAT_ID=<your-chat-id> node bot.mjs
```

Get your chat id: message [@userinfobot](https://t.me/userinfobot) on Telegram.

## Edit the answers

Open `faq.json` — each item is a set of `keywords` + an `answer`. No code
changes needed. The `_welcome` and `_escalation` fields are the generic messages.

## What it does

- `/start` → welcome message
- Message matches a FAQ keyword → instant answer, replies to the original message
- Anything else → forwards the message to `ADMIN_CHAT_ID` and tells the user a human is on it
- Never drops messages; logs escalations to stdout

## Deployment

- Any always-on box: VPS, Railway free tier, Replit, or a Termux phone in a pinch
- Read `BOT_TOKEN` from the environment — never commit it
- Vercel: switch to webhook mode (`setWebhook`) using the same qrbtc-api deploy muscle

## Next steps when a client buys add-ons

- **Payments (+$199):** add `/pay` → invoice link (Stripe Payment Link or crypto)
- **Broadcast (+$149):** wrap `sendMessage` in a loop over stored chat ids (add a tiny SQLite or JSON store)