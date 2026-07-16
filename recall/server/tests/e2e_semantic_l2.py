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

from recall_server.db import BrainStore  # noqa: E402
from recall_server.projectors import canonical_json  # noqa: E402
from recall_server.semantic import SearchPlan  # noqa: E402


class FakeSemanticRuntime:
    model = "synthetic-semantic-v1"
    dimensions = 512
    fingerprint = "1" * 64

    def __init__(self) -> None:
        self.plan_calls = 0
        self.query_calls = 0

    def plan(self, query: str):
        self.plan_calls += 1
        return SearchPlan(False, ()) if "orchard premise" in query else SearchPlan(
            True, ("architecture decision",),
        )

    @staticmethod
    def vector(text: str) -> list[float]:
        value = text.casefold()
        vector = [0.0] * 512
        if "orchard premise" in value:
            vector[2] = 1.0
        else:
            vector[0 if ("trip and cooldown" in value or "circuit breaker" in value) else 1] = 1.0
        return vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.vector(text) for text in texts]

    def embed_query(self, query: str) -> list[float]:
        return self.vector(query)

    def embed_queries(self, queries: list[str]) -> list[list[float]]:
        self.query_calls += 1
        return [self.vector(query) for query in queries]


def envelope(source: str, native: str, text: str, parent: str) -> dict:
    content = {"role": "user", "text": text}
    return {
        "schema_version": 1,
        "source_id": source,
        "native_id": native,
        "native_parent_id": parent,
        "kind": "message",
        "occurred_at": "2026-07-16T00:00:00Z",
        "observed_at": "2026-07-16T00:00:01Z",
        "principal_id": "owner",
        "visibility": "private",
        "content_type": "application/json",
        "content": content,
        "provenance": {"harness": "codex"},
        "content_sha256": hashlib.sha256(canonical_json(content)).hexdigest(),
    }


def main() -> None:
    runtime = FakeSemanticRuntime()
    store = BrainStore(os.environ["RECALL_DATABASE_URL"], semantic_runtime=runtime)
    store.migrate()
    with store.connect() as connection:
        connection.execute(
            "TRUNCATE item_embeddings,session_export_cursors,chunks,entities,items,sessions,"
            "projection_watermarks,source_events,ingest_batches,source_profiles,source_grants,"
            "sources,dead_letters,audit_events RESTART IDENTITY CASCADE"
        )
    store.ingest("semantic-a", [
        envelope("source-a", "target", "Architecture decision: circuit breaker.", "session-a"),
        envelope("source-a", "other-surface", "Verbose tool transcript.", "session-a")
        | {"kind": "tool_output"},
        envelope("source-b", "decoy", "Architecture decision: unrelated queue.", "session-b"),
    ])
    first = store.embed_pending(batch_size=10, source_id="source-a", surface="message")
    scoped_replay = store.embed_pending(batch_size=10, source_id="source-a", surface="message")
    remaining = store.embed_pending(batch_size=10)
    second = store.embed_pending(batch_size=10)
    with store.connect() as connection:
        connection.execute(
            """UPDATE item_embeddings SET runtime_fingerprint=%s
               WHERE source_id='source-a' AND item_id=(
                   SELECT id FROM items WHERE event_native_id='target'
               )""",
            ("0" * 64,),
        )
    stale = store.search(
        "stopped repeated upstream failures with a trip and cooldown guard", {}, 5,
        authorized_source="source-a",
    )
    stale_metrics = store.service_metrics()
    repaired = store.embed_pending(batch_size=10)
    result = store.search(
        "stopped repeated upstream failures with a trip and cooldown guard", {}, 5,
        authorized_source="source-a",
    )
    bounded = store.search(
        "stopped repeated upstream failures with a trip and cooldown guard", {}, 1,
        authorized_source="source-a",
    )
    planner_calls = runtime.plan_calls
    store.search(
        "find rollout password=syntheticvalue123", {}, 5,
        authorized_source="source-a",
    )
    assert first["processed"] == 1 and first["source_scoped"] is True
    assert first["surface_scoped"] is True
    assert scoped_replay["processed"] == 0 and remaining["processed"] == 2
    assert second["processed"] == 0
    assert all(
        "semantic" not in row["legs"]
        for row in stale["results"] if row["native_id"] == "target"
    )
    assert stale_metrics["embedding_lag"] == 1
    assert repaired["processed"] == 1
    assert runtime.plan_calls == planner_calls and runtime.query_calls > 0
    assert [row["source_id"] for row in result["results"]] == ["source-a"]
    assert len(bounded["results"]) <= 1
    assert all(
        not leg["leg"].startswith("rewrite-")
        for leg in bounded["diagnostics"]["legs"]
    )
    legs = [leg["leg"] for leg in result["diagnostics"]["legs"]]
    assert legs.index("semantic-0") < legs.index("rewrite-0")
    assert result["results"][0]["native_id"] == "target"
    assert "semantic" in result["results"][0]["legs"]
    abstained = store.search("nonexistent orchard premise", {}, 5)
    assert abstained["results"] == []
    assert any(
        leg["leg"] == "semantic-0" for leg in abstained["diagnostics"]["legs"]
    )
    print(json.dumps({
        "status": "pass", "semantic_hit": 1, "unauthorized_hits": 0,
        "first_backfill": first["processed"] + remaining["processed"],
        "idempotent_replay": second["processed"], "source_scoped_replay": 0,
        "stale_vectors_searched": 0, "stale_vectors_repaired": repaired["processed"],
        "sensitive_queries_sent_to_planner": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
