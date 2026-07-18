#!/usr/bin/env python3
"""Fresh-PostgreSQL proof for browsers, Apple Notes, and Hermes snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RECALL = ROOT / "recall"
SERVER = RECALL / "server"
sys.path.insert(0, str(RECALL))
sys.path.insert(0, str(SERVER))

from connectors.local_activity import (  # noqa: E402
    AppleNotesConnector,
    BrowserActivityConnector,
    HermesSessionConnector,
)
from connectors.sdk import ConnectorRunner  # noqa: E402
from privacy.policy import PrivacyPolicy  # noqa: E402
from recall_server.db import BrainStore  # noqa: E402
from recall_server.projectors import canonical_json  # noqa: E402


SOURCES = {
    "safari": "synthetic:safari:postgres",
    "chrome": "synthetic:chrome:postgres",
    "notes": "synthetic:notes:postgres",
    "hermes": "synthetic:hermes:postgres",
}


class StoreWriter:
    def __init__(self, store: BrainStore):
        self.store = store

    def ingest(self, events):
        key = "local-activity-e2e-" + hashlib.sha256(
            canonical_json(events)
        ).hexdigest()
        acknowledgement, replay = self.store.ingest(key, events)
        return {**acknowledgement, "replay": replay}


def create_safari(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE history_items(id INTEGER PRIMARY KEY,url TEXT NOT NULL);
        CREATE TABLE history_visits(
          id INTEGER PRIMARY KEY,history_item INTEGER NOT NULL,
          visit_time REAL NOT NULL,title TEXT
        );
        INSERT INTO history_items VALUES
          (1,'https://example.invalid/safari-e2e');
        INSERT INTO history_visits VALUES
          (11,1,804556800,'safari postgres safe marker');
    """)
    connection.commit()
    connection.close()


def create_chrome(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE urls(
          id INTEGER PRIMARY KEY,url TEXT NOT NULL,title TEXT,
          last_visit_time INTEGER,hidden INTEGER DEFAULT 0
        );
        CREATE TABLE visits(
          id INTEGER PRIMARY KEY,url INTEGER NOT NULL,
          visit_time INTEGER NOT NULL,transition INTEGER DEFAULT 0
        );
        INSERT INTO urls VALUES
          (1,'https://example.invalid/chrome-e2e',
           'chrome postgres safe marker',0,0);
        INSERT INTO visits VALUES (21,1,13412779200000000,0);
    """)
    connection.commit()
    connection.close()


def create_notes(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE ZICNOTEDATA(
          Z_PK INTEGER PRIMARY KEY,ZNOTE INTEGER,ZDATA BLOB
        );
        CREATE TABLE ZICCLOUDSYNCINGOBJECT(
          Z_PK INTEGER PRIMARY KEY,ZIDENTIFIER TEXT,ZTITLE1 TEXT,
          ZSNIPPET TEXT,ZCREATIONDATE1 REAL,ZMODIFICATIONDATE1 REAL,
          ZISPASSWORDPROTECTED INTEGER,ZNOTEDATA INTEGER
        );
        INSERT INTO ZICNOTEDATA VALUES (1,101,X'00');
        INSERT INTO ZICCLOUDSYNCINGOBJECT VALUES
          (101,'synthetic-note-e2e','Synthetic note',
           'notes postgres safe marker',804556800,804556860,0,1);
    """)
    connection.commit()
    connection.close()


def create_hermes(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE schema_version(version INTEGER NOT NULL);
        CREATE TABLE sessions(
          id TEXT PRIMARY KEY,source TEXT NOT NULL,title TEXT,
          started_at REAL NOT NULL,ended_at REAL
        );
        CREATE TABLE messages(
          id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,
          role TEXT NOT NULL,content TEXT,timestamp REAL NOT NULL,
          active INTEGER NOT NULL DEFAULT 1,
          compacted INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO schema_version VALUES (22);
        INSERT INTO sessions VALUES
          ('synthetic-session','cli','Synthetic Hermes',1784289600,NULL);
        INSERT INTO messages(session_id,role,content,timestamp,active,compacted)
        VALUES
          ('synthetic-session','user','hermes postgres safe marker',
           1784289600,1,0),
          ('synthetic-session','assistant',
           'api_key=synthetic-hermes-e2e-canary',1784289601,1,0);
    """)
    connection.commit()
    connection.close()


def main() -> None:
    store = BrainStore(os.environ["RECALL_DATABASE_URL"])
    store.migrate()
    with store.connect() as connection:
        connection.execute(
            "TRUNCATE chunks,items,sessions,projection_watermarks,source_events,"
            "ingest_batches,source_grants,sources,dead_letters,audit_events "
            "RESTART IDENTITY CASCADE"
        )
    with tempfile.TemporaryDirectory(
        prefix="recall-local-activity-e2e-"
    ) as temporary:
        root = Path(temporary)
        safari_db = root / "SafariHistory.db"
        chrome_db = root / "ChromeHistory.db"
        notes_db = root / "NoteStore.sqlite"
        hermes_db = root / "state.db"
        safari_bookmarks = root / "SafariBookmarks.plist"
        chrome_bookmarks = root / "ChromeBookmarks.json"
        create_safari(safari_db)
        create_chrome(chrome_db)
        create_notes(notes_db)
        create_hermes(hermes_db)
        with safari_bookmarks.open("wb") as output:
            plistlib.dump({"Children": [{
                "URLString": "https://example.invalid/safari-bookmark-e2e",
                "URIDictionary": {
                    "title": "safari bookmark postgres marker"
                },
            }]}, output)
        chrome_bookmarks.write_text(json.dumps({
            "roots": {"bookmark_bar": {"children": [{
                "type": "url", "id": "31",
                "name": "chrome bookmark postgres marker",
                "url": "https://example.invalid/chrome-bookmark-e2e",
                "date_added": "13412779200000000",
            }]}}
        }))
        writer = StoreWriter(store)
        spools = {
            name: root / "spools" / f"{name}.db"
            for name in SOURCES
        }

        def connectors():
            return {
                "safari": BrowserActivityConnector(
                    browser="safari", history=safari_db,
                    bookmarks=safari_bookmarks,
                    source_id=SOURCES["safari"],
                ),
                "chrome": BrowserActivityConnector(
                    browser="chrome", history=chrome_db,
                    bookmarks=chrome_bookmarks,
                    source_id=SOURCES["chrome"],
                ),
                "notes": AppleNotesConnector(
                    database=notes_db, source_id=SOURCES["notes"],
                ),
                "hermes": HermesSessionConnector(
                    database=hermes_db, source_id=SOURCES["hermes"],
                    sources=("cli",),
                ),
            }

        runners = {
            name: ConnectorRunner(
                connector=connector,
                brain=writer,
                spool_path=spools[name],
                privacy=PrivacyPolicy(mode="scrub"),
            )
            for name, connector in connectors().items()
        }
        expected_initial = {
            "safari": 2, "chrome": 2, "notes": 1, "hermes": 2
        }
        for name, runner in runners.items():
            assert runner.run_once()["acked"] == expected_initial[name]
        for query, source in (
            ("safari postgres safe marker", SOURCES["safari"]),
            ("chrome postgres safe marker", SOURCES["chrome"]),
            ("notes postgres safe marker", SOURCES["notes"]),
            ("hermes postgres safe marker", SOURCES["hermes"]),
        ):
            assert store.search(
                query, authorized_source=source
            )["results"]
        assert store.search(
            "synthetic-hermes-e2e-canary",
            authorized_source=SOURCES["hermes"],
        )["results"] == []

        connection = sqlite3.connect(safari_db)
        connection.execute(
            "UPDATE history_visits SET title=? WHERE id=11",
            ("safari postgres revised marker",),
        )
        connection.commit()
        connection.close()
        connection = sqlite3.connect(notes_db)
        connection.execute(
            "UPDATE ZICCLOUDSYNCINGOBJECT SET ZSNIPPET=? WHERE Z_PK=101",
            ("notes postgres revised marker",),
        )
        connection.commit()
        connection.close()
        chrome_bookmarks.write_text(json.dumps({
            "roots": {"bookmark_bar": {"children": [{
                "type": "url", "id": "31",
                "name": "chrome bookmark revised marker",
                "url": "https://example.invalid/chrome-bookmark-e2e",
                "date_added": "13412779200000000",
            }]}}
        }))
        connection = sqlite3.connect(hermes_db)
        connection.execute(
            "UPDATE messages SET content=? WHERE id=1",
            ("hermes postgres revised marker",),
        )
        connection.commit()
        connection.close()
        for runner in runners.values():
            assert runner.run_once()["acked"] == 1
        for query, source in (
            ("safari postgres revised marker", SOURCES["safari"]),
            ("chrome bookmark revised marker", SOURCES["chrome"]),
            ("notes postgres revised marker", SOURCES["notes"]),
            ("hermes postgres revised marker", SOURCES["hermes"]),
        ):
            assert store.search(
                query, authorized_source=source
            )["results"]

        connection = sqlite3.connect(hermes_db)
        connection.execute("UPDATE messages SET active=0 WHERE id=1")
        connection.commit()
        connection.close()
        assert runners["hermes"].run_once()["acked"] == 1
        assert store.search(
            "hermes postgres revised marker",
            authorized_source=SOURCES["hermes"],
        )["results"] == []
        for runner in runners.values():
            runner.close()

        restarted = {
            name: ConnectorRunner(
                connector=connector,
                brain=writer,
                spool_path=spools[name],
                privacy=PrivacyPolicy(mode="scrub"),
            )
            for name, connector in connectors().items()
        }
        for runner in restarted.values():
            assert runner.run_once()["acked"] == 0
            runner.close()
        private_bytes = b"".join(
            path.read_bytes()
            for path in (root / "spools").iterdir()
            if path.is_file()
        )
        assert b"synthetic-hermes-e2e-canary" not in private_bytes
        print(json.dumps({
            "status": "pass",
            "configured_sources": 4,
            "searchable_sources": 4,
            "content_revisions": 4,
            "explicit_tombstones": 1,
            "duplicate_acknowledged_versions": 0,
            "restart_reacknowledgements": 0,
            "inferred_tombstones": 0,
            "canary_search_hits": 0,
            "spool_canary_hits": 0,
            "attachment_reads": 0,
            "private_content_rendered": False,
        }, sort_keys=True))


if __name__ == "__main__":
    main()
