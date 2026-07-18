from __future__ import annotations

import json
import plistlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from client.cli import parser
from connectors.local_activity import (
    AppleNotesConnector,
    BrowserActivityConnector,
    HermesSessionConnector,
    LocalActivitySchemaError,
)
from connectors.registry import definition
from connectors.sdk import ConnectorRunner
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


def safari_fixture(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE history_items(
          id INTEGER PRIMARY KEY, url TEXT NOT NULL
        );
        CREATE TABLE history_visits(
          id INTEGER PRIMARY KEY,
          history_item INTEGER NOT NULL,
          visit_time REAL NOT NULL,
          title TEXT
        );
        INSERT INTO history_items VALUES
          (1,'https://example.invalid/synthetic?q=first'),
          (2,'https://example.invalid/second');
        INSERT INTO history_visits VALUES
          (11,1,804556800,'Synthetic first'),
          (12,2,804556860,'Synthetic second');
    """)
    connection.commit()
    connection.close()


def chrome_fixture(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE urls(
          id INTEGER PRIMARY KEY, url TEXT NOT NULL, title TEXT,
          last_visit_time INTEGER, hidden INTEGER DEFAULT 0
        );
        CREATE TABLE visits(
          id INTEGER PRIMARY KEY, url INTEGER NOT NULL,
          visit_time INTEGER NOT NULL, transition INTEGER DEFAULT 0
        );
        INSERT INTO urls VALUES
          (1,'https://example.invalid/chrome?q=first','Synthetic Chrome',0,0);
        INSERT INTO visits VALUES (21,1,13412779200000000,0);
    """)
    connection.commit()
    connection.close()


def notes_fixture(path: Path, *, complete: bool = True) -> None:
    connection = sqlite3.connect(path)
    protected = "ZISPASSWORDPROTECTED INTEGER," if complete else ""
    connection.executescript(f"""
        CREATE TABLE ZICNOTEDATA(
          Z_PK INTEGER PRIMARY KEY, ZNOTE INTEGER, ZDATA BLOB
        );
        CREATE TABLE ZICCLOUDSYNCINGOBJECT(
          Z_PK INTEGER PRIMARY KEY,
          ZIDENTIFIER TEXT,
          ZTITLE1 TEXT,
          ZSNIPPET TEXT,
          ZCREATIONDATE1 REAL,
          ZMODIFICATIONDATE1 REAL,
          {protected}
          ZNOTEDATA INTEGER
        );
    """)
    if complete:
        connection.executescript("""
            INSERT INTO ZICNOTEDATA VALUES (1,101,X'00'),(2,102,X'00');
            INSERT INTO ZICCLOUDSYNCINGOBJECT VALUES
              (101,'synthetic-note-1','Synthetic note','safe snippet',
               804556800,804556860,0,1),
              (102,'synthetic-note-2','Locked note','private locked snippet',
               804556800,804556860,1,2);
        """)
    connection.commit()
    connection.close()


def hermes_fixture(path: Path, *, version: int = 22) -> None:
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE schema_version(version INTEGER NOT NULL);
        CREATE TABLE sessions(
          id TEXT PRIMARY KEY, source TEXT NOT NULL, title TEXT,
          started_at REAL NOT NULL, ended_at REAL
        );
        CREATE TABLE messages(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL,
          role TEXT NOT NULL,
          content TEXT,
          timestamp REAL NOT NULL,
          active INTEGER NOT NULL DEFAULT 1,
          compacted INTEGER NOT NULL DEFAULT 0
        );
    """)
    connection.execute("INSERT INTO schema_version VALUES (?)", (version,))
    connection.executescript("""
        INSERT INTO sessions VALUES
          ('synthetic-cli','cli','Synthetic CLI',1784289600,NULL),
          ('synthetic-slack','slack','Synthetic Slack',1784289600,NULL);
        INSERT INTO messages(session_id,role,content,timestamp,active,compacted)
        VALUES
          ('synthetic-cli','user','first synthetic Hermes turn',1784289600,1,0),
          ('synthetic-cli','assistant','second synthetic Hermes turn',1784289601,1,0),
          ('synthetic-slack','user','filtered platform turn',1784289602,1,0),
          ('synthetic-cli','tool','excluded tool payload',1784289603,1,0);
    """)
    connection.commit()
    connection.close()


class LocalActivityConnectorTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.safari = self.root / "SafariHistory.db"
        self.chrome = self.root / "ChromeHistory.db"
        self.notes = self.root / "NoteStore.sqlite"
        self.hermes = self.root / "state.db"
        safari_fixture(self.safari)
        chrome_fixture(self.chrome)
        notes_fixture(self.notes)
        hermes_fixture(self.hermes)
        self.safari_bookmarks = self.root / "SafariBookmarks.plist"
        with self.safari_bookmarks.open("wb") as output:
            plistlib.dump({
                "Children": [{
                    "WebBookmarkType": "WebBookmarkTypeLeaf",
                    "URLString": "https://example.invalid/bookmark",
                    "URIDictionary": {"title": "Synthetic Safari bookmark"},
                }]
            }, output)
        self.chrome_bookmarks = self.root / "ChromeBookmarks.json"
        self.chrome_bookmarks.write_text(json.dumps({
            "roots": {"bookmark_bar": {"children": [{
                "type": "url", "id": "31", "name": "Synthetic Chrome bookmark",
                "url": "https://example.invalid/chrome-bookmark",
                "date_added": "13412779200000000",
            }]}}
        }))

    def tearDown(self):
        self.temporary.cleanup()

    def test_manifests_are_local_read_only_and_explicitly_selected(self):
        expected = {
            "apple.safari": ("os_permission", ("bookmarks", "date_max", "date_min", "history")),
            "google.chrome": ("os_permission", ("bookmarks", "date_max", "date_min", "history")),
            "apple.notes": ("os_permission", ("date_max", "date_min")),
            "hermes.sessions": ("os_permission", ("date_max", "date_min", "roles", "sources")),
        }
        for connector_id, (auth, selectors) in expected.items():
            item = definition(connector_id)
            self.assertEqual(item.execution_placement, "source_local")
            self.assertEqual(item.acquisition_modes, ("snapshot",))
            self.assertEqual(item.auth.kind, auth)
            self.assertEqual(item.selection_fields, selectors)
            self.assertEqual(item.visibility_modes, ("private",))
            rendered = str(item.to_public()).lower()
            self.assertNotIn("send", rendered)
            self.assertNotIn("write", rendered)
        browser = parser().parse_args([
            "browser-sync", "--endpoint", "https://brain.example.invalid",
            "--source-id", "safari:mac:synthetic",
            "--keychain-service", "synthetic",
            "--keychain-account", "safari:mac:synthetic",
            "--browser", "safari", "--history", "/synthetic/History.db",
            "--spool", "/synthetic/safari.db",
        ])
        notes = parser().parse_args([
            "apple-notes-sync", "--endpoint", "https://brain.example.invalid",
            "--source-id", "notes:mac:synthetic",
            "--keychain-service", "synthetic",
            "--keychain-account", "notes:mac:synthetic",
            "--database", "/synthetic/NoteStore.sqlite",
            "--spool", "/synthetic/notes.db",
        ])
        hermes = parser().parse_args([
            "hermes-session-sync",
            "--endpoint", "https://brain.example.invalid",
            "--source-id", "hermes:mac:synthetic",
            "--keychain-service", "synthetic",
            "--keychain-account", "hermes:mac:synthetic",
            "--database", "/synthetic/state.db", "--source", "cli",
            "--spool", "/synthetic/hermes.db",
        ])
        for args in (browser, notes, hermes):
            self.assertEqual(args.privacy_mode, "scrub")
            self.assertEqual(args.visibility, "private")
            self.assertFalse(hasattr(args, "send"))

    def test_safari_and_chrome_history_bookmarks_paginate_and_revise(self):
        safari = BrowserActivityConnector(
            browser="safari", history=self.safari,
            bookmarks=self.safari_bookmarks,
            source_id="safari:mac:synthetic", page_size=1,
        )
        pages = []
        cursor = None
        while True:
            page = safari.pull(cursor)
            pages.extend(page.records)
            cursor = page.next_cursor
            if not page.has_more:
                break
        self.assertEqual(len(pages), 3)
        self.assertEqual(
            {record.content["surface"] for record in pages},
            {"safari-bookmark", "safari-history"},
        )
        first_native = next(
            record.native_id
            for record in pages
            if record.content["surface"] == "safari-history"
            and record.content["name"] == "Synthetic first"
        )
        connection = sqlite3.connect(self.safari)
        connection.execute(
            "UPDATE history_visits SET title='Revised synthetic title' WHERE id=11"
        )
        connection.commit()
        connection.close()
        revised = BrowserActivityConnector(
            browser="safari", history=self.safari,
            bookmarks=self.safari_bookmarks,
            source_id="safari:mac:synthetic",
        ).pull(cursor)
        by_id = {record.native_id: record for record in revised.records}
        self.assertEqual(by_id[first_native].content["name"], "Revised synthetic title")
        connection = sqlite3.connect(self.safari)
        connection.execute("DELETE FROM history_visits WHERE id=11")
        connection.commit()
        connection.close()
        absent = safari.pull(revised.next_cursor)
        self.assertNotIn(first_native, {record.native_id for record in absent.records})
        self.assertFalse(any(record.deleted for record in absent.records))

        chrome = BrowserActivityConnector(
            browser="chrome", history=self.chrome,
            bookmarks=self.chrome_bookmarks,
            source_id="chrome:mac:synthetic",
        ).pull(None)
        self.assertEqual(len(chrome.records), 2)
        self.assertEqual(safari.connector_id, "apple.safari")
        self.assertEqual(
            BrowserActivityConnector(
                browser="chrome", history=self.chrome, bookmarks=None,
                source_id="chrome:mac:synthetic",
            ).connector_id,
            "google.chrome",
        )
        self.assertEqual(
            {record.content["surface"] for record in chrome.records},
            {"chrome-bookmark", "chrome-history"},
        )

    def test_browser_schema_url_selector_and_absence_fail_closed(self):
        broken = self.root / "broken-browser.db"
        sqlite3.connect(broken).close()
        with self.assertRaisesRegex(LocalActivitySchemaError, "browser_schema"):
            BrowserActivityConnector(
                browser="safari", history=broken, bookmarks=None,
                source_id="safari:mac:synthetic",
            )
        connection = sqlite3.connect(self.chrome)
        connection.execute(
            "INSERT INTO urls VALUES (2,'file:///private/path','Private',0,0)"
        )
        connection.execute(
            "INSERT INTO visits VALUES (22,2,13412779200000001,0)"
        )
        connection.commit()
        connection.close()
        selected = BrowserActivityConnector(
            browser="chrome", history=self.chrome, bookmarks=None,
            source_id="chrome:mac:synthetic",
            date_min="2026-01-13T11:59:59Z",
            date_max="2026-01-13T12:00:01Z",
        ).pull(None)
        self.assertEqual(len(selected.records), 1)
        self.chrome.unlink()
        with self.assertRaisesRegex(LocalActivitySchemaError, "unavailable"):
            BrowserActivityConnector(
                browser="chrome", history=self.chrome, bookmarks=None,
                source_id="chrome:mac:synthetic",
            ).pull(None)

    def test_notes_exact_schema_skips_locked_and_never_decodes_attachments(self):
        connector = AppleNotesConnector(
            database=self.notes, source_id="notes:mac:synthetic"
        )
        page = connector.pull(None)
        self.assertEqual(len(page.records), 1)
        record = page.records[0]
        self.assertEqual(record.content["name"], "Synthetic note")
        self.assertEqual(record.content["text"], "safe snippet")
        self.assertNotIn("locked", json.dumps(record.to_mapping()).lower())

        broken = self.root / "broken-notes.db"
        notes_fixture(broken, complete=False)
        with self.assertRaisesRegex(LocalActivitySchemaError, "notes_schema"):
            AppleNotesConnector(
                database=broken, source_id="notes:mac:synthetic"
            )

    def test_hermes_schema_source_filter_roles_and_explicit_inactive(self):
        connector = HermesSessionConnector(
            database=self.hermes,
            source_id="hermes:mac:synthetic",
            sources=("cli",),
        )
        first = connector.pull(None)
        self.assertEqual(len(first.records), 2)
        self.assertEqual(
            [record.content["role"] for record in first.records],
            ["user", "assistant"],
        )
        connection = sqlite3.connect(self.hermes)
        connection.execute("UPDATE messages SET active=0 WHERE id=1")
        connection.commit()
        connection.close()
        inactive = connector.pull(first.next_cursor)
        self.assertTrue(next(
            record.deleted for record in inactive.records
            if record.native_id == first.records[0].native_id
        ))

        unsupported = self.root / "unsupported-hermes.db"
        hermes_fixture(unsupported, version=21)
        with self.assertRaisesRegex(LocalActivitySchemaError, "hermes_schema"):
            HermesSessionConnector(
                database=unsupported, source_id="hermes:mac:synthetic",
                sources=("cli",),
            )

    def test_every_connector_scrubs_before_spool_and_deduplicates(self):
        connection = sqlite3.connect(self.hermes)
        connection.execute(
            "UPDATE messages SET content=? WHERE id=1",
            ("safe Hermes turn api_key=synthetic-local-activity-canary",),
        )
        connection.commit()
        connection.close()
        brain = Brain()
        spool = self.root / "spool.db"
        runner = ConnectorRunner(
            connector=HermesSessionConnector(
                database=self.hermes,
                source_id="hermes:mac:synthetic",
                sources=("cli",),
            ),
            brain=brain,
            spool_path=spool,
            privacy=PrivacyPolicy(mode="scrub"),
        )
        try:
            first = runner.run_once()
            second = runner.run_once()
        finally:
            runner.close()
        self.assertEqual(first["acked"], 2)
        self.assertEqual(second["deduplicated"], 2)
        self.assertEqual(brain.calls, 1)
        self.assertNotIn(b"synthetic-local-activity-canary", spool.read_bytes())
        self.assertNotIn(
            "synthetic-local-activity-canary",
            json.dumps(list(brain.events.values())),
        )


if __name__ == "__main__":
    unittest.main()
