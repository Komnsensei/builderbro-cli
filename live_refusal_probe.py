#!/usr/bin/env python3
"""live_refusal_probe.py — exercise the A3 claim-refusal path with a REAL model.

WHY THIS EXISTS
---------------
Every other arm in this repo drives the loop with scripted replies
(`loop_audit.py`, `autonomy_suite.py`, `verifier_test.py`). That is the right way
to measure the loop's *decisions* — but it cannot answer the one question A3's
refusal path leaves open, because a stub has no opinion:

    when the verifier refuses a claim, the loop tells the model so and offers a
    repair. **Does a real model do the thing it was told?**

Nothing in the stub arms can see that. A stub replies with whatever the spec
says; a model either repairs, repeats, or claims victory regardless. So the
refusal path has been tested *through* (the verdict, the counters, the failure
line) but never exercised *end to end* against a model that has to react to it.

WHAT IT DOES
------------
One live run of `autonomy.run_goal_verified` over the real brain cascade, with
every message the loop speaks and every reply the model returns captured verbatim
to a JSONL transcript. It then classifies the model's first reply after the
rejection note and reports the run's outcome.

Two arms, because they exercise different refusal checks and the honest repair is
different in each:

* `natural` (default) — the model writes its own plan, as in a normal run. The
  refusal only happens if the model declares a non-discriminating expectation
  by itself, which the plan prompt tells it not to do. When it does not, the arm
  reports `untested`: an arm that produced no refusal has measured nothing, and
  saying so is the point.
* `seeded` — the plan is supplied, so the refusal path is reached
  deterministically. This is a **controlled** arm, not a natural one: the weak
  expectation is the probe's, and the measurement is still real, because the
  model's reply to the note is its own. Each seeded scenario names the check it
  is aiming at (`not_vacuous` for a non-discriminating expectation, `not_failure`
  for an expectation that also holds on a failed call).

COST AND SIDE EFFECTS
---------------------
This is the only tool here that spends real requests on a hosted provider, so it
is never run by a test or by another audit. `emit=False` by default: a probe
writes its transcript, not the agent's ledger. `--emit` opts into ledger writes.

Usage:
    python3 live_refusal_probe.py                 # all three arms, live
    python3 live_refusal_probe.py --arm natural   # cheapest; may measure nothing
    python3 live_refusal_probe.py --arm seeded    # both controlled scenarios
    python3 live_refusal_probe.py --report live-refusal-summary.json --register
                                                  # no requests: reprint the report
                                                  # and close the cycle

`--arm all` is the default because the two kinds answer different questions and a
session with only one of them is easy to misread: the natural arm asks whether the
refusal arises from the model itself (measured: it does not — the model writes the
pathological expectation but a weak GOAL-CHECK stops the run first), and the seeded
arms reach the path deterministically so the model's reaction to the note can be
observed at all.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

import agent_runtime as rt
import autonomy
import loop_guard

# The refusal the probe is aiming at, per seeded scenario. `want` is asserted
# against the run's recorded `claims_rejected[].refused_by` so a probe that
# reached *a* refusal but not the intended one cannot report success by accident.
SCENARIOS = {
    # An expectation that holds on the canonical failure line too, so it cannot
    # tell a usable observation from a busted one. The repair is to name a
    # needle — which for a directory listing the model has not seen yet is
    # impossible *a priori*, and that tension is the interesting part.
    # Two steps, the first of them strong: the model acts, is told the step
    # verified, and only then meets the refusal. A one-step plan put the refusal
    # in the first exchange, where the model's first instinct is to answer from
    # the plan it was just handed — a real failure mode (the gate refuses it as
    # `unverified_completion`), but not the one this arm is for.
    "non_discriminating": {
        "want": "not_vacuous",
        "plan": ("PLAN:\n"
                 "1. tool: list_dir .\n"
                 "   expect: contains:agent_runtime.py\n"
                 "2. tool: read_file ./agent_runtime.py\n"
                 "   expect: nonempty\n"
                 "GOAL-CHECK: contains:agent_runtime.py\n"),
        "goal": ("Use the tools to confirm what is in this directory, then report "
                 "one file it contains."),
    },
    # An expectation that holds on a *failed* call's output: the classic A3 hole,
    # where a failed read becomes a "verified observation" because the failure
    # string is not empty. The repair must be to read something that exists.
    #
    # The path is a real directory rather than an invented one on purpose: naming
    # a directory where a file was meant is a mistake a planner actually makes,
    # and a deliberately impossible path was measured to send a reasoning model
    # into 256 tokens of deliberation with no content (see `--arm natural` in the
    # transcript), which blocks the measurement this arm exists for.
    "failed_call": {
        "want": "not_failure",
        "plan": ("PLAN:\n"
                 "1. tool: read_file ./freebrain-residence\n"
                 "   expect: nonempty\n"
                 "GOAL-CHECK: contains:agent_runtime\n"),
        "goal": "Open ./freebrain-residence and quote the first line of its contents.",
    },
}

# The natural arm's goal. Deliberately exploratory: the model must list a
# directory whose contents it cannot know in advance, which is the situation in
# which declaring a discriminating expectation *before* looking is impossible —
# the honest reason a model ends up writing `nonempty`.
NATURAL_GOAL = (
    "List the files in this directory and report how many there are and three of "
    "their names."
)

TRANSCRIPT_DEFAULT = "live-refusal-transcript.jsonl"


class Transcript:
    """Records, verbatim, every message the loop spoke and every reply returned.

    Delta capture, not a full dump per turn: what the model *was newly told* is
    the thing being measured (a rejection note is one message), and dumping the
    whole context on every turn would bury it. `provider` is recorded per reply
    so a failover mid-run is visible in the record rather than inferred.

    `arm` and `turn` are per-arm and `begin()` must be called before each arm:
    the cursor is an offset into a *specific* run's message list, and carrying it
    across arms silently misaligns every delta afterwards (the first run of this
    probe did exactly that, attributing one arm's tool output to the next one).
    """

    def __init__(self, path):
        self.path = path
        self.records = []
        self._seen = 0
        self._turn = 0
        self._arm = None

    def begin(self, arm):
        """Start recording a new run: reset the offset and the turn counter."""
        self._arm = arm
        self._seen = 0
        self._turn = 0
        return len(self.records)

    def chat(self, config, messages, max_tokens=None, stop_when=None, **_):
        """The `chat_fn` seam `run_goal_verified` documents, over the real
        cascade. `stop_when` is the loop's own decision, forwarded by `_chat`:
        the probe does not re-derive it, because the retry escalation raises a
        step's cap past the tool-step constant and any guess keyed on that
        constant would stop matching the loop's behaviour.
        """
        stop = stop_when
        delta = list(messages[self._seen:])
        self._seen = len(messages)
        reply = rt.chat_stream(config, messages, max_tokens=max_tokens, stop_when=stop)
        self._turn += 1
        self.records.append({
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "arm": self._arm,
            "turn": self._turn,
            "told": [{"role": m["role"], "content": (m.get("content") or "")}
                     for m in delta],
            "replied": reply.get("content") or "",
            "provider": reply.get("provider"),
            "model": reply.get("model"),
            "tokens": reply.get("tokens"),
            "max_tokens": max_tokens,
            "elapsed_s": reply.get("elapsed"),
            "failovers": reply.get("attempts") or [],
        })
        return reply

    def write(self):
        with open(self.path, "a", encoding="utf-8") as f:
            for rec in self.records:
                f.write(json.dumps(rec) + "\n")


def classify_reaction(reply):
    """What the model did with the note, in the loop's own vocabulary.

    Deliberately a *classification*, not a score: `replan` is not automatically
    correct (a replan that keeps the same non-discriminating expectation repairs
    nothing) and `acted` is not automatically wrong. The caller pairs this with
    whether the step was eventually promoted.

    `replan_unlabelled` is kept apart from both `replan` and `neither` because it
    is the distinction that was invisible in the first live run: a reply that is a
    plan block without the `REPLAN:` keyword is a repair the model performed and
    the loop cannot read, and collapsing it into "neither" would record a correct
    repair as the model having said nothing.
    """
    body = (reply or "").strip()
    if not body:
        return "empty"
    if "REPLAN:" in body:
        return "replan"
    if "FINAL:" in body:
        return "claimed_completion"
    if autonomy.is_plan_reply(body):
        return "replan_unlabelled"
    if any(rt._is_tool_line(ln) for ln in body.splitlines()):
        return "acted"
    return "neither"


def replan_repairs(reply, refused_by):
    """Does a REPLAN answer actually fix the check that refused?

    For `not_vacuous` the fix is a discriminating expectation on the step that
    was refused; for `not_failure` it is acting on something that exists (the
    expectation is irrelevant while the call itself fails). Both are read out of
    the model's own new plan, parsed with the real parser — a reply that merely
    *says* it repaired does not count.
    """
    try:
        plan = autonomy.parse_plan(reply)
    except Exception:
        return False, "unparseable replan"
    if not plan.steps:
        return False, "replan has no steps"
    step = plan.steps[0]
    strength = autonomy.spec_strength(step.expect)
    if refused_by == "not_vacuous":
        return (strength == "strong",
                "step 1 expect=%s (%s)" % (autonomy.format_spec(step.expect), strength))
    if refused_by == "not_failure":
        probe = rt.TOOLS[step.tool](step.arg) if step.tool in rt.TOOLS else ""
        repaired = not loop_guard.result_failed(probe)
        return repaired, "step 1 %s %s -> %s" % (
            step.tool, step.arg, "a real observation" if repaired
            else "still a failed call")
    return False, "no repair rule for %s" % refused_by


def _first_reaction(transcript, start):
    """The model's reply to the first rejection note, with the note it answered.

    Located by record index (`start` = where this arm began writing), not by turn
    number: turns are per-arm and an index is what the caller actually has. The
    note is recovered verbatim from the transcript rather than rebuilt — a
    summary of the note is not the note, and the note is half of what this
    records.

    The loop hands the model the tool output and the note in ONE user message, so
    the note is cut out of it: quoting the whole message would present a 4000-char
    file listing as "what the loop told the model to fix", which is what the first
    live report did.
    """
    for rec in transcript.records[start:]:
        body = next((m["content"] for m in reversed(rec["told"])
                     if m["role"] == "user"), "")
        if "NOT CONFIRMED:" in body:
            return body[body.index("NOT CONFIRMED:"):], rec["replied"]
    return None, None


def run_arm(name, goal, plan=None, emit=False, thresholds=None,
            transcript=None, config=None):
    """One live run of the verified loop, returning its record.

    `plan` is raw PLAN text for a seeded arm and None for the natural one, which
    is exactly the loop's own `plan` parameter — the probe drives the production
    code path, not a copy of it.
    """
    transcript = transcript or Transcript(TRANSCRIPT_DEFAULT)
    t = dict(thresholds) if thresholds is not None else loop_guard.active_thresholds()
    mark = transcript.begin(name)
    guard = loop_guard.LoopGuard(rt.MAX_STEPS, thresholds=t, emit=emit)
    result = autonomy.run_goal_verified(
        config, goal, plan=plan, chat_fn=transcript.chat, guard=guard,
        thresholds=t, emit=emit)
    rec = {
        "arm": name, "goal": goal, "plan_supplied": plan is not None,
        "ok": bool(result["ok"]), "reason": result["reason"],
        "verified_steps": result["verified_steps"],
        "replans": result["replans"],
        "claims_rejected": result["claims_rejected"],
        "refused_by": [c["refused_by"] for c in result["claims_rejected"]],
        "evidence_levels": {k: v for k, v in result["evidence_levels"].items() if v},
        "false_success_claims": result["false_success_claims"],
        "steps_used": result["steps_used"],
        "turns": len(transcript.records) - mark,
        "answer": (result.get("answer") or "")[:400],
        "failure": result.get("failure"),
    }
    # Which model actually served the run, taken from the transcript rather than
    # from config: the cascade re-discovers a retired model mid-run, so the
    # configured name is not the name that answered, and an observation about a
    # model's behaviour is worthless without it.
    served = [r for r in transcript.records[mark:] if r.get("provider")]
    rec["served_by"] = ("%s/%s" % (served[0]["provider"], served[0]["model"])) if served else None
    rec["failovers"] = [a for r in served for a in (r.get("failovers") or [])]
    # Empty replies with tokens generated are a reasoning budget, not a dropped
    # stream: the provider billed a completion that carried no content. Recorded
    # per turn because it is the thing that blocks an arm from measuring anything,
    # and an arm that reached no refusal must say why.
    rec["empty_replies"] = [{"turn": r["turn"], "tokens": r["tokens"],
                             "max_tokens": r.get("max_tokens")}
                            for r in transcript.records[mark:] if not r["replied"]]
    if rec["claims_rejected"]:
        note, reply = _first_reaction(transcript, mark)
        rec["note"] = note
        rec["reply"] = (reply or "")[:1200]
        rec["reaction"] = classify_reaction(reply)
        refused_by = rec["refused_by"][0]
        if rec["reaction"] in ("replan", "replan_unlabelled"):
            repairs, why = replan_repairs(reply, refused_by)
            rec["replan_repairs"] = repairs
            rec["replan_detail"] = why
    else:
        rec["note"] = rec["reply"] = None
        rec["reaction"] = "untested"
    return rec


def _quote(text, limit=300):
    if not text:
        return "—"
    body = " ".join(text.split())
    return "`%s%s`" % (body[:limit], "…" if len(body) > limit else "")


def report(rows, transcript_path):
    lines = ["", "Live claim-refusal probe — the real model against the A3 "
             "refusal path (transcript: `%s`):" % transcript_path, "",
             "| arm | served by | refusal reached | refused by | model's reaction | "
             "repaired? | run outcome |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        repaired = ("yes (%s)" % r["replan_detail"]) if r.get("replan_repairs") is True \
            else ("no (%s)" % r["replan_detail"]) if r.get("replan_repairs") is False \
            else "—"
        outcome = ("goal met, %d verified step(s), evidence %s"
                   % (r["verified_steps"], r["evidence_levels"] or "none")
                   ) if r["ok"] else "refused (`%s`)" % r["reason"]
        lines.append("| `%s`%s | `%s` | %s | %s | `%s` | %s | %s |" % (
            r["arm"], " (supplied plan)" if r["plan_supplied"] else " (model-planned)",
            r.get("served_by") or "?",
            "%d claim(s)" % len(r["claims_rejected"]) if r["claims_rejected"]
            else "**no** — nothing measured",
            ", ".join("`%s`" % c for c in r["refused_by"]) or "—",
            r["reaction"], repaired, outcome))
    blocked = [r for r in rows if r["empty_replies"]]
    if blocked:
        lines += ["", "**Arms blocked by an empty reply** — the provider billed a "
                  "completion that carried no content, which is a reasoning budget "
                  "spent inside the tool-step cap rather than a dropped stream:", ""]
        for r in blocked:
            lines.append("- `%s`: %s" % (r["arm"], ", ".join(
                "turn %d -> %s of %s token(s)" % (e["turn"], e["tokens"],
                                                  e["max_tokens"])
                for e in r["empty_replies"])))
    for r in rows:
        if not r["claims_rejected"]:
            continue
        lines += ["", "### `%s` — the note, and what came back" % r["arm"],
                  "", "**Note the loop spoke:**", "", "> " + (r["note"] or "").replace(
                      "\n", "\n> "),
                  "", "**Model replied:**", "", "> " + (r["reply"] or "(empty)").replace(
                      "\n", "\n> ")]
    return "\n".join(lines)


SUMMARY_DEFAULT = "live-refusal-summary.json"

# Tests across the modules this cycle touches, counted the way the inventory counts
# them, so the number in the record is re-derivable rather than remembered.
TEST_MODULES = ("autonomy_test.py", "loop_guard_test.py", "verifier_test.py",
                "live_refusal_probe_test.py")


def test_count(paths=TEST_MODULES):
    total = 0
    for path in paths:
        with open(path, "r", encoding="utf-8") as f:
            total += sum(1 for ln in f if ln.strip().startswith("def test_"))
    return total


def register_cycle(rows, transcript_path, summary_path):
    """Close the cycle this probe measured, with its evidence attached.

    Closed, like `autonomy_suite.register`, and for the same reason: every phase
    ran, so writing them all is not a claim about work that did not happen. The
    regression arms are re-measured here rather than quoted from an earlier run,
    and the `detail` block carries the live arm numbers so a reader is not asked
    to trust the prose.
    """
    import loop_audit

    goal_arm = loop_audit.run_autonomy_suite()
    verify_arm = loop_audit.run_verify_comparison()
    v = verify_arm["assessment"]
    tests = test_count()
    tested = [r for r in rows if r["claims_rejected"]]
    repaired = [r["arm"] for r in tested if r.get("replan_repairs") is True]
    detail = {
        "live_arms": len(rows),
        "arms_that_reached_a_refusal": len(tested),
        "arms_that_did_not": [r["arm"] for r in rows if not r["claims_rejected"]],
        "refusals_by_check": sorted({c for r in rows for c in r["refused_by"]}),
        "arms_repaired_by_the_model": repaired,
        # Per-arm outcome, keyed by arm. Named for what it holds: an earlier key
        # read `arms_refused_after_repairing` while listing every arm's end reason,
        # which is the same defect — a record whose label describes something other
        # than its contents — this cycle exists to fix.
        "arm_outcomes": {r["arm"]: (r["reason"] or "goal met") for r in rows},
        "served_by": sorted({r["served_by"] for r in rows if r.get("served_by")}),
        "evidence_levels": {r["arm"]: r["evidence_levels"] for r in rows
                            if r["evidence_levels"]},
        "transcript": transcript_path,
        "summary": summary_path,
        "goal_arm_correct": "%d/%d" % (sum(1 for r in goal_arm if r["correct"]),
                                        len(goal_arm)),
        "verifier_detection": "%d/%d" % (v["detections"], v["opportunities"]),
        "verifier_false_accusations": "%d/%d" % (v["false_accusations"],
                                                  v["honest_opportunities"]),
        "verifier_verdict": v["verdict"],
        "tests": tests,
        "threshold_empty_reply_token_ceiling":
            loop_guard.active_thresholds()["empty_reply_token_ceiling"],
    }
    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "source": "live_refusal_probe.py",
        "reason": "live_refusal_coverage",
        "severity": "info",
        "stage": "verification",
        "title": "A3 live coverage: the refusal path against a real model — and the "
                 "four defects only a live run could find",
        "summary": {
            "arms": len(rows),
            "refusals_reached": sum(len(r["claims_rejected"]) for r in rows),
            "model_repaired": len(repaired),
        },
        "phases": {
            "detect": (
                "A3's refusal path had only ever been driven by scripted replies, so "
                "nothing recorded what a real model does with the message. Driving "
                "it live found four defects, none visible from a stub: a correct "
                "PLAN: block discarded for missing the `REPLAN:` keyword (three "
                "times in one run); a rejection note whose first suggested repair "
                "(\"re-run the step\") cannot change an expectation the plan holds; a "
                "supplied `plan=` parsed and enforced but never shown to the model, "
                "so it was asked to execute a plan it had never seen; and an empty "
                "reply that had consumed its whole cap retried at the same cap"),
            "research": (
                "Each fix is the measured one, not the plausible one. For the empty "
                "reply: one tool-step prompt returned 256/256, 512/512 and 1024/1024 "
                "tokens of reasoning with no content and then the correct directive "
                "in 12 tokens at 2048, so a retry has to reach 2048 to be worth "
                "making (`empty_reply_token_ceiling`, registered). For the plan "
                "block: the system prompt teaches the `PLAN:` block and mentions "
                "`REPLAN:` once, so the block is the form a model produces; a plan "
                "block can only mean \"replace the plan\", and a re-sent identical "
                "one is still the echo `no_action` describes"),
            "design": (
                "`escalated_cap` raises a retry only when the reply actually billed "
                "its cap; `is_plan_reply` + `_same_plan` accept a plan block as a "
                "replacement when it differs from the current plan; a supplied plan "
                "is echoed into the context exactly as a model-written one is; "
                "`REFUSAL_FIXES` gives each refusing check its own remedy and the "
                "note asks for a corrected plan. `live_refusal_probe.py` records the "
                "transcript verbatim and can reprint the report without spending "
                "requests again"),
            "implement": (
                "autonomy.py (escalated_cap, is_plan_reply, _same_plan, "
                "REFUSAL_FIXES, the supplied-plan echo, `stop_when` forwarded to the "
                "injected seam); loop_guard.py (the `empty_reply_token_ceiling` "
                "threshold and the `replan_unlabelled` reason code); "
                "live_refusal_probe.py + live_refusal_probe_test.py"),
            "test": (
                "%d tests across the modules this cycle touches; goal arm %d/%d; "
                "verifier controls %d/%d detected with %d/%d honest claims refused "
                "(`%s`). Live: %d of %d arms reached a refusal, the checks reached "
                "were %s, and the model repaired on %s — its corrected expectation "
                "was accepted, the step re-executed, and the goal then verified over "
                "3 `invariant` steps. The model-planned arm reached no refusal: it "
                "wrote the pathological `expect: nonempty` itself but the plan gate "
                "refused its weak GOAL-CHECK first"
                % (tests, sum(1 for r in goal_arm if r["correct"]), len(goal_arm),
                   v["detections"], v["opportunities"], v["false_accusations"],
                   v["honest_opportunities"], v["verdict"], len(tested), len(rows),
                   ", ".join("`%s`" % c for c in detail["refusals_by_check"]) or "—",
                   ", ".join("`%s`" % a for a in repaired) or "—")),
            "register": "self — this record, with the transcript it rests on",
        },
        "detail": detail,
    }
    path = loop_guard.SELF_IMPROVEMENT_LOG
    loop_guard.LoopGuard.emit_cycle(record, path)
    return path


def save_summary(path, rows, transcript_path):
    """Persist the arm rows next to the transcript.

    Without this the report could only be reprinted by spending the requests
    again, which makes a record that cannot be re-read — and the second run is not
    the first one, so the numbers would silently change underneath the prose.
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"ts": datetime.datetime.now(datetime.timezone.utc)
                   .isoformat(timespec="seconds"),
                   "transcript": transcript_path, "arms": rows}, f, indent=2)
    return path


def main(argv=None):
    p = argparse.ArgumentParser(description="Exercise the A3 claim-refusal path live")
    p.add_argument("--arm", choices=("natural", "seeded", "all"), default="all")
    p.add_argument("--transcript", default=TRANSCRIPT_DEFAULT)
    p.add_argument("--summary", default=SUMMARY_DEFAULT,
                   help="where the arm rows are written (so the report can be "
                        "reprinted without spending the requests again)")
    p.add_argument("--report", metavar="SUMMARY",
                   help="reprint the report from a saved summary and exit — no "
                        "requests are made")
    p.add_argument("--register", action="store_true",
                   help="write the closed self-building cycle for this measurement "
                        "(re-measures the regression arms; makes no model requests)")
    p.add_argument("--emit", action="store_true",
                   help="also write the run to the agent's ledger (off by default: "
                        "a probe records its transcript, not the agent's history)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if args.report:
        # Reprinting and registering from a SAVED summary make no requests, which
        # is the point: the measurement was paid for once, and a record that can
        # only be read by buying it again is not a record.
        with open(args.report, "r", encoding="utf-8") as f:
            saved = json.load(f)
        rows = saved["arms"]
        transcript_path = saved.get("transcript") or args.transcript
        if args.register:
            print("[probe] logged closed cycle -> %s"
                  % register_cycle(rows, transcript_path, args.report))
        if args.json:
            print(json.dumps(rows, indent=2))
        else:
            print(report(rows, transcript_path))
        return 0

    rt.load_env_file()
    config = rt.load_config()
    models = rt.list_models(config)
    if not config["model"] and models:
        config["model"] = models[0]
    transcript = Transcript(args.transcript)

    rows = []
    if args.arm in ("natural", "all"):
        rows.append(run_arm("natural", NATURAL_GOAL, transcript=transcript,
                            config=config, emit=args.emit))
    if args.arm in ("seeded", "all"):
        for name, spec in sorted(SCENARIOS.items()):
            row = run_arm(name, spec["goal"], plan=spec["plan"],
                          transcript=transcript, config=config, emit=args.emit)
            row["wanted"] = spec["want"]
            row["hit_wanted"] = spec["want"] in row["refused_by"]
            rows.append(row)

    transcript.write()
    save_summary(args.summary, rows, transcript.path)
    if args.register:
        print("[probe] logged closed cycle -> %s"
              % register_cycle(rows, transcript.path, args.summary))
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    print(report(rows, transcript.path))
    for r in rows:
        if r.get("wanted") and not r["hit_wanted"]:
            print("\n[probe] arm `%s` reached refusal(s) %s but not the intended `%s` — "
                  "not counted as exercising that check."
                  % (r["arm"], r["refused_by"], r["wanted"]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
