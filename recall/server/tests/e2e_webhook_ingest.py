#!/usr/bin/env python3
"""Fresh-PostgreSQL proof for the authenticated custom webhook lifecycle."""

from __future__ import annotations

import http.client
import json
import os
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "recall"))
sys.path.insert(0, str(ROOT / "recall/server"))

from recall_server.app import Handler
from recall_server.db import BrainStore


def body(*, text: str = "synthetic cobalt webhook", deleted: bool = False) -> dict:
    record = {"kind": "communication_message.v1"}
    if not deleted:
        record.update({
            "content_fidelity": "complete",
            "conversation_id": "synthetic-conversation",
            "direction": "inbound",
            "message_id": "synthetic-event",
            "text": text,
        })
    return {
        "schema_version": 1,
        "event_id": "synthetic-event",
        "parent_id": "synthetic-conversation",
        "occurred_at": "2026-07-18T20:00:00Z",
        "record": record,
        "deleted": deleted,
    }


def post(server, token: str, value: dict) -> tuple[int, dict]:
    payload = json.dumps(value).encode()
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_port, timeout=5,
    )
    connection.request(
        "POST",
        "/webhooks/v1/events",
        body=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
        },
    )
    response = connection.getresponse()
    raw = response.read()
    connection.close()
    return response.status, json.loads(raw)


def main() -> None:
    store = BrainStore(os.environ["RECALL_DATABASE_URL"])
    store.migrate()
    with store.connect() as connection:
        connection.execute(
            "TRUNCATE collector_credentials,session_export_cursors,chunks,items,"
            "sessions,projection_watermarks,source_events,ingest_batches,"
            "source_grants,sources,dead_letters,audit_events RESTART IDENTITY CASCADE"
        )
    webhook = store.create_collector_token(
        "synthetic-webhook",
        "synthetic:webhook",
        ["webhook"],
        principal_id="synthetic-owner",
        webhook_privacy_mode="scrub",
    )
    read_only = store.create_collector_token(
        "synthetic-read-only",
        "synthetic:webhook",
        ["read"],
        principal_id="synthetic-owner",
    )
    previous = {
        key: os.environ.get(key)
        for key in (
            "RECALL_AUTH_REQUIRED",
            "RECALL_HTTP_PROFILE",
            "RECALL_TRUST_TAILSCALE_HEADERS",
        )
    }
    os.environ.update({
        "RECALL_AUTH_REQUIRED": "1",
        "RECALL_HTTP_PROFILE": "public-edge",
        "RECALL_TRUST_TAILSCALE_HEADERS": "0",
    })
    Handler.store = store
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    canary = "password=synthetic-webhook-secret"
    try:
        first_status, first = post(
            server, webhook["token"], body(text=f"safe {canary} safe"),
        )
        replay_status, replay = post(
            server, webhook["token"], body(text=f"safe {canary} safe"),
        )
        changed_status, changed = post(
            server, webhook["token"], body(text="synthetic cobalt changed"),
        )
        denied_status, _ = post(server, read_only["token"], body())
        deleted_status, deleted = post(
            server, webhook["token"], body(deleted=True),
        )
        assert (first_status, replay_status, changed_status) == (201, 200, 201)
        assert denied_status == 401 and deleted_status == 201
        assert first["receipt"] == replay["receipt"]
        assert replay["replay"] is True
        assert changed["receipt"].endswith("?rev=2")
        assert deleted["receipt"].endswith("?rev=3")
        assert store.search(
            "synthetic cobalt", authorized_source="synthetic:webhook",
        )["results"] == []
        with store.connect() as connection:
            rows = connection.execute(
                "SELECT envelope FROM source_events WHERE source_id=%s",
                ("synthetic:webhook",),
            ).fetchall()
        assert len(rows) == 3
        assert canary not in json.dumps(rows)
        assert store.revoke_collector_token("synthetic-webhook")
        revoked_status, _ = post(server, webhook["token"], body())
        assert revoked_status == 401
        print(json.dumps({
            "status": "pass",
            "canonical_versions": 3,
            "replay_added_versions": 0,
            "same_replay_receipt": True,
            "unauthorized_mutations": 0,
            "post_delete_hits": 0,
            "privacy_canary_rows": 0,
            "revoked_status": 401,
        }, sort_keys=True))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        with store.connect() as connection:
            connection.execute(
                "TRUNCATE collector_credentials,session_export_cursors,chunks,items,"
                "sessions,projection_watermarks,source_events,ingest_batches,"
                "source_grants,sources,dead_letters,audit_events RESTART IDENTITY CASCADE"
            )
        store.close()


if __name__ == "__main__":
    main()
