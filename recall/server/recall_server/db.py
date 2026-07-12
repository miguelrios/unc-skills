from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from . import PROJECTOR_VERSION
from .projectors import advisory_lock_key, canonical_json, event_receipt, project, redact_text, validate_envelope


class IdempotencyConflict(Exception):
    pass


class BrainStore:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def migrate(self) -> None:
        schema_dir = Path(__file__).resolve().parents[1] / "schema"
        with self.connect() as conn:
            for schema in sorted(schema_dir.glob("*.sql")):
                conn.execute(schema.read_text())

    def create_collector_token(self, name: str, source_id: str | None, scopes: list[str]) -> dict:
        allowed = {"read", "write", "metrics"}
        if not name or not scopes or set(scopes) - allowed:
            raise ValueError("invalid collector credential")
        plaintext = "rcl_" + secrets.token_urlsafe(32)
        digest = hashlib.sha256(plaintext.encode()).hexdigest()
        credential_id = uuid.uuid4()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO collector_credentials(id,name,token_sha256,source_id,scopes)
                   VALUES (%s,%s,%s,%s,%s)""",
                (credential_id, name, digest, source_id, scopes),
            )
            conn.execute(
                "INSERT INTO audit_events(operation,status,metadata) VALUES ('credential.create','success',%s)",
                (json.dumps({"credential_id": str(credential_id), "name": name, "source_id": source_id, "scopes": scopes}),),
            )
        return {"id": str(credential_id), "name": name, "token": plaintext, "source_id": source_id, "scopes": scopes}

    def revoke_collector_token(self, name: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "UPDATE collector_credentials SET revoked_at=now() WHERE name=%s AND revoked_at IS NULL RETURNING id",
                (name,),
            ).fetchone()
            conn.execute(
                "INSERT INTO audit_events(operation,status,metadata) VALUES ('credential.revoke',%s,%s)",
                ("success" if row else "not_found", json.dumps({"name": name})),
            )
            return bool(row)

    def authenticate_bearer(self, plaintext: str, required_scope: str) -> dict | None:
        digest = hashlib.sha256(plaintext.encode()).hexdigest()
        with self.connect() as conn:
            row = conn.execute(
                """SELECT id,name,source_id,scopes FROM collector_credentials
                   WHERE token_sha256=%s AND revoked_at IS NULL""",
                (digest,),
            ).fetchone()
            if not row or required_scope not in row["scopes"]:
                return None
            return row

    def service_metrics(self) -> dict:
        with self.connect() as conn:
            return {
                "source_events": conn.execute("SELECT count(*) AS n FROM source_events").fetchone()["n"],
                "dead_letters": conn.execute("SELECT count(*) AS n FROM dead_letters").fetchone()["n"],
                "projection_lag": conn.execute(
                    """SELECT GREATEST(0,
                       COALESCE((SELECT max(id) FROM source_events),0) -
                       COALESCE((SELECT last_event_id FROM projection_watermarks WHERE projector='items'),0)) AS n"""
                ).fetchone()["n"],
                "source_freshness_seconds": conn.execute(
                    "SELECT COALESCE(GREATEST(0, extract(epoch FROM now() - max(created_at)))::bigint, 0) AS n FROM source_events"
                ).fetchone()["n"],
            }

    def record_dead_letter(self, error_code: str, summary: str) -> None:
        """Record rejection metadata only; never persist the rejected payload."""
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO dead_letters(error_code,error_summary) VALUES (%s,%s)",
                (error_code[:100], redact_text(summary)[:500]),
            )
            conn.execute(
                "INSERT INTO audit_events(operation,status,metadata) VALUES ('ingest','rejected',%s)",
                (json.dumps({"error_code": error_code[:100]}),),
            )

    def ingest(self, idempotency_key: str, events: list[dict]) -> tuple[dict, bool]:
        if not idempotency_key or len(idempotency_key) > 200:
            raise ValueError("invalid idempotency key")
        for envelope in events:
            validate_envelope(envelope)
        request_hash = hashlib.sha256(canonical_json(events)).hexdigest()
        batch_id = uuid.uuid4()
        with self.connect() as conn:
            with conn.transaction():
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    ("batch\x1f" + idempotency_key,),
                )
                existing = conn.execute(
                    "SELECT request_sha256, acknowledgement FROM ingest_batches WHERE idempotency_key=%s FOR UPDATE",
                    (idempotency_key,),
                ).fetchone()
                if existing:
                    if existing["request_sha256"] != request_hash:
                        raise IdempotencyConflict("idempotency key reused with different request")
                    return existing["acknowledgement"], True

                placeholder = {"batch_id": str(batch_id), "status": "building"}
                conn.execute(
                    "INSERT INTO ingest_batches(id,idempotency_key,request_sha256,status,acknowledgement) VALUES (%s,%s,%s,'committed',%s)",
                    (batch_id, idempotency_key, request_hash, json.dumps(placeholder)),
                )
                receipts: list[str] = []
                inserted = 0
                duplicate_events = 0
                source_principals: dict[str, str] = {}
                for envelope in events:
                    source_id = envelope["source_id"]
                    principal_id = envelope["principal_id"]
                    if source_id in source_principals and source_principals[source_id] != principal_id:
                        raise ValueError("source principal mismatch within batch")
                    source_principals[source_id] = principal_id
                for source_id, principal_id in source_principals.items():
                    conn.execute(
                        "INSERT INTO sources(id,principal_id) VALUES (%s,%s) ON CONFLICT(id) DO NOTHING",
                        (source_id, principal_id),
                    )
                    source = conn.execute("SELECT principal_id FROM sources WHERE id=%s", (source_id,)).fetchone()
                    if source["principal_id"] != principal_id:
                        raise ValueError("source principal mismatch")
                    conn.execute(
                        "INSERT INTO source_grants(source_id,principal_id,permission) VALUES (%s,%s,'owner') ON CONFLICT DO NOTHING",
                        (source_id, principal_id),
                    )
                for envelope in events:
                    source_id = envelope["source_id"]
                    # Serialize revision allocation for one source-native identity while
                    # allowing unrelated identities to ingest concurrently.
                    conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                        (advisory_lock_key(source_id, envelope["native_id"]),),
                    )
                    existing_event = conn.execute(
                        "SELECT id,revision FROM source_events WHERE source_id=%s AND native_id=%s AND content_sha256=%s",
                        (source_id, envelope["native_id"], envelope["content_sha256"]),
                    ).fetchone()
                    if existing_event:
                        revision = existing_event["revision"]
                        duplicate_events += 1
                    else:
                        revision = conn.execute(
                            "SELECT COALESCE(max(revision),0)+1 AS revision FROM source_events WHERE source_id=%s AND native_id=%s",
                            (source_id, envelope["native_id"]),
                        ).fetchone()["revision"]
                        row = conn.execute(
                            """INSERT INTO source_events(source_id,native_id,native_parent_id,kind,occurred_at,observed_at,
                               principal_id,visibility,content_type,content_sha256,revision,envelope,is_tombstone,batch_id)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                            (source_id, envelope["native_id"], envelope.get("native_parent_id"), envelope["kind"],
                             envelope["occurred_at"], envelope["observed_at"], envelope["principal_id"],
                             envelope["visibility"], envelope["content_type"], envelope["content_sha256"], revision,
                             json.dumps(envelope), envelope["kind"] == "tombstone", batch_id),
                        ).fetchone()
                        self._project_one(conn, row["id"], envelope, revision)
                        inserted += 1
                    receipts.append(event_receipt(source_id, envelope["native_id"], revision))

                acknowledgement = {
                    "batch_id": str(batch_id),
                    "status": "committed",
                    "inserted": inserted,
                    "duplicate_events": duplicate_events,
                    "receipts": receipts,
                }
                conn.execute("UPDATE ingest_batches SET acknowledgement=%s WHERE id=%s", (json.dumps(acknowledgement), batch_id))
                conn.execute(
                    "INSERT INTO audit_events(operation,status,metadata) VALUES ('ingest','success',%s)",
                    (json.dumps({"batch_id": str(batch_id), "event_count": len(events), "request_sha256": request_hash}),),
                )
                return acknowledgement, False

    def _project_one(self, conn, event_id: int, envelope: dict, revision: int) -> None:
        session_id = envelope.get("native_parent_id") or envelope["native_id"]
        if envelope["kind"] == "tombstone":
            target = envelope.get("content", {}).get("target_native_id") or envelope["native_id"]
            conn.execute(
                "UPDATE items SET deleted_at=now() WHERE source_id=%s AND event_native_id=%s AND deleted_at IS NULL",
                (envelope["source_id"], target),
            )
            self._advance_projector(conn, event_id)
            return
        items, metadata = project(envelope, revision)
        if not items and set(metadata) <= {"projector_version", "harness"}:
            self._advance_projector(conn, event_id)
            return
        conn.execute(
            """INSERT INTO sessions(source_id,native_id,principal_id,harness,started_at,ended_at,metadata,projector_version)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT(source_id,native_id) DO UPDATE SET
                 started_at=LEAST(sessions.started_at,excluded.started_at),
                 ended_at=GREATEST(sessions.ended_at,excluded.ended_at),
                 metadata=sessions.metadata || excluded.metadata,
                 projector_version=excluded.projector_version,rebuilt_at=now()""",
            (envelope["source_id"], session_id, envelope["principal_id"], metadata.get("harness"),
             envelope["occurred_at"], envelope["occurred_at"], json.dumps(metadata), PROJECTOR_VERSION),
        )
        for item in items:
            occurred = item["occurred_at"]
            if isinstance(occurred, (int, float)):
                occurred = None if occurred is None else __import__("datetime").datetime.fromtimestamp(occurred, __import__("datetime").timezone.utc)
            row = conn.execute(
                """INSERT INTO items(event_id,source_id,session_native_id,event_native_id,ordinal,occurred_at,role,surface,
                   text_redacted,receipt,projector_version) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (event_id, envelope["source_id"], session_id, envelope["native_id"], item["ordinal"], occurred,
                 item["role"], item["surface"], item["text_redacted"], item["receipt"], PROJECTOR_VERSION),
            ).fetchone()
            conn.execute(
                "INSERT INTO chunks(item_id,ordinal,text_redacted,receipt) VALUES (%s,0,%s,%s)",
                (row["id"], item["text_redacted"], item["receipt"]),
            )
        self._advance_projector(conn, event_id)

    def _advance_projector(self, conn, event_id: int) -> None:
        conn.execute(
            """INSERT INTO projection_watermarks(projector,version,last_event_id) VALUES ('items',%s,%s)
               ON CONFLICT(projector) DO UPDATE SET version=excluded.version,
               last_event_id=GREATEST(projection_watermarks.last_event_id,excluded.last_event_id),updated_at=now()""",
            (PROJECTOR_VERSION, event_id),
        )

    def resolve(self, receipt: str) -> dict | None:
        event_part = receipt.split("#", 1)[0]
        try:
            base, query = event_part.rsplit("?rev=", 1)
            source_native = base.removeprefix("recall://")
            source_id, native_id = source_native.split("/", 1)
            revision = int(query)
        except (ValueError, TypeError):
            raise ValueError("invalid receipt")
        with self.connect() as conn:
            event = conn.execute(
                """SELECT id,source_id,native_id,native_parent_id,kind,occurred_at,observed_at,principal_id,
                   visibility,content_type,content_sha256,revision,is_tombstone
                   FROM source_events WHERE source_id=%s AND native_id=%s AND revision=%s""",
                (source_id, native_id, revision),
            ).fetchone()
            if not event:
                return None
            items = conn.execute(
                """SELECT ordinal,occurred_at,role,surface,text_redacted,receipt FROM items
                   WHERE event_id=%s AND deleted_at IS NULL ORDER BY ordinal""",
                (event["id"],),
            ).fetchall()
            return {"event": {key: value for key, value in event.items() if key != "id"}, "items": items}

    def rebuild(self) -> dict:
        with self.connect() as conn:
            with conn.transaction():
                before = conn.execute("SELECT count(*) AS n FROM items WHERE deleted_at IS NULL").fetchone()["n"]
                conn.execute("TRUNCATE chunks,items,sessions,projection_watermarks RESTART IDENTITY")
                rows = conn.execute("SELECT id,envelope,revision FROM source_events ORDER BY id").fetchall()
                for row in rows:
                    self._project_one(conn, row["id"], row["envelope"], row["revision"])
                after = conn.execute("SELECT count(*) AS n FROM items WHERE deleted_at IS NULL").fetchone()["n"]
                return {"events": len(rows), "items_before": before, "items_after": after}

    def export_raw(self) -> list[dict]:
        """Admin/offline API only; intentionally not routed by the HTTP app."""
        with self.connect() as conn:
            return [row["envelope"] for row in conn.execute("SELECT envelope FROM source_events ORDER BY id")]
