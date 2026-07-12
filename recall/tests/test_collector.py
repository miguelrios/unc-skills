from __future__ import annotations

import json
import hashlib
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from collector.collector import Collector


def claude_line(text: str, timestamp: str = "2026-07-12T22:00:00Z") -> str:
    return json.dumps({"type": "user", "timestamp": timestamp, "message": {"content": text}}) + "\n"


class AckServer(BaseHTTPRequestHandler):
    batches: dict[str, dict] = {}
    drop_after_first_commit = False
    requests = 0
    received: list[dict] = []

    def log_message(self, *_args) -> None:
        pass

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length))
        type(self).received.append(body)
        key = self.headers["Idempotency-Key"]
        type(self).requests += 1
        ack = type(self).batches.get(key)
        replay = ack is not None
        if ack is None:
            ack = {
                "batch_id": "fake-" + str(len(type(self).batches) + 1),
                "status": "committed",
                "inserted": len(body["events"]),
                "duplicate_events": 0,
                "receipts": [f"recall://{e['source_id']}/{e['native_id']}?rev=1" for e in body["events"]],
            }
            type(self).batches[key] = ack
            if type(self).drop_after_first_commit:
                type(self).drop_after_first_commit = False
                self.connection.shutdown(2)
                self.connection.close()
                return
        rendered = json.dumps({**ack, "replay": replay}).encode()
        self.send_response(200 if replay else 201)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(rendered)))
        self.end_headers()
        self.wfile.write(rendered)


class CollectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "claude"
        self.root.mkdir()
        self.spool = Path(self.tmp.name) / "spool.db"
        AckServer.batches = {}
        AckServer.requests = 0
        AckServer.drop_after_first_commit = False
        AckServer.received = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), AckServer)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def collector(self, endpoint: str | None = None) -> Collector:
        return Collector(
            root=self.root,
            harness="claude",
            source_id="claude:linux:test",
            spool_path=self.spool,
            endpoint=endpoint or self.endpoint,
            token="test-token-not-a-secret",
        )

    def test_committed_cursor_waits_for_ack_and_survives_restart(self) -> None:
        transcript = self.root / "session.jsonl"
        transcript.write_text(claude_line("first") + claude_line("second"))
        offline = self.collector("http://127.0.0.1:1")
        scan = offline.scan()
        self.assertEqual(scan["records_queued"], 2)
        self.assertEqual(offline.doctor()["committed_files"], 0)
        self.assertEqual(offline.flush()["acked"], 0)
        offline.close()

        resumed = self.collector()
        self.assertEqual(resumed.flush()["acked"], 2)
        doctor = resumed.doctor()
        self.assertEqual(doctor["pending"], 0)
        self.assertEqual(doctor["committed_files"], 1)
        resumed.close()

    def test_disconnect_after_commit_replays_without_duplicate(self) -> None:
        (self.root / "session.jsonl").write_text(claude_line("durable once"))
        collector = self.collector()
        collector.scan()
        AckServer.drop_after_first_commit = True
        self.assertEqual(collector.flush()["acked"], 0)
        self.assertEqual(collector.doctor()["pending"], 1)
        result = collector.flush()
        self.assertEqual(result["acked"], 1)
        self.assertEqual(result["replayed_batches"], 1)
        self.assertEqual(len(AckServer.batches), 1)
        receipt = next(iter(AckServer.batches.values()))["receipts"][0]
        located = collector.locate_receipt(receipt)
        self.assertEqual(Path(located["path"]).name, "session.jsonl")
        self.assertGreater(located["end_offset"], located["start_offset"])
        collector.close()

    def test_append_queues_only_new_complete_records(self) -> None:
        transcript = self.root / "session.jsonl"
        transcript.write_text(claude_line("first"))
        collector = self.collector()
        collector.scan(); collector.flush()
        with transcript.open("a") as output:
            output.write(claude_line("second"))
            output.write('{"type":"user"')
        scan = collector.scan()
        self.assertEqual(scan["records_queued"], 1)
        self.assertEqual(scan["partial_files"], 1)
        self.assertEqual(collector.flush()["acked"], 1)
        collector.close()

    def test_truncation_queues_tombstone_for_removed_record(self) -> None:
        transcript = self.root / "session.jsonl"
        transcript.write_text(claude_line("first") + claude_line("removed"))
        collector = self.collector(); collector.scan(); collector.flush()
        transcript.write_text(claude_line("first"))
        scan = collector.scan()
        self.assertEqual(scan["tombstones_queued"], 1)
        collector.flush()
        self.assertEqual(collector.doctor()["pending"], 0)
        transcript.write_text(claude_line("first") + claude_line("removed"))
        resurrect = collector.scan()
        self.assertEqual(resurrect["records_queued"], 1)
        collector.flush()
        transcript.write_text(claude_line("first"))
        self.assertEqual(collector.scan()["tombstones_queued"], 1)
        collector.close()

    def test_scope_and_master_key_are_sanitized_and_unrelated_files_ignored(self) -> None:
        (self.root / "session.jsonl").write_text(
            json.dumps({"type": "user", "timestamp": "2026-07-12T22:00:00Z", "message": {"content": "ok\x00after"}, "LITELLM_MASTER_KEY": "forbidden-value"}) + "\n"
        )
        (self.root / "notes.txt").write_text("must not ingest")
        collector = self.collector(); collector.scan()
        payloads = collector.pending_envelopes()
        self.assertEqual(len(payloads), 1)
        rendered = json.dumps(payloads)
        self.assertNotIn("forbidden-value", rendered)
        self.assertIn("[REDACTED]", rendered)
        self.assertNotIn("\x00", payloads[0]["content"]["message"]["content"])
        self.assertIn("[NUL]", payloads[0]["content"]["message"]["content"])
        self.assertNotIn("notes.txt", rendered)
        self.assertEqual(payloads[0]["source_id"], "claude:linux:test")
        collector.close()

    def test_giant_file_resumes_from_durable_scan_checkpoint(self) -> None:
        transcript = self.root / "giant.jsonl"
        transcript.write_text("".join(claude_line(f"record-{index}") for index in range(1001)))
        collector = self.collector()
        save = collector._save_file_progress

        def crash_after_checkpoint(*args, **kwargs):
            save(*args, **kwargs)
            collector.db.commit()
            raise RuntimeError("simulated process death")

        collector._save_file_progress = crash_after_checkpoint
        with self.assertRaisesRegex(RuntimeError, "simulated process death"):
            collector.scan()
        collector.close()

        resumed = self.collector()
        scan = resumed.scan()
        self.assertEqual(scan["records_queued"], 1)
        self.assertEqual(len(resumed.pending_envelopes()), 1001)
        resumed.close()

    def test_flush_repairs_nul_in_an_already_durable_spool_row(self) -> None:
        (self.root / "session.jsonl").write_text(claude_line("before"))
        collector = self.collector(); collector.scan()
        row = collector.db.execute("SELECT id,envelope_json FROM outbox").fetchone()
        envelope = json.loads(row["envelope_json"])
        envelope["content"]["message"]["content"] = "before\x00after"
        envelope["content_sha256"] = hashlib.sha256(
            json.dumps(envelope["content"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        collector.db.execute(
            "UPDATE outbox SET envelope_json=?,content_sha256=? WHERE id=?",
            (json.dumps(envelope, sort_keys=True, separators=(",", ":")), envelope["content_sha256"], row["id"]),
        )
        collector.db.commit()
        self.assertEqual(collector.flush()["acked"], 1)
        received = AckServer.received[-1]["events"][0]["content"]["message"]["content"]
        self.assertEqual(received, "before[NUL]after")
        collector.close()


if __name__ == "__main__":
    unittest.main()
