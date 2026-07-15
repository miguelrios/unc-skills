#!/usr/bin/env python3
"""Fresh-PostgreSQL proof for the explicit ChatGPT/Cowork export inbox."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "recall"))
sys.path.insert(0, str(ROOT / "recall/server"))

from connectors.export_inbox import ExportInboxConnector
from connectors.sdk import ConnectorRunner
from privacy.policy import PrivacyPolicy
from recall_server.db import BrainStore
from recall_server.projectors import canonical_json


SOURCE = "synthetic:export-inbox:postgres"
FIXTURES = ROOT / "recall/tests/export_inbox_v1"


class StoreWriter:
    def __init__(self, store: BrainStore):
        self.store = store

    def ingest(self, events):
        key = "export-inbox-e2e-" + hashlib.sha256(canonical_json(events)).hexdigest()
        acknowledgement, replay = self.store.ingest(key, events)
        return {**acknowledgement, "replay": replay}


def truncate(store: BrainStore) -> None:
    with store.connect() as connection:
        connection.execute(
            "TRUNCATE chunks,items,sessions,projection_watermarks,source_events,"
            "ingest_batches,source_grants,sources,dead_letters,audit_events RESTART IDENTITY CASCADE"
        )


def main() -> None:
    store = BrainStore(os.environ["RECALL_DATABASE_URL"])
    store.migrate()
    truncate(store)
    try:
        with tempfile.TemporaryDirectory(prefix="recall-export-inbox-e2e-") as temporary:
            root = Path(temporary)
            inbox = root / "explicit-inbox"
            inbox.mkdir()
            shutil.copy(FIXTURES / "conversations.json", inbox / "renamed.json")
            shutil.copy(FIXTURES / "cowork.jsonl", inbox / "renamed.jsonl")
            catalog = root / "state" / "catalog.db"
            spool = root / "state" / "spool.db"
            connector = ExportInboxConnector(
                inbox=inbox, catalog_path=catalog, source_id=SOURCE, page_size=2,
                privacy_mode="scrub",
            )
            runner = ConnectorRunner(
                connector=connector, brain=StoreWriter(store), spool_path=spool,
                privacy=PrivacyPolicy(mode="scrub"),
            )
            for _ in range(3):
                assert runner.run_once()["acked"] == 2
            assert store.search("Plan the synthetic cobalt release", authorized_source=SOURCE)["results"]
            assert store.search("Synthetic Cowork beta decision", authorized_source=SOURCE)["results"]
            for canary in (
                "synthetic-chatgpt-secret-canary-91",
                "synthetic-cowork-secret-canary-92",
            ):
                assert store.search(canary, authorized_source=SOURCE)["results"] == []
                assert canary.encode() not in spool.read_bytes()

            before = store.doctor(SOURCE)
            assert before["source_events"] == 6 and before["live_items"] == 6
            for path in tuple(inbox.iterdir()):
                path.unlink()
            unchanged = connector.pull(runner._cursor())
            assert not any(record.deleted for record in unchanged.records)
            assert store.doctor(SOURCE)["live_items"] == 6

            for item in connector.exports():
                assert connector.queue_remove(item["export_id"])["status"] == "queued"
            for _ in range(12):
                runner.run_once()
                if all(item["status"] == "removed" for item in connector.exports()):
                    break
            else:
                raise AssertionError("explicit removals did not converge")
            after = store.doctor(SOURCE)
            assert after["source_events"] == 12 and after["live_items"] == 0
            assert store.search("synthetic cobalt", authorized_source=SOURCE)["results"] == []
            assert runner.doctor()["pending"] == 0
            runner.close()
            connector.close()
            for database in (catalog, spool):
                payload = database.read_bytes()
                assert b"synthetic-chatgpt-secret-canary-91" not in payload
                assert b"synthetic-cowork-secret-canary-92" not in payload
            print(json.dumps({
                "status": "pass", "ingested": 6, "tombstoned": 6,
                "live_items_after_removal": 0, "canary_search_hits": 0,
                "local_canary_hits": 0, "pending": 0,
            }, sort_keys=True))
    finally:
        truncate(store)


if __name__ == "__main__":
    main()
