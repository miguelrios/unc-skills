import contextlib
import hashlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/recap/scripts/event_ledger.py"
spec = importlib.util.spec_from_file_location("event_ledger_under_test", SCRIPT)
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)


def fake_event(ordinal: int, *, surface: str | None = None, text: str | None = None, timestamp=None):
    surface = surface or ("user" if ordinal % 2 == 0 else "assistant")
    text = text if text is not None else f"event {ordinal}"
    return {
        "ordinal": ordinal,
        "event_id": f"event-{ordinal:08d}",
        "event_native_id": f"native-{ordinal:08d}",
        "item_ordinal": 0,
        "timestamp": timestamp if timestamp is not None else float(ordinal),
        "surface": surface,
        "role": surface,
        "text": text,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "receipt": None,
    }


def build(path: Path, count: int, *, heartbeat_every=0):
    builder = ledger.LedgerBuilder(path, heartbeat_every=heartbeat_every)
    for ordinal in range(count):
        builder.add(fake_event(ordinal))
    return builder.finish()


class EventLedgerTest(unittest.TestCase):
    def test_free_text_serializes_after_hash_receipts(self):
        value = {
            "text": "sentry token discussion",
            "text_sha256": "a" * 64,
            "event_id": "rse_" + "b" * 64,
        }
        encoded = ledger.canonical(value).decode()
        self.assertGreater(encoded.index('"text":'), encoded.index('"text_sha256":'))
        self.assertGreater(encoded.index('"text":'), encoded.index('"event_id":'))

    def test_streaming_bundle_accounts_every_event_episode_and_packet_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "private" / "manifest.json"
            bundle = build(manifest, 2501)
            result = ledger.validate_bundle(bundle)
            self.assertTrue(result["valid"], result["errors"])
            self.assertEqual(result["event_count"], 2501)
            self.assertEqual(result["packet_count"], 3)
            self.assertEqual(result["episode_count"], 1251)
            self.assertEqual(
                [item["ordinal"] for item in ledger.packet_events(bundle, "packet-00000001")],
                list(range(1000, 2000)),
            )

    def test_single_huge_episode_uses_incremental_digest_not_event_id_list(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "private" / "manifest.json"
            builder = ledger.LedgerBuilder(manifest, heartbeat_every=0)
            for ordinal in range(10_000):
                builder.add(fake_event(ordinal, surface="assistant"))
                self.assertNotIn("event_ids", builder.episode)
            bundle = builder.finish()
            episodes = list(ledger.iter_jsonl(Path(bundle["episodes"]["path"])))
            self.assertEqual(len(episodes), 1)
            self.assertEqual(episodes[0]["event_count"], 10_000)
            self.assertEqual(
                episodes[0]["event_ids_sha256"],
                ledger.digest_ids([f"event-{ordinal:08d}" for ordinal in range(10_000)]),
            )

    def test_tool_pairing_is_order_inferred_and_repeated_runs_are_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest = Path(temporary) / "private" / "manifest.json"
            builder = ledger.LedgerBuilder(manifest, heartbeat_every=0)
            values = [
                fake_event(0, surface="tool_input", text="same poll"),
                fake_event(1, surface="tool_output", text="unchanged"),
                fake_event(2, surface="assistant", text="repeat"),
                fake_event(3, surface="assistant", text="repeat"),
                fake_event(4, surface="assistant", text="repeat"),
            ]
            for value in values:
                builder.add(value)
            bundle = builder.finish()
            events = list(ledger.iter_jsonl(Path(bundle["events"]["path"])))
            self.assertEqual(events[1]["paired_call_event_id"], events[0]["event_id"])
            self.assertEqual(events[1]["pairing"], "order_inferred")
            repeats = list(ledger.iter_jsonl(Path(bundle["repeat_groups"]["path"])))
            self.assertEqual(repeats[0]["count"], 3)
            self.assertEqual((repeats[0]["first_ordinal"], repeats[0]["last_ordinal"]), (2, 4))

    def test_unchanged_prefix_packet_key_survives_live_tail_append(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"
            first = build(root / "first.json", 1500)
            second = build(root / "second.json", 1501)
            first_packets = list(ledger.iter_jsonl(Path(first["packets"]["path"])))
            second_packets = list(ledger.iter_jsonl(Path(second["packets"]["path"])))
            self.assertEqual(first_packets[0]["content_receipt"], second_packets[0]["content_receipt"])
            self.assertNotEqual(first_packets[1]["content_receipt"], second_packets[1]["content_receipt"])

    def test_same_input_same_path_is_byte_stable(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "private" / "manifest.json"
            first = build(path, 25)
            first_bytes = {
                name: Path(receipt["path"]).read_bytes()
                for name, receipt in first.items()
                if isinstance(receipt, dict) and "path" in receipt
            }
            second = build(path, 25)
            self.assertEqual(first, second)
            self.assertEqual(first_bytes, {
                name: Path(receipt["path"]).read_bytes()
                for name, receipt in second.items()
                if isinstance(receipt, dict) and "path" in receipt
            })

    def test_heartbeat_is_content_free_and_private_modes_are_enforced(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "private" / "manifest.json"
            output = io.StringIO()
            with contextlib.redirect_stderr(output):
                bundle = build(path, 3, heartbeat_every=2)
            self.assertIn('"events": 2', output.getvalue())
            self.assertNotIn("event 1", output.getvalue())
            for receipt in bundle.values():
                if isinstance(receipt, dict) and "path" in receipt:
                    self.assertEqual(Path(receipt["path"]).stat().st_mode & 0o777, 0o600)
            shared = Path(temporary) / "shared"
            shared.mkdir(mode=0o755)
            shared.chmod(0o755)
            with self.assertRaisesRegex(ledger.LedgerError, "0700"):
                ledger.LedgerBuilder(shared / "manifest.json")

    def test_ledger_target_symlink_is_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            private = Path(temporary) / "private"
            private.mkdir(mode=0o700)
            target = private / "target.json.events.jsonl"
            target.write_text("unchanged\n")
            target.chmod(0o600)
            link = private / "manifest.json.events.jsonl"
            link.symlink_to(target)
            with self.assertRaisesRegex(ledger.LedgerError, "symlink"):
                ledger.LedgerBuilder(private / "manifest.json")
            self.assertEqual(target.read_text(), "unchanged\n")

    def test_tampered_ledger_fails_digest_and_evidence_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "private" / "manifest.json"
            bundle = build(path, 3)
            event_path = Path(bundle["events"]["path"])
            raw = event_path.read_text()
            event_path.write_text(raw.replace("event 1", "tampered", 1))
            event_path.chmod(0o600)
            result = ledger.validate_bundle(bundle)
            self.assertFalse(result["valid"])
            self.assertTrue(any("digest mismatch" in error for error in result["errors"]))

    def test_validation_rejects_shared_ledger_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "private" / "manifest.json"
            bundle = build(path, 3)
            path.parent.chmod(0o755)
            result = ledger.validate_bundle(bundle)
            self.assertFalse(result["valid"])
            self.assertTrue(any("parent directory" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
