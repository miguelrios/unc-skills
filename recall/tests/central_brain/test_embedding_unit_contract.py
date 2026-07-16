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

    def test_backfill_oneshot_does_not_time_out_a_valid_cpu_batch(self) -> None:
        unit = (ROOT / "server" / "deploy" / "recall-embedding-backfill.service").read_text()
        self.assertIn("TimeoutStartSec=infinity", unit)

    def test_backfill_has_a_separate_loopback_sidecar(self) -> None:
        worker = (ROOT / "server" / "deploy" / "recall-embedding-backfill.service").read_text()
        sidecar = (
            ROOT / "server" / "deploy" / "recall-embedding-backfill-sidecar.service"
        ).read_text()
        self.assertIn("Requires=recall-embedding-backfill-sidecar.service", worker)
        self.assertIn(
            "ExecStart=/usr/bin/env RECALL_EMBEDDING_URL=http://127.0.0.1:8090 ", worker,
        )
        self.assertIn("RECALL_SEMANTIC_TIMEOUT_SECONDS=120", worker)
        self.assertNotIn("\nEnvironment=RECALL_EMBEDDING_URL=", worker)
        self.assertIn("-p 127.0.0.1:8090:80", sidecar)
        self.assertIn("--max-client-batch-size 1", sidecar)
        self.assertIn("--max-concurrent-requests 1", sidecar)


if __name__ == "__main__":
    unittest.main()
