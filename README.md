# builderBRO

An AI coding agent plus the machinery that makes its claims checkable: the model
plans and acts, and a separate layer checks what it says happened before any of it
counts as fact.

The core rule of this repository is that **a capability is `verified` only if it has
a reproduction command that has been run and a number that came out of it**.
Anything else is `unmeasured` or `planned` and is marked as such, everywhere,
including about the project's own claims. `CAPABILITY_INVENTORY.md` is the record
of what exists now; `AUTONOMY-UPGRADE.md` is the plan for what does not.

## Two halves, easy to mistake for one system

| half | what it is | is it the autonomy layer? |
| --- | --- | --- |
| **BRO CLI** — `loader.mjs` → `cli.mjs` (the agent runtime), `bromance.mjs`, `bro-web.mjs`, `bro-build.mjs`, `bro-continuity.mjs`, `agent-upgrade.mjs`, `model-selector.mjs` | an interactive terminal assistant with file, web and build tools and a local → Vertex → Base44 → Groq → Cerebras → OpenAI model seam | **no** — presentation, tools and model selection for a human at a keyboard |
| **Free Brain** — `agent_runtime.py`, `autonomy.py`, `loop_guard.py`, `verifier.py`, `memory.py`, `brain_cascade.py`, `rag_*.py`, `drift_loop.py`, `qih_metrics.py` | the autonomy layer proper: a goal-directed loop whose every step is verified against real tool output before it is promoted to evidence | **yes** |

The split is a measured decision rather than a preference. The unseeded live run
found which half was failing: the hosted hop **answered from priors instead of
acting** — asserting `0.75` where the file said `0.25`, then writing out the JSON
snippet it believed the file contained — and in most attempts never called a tool
at all. So the model's own `FINAL:` claim stopped being a result: `autonomy.py`
accepts completion only when the declared goal condition holds over verified
evidence, `verifier.py` confirms each step adversarially, and `memory.py` refuses
to store a fact that names no independent check.

The same reasoning produced the **MCP surface** below: an agent that already acts
(a trusted Freebuff session) does the planning and the work, and BuilderBro does the
verification.

## Quick start

Requirements: **Python 3.11+** (3.14 here) and **Node 20+** (26 here, for
`AbortSignal.any`). The Python core is stdlib-only — no third-party imports — and
Node needs `npm install` only for Playwright.

```bash
npm install

# BRO CLI
node loader.mjs                                  # interactive (usually aliased to `bro`)
echo 'print(1)' | node loader.mjs "explain this" # pipe mode, no REPL
npm start                                        # = node cli.mjs

# Free Brain, offline-first
python3 agent_runtime.py --check                 # local server + model health
python3 agent_runtime.py --providers             # the cascade as actually configured
python3 agent_runtime.py --goal "make tests pass" # verified goal loop
python3 agent_runtime.py --goal "..." --reflex    # the unverified baseline, for comparison

# tests
python3 -m unittest discover -p '*_test.py'       # 776 tests
node --test tests/*.test.mjs tests/*.test.cjs     # 60 tests
```

Run the Python suites as a whole rather than by enumeration — the per-module list
in `CAPABILITY_INVENTORY.md` went stale three times, and the header there says so.

> **`autonomy.py` takes its goal positionally and has no `--help`.** It reads
> `sys.argv[1:]` as the goal text, so `python3 autonomy.py --help` runs a goal
> literally named `--help` and spends provider requests. Use
> `python3 autonomy.py "your goal"`.

## Configuration

Copy `.env.example` to `.env`. The local open-weight server is **always tried
first**, so the system runs fully offline when `LOCAL_MODEL_URL` is set:

```dotenv
LOCAL_MODEL_URL=http://127.0.0.1:11434/v1   # Ollama, vLLM, llama.cpp
LOCAL_MODEL=qwen2.5-coder:7b
```

Hosted keys are added to `brain_cascade.py`'s chain only when present, each one a
hop: `GROQ_API_KEY` (plus numbered siblings `_2`…`_9`, or a csv `GROQ_API_KEYS`),
`CEREBRAS_API_KEY`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`, `MISTRAL_API_KEY`,
`GITHUB_TOKEN`. A rate-limited or blocked key benches itself, not the chain —
`.env.example` documents the measured failure classes (a Cloudflare `1010` on a
bare `urllib` User-Agent, a Cerebras `402` on the free trial) so each costs one
failed attempt instead of looking like a bad key.

## The MCP surface

`.agents/mcp.json` registers two stdio servers. Both `args` are **absolute**, and
that is load-bearing: Freebuff's loader joins `mcp.json` onto `<cwd>/.agents`,
`<cwd>/.agents/..` and `~/.agents`, and skips any path that does not exist, while
the spawn *inherits* the client's cwd. The entry schema is a `strictObject`
(`type` / `command` / `args` / `env`), so `cwd` cannot be set and one stray key
silently unregisters the whole file. Loaded tools are namespaced
`server__tool` — `builderbro__verify_claim`.

| server | tools |
| --- | --- |
| `builderbro` — `builderbro_mcp.py` | `verify_claim`, `memory_recall`, `memory_record`, `rag_ask`, `evidence_audit` |
| `bro-bridge` — `bro_bridge_mcp.py` | `bridge_status`, `bridge_attach`, `bridge_observe`, `bridge_ask`, `bridge_capture`, `bridge_stop`, `bridge_shutdown` |

Neither server re-implements policy: `verify_claim` calls `verifier.confirm`,
`memory_record` calls `memory.promote`'s own rule, `rag_ask` shells out to the
`builderbro-rag` skill so there is exactly one implementation, and `evidence_audit`
calls `evidence_hygiene.classify`. A **refusal is a working tool**, so
`E_LOCK_FOREIGN` and `E_REFUSED` stay `isError: false` while `E_NO_SESSION` and an
unreadable record stay real errors — a caller must not retry a check that has
already ruled, and must not read a transport failure as an answer.

Both also run from the shell, which is how they are tested without a client:

```bash
python3 builderbro_mcp.py --tools
python3 builderbro_mcp.py --call verify_claim --json \
  '{"tool":"read_file","arg":"memory.py","output":"<file>","expect":"contains:class MemoryStore"}'

python3 bro_bridge_mcp.py --tools
python3 bro_bridge_mcp.py --call bridge_status --json '{}' --human
```

## Repository layout

| path | what it is |
| --- | --- |
| `*.py` (root) | the Free Brain core and its 17 test modules — the autonomy layer and its instruments |
| `loader.mjs`, `cli.mjs`, `bromance.mjs`, `bro-*.mjs`, `model-selector.mjs`, `tools.js` | the BRO CLI |
| `.agents/mcp.json`, `.agents/skills/builderbro-rag/` | the MCP registry and the RAG skill the `rag_ask` tool calls |
| `freebrain-residence/` | the agent's **home**: `instructions.md` (the mutable self), `state.json` (checkpoint), `ledger.jsonl`, `evidence/`, `generated/`. The residence *is* the machine — see its own README |
| `qih-residence/`, `qih_metrics.py` | the QIH metric ledger: four formulas as stdlib ports, cross-referenced to `NewState/qih_consciousness/` |
| `deploy/` | four ways to run the loop headless: `oracle/` (always-on box + systemd), `phone/watchdog.sh`, `gitlab/`, `actions/` |
| `src/`, `tsconfig.json` | a separate TypeScript `passionstate` MCP server (passes, chambers, immutable archive). Source is tracked; `dist/` holds its build output and is gitignored. `package.json` declares no build script |
| `tests/` | the Node test suites (`node:test`) |
| `bro-freebuff-brain-spec.md` | the `bro` ↔ Freebuff brain spec (§18 wire protocol, §19 lock, §20 M0's amendments) |
| `NewState/`, `repo-fixes/`, `dycrag_system/`, `passionstate-nexus/`, `presence/`, `money/`, `love/`, `Laland_*`, `Leland_*`, `m-m-mjustqwa1/` | adjacent and generated projects, some tracked as gitlinks and some ignored. Not part of the autonomy stack |

## Documents

Read these in this order; each states its own confidence and names its evidence.

| document | what it answers |
| --- | --- |
| `CAPABILITY_INVENTORY.md` | **what the system can do now**, with the reproduction command and the measured number for each capability |
| `AUTONOMY-UPGRADE.md` | what *autonomy* requires that it still cannot do, and the dependency-ordered plan — A1–A3 delivered, the rest `planned` |
| `FREE-BRAIN.md` | the research brief's full decomposition: open-weight model serving, the file-driven residence, the Q4 drift loop, deployment |
| `AGENT-INTEGRITY.md` | the threat model for conversational continuity corruption and the hard circuit breakers designed against it |
| `QIH.md` | the QIH metrics, with the metaphysics labelled as hypothesis rather than capability |
| `SELF_IMPROVEMENT_LOG.md` | every closed DETECT → RESEARCH → DESIGN → IMPLEMENT → TEST → REGISTER cycle, with its evidence blob and its honest inconclusives |
| `REPO-AUDIT-2026-09-23.md` | the full-tree audit: what was broken, what was fixed, and what was reported and left alone |
| `M0-SESSION-MEASUREMENT.md` | what a live Freebuff session actually does, measured — including the three spec rules the measurement falsified |
| `bro-freebuff-brain-spec.md` | the normative spec for bridging `bro` to a live Freebuff session |
| `deploy/actions/GITHUB-ACTIONS.md`, `deploy/gitlab/GITLAB.md`, `deploy/oracle/ORACLE.md` | runbooks for each headless path |

## Deployment

The loop is designed for a host that dies: `state.json` is the checkpoint,
`ledger.jsonl` is the record and `instructions.md` is the mutable self, so an
OOM-killed phone or a 6-hour CI job costs at most one cycle. Every path stops the
loop **itself** on a cycle boundary (`--max-seconds`) so the persist step always
gets to run.

| path | runs on | note |
| --- | --- | --- |
| `deploy/oracle/` | an always-on box | systemd units for the loop and an rclone sync of the residence |
| `deploy/phone/watchdog.sh` | the phone this was built on | restarts the loop, counts trips and alarms, refuses to fork the record |
| `.gitlab-ci.yml` | GitLab shared runners | card-free; the whole study fits one month's free minutes |
| `.github/workflows/drift-runner.yml` | GitHub Actions | `schedule` is disabled for private repos on free accounts, so trigger it via `workflow_dispatch` |

```bash
python3 drift_loop.py --cycles 5                    # a local smoke run
python3 drift_loop.py --max-seconds 19200 --max-trips 400 --max-alarms 200
python3 loop_audit.py                               # A/B the detection pathologies
python3 autonomy_suite.py                           # 20 scored tasks, 8 pre-registered floors
```

## Status, honestly

- **776 Python tests and 60 Node tests, all green** — measured 2026-09-24 with the
  two commands in *Quick start*. Counts are re-measured, never carried forward.
- **The `bro` ↔ Freebuff bridge (M1) is partial.** All seven `bro-bridge` tools were
  driven live against a real session — read-only attach under a foreign lock,
  `E_LOCK_FOREIGN` on a post attempt, correct `isError` polarity on every call —
  but M1's own exit criterion, one verified round trip in a *fresh* trusted
  session, is **unmeasured**: the isolated client paints its TUI and stalls at
  `Connecting…` until `E_READY_TIMEOUT`. `live-fresh-mcp-status.json` carries the
  verdict and the launch command rather than claiming success.
- **Nothing here makes self-building possible.** The MCP tools are read-only plus a
  memory store: no execution, no file writes, no sandbox.
- **Four files are known not to parse and are deliberately left alone** —
  `cli1.mjs`, `cli12.mjs` (10 leaked `SEARCH`/`REPLACE` patch markers), the
  gitignored `dist/server.js` and files under `repo-fixes/`. Repairing them would
  mean inventing intent; their exact sites are named in `REPO-AUDIT-2026-09-23.md`.
- **GitHub reports 11 open Dependabot advisories on the default branch** (4 high,
  7 moderate), and all of them are in one manifest —
  `passionstate-nexus/package-lock.json` (`fast-uri` ×4 high, `hono` ×3, `qs` ×2,
  `vitest` + `@vitest/mocker` ×2). The count is read from the API, not from the push
  banner: the banner said 12 minutes earlier and the advisory set is not stable
  between pushes, so a number quoted from it is stale before it is written down.
- **Hosted providers are optional and rate-limited.** The local-first path is the
  tested one; anything cloud needs your own keys, and free tiers are hard caps.
