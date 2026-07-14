import importlib.util
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "eval_v4/synthesis_gate.py"


def load_gate():
    spec = importlib.util.spec_from_file_location("synthesis_gate_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecapEvalV4Test(unittest.TestCase):
    def test_frozen_corpus_slice_meets_semantic_and_length_gates(self):
        gate = load_gate()
        with tempfile.TemporaryDirectory() as temporary:
            report = gate.evaluate(Path(temporary) / "private", max_events=1100)
        self.assertTrue(report["valid"])
        self.assertEqual(report["significant_action_precision"], 1.0)
        self.assertEqual(report["significant_action_recall"], 1.0)
        self.assertEqual(report["unsupported_factual_claims"], 0)
        self.assertEqual(report["duplicate_assignments"], 0)
        self.assertEqual(report["unaccounted_events"], 0)
        self.assertLessEqual(report["maximum_render_words"], 2_500)


if __name__ == "__main__":
    unittest.main()
