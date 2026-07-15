import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "eval_v3/ledger_gate.py"


def load_gate():
    spec = importlib.util.spec_from_file_location("ledger_gate_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecapEvalV3Test(unittest.TestCase):
    def test_frozen_corpus_slice_is_lossless_and_exactly_once(self):
        gate = load_gate()
        with tempfile.TemporaryDirectory() as temporary:
            report = gate.evaluate(Path(temporary) / "private", max_events=1100)
        self.assertTrue(report["valid"])
        self.assertEqual(report["significant_event_precision"], 1.0)
        self.assertEqual(report["significant_event_recall"], 1.0)
        self.assertEqual(report["duplicate_assignments"], 0)
        self.assertEqual(report["unaccounted_events"], 0)
        self.assertIn("huge-100k", report["skipped_cases"])
        self.assertIn("long-10k", report["skipped_cases"])


if __name__ == "__main__":
    unittest.main()
