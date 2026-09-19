#!/usr/bin/env python3
"""live_refusal_probe_test.py — the live probe's decision logic, tested hermetically.

The probe itself is the one instrument here that spends real requests, so it is
never run by a test. What *is* testable is everything around the network call: how
the transcript is assembled, and how the model's reply is classified. Both earned
tests the hard way — the first live transcript misattributed one arm's tool output
to the next arm, and the first classifier called a correct repair "the model said
nothing".
"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent_runtime as rt
import live_refusal_probe as probe


class ReactionTest(unittest.TestCase):
    """`classify_reaction` is the measurement, so it is tested as one."""

    def test_every_reply_shape_the_loop_understands(self):
        cases = [
            ("", "empty"),
            ("   ", "empty"),
            ("<<<TOOL:list_dir .>>>", "acted"),
            ("FINAL: done", "claimed_completion"),
            ("REPLAN:\nPLAN:\n1. tool: list_dir .\n   expect: ok\n"
             "GOAL-CHECK: contains:x", "replan"),
            ("PLAN:\n1. tool: list_dir .\n   expect: ok\nGOAL-CHECK: contains:x",
             "replan_unlabelled"),
            ("1. tool: list_dir .\nGOAL-CHECK: contains:x", "replan_unlabelled"),
            ("I am not sure what to do next.", "neither"),
        ]
        for reply, want in cases:
            self.assertEqual(probe.classify_reaction(reply), want, repr(reply))

    def test_a_bare_plan_block_is_not_reported_as_saying_nothing(self):
        # The distinction that was invisible in the first live run: a plan block
        # without the keyword is a repair the model performed. Folding it into
        # `neither` would record correct work as silence — which is what the loop
        # itself did, and what the fix in autonomy.is_plan_reply addresses.
        self.assertNotEqual(
            probe.classify_reaction("PLAN:\n1. tool: list_dir .\n"
                                    "   expect: ok\nGOAL-CHECK: contains:x"),
            "neither")


class RepairTest(unittest.TestCase):
    """`replan_repairs` reads the repair out of the model's own plan, with the real
    parser — a reply that says it repaired does not count as having repaired."""

    def test_a_specific_expectation_repairs_a_vacuity_refusal(self):
        reply = ("PLAN:\n1. tool: list_dir .\n   expect: contains:README.md\n"
                 "GOAL-CHECK: contains:README.md")
        ok, detail = probe.replan_repairs(reply, "not_vacuous")
        self.assertTrue(ok, detail)
        self.assertIn("strong", detail)

    def test_repeating_the_non_discriminating_expectation_repairs_nothing(self):
        reply = ("PLAN:\n1. tool: list_dir .\n   expect: nonempty\n"
                 "GOAL-CHECK: contains:README.md")
        ok, detail = probe.replan_repairs(reply, "not_vacuous")
        self.assertFalse(ok)
        self.assertIn("weak", detail)

    def test_a_failed_call_is_only_repaired_by_acting_on_something_real(self):
        still_broken = ("PLAN:\n1. tool: read_file ./no/such/file.txt\n"
                        "   expect: contains:x\nGOAL-CHECK: contains:x")
        ok, detail = probe.replan_repairs(still_broken, "not_failure")
        self.assertFalse(ok, detail)
        root = tempfile.mkdtemp(prefix="probe-test-")
        try:
            self.addCleanup(shutil.rmtree, root, True)
            real = ("PLAN:\n1. tool: list_dir %s\n   expect: contains:a\n"
                    "GOAL-CHECK: contains:a" % root)
            ok, detail = probe.replan_repairs(real, "not_failure")
            self.assertTrue(ok, detail)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_an_unparseable_replan_is_not_a_repair(self):
        ok, detail = probe.replan_repairs("PLAN:\nGOAL-CHECK: nope", "not_vacuous")
        self.assertFalse(ok)
        self.assertTrue(detail)


class TranscriptTest(unittest.TestCase):
    """The delta cursor is per-run."""

    def test_each_arm_re_reads_its_own_messages(self):
        # Carrying the offset across arms misaligned every delta after the first:
        # the first live transcript credited an arm with the *previous* arm's tool
        # output, and the record that was supposed to show what the model was told
        # showed another run's messages instead.
        calls = []
        real = rt.chat_stream

        def fake(config, messages, max_tokens=None, stop_when=None, **_kw):
            calls.append(list(messages))
            return {"content": "ok", "provider": "stub", "model": "stub",
                    "tokens": 1, "elapsed": 0.0, "attempts": []}

        rt.chat_stream = fake
        try:
            path = os.path.join(tempfile.mkdtemp(prefix="probe-test-"), "t.jsonl")
            self.addCleanup(shutil.rmtree, os.path.dirname(path), True)
            transcript = probe.Transcript(path)
            for arm in ("a", "b"):
                transcript.begin(arm)
                transcript.chat(None, [{"role": "user", "content": arm + "-1"}], 8)
                transcript.chat(None, [{"role": "user", "content": arm + "-1"},
                                       {"role": "user", "content": arm + "-2"}], 8)
        finally:
            rt.chat_stream = real
        self.assertEqual(len(calls), 4)
        # Each record carries only what was newly said before that reply, and the
        # second arm starts from its own beginning rather than the first arm's
        # offset.
        self.assertEqual([[m["content"] for m in r["told"]] for r in transcript.records],
                         [["a-1"], ["a-2"], ["b-1"], ["b-2"]])
        self.assertEqual([r["arm"] for r in transcript.records], ["a", "a", "b", "b"])
        self.assertEqual([r["turn"] for r in transcript.records], [1, 2, 1, 2])

    def test_the_note_is_recovered_verbatim_from_the_record(self):
        # The note is half of what the probe records and is never rebuilt: a
        # summary of the note is not the note, and the model's reply is only
        # interpretable next to what it was actually told.
        transcript = probe.Transcript("unused.jsonl")
        transcript.begin("x")
        transcript.records.append(
            {"arm": "x", "turn": 1, "told": [{"role": "user", "content": "other"}],
             "replied": "whatever"})
        transcript.records.append(
            {"arm": "x", "turn": 2,
             "told": [{"role": "user", "content": "NOT CONFIRMED: step 1/1 ..."}],
             "replied": "PLAN:\n1. tool: list_dir ."})
        note, reply = probe._first_reaction(transcript, 0)
        self.assertIn("NOT CONFIRMED", note)
        self.assertEqual(reply, "PLAN:\n1. tool: list_dir .")


if __name__ == "__main__":
    unittest.main()
