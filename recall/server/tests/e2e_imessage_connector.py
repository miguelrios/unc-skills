#!/usr/bin/env python3
"""Fresh-PostgreSQL proof for read-only iMessage snapshot ingestion."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RECALL = ROOT / "recall"
SERVER = RECALL / "server"
sys.path.insert(0, str(RECALL))
sys.path.insert(0, str(SERVER))

from connectors.imessage import IMessageConnector
from connectors.sdk import ConnectorRunner
from privacy.policy import PrivacyPolicy
from recall_server.db import BrainStore
from recall_server.projectors import canonical_json


SOURCE = "synthetic:imessage:postgres"
APPLE_SECOND = 788_918_400_000_000_000


class StoreWriter:
    def __init__(self, store: BrainStore):
        self.store = store

    def ingest(self, events):
        key = "imessage-e2e-" + hashlib.sha256(canonical_json(events)).hexdigest()
        acknowledgement, replay = self.store.ingest(key, events)
        return {**acknowledgement, "replay": replay}


def create_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE message(
          ROWID INTEGER PRIMARY KEY,
          guid TEXT NOT NULL,
          text TEXT,
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
        INSERT INTO handle VALUES (1,'synthetic-sender@example.invalid','iMessage');
        INSERT INTO chat VALUES (1,'synthetic-chat-guid','synthetic-chat','iMessage');
        INSERT INTO message VALUES (
          1,'synthetic-guid-private','api_key=synthetic-imessage-e2e-canary',
          1,'iMessage',788918400000000000,0,0,0
        );
        INSERT INTO message VALUES (
          2,'synthetic-guid-safe','imessage postgres safe marker',
          1,'iMessage',788918401000000000,1,0,0
        );
        INSERT INTO chat_message_join VALUES (1,1);
        INSERT INTO chat_message_join VALUES (1,2);
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
    with tempfile.TemporaryDirectory(prefix="recall-imessage-e2e-") as temporary:
        root = Path(temporary)
        database = root / "chat.db"
        spool = root / "state" / "imessage.db"
        create_database(database)
        writer = StoreWriter(store)
        runner = ConnectorRunner(
            connector=IMessageConnector(database=database, source_id=SOURCE),
            brain=writer,
            spool_path=spool,
            privacy=PrivacyPolicy(mode="scrub"),
        )
        first = runner.run_once()
        assert first["acked"] == 2
        assert store.search(
            "imessage postgres safe marker",
            authorized_source=SOURCE,
        )["results"]
        assert store.search(
            "synthetic-imessage-e2e-canary",
            authorized_source=SOURCE,
        )["results"] == []

        connection = sqlite3.connect(database)
        connection.execute(
            "UPDATE message SET text=?,date_edited=? WHERE ROWID=2",
            ("imessage postgres revised marker", APPLE_SECOND + 2_000_000_000),
        )
        connection.commit()
        connection.close()
        revised = runner.run_once()
        assert revised["acked"] == 1
        assert store.search(
            "imessage postgres revised marker",
            authorized_source=SOURCE,
        )["results"]

        connection = sqlite3.connect(database)
        connection.execute("UPDATE message SET is_deleted=1 WHERE ROWID=2")
        connection.commit()
        connection.close()
        deleted = runner.run_once()
        assert deleted["acked"] == 1
        assert store.search(
            "imessage postgres revised marker",
            authorized_source=SOURCE,
        )["results"] == []
        runner.close()

        restarted = ConnectorRunner(
            connector=IMessageConnector(database=database, source_id=SOURCE),
            brain=writer,
            spool_path=spool,
            privacy=PrivacyPolicy(mode="scrub"),
        )
        unchanged = restarted.run_once()
        assert unchanged["acked"] == 0
        restarted.close()
        assert b"synthetic-imessage-e2e-canary" not in b"".join(
            path.read_bytes()
            for path in spool.parent.glob("imessage.db*")
            if path.is_file()
        )
        with store.connect() as connection:
            event_count = connection.execute(
                "SELECT count(*) AS n FROM source_events"
            ).fetchone()["n"]
        assert event_count == 4
        print(json.dumps({
            "status": "pass",
            "source_records": 2,
            "content_revisions": 1,
            "explicit_tombstones": 1,
            "duplicate_acknowledged_versions": 0,
            "restart_reacknowledgements": 0,
            "canary_search_hits": 0,
            "spool_canary_hits": 0,
            "private_content_rendered": False,
        }, sort_keys=True))


if __name__ == "__main__":
    main()
