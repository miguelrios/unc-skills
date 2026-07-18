#!/usr/bin/env python3
"""Fresh-PostgreSQL proof for the disabled managed-auth adapter contract."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RECALL = ROOT / "recall"
SERVER = RECALL / "server"
sys.path.insert(0, str(RECALL))
sys.path.insert(0, str(SERVER))

from connectors.managed_auth import (
    ManagedAuthConnector,
    ManagedPage,
    ManagedProjection,
    ManagedRecord,
)
from connectors.sdk import ConnectorRunError, ConnectorRunner
from privacy.policy import PrivacyPolicy
from recall_server.db import BrainStore
from recall_server.projectors import canonical_json


SOURCE = "synthetic:managed-auth:postgres"


class StoreWriter:
    def __init__(self, store):
        self.store = store

    def ingest(self, events):
        key = "managed-auth-e2e-" + hashlib.sha256(
            canonical_json(events)
        ).hexdigest()
        acknowledgement, replay = self.store.ingest(key, events)
        return {**acknowledgement, "replay": replay}


class Transport:
    bound_source_id = SOURCE

    def __init__(self):
        self.pages = [
            ManagedPage(records=(ManagedRecord(
                id="managed-e2e-1",
                last_action="ADDED",
                deleted_at=None,
                data={
                    "text": (
                        "managed auth postgres marker "
                        "api_key=synthetic-managed-auth-canary"
                    ),
                    "updated_at": "2026-07-18T10:00:00Z",
                },
            ),), next_cursor="opaque-1", has_more=False),
            ManagedPage(records=(), next_cursor="opaque-2", has_more=False),
            ManagedPage(records=(ManagedRecord(
                id="managed-e2e-1",
                last_action="UPDATED",
                deleted_at=None,
                data={
                    "text": "managed auth revised marker",
                    "updated_at": "2026-07-18T11:00:00Z",
                },
            ),), next_cursor="opaque-3", has_more=False),
            ManagedPage(records=(ManagedRecord(
                id="managed-e2e-1",
                last_action="DELETED",
                deleted_at="2026-07-18T12:00:00Z",
                data={},
            ),), next_cursor="opaque-4", has_more=False),
        ]
        self.cursors = []
        self.revoked = 0

    def fetch_records(
        self, *, connection_id, provider_config_key, model, cursor
    ):
        assert (
            connection_id, provider_config_key, model
        ) == ("connection-e2e", "provider-e2e", "SyntheticDocument")
        self.cursors.append(cursor)
        return self.pages.pop(0)

    def revoke(self, *, connection_id, provider_config_key):
        assert (connection_id, provider_config_key) == (
            "connection-e2e", "provider-e2e"
        )
        self.revoked += 1


def mapper(record):
    return ManagedProjection(
        occurred_at=record.data["updated_at"],
        content={
            "kind": "document.v1",
            "document_id": record.id,
            "mime_type": "text/plain",
            "name": "Synthetic managed document",
            "surface": "managed_auth_synthetic",
            "text": record.data["text"],
        },
        provenance={"uri": "connector://managed-auth/synthetic"},
    )


def main() -> None:
    store = BrainStore(os.environ["RECALL_DATABASE_URL"])
    store.migrate()
    with store.connect() as connection:
        connection.execute(
            "TRUNCATE chunks,items,sessions,projection_watermarks,source_events,"
            "ingest_batches,source_grants,sources,dead_letters,audit_events "
            "RESTART IDENTITY CASCADE"
        )
    with tempfile.TemporaryDirectory(prefix="recall-managed-auth-e2e-") as temporary:
        transport = Transport()
        connector = ManagedAuthConnector(
            source_id=SOURCE,
            connection_id="connection-e2e",
            provider_config_key="provider-e2e",
            model="SyntheticDocument",
            record_kind="document.v1",
            transport=transport,
            mapper=mapper,
        )
        secret = b"synthetic-managed-webhook-secret"
        body = json.dumps({
            "connectionId": "connection-e2e",
            "providerConfigKey": "provider-e2e",
            "model": "SyntheticDocument",
            "responseResults": {"added": 1, "updated": 0, "deleted": 0},
            "modifiedAfter": "2026-07-18T10:00:00Z",
        }, separators=(",", ":")).encode()
        connector.accept_wakeup(
            body=body,
            signature=hmac.new(secret, body, hashlib.sha256).hexdigest(),
            secret=secret,
        )
        spool = Path(temporary) / "state.db"
        runner = ConnectorRunner(
            connector=connector,
            brain=StoreWriter(store),
            spool_path=spool,
            privacy=PrivacyPolicy(mode="scrub"),
        )
        assert runner.run_once()["acked"] == 1
        assert runner.run_once()["acked"] == 0
        assert store.search(
            "managed auth postgres marker", authorized_source=SOURCE
        )["results"]
        assert store.search(
            "synthetic-managed-auth-canary", authorized_source=SOURCE
        )["results"] == []
        assert runner.run_once()["acked"] == 1
        assert store.search(
            "managed auth revised marker", authorized_source=SOURCE
        )["results"]
        assert runner.run_once()["acked"] == 1
        assert store.search(
            "managed auth revised marker", authorized_source=SOURCE
        )["results"] == []
        runner.close()
        assert transport.cursors == [None, "opaque-1", "opaque-2", "opaque-3"]
        connector.revoke()
        assert transport.revoked == 1
        try:
            connector.pull("opaque-4")
        except ConnectorRunError as error:
            assert error.error_code == "connector_authority_revoked"
        else:
            raise AssertionError("revoked managed authority still read")
        assert b"synthetic-managed-auth-canary" not in spool.read_bytes()
        print(json.dumps({
            "status": "pass",
            "signed_wakeups": 1,
            "configured_sources": 1,
            "searchable_sources": 1,
            "content_revisions": 1,
            "explicit_tombstones": 1,
            "conditional_empty_pages": 1,
            "cursor_before_ack": 0,
            "duplicate_acknowledged_versions": 0,
            "unauthorized_source_reads": 0,
            "revoked_reads": 0,
            "canary_search_hits": 0,
            "spool_canary_hits": 0,
            "private_content_rendered": False,
            "default_third_party_transfers": 0,
        }, sort_keys=True))


if __name__ == "__main__":
    main()
