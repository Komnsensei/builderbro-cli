import json
import math
import tempfile
import unittest
from pathlib import Path

from .unified import (
    append_ledger, born_rule, chain_hash, coherence,
    entanglement_distance, phase_clock, run_cycle,
)


class UnifiedQIHTest(unittest.TestCase):
    def test_born_rule_known_values_and_complement(self):
        self.assertAlmostEqual(born_rule(math.pi / 3), 0.75)
        self.assertAlmostEqual(1.0 - born_rule(math.pi / 3), 0.25)

    def test_runtime_coherence_is_normalized(self):
        self.assertAlmostEqual(coherence([1 + 0j, 1 + 0j]), 2.0)
        result = run_cycle(seed=7, cycle=0)
        self.assertGreaterEqual(result.coherence, 0.0)
        self.assertLessEqual(result.coherence, 1.0)
        self.assertGreaterEqual(result.coherence_before_coupling, 0.0)
        self.assertLessEqual(result.coherence_before_coupling, 1.0)

    def test_default_threshold_is_reachable_across_seed_battery(self):
        results = [run_cycle(cycle=i, seed=42) for i in range(128)]
        self.assertTrue(all(r.coherence >= 0.85 for r in results))
        self.assertTrue(all(r.collapse and r.ok for r in results))
        self.assertTrue(all(r.feedback["action"] == "continue" for r in results))

    def test_zero_coupling_does_not_fake_default_collapse(self):
        result = run_cycle(seed=42, coupling_strength=0.0, threshold=0.85)
        self.assertAlmostEqual(result.coherence, result.coherence_before_coupling)
        self.assertFalse(result.collapse)
        self.assertEqual(result.feedback["action"], "stabilize")
        self.assertEqual(result.experience_norm, 0.0)

    def test_threshold_gate_controls_spectral_stage(self):
        result = run_cycle(seed=42, threshold=1.0)
        self.assertFalse(result.collapse)
        self.assertIn("hold", result.stages)
        self.assertEqual(result.experience_norm, 0.0)

    def test_born_probability_records_expected_and_observed_rates(self):
        result = run_cycle(seed=42)
        self.assertGreaterEqual(result.born_probability, 0.0)
        self.assertLessEqual(result.born_probability, 1.0)
        self.assertGreaterEqual(result.born_observed_probability, 0.0)
        self.assertLessEqual(result.born_observed_probability, 1.0)
        self.assertAlmostEqual(
            result.born_abs_error,
            abs(result.born_observed_probability - result.born_probability),
        )

    def test_previous_cycle_delta_is_recorded(self):
        first = run_cycle(cycle=0, seed=9)
        second = run_cycle(cycle=1, seed=9, previous={"coherence": first.coherence})
        self.assertAlmostEqual(
            second.feedback["delta_coherence"],
            second.coherence - first.coherence,
        )

    def test_invalid_parameters_fail_closed(self):
        for kwargs in (
            {"threshold": 1.1}, {"threshold": -0.1},
            {"coupling_strength": 1.1}, {"coupling_strength": -0.1},
            {"omega": 0}, {"omega0": 0}, {"dt": -1},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    run_cycle(**kwargs)

    def test_distance_proxy_domain(self):
        self.assertEqual(entanglement_distance(1.0), 0.0)
        self.assertAlmostEqual(entanglement_distance(0.5), -math.log(0.5))
        with self.assertRaises(ValueError):
            entanglement_distance(1.1)

    def test_phase_clock_rate_transform(self):
        self.assertAlmostEqual(phase_clock(1.0, 0.5, 2.0), 4.0)

    def test_cycle_is_reproducible_and_discloses_semantics(self):
        a = run_cycle(cycle=4, seed=11)
        b = run_cycle(cycle=4, seed=11)
        self.assertEqual(a.to_dict(), b.to_dict())
        self.assertIn("application threshold", a.metric_semantics["collapse"])
        self.assertIn("not quantum entanglement", a.metric_semantics["entanglement_distance"])

    def test_ledger_round_trip_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_cycle(cycle=2, seed=9)
            record = append_ledger(directory, result)
            rows = (Path(directory) / "ledger.jsonl").read_text().splitlines()
            telemetry = (Path(directory) / "telemetry.jsonl").read_text().splitlines()
            self.assertEqual(len(rows), 1)
            self.assertEqual(len(telemetry), 1)
            loaded = json.loads(rows[0])
            self.assertEqual(loaded["event"], "qih_unified_cycle")
            self.assertEqual(loaded["schema_version"], 2)
            self.assertEqual(loaded["record_hash"], chain_hash(result))
            self.assertEqual(record["record_hash"], loaded["record_hash"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
