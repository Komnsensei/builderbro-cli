#!/usr/bin/env python3
"""
policy_search_test.py — hermetic tests for the A6 policy search.

No model, no network: `search()` is exercised both against the real scripted
suite and against a stubbed `evaluate`, so the gate/objective/registration logic
is pinned independently of what the suite happens to score. Registration writes to
a temp config via `LOOP_GUARD_CONFIG`, never the shipped `loop_guard.json`.

Run: python3 policy_search_test.py
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import autonomy_suite
import loop_guard
import policy_search
import test_support

# Never touch the shipped residence / ledger from a test.
test_support.isolate_residence()


def _metrics(accuracy=1.0, tokens=25.0, replans=0.1):
    return {
        "task_accuracy": accuracy,
        "mean_tokens_per_task": tokens,
        "mean_replans_per_completion": replans,
    }


class PolicySearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="policy-search-")
        self.cfg = os.path.join(self.tmp, "loop_guard.json")
        self._saved_env = dict(os.environ)
        os.environ["LOOP_GUARD_CONFIG"] = self.cfg

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ── the candidate space ────────────────────────────────────────────────

    def test_candidates_start_with_the_incumbent_and_dedupe(self):
        incumbent = dict(loop_guard.DEFAULT_THRESHOLDS)
        cands = list(policy_search.candidate_policies(incumbent=incumbent))
        self.assertEqual(cands[0], incumbent)                 # incumbent first
        keys = [tuple(sorted(c.items())) for c in cands]
        self.assertEqual(len(keys), len(set(keys)))           # no duplicates
        # Grid size plus the incumbent, minus one when the incumbent's own knob
        # values are themselves a grid point (they usually are).
        grid = 1
        for values in policy_search.POLICY_SPACE.values():
            grid *= len(values)
        in_grid = all(incumbent[k] in policy_search.POLICY_SPACE[k]
                      for k in policy_search.POLICY_SPACE)
        self.assertEqual(len(cands), 1 + grid - (1 if in_grid else 0))

    def test_candidate_limit_is_refused_loudly(self):
        """A wider space must not silently truncate — that would turn a search
        into a sample and report the sample as the space."""
        with self.assertRaises(ValueError):
            list(policy_search.candidate_policies(limit=3))

    def test_the_default_space_touches_no_frozen_invariant(self):
        overlap = set(policy_search.POLICY_SPACE) & set(policy_search.FROZEN)
        self.assertEqual(overlap, set())

    def test_a_space_over_a_frozen_invariant_is_refused(self):
        """The negative control for the over-fit this search actually committed:
        a space that would tune `plan_repair_attempts` must be refused before any
        candidate is evaluated, not caught later by a red test."""
        with self.assertRaises(ValueError):
            policy_search.validate_space({"plan_repair_attempts": [0]})
        with self.assertRaises(ValueError):
            list(policy_search.candidate_policies(space={"max_replans": [1]}))

    def test_frozen_values_match_what_the_repo_registers(self):
        """FROZEN is only meaningful if it names the values the repo enforces. If
        a registration moves, this fails rather than silently diverging."""
        t = loop_guard.active_thresholds({})
        for knob, (value, _why) in policy_search.FROZEN.items():
            self.assertEqual(
                t[knob], value,
                "FROZEN says `%s` is %r but the repo registers %r" % (knob, value, t[knob]))

    def test_a_candidate_changes_only_the_space_knobs(self):
        incumbent = dict(loop_guard.DEFAULT_THRESHOLDS)
        cands = list(policy_search.candidate_policies(incumbent=incumbent))
        for c in cands:
            changed = {k for k in c if c[k] != incumbent[k]}
            self.assertTrue(changed <= set(policy_search.POLICY_SPACE))

    # ── the objective ──────────────────────────────────────────────────────

    def test_score_is_lexicographic(self):
        # accuracy dominates, then fewer tokens, then fewer replans
        self.assertGreater(policy_search.score(_metrics(accuracy=1.0)),
                           policy_search.score(_metrics(accuracy=0.9, tokens=1.0)))
        self.assertGreater(policy_search.score(_metrics(tokens=10.0)),
                           policy_search.score(_metrics(tokens=20.0)))
        self.assertGreater(policy_search.score(_metrics(replans=0.0)),
                           policy_search.score(_metrics(replans=1.0)))

    # ── the gate and the search ────────────────────────────────────────────

    def test_search_gates_out_floor_failures_and_picks_the_best_eligible(self):
        """A candidate that is more accurate but FAILS a floor must not win, and
        a candidate that passes the floors and scores higher must."""
        incumbent = dict(loop_guard.DEFAULT_THRESHOLDS)

        def fake_evaluate(policy, config=None):
            # A floor-breaking but higher-scoring policy, and a genuine winner.
            if policy["no_action_limit"] == 1:
                return {"ok": False, "score": (1.0, -1.0, -0.0),
                        "metrics": _metrics(accuracy=1.0, tokens=1.0)}
            if policy["no_action_limit"] == 3 and policy["repeat_limit"] == 2:
                return {"ok": True, "score": (1.0, -20.0, -0.1),
                        "metrics": _metrics(tokens=20.0)}
            return {"ok": True, "score": (1.0, -25.0, -0.1), "metrics": _metrics()}

        original = policy_search.evaluate
        policy_search.evaluate = fake_evaluate
        try:
            rep = policy_search.search(incumbent=incumbent)
        finally:
            policy_search.evaluate = original

        self.assertGreater(rep["gated_out"], 0)                # the floor-breaker was seen
        # The floor-breaking candidate scored (1.0, -1.0, 0), which would have won
        # on the objective alone — it must not be the winner.
        self.assertNotEqual(rep["winner"]["policy"]["no_action_limit"], 1)
        self.assertEqual(rep["winner"]["policy"]["no_action_limit"], 3)
        self.assertEqual(rep["winner"]["policy"]["repeat_limit"], 2)

    def test_no_improvement_is_a_reported_outcome(self):
        def flat(policy, config=None):
            return {"ok": True, "score": (1.0, -25.0, -0.1), "metrics": _metrics()}

        original = policy_search.evaluate
        policy_search.evaluate = flat
        try:
            rep = policy_search.search()
        finally:
            policy_search.evaluate = original
        self.assertIsNone(rep["winner"])
        self.assertEqual(rep["eligible"], 0)

    def test_a_failed_suite_is_not_a_losing_policy(self):
        """`None` from evaluate means 'could not measure' — it must never be
        reported as a candidate that scored worse."""
        original = policy_search.evaluate
        policy_search.evaluate = lambda policy, config=None: None
        try:
            rep = policy_search.search()
        finally:
            policy_search.evaluate = original
        self.assertIn("error", rep)

    # ── registration and revert ────────────────────────────────────────────

    def test_register_and_revert_are_byte_exact(self):
        with open(self.cfg, "w", encoding="utf-8") as f:
            f.write(json.dumps({"params": {"max_replans": 2, "repeat_mode": "streak"}}))
        with open(self.cfg, "rb") as f:
            before = f.read()
        policy_search.register({"max_replans": 1}, path=self.cfg)
        with open(self.cfg, "rb") as f:
            self.assertNotEqual(f.read(), before)
        self.assertEqual(policy_search.revert(path=self.cfg), self.cfg)
        with open(self.cfg, "rb") as f:
            self.assertEqual(f.read(), before)                 # byte-exact restore
        self.assertFalse(os.path.exists(self.cfg + policy_search.BACKUP_SUFFIX))

    def test_revert_without_a_snapshot_is_refused(self):
        self.assertIsNone(policy_search.revert(path=self.cfg))

    def test_registered_config_is_read_back_by_the_loop(self):
        policy_search.register({"max_replans": 1, "no_action_limit": 1}, path=self.cfg)
        active = loop_guard.active_thresholds(env={"LOOP_GUARD_CONFIG": self.cfg})
        self.assertEqual(active["max_replans"], 1)
        self.assertEqual(active["no_action_limit"], 1)

    def test_replay_reports_both_policies(self):
        rep = policy_search.replay()
        self.assertIsNotNone(rep["registered_eval"])
        self.assertIsNotNone(rep["default_eval"])

    # ── the suite seam A6 depends on ───────────────────────────────────────

    def test_the_suite_is_replayable_under_a_thresholds_override(self):
        thresholds = dict(loop_guard.DEFAULT_THRESHOLDS)
        thresholds["max_replans"] = 1
        results, errors = autonomy_suite.run_suite(thresholds=thresholds)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
