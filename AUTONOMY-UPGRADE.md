# AUTONOMY-UPGRADE — what is missing, and the order to build it

**Status of this file: proposal, partially delivered.** Every "present" claim below
was read out of the source or a ledger on **2026-09-16** and names its evidence; the
counts in §1 and the absences in §2 are that snapshot and have deliberately been
left as written, so what was *predicted* can be compared against what was
*measured*. **A1, A2 and A3 have since been delivered** — §4 carries their status
and their measured numbers, §2's first three rows are closed by A1, and A2's and
A3's own predictions are compared against their measurements below. Every other
proposal is still `planned` and must not be depended on (same rule as
`CAPABILITY_INVENTORY.md`, which is the record of what exists *now*).

Read this after `CAPABILITY_INVENTORY.md`. That file answers "what can it do".
This one answers "what does *autonomy* require that it cannot do yet, and what is
the next honest step".

---

## 1. What is actually on disk

Two halves that are easy to mistake for one system:

| half | what it is | is it the autonomy layer? |
| --- | --- | --- |
| BRO Node CLI (`loader.mjs` → `cli.mjs` 332 KB, `bromance.mjs`, `bro-web.mjs`, `bro-build.mjs`, `model-selector.mjs`) | an interactive terminal assistant with web/build tools and a Vertex → Base44 → Groq → OpenAI → local model seam | **no** — presentation, tools and model selection for a human at a keyboard |
| Free Brain Python core (`agent_runtime.py` 1,217 lines + `brain_cascade.py`, `loop_guard.py`, `rag_*.py`, `drift_loop.py`, `qih_metrics.py`; ≈8.7 k lines incl. tests) | the autonomy layer proper | **yes** |

The Python core, verified:

| layer | state | evidence |
| --- | --- | --- |
| L0 brain — local-first cascade over free OpenAI-compatible providers, failure classes, cooldowns, 1-token serving probe, retired-model re-discovery | `verified` | `brain_cascade.py`; 49 tests; live: Cerebras `/models` 200 vs `/chat/completions` 402, Groq retired model self-healed to `openai/gpt-oss-120b` |
| L1 loop — bounded tool loop, 5 read-only tools (`list_dir`, `read_file`, `drive_sync`, `qih_metric`, `rag`), `MAX_STEPS = 8`, 256 tokens/step, one canonical structured failure | `verified` | `agent_runtime.py`; `Tool(fn, probe)` per entry, validated at import |
| L1 guard — per-step observation, 9 named reason codes with stage + severity, compaction, self-improvement triggers on high/critical | `verified` | `loop_guard.py`; audit 6/11 → 11/11 correct classification, 41 → 20 steps burned on doomed runs, 0 false-positive triggers |
| L2 retrieval + grounding | `verified` | `rag_core.py` / `rag_eval.py`; recall@k 0.929, MRR 0.729, citation fidelity 1.000, false-answer 0.000, auditor bracketed by an honest and an adversarial control |
| L4 ledger (partial) — evidence JSONL, drift ledger, provenance-free | `unmeasured` for L4's actual job | 32 records in `freebrain-residence/ledger.jsonl`; no invariant/observed/volatile tags, no `promote()` |
| QIH metrics — Born rule, d_ij, C_MT, phase-clock, machine-recorded | `verified` | `qih_metrics.py`; 21 tests |
| P4 drift loop — file-driven state machine (objective, instructions, state, ledger, trigger) | `partially verified` | 32 cycles, coherence min 0.25 / mean 0.97; target is 1,000 |

Test suites at the time of writing: **222 tests, all passing** (`agent_runtime_test`
32, `brain_cascade_test` 49, `drift_loop_test` 22, `qih_metrics_test` 21,
`rag_test` 42, `loop_guard_test` 56). Now **298**, with `autonomy_test` added — the
current count and its reproduction command live in `CAPABILITY_INVENTORY.md`, which
is the live document; this one is a snapshot.

---

## 2. The autonomy gap, stated as absences

Not opinions — each row is something a reader can check.

> **Snapshot (2026-09-16).** Since this was written, **A1 closed the first three
> rows** — a plan, per-step verification, and task-level evaluation all now exist
> (`autonomy.py`, `loop_audit.py --goals`, `autonomy_suite.py`), with their measured
> numbers in §4 — and **A3 tightened the second row further**: "verification that
> the goal was met" is now verification that a *step's claim* was independently
> confirmed, not merely that the model's own expectation held (`verifier.py`). The
> rows are left verbatim because the gap *was* real and the honest comparison is
> prediction against measurement. Read them as "what was missing on 2026-09-16",
> not as current state.

| what autonomy requires | present? | evidence of absence |
| --- | --- | --- |
| a **plan** (goal → ordered steps with expected outcomes) | **no** | no plan/subgoal/decomposition structure anywhere in the core; the loop asks the model for one tool call and then `FINAL:`. The only mention of the concept in the codebase is `loop_guard.py`'s own diagnostic lever string for `budget_exhausted`: `"MAX_STEPS, goal decomposition"` — the guard names the missing capability as the fix |
| **verification that the goal was met** | **no** | `FINAL:` is accepted as success; success means "the model stopped talking", not "the stated condition holds". The guard detects *faults*, never *unmet goals* |
| **learning from its own history** | **no** | the ledger is write-only — nothing reads it to change behaviour. The one real self-improvement loop (`rag_diagnose`) tunes *config* (fusion, chunk budget, dense weight), not policy |
| **execution** of anything it writes | **no** | no sandbox, Docker, seccomp or rlimit path in the Python core; the model cannot run code at all (P1 unstarted) |
| **memory beyond lexical retrieval** | **no** | RAG is lexical over a document corpus (`dense_only = 0` — the hashed-dense channel found zero gold chunks alone). `.bro/memory_recall.json` exists but belongs to the Node side and the loop never reads it |
| **task-level evaluation** | **no** | all 222 tests are deterministic/hermetic. Nothing measures task completion, so none of FREE-BRAIN §8's three headline metrics (>90% completion, 100% escape prevention, <100 MB per loop) has a harness |
| **long-horizon evidence** | **no** | 32 of 1,000 cycles, and the coherence metric is near-degenerate (mean 0.97, min 0.25 — it mostly returns 1.0), so even those 32 are not a curve yet |
| multi-model routing (L5) | **no** | `[SPECULATIVE]` in FREE-BRAIN §5, deliberately not a dependency |

---

## 3. The single highest-leverage gap

**Verification.** Today the loop's success signal is the model's own assertion that
it is finished, and its failure signal is a repeat/error pattern. So the loop can be
*instrumented* but not *goal-directed*: it cannot tell "done" from "gave up", and
every later capability inherits that blindness.

- Self-improvement without verification optimises the wrong quantity (a policy can
  learn to end runs early).
- Tool synthesis without verification promotes untested code.
- A 1,000-cycle run without verification measures self-consistency, not success.

The repo already knows this: `loop_guard`'s `budget_exhausted` diagnosis points the
operator at "goal decomposition" — the system diagnosed its own missing capability
before any document did. A1 below is how that lever gets pulled.

---

## 4. Upgrade path, dependency-ordered

Each phase ends runnable and independently testable, and each adds its **own**
comparison arm to `loop_audit.py` — the audit's guard-OFF baseline cannot isolate
an intermediate rule, and that is already recorded as a known weakness.

> **Status (2026-09-19).** A1 is **implemented and measured**; §5.1 (the task
> suite) and §5.2 (per-task budgets) ship with it. **A3 and A2 are also
> delivered** (`verifier.py`, then `memory.py`, each with its own audit arm and
> its own closed cycle), so of the phases below, A1, A2 and A3 are done and
> A4–A8 are still `planned`. A3 was built before A2 on purpose, and that ordering
> paid: A2's levels are derived from the verifier rather than chosen, so the map
> is `invariant` (confirmed and reproduced) → `invariant`, `observed` (real, not
> re-observed) → `observed`, and `declared` (the verifier off) → **`volatile`** —
> without that last row, turning the verifier off would have been a way to
> launder a model's claim into a remembered fact. The delivered numbers, the
> audit arms that back them, and the weaknesses found by the first live runs are
> in `CAPABILITY_INVENTORY.md` ("Goal-directed loop", "Adversarial verifier (A3)",
> "Episodic memory with provenance (A2)") and in the closed cycles in
> `SELF_IMPROVEMENT_LOG.md`.
>
> **A2 is now implemented and measured** (`memory.py`): one audit task run twice
> over one store, in four cases. With recall **off** the second run is refused
> (`unverified_completion`); with it **on** the same task succeeds, because the
> fact the first run confirmed is in front of the planner. With **no history at
> all** it fails, so the improvement is the record and not the store's existence;
> and with the **same value seeded with the verifier off** it is recalled and
> *unusable* — offered below the unverified header, never in a fact position.
> False recall **0** with the value actually searched for. `audit_ok` now requires
> the arm's verdict to be `useful`, so recall that changes no outcome fails the
> audit. The prediction this measurement contradicted is below.
>
> Headline: reflex loop **5/5 false successes** on the same scripted replies;
> verified loop **0/5**, with **18/18** goal-arm pathologies classified correctly
> and **20/20** tasks passing eight pre-registered floors on two identical runs.
> A later `weakness_closure` cycle closed the five real holes the first one left
> open — a goal satisfiable by a *vacuum*, near-vacuous goal kinds, a dropped reply
> killing the run, unmeasured budget adequacy, and the audit's own hardcoded ground
> truth — recorded separately, because it is separate work.
>
> **A3 is now also implemented and measured** (`verifier.py`): A1 checks the
> expectation the *model* declared, so four unsupported step claims were promoted;
> with independent confirmation the lying control is detected **4/4** and the
> honest control refused **0/2** — verdict `useful`, and a verifier that detects
> nothing is reported `no_verifier` rather than as a pass. The `adversarial_verifier`
> cycle is in `SELF_IMPROVEMENT_LOG.md`.
>
> The design note below is left as written, so what was *predicted* can be
> compared against what was *measured* — including the one false refusal the
> prediction did not anticipate, and for A3 the shape its prediction did *not*
> name: the hole was not a lying model but a *non-discriminating expectation*, and
> the honest control needed a second case (a real claim on a tool that cannot be
> re-run) to stop a deny-everything policy from scoring as a verifier.

### A1 — A plan and a per-step verification gate  *(highest leverage, do first)*

| field | value |
| --- | --- |
| change | the model first emits a plan (`[{action, expect}]`), the loop executes one step at a time, checks each step's declared observable outcome, replans on mismatch, and accepts `FINAL:` **only** when the goal's condition is verified |
| new reason codes | `unverified_completion` (claims done, condition false), `plan_divergence`, `replan_storm` |
| measurement | new `loop_audit` pathologies: a model that claims success without acting; a step that "succeeds" but whose expected observation is absent; a plan needing one legitimate replan; a rogue step |
| delivered | 14 goal pathologies at A1 (12 scripted, 2 forced by live runs) — **18** after the separate weakness-closure cycle — plus the reflex-vs-verified false-success comparison and 20 suite tasks |
| metrics | verified-completion rate **1.000**, false-success rate **0.000** (floor 0), replans per completed task **0.111** |
| exit | **met** — false success 0, no regression (detection arm still 11/11). Two faults the exit criterion did not anticipate were found only by live runs: a model that re-emits its plan (`no_action`) and a verified goal with an empty answer (`empty_answer`) |

Why first: it is the only change that makes later ones safe, and the harness to
measure it (scripted pathologies through the real loop) already exists.

### A2 — Episodic memory with provenance  *(delivered)*

| field | value |
| --- | --- |
| change | the ledger becomes readable: past actions and outcomes recalled into the prompt, tagged `invariant` / `observed` / `volatile` (AGENT-INTEGRITY semantics), with only `invariant` promotable to fact |
| measurement | scripted tasks whose second run needs the first run's outcome; false-recall (an unverified value presented as fact) must be 0 |
| exit | task success improves with recall versus without, on the same suite; false-recall 0 |
| delivered | `memory.py` (three levels taken verbatim from `AGENT-INTEGRITY.md`, an append-only JSONL store, lexical recall, a render whose facts/unverified split is structural, `ProvenanceError` on using a non-invariant as a fact, gated promotion, and an `audit()` that re-checks a rendered block against the records it claims to render); wired into `autonomy.run_goal_verified` behind `memory_store=None`, so no pre-A2 caller changes; `loop_guard` registers 5 recall thresholds; `loop_audit.py --memory` adds the arm; `autonomy_suite.py --register-memory` closes the cycle |
| metrics | recall **ON succeeded where OFF failed** on the same task and store; no-history control failed; never-confirmed control recalled but **unusable** (0 facts offered); false recall **0** with 1 value actually checked for leakage; seeded with the verifier off → stored `volatile`, with it on → `invariant`; verdict **`useful`** |
| exit | **met** — success improves with recall and false recall is 0, with the no-history case as the control that the improvement is attributable. The prediction this measurement **added to**: the exit criterion said nothing about memory reaching the *goal gate*, and the hole it did not name is the tempting shortcut — a goal condition that holds over recalled facts alone. That is now refused with `recall_gap: true` in the diagnosis, tested in both directions, because a fact confirmed in an earlier run is a hint about where to look, not an observation of what is there now |
| cost of the measurement | verifying A2 turned up a second evidence leak: `agent_runtime.run_goal` wrote `_emit_step` records unconditionally, so a suite run appended 5 stub rows to the published Q1 log. 7,033 such rows had accumulated against 374 real measurements; they are quarantined (kept, not deleted) in `evidence/test-artifacts.jsonl`, the write is gated at the source, and it is asserted in both directions. Recorded as its own cycle because it is separate work |
| still open | recall is lexical (deliberate: §6 below); the store is read once at plan time, so a run cannot recall its own earlier steps; records are truncated to `recall_value_chars`; no decay and no size cap; the arm's consumer is a deterministic stand-in, so it measures the channel rather than a real model's use of it |

### A3 — An adversarial verifier  *(delivered)*

| field | value |
| --- | --- |
| change | a step's claimed outcome must be independently confirmed (deterministic where possible, a second model where not) before promotion |
| measurement | a lying model's detection rate **and** the false-accusation rate on honest runs — the same two-control bracketing that made the RAG auditor trustworthy (`extractive` passes, `hallucinating` fails) |
| exit | both controls behave; a verifier that passes everything is treated as no verifier |
| delivered | `verifier.py` (four named checks — `expectation_held`, `not_failure`, `not_vacuous`, `reproduced` — with `invariant` / `observed` levels carried into `how_verified`), wired into every promotion in `autonomy.run_goal_verified` behind `verify_mode: confirm`; `loop_audit.py --verifier` adds 6 claims and both controls; `autonomy_suite.py --register-verifier` closes the cycle |
| metrics | lying control **4/4 (rate 1.00)**, honest control **0/2 (rate 0.00)**, verdict **`useful`**; promotions with nothing independent behind them **4 → 0**; no regression (detection 11/11, goal arm 18/18, suite 20/20 on 8 floors) |
| exit | **met** — both controls behave, and `audit_ok` requires `useful`, so a verifier detecting nothing fails the audit. The prediction that missed: the hole was not dishonesty but a *non-discriminating expectation* (`nonempty` holds on the tool's own failure line), and the honest control needed a second case — a real claim on a tool that cannot be re-run — or a refuse-everything policy would have scored as a verifier |
| live coverage | **delivered** — `live_refusal_probe.py` drives the production loop over the real cascade and records the refusal + the model's reply verbatim. Measured: `not_vacuous` and `not_failure` both reached a real model, both times the model **repaired correctly** (it replaced `expect: nonempty` with a specific `contains:` needle, the loop accepted the replacement, and the goal then verified over 3 `invariant` steps). The model-planned arm reached **no** refusal — it wrote the pathological `expect: nonempty` itself, but the plan gate refused its weak `GOAL-CHECK` first, which is why the probe needs a seeded arm at all |
| found by those live runs | four defects no stub arm could see, all fixed: a correct `PLAN:` block discarded for missing the `REPLAN:` keyword (3× in one run); a rejection note whose suggested repair ("re-run the step") cannot change an expectation the plan holds; a supplied `plan=` parsed but never shown to the model; and an empty reply that had **consumed its whole cap** retried at the same cap — measured 256/256, 512/512, 1024/1024 tokens of reasoning with no content, then the correct directive in 12 tokens at 2048, so the retry could not succeed and the run died `empty_response` while the model held the answer |
| still open | the second model ships **off** (`verify_model_adjudication: 0`) and is unmeasured live; reproduction covers only tools declared deterministic; a specific-but-irrelevant needle can still promote, because judging relevance needs entailment; the live probe's seeded arm is the only route to a refusal, since a model-planned run is refused earlier for a weak goal condition |

### A4 — Local sandbox and caps (P1)

| field | value |
| --- | --- |
| change | subprocess execution with rlimits, closed stdin, wall-clock deadline, output cap; namespace/network isolation **if the target device supports it**; Docker when available |
| measurement | the FREE-BRAIN §8 escape suite (network, fs, process, mem, fork-bomb, infinite output) |
| exit | 0 escapes, caps enforced outside the model, and re-run on every runtime/image change |

Honest note: this runs on phone-class hardware (Termux). Whether `unshare`/
namespaces work there must be **measured and reported either way** — a failed
isolation primitive is a finding, not a reason to skip the suite.

### A5 — Tool synthesis behind the gate chain (P2)

| field | value |
| --- | --- |
| change | the model emits a tool manifest + code → static gate → A4 sandbox → A3 verification (delivered: `verifier.confirm` + `how_verified`) → promote to `invariant` |
| measurement | manifest fuzz (all malformed/over-privileged manifests rejected) and the 20 curated multi-language tasks |
| exit | 100% invalid manifests rejected; completion rate at or above a pre-registered floor, measured twice |

### A6 — Policy learning on a replayable suite

| field | value |
| --- | --- |
| change | search **policy**, not just config: plan depth, retry behaviour, escalation to a stronger cascade hop, tool order — scored by replaying the audit pathologies plus the curated task suite |
| measurement | before/after on the replay suite, floors pre-registered before the search |
| exit | a measured improvement on a replayable suite, registered to config and revertible without editing code (the `repeat_mode` pattern) |

### A7 — The 1,000-cycle drift study, with a metric worth publishing

| field | value |
| --- | --- |
| change | first fix the coherence metric — it should test cross-cycle objective invariance, not only anchor retention (min 0.25 / mean 0.97 over 32 cycles is too coarse to be called a curve), then run |
| measurement | the drift curve, per-cycle graph hashes (Test 2), rejection rate, breaker trips |
| exit | pass **or** fail, reported either way. Also: run one cycle in `freebrain-residence/` so the already-implemented cascade health block appears in a real ledger line (the 32 records on disk predate it) |

### A8 — Router (L5) — speculative, last

Only after A1 and A3 exist — **both now do** — because a router without
per-boundary verification just distributes unverified output. Pre-registered
hypothesis; "does not work" is an acceptable result.

---

## 5. Cross-cutting, non-optional

1. **A task-level suite.** ~~Every current test is deterministic. Add a curated
   20-task suite with a pre-registered floor, run twice, before making any claim
   about autonomy.~~ **Done** — `autonomy_suite.py`: 20 tasks, seven pre-registered
   floors, `--repeat` refuses to report if verdicts differ between runs, and
   `--register` writes a closed cycle. `--live` runs the same goals against the
   real model and is informational on purpose. Scope limit stated in `FREE-BRAIN.md`
   §8.2: the tasks are read/measure/refuse/budget/fault shapes, not code generation.
2. **Per-task accounting.** ~~Extend it to a per-task budget the loop must refuse
   to exceed.~~ **Done** — `task_token_budget`, plus per-task `max_tool_steps` and
   `max_calls`; two suite tasks exist purely to prove the loop stops at a ceiling
   (`budget_exhausted`, `token_budget_exceeded`), and `budget_compliance` is a floor.
   Still open: nothing measures whether a chosen budget was *warranted*.
3. **Discipline that already works, keep it.** The audit's A/B arms, the
   two-control bracketing of every checker, the register-to-config pattern, and the
   rule that the model can never touch the runtime, the guard, or the ledger.

---

## 6. What not to do next

| tempting | why not yet |
| --- | --- |
| more tools | each new tool widens the surface that A1 verification has to cover; the failure protocol would also need a probe per new entry (cheap, but the point is ordering) |
| multi-agent routing | distributes unverified decisions; depends on A1/A3 |
| a 1,000-cycle run now | would produce a curve from a metric that mostly returns 1.0 |
| semantic embeddings (new dependency) | the repo is deliberately stdlib-only, and retrieval currently *passes* its floors (recall@k 0.929, MRR 0.729). The binding constraint is verification, not retrieval quality — add embeddings when a measurement says lexical is what is failing |
| sandboxed code execution before A1 | would let the system run generated code while still unable to tell whether it worked |
