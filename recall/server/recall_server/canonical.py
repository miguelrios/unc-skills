from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

from contracts.v2 import ContractError, IDENTITY_RE, validate_contract

from .db import BrainStore
from .projectors import validate_envelope


class CanonicalLifecycleError(RuntimeError):
    """Stable, content-free failure at a v2 lifecycle boundary."""

    def __init__(self, error_code: str):
        self.error_code = error_code
        super().__init__(error_code)


class ArchiveLifecycle(Protocol):
    def put_raw(
        self,
        *,
        tenant_id: str,
        source_id: str,
        native_id: str,
        payload: bytes,
        media_type: str,
        created_at: str,
    ) -> dict[str, Any]: ...

    def delete_raw(self, reference: dict[str, Any]) -> bool: ...


def _identity_sha256(tenant_id: str, source_id: str, native_id: str) -> str:
    return hashlib.sha256(
        "\x1f".join((tenant_id, source_id, native_id)).encode()
    ).hexdigest()


def _opaque(prefix: str, *values: str) -> str:
    digest = hashlib.sha256("\x1f".join(values).encode()).hexdigest()
    return f"{prefix}_{digest[:32]}"


def _timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, str):
        return value
    raise CanonicalLifecycleError("canonical_state_invalid")


def _native_from_receipt(receipt: str, source_id: str) -> str:
    parsed = urlsplit(receipt)
    if parsed.scheme != "recall" or parsed.netloc != source_id:
        raise CanonicalLifecycleError("forget_target_invalid")
    native_id = unquote(parsed.path.lstrip("/"))
    if not native_id or not IDENTITY_RE.fullmatch(native_id):
        raise CanonicalLifecycleError("forget_target_invalid")
    return native_id


class CanonicalArchiveGateway:
    """Serialize archive writes with the canonical forget fence."""

    def __init__(
        self,
        store: BrainStore,
        archive: ArchiveLifecycle,
        *,
        tenant_id: str,
        principal_id: str,
    ):
        CanonicalPlane._validate_host_identity(
            tenant_id, principal_id, "source:placeholder",
        )
        self.store = store
        self.archive = archive
        self.tenant_id = tenant_id
        self.principal_id = principal_id

    def put_raw(
        self,
        *,
        tenant_id: str,
        source_id: str,
        native_id: str,
        payload: bytes,
        media_type: str,
        created_at: str,
    ) -> dict[str, Any]:
        if tenant_id != self.tenant_id:
            raise CanonicalLifecycleError("archive_authority_forbidden")
        CanonicalPlane._validate_host_identity(
            tenant_id, self.principal_id, source_id,
        )
        if not isinstance(native_id, str) or not IDENTITY_RE.fullmatch(native_id):
            raise CanonicalLifecycleError("archive_identity_invalid")
        identity_sha256 = _identity_sha256(tenant_id, source_id, native_id)
        with self.store.connect() as conn:
            with conn.transaction():
                CanonicalPlane._register_source(
                    conn,
                    tenant_id=tenant_id,
                    principal_id=self.principal_id,
                    source_id=source_id,
                )
                conn.execute(
                    """SELECT pg_advisory_xact_lock(hashtextextended(%s,0))""",
                    (f"v2\x1f{tenant_id}\x1f{source_id}\x1f{native_id}",),
                )
                forgotten = conn.execute(
                    """SELECT 1 FROM forget_tombstones
                       WHERE tenant_id=%s AND source_id=%s
                         AND target_identity_sha256=%s""",
                    (tenant_id, source_id, identity_sha256),
                ).fetchone()
                if forgotten:
                    raise CanonicalLifecycleError("archive_identity_forgotten")
                return self.archive.put_raw(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    native_id=native_id,
                    payload=payload,
                    media_type=media_type,
                    created_at=created_at,
                )


class CanonicalPlane:
    """Tenant-aware v2 canonical ingest and crash-resumable authoritative forget."""

    def __init__(self, store: BrainStore, archive: ArchiveLifecycle):
        self.store = store
        self.archive = archive

    @staticmethod
    def _validate_host_identity(
        tenant_id: str,
        principal_id: str,
        source_id: str,
    ) -> None:
        if not all(
            isinstance(value, str) and IDENTITY_RE.fullmatch(value)
            for value in (tenant_id, principal_id, source_id)
        ):
            raise CanonicalLifecycleError("canonical_authority_invalid")

    @staticmethod
    def _register_source(
        conn: Any,
        *,
        tenant_id: str,
        principal_id: str,
        source_id: str,
    ) -> None:
        conn.execute(
            "INSERT INTO brain_tenants(tenant_id) VALUES (%s) ON CONFLICT DO NOTHING",
            (tenant_id,),
        )
        conn.execute(
            """INSERT INTO brain_principals(tenant_id,principal_id)
               VALUES (%s,%s) ON CONFLICT DO NOTHING""",
            (tenant_id, principal_id),
        )
        conn.execute(
            """INSERT INTO canonical_sources(tenant_id,source_id,owner_principal_id)
               VALUES (%s,%s,%s) ON CONFLICT DO NOTHING""",
            (tenant_id, source_id, principal_id),
        )
        owner = conn.execute(
            """SELECT owner_principal_id
               FROM canonical_sources
               WHERE tenant_id=%s AND source_id=%s""",
            (tenant_id, source_id),
        ).fetchone()
        if owner is None or owner["owner_principal_id"] != principal_id:
            raise CanonicalLifecycleError("canonical_authority_forbidden")

    def ingest_document(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        connector_id: str,
        artifact_ref: dict[str, Any],
        envelope: dict[str, Any],
        text_redacted: str,
    ) -> dict[str, Any]:
        self._validate_host_identity(tenant_id, principal_id, envelope.get("source_id"))
        if not isinstance(connector_id, str) or not IDENTITY_RE.fullmatch(connector_id):
            raise CanonicalLifecycleError("canonical_connector_invalid")
        if not isinstance(text_redacted, str) or len(text_redacted.encode()) > 1_000_000:
            raise CanonicalLifecycleError("canonical_text_invalid")
        try:
            artifact = validate_contract(
                artifact_ref, expected="recall.artifact-ref.v1",
            )
            event = validate_envelope(envelope)
        except (ContractError, ValueError):
            raise CanonicalLifecycleError("canonical_contract_invalid") from None
        source_id = event["source_id"]
        native_id = event["native_id"]
        is_tombstone = event["kind"] == "tombstone"
        if (
            artifact["tenant_id"] != tenant_id
            or artifact["source_id"] != source_id
            or event["principal_id"] != principal_id
            or event.get("provenance", {}).get("artifact_ref") != artifact
        ):
            raise CanonicalLifecycleError("canonical_lineage_invalid")
        raw_sha256 = artifact["content_sha256"]
        identity_sha256 = _identity_sha256(tenant_id, source_id, native_id)
        event_id = _opaque("evt", tenant_id, source_id, native_id, raw_sha256)
        job_id = _opaque("job", tenant_id, source_id, connector_id, event_id)
        document_id = _opaque("doc", tenant_id, source_id, event_id)
        chunk_id = _opaque("chk", tenant_id, source_id, document_id, "0")
        text_sha256 = hashlib.sha256(text_redacted.encode()).hexdigest()

        with self.store.connect() as conn:
            with conn.transaction():
                self._register_source(
                    conn,
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    source_id=source_id,
                )
                forgotten = conn.execute(
                    """SELECT 1 FROM forget_tombstones
                       WHERE tenant_id=%s AND source_id=%s
                         AND target_identity_sha256=%s""",
                    (tenant_id, source_id, identity_sha256),
                ).fetchone()
                if forgotten:
                    raise CanonicalLifecycleError("canonical_identity_forgotten")
                conn.execute(
                    """SELECT pg_advisory_xact_lock(hashtextextended(%s,0))""",
                    (f"v2\x1f{tenant_id}\x1f{source_id}\x1f{native_id}",),
                )
                conn.execute(
                    """INSERT INTO raw_artifacts(
                           tenant_id,source_id,artifact_id,storage_backend,object_key,
                           content_sha256,size_bytes,media_type,encryption,version_id,created_at
                       ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(tenant_id,source_id,artifact_id) DO NOTHING""",
                    (
                        tenant_id, source_id, artifact["artifact_id"],
                        artifact["storage_backend"], artifact["object_key"],
                        raw_sha256, artifact["size_bytes"], artifact["media_type"],
                        artifact["encryption"], artifact["version_id"],
                        artifact["created_at"],
                    ),
                )
                stored_artifact = conn.execute(
                    """SELECT storage_backend,object_key,content_sha256,size_bytes,
                              media_type,encryption,version_id,state
                       FROM raw_artifacts
                       WHERE tenant_id=%s AND source_id=%s AND artifact_id=%s""",
                    (tenant_id, source_id, artifact["artifact_id"]),
                ).fetchone()
                expected_artifact = {
                    key: artifact[key]
                    for key in (
                        "storage_backend", "object_key", "content_sha256", "size_bytes",
                        "media_type", "encryption", "version_id",
                    )
                }
                if (
                    stored_artifact is None
                    or stored_artifact["state"] != "live"
                    or any(
                        stored_artifact[key] != value
                        for key, value in expected_artifact.items()
                    )
                ):
                    raise CanonicalLifecycleError("canonical_artifact_conflict")
                existing = conn.execute(
                    """SELECT revision FROM canonical_events
                       WHERE tenant_id=%s AND source_id=%s
                         AND native_id=%s AND content_sha256=%s""",
                    (tenant_id, source_id, native_id, raw_sha256),
                ).fetchone()
                if existing:
                    revision = existing["revision"]
                    return {
                        "status": "committed",
                        "inserted": 0,
                        "duplicate_events": 1,
                        "revision": revision,
                        "receipt": (
                            f"recall://{source_id}/{native_id}?rev={revision}#item=0"
                        ),
                        "replay": True,
                    }
                row = conn.execute(
                    """SELECT COALESCE(max(revision),0)+1 AS revision
                       FROM canonical_events
                       WHERE tenant_id=%s AND source_id=%s AND native_id=%s""",
                    (tenant_id, source_id, native_id),
                ).fetchone()
                revision = row["revision"]
                receipt = f"recall://{source_id}/{native_id}?rev={revision}#item=0"
                conn.execute(
                    """INSERT INTO canonical_ingest_jobs(
                           tenant_id,source_id,job_id,connector_id,mode,status,
                           attempt,created_at,updated_at
                       ) VALUES (%s,%s,%s,%s,'incremental','committed',1,now(),now())
                       ON CONFLICT(tenant_id,source_id,job_id) DO NOTHING""",
                    (tenant_id, source_id, job_id, connector_id),
                )
                conn.execute(
                    """INSERT INTO canonical_events(
                           tenant_id,source_id,event_id,native_id,native_parent_id,
                           artifact_id,job_id,kind,content_sha256,revision,
                           occurred_at,observed_at,is_tombstone,canonical_redacted
                       ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        tenant_id, source_id, event_id, native_id,
                        event.get("native_parent_id"), artifact["artifact_id"], job_id,
                        event["kind"], raw_sha256, revision, event["occurred_at"],
                        event["observed_at"], is_tombstone, json.dumps(event),
                    ),
                )
                conn.execute(
                    """UPDATE canonical_documents
                       SET is_current=false,
                           deleted_at=CASE WHEN %s THEN now() ELSE deleted_at END
                       WHERE tenant_id=%s AND source_id=%s AND native_id=%s
                         AND is_current""",
                    (is_tombstone, tenant_id, source_id, native_id),
                )
                if is_tombstone:
                    conn.execute(
                        """UPDATE canonical_chunks chunk
                           SET deleted_at=now()
                           FROM canonical_documents document
                           WHERE chunk.tenant_id=document.tenant_id
                             AND chunk.source_id=document.source_id
                             AND chunk.document_id=document.document_id
                             AND document.tenant_id=%s AND document.source_id=%s
                             AND document.native_id=%s
                             AND chunk.deleted_at IS NULL""",
                        (tenant_id, source_id, native_id),
                    )
                else:
                    conn.execute(
                        """INSERT INTO canonical_documents(
                               tenant_id,source_id,document_id,event_id,artifact_id,native_id,
                               content_sha256,revision,is_current,text_redacted,text_sha256
                           ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true,%s,%s)""",
                        (
                            tenant_id, source_id, document_id, event_id,
                            artifact["artifact_id"], native_id, raw_sha256, revision,
                            text_redacted, text_sha256,
                        ),
                    )
                    conn.execute(
                        """INSERT INTO canonical_chunks(
                               tenant_id,source_id,chunk_id,document_id,ordinal,receipt,
                               text_redacted,text_sha256
                           ) VALUES (%s,%s,%s,%s,0,%s,%s,%s)""",
                        (
                            tenant_id, source_id, chunk_id, document_id, receipt,
                            text_redacted, text_sha256,
                        ),
                    )
                conn.execute(
                    """INSERT INTO canonical_audit_events(
                           tenant_id,source_id,audit_id,operation,status,
                           subject_sha256,item_count,byte_count
                       ) VALUES (%s,%s,%s,'ingest.commit','success',%s,1,%s)""",
                    (
                        tenant_id, source_id,
                        _opaque("audit", tenant_id, source_id, event_id, "ingest"),
                        identity_sha256, artifact["size_bytes"],
                    ),
                )
        return {
            "status": "committed",
            "inserted": 1,
            "duplicate_events": 0,
            "revision": revision,
            "receipt": receipt,
            "replay": False,
        }

    def ingest_batch(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not isinstance(events, list) or not 1 <= len(events) <= 500:
            raise CanonicalLifecycleError("canonical_batch_invalid")
        results = []
        for envelope in events:
            provenance = envelope.get("provenance", {})
            connector_id = provenance.get("connector_id")
            artifact_ref = provenance.get("artifact_ref")
            text_redacted = json.dumps(
                envelope.get("content"),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            results.append(self.ingest_document(
                tenant_id=tenant_id,
                principal_id=principal_id,
                connector_id=connector_id,
                artifact_ref=artifact_ref,
                envelope=envelope,
                text_redacted="" if envelope.get("kind") == "tombstone" else text_redacted,
            ))
        return {
            "status": "committed",
            "inserted": sum(result["inserted"] for result in results),
            "duplicate_events": sum(
                result["duplicate_events"] for result in results
            ),
            "receipts": [result["receipt"] for result in results],
            "replay": all(result["replay"] for result in results),
        }

    def _archive_references(
        self,
        conn: Any,
        *,
        tenant_id: str,
        source_id: str,
        native_id: str,
    ) -> list[dict[str, Any]]:
        rows = conn.execute(
            """SELECT DISTINCT artifact.tenant_id,artifact.source_id,
                       artifact.artifact_id,artifact.storage_backend,artifact.object_key,
                       artifact.content_sha256,artifact.size_bytes,artifact.media_type,
                       artifact.encryption,artifact.version_id,artifact.created_at
               FROM canonical_events event
               JOIN raw_artifacts artifact
                 USING(tenant_id,source_id,artifact_id)
               WHERE event.tenant_id=%s AND event.source_id=%s
                 AND event.native_id=%s
               ORDER BY artifact.artifact_id""",
            (tenant_id, source_id, native_id),
        ).fetchall()
        return [
            {
                "contract": "recall.artifact-ref.v1",
                "schema_version": 1,
                **{
                    key: row[key]
                    for key in (
                        "tenant_id", "source_id", "artifact_id", "storage_backend",
                        "object_key", "content_sha256", "size_bytes", "media_type",
                        "encryption", "version_id",
                    )
                },
                "created_at": _timestamp(row["created_at"]),
            }
            for row in rows
        ]

    def forget(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            value = validate_contract(request, expected="recall.forget-request.v1")
        except ContractError:
            raise CanonicalLifecycleError("forget_contract_invalid") from None
        tenant_id = value["tenant_id"]
        principal_id = value["principal_id"]
        source_id = value["source_id"]
        native_id = _native_from_receipt(value["target_receipt"], source_id)
        target_sha256 = _identity_sha256(tenant_id, source_id, native_id)
        reason = value["reason"]

        with self.store.connect() as conn:
            with conn.transaction():
                owner = conn.execute(
                    """SELECT owner_principal_id FROM canonical_sources
                       WHERE tenant_id=%s AND source_id=%s""",
                    (tenant_id, source_id),
                ).fetchone()
                if owner is None or owner["owner_principal_id"] != principal_id:
                    raise CanonicalLifecycleError("forget_authority_forbidden")
                conn.execute(
                    """SELECT pg_advisory_xact_lock(hashtextextended(%s,0))""",
                    (f"v2\x1f{tenant_id}\x1f{source_id}\x1f{native_id}",),
                )
                prior = conn.execute(
                    """SELECT target_identity_sha256,status
                       FROM forget_tombstones
                       WHERE tenant_id=%s AND source_id=%s AND idempotency_key=%s""",
                    (tenant_id, source_id, value["idempotency_key"]),
                ).fetchone()
                if prior and prior["target_identity_sha256"] != target_sha256:
                    raise CanonicalLifecycleError("forget_idempotency_conflict")
                tombstone = conn.execute(
                    """SELECT status FROM forget_tombstones
                       WHERE tenant_id=%s AND source_id=%s
                         AND target_identity_sha256=%s
                       FOR UPDATE""",
                    (tenant_id, source_id, target_sha256),
                ).fetchone()
                if tombstone and tombstone["status"] == "deleted":
                    return {
                        "status": "deleted",
                        "replay": True,
                        "raw_deleted": 0,
                        "projections_deleted": 0,
                    }
                target = conn.execute(
                    """SELECT document.native_id
                       FROM canonical_chunks chunk
                       JOIN canonical_documents document
                         USING(tenant_id,source_id,document_id)
                       WHERE chunk.tenant_id=%s AND chunk.source_id=%s
                         AND chunk.receipt=%s""",
                    (tenant_id, source_id, value["target_receipt"]),
                ).fetchone()
                if target is None or target["native_id"] != native_id:
                    raise CanonicalLifecycleError("forget_target_not_found")
                if tombstone is None:
                    conn.execute(
                        """INSERT INTO forget_tombstones(
                               tenant_id,source_id,target_identity_sha256,mode,reason,
                               deleted_at,status,idempotency_key
                           ) VALUES (%s,%s,%s,%s,%s,%s,'deleting',%s)""",
                        (
                            tenant_id, source_id, target_sha256, value["mode"], reason,
                            value["requested_at"], value["idempotency_key"],
                        ),
                    )
                references = self._archive_references(
                    conn,
                    tenant_id=tenant_id,
                    source_id=source_id,
                    native_id=native_id,
                )
                projection_count = conn.execute(
                    """SELECT count(*) AS count
                       FROM canonical_chunks chunk
                       JOIN canonical_documents document
                         USING(tenant_id,source_id,document_id)
                       WHERE document.tenant_id=%s AND document.source_id=%s
                         AND document.native_id=%s AND chunk.deleted_at IS NULL""",
                    (tenant_id, source_id, native_id),
                ).fetchone()["count"]
                conn.execute(
                    """UPDATE canonical_chunks chunk
                       SET deleted_at=COALESCE(chunk.deleted_at,%s)
                       FROM canonical_documents document
                       WHERE chunk.tenant_id=document.tenant_id
                         AND chunk.source_id=document.source_id
                         AND chunk.document_id=document.document_id
                         AND document.tenant_id=%s AND document.source_id=%s
                         AND document.native_id=%s""",
                    (value["requested_at"], tenant_id, source_id, native_id),
                )
                conn.execute(
                    """UPDATE canonical_documents
                       SET is_current=false,
                           deleted_at=COALESCE(deleted_at,%s)
                       WHERE tenant_id=%s AND source_id=%s AND native_id=%s""",
                    (value["requested_at"], tenant_id, source_id, native_id),
                )
                conn.execute(
                    """UPDATE raw_artifacts artifact
                       SET state='deleting'
                       FROM canonical_events event
                       WHERE artifact.tenant_id=event.tenant_id
                         AND artifact.source_id=event.source_id
                         AND artifact.artifact_id=event.artifact_id
                         AND event.tenant_id=%s AND event.source_id=%s
                         AND event.native_id=%s AND artifact.state='live'""",
                    (tenant_id, source_id, native_id),
                )

        try:
            for reference in references:
                self.archive.delete_raw(reference)
        except Exception:
            with self.store.connect() as conn:
                conn.execute(
                    """INSERT INTO canonical_audit_events(
                           tenant_id,source_id,audit_id,operation,status,
                           subject_sha256,item_count
                       ) VALUES (%s,%s,%s,'forget.archive','failed',%s,%s)
                       ON CONFLICT DO NOTHING""",
                    (
                        tenant_id, source_id,
                        _opaque(
                            "audit", tenant_id, source_id, target_sha256,
                            value["idempotency_key"], "failed",
                        ),
                        target_sha256, len(references),
                    ),
                )
            raise CanonicalLifecycleError("archive_delete_failed") from None

        with self.store.connect() as conn:
            with conn.transaction():
                conn.execute(
                    """DELETE FROM canonical_chunks chunk
                       USING canonical_documents document
                       WHERE chunk.tenant_id=document.tenant_id
                         AND chunk.source_id=document.source_id
                         AND chunk.document_id=document.document_id
                         AND document.tenant_id=%s AND document.source_id=%s
                         AND document.native_id=%s""",
                    (tenant_id, source_id, native_id),
                )
                conn.execute(
                    """DELETE FROM canonical_documents
                       WHERE tenant_id=%s AND source_id=%s AND native_id=%s""",
                    (tenant_id, source_id, native_id),
                )
                conn.execute(
                    """DELETE FROM canonical_events
                       WHERE tenant_id=%s AND source_id=%s AND native_id=%s""",
                    (tenant_id, source_id, native_id),
                )
                artifact_ids = [reference["artifact_id"] for reference in references]
                if artifact_ids:
                    conn.execute(
                        """DELETE FROM raw_artifacts
                           WHERE tenant_id=%s AND source_id=%s
                             AND artifact_id=ANY(%s)""",
                        (tenant_id, source_id, artifact_ids),
                    )
                conn.execute(
                    """UPDATE forget_tombstones
                       SET status='deleted',completed_at=now()
                       WHERE tenant_id=%s AND source_id=%s
                         AND target_identity_sha256=%s""",
                    (tenant_id, source_id, target_sha256),
                )
                conn.execute(
                    """INSERT INTO canonical_audit_events(
                           tenant_id,source_id,audit_id,operation,status,
                           subject_sha256,item_count,byte_count
                       ) VALUES (%s,%s,%s,'forget.commit','success',%s,%s,%s)""",
                    (
                        tenant_id, source_id,
                        _opaque(
                            "audit", tenant_id, source_id, target_sha256,
                            value["idempotency_key"], "success",
                        ),
                        target_sha256, projection_count,
                        sum(reference["size_bytes"] for reference in references),
                    ),
                )
        return {
            "status": "deleted",
            "replay": False,
            "raw_deleted": len(references),
            "projections_deleted": projection_count,
        }
