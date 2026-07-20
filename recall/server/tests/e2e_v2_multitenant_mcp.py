#!/usr/bin/env python3
"""Fresh-PostgreSQL proof for canonical-only personal/company MCP retrieval."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import sys
import tempfile
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "recall"), str(ROOT / "recall/server")]

from client.mac import canonical_envelope
from recall_server.app import Handler
from recall_server.archive import FilesystemArchiveStore
from recall_server.canonical import CanonicalArchiveGateway, CanonicalPlane
from recall_server.canonical_retrieval import CanonicalRetrieval
from recall_server.db import BrainStore


OWNER = "principal:owner:e2e"
OUTSIDER = "principal:outsider:e2e"
VIEWER = "principal:viewer:e2e"
PERSONAL = "tenant:personal:e2e"
COMPANY = "tenant:company:e2e"
PERSONAL_SOURCE = "source:personal:e2e"
COMPANY_SOURCE = "source:company:e2e"
OUTSIDER_SOURCE = "source:company:outsider:e2e"
OCCURRED = "2026-07-20T07:00:00Z"


class FakeSemanticRuntime:
    dimensions = 512
    model = "synthetic-embedding-v1"
    fingerprint = "synthetic-canonical-runtime-v1"

    @staticmethod
    def _vector(text: str) -> list[float]:
        vector = [0.0] * 512
        lowered = text.casefold()
        vector[0] = 1.0 if "personal" in lowered else 0.01
        vector[1] = 1.0 if "company" in lowered else 0.01
        vector[2] = 1.0 if "outsider" in lowered else 0.01
        return vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, query: str) -> list[float]:
        return self._vector(query)


def rpc(server: ThreadingHTTPServer, token: str, name: str, arguments: dict) -> dict:
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    ).encode()
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_port, timeout=10
    )
    connection.request(
        "POST",
        "/mcp",
        body=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "MCP-Protocol-Version": "2025-11-25",
        },
    )
    response = connection.getresponse()
    payload = json.loads(response.read())
    connection.close()
    assert response.status == 200, payload
    return payload


def ingest(
    store: BrainStore,
    archive: FilesystemArchiveStore,
    *,
    tenant_id: str,
    principal_id: str,
    source_id: str,
    native_id: str,
    text: str,
    tombstone: bool = False,
) -> str:
    raw = json.dumps(
        {"text": text, "deleted": tombstone},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    reference = CanonicalArchiveGateway(
        store,
        archive,
        tenant_id=tenant_id,
        principal_id=principal_id,
    ).put_raw(
        tenant_id=tenant_id,
        source_id=source_id,
        native_id=native_id,
        payload=raw,
        media_type="application/json",
        created_at=OCCURRED,
    )
    content = {"target_native_id": native_id} if tombstone else {"text": text}
    event = canonical_envelope(
        source_id=source_id,
        native_id=native_id,
        kind="tombstone" if tombstone else "connector_record",
        content=content,
        principal_id=principal_id,
        visibility="private",
        occurred_at=OCCURRED,
        provenance={
            "uri": f"connector://synthetic/{hashlib.sha256(source_id.encode()).hexdigest()[:8]}",
            "cwd": "/synthetic/unified-brain",
            "branch": "test/multitenant-mcp",
            "connector_id": "synthetic.v2",
            "artifact_ref": reference,
        },
    )
    result = CanonicalPlane(store, archive).ingest_batch(
        tenant_id=tenant_id,
        principal_id=principal_id,
        events=[event],
    )
    assert result["inserted"] == 1
    return result["receipts"][0]


def main() -> None:
    store = BrainStore(
        os.environ["RECALL_DATABASE_URL"],
        semantic_runtime=FakeSemanticRuntime(),
    )
    store.migrate()
    tables = (
        "mcp_credentials,canonical_chunk_embeddings,canonical_source_grants,"
        "brain_access_grants,brain_memberships,brain_spaces,brain_organizations,"
        "forget_tombstones,receipt_redirects,canonical_audit_events,"
        "canonical_chunks,canonical_documents,canonical_events,"
        "canonical_ingest_jobs,raw_artifacts,canonical_sources,"
        "brain_principals,brain_tenants,collector_credentials"
    )
    with store.connect() as connection:
        connection.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
    store.provision_brain(
        organization_id="org:personal:e2e",
        organization_kind="personal",
        display_name="Synthetic Personal",
        tenant_id=PERSONAL,
        brain_kind="personal",
        slug="personal",
        owner_principal_id=OWNER,
    )
    store.provision_brain(
        organization_id="org:company:e2e",
        organization_kind="company",
        display_name="Synthetic Company",
        tenant_id=COMPANY,
        brain_kind="company",
        slug="company",
        owner_principal_id=OWNER,
    )
    with tempfile.TemporaryDirectory() as temporary:
        archive = FilesystemArchiveStore(
            Path(temporary) / "archive",
            namespace_key=b"m" * 32,
        )
        personal_receipt = ingest(
            store,
            archive,
            tenant_id=PERSONAL,
            principal_id=OWNER,
            source_id=PERSONAL_SOURCE,
            native_id="native:personal:e2e",
            text="shared launch marker personal semantic decision",
        )
        company_receipt = ingest(
            store,
            archive,
            tenant_id=COMPANY,
            principal_id=OWNER,
            source_id=COMPANY_SOURCE,
            native_id="native:company:e2e",
            text="shared launch marker company semantic roadmap",
        )
        ingest(
            store,
            archive,
            tenant_id=COMPANY,
            principal_id=OUTSIDER,
            source_id=OUTSIDER_SOURCE,
            native_id="native:outsider:e2e",
            text="shared launch marker outsider confidential plan",
        )
        forget_receipt = ingest(
            store,
            archive,
            tenant_id=PERSONAL,
            principal_id=OWNER,
            source_id=PERSONAL_SOURCE,
            native_id="native:personal:forgettable:e2e",
            text="synthetic forgettable personal note",
        )
        with store.connect() as connection:
            connection.execute(
                """INSERT INTO brain_principals(tenant_id,principal_id)
                   VALUES (%s,%s) ON CONFLICT DO NOTHING""",
                (COMPANY, VIEWER),
            )
            connection.execute(
                """INSERT INTO brain_access_grants(
                       tenant_id,principal_id,permission
                   ) VALUES (%s,%s,'read')""",
                (COMPANY, VIEWER),
            )
        personal_token = store.create_mcp_token(
            "personal-mcp-e2e",
            tenant_id=PERSONAL,
            principal_id=OWNER,
            scopes=["read", "forget"],
        )
        company_token = store.create_mcp_token(
            "company-mcp-e2e",
            tenant_id=COMPANY,
            principal_id=OWNER,
        )
        empty_token = store.create_mcp_token(
            "empty-company-mcp-e2e",
            tenant_id=COMPANY,
            principal_id=VIEWER,
        )
        retrieval = CanonicalRetrieval(store, archive)
        embedding = retrieval.embed_pending()
        assert embedding["processed"] == 4

        def legacy_read_forbidden(*_args, **_kwargs):
            raise AssertionError("legacy retrieval was called")

        store.search = legacy_read_forbidden
        store.show = legacy_read_forbidden
        store.related = legacy_read_forbidden
        previous = {
            name: os.environ.get(name)
            for name in (
                "RECALL_AUTH_REQUIRED",
                "RECALL_HTTP_PROFILE",
                "RECALL_TRUST_TAILSCALE_HEADERS",
                "RECALL_CANONICAL_V2_ENABLED",
                "RECALL_CANONICAL_MCP_ENABLED",
            )
        }
        os.environ.update(
            {
                "RECALL_AUTH_REQUIRED": "1",
                "RECALL_HTTP_PROFILE": "public-mcp",
                "RECALL_TRUST_TAILSCALE_HEADERS": "0",
                "RECALL_CANONICAL_V2_ENABLED": "1",
                "RECALL_CANONICAL_MCP_ENABLED": "1",
            }
        )
        Handler.store = store
        Handler.archive_store = archive
        Handler.canonical_plane = CanonicalPlane(store, archive)
        Handler.canonical_retrieval = retrieval
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            personal = rpc(
                server,
                personal_token["token"],
                "recall_search",
                {"query": "shared launch marker"},
            )
            personal_results = personal["result"]["structuredContent"]["results"]
            assert {row["source_id"] for row in personal_results} == {PERSONAL_SOURCE}
            assert COMPANY_SOURCE not in json.dumps(personal)
            assert OUTSIDER_SOURCE not in json.dumps(personal)

            company = rpc(
                server,
                company_token["token"],
                "recall_search",
                {"query": "shared launch marker"},
            )
            company_results = company["result"]["structuredContent"]["results"]
            assert {row["source_id"] for row in company_results} == {COMPANY_SOURCE}
            assert PERSONAL_SOURCE not in json.dumps(company)
            assert OUTSIDER_SOURCE not in json.dumps(company)

            semantic = rpc(
                server,
                personal_token["token"],
                "recall_search",
                {"query": "personal semantic"},
            )["result"]["structuredContent"]
            assert semantic["results"][0]["receipt"] == personal_receipt
            assert semantic["diagnostics"]["semantic_candidates"] >= 1

            shown = rpc(
                server,
                company_token["token"],
                "recall_show",
                {"target": company_receipt},
            )
            assert (
                shown["result"]["structuredContent"]["event"]["source_id"]
                == COMPANY_SOURCE
            )
            denied_show = rpc(
                server,
                personal_token["token"],
                "recall_show",
                {"target": company_receipt},
            )
            assert denied_show["error"]["message"] == "receipt not found"

            related = rpc(
                server,
                company_token["token"],
                "recall_related",
                {
                    "cwd": "/synthetic/unified-brain",
                    "branch": "test/multitenant-mcp",
                },
            )
            assert {
                row["source_id"]
                for row in related["result"]["structuredContent"]["results"]
            } == {COMPANY_SOURCE}

            empty = rpc(
                server,
                empty_token["token"],
                "recall_search",
                {"query": "shared launch marker"},
            )
            assert empty["result"]["structuredContent"]["results"] == []

            eval_cases = (
                (personal_token["token"], "shared launch marker", personal_receipt),
                (
                    personal_token["token"],
                    "personal semantic decision",
                    personal_receipt,
                ),
                (company_token["token"], "shared launch marker", company_receipt),
                (
                    company_token["token"],
                    "company semantic roadmap",
                    company_receipt,
                ),
            ) * 3
            latencies = []
            recalled = useful = 0
            for token, query, expected in eval_cases:
                started = time.perf_counter()
                evaluated = rpc(
                    server,
                    token,
                    "recall_search",
                    {"query": query, "limit": 5},
                )["result"]["structuredContent"]["results"]
                latencies.append((time.perf_counter() - started) * 1000)
                receipts = [row["receipt"] for row in evaluated]
                recalled += expected in receipts
                useful += bool(receipts and receipts[0] == expected)
            recall_at_5 = recalled / len(eval_cases)
            usefulness = useful / len(eval_cases)
            p95_ms = sorted(latencies)[
                max(0, int(0.95 * len(latencies) + 0.999999) - 1)
            ]
            assert recall_at_5 >= 0.91
            assert usefulness >= 0.80
            assert p95_ms < 8000

            forgotten = rpc(
                server,
                personal_token["token"],
                "recall_forget",
                {"receipt": forget_receipt},
            )["result"]["structuredContent"]
            assert forgotten["raw_deleted"] == 1
            forgotten_show = rpc(
                server,
                personal_token["token"],
                "recall_show",
                {"target": forget_receipt},
            )
            assert forgotten_show["error"]["message"] == "receipt not found"

            ingest(
                store,
                archive,
                tenant_id=PERSONAL,
                principal_id=OWNER,
                source_id=PERSONAL_SOURCE,
                native_id="native:personal:e2e",
                text="",
                tombstone=True,
            )
            deleted = rpc(
                server,
                personal_token["token"],
                "recall_search",
                {"query": "shared launch marker"},
            )
            assert deleted["result"]["structuredContent"]["results"] == []
            deleted_show = rpc(
                server,
                personal_token["token"],
                "recall_show",
                {"target": personal_receipt},
            )
            assert deleted_show["error"]["message"] == "receipt not found"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
    store.close()
    print(
        json.dumps(
            {
                "status": "pass",
                "brains": 2,
                "cross_tenant_hits": 0,
                "cross_principal_hits": 0,
                "empty_grant_hits": 0,
                "legacy_reads": 0,
                "lexical_candidates": 2,
                "semantic_candidates": semantic["diagnostics"][
                    "semantic_candidates"
                ],
                "tombstoned_search_hits": 0,
                "tombstoned_show_hits": 0,
                "forgotten_show_hits": 0,
                "recall_at_5": recall_at_5,
                "usefulness": usefulness,
                "p95_ms": round(p95_ms, 3),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
