from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from client.cli import parser
from connectors.local_files import SelectedTextConnector
from connectors.registry import definition
from connectors.sdk import ConnectorContractError, ConnectorRunner
from connectors.whatsapp_export import WhatsAppExportConnector
from privacy.policy import PrivacyPolicy


class Brain:
    def __init__(self):
        self.events: dict[tuple[str, str], dict] = {}
        self.calls = 0

    def ingest(self, events: list[dict]) -> dict:
        self.calls += 1
        inserted = 0
        duplicates = 0
        receipts = []
        for event in events:
            key = (event["native_id"], event["content_sha256"])
            if key in self.events:
                duplicates += 1
            else:
                self.events[key] = event
                inserted += 1
            receipts.append(
                f"recall://{event['source_id']}/{event['native_id']}?rev=1"
            )
        return {
            "status": "committed",
            "inserted": inserted,
            "duplicate_events": duplicates,
            "receipts": receipts,
            "replay": bool(duplicates),
        }


class WhatsAppExportConnectorTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.export = self.root / "selected-chat.txt"
        self.export.write_text(
            "[1/2/26, 3:04:05 PM] Synthetic Friend: first synthetic message\n"
            "continued line\n"
            "[1/2/26, 3:05:05 PM] Synthetic Owner: second synthetic message\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def connector(self, *, page_size: int = 100) -> WhatsAppExportConnector:
        return WhatsAppExportConnector(
            export=self.export,
            source_id="whatsapp:mac:synthetic",
            conversation_id="synthetic-conversation",
            owner_names=("Synthetic Owner",),
            date_order="mdy",
            timezone_name="UTC",
            page_size=page_size,
        )

    def test_manifest_is_selected_export_without_linked_device_or_send_surface(self):
        item = definition("whatsapp.export")
        self.assertEqual(item.execution_placement, "source_local")
        self.assertEqual(item.acquisition_modes, ("import", "watch"))
        self.assertEqual(item.auth.kind, "selected_export")
        self.assertEqual(item.minimum_external_scopes, ())
        self.assertEqual(
            item.selection_fields,
            ("conversation_id", "date_order", "owner_names", "timezone"),
        )
        rendered = str(item.to_public()).lower()
        self.assertNotIn("linked", rendered)
        self.assertNotIn("send", rendered)
        args = parser().parse_args([
            "whatsapp-export-sync",
            "--endpoint", "https://brain.example.invalid",
            "--source-id", "whatsapp:mac:synthetic",
            "--keychain-service", "synthetic",
            "--keychain-account", "whatsapp:mac:synthetic",
            "--export", "/synthetic/chat.txt",
            "--conversation-id", "synthetic-conversation",
            "--date-order", "mdy",
            "--timezone", "UTC",
            "--spool", "/synthetic/whatsapp.db",
        ])
        self.assertEqual(args.privacy_mode, "scrub")
        self.assertEqual(args.visibility, "private")

    def test_multiline_projection_pagination_and_stable_revision(self):
        connector = self.connector(page_size=1)
        first = connector.pull(None)
        second = connector.pull(first.next_cursor)
        self.assertTrue(first.has_more)
        self.assertFalse(second.has_more)
        self.assertEqual(first.records[0].content["direction"], "inbound")
        self.assertEqual(second.records[0].content["direction"], "outbound")
        self.assertIn("continued line", first.records[0].content["text"])
        terminal = second.next_cursor
        native_id = first.records[0].native_id

        self.export.write_text(
            "[1/2/26, 3:04:05 PM] Synthetic Friend: revised synthetic message\n"
            "[1/2/26, 3:05:05 PM] Synthetic Owner: second synthetic message\n",
            encoding="utf-8",
        )
        revised = self.connector().pull(terminal)
        self.assertEqual(revised.records[0].native_id, native_id)
        self.assertEqual(
            revised.records[0].content["text"],
            "revised synthetic message",
        )

        self.export.write_text(
            "[1/2/26, 3:05:05 PM] Synthetic Owner: second synthetic message\n",
            encoding="utf-8",
        )
        absent = self.connector().pull(revised.next_cursor)
        self.assertNotIn(native_id, {record.native_id for record in absent.records})
        self.assertFalse(any(record.deleted for record in absent.records))

    def test_explicit_file_boundary_and_malformed_export_fail_closed(self):
        alias = self.root / "alias.txt"
        alias.symlink_to(self.export)
        with self.assertRaisesRegex(ConnectorContractError, "symlink"):
            WhatsAppExportConnector(
                export=alias,
                source_id="whatsapp:mac:synthetic",
                conversation_id="synthetic-conversation",
                owner_names=(),
                date_order="mdy",
                timezone_name="UTC",
            )
        self.export.write_text("not an export\n", encoding="utf-8")
        with self.assertRaisesRegex(ConnectorContractError, "format"):
            self.connector().pull(None)

    def test_privacy_precedes_spool_and_brain(self):
        self.export.write_text(
            "[1/2/26, 3:04:05 PM] Synthetic Friend: "
            "api_key=synthetic-whatsapp-private-canary\n",
            encoding="utf-8",
        )
        brain = Brain()
        spool = self.root / "state" / "whatsapp.db"
        runner = ConnectorRunner(
            connector=self.connector(),
            brain=brain,
            spool_path=spool,
            privacy=PrivacyPolicy(mode="scrub"),
        )
        try:
            runner.run_once()
        finally:
            runner.close()
        self.assertNotIn(
            b"synthetic-whatsapp-private-canary",
            b"".join(
                path.read_bytes()
                for path in spool.parent.glob("whatsapp.db*")
                if path.is_file()
            ),
        )
        self.assertNotIn(
            "synthetic-whatsapp-private-canary",
            str(tuple(brain.events.values())),
        )


class SelectedTextConnectorTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "note.md").write_text(
            "# Synthetic note\nfirst selected marker\n",
            encoding="utf-8",
        )
        (self.root / "nested").mkdir()
        (self.root / "nested" / "second.txt").write_text(
            "second selected marker\n",
            encoding="utf-8",
        )
        (self.root / ".obsidian").mkdir()
        (self.root / ".obsidian" / "workspace.json").write_text(
            "must never be selected",
            encoding="utf-8",
        )
        (self.root / "ignored.json").write_text(
            "must never be selected",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def connector(self, *, page_size: int = 100) -> SelectedTextConnector:
        return SelectedTextConnector(
            root=self.root,
            source_id="selected-text:mac:synthetic",
            extensions=(".md", ".txt"),
            max_depth=4,
            page_size=page_size,
        )

    def test_manifest_covers_explicit_markdown_and_obsidian_roots(self):
        item = definition("local.selected-text")
        self.assertEqual(item.execution_placement, "source_local")
        self.assertEqual(item.acquisition_modes, ("snapshot", "watch"))
        self.assertEqual(item.auth.kind, "os_permission")
        self.assertEqual(item.record_kinds, ("document.v1",))
        self.assertEqual(
            item.selection_fields,
            ("extensions", "max_depth", "root"),
        )
        args = parser().parse_args([
            "selected-text-sync",
            "--endpoint", "https://brain.example.invalid",
            "--source-id", "selected-text:mac:synthetic",
            "--keychain-service", "synthetic",
            "--keychain-account", "selected-text:mac:synthetic",
            "--root", "/synthetic/notes",
            "--spool", "/synthetic/selected-text.db",
        ])
        self.assertEqual(args.privacy_mode, "scrub")
        self.assertEqual(args.visibility, "private")

    def test_closed_tree_paginates_revises_and_never_deletes_by_absence(self):
        connector = self.connector(page_size=1)
        first = connector.pull(None)
        second = connector.pull(first.next_cursor)
        self.assertTrue(first.has_more)
        self.assertFalse(second.has_more)
        records = first.records + second.records
        self.assertEqual(len(records), 2)
        rendered = str(tuple(record.content for record in records))
        self.assertIn("first selected marker", rendered)
        self.assertIn("second selected marker", rendered)
        self.assertNotIn("must never be selected", rendered)
        note = next(record for record in records if record.content["name"] == "note.md")
        terminal = second.next_cursor

        (self.root / "note.md").write_text(
            "# Synthetic note\nrevised selected marker\n",
            encoding="utf-8",
        )
        revised = self.connector().pull(terminal)
        revised_note = next(
            record for record in revised.records
            if record.content["name"] == "note.md"
        )
        self.assertEqual(revised_note.native_id, note.native_id)
        self.assertIn("revised selected marker", revised_note.content["text"])

        (self.root / "note.md").unlink()
        absent = self.connector().pull(revised.next_cursor)
        self.assertNotIn(
            note.native_id,
            {record.native_id for record in absent.records},
        )
        self.assertFalse(any(record.deleted for record in absent.records))

    def test_symlink_and_noncanonical_selection_fail_closed(self):
        (self.root / "linked.md").symlink_to(self.root / "note.md")
        with self.assertRaisesRegex(ConnectorContractError, "symlink"):
            self.connector().pull(None)
        with self.assertRaisesRegex(ConnectorContractError, "extensions"):
            SelectedTextConnector(
                root=self.root,
                source_id="selected-text:mac:synthetic",
                extensions=(".txt", ".md"),
            )
        with self.assertRaisesRegex(ConnectorContractError, "extensions"):
            SelectedTextConnector(
                root=self.root,
                source_id="selected-text:mac:synthetic",
                extensions=([".md"],),  # type: ignore[arg-type]
            )

    def test_privacy_precedes_spool_and_brain(self):
        (self.root / "note.md").write_text(
            "api_key=synthetic-selected-text-private-canary",
            encoding="utf-8",
        )
        brain = Brain()
        spool = self.root / "state" / "selected-text.db"
        runner = ConnectorRunner(
            connector=self.connector(),
            brain=brain,
            spool_path=spool,
            privacy=PrivacyPolicy(mode="scrub"),
        )
        try:
            runner.run_once()
        finally:
            runner.close()
        private = b"".join(
            path.read_bytes()
            for path in spool.parent.glob("selected-text.db*")
            if path.is_file()
        )
        self.assertNotIn(b"synthetic-selected-text-private-canary", private)
        self.assertNotIn(
            "synthetic-selected-text-private-canary",
            str(tuple(brain.events.values())),
        )


if __name__ == "__main__":
    unittest.main()
