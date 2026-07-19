from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

RECALL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RECALL))

from contracts.v2 import validate_contract  # noqa: E402


BASELINE = RECALL / "tests/central_brain/v2_baseline/scoreboard.json"
RETRIEVAL_MANIFEST = RECALL / "tests/central_brain/retrieval_eval_v2/manifest.json"
FEDERATION_MANIFEST = RECALL / "tests/central_brain/federation_eval_v1/manifest.json"


class V2BaselineArtifactsTest(unittest.TestCase):
    def test_baseline_is_closed_content_free_and_manifest_pinned(self) -> None:
        baseline = validate_contract(
            json.loads(BASELINE.read_text()),
            expected="recall.public-evidence.v1",
        )
        manifests = hashlib.sha256(
            hashlib.sha256(RETRIEVAL_MANIFEST.read_bytes()).digest()
            + hashlib.sha256(FEDERATION_MANIFEST.read_bytes()).digest()
        ).hexdigest()
        self.assertEqual(baseline["manifest_sha256"], manifests)
        self.assertEqual(baseline["test_counts"]["baseline_python_passed"], 518)
        self.assertEqual(baseline["test_counts"]["baseline_node_passed"], 3)
        self.assertEqual(baseline["test_counts"]["current_python_passed"], 537)
        self.assertEqual(baseline["test_counts"]["current_node_passed"], 3)

    def test_baseline_records_the_quality_gap_and_safety_floors(self) -> None:
        baseline = json.loads(BASELINE.read_text())
        owner = baseline["metrics"]["owner_holdout"]
        safety = baseline["metrics"]["safety"]
        synthetic = baseline["metrics"]["synthetic_retrieval"]

        self.assertEqual(owner["questions"], 50)
        self.assertEqual(owner["qrels"], 50)
        self.assertEqual(owner["recall@5"], 0.1)
        self.assertGreater(owner["latency_p95_ms"], 0)
        self.assertEqual(safety["duplicate_acknowledged_versions"], 0)
        self.assertEqual(safety["deletion_resurrection_rate"], 0)
        self.assertEqual(safety["unauthorized_hit_rate"], 0)
        self.assertEqual(synthetic["embedding_lag"], 180)
        self.assertEqual(synthetic["semantic_recall@5"], 0)


if __name__ == "__main__":
    unittest.main()
