#!/usr/bin/env python3
"""
qih_metrics_test.py — deterministic tests for the QIH metric functions
(QIH.md §II). Pure stdlib math only: no numpy, no model, no network.

Run: python3 qih_metrics_test.py
"""

import json
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import qih_metrics
from qih_metrics import (
    born_rule,
    coherence_functional,
    entanglement_distance,
    metric_record,
    phase_clock,
)


class BornRuleTest(unittest.TestCase):
    def test_up_probability_passes(self):
        # θ=60° → P↑ = cos²(30°) = 0.75
        ok, rule, expected = born_rule(0.75, 60)
        self.assertTrue(ok)
        self.assertEqual(rule, "up")
        self.assertAlmostEqual(expected, 0.75, places=6)

    def test_down_probability_passes(self):
        # θ=60° → P↓ = sin²(30°) = 0.25
        ok, rule, expected = born_rule(0.25, 60)
        self.assertTrue(ok)
        self.assertEqual(rule, "down")
        self.assertAlmostEqual(expected, 0.25, places=6)

    def test_degenerate_theta_90_matches(self):
        # θ=90° → both rules are 0.5
        ok, rule, expected = born_rule(0.5, 90)
        self.assertTrue(ok)
        self.assertIn(rule, ("up", "down"))
        self.assertAlmostEqual(expected, 0.5, places=6)

    def test_no_match_fails_structured(self):
        ok, rule, expected = born_rule(0.5, 60)  # 0.5 matches neither 0.75 nor 0.25
        self.assertFalse(ok)
        self.assertIsNone(rule)
        self.assertIsNone(expected)

    def test_tolerance_respected(self):
        ok, _, _ = born_rule(0.7501, 60, tol=1e-3)
        self.assertTrue(ok)
        ok, _, _ = born_rule(0.7501, 60, tol=1e-5)
        self.assertFalse(ok)

    def test_domain_errors(self):
        with self.assertRaises(ValueError):
            born_rule(1.5, 60)
        with self.assertRaises(ValueError):
            born_rule(-0.1, 60)
        with self.assertRaises(ValueError):
            born_rule(0.5, 361)


class EntanglementDistanceTest(unittest.TestCase):
    def test_self_connection_zero(self):
        self.assertEqual(entanglement_distance(1.0), 0.0)
        self.assertEqual(entanglement_distance(2.0), 0.0)  # E ≥ 1 → self-connection

    def test_logarithmic_formula(self):
        # d = −1.0·log(0.8 + 1e-10) ≈ 0.22314
        d = entanglement_distance(0.8)
        self.assertAlmostEqual(d, 0.22314355131420974, places=6)

    def test_alpha_scales(self):
        d1 = entanglement_distance(0.5, alpha_0=1.0)
        d2 = entanglement_distance(0.5, alpha_0=2.0)
        self.assertAlmostEqual(d2, 2.0 * d1, places=6)

    def test_zero_strength_capped(self):
        # E=0 → −log(1e-10) ≈ 23.0258 — low connectivity, large capped distance
        self.assertAlmostEqual(entanglement_distance(0.0), 23.025850929940457, places=6)

    def test_domain_errors(self):
        with self.assertRaises(ValueError):
            entanglement_distance(0.5, alpha_0=0)
        with self.assertRaises(ValueError):
            entanglement_distance(-0.1)


class CoherenceFunctionalTest(unittest.TestCase):
    def test_aligned_states_constructive(self):
        # two identical unit states: |1+1|² / (1+1) = 4/2 = 2.0
        self.assertEqual(coherence_functional([(1.0, 0.0), (1.0, 0.0)]), 2.0)

    def test_opposite_phases_zero(self):
        self.assertEqual(coherence_functional([(1.0, 0.0), (-1.0, 0.0)]), 0.0)

    def test_orthogonal_components(self):
        # (1,0),(0,1): |1+i|² = 2, Σ|m|² = 2 → 1.0
        self.assertEqual(coherence_functional([(1.0, 0.0), (0.0, 1.0)]), 1.0)

    def test_zero_activity_zero_coherence(self):
        self.assertEqual(coherence_functional([(0.0, 0.0), (0.0, 0.0)]), 0.0)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            coherence_functional([])


class PhaseClockTest(unittest.TestCase):
    def test_slower_clock_longer_subjective_time(self):
        # dτ = (Ω₀/Ω)·dt — half the rate → twice the subjective time
        self.assertEqual(phase_clock(1.0, 0.5, dt=1.0), 2.0)

    def test_reference_rate_identity(self):
        self.assertEqual(phase_clock(1.0, 1.0, dt=1.0), 1.0)

    def test_dt_scales(self):
        self.assertEqual(phase_clock(1.0, 2.0, dt=4.0), 2.0)

    def test_domain_errors(self):
        with self.assertRaises(ValueError):
            phase_clock(1.0, 0.0)
        with self.assertRaises(ValueError):
            phase_clock(0.0, 1.0)


class MetricRecordTest(unittest.TestCase):
    def test_record_shape(self):
        rec = metric_record("born-rule", {"p": 0.75, "theta_deg": 60}, {"rule": "up"}, "born-rule:ok")
        self.assertEqual(rec["event"], "metric")
        self.assertEqual(rec["kind"], "born-rule")
        self.assertEqual(rec["gate"], "born-rule:ok")
        self.assertEqual(rec["inputs"]["p"], 0.75)
        self.assertEqual(rec["output"]["rule"], "up")
        self.assertEqual(rec["source"], "qih_metric")
        self.assertIn("ts", rec)
        # serializable — the ledger is JSONL
        json.dumps(rec)


if __name__ == "__main__":
    unittest.main(verbosity=2)