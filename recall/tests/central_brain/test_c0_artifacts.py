from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
EVAL = Path(__file__).with_name("eval_v1")


class C0ArtifactsTest(unittest.TestCase):
    def test_frozen_eval_manifest(self) -> None:
        manifest = json.loads((EVAL / "manifest.json").read_text())
        self.assertGreaterEqual(manifest["counts"]["semantic-paraphrase"], 20)
        self.assertGreaterEqual(manifest["counts"]["negative"], 15)
        self.assertGreaterEqual(manifest["counts"]["source-isolation"], 10)
        for name, expected in manifest["sha256"].items():
            self.assertEqual(hashlib.sha256((EVAL / name).read_bytes()).hexdigest(), expected)

    def test_scorecard_has_three_real_measurement_shapes(self) -> None:
        scorecard = json.loads((ROOT / "docs/evidence/c0-backend-decision/scorecard.json").read_text())
        self.assertEqual(len(scorecard["runs"]), 3)
        for run in scorecard["runs"]:
            measured = run["measured"]
            for key in ("setup_seconds", "retrieval_p50_ms", "retrieval_p95_ms", "roundtrip_field_fraction", "receipt_precision_at_1", "source_isolation_leaks", "idempotency_duplicates", "export_field_fraction"):
                self.assertIsNotNone(measured[key])
            self.assertEqual(measured["receipt_precision_at_1"], 1.0)
            self.assertEqual(measured["source_isolation_leaks"], 0)
            self.assertEqual(measured["idempotency_duplicates"], 0)


if __name__ == "__main__":
    unittest.main()
