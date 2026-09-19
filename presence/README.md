# the circle

A close-circle presence app: invite-only, live status derived from sensor
state, one auto-generated signature per person, one plain-language line per
person. No dashboards, no feeds, no likes. One person, one voice.

This is the smallest true slice of the bigger vision. The bigger vision —
"soul signature", "one person one voice", "what do people actually want" —
lives in the story around it and in the modules below, but it is **not** the
app. The app is five people on one page. If it doesn't work with five, it
wouldn't work with five billion.

## What's here

| File | What it is |
| --- | --- |
| `signature.mjs` | A deterministic visual identity computed from *how you actually live* (activity by hour-of-day). Same patterns → same signature. You can't claim one; you earn it. |
| `summarize.mjs` | Turns raw sensor state into the one line a human wants to read. "Sam at home — headphones on, low battery." |
| `consensus.mjs` | One person, one voice. Append-only vote log, honest tallies (never forced to 100%), changing your mind is allowed but the history stays. |
| `server.mjs` | Zero-dependency HTTP server (Node 18+), JSON file store, the circle page, and a small API. |
| `simulate.mjs` | Fake sensors so the demo works without phones. Pushes a believable day for each member. |
| `test.mjs` | 25 tests: signature determinism, summaries, consensus, server smoke. |

## Run it

```bash
node server.mjs        # → http://localhost:8787
node simulate.mjs      # in another terminal: fill the circle with a believable day
```

Then open http://localhost:8787 — you'll see each person's signature mark,
their one-line status, and a question on the floor you can vote on as "you".

## API

```
GET  /api/members                → members with signatures, one-liners, summary
POST /api/members/:id/status     → push a sensor reading {activity, location, headphones, battery, charging}
GET  /api/signature/:id          → a member's signature in isolation
GET  /api/questions              → all questions with honest tallies
POST /api/questions              → put a question on the floor {title, choices?}
POST /api/questions/:id/vote     → {memberId, choice} — one voice per memberId
```

## The honest framing

The cosmic version — "unify the world, end wars" — is the story this thing
could earn. It is not the build. The build is: **can your people actually be
heard, honestly, by your system?** That's the test this repo exists for.

The chain, in order:

1. Presence → trust between people
2. Trust → identity (one real human)
3. Identity → one person, one voice
4. One voice → "what do your people actually want"
5. That, at scale → consensus instead of war

This repo is steps 1–3, at five-person scale, working. Step 5 is the last
step, not the first.