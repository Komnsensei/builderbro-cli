"""autonomy.py — the goal-directed layer for BuilderBro.

The reflex loop (`agent_runtime.run_goal`) asks a model for one tool call and
repeats until the model says `FINAL`. Its success signal is therefore the model's
own assertion, and `loop_audit.py` measured exactly what that costs: on a model
that claims completion without acting, the loop returns the claim *as the
result*. The loop could be instrumented but not goal-directed — it could not tell
"done" from "gave up", and every later capability (memory, tool synthesis,
policy learning) inherits that blindness.

This module adds the three things that make a loop goal-directed:

1. a **plan** — ordered steps whose observable expectation is declared *before*
   acting, plus one goal condition for the whole run;
2. **per-step verification** — the declared expectation is checked against the
   real tool output, and only output that verifies is promoted to evidence;
3. an **adversarial verifier** (`verifier.py`, A3) — because step 2 checks the
   *model's own* expectation, a claim must also survive independent confirmation
   before it counts;
4. a **completion gate** — `FINAL` is accepted only when the goal condition holds
   over verified evidence.

Design notes that are load-bearing, not decorative:

* **The gate reads evidence, never the model's answer.** A model that writes the
  expected string into its reply has not produced it. This is the difference
  between a checker the model can talk past and one it cannot.
* **Only verified output becomes evidence.** When a step's expectation fails, its
  output is not promoted, so a failed step cannot contribute the very string that
  would satisfy the goal.
* **A weak goal condition is refused, not warned about.** `ok` and `nonempty`
  are accepted for a *step* expectation (they are real checks), but a run whose
  goal gate is `nonempty` would pass everything ever run. `parse_plan` rejects it
  as `plan_invalid`: a goal you cannot check is not a goal. The price is real and
  worth stating — this layer can only run goals that come with a checkable
  condition, and anything else fails fast instead of passing quietly.
* **Deviation is attributed, not censored.** The model may act off-plan; the
  harness counts it, records it, and stops only past `plan_divergence_limit`.
  A harness that silently refused would hide the fault the audit needs to see.

Fault detection is *not* duplicated here: the existing `loop_guard.LoopGuard` is
given every step exactly as before, so all nine of its reason codes apply to a
planned run too. Detection (guard) ⊂ verification (this module).
"""

from __future__ import annotations

import collections
import json
import os
import re
import sys
import uuid

import agent_runtime as rt
import loop_guard
import verifier

# ── The expectation grammar ───────────────────────────────────────────────────
#
# Deliberately small and *decidable*. `contains:` is a substring test, not
# entailment: it can be checked exactly, and it will fail a correct paraphrase,
# which is the honest limitation of every checker in this repo (the RAG auditor
# has the same asymmetry — fabrication detection is exact, entailment is
# approximate). A checker that cannot be wrong cannot be trusted.

SPEC_KINDS = ("ok", "nonempty", "contains", "absent", "regex", "lines", "chars")

# Checks that are real but cannot carry a goal: they are satisfied by almost any
# output, so a run gated on one would pass regardless of whether it achieved
# anything. Allowed per step, refused as a goal condition.
WEAK_KINDS = ("ok", "nonempty")

_NUMERIC_KINDS = ("lines", "chars")
_TEXT_KINDS = ("contains", "absent", "regex")


class SpecError(ValueError):
    """A malformed expectation. Raised, never guessed at."""


def parse_spec(text):
    """`'contains:builderbro'` -> `('contains', 'builderbro')`.

    Uniform `kind:arg` form. Bare `ok` / `nonempty` take no argument. Returns
    None for anything unrecognised so callers can distinguish "no check" from
    "bad check" without exceptions in the hot path.
    """
    raw = (text or "").strip().strip("`").strip()
    if not raw:
        return None
    low = raw.lower()
    if low in WEAK_KINDS:
        return (low, "")
    kind, sep, arg = raw.partition(":")
    if not sep:
        return None
    kind = kind.strip().lower()
    arg = arg.strip().strip("`").strip()
    if kind not in SPEC_KINDS or not arg:
        return None
    if kind in _NUMERIC_KINDS:
        try:
            n = int(arg)
        except ValueError:
            return None
        if n <= 0:
            return None
        return (kind, n)
    return (kind, arg)


def format_spec(spec):
    if spec is None:
        return "(none)"
    kind, arg = spec
    return kind if kind in WEAK_KINDS else "%s:%s" % (kind, arg)


# ── Goal strength ─────────────────────────────────────────────────────────────
#
# A goal condition the gate cannot fail is not a goal. Two rules decide that, and
# neither is a tuned threshold:
#
# 1. **A kind that names a needle is discriminating by construction.** `contains:X`
#    and `absent:X` reference a specific string, so evidence can always be found
#    that satisfies or defeats them. No probe set is needed to know that, and
#    probing them would be wrong: `absent:X` accepts every probe lacking X while
#    still being a perfectly falsifiable goal.
# 2. **Every other kind is tested against a diverse probe set.** Evidence from a
#    successful tool call is never empty, so a condition that accepts *all* of the
#    probes below — none of which is empty — is accepted by essentially any real
#    evidence. This is what caught `regex:.+`, proposed by a hosted model in a live
#    run: the gate printed `[verified]` for a goal whose answer was the empty
#    string.
#
# `ok` and `nonempty` short-circuit to weak before either rule (WEAK_KINDS): they
# are real checks, satisfied by almost any output.
#
# The probe set *is* the instrument, so it is written out and its coverage is
# tested: single tokens, prose, markdown, JSON, numbered lines, unicode,
# punctuation, and a long run. Weakness requires accepting all of them, so adding
# a probe can only ever make the test stricter — it can never mislabel a specific
# goal as weak — which is why this needs no calibrated count.
PROBES = (
    "1",
    "0",
    "true",
    "no",
    "x",
    "an unrelated sentence about nothing in particular",
    "# heading\n\n- a bullet\n- another\n",
    '{"key": "value", "n": 3}\n',
    "line one\nline two\nline three\nline four\n",
    "αβγ — unicode, punctuation: !?;:",
    "a" * 300,
)

DISCRIMINATING_KINDS = ("contains", "absent")


def _accepts_every_probe(spec):
    for probe in PROBES:
        ok, _detail = verify(spec, probe)
        if not ok:
            return False
    return True


def spec_strength(spec):
    """"weak" if the gate could not fail on ordinary evidence, else "strong"."""
    if spec is None:
        return "invalid"
    if spec[0] in WEAK_KINDS:
        return "weak"
    if spec[0] in DISCRIMINATING_KINDS:
        return "strong"
    return "weak" if _accepts_every_probe(spec) else "strong"


def verify(spec, text):
    """Check one expectation against one output.

    Returns `(ok, detail)`. `detail` is always populated — a failure has to say
    what was looked for and what was seen, or the diagnosis is unusable.
    """
    if spec is None:
        return False, {"error": "no expectation given"}
    kind, arg = spec
    body = text or ""
    if kind == "ok":
        ok = not loop_guard.result_failed(body)
        detail = {"kind": kind, "failed_marker": not ok}
    elif kind == "nonempty":
        ok = bool(body.strip())
        detail = {"kind": kind, "chars": len(body)}
    elif kind == "contains":
        ok = arg.lower() in body.lower()
        detail = {"kind": kind, "needle": arg, "hit": ok}
    elif kind == "absent":
        ok = arg.lower() not in body.lower()
        detail = {"kind": kind, "needle": arg, "present": not ok}
    elif kind == "regex":
        try:
            ok = re.search(arg, body, re.IGNORECASE) is not None
            detail = {"kind": kind, "pattern": arg, "hit": ok}
        except re.error as e:
            return False, {"kind": kind, "pattern": arg, "error": str(e)}
    elif kind == "lines":
        n = len([ln for ln in body.splitlines() if ln.strip()])
        ok = n >= arg
        detail = {"kind": kind, "need": arg, "observed": n}
    elif kind == "chars":
        ok = len(body) >= arg
        detail = {"kind": kind, "need": arg, "observed": len(body)}
    else:
        return False, {"error": "unknown check kind %r" % kind}
    detail["spec"] = format_spec(spec)
    return ok, detail


# ── Plans ─────────────────────────────────────────────────────────────────────

PlanStep = collections.namedtuple("PlanStep", "tool arg expect")
Plan = collections.namedtuple("Plan", "steps goal_check raw")


class PlanError(ValueError):
    """A plan that cannot be executed or cannot be checked. Every message ends up
    in the `plan_invalid` diagnosis detail, so it has to name the defect."""


PLAN_HEADER_RE = re.compile(r"^\s*(?:PLAN|STEPS?)\s*:?\s*$", re.IGNORECASE)
ITEM_RE = re.compile(r"^\s*(?:\d+\s*[.)]|[-*])\s*(.+?)\s*$")
TOOL_FIELD_RE = re.compile(r"^\s*(?:tool|action|call|step)\s*:\s*(.+?)\s*$", re.IGNORECASE)
EXPECT_FIELD_RE = re.compile(
    r"^\s*(?:expect|expects|expectation|check|observable)\s*:\s*(.+?)\s*$", re.IGNORECASE)
GOAL_FIELD_RE = re.compile(
    r"^\s*(?:goal[ _-]?check|goalcheck|verify[ _-]?goal|goal)\s*:\s*(.+?)\s*$", re.IGNORECASE)

MAX_PLAN_STEPS = 8

# A plan block's second signature, as a *field* rather than a mention: the parser
# accepts `GOAL-CHECK:` after any tolerance, so requiring the colon keeps an
# echoed instruction ("GOAL-CHECK `contains:x` already holds") from reading as a
# plan the model did not write.
GOAL_FIELD_LINE_RE = re.compile(r"^\s*GOAL-CHECK\s*:", re.IGNORECASE)


def is_plan_reply(text):
    """True when a reply is a plan block — with or without the `REPLAN:` keyword.

    The system prompt teaches the model the `PLAN:` block; `REPLAN:` is mentioned
    once, in the sentence about replacing a plan. A real model answering a
    rejection note sent the corrected block and no keyword — three consecutive
    times, in the live probe — and the loop recorded each as `no_action`,
    discarding three correct repairs before failing the run. A plan block cannot
    be executed as a step, so in the middle of a run it can only mean "replace the
    plan"; reading it as silence points the fix at the model instead of the loop.

    Shape, not validity: a plan-shaped reply that does not parse goes to the
    plan-repair prompt, which asks for the thing that is actually missing.
    """
    lines = (text or "").splitlines()
    return (any(PLAN_HEADER_RE.match(ln) for ln in lines)
            or any(GOAL_FIELD_LINE_RE.match(ln) for ln in lines))


def parse_plan(text):
    """Parse a plan block; raise PlanError with the specific defect otherwise.

    Requires the `PLAN:` header. Tolerance is bought elsewhere — the loop gives a
    malformed plan one repair round — because a parser that accepts prose
    containing the word `tool:` will happily invent a plan the model never wrote,
    and then verify the run against it.
    """
    lines = [ln for ln in (text or "").splitlines()]
    if not any(PLAN_HEADER_RE.match(ln) for ln in lines):
        raise PlanError("no `PLAN:` header — reply with the plan block only")
    target = text.split("PLAN:", 1)[1] if "PLAN:" in text else text

    steps = []
    goal_raw = None
    current = None
    for line in target.splitlines():
        body = line
        m = ITEM_RE.match(line)
        if m:
            body = m.group(1)
        gm = GOAL_FIELD_RE.match(body)
        if gm:
            goal_raw = gm.group(1)
            continue
        tm = TOOL_FIELD_RE.match(body)
        if tm:
            call = tm.group(1)
            tool, _, arg = call.partition(" ")
            current = {"tool": tool.strip(), "arg": arg.strip(), "expect": None}
            steps.append(current)
            continue
        em = EXPECT_FIELD_RE.match(body)
        if em and current is not None:
            current["expect"] = em.group(1)
            continue

    if not steps:
        raise PlanError("no steps found — each step needs a `tool: <name> <arg>` line")
    if len(steps) > MAX_PLAN_STEPS:
        raise PlanError("plan has %d steps, limit is %d" % (len(steps), MAX_PLAN_STEPS))
    if goal_raw is None:
        raise PlanError("no `GOAL-CHECK:` line — the goal condition is what the "
                        "completion gate evaluates")

    goal = parse_spec(goal_raw)
    if goal is None:
        raise PlanError("unparseable GOAL-CHECK %r (grammar: %s)"
                        % (goal_raw, " | ".join(SPEC_KINDS)))
    if spec_strength(goal) == "weak":
        # Refused on purpose — see the module docstring. This is the check that
        # makes the gate meaningful, so it cannot be downgraded by the model.
        why = ("`%s` is satisfied by almost any output" % format_spec(goal)
               if goal[0] in WEAK_KINDS else
               "`%s` is accepted by every probe in the strength set, so the gate "
               "could not fail on ordinary evidence" % format_spec(goal))
        raise PlanError(
            "GOAL-CHECK %r is too weak to gate a run: %s, so the completion gate "
            "would pass everything" % (format_spec(goal), why))

    built = []
    for i, step in enumerate(steps, 1):
        if step["tool"] not in rt.TOOLS:
            raise PlanError("step %d uses unknown tool %r (available: %s)"
                            % (i, step["tool"], ", ".join(sorted(rt.TOOLS))))
        if not step["arg"]:
            raise PlanError("step %d (%s) has no argument" % (i, step["tool"]))
        expect = parse_spec(step["expect"])
        if expect is None:
            raise PlanError("step %d has no usable `expect:` check (grammar: %s)"
                            % (i, " | ".join(SPEC_KINDS)))
        # A weak step expectation is *allowed* here and refused later, at
        # promotion (verifier.py): it is still a real check the model may want
        # (`expect: ok` as a sanity step), it just cannot certify output as
        # evidence. Rejecting it at plan time would remove a legitimate plan shape
        # and hide the finding the model needs to hear — why the step did not
        # count — behind a plan-invalid error.
        built.append(PlanStep(step["tool"], step["arg"], expect))
    return Plan(tuple(built), goal, text)


# ── Prompts ───────────────────────────────────────────────────────────────────

def plan_system_prompt(persona="BuilderBro"):
    return (
        "You are %s, a local autonomous agent. You have these read-only tools: %s. "
        "You never answer from memory: every claim must come from a tool result.\n\n"
        "FIRST, and before using any tool, reply with ONLY this block:\n"
        "PLAN:\n"
        "1. tool: <name> <argument>\n"
        "   expect: <check>\n"
        "2. tool: <name> <argument>\n"
        "   expect: <check>\n"
        "GOAL-CHECK: <check>\n\n"
        "A <check> is exactly one of: ok | nonempty | contains:<text> | "
        "absent:<text> | regex:<pattern> | lines:<n> | chars:<n>\n"
        "A step's `expect` must be something that will appear in that step's tool "
        "output if and only if the step worked. Only output whose expectation "
        "is independently confirmed becomes evidence, and a check that a failed "
        "call would also satisfy cannot confirm anything — so name a specific "
        "`contains:<text>` you expect to see, not `ok`, `nonempty`, `absent:`, a "
        "wildcard regex, `lines:1` or a tiny `chars:`. GOAL-CHECK must be a strong check "
        "over the collected tool output that will hold only when the goal is truly "
        "met — `ok` and `nonempty` are refused there.\n\n"
        "THEN act one step at a time: reply with ONLY <<<TOOL:name argument>>> and "
        "nothing else. If a step's expectation did not hold, or the plan cannot "
        "reach the goal, reply with REPLAN: followed by a fresh PLAN block.\n"
        "When the goal condition holds, reply with FINAL: <answer>. Your FINAL "
        "answer is accepted only if GOAL-CHECK holds over the tool output you "
        "actually collected — so collect the evidence before you claim the goal."
        % (persona, ", ".join(sorted(rt.TOOLS)))
    )


REPAIR_NOTE = (
    "That reply was not a usable plan. Reply with ONLY the PLAN block, and use a "
    "strong GOAL-CHECK (contains:/absent:/regex:/lines:/chars:). Nothing else."
)


def _nudge(plan, index):
    """The one thing said to a model that neither acted nor claimed completion.

    This case is real, not hypothetical: the first live run of the verified loop
    against a hosted model re-emitted the plan block instead of executing step 1.
    Naming what is expected — act, finish, or replan — is cheap, and it is the
    difference between a loop that works on a real model and one that only works
    against scripted replies.
    """
    target = _step_directive(plan, index)
    if target is None:
        return ("Your last reply was neither a tool call nor a completion claim, and "
                "the plan has no steps left. Reply with ONLY FINAL: <answer> if "
                "GOAL-CHECK `%s` holds over the evidence you collected, or REPLAN: "
                "with a new plan." % format_spec(plan.goal_check))
    return ("Your last reply was neither a tool call nor a completion claim. Reply "
            "with ONLY <<<TOOL:%s>>> — or FINAL: <answer> if GOAL-CHECK `%s` already "
            "holds over your collected evidence, or REPLAN: with a new plan."
            % (target, format_spec(plan.goal_check)))


def _empty_note(plan, index):
    """Said after a reply that came back with no text at all.

    Distinct from `_nudge`: the model may not know its reply was dropped (a
    provider can return an empty completion for a stream that never arrived), so
    it is told that, and told exactly what to send.
    """
    target = _step_directive(plan, index)
    if target is None:
        return ("Your last reply came back empty (no text at all) — that is usually "
                "transient. Reply with ONLY FINAL: <answer> if GOAL-CHECK `%s` holds "
                "over the evidence you collected, or REPLAN: with a new plan."
                % format_spec(plan.goal_check))
    return ("Your last reply came back empty (no text at all) — that is usually "
            "transient. Reply with ONLY <<<TOOL:%s>>> and nothing else." % target)

PLAN_SYSTEM = None  # set per run (persona differs)


def _step_directive(plan, index):
    if index >= len(plan.steps):
        return None
    s = plan.steps[index]
    return "%s %s" % (s.tool, s.arg)


def _status_note(plan, index, verified, detail):
    """The one thing the model is told after each step. Kept explicit because the
    gate is unforgiving: if an expectation failed, the model must know which."""
    if verified:
        return ("verified: step %d/%d expectation `%s` held."
                % (index, len(plan.steps), detail.get("spec")))
    return ("NOT VERIFIED: planned expectation `%s` did not hold in that output "
            "(%s). Either take a corrective step or reply REPLAN:."
            % (detail.get("spec"), json.dumps(detail, sort_keys=True)))


# Per-check remedies. This started as one generic instruction for four different
# findings, which the live probe showed cannot work: a `not_vacuous` refusal is not
# repaired by re-running the step (the plan holds the expectation), so the model
# that followed the old note literally would re-run and be refused again. Each
# check now names its own remedy.
REFUSAL_FIXES = {
    "not_vacuous": ("name an expectation a failed call could not also satisfy — a "
                    "specific `contains:<text>` — for that step"),
    "not_failure": ("that call failed, so its output is not an observation at all: "
                    "point the step at something that exists"),
    "reproduced": ("the claim did not reproduce on re-observation: choose a step "
                   "whose output will hold still long enough to confirm"),
    "model_adjudication": ("the second model judged the raw output does not support "
                           "the claim: replace the step with one whose output does"),
}


def _rejection_note(plan, index, verdict):
    """Spoken when a step's expectation *held* but independent confirmation
    refused it. Distinct from `_status_note`'s failure case on purpose: the two
    are different findings with different fixes, and reporting a vacuous
    expectation as a failed step would point the model at the wrong one.

    The remedy is a **plan** change and the note says so: a step's expectation
    comes from the plan, so re-running the step unchanged re-checks the same
    expectation. The earlier wording offered "re-run the step" as the first move,
    which is not actionable.
    """
    failing = next((c for c in verdict.checks if c.get("ok") is False), {})
    return ("NOT CONFIRMED: step %d/%d ran and `%s` held in its output, but the "
            "independent check `%s` refused to promote it as evidence (%s). That "
            "output does not count toward the goal. Fix: %s. The step's "
            "expectation is fixed by the plan, so reply with a corrected plan "
            "(REPLAN:, or a PLAN block) that carries the fix."
            % (index, len(plan.steps),
               format_spec(plan.steps[index - 1].expect if index >= 1 and
                           index <= len(plan.steps) else None),
               verdict.refused_by,
               json.dumps(failing.get("detail") or {}, sort_keys=True),
               REFUSAL_FIXES.get(verdict.refused_by, "correct that step")))


def _same_plan(a, b):
    """True when two parsed plans are the same plan.

    What tells a replacement from an echo. A model that re-emits the plan it is
    already following has not replanned, and the reason code for that is
    `no_action` ("the model is re-emitting its plan instead of executing a step") —
    the distinction is load-bearing, because an echo counted as progress turns a
    stuck run into a `replan_storm` and blames the wrong lever.

    Compared on parsed values, so renumbering or whitespace cannot disguise an
    echo as progress, and a *corrected expectation* — the repair a rejection note
    asks for — counts as a replacement. That last case is why this exists.
    """
    if a is None or b is None:
        return False
    if a.goal_check != b.goal_check or len(a.steps) != len(b.steps):
        return False
    return all((s.tool, s.arg, s.expect) == (q.tool, q.arg, q.expect)
               for s, q in zip(a.steps, b.steps))


def _norm(text):
    """Argument comparison for plan-divergence. Quotes and repeated whitespace are
    formatting, not intent: a model that writes \"x\" where the plan said x has
    not deviated. Anything else has."""
    return " ".join((text or "").replace('"', "").replace("'", "").split()).lower()


# ── The loop ──────────────────────────────────────────────────────────────────

DEFAULT_MAX_CALLS = 12      # plan + up to 8 tool steps + final + slack
PLAN_MAX_TOKENS = 512       # a plan block does not fit in the 256-token step cap


def escalated_cap(cap, ceiling, tokens):
    """The cap a retry should use, given an empty reply that consumed `cap`.

    An empty reply arrives two ways and they need different answers: a dropped
    stream costs nothing and is worth retrying as it was, while a reply that
    burned its entire cap without emitting content is a **budget** signal —
    retrying at the same size cannot succeed. Measured against the cascade's
    default hosted hop (`openai/gpt-oss-120b` on Groq): one tool-step prompt
    returned 256/256, 512/512 and 1024/1024 tokens with no content, and the
    correct directive in 12 tokens at 2048. So consuming the cap raises the retry
    to `empty_reply_token_ceiling`; anything less is a guess that the measurement
    already refutes.

    Returns `cap` unchanged when the ceiling is disabled, already reached, or the
    reply did not consume the cap.
    """
    ceiling = int(ceiling or 0)
    if ceiling > cap and int(tokens or 0) >= cap:
        return ceiling
    return cap


def _chat(config, messages, max_tokens, stop_when, chat_fn):
    """One model call, through the injected seam when there is one.

    `stop_when` is forwarded to an injected `chat_fn` rather than dropped: it is
    the loop's own decision about when a reply is complete, and a seam that loses
    it changes the very thing an injected caller is there to observe. Found when
    the live probe grew a cap large enough that its own guess at the predicate
    stopped matching the loop's (`.`live_refusal_probe`), which silently disabled
    early-stop for exactly the steps being measured.
    """
    if chat_fn is not None:
        return chat_fn(config, messages, max_tokens=max_tokens, stop_when=stop_when)
    return rt.chat_stream(config, messages, max_tokens=max_tokens, stop_when=stop_when)


def _failure(guard, reason, detail, step, run_id, stats):
    verdict = guard.step_failed(reason, detail, step=step)
    stats["diagnosis"] = verdict["diagnosis"]
    rt._emit_loop_diagnosis(run_id, step, verdict["diagnosis"])
    return verdict, guard.failure_line(verdict["diagnosis"], step)


def run_goal_verified(config, goal, *, max_tool_steps=None, max_calls=None,
                      token_budget=None, thresholds=None, env=None,
                      persona="BuilderBro", guard=None, chat_fn=None, plan=None,
                      emit=None, run_id=None):
    """Plan → act → verify → replan, gated on evidence.

    Returns a result dict (never raises for a model/tool fault):

        ok                 True only when the goal gate passed
        answer / failure    the answer, or the canonical structured failure line
        goal_verified       whether GOAL-CHECK held over verified evidence
        verified_steps      steps whose declared expectation held
        unmet_expectations  steps whose expectation did not hold (with detail)
        divergences         actions that did not match the plan
        replans             number of accepted REPLANs
        false_success_claims  how many times the model claimed done and the gate
                            refused it — the detector firing, counted
        evidence            each promoted item with the level it was confirmed at
                            (`invariant` independently, `observed` not) and the
                            checks named in `how_verified`
        claims_rejected     promotions the adversarial verifier refused, with the
                            check that refused them (A3, `verifier.py`)
        steps_used, tool_steps, tokens, plan

    `chat_fn(config, messages, max_tokens=...)` is injectable so the audit and
    the suite can drive real pathologies through this exact code path instead of
    a paraphrase of it. `plan` may be supplied to skip the planning call.

    `emit` (default from LOOP_GUARD_EMIT) gates evidence/ledger writes, so a
    library or test run writes nothing — the same rule the guard learned the hard
    way when a stubbed suite appended a fabricated record to the real log.
    """
    env = env if env is not None else os.environ
    if emit is None:
        emit = env.get("LOOP_GUARD_EMIT", "0") == "1"
    t = dict(thresholds) if thresholds is not None else loop_guard.active_thresholds(env)
    max_tool_steps = rt.MAX_STEPS if max_tool_steps is None else int(max_tool_steps)
    max_calls = DEFAULT_MAX_CALLS if max_calls is None else int(max_calls)
    token_budget = int(t.get("task_token_budget", 0) if token_budget is None else token_budget)
    run_id = run_id or uuid.uuid4().hex[:10]
    guard = guard if guard is not None else loop_guard.LoopGuard(
        max_tool_steps, thresholds=t, env=env, emit=emit)

    stats = {
        "ok": False, "answer": None, "failure": None, "reason": None,
        "goal_verified": False, "verified_steps": 0,
        "unmet_expectations": [], "divergences": [], "replans": 0,
        "false_success_claims": 0, "no_action": 0, "empty_responses": 0,
        "steps_used": 0, "tool_steps": 0, "tokens": 0, "plan": None,
        "diagnosis": None, "run_id": run_id,
        # ── A3 (verifier.py) ─────────────────────────────────────────────────
        # Every promoted item, with the level it was confirmed at, and every
        # promotion the verifier refused. Both are recorded rather than counted
        # away: a run that ends unverified has to say what was refused and why.
        "evidence": [], "claims_rejected": [],
        "evidence_levels": {"invariant": 0, "observed": 0, "declared": 0},
        "verify_mode": t.get("verify_mode", "confirm"),
    }

    def _confirm(tool, arg, output, expect):
        """Independently confirm a step's claim before it becomes evidence (A3).

        `None` means the verifier is off (`verify_mode: off`) — the A1 behaviour,
        where the model's own declared expectation is the whole gate. Returned
        rather than short-circuited so the loop records that the evidence was
        never confirmed, instead of recording it as if it had been.
        """
        if stats["verify_mode"] == "off":
            return None
        rerun = None
        if int(t.get("verify_reproduce", 1)) == 1:
            rerun = lambda a: rt.TOOLS[tool](a)
        model_adj = int(t.get("verify_model_adjudication", 0)) == 1
        judge = None
        if model_adj:
            judge = verifier.make_adjudicator(
                lambda cfg, msgs, max_tokens: _chat(config, msgs, max_tokens,
                                                    None, chat_fn))
        return verifier.confirm(
            {"tool": tool, "arg": arg, "output": output, "expect": expect},
            rerun=rerun, model_adjudication=model_adj, adjudicate=judge)

    messages = [
        {"role": "system", "content": plan_system_prompt(persona)},
        {"role": "user", "content": goal},
    ]

    def _call(kind, max_tokens, stop_when):
        reply = _chat(config, messages, max_tokens, stop_when, chat_fn)
        stats["steps_used"] += 1
        stats["tokens"] += int(reply.get("tokens") or 0)
        if emit:
            rt._emit_step(run_id, config, stats["steps_used"], reply, kind,
                          extra={"autonomy": {"plan_index": stats.get("plan_index", 0),
                                              "replans": stats["replans"]}})
        return reply

    def _budget_stop(step):
        if token_budget and stats["tokens"] > token_budget:
            return _failure(guard, "token_budget_exceeded",
                            {"tokens": stats["tokens"], "budget": token_budget}, step,
                            run_id, stats)[1]
        if stats["tool_steps"] > max_tool_steps:
            return _failure(guard, "budget_exhausted",
                            {"max_tool_steps": max_tool_steps,
                             "plan_steps": len(plan.steps) if plan else 0,
                             "suggestion": "goal decomposition"}, step,
                            run_id, stats)[1]
        return None

    # ── 1. Obtain a plan ──────────────────────────────────────────────────────
    plan_source = "supplied" if plan is not None else "model"
    if plan is not None:
        plan_raw = plan
        try:
            plan = parse_plan(plan_raw)
        except PlanError as e:
            verdict, line = _failure(guard, "plan_invalid", {"error": str(e)}, 1, run_id, stats)
            stats["failure"] = line
            stats["reason"] = "plan_invalid"
            return stats
        # The plan goes into the context for a supplied plan too, exactly as the
        # model's own plan does below. Found live: without it the model was told
        # "Execute step 1" about a plan it had never seen, and answered by writing
        # a fresh plan (with a wildcard goal check, refused as `plan_invalid`)
        # instead of acting. A run that was handed a plan should look to the model
        # like a run that wrote one — anything else measures the harness, not the
        # model.
        messages.append({"role": "assistant", "content": plan.raw})
    else:
        plan = None
        attempts = 1 + int(t.get("plan_repair_attempts", 1))
        plan_cap = PLAN_MAX_TOKENS
        for attempt in range(attempts):
            reply = _call("plan", plan_cap, None)
            content = (reply.get("content") or "").strip()
            # An empty plan reply consumes a repair attempt rather than ending the
            # run: the same transient that can drop one step can drop the very
            # first call, and `plan_invalid` with a repair prompt is a better
            # answer than a fatal transport error on a recoverable glitch.
            if not content:
                if attempt == attempts - 1:
                    verdict, line = _failure(
                        guard, "empty_response",
                        {"provider": reply.get("provider"), "attempts": attempts},
                        stats["steps_used"], run_id, stats)
                    stats["failure"], stats["reason"] = line, "empty_response"
                    return stats
                # Same escalation as a tool step: the plan call has the same cap
                # problem, and it is the first call a run makes.
                plan_cap = escalated_cap(
                    plan_cap, t.get("empty_reply_token_ceiling", 0),
                    reply.get("tokens"))
                guard.note("transient_empty_response",
                           {"provider": reply.get("provider"), "phase": "plan",
                            "attempt": attempt + 1, "attempts": attempts,
                            "tokens": reply.get("tokens"), "next_cap": plan_cap},
                           step=stats["steps_used"])
                messages.append({"role": "user", "content": REPAIR_NOTE})
                continue
            try:
                plan = parse_plan(content)
                break
            except PlanError as e:
                if attempt == attempts - 1:
                    verdict, line = _failure(
                        guard, "plan_invalid",
                        {"error": str(e), "attempts": attempts, "reply": content[:400]},
                        stats["steps_used"], run_id, stats)
                    stats["failure"], stats["reason"] = line, "plan_invalid"
                    return stats
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": REPAIR_NOTE})
        messages.append({"role": "assistant", "content": plan.raw})

    stats["plan"] = {
        "source": plan_source,
        "goal_check": format_spec(plan.goal_check),
        "goal_strength": spec_strength(plan.goal_check),
        "steps": [{"tool": s.tool, "arg": s.arg, "expect": format_spec(s.expect)}
                  for s in plan.steps],
    }
    messages.append({"role": "user", "content":
                     "Plan accepted (%d step(s), goal check `%s`). Execute step 1 "
                     "now: reply with ONLY <<<TOOL:%s>>>"
                     % (len(plan.steps), format_spec(plan.goal_check), _step_directive(plan, 0))})

    plan_index = 0
    step_cap = rt.TOOL_STEP_MAX_TOKENS   # raised after an empty cap-consuming reply
    evidence = []          # only output from steps whose expectation verified
    started = None
    try:
        import time as _time
        started = _time.monotonic()
    except Exception:  # pragma: no cover - time is stdlib
        pass

    # ── 2. Act / verify ───────────────────────────────────────────────────────
    while True:
        stats["plan_index"] = plan_index
        if stats["steps_used"] >= max_calls:
            verdict, line = _failure(guard, "budget_exhausted",
                                     {"max_calls": max_calls,
                                      "plan_steps": len(plan.steps),
                                      "verified_steps": stats["verified_steps"]},
                                     stats["steps_used"], run_id, stats)
            stats["failure"], stats["reason"] = line, "budget_exhausted"
            return stats

        reply = _call("tool", step_cap, rt._stream_stop)
        content = (reply.get("content") or "").strip()
        if not content:
            # An empty reply is usually transient: a provider that spent its token
            # budget without emitting content returns one, and so does a dropped
            # stream. Killing the run on the first one throws away a plan that was
            # already paid for, so retry a bounded number of times and record each
            # attempt as a tolerated note. Only exhaustion is a transport failure.
            stats["empty_responses"] += 1
            retries = int(t.get("empty_response_retries", 1))
            if stats["empty_responses"] > retries:
                verdict, line = _failure(guard, "empty_response",
                                         {"provider": reply.get("provider"),
                                          "empty_responses": stats["empty_responses"],
                                          "retries": retries,
                                          "cap": step_cap,
                                          "tokens": reply.get("tokens"),
                                          "plan_index": plan_index},
                                         stats["steps_used"], run_id, stats)
                stats["failure"], stats["reason"] = line, "empty_response"
                return stats
            consumed_cap = int(reply.get("tokens") or 0) >= step_cap
            step_cap = escalated_cap(step_cap, t.get("empty_reply_token_ceiling", 0),
                                     reply.get("tokens"))
            guard.note("transient_empty_response",
                       {"provider": reply.get("provider"),
                        "attempt": stats["empty_responses"],
                        "retries": retries, "tokens": reply.get("tokens"),
                        "consumed_cap": consumed_cap, "next_cap": step_cap},
                       step=stats["steps_used"])
            messages.append({"role": "user", "content": _empty_note(plan, plan_index)})
            continue
        stats["empty_responses"] = 0

        # REPLAN is checked before FINAL: a message that replaces the plan is not
        # also a completion claim, and treating it as one would let a model that
        # is still working be graded as finished. A plan *block* counts even
        # without the keyword — `is_plan_reply` records the live run that made
        # that necessary: a model that repaired a refused expectation correctly,
        # three times, and had all three repairs discarded as `no_action`.
        if "REPLAN:" in content or is_plan_reply(content):
            new_plan = None
            try:
                new_plan = parse_plan(content)
            except PlanError as e:
                stats["replans"] += 1
                if stats["replans"] > int(t.get("max_replans", 2)):
                    verdict, line = _failure(guard, "replan_storm",
                                             {"replans": stats["replans"],
                                              "max_replans": t.get("max_replans", 2),
                                              "last_error": str(e)},
                                             stats["steps_used"], run_id, stats)
                    stats["failure"], stats["reason"] = line, "replan_storm"
                    return stats
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": REPAIR_NOTE})
                continue
            if "REPLAN:" not in content and _same_plan(new_plan, plan):
                # A **bare** plan block that repeats the current plan was
                # re-sent, not replaced. That is the echo the `no_action` code is
                # for (the guard's hypothesis for it is "the model is re-emitting
                # its plan instead of executing a step"), so it falls through to
                # that path rather than being counted as progress. An explicit
                # `REPLAN:` keeps its own meaning: the model is *claiming* a
                # replacement, and repeating that claim is the storm.
                pass
            else:
                stats["replans"] += 1
                if stats["replans"] > int(t.get("max_replans", 2)):
                    verdict, line = _failure(guard, "replan_storm",
                                             {"replans": stats["replans"],
                                              "max_replans": t.get("max_replans", 2)},
                                             stats["steps_used"], run_id, stats)
                    stats["failure"], stats["reason"] = line, "replan_storm"
                    return stats
                if "REPLAN:" not in content:
                    # Tolerated, not a fault: the replan was accepted, so nothing
                    # is wrong with the run. Recorded because the *keyword* is
                    # what the prompt asks for, and a model that omits it is a
                    # finding about the prompt, not about the model.
                    guard.note("replan_unlabelled",
                               {"plan_index": plan_index,
                                "replans": stats["replans"],
                                "steps": len(new_plan.steps),
                                "reply": content[:200]}, step=stats["steps_used"])
                plan = new_plan
                plan_index = 0
                evidence = []
                stats["plan"]["steps"] = [{"tool": s.tool, "arg": s.arg,
                                           "expect": format_spec(s.expect)}
                                          for s in plan.steps]
                stats["plan"]["goal_check"] = format_spec(plan.goal_check)
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content":
                                 "Plan replaced (replan %d/%d). Execute step 1: reply "
                                 "with ONLY <<<TOOL:%s>>>"
                                 % (stats["replans"], t.get("max_replans", 2),
                                    _step_directive(plan, 0))})
                continue

        tool_lines = [ln for ln in content.splitlines() if rt._is_tool_line(ln)]

        # Neither acting nor claiming completion is its own fault. The earlier
        # behaviour folded it into the completion gate, so a model that re-emitted
        # its plan was reported as having claimed a completion it never made — the
        # right verdict (no evidence was collected) reached through the wrong
        # diagnosis, pointing the fix at the prompt's honesty rather than at the
        # step directive the model ignored.
        if not tool_lines and "FINAL:" not in content:
            stats["no_action"] += 1
            limit = int(t.get("no_action_limit", 2))
            if stats["no_action"] > limit:
                verdict, line = _failure(guard, "no_action",
                                         {"no_action": stats["no_action"],
                                          "limit": limit,
                                          "plan_index": plan_index,
                                          "reply": content[:300]},
                                         stats["steps_used"], run_id, stats)
                stats["failure"], stats["reason"] = line, "no_action"
                return stats
            messages.append({"role": "assistant", "content": content})
            messages.append({"role": "user", "content": _nudge(plan, plan_index)})
            continue

        # ── The completion gate ───────────────────────────────────────────────
        if "FINAL:" in content or not tool_lines:
            goal_ok, detail = verify(plan.goal_check, "\n".join(evidence))
            # A completion is not verifiable without evidence. Some conditions are
            # satisfied by a vacuum — `absent:X` above all — so a run that promoted
            # nothing could pass a gate it never fed. The goal condition is a claim
            # about collected evidence, so no evidence means no verified goal.
            if goal_ok and not evidence:
                goal_ok = False
                detail = dict(detail, evidence_empty=True,
                              note="the goal condition held over an empty evidence "
                                   "set, which is not a verified completion")
            # A verified goal with no answer is not a completion. Found live: the
            # model replied `FINAL:` and nothing else, and the run reported
            # success with an empty answer because the goal condition held.
            if goal_ok and "FINAL:" in content:
                answer = content.split("FINAL:", 1)[1].strip()
                if not answer:
                    verdict, line = _failure(
                        guard, "empty_answer",
                        {"goal_check": format_spec(plan.goal_check),
                         "verified_steps": stats["verified_steps"],
                         "claim": content[:200]},
                        stats["steps_used"], run_id, stats)
                    stats["failure"], stats["reason"] = line, "empty_answer"
                    return stats
            if not goal_ok:
                stats["false_success_claims"] += 1
                verdict, line = _failure(
                    guard, "unverified_completion",
                    {"goal_check": format_spec(plan.goal_check),
                     "check_detail": detail,
                     "verified_steps": stats["verified_steps"],
                     "unmet_expectations": len(stats["unmet_expectations"]),
                     "claims_rejected": len(stats["claims_rejected"]),
                     # How the evidence it *did* collect was confirmed, on the
                     # failure path as well as the success one: a refused run that
                     # silently drops the distinction between independent and
                     # merely-declared evidence hides the thing A3 measures. Found
                     # by a live run that ended `unverified_completion` with 0
                     # verified steps in the detail and no level named.
                     "evidence_levels": {k: v for k, v in
                                         stats["evidence_levels"].items() if v},
                     "evidence_chars": sum(len(e) for e in evidence),
                     "claim": content[:300]},
                    stats["steps_used"], run_id, stats)
                stats["failure"], stats["reason"] = line, "unverified_completion"
                return stats
            stats["ok"] = True
            stats["goal_verified"] = True
            stats["answer"] = content
            if emit:
                rt._emit_step(run_id, config, stats["steps_used"], reply, "final",
                              extra=guard.summary("final", stats["steps_used"]))
            return stats

        # ── Execute, then verify against the plan ─────────────────────────────
        planned = plan.steps[plan_index] if plan_index < len(plan.steps) else None
        executed = []
        calls = []
        for ln in tool_lines:
            name, arg = loop_guard.parse_directive(ln)
            if name not in rt.TOOLS:
                out = loop_guard.fail("step=%d reason=unknown tool %r"
                                      % (stats["steps_used"], name))
            elif not arg:
                out = loop_guard.fail("step=%d reason=tool %s missing argument"
                                      % (stats["steps_used"], name))
            else:
                try:
                    out = rt.TOOLS[name](arg)
                except Exception as e:
                    out = loop_guard.fail("step=%d reason=%s" % (stats["steps_used"], e))
            calls.append((name, arg, loop_guard.result_failed(out),
                          loop_guard.result_signature(out)))
            executed.append(out)

            match = (planned is not None and name == planned.tool
                     and _norm(arg) == _norm(planned.arg))
            if match:
                ok, detail = verify(planned.expect, out)
                verdict = _confirm(name, arg, out, planned.expect) if ok else None
                if ok and verdict is not None and not verdict.ok:
                    # The declared expectation held, and independent confirmation
                    # refused it anyway — a non-discriminating expectation, or a
                    # claim that did not reproduce. Recorded, not promoted, and
                    # the plan does not advance: the step produced no evidence, so
                    # treating it as done would be the very vacuity A3 exists to
                    # refuse. The model is told which check refused and why.
                    stats["claims_rejected"].append({
                        "step": plan_index + 1, "tool": name, "arg": arg,
                        "expect": format_spec(planned.expect),
                        "refused_by": verdict.refused_by,
                        "checks": [c for c in verdict.checks if c.get("ok") is False]})
                    guard.note("claim_rejected", stats["claims_rejected"][-1],
                               step=stats["steps_used"])
                    note = _rejection_note(plan, plan_index + 1, verdict)
                elif ok:
                    stats["verified_steps"] += 1
                    # `declared` = the verifier was off, so the promotion rests on
                    # the model's own expectation and says so.
                    level = verdict.level if verdict is not None else "declared"
                    stats["evidence_levels"][level] = \
                        stats["evidence_levels"].get(level, 0) + 1
                    stats["evidence"].append({
                        "step": plan_index + 1, "tool": name, "arg": arg,
                        "level": level,
                        "how_verified": (verdict.how_verified if verdict is not None
                                         else "declared_expectation_only"),
                        "chars": len(out)})
                    evidence.append(out)          # only confirmed output is evidence
                    plan_index += 1
                    note = _status_note(plan, plan_index, True, detail)
                else:
                    stats["unmet_expectations"].append(
                        {"step": plan_index + 1, "tool": name, "arg": arg, "detail": detail})
                    # Tolerated: the model gets the finding and may replan. It is
                    # recorded as a note now and only becomes a stop if the run
                    # ends without ever verifying the goal.
                    guard.note("unmet_expectation",
                               {"step": plan_index + 1, "tool": name, "arg": arg,
                                "detail": detail}, step=stats["steps_used"])
                    note = _status_note(plan, plan_index + 1, False, detail)
            else:
                stats["divergences"].append(
                    {"step": stats["steps_used"], "planned": (
                        "%s %s" % (planned.tool, planned.arg)) if planned else None,
                     "actual": "%s %s" % (name, arg)})
                guard.note("plan_divergence",
                           stats["divergences"][-1], step=stats["steps_used"])
                if len(stats["divergences"]) > int(t.get("plan_divergence_limit", 1)):
                    verdict, line = _failure(
                        guard, "plan_divergence",
                        {"divergences": stats["divergences"],
                         "limit": t.get("plan_divergence_limit", 1)},
                        stats["steps_used"], run_id, stats)
                    stats["failure"], stats["reason"] = line, "plan_divergence"
                    return stats
                note = ("OFF-PLAN: the plan's next step was `%s`; you issued `%s %s`. "
                        "Return to the plan or reply REPLAN:."
                        % ((("%s %s" % (planned.tool, planned.arg))
                            if planned else "(plan complete)"), name, arg))

        messages.append({"role": "assistant", "content": content})
        messages.append({"role": "user", "content": "\n".join(executed) + "\n" + note})

        if calls:
            stats["tool_steps"] += 1
        verdict = guard.observe(stats["steps_used"], "tool", calls,
                                [c[2] for c in calls], messages,
                                elapsed_s=(None if started is None else
                                           __import__("time").monotonic() - started))
        if verdict["action"] == "stop":
            rt._emit_loop_diagnosis(run_id, stats["steps_used"], verdict["diagnosis"])
            stats["failure"] = guard.failure_line(verdict["diagnosis"], stats["steps_used"])
            stats["reason"] = verdict["diagnosis"]["reason"]
            stats["diagnosis"] = verdict["diagnosis"]
            return stats
        if verdict["action"] == "compact":
            print("[autonomy] context %d chars over budget %d — elided %d older "
                  "result(s)" % (verdict["context_chars"], verdict["budget"],
                                 verdict["elided"]), file=sys.stderr)

        stop = _budget_stop(stats["steps_used"])
        if stop:
            stats["failure"] = stop
            stats["reason"] = stats["diagnosis"]["reason"]
            return stats
        if plan_index >= len(plan.steps):
            messages.append({"role": "user", "content":
                             "All %d planned step(s) executed. If GOAL-CHECK `%s` holds "
                             "over the collected evidence, reply FINAL: <answer>. "
                             "Otherwise reply REPLAN: with a new plan."
                             % (len(plan.steps), format_spec(plan.goal_check))})


def format_result(result):
    """CLI rendering: the answer on success, the structured failure otherwise.

    The success line names how the evidence was confirmed, because "verified"
    without saying *by what* is exactly the claim this layer exists to stop
    making. With the verifier off it says so rather than staying silent.
    """
    if result.get("ok"):
        m = result
        levels = m.get("evidence_levels") or {}
        how = ", ".join("%d %s" % (n, lvl) for lvl, n in sorted(levels.items()) if n) \
            or "none"
        return ("%s\n[verified] goal check `%s` held over %d confirmed step(s) "
                "[evidence: %s] (%d call(s), %d tool step(s), %d replan(s), "
                "%d divergence(s), %d claim(s) rejected, %d tokens)"
                % (m["answer"], m["plan"]["goal_check"], m["verified_steps"], how,
                   m["steps_used"], m["tool_steps"], m["replans"],
                   len(m["divergences"]), len(m["claims_rejected"]), m["tokens"]))
    return result.get("failure") or loop_guard.fail("reason=unknown autonomy failure")


if __name__ == "__main__":  # pragma: no cover - thin manual entry point
    import agent_runtime
    if len(sys.argv) < 2:
        print("usage: python3 autonomy.py <goal>")
        raise SystemExit(2)
    agent_runtime.load_env_file()
    _cfg = agent_runtime.load_config()
    os.environ.setdefault("LOOP_GUARD_EMIT", "1")
    print(format_result(run_goal_verified(_cfg, " ".join(sys.argv[1:]))))
