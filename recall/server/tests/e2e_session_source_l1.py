#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SERVER))

from client.mac import canonical_envelope  # noqa: E402
from recall_server.db import BrainStore  # noqa: E402


def cowork(turn: int, role: str, text: str) -> dict:
    native_id = f"session-l1/message-{turn}"
    value = canonical_envelope(
        source_id="cowork:mac:e2e-l1",
        native_id=native_id,
        kind="connector_record",
        content={
            "session_id": "session-l1", "message_id": f"message-{turn}",
            "role": role, "text": text,
        },
        principal_id="owner",
        visibility="private",
        occurred_at=f"2026-07-16T01:00:0{turn}Z",
        provenance={
            "uri": f"connector://anthropic.cowork-local/{native_id}",
            "connector_id": "anthropic.cowork-local",
        },
    )
    # This is the exact legacy shape: the old SDK self-parented every record.
    value["native_parent_id"] = native_id
    return value


def fingerprint(store: BrainStore) -> tuple[int, str, int, str]:
    with store.connect() as conn:
        events = conn.execute(
            "SELECT id,source_id,native_id,revision,content_sha256 FROM source_events ORDER BY id"
        ).fetchall()
        receipts = conn.execute("SELECT id,receipt FROM items ORDER BY id").fetchall()
    event_hash = hashlib.sha256(json.dumps(events, default=str, sort_keys=True).encode()).hexdigest()
    receipt_hash = hashlib.sha256(json.dumps(receipts, default=str, sort_keys=True).encode()).hexdigest()
    return len(events), event_hash, len(receipts), receipt_hash


def main() -> None:
    store = BrainStore(os.environ["RECALL_DATABASE_URL"])
    store.migrate()
    with store.connect() as conn:
        conn.execute(
            "TRUNCATE session_export_cursors,chunks,entities,items,sessions,projection_watermarks,"
            "source_events,ingest_batches,source_profiles,source_grants,sources,projection_backfills,"
            "dead_letters,audit_events RESTART IDENTITY CASCADE"
        )

    events = [
        cowork(1, "user", "l1 cobalt budget question"),
        cowork(2, "assistant", "l1 cobalt budget answer"),
        cowork(3, "user", "l1 cobalt budget followup"),
    ]
    acknowledgement, replay = store.ingest("l1-cowork-legacy", events)
    assert acknowledgement["inserted"] == 3 and not replay

    # Recreate only the old derived fragmentation; canonical rows and receipts stay untouched.
    with store.connect() as conn:
        for event in events:
            native = event["native_id"]
            row = conn.execute(
                "SELECT principal_id,harness,started_at,ended_at,metadata,projector_version "
                "FROM sessions WHERE source_id=%s AND native_id='session-l1'",
                (event["source_id"],),
            ).fetchone()
            conn.execute(
                """INSERT INTO sessions(source_id,native_id,principal_id,harness,started_at,ended_at,metadata,projector_version)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    event["source_id"], native, row["principal_id"], row["harness"],
                    row["started_at"], row["ended_at"], json.dumps(row["metadata"]), row["projector_version"],
                ),
            )
            conn.execute(
                "UPDATE items SET session_native_id=%s WHERE source_id=%s AND event_native_id=%s",
                (native, event["source_id"], native),
            )
        conn.execute(
            "DELETE FROM sessions WHERE source_id=%s AND native_id='session-l1'",
            (events[0]["source_id"],),
        )
    before = fingerprint(store)

    first = store.backfill_cowork_sessions(batch_size=1, max_batches=1)
    assert first["events_scanned"] == 1 and not first["completed"]
    resumed = store.backfill_cowork_sessions(batch_size=2)
    assert resumed["completed"] and resumed["events_moved"] == 2
    replayed = store.backfill_cowork_sessions(batch_size=2)
    assert replayed["events_scanned"] == 0 and replayed["items_moved"] == 0
    assert fingerprint(store) == before

    with store.connect() as conn:
        sessions = conn.execute(
            "SELECT native_id FROM sessions WHERE source_id=%s ORDER BY native_id",
            (events[0]["source_id"],),
        ).fetchall()
    assert [row["native_id"] for row in sessions] == ["session-l1"]

    store.set_source_profile({
        "source_id": events[0]["source_id"], "family": "coding_history",
        "quality": "standard", "freshness_half_life_days": 180,
    })
    store.set_source_alias("cowork", events[0]["source_id"])
    routed = store.search(
        "cobalt budget", {"source_id": events[0]["source_id"]}, 5,
    )
    assert routed["results"] and {row["source_id"] for row in routed["results"]} == {events[0]["source_id"]}
    assert routed["diagnostics"]["routing"]["requested_source_id"] == events[0]["source_id"]
    by_family = store.search("cobalt budget", {"source_family": "coding_history"}, 5)
    assert by_family["results"] and by_family["results"][0]["source_profile"]["family"] == "coding_history"
    by_alias = store.search("cobalt budget", {"source_alias": "cowork"}, 5)
    assert by_alias["results"] and {row["source_id"] for row in by_alias["results"]} == {events[0]["source_id"]}
    denied = store.search(
        "cobalt budget", {"source_id": events[0]["source_id"]}, 5,
        authorized_source="codex:mac:other",
    )
    assert denied["results"] == []

    shown = store.show(routed["results"][0]["receipt"], around="2026-07-16T01:00:02Z")
    assert [chunk["text"] for chunk in shown["chunks"]] == [
        "l1 cobalt budget question", "l1 cobalt budget answer", "l1 cobalt budget followup",
    ]
    exported = store.session_export(target=routed["results"][0]["receipt"], limit=10)
    assert exported["page"]["complete"] and len(exported["items"]) == 3
    assert exported["session"]["native_session_id"] == "session-l1"
    duplicate, duplicate_replay = store.ingest("l1-cowork-duplicate", events)
    assert not duplicate_replay and duplicate["inserted"] == 0 and duplicate["duplicate_events"] == 3
    assert fingerprint(store) == before
    print(json.dumps({
        "status": "pass", "session_reconstruction_accuracy": 1.0,
        "source_routing_accuracy": 1.0, "unauthorized_hits": 0,
        "canonical_and_receipt_fingerprint_stable": True,
        "duplicate_imports": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
