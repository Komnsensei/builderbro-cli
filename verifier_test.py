#!/usr/bin/env python3
"""verifier_test.py — tests for the adversarial verifier (A3).

Hermetic: no model, no network, no loop. `confirm` is a pure function of a claim
and two callables, so every check is exercised directly — and, more importantly,
so is the **control on the control**: `assess` is tested against a verifier that
detects nothing, to prove a passes-everything verifier is reported as
`no_verifier` instead of as a pass. A verifier nobody can catch passing everything
is not a verifier.

Run: python3 verifier_test.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import autonomy
import loop_guard
import verifier

MARKER = "builderbro-marker"
CLEAN = "index.html\n%s\nREADME.md\n" % MARKER
STABLE = lambda arg: CLEAN            # noqa: E731 - the re-observation of a stable tool


def _claim(tool="list_dir", arg=".", output=CLEAN, expect="contains:%s" % MARKER):
    return {"tool": tool, "arg": arg, "output": output,
            "expect": autonomy.parse_spec(expect) if expect else None}


class CheckTest(unittest.TestCase):
    def test_a_discriminating_claim_reproduces_and_is_invariant(self):
        v = verifier.confirm(_claim(), rerun=STABLE)
        self.assertTrue(v.ok)
        self.assertEqual(v.level, "invariant")
        self.assertIsNone(v.refused_by)
        # The promotion record must name what ran — AGENT-INTEGRITY's rule.
        self.assertEqual(v.how_verified,
                         "list_dir:expectation_held+not_failure+not_vacuous+reproduced")

    def test_every_check_is_recorded_not_summarised(self):
        v = verifier.confirm(_claim(), rerun=STABLE)
        self.assertEqual([c["check"] for c in v.checks],
                         ["expectation_held", "not_failure", "not_vacuous", "reproduced"])
        self.assertTrue(all(c["ok"] is True for c in v.checks))

    def test_the_declared_expectation_is_re_derived_not_taken_on_trust(self):
        # The caller says the expectation holds; the output says otherwise. The
        # verifier must side with the output.
        v = verifier.confirm(_claim(output="nothing relevant here\n"), rerun=STABLE)
        self.assertFalse(v.ok)
        self.assertEqual(v.refused_by, "expectation_held")

    def test_a_failed_call_is_never_a_verified_observation(self):
        # `nonempty` HOLDS on the failure string — which is exactly how a failed
        # call used to be promoted as a verified step.
        failing = loop_guard.fail("not a file: ./missing.txt")
        self.assertTrue(autonomy.verify(autonomy.parse_spec("nonempty"), failing)[0])
        v = verifier.confirm(_claim(tool="read_file", output=failing,
                                    expect="nonempty"))
        self.assertFalse(v.ok)
        # `not_failure` is checked before `not_vacuous` on purpose: the output is
        # refused for what it *is* before anything is said about the expectation
        # that let it through.
        self.assertEqual(v.refused_by, "not_failure")

    def test_a_failure_cannot_be_promoted_even_by_a_specific_needle(self):
        # Ordering matters: `not_failure` is reached only if the expectation held,
        # so the failure check is exercised with a needle the failure text carries.
        failing = loop_guard.fail("marker builderbro-marker inside the failure")
        v = verifier.confirm(_claim(output=failing, expect="contains:builderbro-marker"))
        self.assertFalse(v.ok)
        self.assertEqual(v.refused_by, "not_failure")

    def test_expectations_a_failure_line_would_satisfy_are_refused(self):
        # Each of these holds on ordinary output yet also accepts either the empty
        # string or the tool's own failure line, so it cannot distinguish a usable
        # observation from a busted one. The output is chosen to satisfy the spec,
        # so each case reaches the vacuity check rather than failing at step 1.
        cases = [("nonempty", CLEAN), ("ok", CLEAN), ("regex:.+", CLEAN),
                 ("lines:1", CLEAN), ("chars:5", CLEAN),
                 ("absent:%s" % MARKER, "index.html\nREADME.md\n")]
        for spec, output in cases:
            self.assertTrue(autonomy.verify(autonomy.parse_spec(spec), output)[0],
                            "%s should hold here, or the case proves nothing" % spec)
            v = verifier.confirm(_claim(output=output, expect=spec))
            self.assertFalse(v.ok, "%s should be refused" % spec)
            self.assertEqual(v.refused_by, "not_vacuous", spec)

    def test_a_specific_negative_check_by_a_positive_means_is_fine(self):
        # `contains:` names a needle, so it rejects both counterfactuals even when
        # the needle is absent from a real output: falsifiable, just false.
        v = verifier.confirm(_claim(expect="contains:zzz-not-here"))
        self.assertFalse(v.ok)
        self.assertEqual(v.refused_by, "expectation_held")

    def test_a_claim_without_an_expectation_is_refused(self):
        v = verifier.confirm(_claim(expect=None))
        self.assertFalse(v.ok)
        self.assertEqual(v.refused_by, "expectation_held")

    def test_a_claim_without_a_tool_is_refused(self):
        v = verifier.confirm({"tool": "", "arg": ".", "output": CLEAN,
                              "expect": autonomy.parse_spec("contains:x")})
        self.assertFalse(v.ok)
        self.assertEqual(v.refused_by, "claim_wellformed")

    def test_a_claim_that_does_not_reproduce_is_refused(self):
        # Real once, not re-observable: the drift loop's state.json has this shape,
        # which is why only a LOST expectation is refused — see the test below.
        v = verifier.confirm(_claim(), rerun=lambda a: "index.html\nREADME.md\n")
        self.assertFalse(v.ok)
        self.assertEqual(v.refused_by, "reproduced")

    def test_a_reobservation_that_changes_but_still_holds_is_confirmed(self):
        v = verifier.confirm(_claim(), rerun=lambda a: "%s\nplus a new line\n" % CLEAN)
        self.assertTrue(v.ok)
        self.assertEqual(v.level, "invariant")
        detail = [c for c in v.checks if c["check"] == "reproduced"][0]["detail"]
        self.assertTrue(detail["changed"], "the change must be recorded, not hidden")

    def test_a_tool_that_raises_on_reobservation_is_refused(self):
        def boom(arg):
            raise OSError("device gone")
        v = verifier.confirm(_claim(), rerun=boom)
        self.assertFalse(v.ok)
        self.assertEqual(v.refused_by, "reproduced")

    def test_an_unreproducible_tool_is_observed_not_refused(self):
        v = verifier.confirm(_claim(tool="rag"), rerun=lambda a: CLEAN)
        self.assertTrue(v.ok)
        self.assertEqual(v.level, "observed")
        # `ok: None` — the check did not fail, it was never available. Recording
        # it as a failure would read as a defect.
        rec = [c for c in v.checks if c["check"] == "reproduced"][0]
        self.assertIsNone(rec["ok"])
        self.assertFalse(rec["applicable"])
        self.assertEqual(v.how_verified, "rag:expectation_held+not_failure+not_vacuous")

    def test_observed_is_not_invariant(self):
        with_run = verifier.confirm(_claim(), rerun=STABLE)
        without = verifier.confirm(_claim(tool="rag"))
        self.assertNotEqual(with_run.level, without.level)


class AdjudicationTest(unittest.TestCase):
    """The "second model where not" clause: only the undecided band, only upward."""

    def _judge(self, answer):
        def adjudicate(claim, detail):
            return answer, str(answer)
        return adjudicate

    def test_a_second_model_may_promote_an_unreproducible_claim(self):
        v = verifier.confirm(_claim(tool="rag"), rerun=lambda a: CLEAN,
                             model_adjudication=True, adjudicate=self._judge(True))
        self.assertTrue(v.ok)
        self.assertEqual(v.level, "invariant")
        self.assertIn("model_adjudication", v.how_verified)

    def test_a_second_model_may_refuse_an_unreproducible_claim(self):
        v = verifier.confirm(_claim(tool="rag"), rerun=lambda a: CLEAN,
                             model_adjudication=True, adjudicate=self._judge(False))
        self.assertFalse(v.ok)
        self.assertEqual(v.refused_by, "model_adjudication")

    def test_an_unparsed_ruling_leaves_the_claim_observed(self):
        v = verifier.confirm(_claim(tool="rag"), rerun=lambda a: CLEAN,
                             model_adjudication=True, adjudicate=self._judge(None))
        self.assertTrue(v.ok)
        self.assertEqual(v.level, "observed")

    def test_a_model_cannot_overrule_a_deterministic_refusal(self):
        # A model's opinion does not outrank a machine check. Adjudication is only
        # consulted where the deterministic checks passed.
        calls = []

        def adjudicate(claim, detail):
            calls.append(claim)
            return True, "YES"

        v = verifier.confirm(_claim(expect="nonempty"), model_adjudication=True,
                             adjudicate=adjudicate)
        self.assertFalse(v.ok)
        self.assertEqual(v.refused_by, "not_vacuous")
        self.assertEqual(calls, [], "the second model must never be asked to rescue this")

    def test_adjudication_is_off_unless_asked_for(self):
        v = verifier.confirm(_claim(tool="rag"), rerun=lambda a: CLEAN,
                             adjudicate=self._judge(False))
        self.assertTrue(v.ok)
        self.assertEqual(v.level, "observed")

    def test_the_adjudicator_parses_only_yes_and_no(self):
        def chat(config, messages, max_tokens=None):
            return {"content": "NO: the output does not show a listing\n"}

        adjudicate = verifier.make_adjudicator(chat)
        self.assertEqual(adjudicate(_claim(), {})[0], False)

        def vague(config, messages, max_tokens=None):
            return {"content": "It depends what you mean.\n"}

        self.assertIsNone(verifier.make_adjudicator(vague)(_claim(), {})[0])

    def test_the_adjudication_prompt_carries_the_raw_output(self):
        messages = verifier.adjudication_messages(_claim(), {"needle": MARKER})
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn(MARKER, messages[1]["content"])


class AssessTest(unittest.TestCase):
    """A3's exit criterion. The meta-rule is the test."""

    def test_a_verifier_that_detects_the_lie_and_spares_the_truth_is_useful(self):
        a = verifier.assess(detections=4, opportunities=4,
                            false_accusations=0, honest_opportunities=2)
        self.assertEqual(a["verdict"], "useful")
        self.assertTrue(a["useful"])
        self.assertEqual(a["detection_rate"], 1.0)

    def test_a_verifier_that_detects_nothing_is_no_verifier(self):
        # Not "a weak verifier" — a pass-through. Reported as such so a green
        # audit cannot be bought by confirming everything.
        a = verifier.assess(detections=0, opportunities=4,
                            false_accusations=0, honest_opportunities=2)
        self.assertEqual(a["verdict"], "no_verifier")
        self.assertFalse(a["useful"])

    def test_a_verifier_that_refuses_everything_is_not_a_verifier_either(self):
        a = verifier.assess(detections=4, opportunities=4,
                            false_accusations=2, honest_opportunities=2)
        self.assertEqual(a["verdict"], "too_loud")
        self.assertFalse(a["useful"])

    def test_detecting_some_but_not_all_is_underpowered(self):
        a = verifier.assess(detections=1, opportunities=4,
                            false_accusations=0, honest_opportunities=2)
        self.assertEqual(a["verdict"], "underpowered")
        self.assertFalse(a["useful"])

    def test_an_untested_verifier_is_not_a_passing_one(self):
        a = verifier.assess(detections=0, opportunities=0,
                            false_accusations=0, honest_opportunities=0)
        self.assertEqual(a["verdict"], "untested")
        self.assertFalse(a["useful"])

    def test_the_floors_are_stated_by_the_caller_not_assumed(self):
        a = verifier.assess(detections=1, opportunities=4, false_accusations=0,
                            honest_opportunities=1, detection_floor=0.25)
        self.assertEqual(a["verdict"], "useful")
        self.assertEqual(a["detection_floor"], 0.25)


if __name__ == "__main__":
    unittest.main(verbosity=2)
