from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


EVAL = Path(__file__).with_name("eval_v1")
RETRIEVAL_V2 = Path(__file__).with_name("retrieval_eval_v2")


class C0ArtifactsTest(unittest.TestCase):
    def test_frozen_eval_manifest(self) -> None:
        manifest = json.loads((EVAL / "manifest.json").read_text())
        self.assertGreaterEqual(manifest["counts"]["semantic-paraphrase"], 20)
        self.assertGreaterEqual(manifest["counts"]["negative"], 15)
        self.assertGreaterEqual(manifest["counts"]["source-isolation"], 10)
        for name, expected in manifest["sha256"].items():
            self.assertEqual(hashlib.sha256((EVAL / name).read_bytes()).hexdigest(), expected)

    def test_retrieval_v2_manifest_covers_every_frozen_stratum(self) -> None:
        manifest = json.loads((RETRIEVAL_V2 / "manifest.json").read_text())
        expected = {
            "exact-identifier", "semantic-paraphrase", "source-routed",
            "temporal-state", "cross-session-synthesis", "workflow-gotcha",
            "session-reconstruction", "negative-premise", "privacy-auth",
            "deletion-forgetting", "deduplication",
        }
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["counts"]["queries"], 132)
        self.assertEqual(
            manifest["counts"]["dev"] + manifest["counts"]["holdout"],
            manifest["counts"]["queries"],
        )
        self.assertEqual({key for key in manifest["counts"] if key in expected}, expected)
        for stratum in expected:
            self.assertGreaterEqual(manifest["counts"][stratum], 12)
        for name, digest in manifest["sha256"].items():
            self.assertEqual(hashlib.sha256((RETRIEVAL_V2 / name).read_bytes()).hexdigest(), digest)
        self.assertEqual(manifest["contamination_guard"], "pass")

if __name__ == "__main__":
    unittest.main()
