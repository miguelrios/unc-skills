from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path


EVAL = Path(__file__).with_name("eval_v1")


class C0ArtifactsTest(unittest.TestCase):
    def test_frozen_eval_manifest(self) -> None:
        manifest = json.loads((EVAL / "manifest.json").read_text())
        self.assertGreaterEqual(manifest["counts"]["semantic-paraphrase"], 20)
        self.assertGreaterEqual(manifest["counts"]["negative"], 15)
        self.assertGreaterEqual(manifest["counts"]["source-isolation"], 10)
        for name, expected in manifest["sha256"].items():
            self.assertEqual(hashlib.sha256((EVAL / name).read_bytes()).hexdigest(), expected)

if __name__ == "__main__":
    unittest.main()
