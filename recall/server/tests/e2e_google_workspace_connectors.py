#!/usr/bin/env python3
"""Fresh-PostgreSQL E2E for the four typed Google Workspace connectors."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "recall"))
sys.path.insert(0, str(ROOT / "recall/server"))

from connectors.google_workspace import (
    GmailConnector,
    GoogleCalendarConnector,
    GoogleContactsConnector,
    GoogleDriveConnector,
)
from connectors.sdk import ConnectorRunner
from privacy.policy import PrivacyPolicy
from recall_server.db import BrainStore
from recall_server.projectors import canonical_json


class Rail:
    def run(self, operation, params):
        if operation == "gmail.messages.list":
            return {"messages": [{"id": "message-1"}]}
        if operation == "gmail.messages.get":
            body = base64.urlsafe_b64encode(
                b"workspace gmail marker api_key=google-e2e-private-canary"
            ).decode().rstrip("=")
            return {
                "id": "message-1",
                "threadId": "thread-1",
                "historyId": "100",
                "internalDate": "1784332800000",
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        {"name": "From", "value": "friend@example.invalid"},
                        {"name": "To", "value": "owner@example.invalid"},
                        {"name": "Subject", "value": "workspace gmail marker"},
                    ],
                    "body": {"data": body},
                },
            }
        if operation == "calendar.events.list":
            return {
                "items": [{
                    "id": "event-1",
                    "status": "confirmed",
                    "summary": "workspace calendar marker",
                    "updated": "2026-07-18T01:00:00Z",
                    "start": {"dateTime": "2026-07-18T10:00:00Z"},
                    "end": {"dateTime": "2026-07-18T11:00:00Z"},
                }],
                "nextSyncToken": "calendar-sync-1",
            }
        if operation == "people.people.connections.list":
            return {
                "connections": [{
                    "resourceName": "people/c1",
                    "metadata": {
                        "sources": [{"updateTime": "2026-07-18T02:00:00Z"}],
                    },
                    "names": [{"displayName": "workspace contact marker"}],
                    "emailAddresses": [{"value": "contact@example.invalid"}],
                }],
                "nextSyncToken": "contacts-sync-1",
            }
        if operation == "drive.changes.getStartPageToken":
            return {"startPageToken": "drive-start-1"}
        if operation == "drive.files.list":
            return {"files": [{
                "id": "file-1",
                "name": "workspace drive marker",
                "mimeType": "text/plain",
                "modifiedTime": "2026-07-18T03:00:00Z",
            }]}
        raise AssertionError("unexpected synthetic operation")

    def export_document(self, *, file_id, mime_type="text/plain"):
        raise AssertionError("metadata-only fixture must not export")


class StoreWriter:
    def __init__(self, store):
        self.store = store

    def ingest(self, events):
        key = "google-workspace-e2e-" + hashlib.sha256(canonical_json(events)).hexdigest()
        acknowledgement, replay = self.store.ingest(key, events)
        return {**acknowledgement, "replay": replay}


def main() -> None:
    store = BrainStore(os.environ["RECALL_DATABASE_URL"])
    store.migrate()
    with store.connect() as connection:
        connection.execute(
            "TRUNCATE chunks,items,sessions,projection_watermarks,source_events,"
            "ingest_batches,source_grants,sources,dead_letters,audit_events "
            "RESTART IDENTITY CASCADE"
        )
    rail = Rail()
    sources = {
        "gmail": "synthetic:google:gmail:e2e",
        "calendar": "synthetic:google:calendar:e2e",
        "contacts": "synthetic:google:contacts:e2e",
        "drive": "synthetic:google:drive:e2e",
    }
    connectors = (
        GmailConnector(
            rail=rail,
            source_id=sources["gmail"],
            own_addresses=("owner@example.invalid",),
        ),
        GoogleCalendarConnector(
            rail=rail,
            source_id=sources["calendar"],
        ),
        GoogleContactsConnector(
            rail=rail,
            source_id=sources["contacts"],
        ),
        GoogleDriveConnector(
            rail=rail,
            source_id=sources["drive"],
        ),
    )
    with tempfile.TemporaryDirectory(prefix="recall-google-connectors-e2e-") as directory:
        acked = 0
        spools = []
        for connector in connectors:
            spool = Path(directory) / f"{connector.connector_id}.db"
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
            acked += result["acked"]
            spools.append(spool)
            runner.close()
        assert store.search(
            "workspace gmail marker",
            authorized_source=sources["gmail"],
        )["results"]
        assert store.search(
            "workspace calendar marker",
            authorized_source=sources["calendar"],
        )["results"]
        assert store.search(
            "workspace contact marker",
            authorized_source=sources["contacts"],
        )["results"]
        assert store.search(
            "workspace drive marker",
            authorized_source=sources["drive"],
        )["results"]
        assert store.search(
            "google-e2e-private-canary",
            authorized_source=sources["gmail"],
        )["results"] == []
        assert all(
            b"google-e2e-private-canary" not in spool.read_bytes()
            for spool in spools
        )
    print(json.dumps({
        "status": "pass",
        "connectors": len(connectors),
        "records_acked": acked,
        "typed_search_hits": 4,
        "canary_search_hits": 0,
        "spool_canary_hits": 0,
        "live_grants": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
