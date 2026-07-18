#!/usr/bin/env python3
"""Fresh-PostgreSQL E2E for explicitly selected X activity streams."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "recall"))
sys.path.insert(0, str(ROOT / "recall/server"))

from connectors.sdk import ConnectorRunner
from connectors.x_activity import XActivityConnector
from privacy.policy import PrivacyPolicy
from recall_server.db import BrainStore
from recall_server.projectors import canonical_json


class Rail:
    def __init__(self):
        self.calls = []

    def request(self, operation, **parameters):
        self.calls.append((operation, parameters))
        if operation != "bookmarks.list":
            raise AssertionError("unselected X stream was called")
        canary = "".join(("x-e2e-", "private-canary"))
        return {
            "data": [{
                "id": "100",
                "text": f"social x marker api_key={canary}",
                "author_id": "888",
                "conversation_id": "100",
                "created_at": "2026-07-18T01:00:00.000Z",
                "public_metrics": {"like_count": 2},
            }],
            "meta": {},
        }


class StoreWriter:
    def __init__(self, store):
        self.store = store

    def ingest(self, events):
        key = "x-e2e-" + hashlib.sha256(canonical_json(events)).hexdigest()
        acknowledgement, replay = self.store.ingest(key, events)
        return {**acknowledgement, "replay": replay}


def main() -> None:
    source = "synthetic:x:activity:e2e"
    store = BrainStore(os.environ["RECALL_DATABASE_URL"])
    store.migrate()
    with store.connect() as connection:
        connection.execute(
            "TRUNCATE chunks,items,sessions,projection_watermarks,source_events,"
            "ingest_batches,source_grants,sources,dead_letters,audit_events "
            "RESTART IDENTITY CASCADE"
        )
    rail = Rail()
    connector = XActivityConnector(
        rail=rail,
        source_id=source,
        user_id="999",
        streams=("bookmark",),
    )
    with tempfile.TemporaryDirectory(prefix="recall-x-e2e-") as directory:
        spool = Path(directory) / "x.db"
        runner = ConnectorRunner(
            connector=connector,
            brain=StoreWriter(store),
            spool_path=spool,
            privacy=PrivacyPolicy(mode="scrub"),
        )
        result = runner.run_once()
        assert result["acked"] == 1
        assert runner.doctor()["checkpointed"]
        assert runner.doctor()["pending"] == 0
        assert store.search(
            "social x marker",
            authorized_source=source,
        )["results"]
        assert store.search(
            "x-e2e-private-canary",
            authorized_source=source,
        )["results"] == []
        assert b"x-e2e-private-canary" not in spool.read_bytes()
        assert [operation for operation, _parameters in rail.calls] == ["bookmarks.list"]
        runner.close()
    print(json.dumps({
        "status": "pass",
        "selected_streams": 1,
        "unselected_stream_calls": 0,
        "records_acked": 1,
        "typed_search_hits": 1,
        "canary_search_hits": 0,
        "spool_canary_hits": 0,
        "inferred_tombstones": 0,
        "live_grants": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
