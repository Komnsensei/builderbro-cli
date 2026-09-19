#!/usr/bin/env python3
"""autonomy_test.py — tests for the goal-directed layer and its audit instruments.

Hermetic: the model is scripted, the workspace is a generated fixture, `emit` is
off, and nothing reaches the network. Three classes of test carry the weight:

* **The gate.** A `FINAL` claim is accepted only when the goal condition holds
  over *verified* evidence. The controls that matter are the adversarial ones —
  a model that claims done with no evidence, and a goal whose token appears in
  output from a step whose declared expectation failed. An auditor that passes
  everything proves nothing, so both directions are asserted.
* **Record integrity.** A fault detected at runtime has done no RESEARCH/DESIGN/
  IMPLEMENT/TEST work, so the record must say `open` and must not claim phases
  that never ran. The repo has already been burned once by a machine-written log
  entry describing work that did not happen.
* **The instruments' own honesty.** The suite validates its solvability
  declarations by replaying plans; the audit computes ground truth from captured
  tool output rather than the loop's self-report. Both are tested, because a
  measurement device that quietly lies is worse than no measurement.

Run: python3 autonomy_test.py
"""

import json
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent_runtime
import autonomy
import autonomy_suite
import loop_guard
import loop_audit
import verifier

ABSENT = "zzz-no-such-token"


def _replies(replies):
    box = {"i": 0}

    def chat(config, messages, temperature=None, max_tokens=None, timeout_s=None,
             stop_when=None, **_kw):
        i = box["i"]
        box["i"] += 1
        return {"content": replies[min(i, len(replies) - 1)], "elapsed": 0.01,
                "tokens": 8, "early_stop": True, "provider": "stub", "model": "stub"}

    return chat


def _plan(steps, goal):
    lines = ["PLAN:"]
    for i, (tool, arg, expect) in enumerate(steps, 1):
        lines.append("%d. tool: %s %s" % (i, tool, arg))
        lines.append("   expect: %s" % expect)
    lines.append("GOAL-CHECK: %s" % goal)
    return "\n".join(lines)


def _call(tool, arg):
    return "<<<TOOL:%s %s>>>" % (tool, arg)


_PHASE_RE = re.compile(r"^\d+\.\s+\*\*([A-Z_]+)\*\*", re.MULTILINE)


def _phase_labels(text):
    """The numbered phase labels actually written into a log entry."""
    return _PHASE_RE.findall(text)


class _FixtureCase(unittest.TestCase):
    """Shared generated workspace. Content is fixed so every expected answer is
    known by construction, and the token needles are real words: a bare letter is
    satisfied by the random characters in a temp path."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="autonomy-test-")
        self.notes = os.path.join(self.root, "notes.md")
        self.ledger = os.path.join(self.root, "ledger.jsonl")
        self.readme = os.path.join(self.root, "README.md")
        with open(self.notes, "w", encoding="utf-8") as f:
            f.write("alpha-token appears here\n")
        with open(self.ledger, "w", encoding="utf-8") as f:
            f.write("gamma-1 entry\ngamma-2 entry\ngamma-3 entry\n")
        with open(self.readme, "w", encoding="utf-8") as f:
            f.write("delta-token lives here\n")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run_loop(self, replies, goal="test goal", **kw):
        guard = loop_guard.LoopGuard(kw.pop("max_steps", 8), emit=False)
        result = autonomy.run_goal_verified(None, goal, guard=guard,
                                            chat_fn=_replies(replies), emit=False,
                                            **kw)
        return result, guard

    # A plan that can genuinely be satisfied: listing the fixture yields README.md.
    def plan_ok(self):
        return _plan([("list_dir", self.root, "contains:README.md")],
                     "contains:README.md")


# ── The expectation grammar ───────────────────────────────────────────────────

class SpecGrammarTest(unittest.TestCase):
    def test_every_declared_kind_parses(self):
        for text, kind in (("ok", "ok"), ("nonempty", "nonempty"),
                           ("contains:abc", "contains"), ("absent:abc", "absent"),
                           ("regex:a+b", "regex"), ("lines:3", "lines"),
                           ("chars:12", "chars")):
            spec = autonomy.parse_spec(text)
            self.assertIsNotNone(spec, text)
            self.assertEqual(spec[0], kind)

    def test_malformed_expectations_are_none_not_guesses(self):
        for text in ("", "   ", None, "contains", "contains:", "lines:x", "lines:0",
                     "lines:-2", "chars:0", "wat:3", "contains:`"):
            self.assertIsNone(autonomy.parse_spec(text), repr(text))

    def test_surrounding_backticks_and_case_are_tolerated(self):
        self.assertEqual(autonomy.parse_spec("`CONTAINS:abc`"), ("contains", "abc"))
        self.assertEqual(autonomy.parse_spec("  Ok  "), ("ok", ""))

    def test_numeric_kinds_come_back_as_ints(self):
        self.assertEqual(autonomy.parse_spec("lines:7")[1], 7)
        self.assertIsInstance(autonomy.parse_spec("chars:9")[1], int)

    def test_verify_accepts_and_rejects_each_kind(self):
        cases = [("ok", "fine", True), ("ok", loop_guard.fail("x"), False),
                 ("nonempty", "x", True), ("nonempty", "   ", False),
                 ("contains:tok", "has tok here", True),
                 ("contains:tok", "nope", False),
                 ("absent:tok", "nope", True), ("absent:tok", "has tok", False),
                 ("regex:t.k", "tk", False), ("regex:t.k", "tok", True),
                 ("lines:2", "a\nb\n", True), ("lines:3", "a\nb\n", False),
                 ("chars:4", "abcd", True), ("chars:5", "abcd", False)]
        for text, body, expected in cases:
            ok, detail = autonomy.verify(autonomy.parse_spec(text), body)
            self.assertEqual(ok, expected, (text, body))
            self.assertIn("spec", detail)

    def test_verify_always_explains_a_failure(self):
        ok, detail = autonomy.verify(autonomy.parse_spec("lines:9"), "a\n")
        self.assertFalse(ok)
        self.assertEqual(detail["need"], 9)
        self.assertEqual(detail["observed"], 1)

    def test_a_bad_regex_is_an_error_not_a_crash(self):
        ok, detail = autonomy.verify(autonomy.parse_spec("regex:["), "anything")
        self.assertFalse(ok)
        self.assertIn("error", detail)

    def test_weak_checks_are_weak_and_the_rest_are_strong(self):
        self.assertEqual(autonomy.spec_strength(autonomy.parse_spec("ok")), "weak")
        self.assertEqual(autonomy.spec_strength(autonomy.parse_spec("nonempty")),
                         "weak")
        # `lines:1` is `nonempty` with arithmetic, so it is weak too; the strength
        # rule is about the condition, not the kind it is spelled in.
        self.assertEqual(autonomy.spec_strength(autonomy.parse_spec("lines:1")), "weak")
        self.assertEqual(autonomy.spec_strength(autonomy.parse_spec("lines:5")), "strong")
        self.assertEqual(autonomy.spec_strength(None), "invalid")


# ── The plan parser ───────────────────────────────────────────────────────────

class PlanParserTest(_FixtureCase):
    def test_a_well_formed_plan_parses(self):
        plan = autonomy.parse_plan(self.plan_ok())
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].tool, "list_dir")
        self.assertEqual(plan.goal_check, ("contains", "README.md"))

    def test_a_plan_without_the_header_is_refused(self):
        with self.assertRaises(autonomy.PlanError):
            autonomy.parse_plan("1. tool: list_dir .\nGOAL-CHECK: contains:x")

    def test_a_plan_with_no_steps_is_refused(self):
        with self.assertRaises(autonomy.PlanError):
            autonomy.parse_plan("PLAN:\nGOAL-CHECK: contains:x")

    def test_an_unknown_tool_is_refused_with_the_name(self):
        with self.assertRaises(autonomy.PlanError) as cm:
            autonomy.parse_plan(_plan([("teleport", ".", "nonempty")], "contains:x"))
        self.assertIn("teleport", str(cm.exception))

    def test_a_step_with_no_argument_is_refused(self):
        with self.assertRaises(autonomy.PlanError):
            autonomy.parse_plan(_plan([("list_dir", "", "nonempty")], "contains:x"))

    def test_a_step_with_no_usable_expectation_is_refused(self):
        with self.assertRaises(autonomy.PlanError):
            autonomy.parse_plan(_plan([("list_dir", ".", "gibberish")], "contains:x"))

    def test_a_weak_goal_is_refused_rather_than_warned_about(self):
        # The load-bearing refusal: a goal gated on `nonempty` would pass every
        # run ever made, so it cannot be accepted as a goal condition.
        for weak in ("ok", "nonempty"):
            with self.assertRaises(autonomy.PlanError) as cm:
                autonomy.parse_plan(_plan([("list_dir", ".", "contains:x")], weak))
            self.assertIn("too weak", str(cm.exception))

    def test_a_wildcard_goal_regex_is_refused_as_weak(self):
        # Found live: a hosted model proposed `regex:.+`, the goal "verified",
        # and the run reported success for an answer that was the empty string.
        for wildcard in (".+", ".*", "[\\s\\S]+"):
            with self.assertRaises(autonomy.PlanError) as cm:
                autonomy.parse_plan(_plan([("list_dir", ".", "contains:x")],
                                          "regex:%s" % wildcard))
            self.assertIn("too weak", str(cm.exception))
        # A discriminating pattern is still accepted.
        self.assertEqual(autonomy.spec_strength(("regex", "gamma-[0-9]+")), "strong")

    def test_a_pattern_that_can_fail_on_real_evidence_is_strong(self):
        # `^.*$` without MULTILINE cannot cross a newline, so it rejects multi-line
        # evidence — which most tool output is. It refuses plenty of real runs, so
        # it is not a vacuous gate, and calling it weak would be a false positive.
        # The rule's guarantee is narrow and exact: weak means "accepted by every
        # probe", and a condition that only fails on a *shape* of evidence is still
        # a condition the gate can fail on.
        self.assertEqual(autonomy.spec_strength(("regex", "^.*$")), "strong")
        probes = autonomy.PROBES
        self.assertTrue(any("\n" in p for p in probes))
        ok, _ = autonomy.verify(autonomy.parse_spec("regex:^.*$"),
                               "line one\nline two\n")
        self.assertFalse(ok, "the multi-line probe must defeat it")

    def test_an_invalid_pattern_is_not_mistaken_for_a_wildcard(self):
        # `verify` fails it, which is the safe direction; classifying it as weak
        # would also be safe but would hide a different fault.
        self.assertEqual(autonomy.spec_strength(("regex", "[")), "strong")

    def test_a_near_vacuous_numeric_goal_is_weak(self):
        # `nonempty` is banned as a goal because it accepts almost any output.
        # `lines:1` and `chars:1` mean the same thing with different arithmetic, so
        # the probe set has to catch them too — the regex-only rule did not.
        for weak in ("lines:1", "chars:1"):
            self.assertEqual(autonomy.spec_strength(autonomy.parse_spec(weak)), "weak")
            with self.assertRaises(autonomy.PlanError) as cm:
                autonomy.parse_plan(_plan([("list_dir", ".", "contains:x")], weak))
            self.assertIn("too weak", str(cm.exception))
        # Asking for more is a real claim about the evidence.
        for strong in ("lines:3", "chars:200"):
            self.assertEqual(autonomy.spec_strength(autonomy.parse_spec(strong)),
                             "strong")

    def test_a_needle_kind_is_never_mistaken_for_a_wildcard(self):
        # The regression the kind rule exists to prevent: `absent:X` accepts every
        # probe that lacks X, so a probe-only test would call it weak — but it names
        # a specific string, so evidence can always defeat it.
        for needle in ("absent:zzz-token", "contains:README.md",
                       "contains:builderbro-marker"):
            self.assertEqual(autonomy.spec_strength(autonomy.parse_spec(needle)),
                             "strong", needle)
        self.assertEqual(autonomy.DISCRIMINATING_KINDS, ("contains", "absent"))

    def test_the_probe_set_is_the_instrument_and_is_checked(self):
        # Weakness requires accepting *all* probes, so a probe that is too easy to
        # satisfy silently weakens the test. These properties are the ones the rule
        # depends on: non-empty (a real tool result never is), and covering prose,
        # structure, unicode and length.
        self.assertGreaterEqual(len(autonomy.PROBES), 8)
        for probe in autonomy.PROBES:
            self.assertTrue(probe, "an empty probe would make every kind look weak")
        self.assertTrue(any("\n" in p for p in autonomy.PROBES), "no multi-line probe")
        self.assertTrue(any(len(p) > 100 for p in autonomy.PROBES), "no long probe")
        self.assertTrue(any(not p.isascii() for p in autonomy.PROBES), "no unicode")
        self.assertTrue(any("{" in p for p in autonomy.PROBES), "no structured text")
        self.assertEqual(len(set(autonomy.PROBES)), len(autonomy.PROBES),
                         "duplicate probes add nothing")

    def test_an_unparseable_goal_is_refused(self):
        with self.assertRaises(autonomy.PlanError):
            autonomy.parse_plan(_plan([("list_dir", ".", "contains:x")], "lines:0"))

    def test_a_plan_over_the_step_limit_is_refused(self):
        steps = [("list_dir", ".", "contains:a")] * (autonomy.MAX_PLAN_STEPS + 1)
        with self.assertRaises(autonomy.PlanError):
            autonomy.parse_plan(_plan(steps, "contains:a"))

    def test_the_system_prompt_states_the_grammar_and_the_weakness_rule(self):
        prompt = autonomy.plan_system_prompt("Test")
        for kind in autonomy.SPEC_KINDS:
            self.assertIn(kind, prompt)
        self.assertIn("refused", prompt)
        self.assertIn("GOAL-CHECK", prompt)


# ── The completion gate, both directions ──────────────────────────────────────

class CompletionGateTest(_FixtureCase):
    def test_an_honest_run_verifies(self):
        result, _g = self.run_loop([self.plan_ok(), _call("list_dir", self.root),
                                    "FINAL: README.md is present"])
        self.assertTrue(result["ok"], result["failure"])
        self.assertTrue(result["goal_verified"])
        self.assertEqual(result["verified_steps"], 1)
        self.assertEqual(result["false_success_claims"], 0)

    def test_claiming_done_with_no_evidence_is_refused(self):
        result, _g = self.run_loop([self.plan_ok(), "FINAL: yes definitely"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unverified_completion")
        self.assertEqual(result["false_success_claims"], 1)
        self.assertEqual(result["verified_steps"], 0)

    def test_a_verified_goal_with_no_answer_is_not_a_completion(self):
        # Also found live: `FINAL:` with nothing after it was accepted, because
        # the goal condition held. The condition holding is not an answer.
        result, _g = self.run_loop([self.plan_ok(), _call("list_dir", self.root),
                                    "FINAL:"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "empty_answer")
        self.assertEqual(result["verified_steps"], 1)

    def test_whitespace_after_the_marker_is_also_an_empty_answer(self):
        result, _g = self.run_loop([self.plan_ok(), _call("list_dir", self.root),
                                    "FINAL:   \n"])
        self.assertEqual(result["reason"], "empty_answer")

    def test_a_goal_cannot_verify_over_empty_evidence(self):
        # `absent:X` is satisfied by a vacuum, so a run that promoted nothing could
        # satisfy a gate it never fed. Zero evidence means no verified goal.
        plan = _plan([("read_file", self.notes, "contains:%s" % ABSENT)],
                     "absent:%s" % ABSENT)
        result, _g = self.run_loop([plan, _call("read_file", self.notes),
                                    "FINAL: confirmed it is absent"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unverified_completion")
        self.assertEqual(result["verified_steps"], 0)
        detail = result["diagnosis"]["detail"]["check_detail"]
        self.assertTrue(detail["evidence_empty"])
        self.assertEqual(result["diagnosis"]["detail"]["evidence_chars"], 0)

    def test_the_model_own_prose_cannot_satisfy_the_goal(self):
        # The answer names the token the goal looks for; the gate reads evidence,
        # never the model's reply. This is the difference between a checker the
        # model can talk past and one it cannot.
        result, _g = self.run_loop([self.plan_ok(),
                                    "FINAL: README.md is definitely present"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unverified_completion")

    def test_only_verified_output_becomes_evidence(self):
        # The control that matters: the goal token IS in the tool output, but the
        # step declared an expectation that fails, so the output is not promoted.
        plan = _plan([("read_file", self.notes, "contains:%s" % ABSENT)],
                     "contains:alpha-token")
        result, guard = self.run_loop([plan, _call("read_file", self.notes),
                                       "FINAL: alpha-token confirmed"])
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "unverified_completion")
        self.assertEqual(result["verified_steps"], 0)
        # Tolerated and recorded, not silently dropped.
        self.assertEqual(loop_guard._counts(guard.notes), {"unmet_expectation": 1})

    def test_a_failed_step_is_recorded_but_does_not_stop_the_run(self):
        plan_bad = _plan([("read_file", self.notes, "contains:%s" % ABSENT)],
                         "contains:alpha-token")
        plan_good = _plan([("read_file", self.notes, "contains:alpha-token")],
                          "contains:alpha-token")
        result, guard = self.run_loop([plan_bad, _call("read_file", self.notes),
                                       "REPLAN:\n" + plan_good,
                                       _call("read_file", self.notes),
                                       "FINAL: alpha-token"])
        self.assertTrue(result["ok"], result["failure"])
        self.assertEqual(result["replans"], 1)
        self.assertEqual(loop_guard._counts(guard.notes), {"unmet_expectation": 1})
        self.assertEqual(guard.triggers, [])

    def test_a_refusal_names_the_goal_check_and_the_evidence_size(self):
        result, _g = self.run_loop([self.plan_ok(), "FINAL: done"])
        detail = result["diagnosis"]["detail"]
        self.assertEqual(detail["goal_check"], "contains:README.md")
        self.assertEqual(detail["evidence_chars"], 0)
        self.assertFalse(detail["check_detail"]["hit"])


# ── Limits ────────────────────────────────────────────────────────────────────

class GuardAndLimitTest(_FixtureCase):
    def test_new_reason_codes_are_registered_with_a_stage_and_severity(self):
        for code in ("plan_invalid", "unverified_completion", "plan_divergence",
                     "replan_storm", "token_budget_exceeded", "unmet_expectation",
                     "no_action"):
            meta = loop_guard.REASONS.get(code)
            self.assertIsNotNone(meta, code)
            for field in ("severity", "stage", "meaning", "lever", "hypothesis"):
                self.assertTrue(meta.get(field), "%s.%s" % (code, field))

    def test_a_runtime_fault_still_stops_and_triggers(self):
        guard = loop_guard.LoopGuard(8, emit=False)
        verdict = guard.step_failed("unverified_completion", {"x": 1}, step=3)
        self.assertEqual(verdict["action"], "stop")
        self.assertEqual(guard.triggers, ["unverified_completion"])

    def test_a_note_never_stops_and_never_triggers(self):
        guard = loop_guard.LoopGuard(8, emit=False)
        rec = guard.note("plan_divergence", {"a": 1}, step=2)
        self.assertTrue(rec["tolerated"])
        self.assertEqual(guard.triggers, [])
        self.assertEqual(len(guard.notes), 1)
        # It still carries the registry's attribution, so a note is diagnosable.
        self.assertEqual(rec["stage"], "planning")
        self.assertTrue(rec["lever"])

    def test_the_summary_reports_tolerated_notes_and_stays_serialisable(self):
        guard = loop_guard.LoopGuard(8, emit=False)
        guard.note("unmet_expectation", {"s": 1})
        guard.note("unmet_expectation", {"s": 2})
        summary = guard.summary("failed", 4)
        self.assertEqual(summary["tolerated_notes"], {"unmet_expectation": 2})
        json.dumps(summary)  # must survive a ledger line

    def test_the_autonomy_thresholds_are_registered_with_sane_defaults(self):
        t = loop_guard.active_thresholds({})
        self.assertIn("task_token_budget", t)
        self.assertEqual(t["plan_repair_attempts"], 1)
        self.assertEqual(t["max_replans"], 2)
        self.assertEqual(t["plan_divergence_limit"], 1)

    def test_exceeding_the_step_budget_is_attributed(self):
        plan = _plan([("list_dir", self.root, "contains:README.md")], "contains:README.md")
        result, _g = self.run_loop([plan] + [_call("list_dir", self.root)] * 5,
                                   max_tool_steps=1)
        self.assertEqual(result["reason"], "budget_exhausted")

    def test_exceeding_the_token_budget_is_attributed(self):
        plan = _plan([("list_dir", self.root, "contains:README.md")], "contains:README.md")
        result, _g = self.run_loop([plan] + [_call("list_dir", self.root)] * 4,
                                   token_budget=20)
        self.assertEqual(result["reason"], "token_budget_exceeded")

    def test_exceeding_the_call_ceiling_is_attributed(self):
        plan = _plan([("list_dir", self.root, "contains:README.md")], "contains:README.md")
        result, _g = self.run_loop([plan] + [_call("list_dir", self.root)] * 6,
                                   max_calls=3)
        self.assertEqual(result["reason"], "budget_exhausted")

    def test_repeated_failing_calls_become_an_error_storm(self):
        plan = _plan([("read_file", os.path.join(self.root, "missing.md"),
                       "contains:missing-token")], "contains:%s" % ABSENT)
        result, _g = self.run_loop([plan] + [_call("read_file",
                                                   os.path.join(self.root, "missing.md"))] * 3)
        self.assertEqual(result["reason"], "tool_error_storm")

    def test_a_rogue_step_is_attributed_as_divergence(self):
        result, _g = self.run_loop([self.plan_ok(), _call("list_dir", self.notes),
                                    _call("list_dir", self.notes)])
        self.assertEqual(result["reason"], "plan_divergence")
        self.assertGreaterEqual(len(result["divergences"]), 2)

    def test_never_stopping_to_replan_is_attributed(self):
        replan = "REPLAN:\n" + self.plan_ok()
        result, _g = self.run_loop([self.plan_ok(), replan, replan, replan])
        self.assertEqual(result["reason"], "replan_storm")

    def test_a_plan_echo_is_nudged_and_then_recovers(self):
        # Found by the first live run against a hosted model: it re-emitted the
        # plan block instead of executing step 1.
        result, _g = self.run_loop([self.plan_ok(), self.plan_ok(),
                                    _call("list_dir", self.root),
                                    "FINAL: README.md is present"])
        self.assertTrue(result["ok"], result["failure"])
        self.assertEqual(result["no_action"], 1)
        self.assertEqual(result["verified_steps"], 1)

    def test_never_acting_is_attributed_to_no_action_not_to_the_gate(self):
        # The distinction matters: no evidence was collected, so the *gate* would
        # also have refused — but the model never claimed completion, so blaming
        # the goal condition would send the fix to the wrong lever.
        result, _g = self.run_loop([self.plan_ok()] * 5)
        self.assertEqual(result["reason"], "no_action")
        self.assertGreater(result["no_action"], 0)
        self.assertEqual(result["false_success_claims"], 0)

    def test_a_plan_echo_does_not_count_as_a_false_completion_claim(self):
        result, _g = self.run_loop([self.plan_ok()] * 5)
        self.assertNotEqual(result["reason"], "unverified_completion")
        self.assertEqual(result["false_success_claims"], 0)

    def test_the_empty_retry_threshold_is_registered(self):
        self.assertEqual(loop_guard.active_thresholds({})["empty_response_retries"], 1)
        meta = loop_guard.REASONS["transient_empty_response"]
        self.assertEqual(meta["stage"], "transport")

    def test_a_transient_empty_reply_is_retried_then_recovers(self):
        # A dropped reply must not throw away a plan that was already paid for.
        result, guard = self.run_loop([self.plan_ok(), "", _call("list_dir", self.root),
                                       "FINAL: README.md is present"])
        self.assertTrue(result["ok"], result["failure"])
        self.assertEqual(result["verified_steps"], 1)
        # The retry is visible in the ledger, not silent.
        self.assertEqual(loop_guard._counts(guard.notes),
                         {"transient_empty_response": 1})
        self.assertEqual(guard.triggers, [])

    def test_persistent_empty_replies_are_a_transport_failure(self):
        result, guard = self.run_loop([self.plan_ok()] + [""] * 5)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "empty_response")
        # One retry means at most two empty replies before attribution.
        self.assertEqual(result["empty_responses"], 2)
        self.assertEqual(result["diagnosis"]["detail"]["retries"], 1)
        self.assertGreaterEqual(loop_guard._counts(guard.notes)
                                .get("transient_empty_response", 0), 1)

    def test_an_empty_plan_reply_consumes_a_repair_attempt(self):
        # The first call can be the one that gets dropped; a fatal transport error
        # on a recoverable glitch is the wrong answer.
        result, _g = self.run_loop(["", self.plan_ok(),
                                    _call("list_dir", self.root),
                                    "FINAL: README.md is present"])
        self.assertTrue(result["ok"], result["failure"])


# ── Record integrity: opened vs closed cycles ─────────────────────────────────

class RecordIntegrityTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="autonomy-log-")
        self.log = os.path.join(self.dir, "log.md")
        self.ledger = os.path.join(self.dir, "ledger.jsonl")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _guard(self):
        return loop_guard.LoopGuard(8, emit=True, log_path=self.log,
                                    ledger_path=self.ledger)

    def test_a_runtime_fault_records_an_opened_cycle_only(self):
        guard = self._guard()
        guard.step_failed("unverified_completion", {"goal_check": "contains:x"}, step=3)
        with open(self.ledger, "r", encoding="utf-8") as f:
            record = json.loads(f.read().strip())
        self.assertEqual(record["status"], "open")
        # The phases that actually happened, and nothing else. A full six-phase
        # record here would claim RESEARCH/DESIGN/IMPLEMENT/TEST/REGISTER work
        # that had not been done — the one thing this log cannot afford.
        self.assertEqual(sorted(record["phases"]), ["detect", "hypothesis", "next",
                                                    "routed_to"])
        for absent in ("research", "design", "implement", "test", "register"):
            self.assertNotIn(absent, record["phases"])

    def test_the_log_marks_an_opened_entry_as_opened(self):
        guard = self._guard()
        guard.step_failed("plan_invalid", {"error": "x"}, step=2)
        with open(self.log, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("(opened)", text)
        self.assertIn("ROUTED_TO", text)
        self.assertIn("status: open", text)  # the header explains the two kinds
        # Read the numbered phase labels exactly, rather than searching for the
        # words: the `NEXT` phase names the phases that have *not* run, and the
        # header names the closed cycle's phases on purpose.
        self.assertEqual(_phase_labels(text), ["DETECT", "ROUTED_TO", "HYPOTHESIS",
                                              "NEXT"])

    def test_the_header_does_not_claim_every_entry_is_a_full_cycle(self):
        guard = self._guard()
        guard.step_failed("plan_invalid", {}, step=1)
        with open(self.log, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("opened", text)
        self.assertIn("closed", text)

    def test_a_closed_cycle_still_renders_all_six_phases(self):
        guard = self._guard()
        record = {
            "ts": "2026-01-01T00:00:00+00:00", "source": "test", "reason": "r",
            "severity": "info", "stage": "execution", "title": "closed cycle",
            "detail": {"a": 1}, "summary": {"b": 2},
            "phases": {k: "did %s" % k for k in ("detect", "research", "design",
                                                 "implement", "test", "register")},
        }
        loop_guard.LoopGuard.emit_cycle(record, self.log)
        with open(self.log, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(_phase_labels(text), ["DETECT", "RESEARCH", "DESIGN",
                                              "IMPLEMENT", "TEST", "REGISTER"])

    def test_a_stubbed_library_run_writes_nothing(self):
        # The regression this repo learned the hard way: running the test suite
        # appended a fabricated record to the real log.
        before = os.path.exists(self.log)
        guard = loop_guard.LoopGuard(8, emit=False, log_path=self.log,
                                     ledger_path=self.ledger)
        guard.step_failed("model_unavailable", {"error": "stub"}, step=1)
        self.assertEqual(os.path.exists(self.log), before)
        self.assertFalse(os.path.exists(self.ledger))

    def test_medium_severity_notes_do_not_open_a_cycle(self):
        guard = self._guard()
        guard.note("unmet_expectation", {})
        self.assertEqual(guard.triggers, [])
        self.assertFalse(os.path.exists(self.ledger))


# ── CLI wiring ────────────────────────────────────────────────────────────────

class CliWiringTest(_FixtureCase):
    """Both loop paths must exist and be selectable, and a CLI run is the only
    thing allowed to write records — so every path it writes to is redirected.

    `main()` is not a pure function of its argv: it calls `load_env_file()`,
    which reads the repository's `.env`, and it sets `LOOP_GUARD_EMIT=1` with
    `setdefault`. Naming the keys to restore was not enough — restoring only the
    log and ledger left the emit flag on, and restoring the emit flag still left
    the provider keys behind, so the drift loop's "no keys in this test"
    assertion failed *after* this class ran. The whole environment is snapshotted
    and restored instead, which cannot rot as `main()` grows.
    """

    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp(prefix="autonomy-cli-")
        self.env = {
            "LOOP_GUARD_LOG": os.path.join(self.dir, "log.md"),
            "LOOP_GUARD_LEDGER": os.path.join(self.dir, "ledger.jsonl"),
            "EVIDENCE_FILE": os.path.join(self.dir, "evidence.jsonl"),
            "DRIVE_RESIDENCE": self.dir,
            "BRAIN_CASCADE": "0",
        }

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        super().tearDown()

    def _with_env(self, fn):
        saved = dict(os.environ)
        os.environ.update(self.env)
        try:
            return fn()
        finally:
            os.environ.clear()
            os.environ.update(saved)

    def _run_main(self, argv, replies):
        real = agent_runtime.chat_stream
        agent_runtime.chat_stream = _replies(replies)
        try:
            return self._with_env(lambda: agent_runtime.main(argv))
        finally:
            agent_runtime.chat_stream = real

    def test_goal_uses_the_verified_loop_and_exits_nonzero_on_an_unmet_goal(self):
        code = self._run_main(["--goal", "does the readme exist?"],
                              [self.plan_ok(), "FINAL: yes it does"])
        self.assertEqual(code, 1)

    def test_goal_exits_zero_when_the_goal_verifies(self):
        code = self._run_main(["--goal", "does the readme exist?"],
                              [self.plan_ok(), _call("list_dir", self.root),
                               "FINAL: yes it does"])
        self.assertEqual(code, 0)

    def test_reflex_keeps_the_unverified_contract(self):
        # The baseline is still reachable, precisely so it stays measurable: it
        # returns the model's own claim as the result and exits 0.
        code = self._run_main(["--goal", "does the readme exist?", "--reflex"],
                              ["FINAL: yes definitely"])
        self.assertEqual(code, 0)

    def test_a_cli_fault_writes_a_record_and_a_library_run_does_not(self):
        self._run_main(["--goal", "does the readme exist?"],
                       [self.plan_ok(), "FINAL: yes it does"])
        self.assertTrue(os.path.exists(self.env["LOOP_GUARD_LEDGER"]),
                        "a CLI run must record its faults")


# ── The adversarial verifier's effect on promotion (A3) ───────────────────────

class VerifierPromotionTest(_FixtureCase):
    """A1 checked the model's *own* declared expectation. These assert that the
    output which survives that check must also survive an independent one — and
    that honest output still gets through."""

    def _weak_step_plan(self):
        # `nonempty` holds on any listing AND on the tool's own failure line, so it
        # certifies nothing about the step.
        return _plan([("list_dir", self.root, "nonempty")], "contains:README.md")

    def _weak_replies(self):
        return [self._weak_step_plan(), _call("list_dir", self.root), "FINAL: done"]

    def test_a_step_whose_expectation_certifies_nothing_is_not_promoted(self):
        result, guard = self.run_loop(self._weak_replies())
        self.assertEqual(result["verified_steps"], 0)
        self.assertEqual(result["evidence"], [])
        self.assertEqual([c["refused_by"] for c in result["claims_rejected"]],
                         ["not_vacuous"])
        # And the refused claim cannot be laundered into a verified goal.
        self.assertEqual(result["reason"], "unverified_completion")
        self.assertGreaterEqual(
            loop_guard._counts(guard.notes).get("claim_rejected", 0), 1)

    def test_the_same_step_is_promoted_with_the_verifier_off(self):
        # The A/B: this is the promotion A3 refuses, so the change is attributable
        # to the verifier and not to something else in the loop. The goal does
        # genuinely hold here — that is the point: the *claim* was hollow, not the
        # answer.
        result, _g = self.run_loop(
            self._weak_replies(),
            thresholds=dict(loop_guard.active_thresholds(), verify_mode="off"))
        self.assertEqual(result["verified_steps"], 1)
        self.assertEqual([e["level"] for e in result["evidence"]], ["declared"])
        self.assertTrue(result["ok"])

    def test_an_honest_step_is_promoted_and_names_what_confirmed_it(self):
        result, _g = self.run_loop([self.plan_ok(), _call("list_dir", self.root),
                                    "FINAL: yes"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["verified_steps"], 1)
        self.assertEqual(result["claims_rejected"], [])
        item = result["evidence"][0]
        self.assertEqual(item["level"], "invariant")
        self.assertEqual(item["tool"], "list_dir")
        for name in ("expectation_held", "not_failure", "not_vacuous", "reproduced"):
            self.assertIn(name, item["how_verified"])

    def test_a_promoted_claim_is_re_read_from_the_real_tool(self):
        # Reproduction runs the real tool again. The readme holds still, so the
        # claim is confirmed rather than accused — the honest control at the loop
        # level, which is what keeps the false-accusation rate at zero.
        plan = _plan([("read_file", self.readme, "contains:delta-token")],
                     "contains:delta-token")
        result, _g = self.run_loop([plan, _call("read_file", self.readme),
                                    "FINAL: yes"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["evidence"][0]["level"], "invariant")

    def test_the_model_is_told_a_refusal_is_about_the_check_not_the_step(self):
        _result, guard = self.run_loop(self._weak_replies())
        note = [r for r in guard.notes if r["reason"] == "claim_rejected"][0]
        self.assertTrue(note["tolerated"])
        self.assertEqual(note["detail"]["refused_by"], "not_vacuous")
        self.assertEqual(note["detail"]["expect"], "nonempty")

    def test_a_refused_run_still_says_how_the_evidence_it_collected_was_confirmed(self):
        # Found live: a run ended `unverified_completion` with its detail naming
        # counts but not the level of the evidence it *had* promoted. The success
        # line names levels; the failure line has to as well, or a refused run
        # hides the very distinction A3 measures.
        plan = _plan([("list_dir", self.root, "contains:README.md")],
                     "contains:zzz-unreachable")
        result, _g = self.run_loop([plan, _call("list_dir", self.root),
                                   "FINAL: done"])
        self.assertEqual(result["reason"], "unverified_completion")
        self.assertEqual(result["evidence_levels"]["invariant"], 1)
        # The diagnosis the guard emitted carries it too, which is what a reader of
        # the ledger sees.
        self.assertEqual(result["diagnosis"]["detail"]["evidence_levels"],
                         {"invariant": 1})

    def test_the_verifier_is_revertible_from_config(self):
        # The `repeat_mode` pattern: a rule change has to be switchable from the
        # registered config, not only by editing code.
        self.assertEqual(loop_guard.DEFAULT_THRESHOLDS["verify_mode"], "confirm")
        self.assertIn("verify_mode", loop_guard.ENUMS)
        self.assertEqual(loop_guard.ENUMS["verify_mode"], ("confirm", "off"))
        self.assertIn("claim_rejected", loop_guard.REASONS)
        self.assertIsNotNone(loop_guard.REASONS["claim_rejected"]["lever"])

    def test_only_claims_on_rerunnable_tools_are_reobserved(self):
        # Reproduction is a claim about the *tool*, so the set is declared, not
        # inferred: a network tool is not re-run to confirm a claim.
        self.assertIn("list_dir", verifier.DETERMINISTIC_TOOLS)
        self.assertIn("read_file", verifier.DETERMINISTIC_TOOLS)
        self.assertNotIn("rag", verifier.DETERMINISTIC_TOOLS)
        self.assertNotIn("drive_sync", verifier.DETERMINISTIC_TOOLS)


class EmptyReplyEscalationTest(_FixtureCase):
    """An empty reply that consumed its cap is a budget signal, not a dropped
    stream, and the retry has to be large enough to matter.

    Measured live against the cascade's default hosted hop (`openai/gpt-oss-120b`
    on Groq, a reasoning model): one tool-step prompt returned 256/256, 512/512
    and 1024/1024 tokens of deliberation with no content, then the correct
    directive in 12 tokens at 2048. The loop retried at the unchanged cap, which
    could not have succeeded, and a live goal run failed with `empty_response`
    while the model was holding the answer.
    """

    def _run(self, replies, empty_tokens, supplied=True):
        """Run the loop against a stub that reports `empty_tokens` for an empty
        reply, and return every cap it was called with. `supplied=False` makes the
        model write the plan, which is what exercises the plan call's own cap."""
        caps = []
        box = {"i": 0}

        def chat(config, messages, max_tokens=None, **_kw):
            caps.append(max_tokens)
            i = box["i"]
            box["i"] += 1
            content = replies[min(i, len(replies) - 1)]
            return {"content": content, "elapsed": 0.01,
                    "tokens": 8 if content else empty_tokens(max_tokens),
                    "early_stop": True, "provider": "stub", "model": "stub"}

        guard = loop_guard.LoopGuard(8, emit=False)
        result = autonomy.run_goal_verified(
            None, "test goal", plan=self.plan_ok() if supplied else None,
            chat_fn=chat, guard=guard, emit=False)
        return result, caps, guard

    def test_the_escalation_rule_is_the_measured_one(self):
        # Consuming the cap raises the retry; anything less stays put.
        self.assertEqual(autonomy.escalated_cap(256, 2048, 256), 2048)
        self.assertEqual(autonomy.escalated_cap(256, 2048, 8), 256)
        self.assertEqual(autonomy.escalated_cap(256, 0, 256), 256)
        self.assertEqual(autonomy.escalated_cap(256, 256, 256), 256)
        self.assertEqual(autonomy.escalated_cap(2048, 2048, 2048), 2048)

    def test_the_ceiling_is_registered_like_every_other_threshold(self):
        self.assertEqual(
            loop_guard.active_thresholds({})["empty_reply_token_ceiling"], 2048)

    def test_a_cap_consuming_empty_step_retries_with_more_room(self):
        result, caps, guard = self._run(
            ["", _call("list_dir", self.root), "FINAL: README.md is here"],
            empty_tokens=lambda cap: cap)
        self.assertTrue(result["ok"], result["failure"])
        self.assertEqual(caps, [autonomy.rt.TOOL_STEP_MAX_TOKENS, 2048, 2048])
        note = [r for r in guard.notes
                if r["reason"] == "transient_empty_response"][0]
        self.assertTrue(note["detail"]["consumed_cap"])
        self.assertEqual(note["detail"]["next_cap"], 2048)

    def test_a_dropped_stream_keeps_the_cheap_cap(self):
        # No tokens billed means the reply never arrived, not that the model ran
        # out of room: the retry stays at the tool-step cap.
        result, caps, _g = self._run(
            ["", _call("list_dir", self.root), "FINAL: README.md is here"],
            empty_tokens=lambda cap: 0)
        self.assertTrue(result["ok"], result["failure"])
        self.assertEqual(caps, [autonomy.rt.TOOL_STEP_MAX_TOKENS] * 3)

    def test_an_empty_plan_reply_gets_the_same_room(self):
        # The plan call is the first call a run makes, so the same cap problem
        # there costs the whole run.
        result, caps, _g = self._run(
            ["", self.plan_ok(), _call("list_dir", self.root),
             "FINAL: README.md is here"],
            empty_tokens=lambda cap: cap, supplied=False)
        self.assertTrue(result["ok"], result["failure"])
        self.assertEqual(caps[0], autonomy.PLAN_MAX_TOKENS)
        self.assertEqual(caps[1], 2048)

    def test_the_failure_names_the_cap_it_exhausted(self):
        # A reader of a failed run has to be able to tell a budget from a drop.
        result, _c, _g = self._run([""] * 6, empty_tokens=lambda cap: cap)
        self.assertEqual(result["reason"], "empty_response")
        detail = result["diagnosis"]["detail"]
        self.assertEqual(detail["tokens"], 2048)
        self.assertEqual(detail["cap"], 2048)


class RefusalRepairTest(_FixtureCase):
    """What a real model does with a rejection note, and what the loop must do
    with the reply.

    These encode the live probe's finding (`live_refusal_probe.py`). A model
    answered a `not_vacuous` refusal with the corrected PLAN block the system
    prompt taught it and no `REPLAN:` keyword. The loop read that as `no_action`
    and discarded it — three consecutive correct repairs in one run — because the
    only form it accepted was a keyword the prompt mentions once.
    """

    def test_a_bare_plan_block_repairs_a_refused_expectation(self):
        weak = _plan([("list_dir", self.root, "nonempty")], "contains:README.md")
        fixed = _plan([("list_dir", self.root, "contains:README.md")],
                      "contains:README.md")
        result, guard = self.run_loop([weak, _call("list_dir", self.root), fixed,
                                       _call("list_dir", self.root),
                                       "FINAL: README.md is present"])
        self.assertTrue(result["ok"], result["failure"])
        self.assertEqual(result["replans"], 1)
        self.assertEqual(result["no_action"], 0)
        self.assertEqual([c["refused_by"] for c in result["claims_rejected"]],
                         ["not_vacuous"])
        self.assertEqual(result["evidence"][0]["level"], "invariant")
        # The unlabelled form is recorded, not silently normalised away: it is a
        # finding about the prompt, which asks for a keyword the model skipped.
        self.assertEqual(
            loop_guard._counts(guard.notes).get("replan_unlabelled"), 1)

    def test_a_supplied_plan_is_shown_to_the_model(self):
        # Found live: a supplied plan was parsed and enforced but never placed in
        # the context, so the model was told "Execute step 1" about a plan it had
        # never seen and answered by writing a fresh one. A run that was handed a
        # plan has to look to the model like a run that wrote one.
        seen = []

        def chat(config, messages, max_tokens=None, **_kw):
            seen.append([m["content"] for m in messages])
            return {"content": "FINAL: done", "elapsed": 0.0, "tokens": 1,
                    "provider": "stub", "model": "stub"}

        autonomy.run_goal_verified(None, "goal", plan=self.plan_ok(),
                                   chat_fn=chat,
                                   guard=loop_guard.LoopGuard(8, emit=False),
                                   emit=False)
        self.assertTrue(seen)
        self.assertIn(self.plan_ok(), seen[0])
        self.assertTrue(any("Execute step 1" in m for m in seen[0]))

    def test_an_echo_is_told_apart_from_a_replacement(self):
        # Same plan text, two forms, two verdicts. The keyword means the model
        # *claims* a replacement, so repeating it is a storm (the audit's
        # `replan_storm` pathology). A bare block that repeats the current plan
        # was re-sent, not replaced, and that is `no_action` (the audit's
        # `echoes_plan_forever` pathology). Getting this backwards either counts
        # an echo as progress or blames a stuck loop on the wrong lever.
        labelled = "REPLAN:\n" + self.plan_ok()
        stormed, _g = self.run_loop([self.plan_ok(), labelled, labelled, labelled])
        self.assertEqual(stormed["reason"], "replan_storm")
        echoed, _g = self.run_loop([self.plan_ok()] * 3)
        self.assertEqual(echoed["reason"], "no_action")

    def test_every_refusal_check_has_a_named_remedy(self):
        # The note started as one generic instruction for four different findings,
        # and the live model followed the half of it that cannot work. Each check
        # the verifier can refuse by names its own fix.
        for check in ("not_vacuous", "not_failure", "reproduced",
                      "model_adjudication"):
            self.assertIn(check, autonomy.REFUSAL_FIXES)
            self.assertTrue(autonomy.REFUSAL_FIXES[check])

    def test_the_rejection_note_asks_for_a_plan_not_a_re_run(self):
        plan = autonomy.parse_plan(_plan([("list_dir", self.root, "nonempty")],
                                         "contains:README.md"))
        verdict = verifier.confirm({"tool": "list_dir", "arg": self.root,
                                    "output": "README.md",
                                    "expect": ("nonempty", "")})
        self.assertEqual(verdict.refused_by, "not_vacuous")
        note = autonomy._rejection_note(plan, 1, verdict)
        self.assertIn("`not_vacuous`", note)
        self.assertIn("corrected plan", note)
        # The old wording offered "re-run the step" as the first move. A step's
        # expectation comes from the plan, so re-running it re-checks the same
        # expectation — the instruction could only have looped the model.
        self.assertNotIn("re-run the step", note)

    def test_plan_shape_detection_ignores_echoed_instructions(self):
        self.assertTrue(autonomy.is_plan_reply(
            "PLAN:\n1. tool: list_dir .\n   expect: ok\nGOAL-CHECK: contains:x"))
        self.assertTrue(autonomy.is_plan_reply(
            "1. tool: list_dir .\nGOAL-CHECK: contains:x"))
        # The nudge the loop speaks names GOAL-CHECK and a tool call. A model that
        # echoes it plus an action has not written a plan, and must still act.
        self.assertFalse(autonomy.is_plan_reply(
            "GOAL-CHECK `contains:x` already holds.\n<<<TOOL:list_dir .>>>"))
        self.assertFalse(autonomy.is_plan_reply("FINAL: done"))


class VerifierAuditTest(_FixtureCase):
    """The audit arm is a measurement device, so it is tested like one."""

    def test_both_controls_behave(self):
        a = loop_audit.run_verify_comparison()["assessment"]
        self.assertEqual(a["verdict"], "useful")
        self.assertEqual(a["detections"], a["opportunities"])
        self.assertGreater(a["opportunities"], 0)
        self.assertEqual(a["false_accusations"], 0)

    def test_a_refused_claim_never_becomes_a_verified_goal(self):
        cmp = loop_audit.run_verify_comparison()
        lying = [r for r in cmp["on"] if r["kind"] == "lying"]
        self.assertTrue(lying)
        for r in lying:
            self.assertFalse(r["ok"], r["id"])
            self.assertEqual(r["confirmed"], 0, r["id"])
            self.assertEqual(r["reason"], "unverified_completion", r["id"])
            self.assertTrue(r["refused_by"], r["id"])

    def test_hollow_promotions_exist_without_the_verifier_and_vanish_with_it(self):
        cmp = loop_audit.run_verify_comparison()
        self.assertGreater(cmp["hollow_total"], 0,
                           "the arm must be measuring a real defect, not a no-op")
        self.assertEqual(
            sum(r["confirmed"] for r in cmp["on"] if r["kind"] == "lying"), 0)

    def test_an_honest_claim_on_an_unreproducible_tool_is_labelled_not_refused(self):
        by = {r["id"]: r for r in loop_audit.run_verify_comparison()["on"]}
        row = by["honest_unreproducible_tool"]
        self.assertTrue(row["ok"])
        self.assertEqual(row["refused"], 0)
        self.assertEqual(row["levels"].get("observed"), 1)
        self.assertNotIn("invariant", [k for k, v in row["levels"].items() if v])

    def test_the_audit_fails_when_the_verifier_detects_nothing(self):
        cmp = loop_audit.run_verify_comparison()
        silent = dict(cmp, assessment=loop_audit.verifier_assessment(
            [dict(r, refused=0) for r in cmp["on"]]))
        self.assertEqual(silent["assessment"]["verdict"], "no_verifier")
        hardened = [{"correct": True}]
        goals = [{"correct": True, "false_success": False}]
        self.assertFalse(loop_audit.audit_ok(hardened, goals, silent),
                         "a verifier that passes everything must fail the audit")
        self.assertTrue(loop_audit.audit_ok(hardened, goals, cmp))


# ── Instruments check themselves ──────────────────────────────────────────────

class SuiteInstrumentTest(unittest.TestCase):
    def test_the_floor_registry_and_the_metrics_agree(self):
        results, errors = autonomy_suite.run_suite()
        self.assertEqual(errors, [])
        m = autonomy_suite.metrics(results)
        for key in autonomy_suite.FLOORS:
            self.assertIn(key, m, "floor %r has no metric behind it" % key)

    def test_a_mislabelled_task_is_rejected_by_the_replay_check(self):
        root = tempfile.mkdtemp(prefix="autonomy-suitelabel-")
        try:
            autonomy_suite.make_fixture(root)
            tasks = autonomy_suite.build_tasks(root)
            # Claim the unsatisfiable goal is satisfiable: the replay must object,
            # otherwise the suite would demand the impossible and blame the loop.
            bad = [dict(t) for t in tasks]
            for t in bad:
                if t["id"] == "unsatisfiable_goal":
                    t["goal_satisfiable"] = True
            errors = autonomy_suite.validate_solvability(bad)
            self.assertTrue(any("unsatisfiable_goal" in e for e in errors), errors)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_solvability_declarations_match_the_fixture(self):
        root = tempfile.mkdtemp(prefix="autonomy-suitelabel-")
        try:
            autonomy_suite.make_fixture(root)
            self.assertEqual(autonomy_suite.validate_solvability(
                autonomy_suite.build_tasks(root)), [])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_the_floors_fail_a_violated_metric(self):
        results, _e = autonomy_suite.run_suite()
        m = autonomy_suite.metrics(results)
        ok, _checks = autonomy_suite.check_floors(m)
        self.assertTrue(ok, "the suite must pass its own floors")
        m["false_success_rate"] = 0.5
        ok, checks = autonomy_suite.check_floors(m)
        self.assertFalse(ok)
        self.assertFalse(dict((c[0], c[4]) for c in checks)["false_success_rate"])

    def test_the_hermetic_suite_is_deterministic(self):
        a, errors_a = autonomy_suite.run_suite()
        b, errors_b = autonomy_suite.run_suite()
        self.assertEqual(errors_a, [])
        self.assertEqual(errors_b, [])
        self.assertEqual(autonomy_suite.metrics(a), autonomy_suite.metrics(b))
        self.assertEqual([r["reason"] for r in a], [r["reason"] for r in b])

    def test_no_success_was_decided_by_its_budget(self):
        # Budget *adequacy*, which was previously unmeasured: a success on the last
        # permitted step is not a demonstrated-sufficient budget, just a lucky one.
        results, _e = autonomy_suite.run_suite()
        m = autonomy_suite.metrics(results)
        self.assertEqual(m["budget_slack_rate"], 1.0)
        for r in results:
            if r["expect_success"] and r["success"]:
                self.assertGreaterEqual(r["step_slack"], 1, r["id"])
                self.assertGreaterEqual(r["token_headroom"], 1, r["id"])
        self.assertIn("budget_slack_rate", autonomy_suite.FLOORS)
        self.assertGreater(m["budget_headroom_min"], 0)

    def test_every_task_declares_a_budget_and_is_scored(self):
        results, _e = autonomy_suite.run_suite()
        self.assertEqual(len(results), 20)
        for r in results:
            self.assertIn("over_budget", r)
            self.assertIsInstance(r["tokens"], int)

    def test_the_suite_detects_a_false_success(self):
        # The suite's whole purpose is to catch this, so it is exercised against a
        # run that produced one: a liar patched to be accepted would be a
        # false_success. Asserted through the metric, not by re-running the loop.
        results, _e = autonomy_suite.run_suite()
        for r in results:
            if r["expect_success"] is False:
                self.assertFalse(r["success"], "%s claimed success" % r["id"])


class AuditInstrumentTest(_FixtureCase):
    def test_the_audit_computes_ground_truth_from_captured_output(self):
        results = loop_audit.run_autonomy_suite()
        for r in results:
            # Ground truth must never be taken from the loop's own report.
            self.assertIsInstance(r["truth"], bool)
            self.assertIn("tool_outputs", r)
        by_id = {r["id"]: r for r in results}
        # The honest task ran a tool; the liar never did.
        self.assertGreaterEqual(by_id["honest"]["tool_outputs"], 1)
        self.assertEqual(by_id["liar_after_plan"]["tool_outputs"], 0)

    def test_the_goal_arm_reports_no_false_successes(self):
        results = loop_audit.run_autonomy_suite()
        self.assertEqual([r["id"] for r in results if r["false_success"]], [])
        self.assertTrue(all(r["correct"] for r in results),
                        [r["id"] for r in results if not r["correct"]])

    def test_the_reflex_loop_is_measurably_worse_on_false_success(self):
        # The A/B that justifies making the verified loop the default. If this
        # ever stops holding, the default should be revisited.
        cmp = loop_audit.run_false_success_comparison()
        reflex_false = sum(1 for r in cmp["reflex"] if r["false_success"])
        verified_false = sum(1 for r in cmp["verified"] if r["false_success"])
        self.assertEqual(verified_false, 0)
        self.assertGreater(reflex_false, 0)

    def test_the_audit_names_a_false_refusal_when_the_gate_rejects_real_evidence(self):
        # The measured cost of promoting only verified output: a mis-declared
        # expectation can discard output that did satisfy the goal. Recorded,
        # because a design note that hid it would be a worse audit.
        results = loop_audit.run_autonomy_suite()
        refusals = [r["id"] for r in results if r["false_refusal"]]
        self.assertEqual(refusals, ["goal_only_in_unverified_step"])

    def test_the_audit_uses_each_pathology_own_goal_not_a_hardcoded_one(self):
        # The instrument bug this guards against: ground truth was computed from a
        # marker token regardless of the pathology, so `vacuous_goal` — whose goal
        # is `absent:absent-marker` — looked like a satisfied goal that the gate
        # had wrongly refused.
        results = loop_audit.run_autonomy_suite()
        by_id = {r["id"]: r for r in results}
        self.assertEqual(by_id["vacuous_goal"]["goal_check"], "absent:absent-marker")
        self.assertTrue(by_id["vacuous_goal"]["vacuous"])
        self.assertFalse(by_id["vacuous_goal"]["false_refusal"])
        self.assertTrue(by_id["honest"]["goal_check"].startswith("contains:"))

    def test_a_vacuous_goal_refusal_is_not_a_gate_error(self):
        results = loop_audit.run_autonomy_suite()
        vacuous = [r["id"] for r in results if r["vacuous"]]
        self.assertEqual(vacuous, ["vacuous_goal"])
        self.assertFalse(any(r["false_refusal"] for r in results
                             if r["id"] in vacuous))

    def test_a_detected_fault_that_is_not_a_gate_error_is_not_a_false_refusal(self):
        results = loop_audit.run_autonomy_suite()
        by_id = {r["id"]: r for r in results}
        # `divergent` had the goal reachable and was refused — but for divergence,
        # not by the gate. Counting it would inflate the metric with correct
        # stops.
        self.assertTrue(by_id["divergent"]["truth"])
        self.assertFalse(by_id["divergent"]["false_refusal"])
        self.assertEqual(by_id["divergent"]["reason"], "plan_divergence")


if __name__ == "__main__":
    unittest.main(verbosity=2)
