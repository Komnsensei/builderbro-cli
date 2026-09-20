# CAPABILITY_INVENTORY

Machine-checkable inventory of what BuilderBro can actually do. Every entry names
its verified entry point, its interface, the exact command that reproduces its
evidence, and its measured number.

**Rule for this file:** a capability is `verified` only if it has a reproduction
command that has been run and a number that came out of it. A capability with a
mechanism but no measurement is `unmeasured`. Aspirational entries are
`planned` and must not be depended on.

Last updated: 2026-09-20 (the verification stack exposed over MCP so an external
agent can call it, and a diverged working tree reconciled against the last
commit; 517 tests)

See also: **`AUTONOMY-UPGRADE.md`** — a measured assessment of what autonomy still
lacks and the dependency-ordered plan to add it. Written 2026-09-16 as a proposal;
its A1, A2 and A3 phases are now delivered and marked as such in §4 of that file.
Everything there beyond A3 is still `planned` and must not be depended on.

Test suites (the reproduction command for every count below):
`python3 -m unittest agent_runtime_test autonomy_test brain_cascade_test drift_loop_test loop_guard_test qih_metrics_test`
→ 340 tests, `python3 -m unittest rag_test` → 55, `python3 memory_test.py` → 42,
plus `verifier_test` 27, `live_refusal_probe_test` 8 and `builderbro_mcp_test` 45.
**517 total, all pass.**

The counts in this file are also the claim most likely to go stale, and they had
(443 → 517 was four modules' worth of tests the header had not been told about).
They are re-measured, not carried forward.

---

## RAG pipeline — `verified`

| field | value |
| --- | --- |
| status | verified |
| implementation | `rag_core.py` — `rag_eval.py` — `rag_diagnose.py` |
| skill package | `.agents/skills/builderbro-rag/SKILL.md` |
| entry point | `python3 .agents/skills/builderbro-rag/rag_skill.py <command>` |
| agent tool | `rag <command> [args]` (allowlisted in `agent_runtime.py`) |
| runtime | python3 stdlib only — no packages, no network required for retrieval/eval |
| config | `rag_config.json` (registered by the self-building cycle) overlaid on `rag_core.DEFAULT_CONFIG` |
| tests | `rag_test.py` — 54 tests |

### Interface

```
rag search <query> [-k N]      ranked chunks + citation markers + char spans
rag ask <question>             gate -> generate -> audit; --generator live|extractive|hallucinate
rag build                      re-ingest and re-index the corpus
rag eval                       measured quality vs pre-registered floors
rag diagnose [--cycle]         stage-attributed findings; optionally self-build
rag contract                   machine-readable interface contract
```

### Measured (the number is the capability)

| metric | value | floor | source |
| --- | --- | --- | --- |
| span coverage (gold text reachable in a chunk) | 1.000 | 1.00 | `rag eval` |
| recall@k | 1.000 | 0.85 | `rag eval` |
| MRR | 0.732 | 0.70 | `rag eval` |
| top-1 source accuracy | 0.714 | 0.60 | `rag eval` |
| citation fidelity | 1.000 | 0.95 | `rag eval` |
| refusal accuracy | 1.000 | 1.00 | `rag eval` |
| false-answer rate (unanswerable questions) | 0.000 | ≤ 0.00 | `rag eval` |
| false-refusal rate (answerable questions) | 0.000 | ≤ 0.00 | `rag eval` |

Reproduce: `python3 .agents/skills/builderbro-rag/rag_skill.py eval --generator extractive`

### Auditor validation — the reason to trust the numbers above

The grounding auditor is bracketed by two controls. An auditor that passes
everything or fails everything proves nothing.

| control | citation fidelity | groundedness | fabricated markers | gate |
| --- | --- | --- | --- | --- |
| extractive (copies real sentences) | 1.000 | 1.000 | 0 | pass |
| hallucinating (fabricates on purpose) | 0.000 | 0.000 | 14 | **fail** |

Reproduce: `python3 .agents/skills/builderbro-rag/rag_skill.py eval --generator hallucinate`

### Scoring memo — `verified`, and measured

Both scoring channels are memoised per **(query, channel-relevant config)**. The
sweep is the reason it exists: `rag_diagnose.sweep` searches one index under every
candidate, so without the memo it recomputes byte-identical scores once per
candidate. Measured over the real 56-candidate sweep:

| | wall time | channel computations |
| --- | --- | --- |
| memo off | 71.3 s | 1904 |
| memo on | 24.5 s | 272 |
| | **−66%** | hit rate **85.7%** |

An optimisation has to prove it changes nothing, so the tests pair a memoised
index against a fresh one with an empty cache for every candidate config — and
force eviction (`SCORE_MEMO_MAX = 1`) to show the memory bound cannot alter a
result either.

The key covers *exactly* the knobs each channel reads: `bm25_*` for the lexical
channel, `dim`/`char_ngram`/`char_weight` for the dense one, and nothing for
`top_k`/`fusion`/`dense_weight`/chunk geometry. A test asserts that boundary in
both directions, because an over-broad key is correct today and wrong the moment
one of those knobs moves.

### Search-config semantics — fixed while verifying the memo

Writing the memo's tests exposed a latent defect: `search(query, config=cfg)`
**accepted** a config and silently scored with the index's own, so a caller asking
for `bm25_k1=2.2` got 1.2's scores. The channels now read the config they are
given (and the memo key is read from that same object, so the two cannot
disagree).

A config that moves vector geometry is now **refused** rather than honoured.
`dim`/`char_ngram`/`char_weight` shape the stored vectors; a smaller `dim` did not
even fail — `zip` silently shortened the dot product, so the index would have
returned a confident wrong ranking. The refusal names the knob and the fix
(rebuild the index). Tests: `ScoreMemoTest` (7 tests).

### Self-diagnosis and self-building — `verified`

| field | value |
| --- | --- |
| status | verified |
| mechanism | `rag_diagnose.attribute()` maps a measured symptom to a failing **stage**; `run_cycle()` runs DETECT → RESEARCH → DESIGN → IMPLEMENT → TEST → REGISTER |
| search space | fusion × chunk budget × chunk overlap × dense weight (56 candidates), then the confidence threshold, then — when no threshold can work — the retrieved-context size |
| gate | hard floors: span_coverage = 1.00, recall@k ≥ 0.85, MRR ≥ 0.70 |
| objective | recall@k + MRR, tie-break top-1 source accuracy then fewer chunks |
| registration | writes `rag_config.json`, which changes every later run |
| log | `SELF_IMPROVEMENT_LOG.md` (append-only) |

First real cycle result: **recall 0.857 → 0.929, MRR 0.667 → 0.729**, registered as
`fusion=linear, budget=100, overlap=20, dense_weight=1.0, min_query_support=0.55`.

Second cycle (2026-09-17): **MRR 0.729 → 0.768, top-1 0.643 → 0.714**, registered as
`fusion=linear, budget=120, overlap=24, dense_weight=1.0, min_query_support=0.5475`.
It re-tuned on a corpus that had moved — see "the corpus edits itself" below.

Third cycle (2026-09-19): the corpus moved again, the two support sets came to
**overlap** (no threshold can fix that), and the cycle instead found the fault
upstream — the retrieved context was one chunk short. Registered `top_k: 7` (the
smallest size that separates) with `min_query_support=0.55`; **recall@k 0.929 →
1.000**, `false_answer 0.000`, `false_refusal 0.000`. See the context stage below.

Reproduce: `python3 .agents/skills/builderbro-rag/rag_skill.py diagnose --cycle`

### The gate's placement — what the grid could not do, and what replaced it

The gate (`min_query_support`) decides whether a question gets answered at all, so
a gate *below* the highest unanswerable support answers an unanswerable question.
That is exactly what happened: the registered `0.55` ended up under a max-negative
support of `0.5504`, `/rag eval` reported `false_answer_rate 0.33`, and the grid
could not fix it — the measured gap was **0.0053 against a grid step of 0.05**.

| | before | after |
| --- | --- | --- |
| max unanswerable support | 0.5504 | 0.5425 |
| gate | 0.5500 (**below it**) | 0.5475 |
| min answerable support | 0.5557 | 0.5524 |
| margin below / above | −0.0004 / 0.0057 | +0.0050 / +0.0049 |
| false-answer rate | 0.333 | 0.000 |

`sweep_support` now derives one extra candidate — the **midpoint of the gap
between the two measured sets**, the maximum-margin placement — and validates it
through the same evaluation as every grid point. Derived is not the same as
accepted: it is kept only if it shows a perfect refusal record. When the sets
overlap the rule returns nothing, because that is a retrieval fault, not a
threshold one, and returning a number would look like a decision.

Tests: `GatePlacementTest` (5 tests) — the separator invariant on the live corpus
plus the rule itself against injected supports.

> The figures above are the *registering cycle's* measurement, not a live
guarantee, and they are deliberately stated that way: writing them down changes
the corpus they describe, so a precise number here is self-invalidating. The live
check is `test_the_registered_gate_separates_the_two_measured_sets`, which
re-derives the supports from whatever the corpus currently holds.

#### When no threshold can work: the context stage

The rule above returns nothing when the sets overlap. That is not a rare corner —
it happened again on the next large addition to these docs, and harder: the gate
had been running on a **0.005** margin, the corpus grew from 170 to 206 chunks,
and the lowest answerable support ended up **below** the highest unanswerable one.
No threshold can separate overlapping sets, so `false_answer_rate` and
`false_refusal_rate` could not both be perfect — a real loss of capability, and the
cycle reported `changed: False`.

What the overlap was: `rag-g12` ("How does the loop survive a crash part way
through a run?") scored **0.5206** against a max-negative of **0.5444**. Its gold
chunk — the one saying `resume-on-crash` — was not in the top 6, while the
question's other words (`part`, `way`) appear in the corpus but not in that
context.

`context_boundary` is the third stage: **the smallest retrieved-context size at
which the gate can work.**

| top_k | min answerable | max unanswerable | gap |
| --- | --- | --- | --- |
| 4–6 | 0.5206 | 0.5444 | **−0.0238** |
| **7 (registered)** | 0.6846 | 0.5444 | **+0.1402** |
| 8–12 | 0.6846 | 0.5702 | +0.1144 |

One more chunk is the difference, and it is the *smallest* size that works: the
criterion cannot be the retrieval objective, because `recall_at_k` is scored *at*
k, so a larger context is always rewarded for being larger. Registered by the
instrument (`rag_diagnose.py --cycle --register`): `top_k: 7`,
`min_query_support: 0.55`, and the gate now passes with `false_answer 0.000`,
`false_refusal 0.000`, `refusal_accuracy 1.000`.

The fragility is now covered in both directions: the gate test re-derives the
supports from the live corpus, and `test_the_gate_can_work_at_the_registered_context_size`
asserts the registered context is the smallest that separates the sets — and that
the size it replaced does **not**.

### The corpus edits itself — a fragility worth naming

**The RAG corpus is this repository's own documentation.** Every figure in the
tables above is therefore a measurement of the docs as they stood when it ran: a
sentence added to `FREE-BRAIN.md` re-chunks the corpus and moves recall, MRR *and*
the support values the gate compares. That is how the gate slipped — a
verification block added to this very file shifted one support by 0.002 across the
threshold. Nothing in the pipeline was wrong; the measurement target moved.

The consequence is that these floors are a **regression test on the docs as much
as on the code**. Two edits are not safe to make blind: a large addition to an
indexed file, and anything that changes the words in `GOLDEN_ITEMS`. Both are
detectable (`python3 -m unittest rag_test`), which is the point of the invariant
above.

Detection is only half of it. The first time this happened the remedy was a
finer-grained threshold; the second time the sets **overlapped**, and the remedy
had to be upstream — the context stage described above. That is the useful shape:
a moved corpus does not mean re-tune the gate, it means re-run the cycle, which now
has a stage for the case where the gate is not the problem.

### Known weaknesses — do not paper over these

| weakness | measured evidence |
| --- | --- |
| retrieval is lexical, not semantic | `attribution dense_only=0` — the hashed-dense channel found **zero** gold chunks on its own |
| the confidence gate's margin is 0.0050 / 0.0049 | max unanswerable 0.5425 vs min answerable 0.5524 — placed at the midpoint, and 3 negatives is a thin basis for a separator |
| the support grid (step 0.05) is coarser than the measured gap (0.0099) | fixed by deriving the boundary candidate, but *the grid itself is still coarse* — a future corpus could put the boundary somewhere neither the grid nor the midpoint serves |
| the corpus is the repository's own docs | a doc edit moved a support by 0.002 and flipped the gate; every floor here is measured against a moving target |
| `SELF_IMPROVEMENT_LOG.md` entries before 2026-09-17T20:56 have placeholder DETECT fields | the writer printed `budget=0 overlap=0` for incumbents that had neither; the writer is fixed, the old entries were left as written |
| entailment scoring penalises paraphrase | live answer with correct citations scored `unsupported_rate` 0.50, `strict_verdict=fail` |
| the golden set is 17 items | one item is ~7% of recall; small deltas are not signal |
| 1 of 14 questions still misses top-6 | `rag diagnose` → `[retrieval] rag-g04 missing` |
| the memo's bound is a memory guard, not a measured threshold | `SCORE_MEMO_MAX = 512` entries per index; an eviction costs a recomputation, never a different answer (tested) |
| the geometry refusal has no production caller yet | the sweep never moves geometry, so its behaviour outside the tests is reasoned, not observed |

---

## Autonomy harness — `verified`

| field | value |
| --- | --- |
| status | verified |
| implementation | `agent_runtime.py` (bounded tool loop), `drift_loop.py` (file-driven self-rewrite loop) |
| tools | `list_dir`, `read_file`, `drive_sync`, `qih_metric`, `rag` |
| tests | `agent_runtime_test.py` (32), `drift_loop_test.py` (22) |

## Autonomy loop guard — `verified`

| field | value |
| --- | --- |
| status | verified |
| implementation | `loop_guard.py` — per-step observation, limit detection, self-improvement triggers |
| audit instrument | `loop_audit.py` — 9 scripted pathologies through the real `run_goal`, A/B with the guard off and on |
| registered config | `loop_guard.json` |
| evidence | `SELF_IMPROVEMENT_LOG.md` (append-only), `freebrain-residence/loop-diagnoses.jsonl` |
| tests | `loop_guard_test.py` (56) |

### Audited result

The loop originally had two exits: `FINAL:` or the step counter ran out. Four
different faults all arrived as the same bare `reason=step budget exceeded`, and
none of them were *detected* — the loop kept paying for steps until the counter
stopped it.

| pathology | expected detection | before | after |
| --- | --- | --- | --- |
| `healthy` (tool then FINAL) | success | correct | correct |
| `stuck_repeating` | `no_progress` | budget_exhausted | correct |
| `unknown_tools` | `unknown_tool_storm` | budget_exhausted | correct |
| `failing_tools` | `tool_error_storm` | budget_exhausted | correct |
| `missing_args` | `tool_error_storm` | budget_exhausted | correct |
| `context_growth` | compaction, no overflow | budget_exhausted | correct |
| `chatty_no_tools` | success | correct | correct |
| `productive_but_long` | `budget_exhausted` | correct | correct |
| `dead_brain` | `model_unavailable` | correct | correct |
| `revisit_interleaved` (legitimate) | success | correct | correct |
| `revisit_changed_result` (legitimate) | success | correct | correct |

**Correct classification: 6/11 (55%) → 11/11 (100%).** Steps burned on doomed
runs: **41 → 20.** Context compactions: **0 → 3** (peak 30,823 chars, no silent
overflow). False-positive triggers on healthy/legitimate runs: **none**.

Reproduce: `python3 loop_audit.py`

### Repeat-rule comparison (the last two rows, measured properly)

The `before` column above is the guard switched *off*, which cannot show what the
first hardening's repeat rule cost. `loop_audit.py` therefore runs the
repeat-sensitive pathologies again under each registered rule:

| pathology | expected | `streak` (default) | `cumulative` |
| --- | --- | --- | --- |
| `stuck_repeating` | `no_progress` | correct | correct |
| `revisit_interleaved` | success | correct | **wrong** (`no_progress`) |
| `revisit_changed_result` | success | correct | **wrong** (`no_progress`) |

**False positives on legitimate revisits: `cumulative` 2, `streak` 0.**
Sensitivity is retained — a genuinely stuck loop stops under both rules, and at
the same step. `cumulative` remains registered and selectable
(`repeat_mode: cumulative`) so the change can be reverted from config rather than
by editing code.

### Per-tool failure probes — `verified`

Each registry entry carries its own failing argument as `Tool(fn, probe)`. Both
fields are positionally required, so a tool cannot be *defined* without a probe
(`TypeError` while `agent_runtime` imports) and `_validate_tools()` rejects an
entry that loses its `Tool` shape. The contract test reads probes from the
registry it is checking rather than keeping a second list — and asserts that no
probe table is reintroduced in the test file.

Reproduce: `python3 -m unittest loop_guard_test.FailureProtocolTest`

### The tool failure protocol — `verified`

| field | value |
| --- | --- |
| status | verified |
| constructor | `loop_guard.fail(reason)` — the only way a failure string is built |
| marker | `loop_guard.FAILED_PREFIX` (`[gate-failed]`) |
| legacy tolerance | `LEGACY_FAILURE_PREFIXES` — detection still accepts `[error]` from a ledger or a tool this module does not own |
| probe location | `agent_runtime.Tool(fn, probe)` — the failing argument is part of the tool entry, not a table in the test |
| enforcement | `agent_runtime._validate_tools()` runs at import; both `Tool` fields are positionally required, so a tool defined without a probe raises `TypeError` before it can run, and an entry that loses its `Tool` shape raises `RuntimeError` |
| test-time cheque | `loop_guard_test.py::FailureProtocolTest` reads probes from `agent_runtime.TOOLS` itself (asserting no probe table is reintroduced) and fails if any probe does not produce the canonical prefix, emits a legacy one, or if the literal reappears in `agent_runtime.py` |
| evidence | 22 call sites in `agent_runtime.py` construct failures through `fail()`; 0 `[gate-failed]` literals remain in that module; 5/5 registered tools declare and pass their own probe |

Why the asymmetry: **emission is conservative, detection is liberal.** The
original split — action tools emitting `[gate-failed]`, file tools emitting
`[error]` — is what hid an entire error storm, because a detector that knew one
prefix saw a model looping on missing files as steady progress. Strict emission
removes the cause; liberal detection means a legacy record is still counted.

### Reason codes and their stages

| reason | severity | stage | meaning |
| --- | --- | --- | --- |
| `no_progress` | high | planning | the same call issued on consecutive steps **returning the same result** (or repeated within one response) — a repeat whose result changed is treated as progress |
| `tool_error_storm` | high | tool-use | every tool call failed for several consecutive steps |
| `unknown_tool_storm` | high | tool-use | repeatedly requesting tools that do not exist |
| `context_over_budget` | medium | context | context exceeded its budget and older output was elided |
| `wall_clock_exceeded` | medium | execution | run exceeded its total wall-clock ceiling |
| `no_tool_use` | medium | planning | answered without inspecting anything |
| `budget_exhausted` | medium | planning | ran out of steps while still progressing |
| `model_unavailable` | critical | transport | the model call raised before any step completed |
| `empty_response` | high | transport | the model returned an empty response |
| `plan_invalid` | high | planning | could not produce a plan whose steps and goal are checkable |
| `unverified_completion` | critical | verification | claimed the goal was met and the goal condition did not hold over verified evidence |
| `plan_divergence` | high | planning | repeatedly acted off-plan instead of executing or replanning |
| `replan_storm` | high | planning | kept replacing the plan without making progress |
| `token_budget_exceeded` | medium | execution | exceeded the whole-task token ceiling |
| `no_action` | high | planning | replied without acting and without claiming completion (re-emitting its own plan) |
| `empty_answer` | high | verification | the goal condition held but the completion carried no answer text |
| `unmet_expectation` | medium | verification | a step's declared expectation did not hold — recorded via `note()`, never a stop |
| `transient_empty_response` | medium | transport | a reply came back empty and was retried — recorded via `note()` |

Self-improvement triggers fire on `high`/`critical` only. `medium` faults are
ordinary outcomes (a short budget, a question answerable from memory) and are
recorded in the loop summary without opening a cycle — logging them would drown
the signal.

**`note()` is not `_trip`.** Tolerated findings (an expectation that failed while
the model can still correct course, an off-plan action before the limit) are
recorded and attributed but never change a run's outcome and never open a cycle.
Dropping them would leave an unverified run with no record of why; escalating them
would stop runs that recover.

### Known weaknesses

| weakness | status |
| --- | --- |
| tool failures used two different prefixes | **fixed** — one constructor, contract-tested over every tool |
| the guard could not tell a legitimate repeated call from a stuck one | **fixed for the measured cases** — repeat is judged on consecutive identical steps *and* identical results. Still heuristic in one respect: a run that legitimately repeats the same call **and** gets the same result **and** never does anything else is indistinguishable from a loop, and is stopped. That is the intended behaviour, but it is a judgement, not a proof |
| thresholds are calibrated on 11 scripted pathologies | registered in `loop_guard.json`; a starting point, not a tuned optimum. `repeat_limit` in particular was set to match the *timing* of the rule it replaces rather than measured against outcomes |
| the audit's baseline arm cannot isolate intermediate rules | the `before` column is guard-OFF, so any rule introduced by the first hardening is invisible to it. The `run_repeat_modes` comparison exists for exactly that reason, and each new rule must add its own arm or its cost goes unmeasured |
| emitter defaults to off for library use | by design (see below) — a CLI run must set `LOOP_GUARD_EMIT=1` |
| `_validate_tools()` only runs at import | a probe-less entry introduced by monkeypatching after import is not re-caught by it. Tests and `loop_audit.py` therefore stub by constructing `Tool(fn, real.probe)` rather than assigning a bare function — the shape is kept deliberately, not enforced at that moment |

**Emission is opt-in.** A library call must not write to repository files: running
the test suite appended a fabricated `model_unavailable` record to the real
`SELF_IMPROVEMENT_LOG.md` before this default changed. `LOOP_GUARD_EMIT=1`
(the CLI sets it) enables it; `LOOP_GUARD_LOG` / `LOOP_GUARD_LEDGER` override the
paths, and empty values disable them.

## Goal-directed loop — `verified`

| field | value |
| --- | --- |
| status | verified |
| implementation | `autonomy.py` — a plan whose per-step expectations are declared *before* acting, promotion of only **independently confirmed** output to evidence (A3, `verifier.py`), a completion gate, an attributed-note path, and per-task budgets |
| entry points | `python3 agent_runtime.py --goal "..."` (the verified loop, **now the default**, exits non-zero on an unmet goal); `--reflex` keeps the old unverified loop reachable so it stays measurable; `verify_mode: off` keeps the pre-A3 promotion rule measurable the same way |
| tests | `autonomy_test.py` (101) + `autonomy_suite.py` (20 tasks, 8 floors) + the goal arm of `loop_audit.py` (18 pathologies) + `live_refusal_probe.py` (the refusal path against a real model) — reproduction: `python3 -m unittest autonomy_test`, `python3 autonomy_suite.py`, `python3 loop_audit.py --goals` |

### The gate is the point

`FINAL:` is accepted only when the plan's GOAL-CHECK holds over evidence the loop
collected from tool output. It is never evaluated over the model's prose: a model
that *writes* the expected string has not produced it, which is the difference
between a checker the model can talk past and one it cannot. Two controls bracket
it — an honest scripted model completes, and an adversarial one that fabricates
is refused — because an auditor that passes everything proves nothing.

Weak goal conditions (`ok`, `nonempty`) are **refused at plan time**, not warned
about: a goal gated on one would pass every run ever made. A regex goal that
accepts two maximally dissimilar generic probes is refused for the same reason —
this was added after a live run proposed `regex:.+` and the gate reported
`[verified]` for an answer that was the empty string.

### Measured

| metric | value | how |
| --- | --- | --- |
| reflex loop false successes | **5/5** | same scripted replies, old loop: claimed success in 5/5, collected evidence in 0/5 |
| verified loop false successes | **0/5** | the same five, one refused via the gate and one as `no_action` |
| goal-arm classification | **18/18 correct** | `loop_audit.py` scripted pathologies through the real loop |
| task suite | **20/20, all 8 floors** | `autonomy_suite.py`, pre-registered floors, hermetic fixture |
| false-success rate | **0.000** | floor was `max 0.000` |
| verified completion rate | **1.000** | 9/9 completable tasks ended with the goal verified |
| refusal accuracy | **1.000** | 11/11 tasks a correct loop must refuse |
| budget compliance | **1.000** | no success while over a task's budget |
| determinism | **2 runs, identical verdicts** | the suite re-runs and fails if any verdict differs |
| detection-regression check | **11/11** | the original guard pathologies still classify correctly |

**One false refusal, kept on purpose.** `goal_only_in_unverified_step`: the goal
token *is* in the tool output, but that step's declared expectation failed, so the
output is not promoted to evidence and the run is refused. That is the measured
cost of "only verified output becomes evidence" — a mis-declared expectation can
discard output that did satisfy the goal. It is recorded rather than hidden.

### Goal strength — how a vacuous gate is refused

A goal the gate cannot fail is not a goal. Two rules decide that, and neither is a
calibrated threshold:

1. **A kind that names a needle is discriminating by construction.** `contains:X`
   and `absent:X` reference a string, so evidence can always be found that
   satisfies or defeats them — no probe set can improve on that, and probing them
   would be wrong: `absent:X` accepts every probe lacking X while still being
   falsifiable.
2. **Every other kind is tested against `autonomy.PROBES`** — 11 diverse, non-empty
   strings (single tokens, prose, markdown, JSON, numbered lines, unicode,
   punctuation, a 300-char run). Weakness requires accepting **all** of them, so
   adding a probe can only make the test stricter; it can never mislabel a specific
   goal as weak. Tool output from a successful call is never empty, so a condition
   that accepts all 11 is accepted by essentially any evidence.

`ok` and `nonempty` short-circuit to weak, and so does anything equivalent to them
spelled differently — `lines:1` and `chars:1` are `nonempty` with arithmetic.

### Known weaknesses — do not paper over these

| weakness | status |
| --- | --- |
| a goal could be satisfied by a **vacuum** | **fixed** — `absent:X` holds over an empty string, so a run that promoted no evidence could pass a gate it never fed. A completion now requires non-empty evidence, and the refusal carries `evidence_empty: true` |
| wildcard-goal detection was a two-probe check | **fixed** — replaced with the kind rule plus an 11-probe set. The residual case is a condition that only fails on a *shape* of evidence (`^.*$` rejects multi-line output), which the gate genuinely can fail, so calling it weak would be a false positive |
| a dropped reply killed the run | **fixed** — an empty reply is retried (`empty_response_retries`, default 1) and each attempt is recorded as `transient_empty_response`. An empty *plan* reply consumes a repair attempt instead of ending the run. Persistent empties still end it as `empty_response` |
| budget adequacy was unmeasured | **fixed** — `budget_slack_rate` is a floor: every success must leave at least one step and one token of room, or it has not shown the budget was sufficient, only that it was barely so. The tightest success is reported |
| an empty reply that had **consumed its whole cap** was retried at the same cap | **fixed** — found live. The cascade's default hosted hop is a reasoning model, and one tool-step prompt returned 256/256, 512/512 and 1024/1024 tokens of deliberation with no content, then the correct directive in 12 tokens at 2048: the retry could not have succeeded, and a goal run failed `empty_response` while the model held the answer. A reply that billed its entire cap now retries at `empty_reply_token_ceiling` (2048), the measured value; a reply that billed nothing is still treated as a dropped stream and retried cheaply |
| a corrected plan block was discarded for missing the `REPLAN:` keyword | **fixed** — found live: the model answered a rejection note with the corrected `PLAN:` block the prompt taught it, three times in one run, and each was recorded as `no_action`. A bare plan block is now read as a replacement when it **differs** from the current plan, and an identical one is still `no_action` (an echo is not progress) |
| a supplied `plan` was enforced but never shown to the model | **fixed** — found live: `plan=` parsed and verified a plan that was never placed in the context, so the model was told "Execute step 1" about a plan it had never seen and replied by writing a new one. A supplied plan is echoed as the assistant turn, exactly as a model-written one is |
| the rejection note offered a repair that cannot work | **fixed** — found live: it said "name an expectation … and re-run the step", but a step's expectation comes from the plan, so re-running re-checks the same expectation. Each refusing check now names its own remedy (`REFUSAL_FIXES`) and the note asks for a corrected plan |
| a step's declared expectation could certify nothing | **fixed** — A1 checked the *model's own* expectation, so `nonempty` (true of a failure line) and `regex:.+` promoted steps that proved nothing — **4/4** such claims promoted. Promotion now requires independent confirmation; see **Adversarial verifier (A3)** below |
| `contains:` is a substring test, not entailment | **by design** — it fails a correct paraphrase, the same asymmetry the RAG auditor has. Exact is checkable; approximate is not, and a checker that cannot be wrong cannot be trusted |
| the suite is scripted | **by design** — the hermetic suite measures the **loop's decisions**, not a model's competence. `--live` runs the same 20 goals against the real cascade and is deliberately informational (no floors): a model's skill is not a property the loop controls |
| terse answers are accepted | **by design, and the boundary is explicit** — emptiness is refused, vacuity is refused, but *sufficiency* belongs to the goal condition. The alternative is a model-authored check over the answer's prose, which is the one thing this layer refuses to trust. A goal that needs a substantive answer must say so in `GOAL-CHECK:` |
| the answer is not compared against the goal's wording | not attempted: it would require entailment, and the cheap approximations are exactly the checkers the model can talk past |

## Adversarial verifier (A3) — `verified`

| field | value |
| --- | --- |
| status | verified |
| implementation | `verifier.py` — a step claim must survive **independent** confirmation before its output is promoted to evidence: four named deterministic checks, a level (`invariant` / `observed`) carried into `how_verified`, and an optional second-model ruling in the undecided band only |
| entry points | automatic — every promotion in `autonomy.run_goal_verified` passes through it; a refused claim is recorded as `claim_rejected`, the step does not advance, and the model is told which check refused it. `verify_mode: off` restores the A1 behaviour so the change stays measurable |
| tests | `verifier_test.py` (27) + 20 in `autonomy_test.py` + the verifier arm of `loop_audit.py` (6 claims, 2 controls) + `live_refusal_probe.py` (the refusal path against a real model) |
| reproduction | `python3 verifier_test.py`; `python3 loop_audit.py --verifier`; `python3 live_refusal_probe.py --arm all` (spends real requests) |

### The hole it closes, stated as a measurement

A1 checks the expectation the **model declared** for a step — so the model writes
the test it is graded on. `loop_audit.py --verifier` ran four unsupported claims
through the real loop and **all four were promoted to evidence**:

| claim | why A1 accepted it |
| --- | --- |
| `expect: nonempty` on a listing | non-empty output — and *also* true of the tool's own failure line |
| `expect: regex:.+` | strong by *kind*, satisfied by anything at all |
| `expect: nonempty` on a **failed** `read_file` | the failure string is non-empty, so a failed call became a verified observation |
| a claim that does not reproduce | real once, and never re-observed |

### The checks

Each is named, and the name is carried into `how_verified` — AGENT-INTEGRITY
requires a promotion to name the check that ran, and a claim that cannot name one
is not promoted.

| check | what it refuses |
| --- | --- |
| `expectation_held` | re-derives the expectation from the raw spec and output; the caller's earlier verdict is **not read** |
| `not_failure` | a failed call wearing an expectation that a failure string satisfies |
| `not_vacuous` | an expectation satisfied by the **empty string or this tool's own failure line** — it cannot tell a usable observation from nothing, so it cannot certify one |
| `reproduced` | for a tool in `verifier.DETERMINISTIC_TOOLS` (read-only, local), the claim must hold again on a re-run; a re-run that *changes* but still holds is recorded as `changed`, not refused — the drift loop's `state.json` is exactly that shape |

`not_vacuous` is decidable with no threshold and no probe set: the counterfactuals
are two constants, one of them built by the canonical `loop_guard.fail`.
`absent:X` at step level is refused by it (it holds over an empty output); the
correct form is a positive step expectation and `absent:` as the **goal**, which
the non-empty-evidence rule already covers.

### Measured

| metric | value | how |
| --- | --- | --- |
| lying control | **4/4 detected (rate 1.00)** | the four claims above, identical model replies with the verifier off and on |
| honest control | **0/2 refused (rate 0.00)** | a discriminating claim on a re-runnable tool, and a real claim on a tool that cannot be re-run |
| promotions with nothing independent behind them | **4 → 0** | `loop_audit.py --verifier`, `verify_mode` off vs confirm |
| controls verdict | **`useful`** | `verifier.assess` — A3's exit criterion, and part of `loop_audit.audit_ok` |
| regression | **11/11 detection, 18/18 goal arm, 20/20 suite, 8/8 floors** | unchanged by the promotion gate |

### A verifier that passes everything is treated as no verifier

`verifier.assess()` returns `no_verifier` when nothing was detected — not "a weak
verifier", a pass-through, indistinguishable from having no verifier at all. It
also returns `too_loud` when the detection was bought by refusing honest claims,
because a deny-everything policy is not a verifier either. `loop_audit.audit_ok`
requires `useful`, so a green audit cannot be obtained by confirming everything —
that rule is tested with a zeroed assessment.

### Live coverage — the refusal path, end to end

Every other arm here drives the loop with scripted replies, which is the right way
to measure the loop's decisions but cannot answer the one question the refusal
path leaves open: when the verifier refuses a claim, **what does a real model do
with the message it gets?** `live_refusal_probe.py` runs the production loop over
the real cascade and records every message and reply verbatim
(`live-refusal-transcript.jsonl`, reprintable from `live-refusal-summary.json`
with `--report`, so the record is not re-buyable only by spending requests again).

One session, `qwen/qwen3.8-27b` on Groq (the default `openai/gpt-oss-120b` hop was
measured separately — see the escalation row above):

| arm | refusal reached | refused by | the model's reaction | repaired? | run outcome |
| --- | --- | --- | --- | --- | --- |
| `natural` (model-planned) | **no** | — | — | — | `plan_invalid` |
| `failed_call` (supplied plan) | 3 claims | `not_failure`, `not_failure`, `not_vacuous` | replan | **yes** — replanned onto a call that exists | `replan_storm` |
| `non_discriminating` (supplied plan) | 1 claim | `not_vacuous` | replan | **yes** — `expect:` corrected to `contains:agent_runtime.py — Free Brain Python agent harness` | `empty_answer` (the goal had verified over **3 `invariant` steps**) |

What the live runs established, none of which a stub could have:

* **The refusal reaches a real model and it repairs correctly.** Told that its
  `nonempty` expectation certified nothing, the model replaced it with a specific
  needle, the loop accepted the replacement, re-executed the step, and the goal
  then verified over three independently confirmed steps. The remaining fault was
  its own bare `FINAL:` (`empty_answer`) — a different, already-measured one.
* **A refusal cannot arise from this model naturally.** In the model-planned arm it
  *did* write `expect: nonempty` on its step — the exact A3 pathology — but the plan
  gate refused its weak `GOAL-CHECK: nonempty` first, so the run never reached the
  verifier. Two defences in series, and the earlier one caught it; the seeded arm is
  therefore the only way to measure this path, which is why the probe has one.
* **`not_failure` is reachable and repairable too**: told its failed call's output
  was not an observation, the model replanned onto a call that exists.

### Unseeded execution — the loop completing a task it planned itself

Every refusal arm above supplies a plan, which answers "what does the model do with
a rejection note?" and leaves the prior question open: **can the production loop
complete a real task with no plan supplied, against a real model?** It had never
been measured — the loop's whole verification stack rested on scripted models plus
one seeded probe. `python3 live_refusal_probe.py --arm nameable --runs 3` measures
it (`live-unseeded-transcript.jsonl`, reprintable from `live-unseeded-summary.json`).

The goal names a file and asks for a threshold's value, so the answer is not in the
question and the run has to act:

| run | verified steps | outcome | the answer it gave |
| --- | --- | --- | --- |
| `nameable#1` | 0 | refused (`unverified_completion`) | `0.75` — wrong, asserted from priors |
| `nameable#2` | **1 `invariant`** | **verified goal** | `0.25` — correct, after reading the file |
| `nameable#3` | 0 | refused (`unverified_completion`) | `0.75` — wrong, asserted from priors |

**2 of 4 unseeded runs reached a verified goal** (1/1 in the session's first
post-fix run, then 1/3). Before those fixes the count was 0, and every failure
traced to the loop rather than to the model.

Four defects, none of them visible to a stub:

| defect | the measurement | what it cost |
| --- | --- | --- |
| `regex:"[0-9]+\\.?[0-9]*"` — the quoted, doubled form all four runs wrote — parsed to a pattern demanding a literal backslash | `hit: false` over **724 chars** of `invariant` evidence | a run failed on a goal check the model had got *right* |
| `_stream_stop` cut `FINAL: 0.25` at the decimal point | the gate received `FINAL: 0.` | the correct answer, refused as no answer at all |
| a replan cleared `evidence` but not the evidence counters | `evidence_chars: 0` printed beside `evidence_levels: {invariant: 1}` | a goal that held over confirmed output was refused, and the record contradicted itself |
| `plan_max_tokens: 2048` (registered) against an assertion on the bare `PLAN_MAX_TOKENS` (512) | `test_an_empty_plan_reply_gets_the_same_room` red | a red test in the tree, in a module the regression loop was not running |

**The remaining failure is the model's, and it is named rather than smoothed.** In
two of three runs the model never called the tool: it answered `0.75` from priors —
the real value is `0.25`, and `0.75`/`0.7` is a stable wrong prior for this field
across every run measured — then *wrote out the JSON snippet it believed the file
contained*. The loop refused that simulated output as non-evidence every time and
said so; in `#2` the model acted on the next turn and the run succeeded. The
unseeded rate is therefore a function of how many nudges this model needs before it
stops asserting and starts observing, not of the gate.

### The second model, and why it ships off

The `observed` band is A3's "where not": `verify_model_adjudication: 0` by
default, and a second model may only **promote** `observed → invariant`. It can
never overrule a deterministic refusal, and it is never consulted for one — a
tested property, not a convention. Its three behaviours (YES, NO, unparsed) are
tested with stubbed models; it has **not** been run against a live model, which is
why it is registered off rather than described as a capability.

### Known weaknesses — do not paper over these

| weakness | status |
| --- | --- |
| the honest control could be kept at zero by refusing everything unreproducible | **bracketed** — `honest_unreproducible_tool` must be promoted at `observed` level, and `assess` marks a deny-everything verifier `too_loud` |
| `not_vacuous` refuses `absent:` as a **step** expectation | **by design, with a measured cost** — the suite's `absent_check` task used one and is now a positive step expectation with `absent:` as the goal. Asserting an absence over evidence you actually collected is the discipline; asserting it over nothing is the hole |
| reproduction only covers tools declared deterministic | **declared, not inferred** — `rag` / `drive_sync` / `qih_metric` are confirmed at `observed` and are *labelled* rather than silently counted as confirmed |
| a specific but irrelevant needle still promotes | the check is one-sided on purpose: whether a needle is *relevant to the goal* is the goal condition's job, and `spec_strength` gates that separately. Judging relevance would need entailment, the one thing every checker here refuses to fake |
| the second model is unmeasured live | **registered off** — see above |
| unseeded execution completes 2 of 4 live runs | **measured, not fixed** — the misses are the model declining to call the tool (see above), and every one was refused rather than passed, so the failure mode is a stall and never a false success |
| one re-observation is not proof of stability | a tool could answer honestly twice and differently a third time. The check buys *reproducibility on demand*, which is weaker than determinism and is what the local tools can offer |

## Episodic memory with provenance (A2) — `verified`

| field | value |
| --- | --- |
| status | verified |
| implementation | `memory.py` — an append-only JSONL store of past actions and outcomes at three provenance levels, lexical recall, and a render whose facts/unverified split is structural |
| wired into | `autonomy.run_goal_verified(..., memory_store=...)` — recall read once at plan time into the *system* message (which compaction never touches); each independently confirmed step written back at its verified level. `memory_store=None` is the default, so every pre-A2 caller behaves identically |
| entry point | `python3 agent_runtime.py --goal "<goal>" --memory [PATH]` — no value uses `<residence>/memory.jsonl`; the run reports what it read and wrote on stderr (`[memory] …`), naming `suppressed` so "did not read its history" cannot be confused with "had no history". Without the flag no store is created at all |
| tests | `memory_test.py` (42) + 7 in `autonomy_test.py` + the memory arm of `loop_audit.py` (one task, four cases) — reproduction: `python3 memory_test.py`, `python3 loop_audit.py --memory` |

### The levels are the boundary, not a label

`AGENT-INTEGRITY.md` names them and this module does not invent its own:
`invariant` (may be consumed as a constraint anywhere), `observed` (real tool
output, not cross-checked), `volatile` (model claim, guess, parse or apology).
Two rules are enforced rather than described:

* **Nothing is born invariant.** The level comes from A3's verifier:
  confirmed-and-reproduced → `invariant`; real but not re-observed → `observed`;
  the model's own expectation with the verifier off (`declared`) → `volatile`.
  That last row is the load-bearing one: without it, turning the verifier off
  would be a way to launder a claim into a remembered fact.
* **The facts section is built from `facts()` alone**, and `unverified()` is its
  exact complement, so no argument to `render()` puts a claim above the header.
  `as_fact()` raises `ProvenanceError` on anything non-invariant; `promote()`
  refuses a `volatile` record outright and refuses an unnamed check.

### Measured (the number is the capability)

One task, two runs, four cases — recall is only measurable as "the second run
needs an outcome the first one produced":

| case | history | recall | recalled | facts | named the file | outcome |
| --- | --- | --- | --- | --- | --- | --- |
| `cold` | none (empty store) | on | 0 | 0 | — | refused (`unverified_completion`) |
| `confirmed_off` | 1 confirmed fact | off | 0 | 0 | — | refused (`unverified_completion`) |
| `confirmed_on` | 1 confirmed fact | on | 1 | 1 | `notes.md` | **succeeded** |
| `declared_on` | 1 never-confirmed value | on | 1 | 0 | — | refused (`unverified_completion`) |

```
improvement : recall ON succeeded where OFF failed — same store, same task
false recall: 0   (1 value actually searched for in the facts section; 0 too short to check)
seeding     : verifier off -> stored as `volatile`;  verifier on -> `invariant`
verdict     : useful
```

`confirmed_off` vs `confirmed_on` is the improvement; `declared_on` is the
false-recall control — a store holding **the same value**, seeded with the
verifier off so it was never independently confirmed. Without that case, a memory
that presented every remembered value as a fact would score a perfect improvement
rate. `memory_assessment` reports recall that changes no outcome as `no_memory`
and any leak as `unsafe`, and `audit_ok` requires `useful` — the same meta-rule as
A3, so a green audit cannot be bought by remembering everything.

### The limit of that measurement

The consumer in the arm is a deterministic stand-in: it can name the file only if
a `*.md` fact is above the unverified header. So the arm measures the **channel**
— that a confirmed fact reaches the planner, labelled, and that an unconfirmed
value is unusable — and *not* a real model's willingness to use what it is given.
Driving the refusal path against a real model is `live_refusal_probe.py`'s job,
and it does not yet exercise recall (it runs the loop with no store attached).

### A goal cannot be met out of memory

`recall_gap` names a distinct fault: the goal condition *would* hold over recalled
facts alone. That is refused as `unverified_completion` with `recall_gap: true`
rather than promoted to evidence — a fact confirmed in an earlier run is a hint
about where to look, not an observation of what is there now. Tested in both
directions: `test_memory_never_satisfies_the_goal_gate`, plus the control where
the recalled fact does not hold the answer (so the diagnosis cannot be a
constant).

### Known weaknesses — do not paper over these

| weakness | status |
| --- | --- |
| recall is lexical | deliberate, and the same call the RAG pipeline made: the binding constraint measured for retrieval was verification, not ranking. `AUTONOMY-UPGRADE.md` §6 says not to add an embedding dependency without a measurement that says lexical is what is failing — that measurement does not exist yet |
| within a run, memory is the evidence list | the store is read **once**, at plan time. A run cannot recall what it did two steps ago through this path, and mixing the two would let a run treat its own fresh output as history |
| a level is only as good as the verifier behind it | an `invariant` rests on A3's checks, including the residual named there (a specific but irrelevant needle can still promote); an un-reproducible tool tops out at `observed` |
| records are truncated to `recall_value_chars` | a recalled value is a hint to re-observe, but a long output recalled as a hint can be a misleading fragment |
| no decay and no size cap | the store grows append-only, one record per confirmed step. At present volumes this is not a problem; nothing measures whether it becomes one |

## Evidence hygiene — `verified`

| field | value |
| --- | --- |
| status | verified |
| implementation | `test_support.py` (a suite's *default* residence is never the shipped one) + `evidence_hygiene.py` (report, or quarantine rows positively identified as stubs) |
| tests | `ReflexEvidenceGateTest` (3: a library run writes nothing; a recorded run still writes; and a **source contract** walking `run_goal` keeps every `_emit_step` call under an `emit` guard, so a new call site fails at definition time) + the before/after row count run as part of every full-suite verification |

### The reflex loop was the one ungated writer

Found by running the full suite with a before/after checksum over the published
records while verifying A2: the Q1 evidence log grew by **5 rows** — all
stub-provider rows with a plausible token rate and nothing visibly wrong about
them, which is the signature of the leak `test_support.py` was built to stop. So
it had a second source. Bisected to `autonomy_test.AuditInstrumentTest`: its
reflex arm runs the unverified loop five times, and `agent_runtime.run_goal`
called `_emit_step` **unconditionally** — the one path in the runtime whose
evidence writes did not follow the guard's emit flag (`autonomy._call` is gated;
`_emit_loop_diagnosis` only prints). That module read as safe because its guards
are built with `emit=False`, which is exactly why the fix could not be a per-suite
patch.

Cumulative damage in the published log, measured: **7,069 artifact rows against a
steady 374 real measurements (95%)**. Both fixes are in because they fail
differently — the write is now gated on `guard.emit` (the source), and
`autonomy_test.py` isolates its residence like the other five suites (the belt).
The gating had to be done twice: the first pass guarded the two call sites that
were obvious and left the guard-stop path writing, which the audit then reported
as 8 more rows on its next run. A hand-fix cannot be trusted to have found every
site, so the third test is a source contract over `run_goal` rather than another
instance fix — verified in the failing direction, not just the passing one. The CLI paths keep
their `setdefault("LOOP_GUARD_EMIT", "1")`, so a recorded run still records;
that is asserted in both directions, so a fix that simply stopped recording real
runs could not pass. The 7,033 rows are **kept**, not deleted, in
`freebrain-residence/evidence/test-artifacts.jsonl` — they are the record of the
bug. The published log now holds 374 rows, **100% measurements**.

## Brain cascade — `verified`

| field | value |
| --- | --- |
| status | verified |
| implementation | `brain_cascade.py` — local-first, then free OpenAI-compatible providers; per-key rotation, failure classes, cooldowns, retired-model re-discovery |
| tests | `brain_cascade_test.py` (53) |

### Failure classification read numbers as substrings

`classify` mapped a provider error to a cooldown class with a bare substring test
for the numeric codes. So the **port** in a dead-local URL decided the class:
`local unreachable at http://127.0.0.1:40233/v1 (Connection refused)` contains
`402`, so a stopped server was classified as a paid wall — benched for the payment
cooldown and reported to the operator as a billing problem. Found by a flaky test
(which picks a closed port at random and hit one containing `402` roughly **1 run
in 17**), then reproduced deliberately.

The fix is two rules, because either alone is insufficient: URL-shaped text is
stripped before scanning (a port can be *exactly* `402`), and what remains must
match a whole token (so `40233` is not `402`). Checked as a property, not against
the one string that failed: **all 65,535 ports** now classify as `server`, and the
transport's real formats (`HTTP 402`, `"status":402`, `code=403`, `error code:
1010`) still classify correctly. Residual, named in the code: a bare number equal
to a code outside any URL is still read as that code — bounded to five exact
three-digit values, and the alternative (requiring an `HTTP ` prefix) would lose
`"status":402`, which is how two live provider walls actually presented.

## QIH metrics — `verified`

| field | value |
| --- | --- |
| status | verified |
| implementation | `qih_metrics.py` — Born rule, d_ij, C_MT, phase-clock; machine-computed ledger records |
| tests | `qih_metrics_test.py` (21) |

---

## Verification surface over MCP — `verified`

| field | value |
| --- | --- |
| status | verified |
| implementation | `builderbro_mcp.py` — a stdio MCP server exposing the verification stack as five tools: `verify_claim` (A3), `memory_recall` / `memory_record` (A2), `rag_ask` (the `builderbro-rag` skill), `evidence_audit` (`evidence_hygiene`). JSON-RPC 2.0, newline-delimited, stdlib only, no network of its own |
| entry points | `mcp.json` registers it with Freebuff, which loads a repository's `.agents` files and `mcp.json` (`--trust-agents`) and namespaces loaded tools `builderbro__<tool>`. Also a shell CLI: `--tools`, `--call NAME --json '{...}'`, or serve on stdio |
| tests | `builderbro_mcp_test.py` (45), one of which spawns the server exactly as `mcp.json` names it and completes a live `initialize` handshake — so the registry file cannot rot into something that only looks correct |
| reproduction | `python3 builderbro_mcp_test.py`; `python3 builderbro_mcp.py --tools`; `python3 builderbro_mcp.py --call verify_claim --json '{"tool":"read_file","arg":"memory.py","output":"<file>","expect":"contains:class MemoryStore"}'` |

### Why it exists — the planner was the weak link, not the checking

BuilderBro was built as a *model-driven loop*: a hosted chat model planned steps
and the loop checked them. The unseeded live measurement says which half was
failing: across the recorded runs the hosted hop **answered from priors instead of
acting** — asserting `0.75` where the file said `0.25`, then writing out the JSON
snippet it believed the file contained — and in the majority of attempts never
called a tool at all (`live-unseeded-summary.json`).

So the split now is: **an agent that already acts does the planning and the work;
BuilderBro does the verification.** Freebuff is that agent (native tool use, in
this repository, able to read and run), and this server is the seam. It targets
the failure that was measured rather than the one that was assumed — the "model
writes the file it imagines" class of defect disappears when the actor reads the
file, because the actor is not answering from priors.

The MCP server is **not** a second implementation of anything:

| rule | where it actually lives |
| --- | --- |
| the expectation grammar (`contains:` / `regex:` / `lines:` …) | `autonomy.parse_spec` + `autonomy.verify` |
| promotion to `invariant` / `observed`, and every named check | `verifier.confirm` |
| what may be a fact, and when a claim may not be promoted | `memory.facts` / `memory.promote` |
| what counts as a stub row in the evidence log | `evidence_hygiene.classify` |
| retrieval, refusal and citation audited | the `builderbro-rag` skill's own CLI |

### What a caller cannot do through it

- **Cannot widen the promotion policy.** `invariant` requires a re-observation
  *and* the tool must be in `verifier.DETERMINISTIC_TOOLS`. No argument adds a
  tool to that set: `grep` with a matching `second_output` still confirms at
  `observed` (asserted in `builderbro_mcp_test`).
- **Cannot launder a claim into a fact.** `memory_record` writes at
  `memory.level_for(evidence_level)` and refuses an `invariant` write that names
  no independent check — `memory.promote`'s own rule, enforced at the seam. An
  unknown provenance falls *down* to `volatile`, never up to `observed`.
- **Cannot have a refusal reported as a malfunction.** `verify_claim`'s `ok` is
  the verifier's own vocabulary ("was this claim confirmed"), so a refusal is
  `ok: false` there. At the transport level a refusal is a *working* tool, so
  `isError` stays false — otherwise a caller would retry a check that had already
  ruled. Both directions are asserted.

### The one thing a caller must supply honestly

Level `invariant` needs a re-observation and this server has no tools of its own,
so it cannot perform one. A caller may pass `second_output`: a sample it obtained
itself. The server **does** test that against the same expectation — the check is
real — but it cannot test the sample's *independence*, so every verdict that used
one carries `"independence": "caller-attested"`. An agent that re-reads the file
is doing the honest thing; one that echoes `output` back defeats the check, and
the response labels that rather than hiding it.

### Measured

A client was written against the exact contract `mcp.json` declares — spawn
`command` + `args` with inherited cwd and no `cwd` argument, newline-delimited
framing — then a full session was driven through it:

| step | result |
| --- | --- |
| `initialize` | `serverInfo.name: builderbro`, protocol echoed |
| `tools/list` | all 5 tools, each schema valid JSON Schema |
| `verify_claim` over a real file (`memory.py`) | `observed`; with `second_output` → `invariant`, `caller-attested` |
| `verify_claim` over empty output | `refused_by: expectation_held`, `isError: false` |
| `memory_record` demanding `invariant` with no check | refused, `error: provenance`, `isError: true`, **nothing written** |
| `memory_record` naming its check | written at `invariant` |
| `memory_recall` | `facts: ['read_file']`, `unverified: []`, 0 audit violations |
| `evidence_audit` on the real Q1 log | 1780 rows, **1780 measurements, 0 artifacts** |
| `rag_ask` (offline extractive) | answered with citations `[1, 2, 5]`, 0.79s |

### Known weaknesses — do not paper over these

- **The re-observation is caller-attested, and cannot be otherwise here.** The
  channel gives a caller that wants to lie exactly one place to do it. What is
  prevented is the *accidental* case (an expectation that does not hold, or a
  sample that does not reproduce) and the *unlabelled* case.
- **Tested against a client written here, not against Freebuff itself.** The
  registry's shape (`mcpServers[name].command` / `.args` / `.env`, tools
  namespaced `server__tool`) was read out of the installed Freebuff binary's own
  loader, and the server was then driven over exactly that contract. That is
  strong evidence and it is not the same as a real session loading it — that
  check needs a Freebuff session started in this repository.
- **A relative path in `mcp.json`, resolved against the client's cwd.** The
  loader spawns with `{env, stdio}` and no `cwd`, so `python3 builderbro_mcp.py`
  resolves relative to wherever Freebuff was started. Launch it in the repo root,
  or the failure is a warning log and no tools for that step.
- **This changes no ceiling.** The tools are read-only plus the memory store — no
  execution, no file writes, no sandbox. Self-building is still impossible, and
  this surface does not make it possible; it makes the *verification* usable by
  something that can act.
- **`rag_ask` shells out to the skill**, so it pays process start plus index load
  (~4s measured, ~0.8s inside an already-warm call). It is deliberately not
  inlined, so the skill keeps exactly one implementation.

---

## Test suites

| suite | tests | result |
| --- | --- | --- |
| `rag_test.py` | 55 | OK |
| `agent_runtime_test.py` | 44 | OK |
| `brain_cascade_test.py` | 53 | OK |
| `drift_loop_test.py` | 40 | OK |
| `qih_metrics_test.py` | 21 | OK |
| `loop_guard_test.py` | 56 | OK |
| `autonomy_test.py` | 126 | OK |
| `memory_test.py` | 42 | OK |
| `verifier_test.py` | 27 | OK |
| `live_refusal_probe_test.py` | 8 | OK |
| `builderbro_mcp_test.py` | 45 | OK |
| **total** | **517** | **all passing** |

Counts are per module, each run on its own (`python3 <module>.py`); the combined
`python3 -m unittest discover -p "*_test.py"` runs the same set. Numbers here are
measured, and were stale once already — a count in a document is a claim like any
other.

These instruments sit beside the tests and are not part of that count:

| instrument | what it measures |
| --- | --- |
| `loop_audit.py` | A/B on 11 detection pathologies (guard OFF vs ON) + 18 goal pathologies + the reflex-vs-verified false-success comparison + `--verifier` (6 claims, an honest and a lying control) + `--memory` (one two-run task, four cases: no history / a confirmed fact with recall off and on / the same value never confirmed) + per-rule arms so a rule change is revertible from config. Ground truth is computed from **each pathology's own goal condition** read out of its plan — the input, never the loop's report. `audit_ok` requires the verifier's and the memory arm's verdicts to be `useful`, so a verifier that detects nothing, or recall that changes no outcome, turns the audit red |
| `autonomy_suite.py` | 20 scored tasks with per-task budgets and 8 pre-registered floors; `--repeat N` requires identical verdicts across runs; `--register` / `--register-live` / `--register-immaculate` / `--register-verifier` / `--register-memory` write closed self-building cycles, each with its measured evidence block |
| `evidence_hygiene.py` | whether the published Q1 evidence log is measurements or test artifacts — classifying a row only on **positively identified stub markers**, never on "an endpoint I don't recognise", so a new real backend is not quarantined by a tool that has not heard of it |
| `verifier_test.py` | the verifier's own two controls, plus the rule that a pass-through verifier is reported `no_verifier` rather than as a pass |
| `live_refusal_probe.py` | the refusal path and **unseeded execution** against a **real model** over the real cascade: it drives `autonomy.run_goal_verified` exactly as production does, records the transcript verbatim, and classifies the model's reply to the rejection note (including `replan_unlabelled`, the repair the loop used to discard). `--report <summary>` reprints and `--register --cycle {refusal,unseeded}` closes either cycle **without spending requests again**, which is why a saved summary exists. The only instrument here that spends requests, so nothing else ever runs it |
