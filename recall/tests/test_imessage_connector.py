from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from client.cli import parser
from connectors.imessage import IMessageConnector, IMessageSchemaError
from connectors.registry import definition
from connectors.sdk import ConnectorContractError, ConnectorRunner
from privacy.policy import PrivacyPolicy


APPLE_SECOND = 788_918_400_000_000_000


def create_messages_database(path: Path, *, missing_text: bool = False) -> None:
    connection = sqlite3.connect(path)
    text_column = "" if missing_text else ", text TEXT"
    connection.executescript(f"""
        CREATE TABLE message(
          ROWID INTEGER PRIMARY KEY,
          guid TEXT NOT NULL
          {text_column},
          handle_id INTEGER,
          service TEXT,
          date INTEGER NOT NULL,
          is_from_me INTEGER NOT NULL,
          date_edited INTEGER NOT NULL DEFAULT 0,
          is_deleted INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE handle(
          ROWID INTEGER PRIMARY KEY,
          id TEXT NOT NULL,
          service TEXT
        );
        CREATE TABLE chat(
          ROWID INTEGER PRIMARY KEY,
          guid TEXT NOT NULL,
          chat_identifier TEXT NOT NULL,
          service_name TEXT
        );
        CREATE TABLE chat_message_join(
          chat_id INTEGER NOT NULL,
          message_id INTEGER NOT NULL
        );
    """)
    connection.execute(
        "INSERT INTO handle(ROWID,id,service) VALUES (1,?,?)",
        ("synthetic-sender@example.invalid", "iMessage"),
    )
    connection.execute(
        "INSERT INTO chat(ROWID,guid,chat_identifier,service_name) VALUES (1,?,?,?)",
        ("synthetic-chat-guid", "synthetic-chat", "iMessage"),
    )
    if not missing_text:
        connection.executemany(
            "INSERT INTO message"
            "(ROWID,guid,text,handle_id,service,date,is_from_me,date_edited,is_deleted)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (
                (
                    1,
                    "synthetic-message-guid-1",
                    "first synthetic iMessage",
                    1,
                    "iMessage",
                    APPLE_SECOND,
                    0,
                    0,
                    0,
                ),
                (
                    2,
                    "synthetic-message-guid-2",
                    "second synthetic iMessage",
                    1,
                    "iMessage",
                    APPLE_SECOND + 1_000_000_000,
                    1,
                    0,
                    0,
                ),
            ),
        )
        connection.executemany(
            "INSERT INTO chat_message_join(chat_id,message_id) VALUES (1,?)",
            ((1,), (2,)),
        )
    connection.commit()
    connection.close()


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


class IMessageContractTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "chat.db"
        create_messages_database(self.database)

    def tearDown(self):
        os.chmod(self.root, 0o700)
        if self.database.exists():
            os.chmod(self.database, 0o600)
        self.temporary.cleanup()

    def connector(self, *, page_size: int = 100) -> IMessageConnector:
        return IMessageConnector(
            database=self.database,
            source_id="imessage:mac:synthetic",
            page_size=page_size,
        )

    def test_static_local_manifest_has_exact_permission_and_no_send_surface(self):
        item = definition("apple.imessage")
        self.assertEqual(item.schema_version, 3)
        self.assertEqual(item.execution_placement, "source_local")
        self.assertEqual(item.acquisition_modes, ("snapshot",))
        self.assertEqual(item.auth.kind, "os_permission")
        self.assertEqual(item.minimum_external_scopes, ("macos.full_disk_access",))
        self.assertEqual(item.selection_fields, ("chat_ids", "date_max", "date_min"))
        self.assertEqual(item.record_kinds, ("communication_message.v1",))
        self.assertNotIn("send", str(item.to_public()).lower())
        args = parser().parse_args([
            "imessage-sync",
            "--endpoint",
            "https://brain.example.invalid",
            "--source-id",
            "imessage:mac:synthetic",
            "--keychain-service",
            "synthetic",
            "--keychain-account",
            "imessage:mac:synthetic",
            "--database",
            "/synthetic/chat.db",
            "--spool",
            "/synthetic/imessage.db",
        ])
        self.assertEqual(args.privacy_mode, "scrub")
        self.assertEqual(args.visibility, "private")
        self.assertFalse(hasattr(args, "send"))

    def test_schema_probe_and_path_boundary_fail_closed(self):
        broken = self.root / "broken.db"
        create_messages_database(broken, missing_text=True)
        with self.assertRaisesRegex(IMessageSchemaError, "unsupported_imessage_schema"):
            IMessageConnector(
                database=broken,
                source_id="imessage:mac:synthetic",
            )
        alias = self.root / "alias.db"
        alias.symlink_to(self.database)
        with self.assertRaisesRegex(ConnectorContractError, "symlink"):
            IMessageConnector(
                database=alias,
                source_id="imessage:mac:synthetic",
            )
        connection = sqlite3.connect(self.database)
        connection.execute("UPDATE message SET is_deleted=2 WHERE ROWID=1")
        connection.commit()
        connection.close()
        with self.assertRaisesRegex(
            IMessageSchemaError, "unsupported_imessage_schema"
        ):
            self.connector().pull(None)

    def test_snapshot_operates_without_source_write_permission(self):
        before = self.database.read_bytes()
        entries = {path.name for path in self.root.iterdir()}
        os.chmod(self.database, 0o400)
        os.chmod(self.root, 0o500)
        page = self.connector().pull(None)
        self.assertEqual(len(page.records), 2)
        self.assertEqual(before, self.database.read_bytes())
        self.assertEqual(entries, {path.name for path in self.root.iterdir()})
        self.assertEqual(
            {record.content["direction"] for record in page.records},
            {"inbound", "outbound"},
        )

    def test_selectors_and_lost_permission_do_not_widen_or_delete(self):
        selected = IMessageConnector(
            database=self.database,
            source_id="imessage:mac:synthetic",
            chat_ids=("synthetic-chat",),
            date_min="2026-01-01T00:00:00Z",
            date_max="2026-12-31T23:59:59Z",
        ).pull(None)
        self.assertEqual(len(selected.records), 2)
        excluded = IMessageConnector(
            database=self.database,
            source_id="imessage:mac:synthetic",
            chat_ids=("not-selected",),
        ).pull(None)
        self.assertFalse(excluded.records)
        connector = self.connector()
        self.database.unlink()
        with self.assertRaisesRegex(
            ConnectorContractError, "local_database_unavailable"
        ):
            connector.pull(None)

    def test_pagination_revision_explicit_delete_and_absence_semantics(self):
        connector = self.connector(page_size=1)
        first = connector.pull(None)
        second = connector.pull(first.next_cursor)
        self.assertTrue(first.has_more)
        self.assertFalse(second.has_more)
        self.assertEqual(len({first.records[0].native_id, second.records[0].native_id}), 2)
        terminal = second.next_cursor

        connection = sqlite3.connect(self.database)
        connection.execute(
            "UPDATE message SET text=?,date_edited=? WHERE ROWID=1",
            ("first synthetic iMessage revised", APPLE_SECOND + 2_000_000_000),
        )
        connection.commit()
        connection.close()
        wide = self.connector()
        revised = wide.pull(terminal)
        self.assertEqual(revised.records[0].native_id, first.records[0].native_id)
        self.assertEqual(
            revised.records[0].content["text"],
            "first synthetic iMessage revised",
        )
        self.assertIn("edited_at", revised.records[0].content)

        connection = sqlite3.connect(self.database)
        connection.execute("UPDATE message SET is_deleted=1 WHERE ROWID=1")
        connection.commit()
        connection.close()
        deleted = wide.pull(revised.next_cursor)
        self.assertTrue(deleted.records[0].deleted)
        self.assertEqual(
            deleted.records[0].content,
            {"kind": "communication_message.v1"},
        )

        connection = sqlite3.connect(self.database)
        connection.execute("DELETE FROM message WHERE ROWID=1")
        connection.commit()
        connection.close()
        absent = wide.pull(deleted.next_cursor)
        self.assertNotIn(
            deleted.records[0].native_id,
            {record.native_id for record in absent.records},
        )
        self.assertFalse(any(record.deleted for record in absent.records))

    def test_runner_deduplicates_and_scrubs_before_spool_and_brain(self):
        connection = sqlite3.connect(self.database)
        connection.execute(
            "UPDATE message SET text=? WHERE ROWID=1",
            ("api_key=synthetic-imessage-private-canary",),
        )
        connection.commit()
        connection.close()
        brain = Brain()
        spool = self.root / "state" / "imessage.db"
        runner = ConnectorRunner(
            connector=self.connector(),
            brain=brain,
            spool_path=spool,
            privacy=PrivacyPolicy(mode="scrub"),
        )
        try:
            first = runner.run_once()
            second = runner.run_once()
            self.assertEqual(first["acked"], 2)
            self.assertEqual(second["acked"], 0)
            self.assertEqual(len(brain.events), 2)
            self.assertNotIn(
                b"synthetic-imessage-private-canary",
                b"".join(
                    path.read_bytes()
                    for path in spool.parent.glob("imessage.db*")
                    if path.is_file()
                ),
            )
            self.assertNotIn(
                "synthetic-imessage-private-canary",
                str(tuple(brain.events.values())),
            )
        finally:
            runner.close()


if __name__ == "__main__":
    unittest.main()
