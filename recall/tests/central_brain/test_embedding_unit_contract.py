from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class EmbeddingUnitContractTest(unittest.TestCase):
    def test_cpu_sidecar_rejects_concurrency_and_batching(self) -> None:
        unit = (ROOT / "server" / "deploy" / "recall-embedding.service").read_text()
        self.assertIn("--max-client-batch-size 1", unit)
        self.assertIn("--max-concurrent-requests 1", unit)
        self.assertIn("--max-batch-requests 1", unit)
        self.assertIn("-p 127.0.0.1:8089:80", unit)


if __name__ == "__main__":
    unittest.main()
