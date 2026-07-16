#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import sys
import threading
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


class CoordinatedSemanticRuntime(FakeSemanticRuntime):
    def __init__(self, barrier: threading.Barrier) -> None:
        super().__init__()
        self.barrier = barrier

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.barrier.wait(timeout=3)
        return super().embed_documents(texts)


class BlockingSemanticRuntime(FakeSemanticRuntime):
    def __init__(self, started: threading.Event, release: threading.Event) -> None:
        super().__init__()
        self.started = started
        self.release = release

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.started.set()
        if not self.release.wait(timeout=3):
            raise TimeoutError("test backfill was not released")
        return super().embed_documents(texts)


class FailingSemanticRuntime(FakeSemanticRuntime):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("synthetic embedding failure")


def envelope(source: str, native: str, text: str, parent: str, *,
             role: str = "user", occurred_at: str = "2026-07-16T00:00:00Z") -> dict:
    content = {"role": role, "text": text}
    return {
        "schema_version": 1,
        "source_id": source,
        "native_id": native,
        "native_parent_id": parent,
        "kind": "message",
        "occurred_at": occurred_at,
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
    planner_calls_before_dense_bounded = runtime.plan_calls
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
    assert runtime.plan_calls == planner_calls_before_dense_bounded
    assert all(
        not leg["leg"].startswith("rewrite-")
        for leg in bounded["diagnostics"]["legs"]
    )
    assert "phrase" not in [leg["leg"] for leg in bounded["diagnostics"]["legs"]]
    legs = [leg["leg"] for leg in result["diagnostics"]["legs"]]
    assert legs.index("semantic-0") < legs.index("rewrite-0")
    assert result["results"][0]["native_id"] == "target"
    assert "semantic" in result["results"][0]["legs"]
    planner_calls_before_sparse = runtime.plan_calls
    abstained = store.search("nonexistent orchard premise", {}, 5)
    assert runtime.plan_calls == planner_calls_before_sparse + 1
    assert abstained["results"] == []
    assert any(
        leg["leg"] == "semantic-0" for leg in abstained["diagnostics"]["legs"]
    )
    assert any(
        leg["leg"] == "phrase" for leg in abstained["diagnostics"]["legs"]
    )
    store.ingest("answer-adjacency-h", [
        envelope(
            "source-h", "question", "What did we decide about the orchard premise?", "session-h",
        ),
        envelope(
            "source-h", "next-question", "What should we review next?", "session-h",
            occurred_at="2026-07-16T00:03:00Z",
        ),
        envelope(
            "source-h", "progress", "I am checking the relevant constraints.", "session-h",
            role="assistant", occurred_at="2026-07-16T00:01:00Z",
        ),
        envelope(
            "source-h", "answer", "The final decision was to use river stones.", "session-h",
            role="assistant", occurred_at="2026-07-16T00:02:00Z",
        ),
    ])
    assert store.embed_pending(batch_size=10, source_id="source-h")["processed"] == 4
    adjacent = store.search(
        "remind me what we chose for the orchard premise", {}, 5,
        authorized_source="source-h",
    )
    assert adjacent["results"][0]["native_id"] == "answer", adjacent
    assert "answer" in adjacent["results"][0]["legs"]
    bounded_answer = store.search(
        "remind me what we chose for the orchard premise",
        {"until": "2026-07-16T00:01:30Z"}, 5,
        authorized_source="source-h",
    )
    assert bounded_answer["results"][0]["native_id"] == "progress", bounded_answer
    store.ingest("parallel-c", [
        envelope("source-c", "parallel-c", "Parallel source C.", "session-c"),
    ])
    store.ingest("parallel-d", [
        envelope("source-d", "parallel-d", "Parallel source D.", "session-d"),
    ])
    barrier = threading.Barrier(2)
    parallel_stores = [
        BrainStore(
            os.environ["RECALL_DATABASE_URL"],
            semantic_runtime=CoordinatedSemanticRuntime(barrier),
        )
        for _ in range(2)
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                parallel_stores[index].embed_pending,
                batch_size=1,
                max_batches=1,
                source_id=source_id,
                surface="message",
            )
            for index, source_id in enumerate(("source-c", "source-d"))
        ]
        parallel_results = [future.result() for future in futures]
    assert [result["processed"] for result in parallel_results] == [1, 1]
    store.ingest("parallel-e", [
        envelope("source-e", "parallel-e-1", "Parallel source E one.", "session-e"),
        envelope("source-e", "parallel-e-2", "Parallel source E two.", "session-e"),
    ])
    same_source_barrier = threading.Barrier(2)
    same_source_stores = [
        BrainStore(
            os.environ["RECALL_DATABASE_URL"],
            semantic_runtime=CoordinatedSemanticRuntime(same_source_barrier),
        )
        for _ in range(2)
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                candidate.embed_pending,
                batch_size=1,
                max_batches=1,
                source_id="source-e",
                surface="message",
            )
            for candidate in same_source_stores
        ]
        same_source_results = [future.result() for future in futures]
    assert [result["processed"] for result in same_source_results] == [1, 1]
    with store.connect() as connection:
        same_source_persisted = connection.execute(
            """SELECT count(*) AS n FROM item_embeddings embedding
               JOIN items item ON item.id=embedding.item_id
               WHERE item.source_id='source-e'"""
        ).fetchone()["n"]
    assert same_source_persisted == 2
    store.ingest("blocked-f", [
        envelope("source-f", "blocked-f", "Blocked source F.", "session-f"),
    ])
    started = threading.Event()
    release = threading.Event()
    blocking_store = BrainStore(
        os.environ["RECALL_DATABASE_URL"],
        semantic_runtime=BlockingSemanticRuntime(started, release),
    )
    competing_store = BrainStore(
        os.environ["RECALL_DATABASE_URL"], semantic_runtime=FakeSemanticRuntime(),
    )
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        active = executor.submit(
            blocking_store.embed_pending,
            batch_size=1,
            max_batches=1,
            source_id="source-f",
            surface="message",
        )
        assert started.wait(timeout=3)
        unscoped = competing_store.embed_pending(batch_size=1, max_batches=1)
        release.set()
        active_result = active.result()
    assert active_result["processed"] == 1
    assert unscoped == {"status": "busy", "processed": 0, "batches": 0}
    store.ingest("recoverable-g", [
        envelope("source-g", "recoverable-g", "Recoverable source G.", "session-g"),
    ])
    failing_store = BrainStore(
        os.environ["RECALL_DATABASE_URL"],
        semantic_runtime=FailingSemanticRuntime(),
    )
    try:
        failing_store.embed_pending(
            batch_size=1,
            max_batches=1,
            source_id="source-g",
            surface="message",
        )
    except RuntimeError as exc:
        assert str(exc) == "synthetic embedding failure"
    else:
        raise AssertionError("synthetic embedding failure did not propagate")
    recovered_claim = competing_store.embed_pending(
        batch_size=1,
        max_batches=1,
        source_id="source-g",
        surface="message",
    )
    assert recovered_claim["processed"] == 1
    print(json.dumps({
        "status": "pass", "semantic_hit": 1, "answer_adjacency_hit": 1,
        "unauthorized_hits": 0,
        "first_backfill": first["processed"] + remaining["processed"],
        "idempotent_replay": second["processed"], "source_scoped_replay": 0,
        "stale_vectors_searched": 0, "stale_vectors_repaired": repaired["processed"],
        "sensitive_queries_sent_to_planner": 0,
        "parallel_source_backfills": sum(
            result["processed"] for result in parallel_results
        ),
        "parallel_same_source_backfills": sum(
            result["processed"] for result in same_source_results
        ),
        "parallel_same_source_persisted": same_source_persisted,
        "released_claims_recovered": recovered_claim["processed"],
        "unscoped_competing_backfills": unscoped["processed"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
