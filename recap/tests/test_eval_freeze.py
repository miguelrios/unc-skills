import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "eval_v1" / "corpus.json"
MANIFEST = ROOT / "eval_v1" / "manifest.json"


class RecapEvalFreezeTest(unittest.TestCase):
    def test_corpus_is_frozen(self):
        manifest = json.loads(MANIFEST.read_text())
        self.assertEqual(hashlib.sha256(CORPUS.read_bytes()).hexdigest(), manifest["corpus_sha256"])
        corpus = json.loads(CORPUS.read_text())
        self.assertEqual(corpus["schema_version"], 1)
        self.assertEqual(len(corpus["scenarios"]), 8)
        self.assertEqual(len(corpus["baseline_capabilities"]), 12)


if __name__ == "__main__":
    unittest.main()
