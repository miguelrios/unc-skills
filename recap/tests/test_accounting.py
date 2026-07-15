import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/recap/scripts"
sys.path.insert(0, str(SCRIPTS))


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ledger = load("event_ledger")
accounting = load("accounting")


def event(ordinal: int) -> dict:
    text = f"event {ordinal}"
    return {
        "ordinal": ordinal,
        "event_id": f"event-{ordinal}",
        "event_native_id": f"native-{ordinal}",
        "item_ordinal": 0,
        "timestamp": float(ordinal),
        "surface": "assistant",
        "role": "assistant",
        "text": text,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "receipt": None,
    }


def manifest_at(root: Path, count: int = 6) -> dict:
    builder = ledger.LedgerBuilder(root / "private/manifest.json", heartbeat_every=0)
    for ordinal in range(count):
        builder.add(event(ordinal))
    return {"ledger": builder.finish()}


def valid_draft() -> dict:
    return {
        "schema_version": accounting.ACCOUNTING_SCHEMA,
        "claims": [
            {
                "claim_id": "claim-goal",
                "kind": "goal",
                "label": "The user set the goal",
                "event_ids": ["event-0"],
            },
            {
                "claim_id": "claim-check",
                "kind": "verification",
                "label": "The agent verified the result",
                "event_ids": ["event-4"],
            },
        ],
        "low_signal_groups": [
            {
                "group_id": "routine-progress",
                "label": "Routine progress and reads",
                "ranges": [[1, 3], [5, 5]],
            },
        ],
    }


class AccountingTest(unittest.TestCase):
    def test_seal_proves_exactly_once_coverage_and_is_deterministic(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = manifest_at(Path(temporary))
            first, first_result = accounting.seal_accounting(manifest, valid_draft())
            second, second_result = accounting.seal_accounting(manifest, valid_draft())
            self.assertTrue(first_result["valid"], first_result["errors"])
            self.assertEqual(first, second)
            self.assertEqual(first_result["event_count"], 6)
            self.assertEqual(first_result["unaccounted_events"], 0)
            self.assertEqual(first_result["duplicate_assignments"], 0)
            self.assertEqual(first["low_signal_groups"][0]["count"], 4)
            self.assertEqual(
                first["event_ledger_sha256"], manifest["ledger"]["events"]["sha256"],
            )

    def test_absent_claim_evidence_and_dropped_events_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = manifest_at(Path(temporary))
            draft = valid_draft()
            draft["claims"][0]["event_ids"] = ["invented"]
            _, result = accounting.seal_accounting(manifest, draft)
            self.assertFalse(result["valid"])
            self.assertGreater(result["unaccounted_events"], 0)
            self.assertGreater(result["missing_claim_evidence"], 0)

    def test_duplicate_claim_evidence_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = manifest_at(Path(temporary))
            draft = valid_draft()
            draft["claims"][1]["event_ids"] = ["event-0", "event-4"]
            _, result = accounting.seal_accounting(manifest, draft)
            self.assertFalse(result["valid"])
            self.assertIn("event evidence is duplicated across claims", result["errors"])

    def test_claim_and_low_signal_overlap_is_multiply_accounted(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = manifest_at(Path(temporary))
            draft = valid_draft()
            draft["low_signal_groups"][0]["ranges"] = [[0, 3], [5, 5]]
            _, result = accounting.seal_accounting(manifest, draft)
            self.assertFalse(result["valid"])
            self.assertEqual(result["duplicate_assignments"], 1)

    def test_overlapping_and_out_of_bounds_ranges_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = manifest_at(Path(temporary))
            overlapping = valid_draft()
            overlapping["low_signal_groups"].append({
                "group_id": "overlap", "label": "Overlap", "ranges": [[3, 3]],
            })
            _, overlap_result = accounting.seal_accounting(manifest, overlapping)
            self.assertFalse(overlap_result["valid"])
            self.assertIn("low-signal ranges overlap", overlap_result["errors"])

            out_of_bounds = valid_draft()
            out_of_bounds["low_signal_groups"][0]["ranges"].append([6, 8])
            _, bounds_result = accounting.seal_accounting(manifest, out_of_bounds)
            self.assertFalse(bounds_result["valid"])
            self.assertTrue(any(
                "extends beyond" in error for error in bounds_result["errors"]
            ))

    def test_seal_tampering_and_significant_event_scores_are_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = manifest_at(Path(temporary))
            sealed, result = accounting.seal_accounting(manifest, valid_draft())
            self.assertTrue(result["valid"])
            sealed["event_ledger_sha256"] = "0" * 64
            self.assertFalse(accounting.validate_accounting(manifest, sealed)["valid"])

        score = accounting.score_significant_events(
            {"event-0", "event-4"}, {"event-0", "event-4"},
        )
        self.assertEqual(score["precision"], 1.0)
        self.assertEqual(score["recall"], 1.0)

    def test_secret_shaped_semantic_labels_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = manifest_at(Path(temporary))
            draft = valid_draft()
            secret = "xai-" + "X" * 40
            draft["claims"][0]["label"] = "Do not render " + secret
            _, result = accounting.seal_accounting(manifest, draft)
            self.assertFalse(result["valid"])
            self.assertIn("accounting contains credential-shaped material", result["errors"])


if __name__ == "__main__":
    unittest.main()
