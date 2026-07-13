from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from . import PROJECTOR_VERSION
from .projectors import advisory_lock_key, canonical_json, event_receipt, legacy_engine, partial_lexical_probes, phrase_query_spec, preferred_phrase_probes, project, redact_text, validate_envelope
from .ranking import DEFAULT_SEARCH_DEADLINE_MS, evidence_rank_components, should_run_partial


class IdempotencyConflict(Exception):
    pass


class SearchDeadlineExceeded(Exception):
    pass


class BrainStore:
    def __init__(self, dsn: str, search_deadline_ms: int | None = None):
        self.dsn = dsn
        configured = search_deadline_ms if search_deadline_ms is not None else int(os.environ.get("RECALL_SEARCH_DEADLINE_MS", str(DEFAULT_SEARCH_DEADLINE_MS)))
        if not 10 <= configured <= 2000:
            raise ValueError("search deadline must be between 10 and 2000 milliseconds")
        self.search_deadline_ms = configured

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
        if not events:
            raise ValueError("empty ingest batch")
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
                identities = sorted({(envelope["source_id"], envelope["native_id"]) for envelope in events})
                lock_keys = [advisory_lock_key(source_id, native_id) for source_id, native_id in identities]
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(value,0)) FROM unnest(%s::text[]) AS locks(value) ORDER BY value",
                    (lock_keys,),
                ).fetchall()
                wanted = ",".join(["(%s,%s)"] * len(identities))
                identity_params = [value for identity in identities for value in identity]
                existing_rows = conn.execute(
                    f"""WITH wanted(source_id,native_id) AS (VALUES {wanted})
                        SELECT event.id,event.source_id,event.native_id,event.content_sha256,event.revision
                        FROM source_events event JOIN wanted USING(source_id,native_id)""",
                    identity_params,
                ).fetchall()
                existing_by_content = {
                    (row["source_id"], row["native_id"], row["content_sha256"]): row["revision"]
                    for row in existing_rows
                }
                max_revision = {}
                for row in existing_rows:
                    identity = (row["source_id"], row["native_id"])
                    max_revision[identity] = max(max_revision.get(identity, 0), row["revision"])
                for envelope in events:
                    source_id = envelope["source_id"]
                    identity = (source_id, envelope["native_id"])
                    content_identity = (*identity, envelope["content_sha256"])
                    if content_identity in existing_by_content:
                        revision = existing_by_content[content_identity]
                        duplicate_events += 1
                    else:
                        revision = max_revision.get(identity, 0) + 1
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
                        existing_by_content[content_identity] = revision
                        max_revision[identity] = revision
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
            if item.get("entities"):
                with conn.cursor() as cursor:
                    cursor.executemany(
                        "INSERT INTO entities(item_id,source_id,kind,value,normalized) VALUES (%s,%s,%s,%s,%s)",
                        [
                            (row["id"], envelope["source_id"], entity["kind"], entity["value"], entity["normalized"])
                            for entity in item["entities"]
                        ],
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
                   FROM source_events event
                   WHERE source_id=%s AND native_id=%s AND revision=%s
                     AND NOT EXISTS (
                       SELECT 1 FROM source_events later
                       WHERE later.source_id=event.source_id
                         AND later.native_id=event.native_id
                         AND later.revision>event.revision
                         AND later.is_tombstone
                     )""",
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

    @staticmethod
    def _read_filters(filters: dict, authorized_source: str | None = None) -> tuple[str, list[Any]]:
        allowed = {"since", "until", "cwd", "branch", "harness"}
        if set(filters) - allowed:
            raise ValueError("unsupported search filter")
        clauses = ["i.deleted_at IS NULL"]
        params: list[Any] = []
        if authorized_source:
            clauses.append("i.source_id = %s"); params.append(authorized_source)
        if filters.get("since"):
            clauses.append("i.occurred_at >= %s::timestamptz"); params.append(filters["since"])
        if filters.get("until"):
            clauses.append("i.occurred_at <= %s::timestamptz"); params.append(filters["until"])
        if filters.get("cwd"):
            clauses.append("COALESCE(s.metadata->>'cwd','') ILIKE %s"); params.append("%" + str(filters["cwd"]) + "%")
        if filters.get("branch"):
            clauses.append("COALESCE(s.metadata->>'branch','') ILIKE %s"); params.append("%" + str(filters["branch"]) + "%")
        if filters.get("harness"):
            if filters["harness"] not in {"claude", "codex"}:
                raise ValueError("unsupported harness filter")
            clauses.append("s.harness = %s"); params.append(filters["harness"])
        return " AND ".join(clauses), params

    def _lexical_leg(self, conn, query: str, query_function: str, filters: dict,
                     leg: str, tier: int, *, exact: str | None = None,
                     limit: int = 400, authorized_source: str | None = None,
                     deadline_at: float | None = None) -> list[dict]:
        if query_function not in {"plainto_tsquery", "phraseto_tsquery", "websearch_to_tsquery"}:
            raise ValueError("unsupported query function")
        where, params = self._read_filters(filters, authorized_source)
        exact_sql = ""
        query_params: list[Any] = [query]
        if exact is not None:
            exact_sql = " AND strpos(lower(i.text_redacted), %s) > 0"
            query_params.append(exact.casefold())
        sql = f"""
            SELECT i.id,i.source_id,i.session_native_id,i.event_native_id,i.occurred_at,i.surface,
                   i.text_redacted,i.receipt,i.projector_version,s.started_at,s.ended_at,s.metadata,
                   se.envelope #>> '{{provenance,original_path}}' AS path,se.observed_at,
                   ts_rank_cd(to_tsvector('simple',i.text_redacted),
                              {query_function}('simple',%s),32) AS lexical_rank
            FROM items i
            JOIN sessions s ON s.source_id=i.source_id AND s.native_id=i.session_native_id
            JOIN source_events se ON se.id=i.event_id
            WHERE to_tsvector('simple',i.text_redacted) @@ {query_function}('simple',%s)
              AND {where}{exact_sql}
            ORDER BY lexical_rank DESC,i.occurred_at DESC NULLS LAST,i.id DESC
            LIMIT %s
        """
        # The same query feeds rank and match; filters remain structurally parameterized.
        values = [query, query, *params]
        if exact is not None:
            values.append(exact.casefold())
        values.append(limit)
        rows = self._execute_bounded(conn, sql, values, deadline_at).fetchall()
        return [{**dict(row), "leg": leg, "tier": tier} for row in rows]

    @staticmethod
    def _execute_bounded(conn, sql: str, values: list[Any] | tuple[Any, ...], deadline_at: float | None):
        if deadline_at is None:
            return conn.execute(sql, values)
        remaining_ms = int((deadline_at - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            raise SearchDeadlineExceeded("search deadline exceeded")
        conn.execute("SELECT set_config('statement_timeout', %s, true)", (f"{remaining_ms}ms",))
        try:
            return conn.execute(sql, values)
        except psycopg.errors.QueryCanceled as exc:
            raise SearchDeadlineExceeded("search deadline exceeded") from exc

    def _entity_leg(self, conn, values: list[str], filters: dict,
                    *, authorized_source: str | None = None, limit: int = 400,
                    deadline_at: float | None = None, tier: int = 3) -> list[dict]:
        normalized = sorted({value.casefold() for value in values if value})
        if not normalized:
            return []
        where, params = self._read_filters(filters, authorized_source)
        rows = self._execute_bounded(
            conn,
            f"""
            SELECT DISTINCT i.id,i.source_id,i.session_native_id,i.event_native_id,i.occurred_at,i.surface,
                   i.text_redacted,i.receipt,i.projector_version,s.started_at,s.ended_at,s.metadata,
                   se.envelope #>> '{{provenance,original_path}}' AS path,se.observed_at,
                   1.0::real AS lexical_rank
            FROM entities e
            JOIN items i ON i.id=e.item_id
            JOIN sessions s ON s.source_id=i.source_id AND s.native_id=i.session_native_id
            JOIN source_events se ON se.id=i.event_id
            WHERE octet_length(e.normalized) <= 512 AND e.normalized = ANY(%s) AND {where}
            ORDER BY i.id DESC
            LIMIT %s
            """,
            [normalized, *params, limit],
            deadline_at,
        ).fetchall()
        return [{**dict(row), "leg": "entity", "tier": tier} for row in rows]

    def search(self, query: str, filters: dict | None = None, limit: int = 10,
               authorized_source: str | None = None) -> dict:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query is required")
        if not isinstance(limit, int) or not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        filters = filters or {}
        if not isinstance(filters, dict):
            raise ValueError("filters must be an object")
        engine = legacy_engine()
        informative = engine.informative_terms(query)
        if not informative:
            return {
                "results": [], "abstention_reason": "no informative lexical terms",
                "diagnostics": {
                    "deadline_ms": self.search_deadline_ms, "elapsed_ms": 0.0,
                    "deadline_exceeded": False, "legs": [],
                },
            }
        candidates: dict[int, dict] = {}
        started = time.monotonic()
        deadline_at = started + self.search_deadline_ms / 1000
        leg_timings: list[dict[str, Any]] = []
        deadline_exceeded = False

        def merge(rows: list[dict]) -> None:
            for row in rows:
                existing = candidates.get(row["id"])
                if existing is None:
                    row["legs"] = {row.pop("leg")}
                    candidates[row["id"]] = row
                else:
                    existing["legs"].add(row["leg"])
                    if (row["tier"], float(row["lexical_rank"])) > (existing["tier"], float(existing["lexical_rank"])):
                        legs = existing["legs"]
                        row["legs"] = legs
                        row.pop("leg")
                        candidates[row["id"]] = row

        def run_leg(name: str, operation) -> list[dict]:
            nonlocal deadline_exceeded
            leg_started = time.monotonic()
            try:
                rows = operation()
                leg_timings.append({
                    "leg": name, "elapsed_ms": round((time.monotonic() - leg_started) * 1000, 3),
                    "n_results": len(rows), "timed_out": False,
                })
                return rows
            except SearchDeadlineExceeded:
                deadline_exceeded = True
                leg_timings.append({
                    "leg": name, "elapsed_ms": round((time.monotonic() - leg_started) * 1000, 3),
                    "n_results": 0, "timed_out": True,
                })
                raise

        identifiers = sorted(
            engine.identifier_terms(informative),
            key=lambda value: (
                bool(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value)),
                bool(re.fullmatch(r"[0-9a-f]{8,}", value)), len(value),
            ),
            reverse=True,
        )
        try:
            with self.connect() as conn:
                if identifiers:
                    merge(run_leg("entity", lambda: self._entity_leg(
                        conn, identifiers, filters, authorized_source=authorized_source,
                        deadline_at=deadline_at, tier=3,
                    )))
                    for identifier in identifiers[:3]:
                        exact_rows = run_leg("identifier", lambda identifier=identifier: self._lexical_leg(
                            conn, identifier, "plainto_tsquery", filters, "identifier", 3,
                            exact=identifier, limit=100, authorized_source=authorized_source, deadline_at=deadline_at,
                        ))
                        merge(exact_rows)
                        if exact_rows:
                            break
                else:
                    phrase_spec = phrase_query_spec(preferred_phrase_probes(engine.phrase_queries(query)))
                    if phrase_spec:
                        phrase_query, phrase_function = phrase_spec
                        merge(run_leg("phrase", lambda: self._lexical_leg(
                            conn, phrase_query, phrase_function, filters, "phrase", 3,
                            limit=100, authorized_source=authorized_source, deadline_at=deadline_at,
                        )))
                    entity_values = [
                        part
                        for term in informative
                        for part in re.split(r"[./_-]+", term)
                        if re.search(r"(?:error|exception|timeout)$", part, re.I)
                    ]
                    if entity_values:
                        merge(run_leg("entity", lambda: self._entity_leg(
                            conn, entity_values, filters, authorized_source=authorized_source,
                            deadline_at=deadline_at, tier=2,
                        )))
                if not identifiers and should_run_partial(candidate_count=len(candidates), result_limit=limit):
                    for probe, leg, tier in partial_lexical_probes(
                        informative,
                        has_time_filter=bool(filters.get("since") or filters.get("until")),
                    ):
                        merge(run_leg(leg, lambda probe=probe, leg=leg, tier=tier: self._lexical_leg(
                            conn, probe, "plainto_tsquery", filters, leg, tier,
                            limit=100, authorized_source=authorized_source, deadline_at=deadline_at,
                        )))
                if not any(row["tier"] >= 3 for row in candidates.values()):
                    merge(run_leg("all", lambda: self._lexical_leg(
                        conn, " ".join(informative), "plainto_tsquery", filters, "all", 1,
                        authorized_source=authorized_source, deadline_at=deadline_at,
                    )))
        except SearchDeadlineExceeded:
            pass

        now = datetime.now(timezone.utc).timestamp()
        grouped: dict[tuple[str, str], tuple[tuple[float, ...], dict]] = {}
        for row in candidates.values():
            matched = [term for term in informative if term.casefold() in row["text_redacted"].casefold()]
            if row["tier"] == 0 and len([term for term in matched if len(term) >= 5]) < 2:
                continue
            occurred = row["occurred_at"] or row["started_at"]
            epoch = occurred.timestamp() if occurred else now
            recency_factor = 1 / (1 + max(0, now - epoch) / 86400 / 180)
            evidence = evidence_rank_components(
                legs=row["legs"], surface=row["surface"], lexical_rank=float(row["lexical_rank"]),
                matched_count=len(matched), informative_count=len(informative),
                has_identifier=bool(identifiers), recency_factor=recency_factor,
            )
            key = (row["source_id"], row["session_native_id"])
            rank_key = tuple(evidence["rank_key"])
            if key not in grouped or rank_key > grouped[key][0]:
                grouped[key] = (rank_key, {**row, "matched_terms": matched, "evidence": evidence})
        ranked = sorted(grouped.values(), key=lambda value: value[0], reverse=True)[:limit]
        results = []
        for _rank, row in ranked:
            metadata = row["metadata"] or {}
            path = row["path"] or metadata.get("original_path") or f"recall://{row['source_id']}/{row['session_native_id']}"
            cwd = metadata.get("cwd")
            results.append({
                "source_id": row["source_id"], "native_id": row["event_native_id"],
                "session_native_id": row["session_native_id"], "path": path,
                "occurred_at": row["occurred_at"], "observed_at": row["observed_at"],
                "cwd": cwd, "slot": metadata.get("slot") or (re.search(r"grep\d+", cwd or "").group(0) if re.search(r"grep\d+", cwd or "") else None),
                "branch": metadata.get("branch"), "harness": metadata.get("harness"),
                "surface": row["surface"], "text": row["text_redacted"],
                "receipt": row["receipt"], "matched_terms": row["matched_terms"],
                "legs": sorted(row["legs"]), "tier": row["evidence"]["class_priority"],
                "evidence": row["evidence"],
                "projector_version": row["projector_version"],
            })
        diagnostics = {
            "deadline_ms": self.search_deadline_ms,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
            "deadline_exceeded": deadline_exceeded,
            "legs": leg_timings,
        }
        return {
            "results": results,
            "abstention_reason": None if results else ("search deadline exceeded" if deadline_exceeded else "insufficient lexical evidence"),
            "diagnostics": diagnostics,
        }

    def show(self, target: str, *, around: str | None = None, tail: int = 0,
             prompts: bool = False, authorized_source: str | None = None) -> dict | None:
        if not target:
            raise ValueError("target is required")
        if tail < 0 or tail > 1000:
            raise ValueError("tail must be between 0 and 1000")
        with self.connect() as conn:
            if target.startswith("recall://"):
                event_part = target.split("#", 1)[0]
                try:
                    base, revision = event_part.rsplit("?rev=", 1)
                    source_id, native_id = base.removeprefix("recall://").split("/", 1)
                    int(revision)
                except (ValueError, TypeError):
                    raise ValueError("invalid receipt")
                identity = conn.execute(
                    "SELECT source_id,COALESCE(native_parent_id,native_id) AS session_native_id FROM source_events WHERE source_id=%s AND native_id=%s AND revision=%s AND (%s::text IS NULL OR source_id=%s)",
                    (source_id, native_id, int(revision), authorized_source, authorized_source),
                ).fetchone()
            else:
                identity = conn.execute(
                    """SELECT source_id,COALESCE(native_parent_id,native_id) AS session_native_id
                       FROM source_events WHERE envelope #>> '{provenance,original_path}'=%s
                         AND (%s::text IS NULL OR source_id=%s)
                       ORDER BY id DESC LIMIT 1""",
                    (target, authorized_source, authorized_source),
                ).fetchone()
            if not identity:
                return None
            where = "source_id=%s AND session_native_id=%s AND deleted_at IS NULL"
            values: list[Any] = [identity["source_id"], identity["session_native_id"]]
            if prompts:
                where += " AND surface='user'"
            if around:
                point = datetime.fromisoformat(around.replace("Z", "+00:00"))
                before = conn.execute(
                    f"SELECT occurred_at,surface,text_redacted AS text,receipt FROM items WHERE {where} AND occurred_at<=%s ORDER BY occurred_at DESC,id DESC LIMIT 4",
                    [*values, point],
                ).fetchall()
                after = conn.execute(
                    f"SELECT occurred_at,surface,text_redacted AS text,receipt FROM items WHERE {where} AND occurred_at>%s ORDER BY occurred_at,id LIMIT 3",
                    [*values, point],
                ).fetchall()
                rows = list(reversed(before)) + list(after)
            elif tail:
                rows = list(reversed(conn.execute(
                    f"SELECT occurred_at,surface,text_redacted AS text,receipt FROM items WHERE {where} ORDER BY occurred_at DESC NULLS LAST,id DESC LIMIT %s",
                    [*values, tail],
                ).fetchall()))
            else:
                rows = conn.execute(
                    f"SELECT occurred_at,surface,text_redacted AS text,receipt FROM items WHERE {where} ORDER BY occurred_at,id LIMIT 1000",
                    values,
                ).fetchall()
            return {"chunks": [dict(row) for row in rows], "truncated": not around and not tail and len(rows) == 1000}

    def related(self, *, cwd: str | None, branch: str | None, limit: int = 10,
                mains_only: bool = False, fast: bool = False,
                authorized_source: str | None = None) -> dict:
        if not 1 <= limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        if not cwd and not branch:
            return {"results": []}
        clauses, score_parts, params = [], [], []
        if cwd:
            clauses.append("COALESCE(s.metadata->>'cwd','') ILIKE %s"); params.append("%" + cwd + "%")
            score_parts.append("(COALESCE(s.metadata->>'cwd','') ILIKE %s)::int")
        if branch:
            clauses.append("COALESCE(s.metadata->>'branch','') ILIKE %s"); params.append("%" + branch + "%")
            score_parts.append("(COALESCE(s.metadata->>'branch','') ILIKE %s)::int")
        score_params = list(params)
        with self.connect() as conn:
            rows = conn.execute(
                f"""SELECT s.source_id,s.native_id,s.metadata,s.ended_at,path.value AS path,evidence.receipt,
                           ({' + '.join(score_parts)}) AS overlap
                    FROM sessions s
                    JOIN LATERAL (
                      SELECT envelope #>> '{{provenance,original_path}}' AS value
                      FROM source_events se
                      WHERE se.source_id=s.source_id AND COALESCE(se.native_parent_id,se.native_id)=s.native_id
                        AND envelope #>> '{{provenance,original_path}}' IS NOT NULL
                      ORDER BY se.id DESC LIMIT 1
                    ) path ON true
                    JOIN LATERAL (
                      SELECT i.receipt FROM items i
                      WHERE i.source_id=s.source_id AND i.session_native_id=s.native_id AND i.deleted_at IS NULL
                      ORDER BY i.occurred_at DESC NULLS LAST,i.id DESC LIMIT 1
                    ) evidence ON true
                    WHERE ({' OR '.join(clauses)})
                      AND (%s::text IS NULL OR s.source_id=%s)
                      {"AND path.value NOT LIKE '%%/subagents/%%'" if mains_only else ''}
                    ORDER BY overlap DESC,s.ended_at DESC NULLS LAST LIMIT %s""",
                [*score_params, *params, authorized_source, authorized_source, limit],
            ).fetchall()
        return {"results": [{
            "source_id": row["source_id"], "session_native_id": row["native_id"],
            "path": row["path"], "cwd": (row["metadata"] or {}).get("cwd"),
            "branch": (row["metadata"] or {}).get("branch"), "overlap": row["overlap"],
            "receipt": row["receipt"],
        } for row in rows]}

    def doctor(self, authorized_source: str | None = None) -> dict:
        result = self.service_metrics()
        with self.connect() as conn:
            if authorized_source:
                result = {
                    "source_events": conn.execute("SELECT count(*) AS n FROM source_events WHERE source_id=%s", (authorized_source,)).fetchone()["n"],
                    "dead_letters": conn.execute("SELECT count(*) AS n FROM dead_letters WHERE source_id=%s", (authorized_source,)).fetchone()["n"],
                    "projection_lag": result["projection_lag"],
                    "source_freshness_seconds": conn.execute("SELECT COALESCE(GREATEST(0,extract(epoch FROM now()-max(created_at)))::bigint,0) AS n FROM source_events WHERE source_id=%s", (authorized_source,)).fetchone()["n"],
                }
            result.update({
                "sources": 1 if authorized_source else conn.execute("SELECT count(*) AS n FROM sources").fetchone()["n"],
                "sessions": conn.execute("SELECT count(*) AS n FROM sessions WHERE (%s::text IS NULL OR source_id=%s)", (authorized_source, authorized_source)).fetchone()["n"],
                "live_items": conn.execute("SELECT count(*) AS n FROM items WHERE deleted_at IS NULL AND (%s::text IS NULL OR source_id=%s)", (authorized_source, authorized_source)).fetchone()["n"],
            })
        return {"status": "ok", **result}

    def rebuild(self) -> dict:
        with self.connect() as conn:
            with conn.transaction():
                before = conn.execute("SELECT count(*) AS n FROM items WHERE deleted_at IS NULL").fetchone()["n"]
                entities_before = conn.execute("SELECT count(*) AS n FROM entities").fetchone()["n"]
                conn.execute("TRUNCATE entities,chunks,items,sessions,projection_watermarks RESTART IDENTITY")
                rows = conn.execute("SELECT id,envelope,revision FROM source_events ORDER BY id").fetchall()
                for row in rows:
                    self._project_one(conn, row["id"], row["envelope"], row["revision"])
                after = conn.execute("SELECT count(*) AS n FROM items WHERE deleted_at IS NULL").fetchone()["n"]
                entities_after = conn.execute("SELECT count(*) AS n FROM entities").fetchone()["n"]
                return {
                    "events": len(rows), "items_before": before, "items_after": after,
                    "entities_before": entities_before, "entities_after": entities_after,
                }

    def backfill_entities(self, batch_size: int = 5000, max_batches: int | None = None) -> dict:
        """Resume an online canonical-event replay into the entity projection."""
        if not 1 <= batch_size <= 20000:
            raise ValueError("batch size must be between 1 and 20000")
        if max_batches is not None and max_batches < 1:
            raise ValueError("max batches must be positive")
        engine = legacy_engine()
        batches = scanned = inserted = 0
        with self.connect() as conn:
            conn.autocommit = True
            conn.execute("SELECT pg_advisory_lock(hashtextextended('recall:entity-backfill',0))")
            try:
                conn.execute(
                    """CREATE TEMP TABLE entity_backfill_stage (
                         item_id bigint,source_id text,kind text,value text,normalized text
                       ) ON COMMIT DELETE ROWS"""
                )
                while max_batches is None or batches < max_batches:
                    with conn.transaction():
                        state = conn.execute(
                            "SELECT target_item_id,last_item_id,completed_at FROM projection_backfills WHERE name='entities-v2' FOR UPDATE"
                        ).fetchone()
                        if state is None:
                            target = conn.execute("SELECT COALESCE(max(id),0) AS n FROM items").fetchone()["n"]
                            conn.execute(
                                "INSERT INTO projection_backfills(name,target_item_id,last_item_id) VALUES ('entities-v2',%s,0)",
                                (target,),
                            )
                            state = {"target_item_id": target, "last_item_id": 0, "completed_at": None}
                        if state["completed_at"] is not None:
                            break
                        rows = conn.execute(
                            """SELECT i.id,i.source_id,i.text_redacted FROM items i
                               WHERE i.id>%s AND i.id<=%s ORDER BY i.id LIMIT %s""",
                            (state["last_item_id"], state["target_item_id"], batch_size),
                        ).fetchall()
                        if not rows:
                            conn.execute(
                                "UPDATE projection_backfills SET completed_at=now(),updated_at=now() WHERE name='entities-v2'"
                            )
                            break
                        entity_rows = []
                        for row in rows:
                            entities = [
                                {"kind": kind, "value": value, "normalized": value.casefold()}
                                for kind, value in engine.extract_entities(row["text_redacted"])
                            ]
                            entity_rows.extend(
                                (row["id"], row["source_id"], entity["kind"], entity["value"], entity["normalized"])
                                for entity in entities
                            )
                        if entity_rows:
                            with conn.cursor() as cursor:
                                with cursor.copy(
                                    "COPY entity_backfill_stage(item_id,source_id,kind,value,normalized) FROM STDIN"
                                ) as copy:
                                    for entity_row in entity_rows:
                                        copy.write_row(entity_row)
                            conn.execute(
                                """INSERT INTO entities(item_id,source_id,kind,value,normalized)
                                   SELECT item_id,source_id,kind,value,normalized FROM entity_backfill_stage
                                   ON CONFLICT DO NOTHING"""
                            )
                        last_item_id = rows[-1]["id"]
                        completed = last_item_id >= state["target_item_id"]
                        conn.execute(
                            """UPDATE projection_backfills SET last_item_id=%s,
                               completed_at=CASE WHEN %s THEN now() ELSE NULL END,updated_at=now()
                               WHERE name='entities-v2'""",
                            (last_item_id, completed),
                        )
                        batches += 1
                        scanned += len(rows)
                        inserted += len(entity_rows)
                        if completed:
                            break
                state = conn.execute(
                    "SELECT target_item_id,last_item_id,completed_at FROM projection_backfills WHERE name='entities-v2'"
                ).fetchone()
            finally:
                conn.execute("SELECT pg_advisory_unlock(hashtextextended('recall:entity-backfill',0))")
        return {
            "batches": batches, "items_scanned": scanned, "entity_rows_attempted": inserted,
            "target_item_id": state["target_item_id"], "last_item_id": state["last_item_id"],
            "completed": state["completed_at"] is not None,
        }

    def export_raw(self) -> list[dict]:
        """Admin/offline API only; intentionally not routed by the HTTP app."""
        with self.connect() as conn:
            return [row["envelope"] for row in conn.execute("SELECT envelope FROM source_events ORDER BY id")]
