import json
import tempfile
import unittest
from pathlib import Path

from .study import run_study, write_study


class QIHStudyTest(unittest.TestCase):
    def test_1000_cycle_study_is_reproducible_and_gated(self):
        rows, summary = run_study(cycles=1000, seed=42)
        self.assertEqual(len(rows), 1000)
        self.assertEqual(summary["collapse_gate_passes"], 1000)
        self.assertEqual(summary["collapse_gate_rate"], 1.0)
        self.assertGreaterEqual(summary["coherence_min"], 0.85)
        self.assertEqual(len(summary["chain_head"]), 64)
        again, summary_again = run_study(cycles=1000, seed=42)
        self.assertEqual(rows, again)
        self.assertEqual(summary, summary_again)

    def test_study_records_form_hash_chain(self):
        rows, summary = run_study(cycles=3, seed=7)
        self.assertEqual(rows[0]["previous_hash"], "0" * 64)
        self.assertEqual(rows[1]["previous_hash"], rows[0]["record_hash"])
        self.assertEqual(rows[2]["previous_hash"], rows[1]["record_hash"])
        self.assertEqual(summary["chain_head"], rows[-1]["record_hash"])

    def test_writer_creates_jsonl_and_summary_without_overwrite(self):
        rows, summary = run_study(cycles=3)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "study.jsonl"
            data_path, summary_path = write_study(output, rows, summary)
            self.assertEqual(len(data_path.read_text().splitlines()), 3)
            self.assertEqual(json.loads(summary_path.read_text())["cycles"], 3)
            with self.assertRaises(FileExistsError):
                write_study(output, rows, summary)

    def test_zero_cycles_rejected(self):
        with self.assertRaises(ValueError):
            run_study(cycles=0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
