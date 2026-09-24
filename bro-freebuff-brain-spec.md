# SPEC — `bro` + Freebuff as brains

**Request short name:** `bro-freebuff-brain`
**Status:** spec / pre-implementation (no code written from this document yet)
**Date:** 2026-09-21
**Scope owner:** user
**Repo:** `/mnt/sdcard/Download/builderbro` (Termux/Android)

---

## 1. The request

> "the builderbro cli and freebuff as brains"

Clarified through interview (raw answers in §3):

> Remove the model wiring from the BuilderBro CLI and put **Freebuff in the main
> seat** — it is the brain, with **Groq secondary** and **Google Vertex removed**.
> Keep the *feeling* of `bro`: its tools, its slash commands, its prompt. Add a
> command where I can pick a provider out of a live-fetched list of the LLM APIs
> on the market and give it a key. `bro` delegates a task to Freebuff and reports
> back, and nothing unverified gets reported. It has to survive nobody watching
> the terminal.

**One-sentence goal:** `bro` keeps its face, Freebuff becomes what it thinks
with, and every answer it reports has been checked.

---

## 2. Interpretation — the two hard constraints

Everything in this spec follows from two measured facts about the environment,
not from preference.

| Constraint | Measured fact | Consequence |
| --- | --- | --- |
| **C-1 — Freebuff cannot be a subprocess brain** | The wrapper (`freebuff@0.0.154`) and the 138 MB core both print the same six flags: `login`, `--continue [conversation-id]`, `--cwd`, `--trust-agents`, `-v`, `-h`. There is no print/headless/exec mode, and the package carries **no model client at all** (`http.js`/`launcher.js` reach only `codebuff.com` for release download and `registry.npmjs.org` for self-update). Args pass through verbatim: `spawn(CONFIG.binaryPath, process.argv.slice(2))`. | Freebuff cannot be called like a provider. It can only be *driven as a session*. The bridge is a pty and a queue, not an HTTP call. |
| **C-2 — one Freebuff instance per machine** | `~/.config/manicode/freebuff-instance-owner.json` is **global** (`{instanceId, pid}`), not per-project. A second instance parks in a loading animation instead of erroring — this is the likeliest cause of the inconclusive `live_mcp_probe.py` run (12.3 s to first frame, all terminal queries answered, then stuck). | The session is a **singleton resource**. The brain layer must own it, check the lock, handle a stale pid, and never spawn a second one by accident. |

The third fact is an opportunity rather than a constraint:

> **Freebuff already writes the whole conversation to disk**, structured:
> `~/.config/manicode/projects/<project>/chats/<ISO-timestamp>/` containing
> `chat-messages.json`, `log.jsonl`, `chat-meta.json`, `run-state.json`. Observed
> for the `builderbro` project: 95 messages, `chat-messages.json` 31.5 MB,
> `log.jsonl` 27.9 MB, `run-state.json` 1.05 MB.

So the bridge does **not** have to scrape an animated TUI to read a reply. It can
read the same conversation record the client writes, which turns "parse what the
screen says" into "read what was recorded".

> **Addendum, measured 2026-09-21 (`§18.7`).** The record is real but it is not
> shaped the way the first draft of this spec assumed, and three details would
> have made a naive reader silently wrong: the directory named for *now* is a
> per-launch **log** dir, not the conversation (the live one is
> `projects/builderbro/chats/2026-09-10T10-18-02.028Z`); `chat-messages.json` is a
> single-line **compact** array (0 newlines, 32.8 MB) rewritten whole, so there is
> no line to tail; and `content` is **empty for 51 of 53** assistant messages — the
> answer lives in `blocks[]`, where reasoning, tool calls and the answer text all
> sit together. `§18.7` is the contract that replaces "read the newest chat file".

---

## 3. Interview decisions (as answered)

| # | Question | Answer |
| --- | --- | --- |
| 1 | Which thing is "the builderbro cli"? | *custom:* "i want to remove the wiring and use freebuff in bro or bro directly in freebuff" |
| 2 | Who is the front door? | **I type into `bro`; it delegates a task to Freebuff and reports back** |
| 3 | Must it work unattended? | **Yes — must be unattended** |
| 4 | What does Freebuff replace? | **The cloud brains entirely** |
| 5 | Unattended, given no headless mode? | **Long-lived session, human starts it, loop posts into it** |
| 6 | How far does "remove the wiring" go? | *custom:* "freebuff as the main provider and groq secondary ... remove google vertex ... and set up a provider / command to input from a list of the known llm api on the market" |
| 7 | Who owns the tool loop? | *custom:* "i want the power of freebuff and the feeling of bro's tools" |
| 8 | What does "reports back" mean? | **Verify before reporting (refuse unverified)** |
| 9 | Session bridge mechanism (no tmux/screen/expect on this box) | **Python pty bridge (reuse what exists)** |
| 10 | Provider list source | **Live-fetched list of known APIs** |
| 11 | What counts as Freebuff unavailable? | **Only explicit failure falls back** |
| 12 | Which of bro's surface must survive | Slash commands · Telegram · TOOLS + tool loop · skills + memory/dreams/heartbeat · prompt/UI feel (**/bromance not selected**) |
| 13 | When verification refuses | **Bounce back with the reason, retry N times** |
| 14 | Expose bro's TOOLS over MCP to the session? | **Yes — Freebuff acts with bro's hands** |
| 15 | Session lifecycle | **One session reused all day, resumed across restarts** |
| 16 | Bridge loses sync? | **Never guess: hand the raw capture to the verifier** |
| 17 | Live-fetched provider list | **A sources list, not one endpoint** |
| 18 | Platform | **Windows as well** (its own bridge via ConPTY) |
| 19 | Entry point | **Keep `bro` (node `loader.mjs`) as the front door** |
| 20 | First thing that must work | **One verified round trip: bro sends a task, Freebuff acts, bro confirms** |
| 21 | Proof bar | **Full existing bar: tests + live probe + registration** |
| 22 | `/bromance` fate | **Not sure — decide during the spec** (recommendation in §11) |
| 23 | Does the Python autonomy loop also use the session? | **Decide in the spec** (recommendation in §12) |
| 24 | Non-goals | *not answered (asked twice)* — inferred in §13, **needs confirmation** |

---

## 4. Goals

- **G1 — Freebuff is the primary brain of `bro`,** driving the same interactive
  session the user would open by hand, with Groq as the only fallback hop.
- **G2 — Google Vertex is removed** from the CLI, the loader and the credential
  handling, along with the gcloud-token machinery that exists only to serve it.
- **G3 — A provider command** that lists LLM APIs fetched live from multiple
  sources, takes a key, **proves the key with a 1-token completion**, and persists
  it — so adding a provider is a user action, not a code change.
- **G4 — The feeling of `bro` survives:** the interactive CLI, its ~60 slash
  commands, TOOLS + tool loop, Telegram, skills, memory/dreams/heartbeat and the
  prompt/UI feel are all still there and still work.
- **G5 — Freebuff acts with bro's hands**, not only its own: bro's TOOLS are
  exposed to the session over MCP alongside the existing `builderbro__` verifier
  tools.
- **G6 — Nothing unverified is reported.** A refusal is a first-class outcome;
  an unverifiable claim is bounced back with its reason and retried a bounded
  number of times, then refused.
- **G7 — It survives nobody watching** — the bridge is a background-capable
  supervisor, not a keyboard macro, and a desync degrades to the fallback hop
  instead of manufacturing a reply.

## 5. Non-goals (inferred — see §13 for confirmation)

- Not reverse-engineering Freebuff's backend API or using `credentials.json`
  against an undocumented endpoint.
- Not patching/impersonating the Freebuff client to create a headless mode.
- Not reworking the Python autonomy loop, `autonomy.py`'s verified loop, or the
  1,000-cycle drift study.
- Not redesigning Telegram or the internals of the slash commands.
- Not extending RAG / memory / verifier capability — the verified stack is
  consumed as-is.
- Not packaging/installers/CI for other machines.

---

## 6. Measured context (evidence, not assumption)

### 6.1 The CLI today

| Fact | Value |
| --- | --- |
| Entry point | `bro` → `node loader.mjs` → interactive: imports `bromance.mjs` into `globalThis.__bromance`, then `cli.mjs`. Pipe mode: `echo code \| bro "fix this"` |
| `cli.mjs` | 4537 lines; `askChat()` at line 1957; `LOCAL_CFG` at 87; `TOOLS` at 2083; `MAX_DEPTH = 5` at 892 |
| TOOLS | `exec read list append mkdir cp mv rm find web broadcast tg_groups tg_approve tg_unapprove github scar` |
| Tool loop | bracket-tool protocol: `extractT()` → `runT()` → results pushed back into `chatHistory`, `MAX_DEPTH` bounded, repeated-failure break |
| Slash commands | `commandList` at line 95 — 60+ entries (`/help /status /queue /memory /thought /build /skills /models /model /self /scan ...`) |
| Brain (interactive) | `askChat`: local (`LOCAL_MODEL_URL`) → Vertex (`gcloud auth print-access-token`, `GCP_PROJECT_ID`, `GCP_MODEL`, `GCP_REGION`) |
| Brain (pipe mode) | `loader.mjs`: local → Vertex → Base44 → Groq → OpenAI |
| Credentials touched | `BASE44_APP_ID/TOKEN`, `GROQ_KEY`/`GROQ_API_KEY`, `OPENAI_API_KEY`, `VERTEX_OAUTH_TOKEN`, `GCP_PROJECT_ID`, `LOCAL_MODEL_URL`, `BRO_LOCAL_FALLBACK` |
| `exec` guard | regex block on `rm -rf /`, `format [a-z]:`, `shutdown`; `maxBuffer` 5 MB; 60 s timeout; PowerShell on Windows |
| Telegram | admin chat + group replies (`tgProcessGroupMessage` runs the same tool loop) |
| Skills / state | `~/.bro/` — `skills.json`, `custom-skills.json`, `memory.json`, `dreams.json`, `heartbeat.json`, `autopilot.json`, `sessions/`, `tools/`, `upgrades/` |

### 6.2 The Python stack (the verification half)

| Fact | Value |
| --- | --- |
| MCP server | `builderbro_mcp.py` (668 lines) — 5 tools: `verify_claim`, `memory_recall`, `memory_record`, `rag_ask`, `evidence_audit`; stdlib-only stdio JSON-RPC 2.0 |
| Registry | `.agents/mcp.json` — **this is where the loader looks** (`<cwd>/.agents`, `<cwd>/../.agents`, `~/.agents`); entry is a `strictObject` (`type`/`command`/`args`/`env` only, no `cwd`) |
| Tool naming | `server__tool` (split on literal `__`) → a real session sees `builderbro__verify_claim` etc. |
| Free-provider cascade | `brain_cascade.py` (731 lines): registry at line 66 = `groq, cerebras, gemini, openrouter, mistral, github` (+ local); multi-key hops (`GROQ_API_KEY_2..4` present in `.env`); cooldown classes (rate-limit 60 s, server 30 s, auth 3600 s, blocked 300 s, payment 3600 s); health file `<residence>/provider-health.json`; `PROBE_PROMPT`/`PROBE_VERDICTS` at 535/539 |
| Runtime flags | `agent_runtime.py`: `--check --chat --goal --reflex --memory --activate --providers --ping --ping-chat` |
| pty harness (exists) | `live_mcp_probe.py` — spawns the TUI on a pty, answers the kitty/DECRQM terminal queries, strips the prompt echo before classifying, reports `loaded` / `not-loaded` / `inconclusive` |
| Suite | 551 tests, all green (`e851ec7`), published records checksummed byte-identical per run |

### 6.3 Environment

| Fact | Value |
| --- | --- |
| Freebuff | `freebuff@0.0.154` npm global → `~/.config/manicode/freebuff` (138 MB core); credentials `~/.config/manicode/credentials.json` (0600) |
| Session state | `~/.config/manicode/projects/<project>/chats/<ts>/{chat-messages.json,log.jsonl,chat-meta.json,run-state.json}`; `chat-meta.json` = `{messageCount, firstPrompt, messagesSize, messagesMtimeMs}` |
| Instance lock | `~/.config/manicode/freebuff-instance-owner.json` = `{instanceId, pid}` — **global** |
| Model in use | `settings.json` → `freebuffModel: "deepseek/deepseek-v4-flash"` |
| Multiplexers | `tmux` ✗ `screen` ✗ `expect` ✗ `socat` ✗ — `script` ✓ `nohup` ✓ `setsid` ✓ |
| Keys present (names only) | `GROQ_API_KEY`, `GROQ_API_KEY_2..4`, `CEREBRAS_API_KEY`, `OPENAI_API_KEY`, `LOCAL_MODEL`, `VERTEX_OAUTH_TOKEN`, `GCP_PROJECT_ID`, `DRIVE_RESIDENCE`, `QIH_RESIDENCE`, `QIH_DRIVE_REMOTE` |
| Local models | `ollama` at `/usr/local/bin/ollama`, not running, **no models pulled** |

---

## 7. Architecture

```
                      ┌───────────────────────────────────────────────┐
  user types ────────▶│  bro   (node loader.mjs → cli.mjs)            │
                      │  prompt/UI · slash commands · TOOLS · Telegram │
                      └───────┬───────────────────────┬───────────────┘
                              │ 1. brain request       │ 5. verdict
                              ▼                        │
                  ┌───────────────────────┐            │
                  │  brain router         │            │
                  │  (bro_brain.mjs)      │            │
                  │  freebuff → groq      │            │
                  └───┬───────────────┬───┘            │
       freebuff hop   │               │  groq hop      │
                      ▼               ▼                │
        ┌──────────────────────┐   ┌──────────────────┐│
        │ bro_bridge.py        │   │ brain_cascade.py ││
        │ pty supervisor       │   │ (existing)       ││
        │ prompt→session→reply  │   └──────────────────┘│
        └───┬──────────────┬───┘                        │
            │ pty writes   │ disk reads                 │
            ▼              ▼                            │
   ┌────────────────┐  ┌──────────────────────────────┐ │
   │ freebuff TUI   │  │ projects/<p>/chats/<ts>/     │ │
   │ (singleton)    │──▶ chat-messages.json·log.jsonl │ │
   └───────┬────────┘  └──────────────────────────────┘ │
           │ MCP tools (server__tool)                   │
           ▼                                            │
   ┌──────────────────────────┐   ┌─────────────────────────────┐
   │ .agents/mcp.json         │──▶│ builderbro_mcp.py (verified │
   │  · builderbro__ (verify, │   │ stack: verifier·memory·RAG) │
   │    memory, rag)          │   └─────────────┬───────────────┘
   │  · bro__ (exec, read, web)                  │
   └──────────────────────────┘                 ▼
   ▲                          ┌──────────────────────────────────────┐
   │ 4. action via bro's TOOLS│  evidence → residence ledger ·       │
   └──────────────────────────│  CAPABILITY_INVENTORY · LOG          │
                              └──────────────────────────────────────┘
```

**Turn sequence (the one that must work first):**

1. `bro` receives a task (typed, or posted by the unattended loop).
2. The brain router asks the Freebuff hop. **Only an explicit failure** (no
   session, dead pid, dead pty, explicit error/timeout) falls through to Groq.
3. The bridge writes the task into the live session and reads the reply from the
   conversation record (disk-first), never from a guess about the screen.
4. Anything the task required to be true is checked through the verification
   stack — including anything Freebuff did with `bro__` tools, which run inside
   bro and are therefore recordable at the choke point.
5. The verdict is either a report or a bounce-back: the reason goes back to
   Freebuff as a repair note, up to `N` retries, then a structured refusal.

---

## 8. Components

### C1 — Brain router (`bro_brain.mjs`, new)

- Replaces `askChat`'s provider body. Single place that answers "who thinks?".
- Hop order: **freebuff → groq**. Configurable later; shipped order fixed.
- Contract, per call: `ask(task, {history, signal}) → {brain, content, elapsed, attempts, fell_back, fallback_reason}`.
- **Explicit-failure-only fallback** (decision #11): the hop is considered failed
  only on (a) no session attached, (b) session process dead, (c) pty write or
  read failure, (d) explicit bridge error, (e) timeout past the configured bound.
  A slow-but-progressing answer waits — it does not fall back.
- Every hop and every fallback is **recorded** (brain, reason, latency) so the
  ledger can show provider outages on a timeline, reusing the pattern the drift
  loop already has (`brain_cascade` health snapshots).
- Removed from this path: Vertex (`askVertex`, `gcloudToken`, `gcloudProject`,
  `refreshVertexToken`, `detectGcpProjectId`, `buildVertex403Message`,
  `VERTEX_OAUTH_TOKEN`/`GCP_*`), Base44 (`askBase44`, `BASE44_*`), OpenAI
  (`askOpenAICompat` + `OPENAI_API_KEY`). See §13 Q1 on Base44/OpenAI.

### C2 — Freebuff pty bridge (`bro_bridge.py`, new)

Reuses and graduates the `live_mcp_probe.py` harness from probe to runtime.

- **Spawn:** `freebuff --cwd <repo> --trust-agents [--continue <conversation-id>]`
  on a pty, answering the terminal queries the client blocks on (`ESC[>0q`,
  `ESC[>4;1m`, `?1004$p ?1016$p ?2004$p ?2027$p ?2031$p`). Measured boot on this
  box: first frame ≈ 12.3 s. Every spawn waits for a ready frame, not a fixed sleep.
- **Lock discipline (C-2):** read `~/.config/manicode/freebuff-instance-owner.json`
  first. Own pid → OK. Live foreign pid → **refuse with an explicit error**
  (never spawn a second instance and hope). Stale pid → reclaim. This check is a
  first-class, tested function.
- **Prompt injection:** write the task as one delimited submission (the prompt is
  the bridge's own text, so it is never mistaken for model output — the false
  positive `live_mcp_probe.py` hit when its prompt echoed back is a tested
  regression case).
- **Reply channel, disk-first:** treat
  `projects/<project>/chats/<session>/chat-messages.json` + `log.jsonl` as the
  authoritative record, read from a **byte-offset watermark** taken before the
  write (files reach 30 MB; never re-read whole). Pane text is used for liveness
  and as a last-resort capture — never as the basis of a fabricated reply.
- **Never guess (decision #16):** if the reply cannot be resolved to a complete
  message, the bridge emits `{status: "unreadable", raw_capture: "…"}` and hands
  the raw capture to the verification stack. The router treats `unreadable` as an
  **explicit failure** (fallback eligible) and the record is kept.
- **Session lifecycle (decision #15):** one session, reused all day. The session
  id / conversation id / project path are stored in `~/.bro/freebuff-session.json`;
  a restart re-attaches with `--continue <conversation-id>` rather than starting
  fresh. `/freebuff status|start|stop|new|id` exposes it.
- **Unattended (decisions #3, #5):** the bridge is a long-lived supervisor process;
  `bro` posts tasks to it and it answers asynchronously, so a watchdog/cron/loop
  can post while nobody is watching. The session is started once (by `bro`, or by
  the user via a command) and left attached — "unattended" means nobody is
  watching, not that nothing was ever started.
- **Platform adapters:** `PtyAdapter` interface, POSIX backend (stdlib `pty`) and
  a **Windows backend via ConPTY** (decision #18) — Windows accepts one small
  platform-only dependency (`pywinpty` or equivalent). POSIX is the path the
  first milestone is measured on; the Windows backend lands behind the same
  interface rather than in a fork of the bridge.
- **Protocol with Node:** newline-delimited JSON on stdin/stdout
  (`{"op":"ask"|"status"|"stop"|"new", ...}`), so `bro_brain.mjs` needs no pty
  knowledge and the bridge is testable without Node.

### C3 — `bro`'s TOOLS over MCP (`bro_tools.mjs` + a Node MCP server)

- The TOOLS live in Node inside `cli.mjs`, so exposing them requires a **single
  definition**: extract `TOOLS` (and the guard) out of `cli.mjs` into
  `bro_tools.mjs`; `cli.mjs` imports it; the MCP server imports it. A test asserts
  one definition, not two.
- A new Node MCP server (`bro_mcp.mjs`) registers the same way the Python one
  does, in `.agents/mcp.json`, so a `--trust-agents` session sees
  `bro__exec`, `bro__read`, `bro__list`, `bro__find`, `bro__web`, … next to
  `builderbro__verify_claim`.
- **Choke point:** every `bro__*` action executes inside `bro`, so the guard
  (blocked-command regex, timeout, `maxBuffer`), the audit trail and the evidence
  record all apply. This is what makes "Freebuff has bro's hands" auditable
  rather than aspirational.
- `exec` stays behind the existing blocklist by default; the spec adds an
  explicit **approval mode** decision (§13 Q4).

### C4 — Verification gate (existing stack, new call site)

- Reuses `verifier.py` (promotion + `DETERMINISTIC_TOOLS`), `autonomy.parse_spec`/
  `verify` (the expectation grammar and goal arm), `memory.py` (provenance
  levels: invariant / observed / volatile), `evidence_hygiene.py` (stub
  classification) — **no re-implementation**, the same rule `builderbro_mcp.py`
  already follows.
- **Bounce-back loop (decision #13):** on refusal, the reason is returned to
  Freebuff as a repair note and the task is retried up to `N` times
  (`N = 2` proposed, registered in a config file the way `loop_guard.json` is).
  Each attempt is recorded, including the reason text, so a repeated failure is
  visible as a pattern rather than a count.
- After `N`: a **structured refusal** in the repo's own vocabulary
  (`[gate-failed] step=… reason=…`) — never a smoothed-over answer. A refusal is
  **not** a transport error (the `builderbro_mcp.py` polarity rule).
- Unverifiable ≠ false: the verdict vocabulary is `confirmed` / `observed` /
  `cannot_verify`, and `cannot_verify` refuses the report but records what was
  actually checked and why it was insufficient.

### C5 — Provider command (`/provider`, decision #10, #17)

- **Sources list, not one endpoint.** Candidate sources to implement against
  (each is a public JSON/API, merged and deduped):
  1. `models.dev` — open-source model/provider database with JSON endpoints
     (provider data, model metadata, combined catalog);
  2. `awesome-free-llm-apis` (GitHub, `mnfst/awesome-free-llm-apis`) — a curated
     list of providers with permanent free tiers, OpenAI-SDK-compatible;
  3. `openrouter` `/api/v1/models` — live, keyless model list.
- **Offline:** fetched result is cached to `~/.bro/providers.json`; the command
  works from cache with a visible `stale` marker. Offline + empty cache = the
  command says so; it does not invent a list.
- **Flow:** pick a provider → paste key → **validate with a 1-token completion**
  (the `brain_cascade` probe pattern: `PROBE_PROMPT`, `PROBE_VERDICTS` — a paid
  wall or a dead key must be *reported*, not stored) → persist → show the
  resulting chain.
- Freebuff itself and Groq are pre-configured; the command is how everything else
  is added.
- **Key storage** is an open question (§13 Q3): `.env` (current behaviour) vs a
  `0600` key file in `~/.bro`. The spec's recommendation is `~/.bro` with `0600`,
  `.env` still honoured, and a startup warning if a key is found in a
  world-readable location.

### C6 — Removals (decision #1, #4, #6)

| Remove | Where |
| --- | --- |
| Vertex/gcloud brain + token/project detection + 403 diagnostics | `loader.mjs`, `cli.mjs` |
| Base44 brain | `loader.mjs` (`askBase44`), `cli.mjs` (`APP`/`TOKEN`/`CONV` usage in the chat path) |
| OpenAI-compatible generic path as a *brain* | `loader.mjs` |
| Vertex/Base44 credential handling + `.env.example` entries | `.env.example`, docs |
| Stale provider diagnostics that only describe Vertex | `/status`, `/models`, provider health block |

`brain_cascade.py`'s registry is **not** deleted: it becomes the shape the
provider command writes into, and the fallback library after Groq.

### C7 — Unattended / loop integration (decision #23 → §12)

---

## 9. Interface contract (what gets built)

> The two **normative** interfaces — the wire protocol between `bro_brain.mjs` and
> `bro_bridge.py` (`§18`) and the session lock's state machine (`§19`) — are
> appendices, and they are the part of this spec an implementation is held to
> literally. Everything in this section is the user-facing summary of them.

### Commands (in `bro`)

```
/freebuff status          attached? session id? project? lock owner? last turn?
/freebuff start           spawn + attach a session (refuses if another instance lives)
/freebuff stop            end the session cleanly
/freebuff new             start a fresh conversation (current session id kept in history)
/freebuff id              print the conversation id used for --continue
/provider                 list fetched providers, pick one, add a key, validate
/provider list|add|remove|test|chain
/brain                    which brain answers now, and the hop order
/brain <freebuff|groq>    pin the brain for this session
```

### Environment

| Variable | Meaning |
| --- | --- |
| `BRO_BRAIN=freebuff,groq` | hop order (default), `freebuff` / `groq` pinning |
| `FREEBUFF_BIN` | path to the client (default: `freebuff` on `PATH`) |
| `FREEBUFF_CWD` | project directory the session runs in (default: repo root) |
| `FREEBUFF_READY_TIMEOUT_S` | boot wait (default 45 s, measured 12.3 s typical) |
| `FREEBUFF_TURN_TIMEOUT_S` | explicit-failure bound for one turn |
| `BRO_VERIFY_RETRIES` | bounce-back budget (default 2) |
| `BRO_PROVIDER_CACHE` | default `~/.bro/providers.json` |
| `GROQ_API_KEY[_2..4]` | secondary hop (already present) |

### Files

| Path | Role |
| --- | --- |
| `bro_bridge.py` | bridge (new) |
| `bro_brain.mjs` | router (new) |
| `bro_tools.mjs` | TOOLS extracted from `cli.mjs` (new) |
| `bro_mcp.mjs` | Node MCP server for `bro__*` (new) |
| `.agents/mcp.json` | registry — gains the `bro` server entry |
| `~/.bro/freebuff-session.json` | session/conversation id, project, lock state |
| `~/.bro/providers.json` | provider catalog cache |
| `~/.bro/brain.json` | hop order, pins, retry budget |
| `~/.config/manicode/freebuff-instance-owner.json` | **read-only** — the lock we must respect |

---

## 10. Test plan (full bar — decision #21)

Repo conventions apply: hermetic, `test_support.isolate_residence()` where the
residence is touched, runnable as `python3 <module>_test.py`, **negative controls
for every guard**, published records checksummed before/after.

| Module | Covers |
| --- | --- |
| `bro_bridge_test.py` (new) | lock discipline (own / live foreign / stale pid), prompt framing + echo rejection, reply resolution from a fixture chat record, `unreadable` path with a raw capture, watermark reads on a growing file, adapter interface conformance |
| `bro_mcp_test.py` (new/extended) | registry contract (`strictObject` key set, `server__tool` naming), one-definition assertion for TOOLS, choke-point guard (blocked command still blocked when it arrives over MCP), refusal-vs-`isError` polarity |
| `bro-brain-test.mjs` (new) | hop order, **explicit-failure-only** fallback (a slow answer must not fall back), fallback recorded with reason, brain pinning |
| `bro-provider-test.mjs` (new) | source merge/dedupe, cache read + `stale` marker, offline-with-empty-cache message, 1-token probe verdicts (valid / dead key / paid wall) |
| `live_freebuff_probe.py` (new, extends `live_mcp_probe.py`) | live round trip: spawn → attach → post one task → read reply → verify → report. Emits a summary JSON the way the existing probes do |
| existing suites | re-run in full; **551 tests must stay green** |

**Negative controls that must fail on purpose** (a test that cannot fail is
decoration): registry moved out of `.agents/`; a `cwd` key added to a server
entry; a **second** bridge spawned while a foreign instance owns the lock; a
reply fabricated from a pane echo; a provider key that probes as a paid wall.

**Proposed floors (to be pre-registered before the live measurement, then
measured rather than asserted):**

- bridge round trip, p95 ≤ 120 s on this box (boot alone is ~12 s);
- `unreadable` never yields a reported answer: 0 fabricated replies across the
  probe's runs;
- verification: 100 % of the unverifiable-control claims refused, 0 honest
  claims refused (the existing verifier bracket, re-used rather than re-invented);
- provider probe: distinguishes valid / dead / paid-wall on the first try for
  every provider the picker offers.

---

## 11. `/bromance` (decision #22 — recommendation)

**Recommendation: keep the catalog, drop the install plumbing.**

- Rationale: `/bromance`'s *reason to exist* was "find and wire the thing that
  makes bro smarter". Once the brain is Freebuff and providers are added through
  `/provider`, the install path is redundant — and two install paths means two
  places to audit.
- Concretely: `/bromance`, `/bromance search`, `/bromance browse`, `/bromance list`
  stay as a reference catalog; the install/wire actions are removed with the rest
  of the wiring (C6).
- If the catalog turns out to be the only place a needed connector is described,
  it is read-only and harmless; if it turns out nobody opens it in a month, it can
  be deleted then with no loss.

---

## 12. Does the Python autonomy loop also use the session? (decision #23 — recommendation)

**Recommendation: not in the first implementation; yes as a thin hop afterwards,
behind the lock.**

- The session is a **singleton** (C-2). If `drift_loop.py` and the interactive
  CLI both post into it, a stray cycle can occupy the only brain the user has.
  Ordering must therefore be explicit — a queue, with the CLI ahead of the loop —
  and that is a design, not a detail.
- The loop already runs headless on `brain_cascade` (local → free providers,
  cooldowns, health snapshots). Its problem is not the brain, it is that the
  hosted hop *asserts from priors instead of acting* (measured: 3 model-planned
  arms, 1 verified goal, both misses never called a tool).
- Therefore: **Phase 1 — CLI only.** Phase 2 (a later milestone) adds
  `BRAIN_PROVIDERS=freebuff` as a `brain_cascade` entry that is only eligible when
  the lock says the session is ours, so the unattended loop can *share* the brain
  without being able to steal it.
- Consequence to state plainly in the docs: in Phase 1 the unattended loop stays
  on Groq/Cerebras/local — "unattended Freebuff" means *posted into an attached
  session*, not *Freebuff running with nobody there*.

---

## 13. Open questions

**Q1 — Base44 and OpenAI removal.** Vertex removal is decided. The same
"remove the wiring" reading implies Base44 and the generic OpenAI-compatible path
go too (Freebuff main, Groq secondary). *Proposed:* remove both as brains; keep
the OpenAI-compatible *client shape* in the router because Groq and every
`/provider` entry speak it. **Confirm.**

**Q2 — The local Ollama hop.** `LOCAL_MODEL_URL` is the current first hop and is
central to `FREE-BRAIN.md`'s local-first thesis, but nothing is pulled on this box
and `ollama` is not running. *Proposed:* keep it as an ordinary provider entry
(registered through `/provider`, not hardcoded first), so local-first is still
possible without being an unconditional first hop. **Confirm.**

**Q3 — Where keys are stored.** `.env` (today) vs `~/.bro/keys.json` at `0600`.
*Proposed:* `~/.bro` at `0600`, `.env` still honoured, warning on a
world-readable key. **Confirm.**

**Q4 — `exec` approval mode.** Freebuff acting with `bro__exec` is the point of
decision #14, but `exec` is arbitrary shell. *Proposed:* default = existing
blocklist + per-call record; optional `approval` mode (prompt before any
write/exec) and `dry-run` mode (report what would run). **Choose the default.**

**Q5 — Adopting a session bro did not start.** If a user already has a session
open in another terminal (or the browser client), the lock is held and bro cannot
spawn. *Proposed:* refuse to spawn, detect the owner pid, and offer to attach
read-only to that project's conversation record; full control only once the other
instance exits. **Confirm**, because it decides whether the browser client and
`bro` can coexist.

**Q6 — Telegram as a brain client.** `tgProcessMessage` answers from Telegram.
Should a Telegram task go through the bridge too (one brain everywhere), or stay
on Groq (cheap, fast, no session contention)? *Proposed:* Telegram stays on the
secondary hop by default, with an opt-in.

**Q7 — Non-goals (§5).** Asked twice, unanswered. The list in §5 is inferred from
your other answers; **confirm or amend it**, since it is the boundary the spec is
held to.

**Q8 — Retry budget `N`.** Proposed `N = 2` bounce-backs. Confirm, or state a
different ceiling.

---

## 14. Milestones

M0 is what "works" means first (decision #20).

| # | Milestone | Exit criterion (measured, with a reproduce command) |
| --- | --- | --- |
| **M0 — Instrument first** | Measure the session before designing around it: boot time, the lock's real behaviour with a live foreign owner, and whether `chat-messages.json`/`log.jsonl` can be read incrementally and resolved to a complete assistant message. | A written measurement (numbers, not impressions), including one honest `inconclusive` if that is the result. **No design decision in M1+ may assume an unmeasured channel.** |
| **M1 — One verified round trip** | `bro` posts a task, Freebuff acts, `bro` verifies, and a claim that does not hold is refused. | A live transcript + summary JSON: task in → action observed → verdict out; plus the same path exercised twice in a row on one attached session. |
| **M2 — Remove the wiring** | Vertex (and per Q1, Base44/OpenAI) gone; `freebuff → groq` is the only path; missing-brain error is explicit. | `grep` proves the paths are gone; a run with no session falls back to Groq and says why; a run with neither fails loudly. |
| **M3 — The provider command** | Live-fetched sources, cache, pick, key, 1-token probe, persist, chain display. | A real key added through the command and used for a real answer; a deliberately bad key rejected with the probe's verdict. |
| **M4 — bro's hands** | `bro__*` tools over MCP, used by a real session, through the single choke point. | A live `freebuff --trust-agents` session calling a `bro__` tool, with the action recorded in bro and in the ledger. |
| **M5 — Windows adapter** | ConPTY backend behind `PtyAdapter`, same tests. | Adapter conformance suite green; state plainly what could not be tested on this box. |

---

## 15. Definition of done (decision #21)

The repo's own rule is that a capability is `verified` only if it has a
reproduction command and a number that came out of it. So this request is done
when:

1. **M0–M4 green**, each with its own measured evidence.
2. **Tests:** new modules above pass, `python3 <module>_test.py` reproduces each
   count, negative controls fail on purpose, and the **existing 551 tests stay
   green**.
3. **Live probe:** `live_freebuff_probe.py` produces a transcript + summary JSON
   for a real session, including the failure modes (no session, foreign lock,
   unreadable reply).
4. **Registration:** `SELF_IMPROVEMENT_LOG.md` carries a DETECT → RESEARCH →
   DESIGN → IMPLEMENT → TEST → REGISTER cycle per delivered piece;
   `CAPABILITY_INVENTORY.md` gains entries with reproduce commands and measured
   numbers, including a **horizon** section for what is still unmeasured.
5. **Honesty:** the docs state the known limits — the bridge is pty-driven and
   fragile by construction, the disk format is undocumented and may change on a
   Freebuff auto-update, the session is a singleton, and Windows is untested on
   this hardware.
6. **Records intact:** `q1-evidence.jsonl` and `ledger.jsonl` checksum-identical
   across the test runs.

---

## 16. Risks

| Risk | Severity | Mitigation in this spec |
| --- | --- | --- |
| Freebuff auto-updates and changes the TUI or the on-disk chat format | high | Both channels are isolated in the bridge; the probe detects breakage; `unreadable` degrades to fallback instead of guessing; no design decision depends on a private format without a measurement |
| Singleton lock collides with the user's own session or the browser client | high | Lock check is a first-class, tested function; refusal is explicit; Q5 decides the adopt/read-only path |
| pty parsing is inherently brittle | medium | Disk-first reply channel; pane only for liveness; echo-rejection regression test |
| Turn latency (boot ~12 s + generation) makes the CLI feel slower than Vertex | medium | Floors in §10 are measured, not assumed; `/brain groq` pins the fast hop; latency is shown per turn |
| "Unattended" oversold | medium | §12 states plainly that Phase 1 unattended = posted into an attached session |
| Removing wiring breaks the ~60-command surface and Telegram | medium | G4 makes surface preservation a goal; the full suite must stay green; changes are additive until M2 |
| Verification refuses so much that the CLI feels broken | medium | Bounce-back with the reason (N=2), verdict vocabulary incl. `cannot_verify`, and the retry reasons recorded so over-refusal is visible as data |

---

## 17. Appendix — assets to reuse (do not re-invent)

| Asset | Reuse for |
| --- | --- |
| `live_mcp_probe.py` | pty spawn, terminal-query answering, prompt-echo stripping, `loaded`/`inconclusive` classification |
| `builderbro_mcp.py` | stdio JSON-RPC shape, `--tools`/`--call` CLI, refusal-vs-`isError` rule |
| `.agents/mcp.json` + `builderbro_mcp_test.RegistryTest` | registry contract, `strictObject` key set, negative controls |
| `brain_cascade.py` | provider registry shape, multi-key hops, cooldowns, health file, `PROBE_PROMPT`/`PROBE_VERDICTS`, `provider_order()` |
| `verifier.py` · `memory.py` · `autonomy.parse_spec`/`verify` · `evidence_hygiene.py` | the entire verification gate — called, never copied |
| `loop_guard.py`/`loop_guard.json` | the pattern for a registered, configured retry/threshold budget |
| `SELF_IMPROVEMENT_LOG.md` | the cycle record shape to append |
| `CAPABILITY_INVENTORY.md` | status vocabulary (`verified`/`unmeasured`/`planned`) and the reproduce-command rule |
| `§18` · `§19` | the normative wire protocol and lock state machine |

---

## 18. Appendix I — Bridge wire protocol (normative)

**Parties.** `bro_brain.mjs` (client, Node) ↔ `bro_bridge.py` (server, Python).
The protocol exists so that Node needs **no pty knowledge** and the bridge is
testable without Node — the same seam rule the Python MCP servers already follow.

> **Status of every number in this appendix.** The record-format facts in `§18.7`
> are **measured** on this machine (`2026-09-21`). The remaining op names, codes
> and timeouts are **proposed** and become normative when M0 confirms them; where a
> value is a proposal it is marked *(proposed)*. No field below may be relied on by
> the implementation until M0 has confirmed the corresponding measured fact.

### 18.1 Transport and framing

- **Transport:** the bridge's stdio. `stdin` = client → bridge, `stdout` =
  bridge → client, `stderr` = human diagnostics **only** and never parsed.
- **Framing:** NDJSON — one JSON object per line, UTF-8, terminated by `\n`.
  A JSON string never contains a raw newline (they are escaped), so a line is a
  frame. Blank lines are ignored; a line that is not a JSON object is `E_PROTOCOL`.
- **Frame ceiling:** 8 MiB (`BRO_BRIDGE_MAX_FRAME`, *(proposed)*). A larger frame
  is refused with `E_PROTOCOL` and the connection stays up — a large `capture`
  payload is truncated **with an explicit `truncated: true`**, never silently.
- **Encoding:** UTF-8. Invalid bytes are replaced and flagged `encoding_repaired`
  (a terminal transcript is not guaranteed clean).
- **Directionality:** strictly one response per request, correlated by `id`.
  Multiple `req` frames may be in flight; responses may arrive out of order.

### 18.2 Envelope

```json
{"v": 1, "id": "1f2c…", "type": "req", "op": "ask", "…": "…"}
```

| Field | Required | Meaning |
| --- | --- | --- |
| `v` | yes | protocol version (integer). `hello` negotiates the effective version |
| `id` | on `req`/`res`/`err` | opaque client-generated correlation id; echoed verbatim |
| `type` | yes | `req` · `res` · `err` · `evt` |
| `op` | yes on `req` | the operation (`§18.4`) |
| `ok` | on `res`/`err` | `true` on `res`, `false` on `err` |
| `code` | on `err` | the taxonomy code (`§18.8`) |
| `retryable` | on `err` | whether a retry is meaningful |
| `fallback_eligible` | on `err` | whether the router may hop to Groq (`§18.8`) |

**Exactly one** `res` **xor** `err` is emitted per `req`, ever. A duplicate `id`
for an op that is not idempotent is `E_PROTOCOL`. Unknown fields are ignored
(forward compatible); a missing required field is `E_PROTOCOL`.

### 18.3 Frame types

| `type` | Direction | Meaning |
| --- | --- | --- |
| `req` | client → bridge | one operation |
| `res` | bridge → client | success payload for that `id` |
| `err` | bridge → client | failure for that `id`; terminal |
| `evt` | bridge → client | unsolicited, carries no `id`; may arrive at any time |

Event types: `heartbeat`, `session_state`, `queued`, `turn_progress`, `resync`,
`capture`. An `evt` is **advisory** — a client that ignores every `evt` still works.

### 18.4 Operations

| `op` | Request fields | `res` fields | Notes |
| --- | --- | --- | --- |
| `hello` | `client`, `proto_min`, `proto_max`, `pid` | `server`, `v`, `platform`, `capabilities[]`, `instance{}` | **Must be the first frame.** Capability negotiation; the bridge never assumes a capability it did not announce. |
| `attach` | `cwd`, `session_id?`, `resume: bool`, `adopt: bool` | `state`, `session_id`, `project`, `chat_dir`, `owner{pid, instanceId, ours}`, `boot_seconds` | Runs the whole lock check (`§19`). May return `E_LOCK_FOREIGN`. |
| `ask` | `turn_id`, `task`, `context?{history[], files[]}`, `priority`, `timeout_s`, `expect?{…}` | `turn_id`, `reply{text, blocks[], tools[]}`, `evidence{}`, `timings{}`, `state`, `attempt` | The main op (`§18.6`). |
| `status` | — | `lock{}`, `session{}`, `turn{}`, `watermark{}`, `queue_depth`, `uptime_s` | **Always answerable, even mid-turn.** Read-only. |
| `interrupt` | `turn_id?` | `interrupted: bool` | Best effort; the client may finish the turn anyway. |
| `new` | — | `session_id` | New conversation; the previous id is retained in history. |
| `stop` | `graceful: bool` | `stopped: bool` | Ends the *session*, not necessarily our lock ownership. |
| `capture` | `bytes?` | `raw`, `truncated` | The "never guess" escape hatch (`§18.6`); the raw pane text is handed to the verifier. |
| `shutdown` | — | — (then EOF) | Flush, release, exit. |

### 18.5 Queueing and ordering (the singleton made fair)

- `ask` is **serialized**: one turn in flight. A second `ask` while a turn runs is
  queued FIFO and answered with `evt{op:"queued", position: N}` — never dropped,
  never interleaved.
- `priority` ∈ `cli` | `loop` *(proposed)*. `cli` is served ahead of `loop`, which
  is how `§12`'s "the interactive user outranks the unattended cycle" is enforced
  rather than hoped for.
- Queue depth is bounded (`BRO_BRIDGE_QUEUE_MAX`, default 32 *(proposed)*); overflow
  is `E_QUEUE_FULL` (not fallback-eligible — the brain is fine, we are the problem).
- **Idempotency:** `turn_id` is client-generated. Re-sending an `ask` with a
  `turn_id` already completed returns the recorded result instead of posting a
  second prompt. This is what makes a client retry after a dropped bridge safe.

### 18.6 `ask` completion semantics

A turn is **complete** when all hold:

1. a **new** message with `variant: "ai"` exists whose `id` epoch (ms) is ≥ the
   epoch of the `user` message we posted;
2. that message is **not** a `mode-divider`;
3. it contains at least one `text` block with `textType != "reasoning"` **or** a
   `tool-call` block;
4. the record stops changing for `QUIET_MS` (default 1200 ms *(proposed)*).

| Situation | Result |
| --- | --- |
| Progress continues past `timeout_s` | **not** a timeout — a progressing turn waits (`§18.8`, decision #11) |
| No progress for `FREEBUFF_TURN_TIMEOUT_S` | `E_TIMEOUT_STALLED`, `retryable: true`, fallback-eligible |
| Turn ended but no complete message could be assembled | `E_UNREADABLE`, with `detail{watermark, raw_capture}` recorded and handed on |
| Chat record contradicts itself / cannot be re-synced | `E_UNREADABLE` + `evt{op:"resync"}` — **never** a fabricated reply |

The answer is the concatenation of qualifying `text` blocks. `reasoning` blocks and
`tool-call` blocks are returned **separately** as `blocks[]`/`tools[]`, because they
are evidence, not prose — and because a client that folds thinking into the answer
is exactly the "smooth over" failure `AGENT-INTEGRITY.md` forbids.

### 18.7 The conversation record — measured contract

This is the part of the protocol that **must not be guessed at**. Measured
2026-09-21 on `freebuff-metadata.json` version `0.0.180`.

**Resolving the active conversation (not the newest directory).**

```
~/.config/manicode/projects/<project>/chats/
  2026-09-20T09-58-14.114Z/log.jsonl                    ← per-launch LOG dir, 1.2 KB
  2026-09-21T02-59-13.481Z/log.jsonl                    ← per-launch LOG dir, 1.5 KB
  2026-09-10T10-18-02.028Z/                             ← the ACTIVE conversation
      chat-messages.json  32,801,056 B
      chat-meta.json             198 B
      log.jsonl           28,225,458 B
      run-state.json         936,197 B
```

Rule: a chat dir holding `chat-meta.json` + `run-state.json` is a conversation;
a dir holding only `log.jsonl` is a launch log. The client confirms the id in its
own log: `Loaded chat state from chat directory … "chatId":"…"`. The bridge
resolves the active id as the newest dir **that has** `chat-meta.json`, and
cross-checks it against that log line. The newest-by-name dir is **wrong** here by
11 days — so "newest" without the shape test is a defect, and it is a named test.

**Project key hazard (measured).** `recent-projects.json` lists both
`/mnt/sdcard/Download/builderbro` and `/sdcard/Download/builderbro`, and the project
dir is keyed by **basename** — so both map to `builderbro`. The bridge must verify
the resolved project's `recent-projects.json` path equals `FREEBUFF_CWD` (after
`realpath`) and return `E_PROJECT_AMBIGUOUS` when it cannot, rather than reading
another checkout's conversation.

**The watermark is cheap; the record is not.**

```json
{"messageCount": 100, "firstPrompt": "To instantiate this new type of agent…",
 "messagesSize": 32796151, "messagesMtimeMs": 1789970024685.6868}
```

`messageCount` equals the array length (measured 100 = 100), so **poll
`chat-meta.json` (198 B)** and read the 32 MB file only when it moves.

**`chat-messages.json` shape (measured).** A **compact single-line** JSON array —
`0` newlines in 32.8 MB, rewritten whole on each append (`[` … `]`, first byte `[`).
Consequences, all of them testable:

- there is **no line to tail** and no append-only log to read;
- the watermark is a triple `(messageCount, prefix_bytes_consumed, prefix_hash)`;
  read only the delta after `prefix_bytes_consumed`, and verify `prefix_hash`
  before trusting it. A mismatch is a `resync` event plus a full re-read — a
  recorded, visible cost, never a silent misread.

**Message object (measured).**

```json
{"id": "ai-1789969952142-dc5942ad92969", "variant": "ai", "content": "",
 "timestamp": "05:52 AM", "metadata": {"allowInlineAds": true},
 "blocks": [{"type": "text", "content": "…", "textType": "reasoning",
              "thinkingId": "thinking-29", "thinkingCollapseState": "preview"},
            {"type": "tool-call", "toolName": "read_file", "input": {…},
              "output": {…}, "toolCallId": "…", "agentId": "…"}]}
```

| Fact | Consequence |
| --- | --- |
| `variant` ∈ `user` \| `ai` (measured 47 / 53) | the role discriminator |
| **`content` is empty for 51 of 53** `ai` messages | the answer is in `blocks[]`; reading `content` yields an empty reply on almost every turn |
| `id` embeds epoch ms (`user-1789969951304`, `ai-1789969952142-<hash>`) | the only ordering key; match reply-by-epoch |
| `timestamp` is `"05:52 AM"` — display only, no date | **never** order or match on it |
| `mode-divider` messages exist (`divider-…`) | "the last `ai` message" can be a divider with no content — a named edge case |
| `tool-call` blocks carry `toolName`/`input`/`output`/`toolCallId`/`agentId` | **Freebuff's own tool calls are auditable from disk** — this is the evidence `§C4` verifies, and what makes "did it actually act" measurable rather than inferred |
| text blocks carry `textType` (`reasoning`) | thinking is separable from the answer by structure, not by heuristic |

### 18.8 Error taxonomy

| Code | Meaning | `retryable` | Falls through to Groq? |
| --- | --- | --- | --- |
| `E_PROTOCOL` | malformed frame / version | no | no |
| `E_BAD_OP` | unknown op | no | no |
| `E_LOCK_FOREIGN` | a live instance owns the session | yes (after wait) | **yes** — with a distinct reason |
| `E_LOCK_MALFORMED` | lock file unreadable/truncated → treat as foreign | after wait | yes |
| `E_PROJECT_AMBIGUOUS` | basename collision | no | yes |
| `E_NO_SESSION` | nothing attached | yes | yes |
| `E_SESSION_DEAD` | pid gone / session over | yes | yes |
| `E_PTY` | write/read on the pty failed | yes | yes |
| `E_READY_TIMEOUT` | no ready frame in budget | yes | yes |
| `E_TIMEOUT_STALLED` | no progress in budget | yes | yes |
| `E_UNREADABLE` | reply could not be assembled | yes | yes |
| `E_QUEUE_FULL` | bridge backlog | yes | **no** |
| `E_REFUSED` | **verification refused** (a verdict) | no | **no** |
| `E_INTERNAL` | bridge bug | yes | no |

**The polarity rules, carried over deliberately:** a *refusal* (`E_REFUSED`) is a
**verdict, not an outage** — it must never fall through to another brain, or
verification becomes decoration. And a refusal is **not** a transport error, the
same rule `builderbro_mcp.py` already enforces between `verifier.ok` and JSON-RPC
`isError`.

### 18.9 Heartbeat, liveness and the ambiguity that started this

`evt{op:"heartbeat"}` every 5 s *(proposed)* with
`{state, turn_id, queue_depth, watermark, last_progress_ms}`. This exists because of
the measured failure that produced `live_mcp_probe.py`: **a silent pty is
ambiguous** — "not answering yet", "blocked on a terminal query", "crashed" and
"found no MCP server" all look identical from outside. A heartbeat turns that into
a decidable state, and its absence is the only signal the client needs to declare
`E_SESSION_DEAD`.

### 18.10 Capture hygiene (a tested regression, not a note)

A pty echoes input, and a prompt necessarily names what it asks for. The probe's
first live run reported `loaded` on **its own echoed prompt**. Therefore:

- every frame the bridge forwards from the pane carries `echo: true|false`;
- echo frames are **excluded** from reply assembly entirely;
- the probe's false-positive transcript is kept as a **fixture** and asserted to
  classify as `unreadable`, so the defect cannot return.

### 18.11 Worked transcript

Happy path, then the two refusals that matter (foreign lock, and a verification
bounce). Values match the measured record contract in `§18.7`.

```jsonc
→ {"v":1,"id":"c1","type":"req","op":"hello","client":"bro/1","proto_min":1,"proto_max":1,"pid":16994}
← {"v":1,"id":"c1","type":"res","ok":true,"server":"bro_bridge/1","platform":"posix",
   "capabilities":["ask","capture","interrupt"],"instance":{"lock":"absent"}}

→ {"v":1,"id":"c2","type":"req","op":"attach","cwd":"/mnt/sdcard/Download/builderbro","resume":true}
← {"v":1,"id":"c2","type":"res","ok":true,"state":"ATTACHED",
   "session_id":"2026-09-10T10-18-02.028Z","project":"builderbro",
   "chat_dir":"…/projects/builderbro/chats/2026-09-10T10-18-02.028Z",
   "owner":{"pid":20683,"instanceId":"40054406-…","ours":true},"boot_seconds":12.3}

→ {"v":1,"id":"c3","type":"req","op":"ask","turn_id":"t-1","priority":"cli",
   "task":"Read .agents/mcp.json and report its server names.",
   "expect":{"tool":"read_file","arg":".agents/mcp.json","check":"contains:builderbro"}}
← {"v":1,"type":"evt","op":"queued","position":0,"turn_id":"t-1"}
← {"v":1,"type":"evt","op":"turn_progress","turn_id":"t-1",
   "watermark":{"messageCount":101,"messagesSize":32801980},"last_progress_ms":340}
← {"v":1,"id":"c3","type":"res","ok":true,"turn_id":"t-1",
   "reply":{"text":"The registry declares one server: builderbro.",
            "blocks":[{"type":"text","textType":"reasoning","content":"…"}],
            "tools":[{"toolName":"read_file","input":{"path":".agents/mcp.json"},"toolCallId":"…"}]},
   "evidence":{"tool_observed":true,"verified":"observed"},
   "timings":{"posted_ms":120,"assemble_ms":880},"state":"ATTACHED"}
```

**Refusal A — the lock is held by somebody else (no spawn, stated fact):**

```jsonc
→ {"v":1,"id":"d1","type":"req","op":"attach","cwd":"/mnt/sdcard/Download/builderbro","resume":true}
← {"v":1,"id":"d1","type":"err","ok":false,"code":"E_LOCK_FOREIGN","retryable":true,
   "fallback_eligible":true,
   "message":"a Freebuff instance already owns this machine",
   "detail":{"owner":{"pid":20683,"instanceId":"40054406-7a60-4eb1-8ae6-6c403ddce569"},
             "pid_alive":true,"read_only_attach":true}}
```

The router reports *that* (`/brain` shows the owning pid and the reason) and may
hop to Groq — it never spawns a second instance.

**Refusal B — verification refuses a turn it could not confirm:**

```jsonc
← {"v":1,"id":"d2","type":"err","ok":false,"code":"E_REFUSED","retryable":false,
   "fallback_eligible":false,"attempt":1,"max_attempts":2,
   "message":"claim not confirmed over the available evidence",
   "detail":{"verdict":"cannot_verify","checked":["tool_observed"],
             "bounce_note":"No read_file tool-call block appears in the record for this turn."}}
```

`bounce_note` is posted back into the session as a repair instruction and the turn
is retried, up to `BRO_VERIFY_RETRIES`. `fallback_eligible: false` is the whole
point: **you do not escape a refusal by asking a different brain.**

### 18.12 Versioning

One integer `v`. `hello` carries `proto_min`/`proto_max`; the effective version is
the highest both support, and a server that cannot serve any overlap replies
`E_PROTOCOL` (it does not half-work). Unknown `op` → `E_BAD_OP`; unknown fields are
ignored; a missing required field → `E_PROTOCOL`. Every `res` may carry
`"warnings":[…]` for non-fatal degradation (a `resync`, a truncated capture, an
`encoding_repaired` transcript) — visible, never silent.

---

## 19. Appendix II — Lock-discipline state machine (normative)

### 19.1 The resource and the measured lock

One Freebuff instance per machine. The record is global, not per-project:

```json
~/.config/manicode/freebuff-instance-owner.json
{"instanceId": "40054406-7a60-4eb1-8ae6-6c403ddce569", "pid": 20683}
```

Measured `2026-09-21`: that `pid` is **alive** (`/root/.config/manicode/freebuff`,
state `t<l+`, elapsed `2:56:16`) and it is the instance hosting the operator's own
session. Two consequences the implementation must respect: the lock is **real**, and
**the operator's session is the normal case**, not an edge case — bro must be able
to attach to / read from a session it did not start, or it will be useless to its
own user.

### 19.2 Two axes, because liveness is not one thing (measured)

The client's own `log.jsonl` shows, for pid `20683`, while the process stays alive:

```
[chat-runtime] Freebuff session over; holding queued messages until rejoin
Reconnection detected, firing onReconnect callback
Loaded chat state from chat directory … "chatId":"2026-09-10T10-18-02.028Z"
```

So **process liveness ≠ session connectivity**. The state machine therefore tracks:

| Axis | Values | Source of truth |
| --- | --- | --- |
| `PROC` | `pid_alive` · `pid_dead` · `pid_reused` | `/proc/<pid>` (cmdline contains the client path) + `/proc/<pid>/stat` start-time compared with what we recorded at acquire |
| `SESS` | `connected` · `reconnecting` · `over` | the client's `log.jsonl` tail (`Freebuff session over`, `Reconnection detected`) + heartbeat progress |

`READY ⇔ PROC == pid_alive ∧ SESS == connected`. A single axis would have called a
*reconnecting* session healthy and then timed out on every `ask` — a defect this
spec's own probe already hit in the field.

### 19.3 States

```
                acquire                     ready frame
   ABSENT ───────────────▶ STARTING ─────────────────────────▶ ATTACHED
     ▲                        │                                  │  ▲
     │                     ready_timeout                    session_over │ reconnect
     │                        ▼                                  ▼  │
     └──── stop/exit ──── DEGRADED ◀── unreadable ──────── RECONNECTING
                                 │
   FOREIGN (terminal for auto-acquire; offers read-only attach)
```

| State | Meaning | May serve `ask`? |
| --- | --- | --- |
| `ABSENT` | no lock file, or it is stale and reclaimed | no |
| `FOREIGN` | a live instance we do not own holds the lock | only via explicit read-only adopt (`§13` Q5) |
| `STARTING` | we spawned, waiting for a ready frame | no (queued) |
| `ATTACHED` | ours, ready | **yes** |
| `RECONNECTING` | ours, process alive, session over/reconnecting | no (queued) |
| `DEGRADED` | ours, but the record could not be read for a turn | no; `capture` still available |
| `RELEASING` | `stop`/`shutdown` in progress | no |

### 19.4 Ownership arithmetic (the decision procedure, in order)

1. **Read** the lock file. Absent → `ABSENT`.
2. **Present, and `pid` equals the pid we recorded as ours** (and instanceId
   matches) → ours → `ATTACHED` / resume. No spawn.
3. **Present, foreign `pid`, `kill(pid, 0)` → `ESRCH`** → *stale* → reclaim: write
   the exact schema (`instanceId`, `pid` — no extra keys) for our child and emit
   the transition event.
4. **Present, foreign `pid`, alive** → `FOREIGN`. Refuse. Report the owning pid and
   instanceId. If `adopt` was requested and allowed, attach **read-only** to that
   project's active conversation; full control only after that instance exits.
5. **Present, `pid` alive but `/proc/<pid>/cmdline` is not the client** → *pid
   reused* → stale **with the reason recorded** (`pid_reused`), never silently.
6. **Present but unreadable** (truncated JSON, missing `pid`, wrong type) →
   `E_LOCK_MALFORMED`, treated as `FOREIGN`. **Fail-safe direction: refuse to
   spawn.** The cost of refusing is a fallback hop; the cost of being wrong is two
   instances fighting over one machine.
7. **Stale reclaim is serialized** by `fcntl.flock` on our own file
   (`~/.bro/freebuff-session.lock`) around read-check-write, so two `bro`
   processes cannot both decide to reclaim. The client's lock is never locked by us
   — only respected; the flock exists to serialize *our* decision.

### 19.5 Transition table

| From | Input | Guard | Action | To | Recorded event |
| --- | --- | --- | --- | --- | --- |
| `ABSENT` | `acquire` | slot free | spawn child, pin pid+start-time | `STARTING` | `spawn{pid}` |
| `ABSENT` | `acquire` | stale lock | reclaim (flock), spawn | `STARTING` | `reclaim{old_pid,reason}` |
| `ABSENT` | `acquire` | foreign live | none — **no spawn** | `FOREIGN` | `refuse{owner_pid}` |
| `ABSENT` | `acquire` | malformed lock | none | `FOREIGN` | `refuse{code:E_LOCK_MALFORMED}` |
| `STARTING` | `ready_frame` | lock says ours | mark ready, take watermark | `ATTACHED` | `ready{seconds}` |
| `STARTING` | `ready_timeout` | — | kill **our** child, release | `DEGRADED` | `timeout{timeout_s}` |
| `ATTACHED` | `ask` | ready | post prompt, watch watermark | `ATTACHED` (busy) | `turn{id}` |
| `ATTACHED` | `turn_complete` | — | assemble reply, verify | `ATTACHED` | `turn_done{ms,verdict}` |
| `ATTACHED` | `unreadable` | record unreadable | keep raw capture | `DEGRADED` | `unreadable{watermark}` |
| `ATTACHED` | `session_over` | log line / heartbeat gap | — | `RECONNECTING` | `session_over{pid}` |
| `RECONNECTING` | `reconnect` | heartbeat resumes | resync watermark | `ATTACHED` | `reconnected{ms}` |
| `RECONNECTING` | `pid_dead` | — | clear our pid; lock now stale | `ABSENT` | `child_died{}` |
| `DEGRADED` | `resync` | record readable again | full re-read, new watermark | `ATTACHED` | `resync{bytes}` |
| any ours | `stop`/`shutdown` | — | graceful stop, then release | `RELEASING` → `ABSENT` | `stop{graceful}` |
| `FOREIGN` | `owner_exits` | poll | reclaim on next `acquire` | `ABSENT` | `foreign_cleared{}` |

### 19.6 Failures and recovery

| Scenario | Required behaviour |
| --- | --- |
| Our bridge crashes while the client lives | the client still holds the lock with an unsupervised pid → next start is `FOREIGN`; bro reports the pid. **It must never kill a pid it does not own** (`I2`) |
| Client crashes while we hold our record | next start: pid dead → reclaim (rule 3) |
| Auto-update replaces the binary | core is **0.0.180** while the npm wrapper is **0.0.154** (measured) — identity checks must not assume a stable path, version or mtime |
| Two `bro` processes race | `flock` serializes reclaim; exactly one spawns |
| Operator's own session holds the lock | the measured normal case: refuse-to-spawn by default, read-only adopt when permitted (`§13` Q5) — this is the difference between a tool and a nuisance |
| Lock file deleted mid-session | treat as `FOREIGN`/unknown → do **not** spawn a second instance while our child is alive; report |

### 19.7 Invariants (each asserted by a test)

- **I1** — At most one live session is ever spawned by bro, machine-wide.
- **I2** — bro never signals a pid it does not own (asserted by recording every
  signal call and requiring an empty list on the foreign-lock path).
- **I3** — No transition to `ATTACHED` without a ready frame **and** a lock verdict
  of ours.
- **I4** — Every refusal names the owning pid and instanceId.
- **I5** — No transition without a recorded event line.
- **I6** — Fail-safe direction is **refuse to spawn**: an unreadable lock is never
  interpreted as ours.

### 19.8 Testable properties (no real client required)

Driven by a fake lock file, a fake `/proc`-like probe and a **spy spawner**, so every
line below asserts *behaviour*, not a file's contents:

1. absent → `acquire` → `STARTING` → ready → `ATTACHED`;
2. own pid → idempotent, **spawner never called**;
3. foreign live → `FOREIGN`, **spawner never called**, zero signals sent;
4. stale pid (`ESRCH`) → reclaim writes **exactly** `{instanceId,pid}`;
5. pid alive + not the client binary → stale with reason `pid_reused`;
6. two brokers concurrently → exactly one spawns;
7. child dies while `ATTACHED` → `DEGRADED` → reclaim on restart;
8. client alive, bridge dies → `FOREIGN` on next start, `kill` **never** called;
9. `session over` in the log → `RECONNECTING`, then `ATTACHED` on reconnect;
10. malformed lock (truncated / missing `pid` / wrong type) → `E_LOCK_MALFORMED`,
    treated as foreign, **no spawn**;
11. every transition emits exactly one event line, and the timeline replays.

Negative controls (must fail on purpose): foreign live treated as stale; an
unreadable lock treated as ours; a second spawn while our child is alive; a signal
sent to a foreign pid.

### 19.9 What M0 must measure before `§19` is frozen

This machine's answers, not assumptions:

1. Does the client **delete** the lock on clean exit, or leave a stale file?
2. Is `instanceId` regenerated per launch, or stable across launches?
3. What does a **second launch** actually do — exit with a code, print a warning,
   or park in a loading animation (the current hypothesis)?
4. Does a parked second instance write anything anywhere (a log line we can detect)?
5. Is `pid` the **core** pid or a wrapper/child pid? (Today: `20683` is the core.)
6. Does the project key follow `--cwd` or the *shell's* cwd? (`recent-projects.json`
   holds both `/mnt/sdcard/…` and `/sdcard/…` → **basename collision is real**.)
7. How long does a ready frame take on a cold cache, and does a resumed session
   (`--continue`) get there faster?

Until those are answered, `§19.4`'s rules are the *proposed* decision procedure and
M0's numbers may amend them — the same rule `§14` already states: no design decision
may assume an unmeasured channel.

---

## 20. Appendix III — M0's amendments (measured 2026-09-23)

M0 has been run. The full measurement, with its numbers and its four honest
inconclusives, is **`M0-SESSION-MEASUREMENT.md`**; the instrument is
`bro_session_measure.py` (89 tests). This appendix records only what changes in
*this* document, so that `§18.7` and `§19.2` are not read as still being true.

Measured against core **`0.0.186`** (this spec was measured against `0.0.180`).

### 20.1 `§18.7` — the tool block is `tool`

`§18.7` records tool-call blocks as `{"type": "tool-call", …}`. On `0.0.186` the
type is **`tool`**; measured 142 blocks, all `tool`, zero `tool-call`. A reader
written from `§18.7` finds no tool calls, reports `cannot_verify` on every turn,
and looks like a careful verifier while doing it. The same block's `output` is a
**string**, not the object `§18.7` shows. Both spellings are now accepted by the
instrument, which reports which one fired.

### 20.2 `§18.7` — the watermark belongs at the last *settled* message

`§18.7`'s watermark is taken at the end of what has been read. **It does not
work.** The client rewrites the message currently being written as its blocks and
metadata grow, so the bytes just before the closing bracket move on every append,
and the consumed prefix is invalidated by the very growth it watches for.
Measured: 240 s / 1,081 polls → **4 resyncs, 0 usable deltas**; byte resolution
showed the divergence at offset 1,959,925 while the last *finished* message ended
at 1,450,253.

**Amended rules:**

1. The watermark is taken at the end of the last **settled** message, never at
   the end of the file. The in-flight message is re-read on every poll until it
   settles — it is the turn being watched and is not yet an answer.
2. **Settled** means `variant == "user"`, or a `mode-divider`, or
   `isComplete is true`. A user message carries no flag and never will; treating
   "no flag" as "in flight" leaves most of the record unconsumed forever.
3. The boundary is computed over **bytes**, not over a decoded string: a string
   index stops equalling a byte offset as soon as any character is not ASCII, and
   the live record contains multi-byte characters.
4. The boundary is **advanced** over the new suffix rather than recomputed, since
   the prefix hash has already proven those bytes unchanged. Measured cost: 8.496 s
   → 0.903 s.

### 20.3 `§18.6` — completion is a flag, and the reply is the *first* candidate

`isComplete` exists (present and true on finished assistant messages, absent on
the one being written). The quiet period stays as a declared fallback only, since
it cannot be evaluated from a snapshot.

And the reply to a prompt is the **first** qualifying assistant message at or
after it, not the newest: taking the newest makes `complete` unreachable on a live
box, because the newest qualifying message is always the turn currently being
written. Measured: **12 of 12** real prompts returned `streaming` until this was
fixed.

### 20.4 `§19.2` — the session axis is read from `msg`, never by substring

`§19.2` names `log.jsonl`'s tail as the source of truth. That file records the
**payloads of the session's own actions** — every file written, every command
run — so the lifecycle phrases appear inside unrelated content: measured 27 raw
hits, **18 of them inside a payload**, 9 real events. A substring scan reported
`over` on a session that was working, which would refuse every turn on a live
brain.

**Amended rule:** parse each line and read the event's own `msg`; count payload
mentions separately and report them. Plus: a marker is a statement about the
*past* — if the conversation record was written after the last `over` marker, the
session is `connected` and the marker is stale.

### 20.5 `§19.9` — what M0 answered, and what it did not

| # | Answer |
| --- | --- |
| 1 | **unmeasured** — no clean exit was observed, so nothing to observe |
| 2 | **unmeasured** — one `instanceId` observed |
| 3 | **parks.** Alive at 60 s, painting a TUI, no exit code — and therefore *silent*, which is worse than an error |
| 4 | No lock file, no download, no disk trace; detectable only by its rendered stdout. The real lock was byte-unchanged |
| 5 | The **core**, not a wrapper |
| 6 | Partly: the collision is real, and the record self-identifies `projectRoot` — a better key than the basename, and what M1 should use |
| 7 | **unmeasured here**; ≈12.3 s to first frame from `live_mcp_probe.py` is the starting number |

M1 may proceed on the channels M0 measured. It may **not** assume a boot time, a
torn-read rate, `--continue` behaviour, or that an exit releases the lock.
