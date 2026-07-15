from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from connectors.export_inbox import ExportInboxConnector, ExportInboxError
from connectors.sdk import ConnectorRunner, ConnectorRunError
from privacy.policy import PrivacyPolicy


FIXTURES = Path(__file__).with_name("export_inbox_v1")
MANIFEST = FIXTURES / "manifest.json"


class FakeBrain:
    def __init__(self):
        self.calls = 0
        self.events: dict[tuple[str, str, str], dict] = {}
        self.fail_after_commit = False

    def ingest(self, events):
        self.calls += 1
        receipts = []
        inserted = duplicates = 0
        for event in events:
            key = (event["source_id"], event["native_id"], event["content_sha256"])
            if key in self.events:
                duplicates += 1
            else:
                self.events[key] = event
                inserted += 1
            receipts.append(f"recall://{event['source_id']}/{event['native_id']}?rev=1")
        if self.fail_after_commit:
            self.fail_after_commit = False
            raise OSError("synthetic lost acknowledgement with private payload")
        return {
            "status": "committed", "receipts": receipts, "inserted": inserted,
            "duplicate_events": duplicates, "replay": False,
        }


class ExportInboxTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.inbox = self.root / "Recall Inbox"
        self.inbox.mkdir()
        self.catalog = self.root / "state" / "exports.db"
        self.spool = self.root / "state" / "runner.db"

    def copy_fixtures(self) -> None:
        shutil.copy(FIXTURES / "conversations.json", self.inbox / "renamed-private-export.json")
        shutil.copy(FIXTURES / "cowork.jsonl", self.inbox / "another-private-name.jsonl")

    def connector(self, *, page_size: int = 500) -> ExportInboxConnector:
        return ExportInboxConnector(
            inbox=self.inbox, catalog_path=self.catalog,
            source_id="chatgpt:export:synthetic", page_size=page_size,
        )

    def test_frozen_synthetic_manifest(self) -> None:
        manifest = json.loads(MANIFEST.read_text())
        for name, digest in manifest["sha256"].items():
            self.assertEqual(hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest(), digest)
        self.assertEqual(manifest["expected"], {
            "attachments": 1, "branches": 1, "conversations": 2,
            "messages": 6, "privacy_canaries": 2,
        })

    def test_dry_run_is_explicit_content_free_and_fail_closed(self) -> None:
        self.copy_fixtures()
        connector = self.connector()
        inventory = connector.dry_run()
        self.assertEqual(inventory, {
            "schema_version": 1, "mode": "export-inbox-inventory",
            "files": 2, "bytes": sum(path.stat().st_size for path in self.inbox.iterdir()),
            "types": {"json": 1, "jsonl": 1}, "supported": 2,
            "ignored": 0, "privacy_mode": "off", "network_requests": 0,
        })
        rendered = json.dumps(inventory)
        self.assertNotIn("private-name", rendered)
        self.assertNotIn(str(self.inbox), rendered)

        symlink = self.inbox / "escape.json"
        symlink.symlink_to(FIXTURES / "conversations.json")
        with self.assertRaisesRegex(ExportInboxError, "symlink"):
            connector.dry_run()
        symlink.unlink()

        nested = self.inbox / "nested"
        nested.mkdir()
        with self.assertRaisesRegex(ExportInboxError, "nested"):
            connector.dry_run()
        nested.rmdir()

        hard_link = self.inbox / "hard-link.json"
        os.link(self.inbox / "renamed-private-export.json", hard_link)
        with self.assertRaisesRegex(ExportInboxError, "hard-linked"):
            connector.dry_run()
        hard_link.unlink()

        alias_flag = b"\0" * 8 + b"\x80\x00" + b"\0" * 22
        with mock.patch("os.getxattr", return_value=alias_flag):
            with self.assertRaisesRegex(ExportInboxError, "Finder alias"):
                connector.dry_run()

        linked_root = self.root / "linked-inbox"
        linked_root.symlink_to(self.inbox, target_is_directory=True)
        with self.assertRaises(ExportInboxError):
            ExportInboxConnector(
                inbox=linked_root, catalog_path=self.catalog,
                source_id="chatgpt:export:synthetic",
            )
        with self.assertRaises(ExportInboxError):
            ExportInboxConnector(
                inbox=self.root / "Downloads", catalog_path=self.catalog,
                source_id="chatgpt:export:synthetic",
            )

    def test_official_tree_jsonl_zip_and_privacy_use_stable_filename_free_records(self) -> None:
        self.copy_fixtures()
        with zipfile.ZipFile(self.inbox / "third-private-name.zip", "w") as archive:
            archive.write(FIXTURES / "conversations.json", "nested/conversations.json")
            archive.writestr("account/user.json", json.dumps({"email": "synthetic-metadata-only@example.invalid"}))
        connector = self.connector(page_size=20)
        page = connector.pull(None)
        self.assertEqual(len(page.records), 6)  # duplicate JSON and ZIP content dedupe by upstream IDs
        rendered = json.dumps([record.__dict__ for record in page.records], sort_keys=True)
        self.assertNotIn("renamed-private-export", rendered)
        self.assertNotIn("another-private-name", rendered)
        self.assertNotIn("third-private-name", rendered)
        self.assertNotIn("private-name.png", rendered)
        self.assertNotIn("file-service", rendered)
        self.assertIn('"attachment_count": 1', rendered)
        self.assertIn("parent_native_id", rendered)
        self.assertEqual(len({record.native_id for record in page.records}), 6)

        brain = FakeBrain()
        runner = ConnectorRunner(
            connector=connector, brain=brain, spool_path=self.spool,
            privacy=PrivacyPolicy(mode="scrub"),
        )
        result = runner.run_once()
        self.assertEqual(result["acked"], 6)
        payload = json.dumps(list(brain.events.values()), sort_keys=True)
        for canary in ("synthetic-chatgpt-secret-canary-91", "synthetic-cowork-secret-canary-92"):
            self.assertNotIn(canary, payload)
            self.assertNotIn(canary.encode(), self.spool.read_bytes())
        self.assertIn("keep aftermath", payload)
        self.assertIn("safe after", payload)
        runner.close()

    def test_official_null_message_timestamp_uses_conversation_timestamp(self) -> None:
        value = json.loads((FIXTURES / "conversations.json").read_text())
        value[0]["mapping"]["user-alpha"]["message"]["create_time"] = None
        (self.inbox / "null-time.json").write_text(json.dumps(value))
        page = self.connector().pull(None)
        user = next(record for record in page.records if record.content["role"] == "user")
        self.assertEqual(user.occurred_at, "2026-07-14T00:00:00.000Z")

    def test_lost_ack_restart_replays_without_repull_or_duplicate(self) -> None:
        self.copy_fixtures()
        connector = self.connector(page_size=20)
        brain = FakeBrain(); brain.fail_after_commit = True
        runner = ConnectorRunner(
            connector=connector, brain=brain, spool_path=self.spool,
            privacy=PrivacyPolicy(mode="drop"),
        )
        with self.assertRaisesRegex(ConnectorRunError, "brain_unavailable"):
            runner.run_once()
        first_count = len(brain.events)
        runner.close()
        self.inbox.rename(self.root / "inbox-temporarily-unavailable")
        recovered = ConnectorRunner(
            connector=connector, brain=brain, spool_path=self.spool,
            privacy=PrivacyPolicy(mode="drop"),
        )
        result = recovered.run_once()
        self.assertEqual(result["replayed"], 1)
        self.assertEqual(len(brain.events), first_count)
        recovered.close()

    def test_explicit_removal_is_reference_safe_and_file_deletion_is_not(self) -> None:
        shutil.copy(FIXTURES / "conversations.json", self.inbox / "first.json")
        shutil.copy(FIXTURES / "conversations.json", self.inbox / "duplicate.json")
        connector = self.connector(page_size=20)
        brain = FakeBrain()
        runner = ConnectorRunner(connector=connector, brain=brain, spool_path=self.spool)
        runner.run_once()
        exports = connector.exports()
        self.assertEqual(len(exports), 1)
        export_id = exports[0]["export_id"]
        (self.inbox / "first.json").unlink()
        (self.inbox / "duplicate.json").unlink()
        no_delete = connector.pull(runner._cursor())
        self.assertFalse(any(record.deleted for record in no_delete.records))
        queued = connector.queue_remove(export_id)
        self.assertEqual(queued["status"], "queued")
        removal = connector.pull(runner._cursor())
        self.assertTrue(removal.records)
        self.assertTrue(all(record.deleted for record in removal.records))
        runner.run_once()
        runner.run_once()  # observe committed removal cursor and finalize catalog state
        self.assertEqual(connector.queue_remove(export_id)["status"], "already_removed")
        self.assertEqual(connector.exports()[0]["status"], "removed")
        runner.close()

    def test_removing_one_changed_export_preserves_shared_message_references(self) -> None:
        original = json.loads((FIXTURES / "conversations.json").read_text())
        shortened = json.loads(json.dumps(original))
        shortened[0]["mapping"].pop("assistant-alpha-branch")
        shortened[0]["mapping"]["user-alpha"]["children"].remove("assistant-alpha-branch")
        (self.inbox / "complete.json").write_text(json.dumps(original))
        (self.inbox / "shortened.json").write_text(json.dumps(shortened))
        connector = self.connector(page_size=20)
        first = connector.pull(None)
        self.assertEqual(len(first.records), 4)
        exports = connector.exports()
        complete_id = next(item["export_id"] for item in exports if item["records"] == 4)
        connector.queue_remove(complete_id)
        removal = connector.pull(first.next_cursor)
        self.assertEqual(len(removal.records), 1)
        branch = next(record for record in first.records if "Alternate synthetic branch" in json.dumps(record.content))
        self.assertEqual(removal.records[0].native_id, branch.native_id)

    def test_multi_page_ingest_and_removal_cursors_are_ack_gated(self) -> None:
        shutil.copy(FIXTURES / "conversations.json", self.inbox / "conversation.json")
        connector = self.connector(page_size=2)
        brain = FakeBrain()
        runner = ConnectorRunner(connector=connector, brain=brain, spool_path=self.spool)
        first = runner.run_once()
        second = runner.run_once()
        self.assertEqual((first["acked"], second["acked"]), (2, 2))
        self.assertEqual(len(brain.events), 4)
        export_id = connector.exports()[0]["export_id"]
        connector.queue_remove(export_id)
        deletion_one = runner.run_once()
        deletion_two = runner.run_once()
        self.assertEqual((deletion_one["acked"], deletion_two["acked"]), (2, 2))
        runner.run_once()  # committed terminal removal cursor finalizes the export
        self.assertEqual(connector.exports()[0]["status"], "removed")
        self.assertEqual(len([event for event in brain.events.values() if event["kind"] == "tombstone"]), 4)
        runner.close()

    def test_new_removal_cannot_change_an_in_flight_removal_record_set(self) -> None:
        original = json.loads((FIXTURES / "conversations.json").read_text())
        shortened = json.loads(json.dumps(original))
        shortened[0]["mapping"].pop("assistant-alpha-branch")
        shortened[0]["mapping"]["user-alpha"]["children"].remove("assistant-alpha-branch")
        (self.inbox / "complete.json").write_text(json.dumps(original))
        (self.inbox / "shortened.json").write_text(json.dumps(shortened))
        connector = self.connector(page_size=1)
        page = connector.pull(None)
        exports = connector.exports()
        complete_id = next(item["export_id"] for item in exports if item["records"] == 4)
        shortened_id = next(item["export_id"] for item in exports if item["records"] == 3)
        connector.queue_remove(complete_id)
        removal = connector.pull(page.next_cursor)
        self.assertTrue(removal.records)
        with self.assertRaisesRegex(ExportInboxError, "in progress"):
            connector.queue_remove(shortened_id)

    def test_content_free_cli_inventory_list_and_explicit_remove(self) -> None:
        self.copy_fixtures()
        environment = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}

        def invoke(*arguments: str) -> dict:
            result = subprocess.run(
                [sys.executable, "-m", "client.cli", *arguments],
                cwd=Path(__file__).resolve().parents[1], env=environment,
                check=True, text=True, capture_output=True,
            )
            self.assertEqual(result.stderr, "")
            return json.loads(result.stdout)

        common = ("--inbox", str(self.inbox), "--catalog", str(self.catalog))
        inventory = invoke("export-inbox-dry-run", *common)
        self.assertEqual(inventory["mode"], "export-inbox-inventory")
        self.assertNotIn("private-name", json.dumps(inventory))
        connector = self.connector()
        connector.pull(None)
        export_id = connector.exports()[0]["export_id"]
        connector.close()
        listed = invoke("export-inbox-list", *common)
        self.assertTrue(listed["exports"])
        queued = invoke("export-inbox-remove", *common, export_id)
        self.assertEqual(queued["status"], "queued")

    def test_malformed_and_malicious_archives_fail_before_catalog_or_spool(self) -> None:
        malformed = [{"id": "broken", "mapping": {"node": {"message": {"author": {"role": "user"}}}}}]
        (self.inbox / "malformed.json").write_text(json.dumps(malformed))
        connector = self.connector()
        with self.assertRaises(ExportInboxError):
            connector.pull(None)
        self.assertEqual(connector.exports(), [])
        (self.inbox / "malformed.json").unlink()
        with zipfile.ZipFile(self.inbox / "malicious.zip", "w") as archive:
            archive.writestr("../escape.json", "[]")
        with self.assertRaisesRegex(ExportInboxError, "unsafe"):
            connector.pull(None)
        self.assertEqual(connector.exports(), [])
        self.assertFalse(self.spool.exists())


if __name__ == "__main__":
    unittest.main()
