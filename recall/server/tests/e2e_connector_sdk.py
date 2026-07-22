#!/usr/bin/env python3
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

from connectors.sdk import ConnectorPage, ConnectorRecord, ConnectorRecordV2, ConnectorRunner
from privacy.policy import PrivacyPolicy
from recall_server.db import BrainStore
from recall_server.projectors import canonical_json


SOURCE = "synthetic:connector:postgres"


def value(native_id: str, text: str, *, deleted: bool = False) -> ConnectorRecord:
    return ConnectorRecord.from_mapping({
        "schema_version": 1,
        "native_id": native_id,
        "occurred_at": "2026-07-14T00:00:00Z",
        "content": {"text": text},
        "provenance": {"uri": f"connector://synthetic/{native_id}"},
        "deleted": deleted,
    })


def typed_value(
    native_id: str = "typed-message",
    *,
    fidelity: str = "complete",
    omissions: list[str] | None = None,
) -> ConnectorRecordV2:
    content = {
        "kind": "communication_message.v1", "conversation_id": "typed-thread",
        "message_id": native_id, "direction": "inbound",
        "content_fidelity": fidelity,
        "subject": "synthetic typed subject",
        "text": (
            "partial typed connector evidence"
            if fidelity == "partial" else "typed connector evidence"
        ),
    }
    if omissions is not None:
        content["content_omissions"] = omissions
    return ConnectorRecordV2.from_mapping({
        "schema_version": 2, "native_id": native_id, "native_parent_id": "typed-thread",
        "occurred_at": "2026-07-14T00:00:00Z",
        "content": content,
        "provenance": {"uri": "connector://synthetic/typed-message"}, "deleted": False,
    })


class Pages:
    connector_id = "synthetic.postgres"
    source_id = SOURCE

    def __init__(self):
        self.pages = {
            None: ConnectorPage(records=(
                value("safe-one", "connector postgres exact safe marker"),
                value("scrub-one", "keep context api_key=connector-postgres-secret-canary after"),
                typed_value(),
                typed_value(
                    "typed-partial-message", fidelity="partial",
                    omissions=["body_truncated"],
                ),
            ), next_cursor="page-1", has_more=True),
            "page-1": ConnectorPage(records=(
                value("scrub-one", "keep context api_key=connector-postgres-secret-canary after"),
                value("safe-one", "deletion bypass content", deleted=True),
            ), next_cursor="page-2", has_more=True),
            "page-2": ConnectorPage(records=(
                value("safe-one", "connector postgres exact safe marker"),
            ), next_cursor="done", has_more=False),
        }

    def pull(self, cursor):
        return self.pages[cursor]


class StoreWriter:
    def __init__(self, store: BrainStore):
        self.store = store

    def ingest(self, events):
        key = "connector-e2e-" + hashlib.sha256(canonical_json(events)).hexdigest()
        acknowledgement, replay = self.store.ingest(key, events)
        return {**acknowledgement, "replay": replay}


def main() -> None:
    store = BrainStore(os.environ["RECALL_DATABASE_URL"])
    store.migrate()
    with store.connect() as connection:
        connection.execute(
            "TRUNCATE chunks,items,sessions,projection_watermarks,source_events,"
            "ingest_batches,source_grants,sources,dead_letters,audit_events RESTART IDENTITY CASCADE"
        )
    with tempfile.TemporaryDirectory(prefix="recall-connector-e2e-") as temporary:
        spool = Path(temporary) / "connector.db"
        runner = ConnectorRunner(
            connector=Pages(), brain=StoreWriter(store), spool_path=spool,
            privacy=PrivacyPolicy(mode="scrub"),
        )
        first = runner.run_once()
        assert first["acked"] == 4
        assert first["privacy"]["actions"] == {"keep": 3, "scrub": 1}
        assert store.search("connector postgres exact safe marker", authorized_source=SOURCE)["results"]
        assert store.search("synthetic typed subject", authorized_source=SOURCE)["results"]
        partial = store.search(
            "partial typed connector evidence", authorized_source=SOURCE,
        )["results"]
        assert partial
        assert store.resolve(partial[0]["receipt"], authorized_source=SOURCE)
        assert store.search("connector-postgres-secret-canary", authorized_source=SOURCE)["results"] == []
        with store.connect() as connection:
            assert connection.execute("SELECT count(*) AS n FROM source_events").fetchone()["n"] == 4
            assert connection.execute("SELECT count(*) AS n FROM items WHERE deleted_at IS NULL").fetchone()["n"] == 4
        second = runner.run_once()
        assert second["acked"] == 1
        assert second["deduplicated"] == 1
        assert store.search("connector postgres exact safe marker", authorized_source=SOURCE)["results"] == []
        third = runner.run_once()
        assert third["acked"] == 0
        assert third["deduplicated"] == 1
        assert store.search("connector postgres exact safe marker", authorized_source=SOURCE)["results"] == []
        assert runner.doctor()["pending"] == 0
        runner.close()
        assert b"connector-postgres-secret-canary" not in spool.read_bytes()
        print(json.dumps({
            "status": "pass", "records_acked": 5, "typed_records_acked": 2,
            "typed_search_hits": 2, "exact_repeats_suppressed": 2,
            "searchable_before_delete": 1,
            "searchable_after_delete": 0, "canary_search_hits": 0,
            "spool_canary_hits": 0, "pending": 0,
        }, sort_keys=True))


if __name__ == "__main__":
    main()
