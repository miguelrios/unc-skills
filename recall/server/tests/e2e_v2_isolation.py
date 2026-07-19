#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
ROOT = SERVER.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SERVER))

from recall_server.archive import ArchiveRequest, FilesystemArchiveStore
from recall_server.canonical import (
    CanonicalArchiveGateway,
    CanonicalLifecycleError,
    CanonicalPlane,
)
from recall_server.db import BrainStore
from recall_server.projectors import canonical_json


def event(
    *,
    source_id: str,
    native_id: str,
    principal_id: str,
    artifact: dict,
    text: str,
) -> dict:
    content = {"text": text}
    return {
        "schema_version": 1,
        "source_id": source_id,
        "native_id": native_id,
        "native_parent_id": native_id,
        "kind": "connector_record",
        "occurred_at": "2026-07-19T13:00:00Z",
        "observed_at": "2026-07-19T13:00:00Z",
        "principal_id": principal_id,
        "visibility": "private",
        "content_type": "application/json",
        "content": content,
        "provenance": {
            "connector_id": "synthetic.isolation",
            "connector_schema_version": 1,
            "artifact_ref": artifact,
        },
        "content_sha256": hashlib.sha256(canonical_json(content)).hexdigest(),
    }


def main() -> None:
    dsn = os.environ["RECALL_DATABASE_URL"]
    store = BrainStore(dsn)
    store.migrate()
    nonce = uuid.uuid4().hex
    source_id = f"source:isolation:{nonce}"
    native_id = "native:same-across-tenants"
    tenants = [f"tenant:isolation:{nonce}:{suffix}" for suffix in ("a", "b")]
    principals = [f"principal:isolation:{nonce}:{suffix}" for suffix in ("a", "b")]

    with tempfile.TemporaryDirectory() as temporary:
        archive = FilesystemArchiveStore(
            Path(temporary) / "archive",
            namespace_key=b"i" * 32,
        )
        plane = CanonicalPlane(store, archive)
        artifacts = []
        ingests = []
        for index, (tenant_id, principal_id) in enumerate(
            zip(tenants, principals, strict=True)
        ):
            gateway = CanonicalArchiveGateway(
                store,
                archive,
                tenant_id=tenant_id,
                principal_id=principal_id,
            )
            raw = json.dumps({
                "tenant_index": index,
                "raw_canary": f"private-isolation-canary-{index}",
            }, sort_keys=True).encode()
            artifact = gateway.put_raw(
                tenant_id=tenant_id,
                source_id=source_id,
                native_id=native_id,
                payload=raw,
                media_type="application/json",
                created_at="2026-07-19T13:00:00Z",
            )
            artifacts.append(artifact)
            ingests.append(plane.ingest_document(
                tenant_id=tenant_id,
                principal_id=principal_id,
                connector_id="synthetic.isolation",
                artifact_ref=artifact,
                envelope=event(
                    source_id=source_id,
                    native_id=native_id,
                    principal_id=principal_id,
                    artifact=artifact,
                    text=f"safe tenant evidence {index}",
                ),
                text_redacted=f"safe tenant evidence {index}",
            ))

        try:
            plane.ingest_document(
                tenant_id=tenants[0],
                principal_id=principals[0],
                connector_id="synthetic.isolation",
                artifact_ref=artifacts[1],
                envelope=event(
                    source_id=source_id,
                    native_id="native:forged",
                    principal_id=principals[0],
                    artifact=artifacts[1],
                    text="forged",
                ),
                text_redacted="forged",
            )
        except CanonicalLifecycleError as error:
            if error.error_code != "canonical_lineage_invalid":
                raise
        else:
            raise RuntimeError("cross-tenant artifact lineage was accepted")

        forget = {
            "contract": "recall.forget-request.v1",
            "schema_version": 1,
            "tenant_id": tenants[0],
            "principal_id": principals[0],
            "source_id": source_id,
            "target_receipt": ingests[0]["receipt"],
            "mode": "explicit_forget",
            "reason": "owner_requested",
            "requested_at": "2026-07-19T13:05:00Z",
            "idempotency_key": "isolation-forget-" + nonce,
        }
        unauthorized = {**forget, "principal_id": principals[1]}
        try:
            plane.forget(unauthorized)
        except CanonicalLifecycleError as error:
            if error.error_code != "forget_authority_forbidden":
                raise
        else:
            raise RuntimeError("cross-principal forget was accepted")
        deleted = plane.forget(forget)
        if deleted["raw_deleted"] != 1:
            raise RuntimeError("tenant-scoped forget did not delete one raw object")

        with store.connect() as connection:
            counts = connection.execute(
                """SELECT tenant_id,
                          count(*) FILTER (WHERE deleted_at IS NULL) AS live_chunks
                   FROM canonical_chunks
                   WHERE tenant_id=ANY(%s) AND source_id=%s
                   GROUP BY tenant_id ORDER BY tenant_id""",
                (tenants, source_id),
            ).fetchall()
            tombstones = connection.execute(
                """SELECT tenant_id,count(*) AS count
                   FROM forget_tombstones
                   WHERE tenant_id=ANY(%s) AND source_id=%s
                   GROUP BY tenant_id ORDER BY tenant_id""",
                (tenants, source_id),
            ).fetchall()
        if [(row["tenant_id"], row["live_chunks"]) for row in counts] != [
            (tenants[1], 1),
        ]:
            raise RuntimeError("tenant-scoped projection isolation failed")
        if [(row["tenant_id"], row["count"]) for row in tombstones] != [
            (tenants[0], 1),
        ]:
            raise RuntimeError("tenant-scoped forget proof escaped")
        remaining = archive.put(ArchiveRequest(
            tenant_id=tenants[1],
            source_id=source_id,
            native_id=native_id,
            media_type="application/json",
            payload=json.dumps({
                "tenant_index": 1,
                "raw_canary": "private-isolation-canary-1",
            }, sort_keys=True).encode(),
        ))
        archive.read(
            remaining,
            tenant_id=tenants[1],
            source_id=source_id,
        )
        if (archive.root / artifacts[0]["object_key"] / "data").exists():
            raise RuntimeError("forgotten tenant raw object survived")
    store.close()
    print(json.dumps({
        "status": "pass",
        "tenants": 2,
        "same_source_and_native": True,
        "cross_tenant_lineage_rejections": 1,
        "cross_principal_forget_rejections": 1,
        "tenant_b_live_chunks": 1,
        "tenant_b_raw_objects": 1,
        "tenant_a_tombstones": 1,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
