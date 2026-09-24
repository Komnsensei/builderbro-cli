# Repository audit — 2026-09-23

Full-tree audit of `builderbro`, run against the working tree at
`/mnt/sdcard/Download/builderbro`. Every claim below was produced by a command
that could have failed; the command is named beside it.

**Baseline → result**

| | before | after |
|---|---|---|
| test suite (`python3 -m unittest discover -p '*_test.py'`) | 661 green | **670 green** |
| Python sources that could not be parsed | **4** | 0 |
| Tracked files that fail `node --check` | 2 (`tools.js`, `dycrag_system/…` n/a) — plus 2 in the nested NewState repo | 0 in the tracked trees |
| Bridge tools reachable from a cwd that is not the repository | 4 of 5 (`evidence_audit` → `missing_log`) | **5 of 5** (`evidence_audit` → 1,780 rows) |
| Published records | — | byte-identical across the whole run |

Records, unchanged through every step:
`ledger.jsonl` `2b5c5136…`, `evidence/q1-evidence.jsonl` `8230b0d2…`,
`loop_guard.json` `76a896a9…`.

---

## 1. Sources that could not be parsed at all — the headline finding

Four Python files carried a syntax error **and nothing noticed**, because nothing
imported them: the suite stayed green while the repository held code no
interpreter could run. Found with a full-tree `py_compile` walk (82 `.py` files),
now guarded by `source_integrity_test.py`.

One signature in every case: a **raw newline where a `\n` escape belonged, inside
a string literal** — e.g. `print("` *newline* `--- BRO Persona Builder Wizard ---")`.

| file | sites | status |
|---|---|---|
| `NewState/persona_wizard.py` | 8 | **fixed** — compiles; wizard driven end-to-end under piped input |
| `dycrag_system/main.py` | 8 | **fixed** (earlier in session), then corrected at the root — see §2 |
| `dycrag_system/agent.py` | 5 | **fixed**, then regenerated from the corrected template |
| `dycrag_system/knowledge_base.py` | 1 | **fixed** — was *generated* broken; regenerated clean |
| `dycrag_system/agent.agent.py` | whole file | **deleted** — a 34-byte tracked fragment (`return "general concepts"`) that was never its own module |
| `tools.js` (tracked) | 1 | **fixed** — `JSON.stringify(…) + '\n'` |

## 2. Root cause: the generator wrote broken code *by design*

`dycrag_system/main.py` is a self-correction shim: at import time it writes
`agent.py` and `knowledge_base.py` from two embedded string templates. **A template
holds a real newline, so a `\n` inside one decodes to a real newline in the
generated file** — which is exactly the corruption signature above. The generator
was therefore the source, not a victim, and it rewrote both dependencies broken on
every run.

Verified before the fix by compiling the templates' *values*, not their text:
`knowledge_base.py: written form is BROKEN -> line 36: unterminated f-string
literal`; `agent.py: … line 27: unterminated string literal`.

**Fix** — doubled the escape at all 11 sites inside the two templates, then
regenerated the modules and ran the real demo:

```
knowledge_base.py: written form COMPILES
agent.py: written form COMPILES
… DyCRAG Demonstration Finished.
```

`source_integrity_test.py` now compiles each template's *output* and asserts the
committed modules equal the templates (a hand edit either file would be silently
discarded by the next run).

## 3. More of the same signature, in JavaScript

| file | defect | status |
|---|---|---|
| `NewState/mcp-server/index.mjs` (tracked in NewState) | 21 backticks — odd, so the stray `` ` `` at EOF opened a template literal that never closed | **fixed** — `node --check` clean |
| `NewState/mcp-server/write_index_mjs.js` (tracked) | 13 backticks — same stray terminator | **fixed** — `node --check` clean |

Verified on `/tmp` copies before touching the originals: stripping the trailing
backtick makes each file parse.

## 4. The bro bridge vs Freebuff — two real defects

### 4a. The spawn path depended on where the client was started
The loader discovers `mcp.json` by walking **up** (`<cwd>/.agents`,
`<cwd>/../.agents`, `~/.agents`) but spawns the server with an **inherited** cwd,
and `cwd` is not a key its `strictObject` schema allows. The registry used
`"args": ["builderbro_mcp.py"]` — so a session started in a subdirectory of the
repo *found* the registry and then failed to start the server.

**Fix** — pin the argument:
`.agents/mcp.json` → `"args": ["/mnt/sdcard/Download/builderbro/builderbro_mcp.py"]`.

Measured both forms from `<repo>/.agents` as cwd (`RegistryTest`):
* relative → non-zero exit, `No such file or directory`
* pinned → `initialize` handshake, `serverInfo.name = builderbro`

> Discovery is still rooted at the client's cwd, so launch Freebuff inside this
> repository — or register the same entry at `~/.agents/mcp.json` to get the tools
> in every project (outside this tree; not done without your say-so).

### 4b. `evidence_audit` and the memory store resolved against the client's cwd
`_residence(env)` returned `"freebrain-residence"`, a **relative** path. From a cwd
that is not the repository:

```
evidence_audit → {"error": "missing_log", "isError": true, "reason": "no evidence log at freebrain-residence/evidence/q1-evidence.jsonl"}
```

**Fix** — anchor to `builderbro_mcp.py`'s own directory; an explicit
`DRIVE_RESIDENCE` still wins (that is how the suites isolate themselves).

### Live end-to-end session, launched exactly as the client launches it
Registry read from `.agents/mcp.json`, cwd = a temp dir that is **not** the repo,
inherited env minus `DRIVE_RESIDENCE`:

| call | result |
|---|---|
| `initialize` | `serverInfo.name = builderbro` |
| `verify_claim` (`contains:class MemoryStore`) | `level=observed ok=True` |
| `verify_claim` (needle absent) | `level=refused ok=False` (a decision, not `isError`) |
| `verify_claim` (no expectation) | `level=refused ok=False` |
| `memory_record` `invariant`, no named check | `isError=True error=provenance`, **nothing written** |
| `memory_record` `invariant` + `how_verified` | accepted, 503 bytes in the store |
| `memory_recall` | `facts=1 unverified=0` |
| `evidence_audit` | **rows=1780, artifacts=0** |
| `rag_ask` | `citations=[1,2,3]`, 637 chars |
| unknown tool | `isError=True` |

`rc=0`, empty stderr, every response ≤ 6.2 KB (the client's read buffer is 10 MB,
newline framing, no `Content-Length`).

## 5. Findings reported, deliberately not "fixed"

These are untracked or gitignored files whose corruption cannot be repaired
without inventing intent, so they are named rather than guessed at:

| file | finding | suggested action |
|---|---|---|
| `cli12.mjs` (untracked) | **10 leaked `SEARCH`/`REPLACE` patch markers** (lines 34, 134, 206, 276, 394, 401, 666, 765, 981, 1018) — a half-applied patch. The tracked `cli.mjs` parses fine | discard or regenerate from `cli.mjs` |
| `cli1.mjs` (untracked, 185 KB) | mangled string literal at line 2109 (`SYS_BASE`) | regenerate |
| `dist/server.js` (untracked, `dist/` is gitignored) | marker garbage (`<< ;`, `write;`, `src / index.ts;`) at 242–244 — corrupted **build output** | rebuild, then delete |
| `repo-fixes/builderBRO/cli.mjs`, `repo-fixes/qrbtc-api/api/{passport,score}.js` (untracked copies of other repos) | parse errors | out of this repo's scope |
| `dycrag_system/__pycache__/*.pyc` ×2 | compiled artifacts committed to the index (`git ls-files` shows them, `.gitignore` does not remove tracked files) | `git rm --cached` + `__pycache__/` in `.gitignore` — a git/index change, so left to you |
| `NewState/mcp-server/write_index_mjs.js` | hardcodes `C:\Users\lynnh\openkraft\NewState\…` as its output path — it is a one-off local generator, and where it ran is baked in | parameterise or drop it |
| `.gitignore` | lists `NewState/`, `dycrag_system/`, `repo-fixes/` as ignored while files inside them are **tracked** — the ignore is misleading, not protecting | choose one | 

## 6. What was *not* touched, and why

* **Published records.** `freebrain-residence/{ledger.jsonl,evidence/q1-evidence.jsonl,
  operator-events.jsonl}`, `state.json`, `loop_guard.json` — read only. Their
  checksums are identical before and after every command above (the Q1 log even
  keeps its 1,780 rows while being audited, because `evidence_audit` is read-only).
* **The residence's 1,000-cycle study.** `state.json` says `cycle: 1000`, `phase: done`;
  its end state is a real result and was not rewritten.
* **M1–M5 of `bro-freebuff-brain-spec.md`.** They need a live client session
  (M1/M3), a working provider key (M3) and Windows hardware (M5). Not attempted.
* **An `~/.agents/mcp.json` global install.** Outside the project tree; would make
  the bridge appear in every Freebuff project. Offered, not done.

## 7. Reproduction

```bash
python3 -m unittest discover -p '*_test.py'   # 670 tests, all green
python3 source_integrity_test.py              # 5 — every source parses; templates sound
python3 builderbro_mcp_test.py                # 55 — incl. both spawn forms from a foreign cwd
python3 builderbro_mcp.py --tools             # the five tool schemas

# the bridge, from a cwd that is not the repository
cd /tmp && python3 /mnt/sdcard/Download/builderbro/builderbro_mcp.py --call evidence_audit --json '{}'

# the repaired generator
cd dycrag_system && python3 main.py           # … DyCRAG Demonstration Finished.
```
