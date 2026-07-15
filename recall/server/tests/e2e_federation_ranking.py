#!/usr/bin/env python3
"""Fresh-PostgreSQL proof for source-aware federated Recall ranking."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "recall/server"))

from recall_server.db import BrainStore, SearchDeadlineExceeded
from recall_server.projectors import canonical_json


FIXTURES = ROOT / "recall/tests/central_brain/federation_eval_v1"


def rows(name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURES / name).read_text().splitlines()]


def envelope(value: dict, *, kind: str = "memory", content: dict | None = None) -> dict:
    payload = content or {"text": value["text"]}
    result = {
        "schema_version": 1, "source_id": value["source_id"],
        "native_id": value["native_id"], "kind": kind,
        "occurred_at": value["occurred_at"],
        "observed_at": "2026-07-14T12:00:00Z",
        "principal_id": "owner", "visibility": "private",
        "content_type": "application/json", "content": payload,
        "provenance": {"uri": "manual://synthetic-federation-eval"},
    }
    result["content_sha256"] = hashlib.sha256(canonical_json(payload)).hexdigest()
    return result


def metrics(store: BrainStore, queries: list[dict]) -> dict:
    reciprocal_ranks = []
    recalled = answerable = 0
    stale_correct = stale_total = 0
    quality_correct = quality_total = 0
    privacy_hits = false_positives = 0
    for query in queries:
        result = store.search(query["query"], limit=5)
        ranked = [item["receipt"] for item in result["results"]]
        for item in result["results"]:
            evidence = item["evidence"]
            for key in (
                "lexical_component", "freshness_component", "quality_component",
                "corroboration_component", "rank_score",
            ):
                assert 0 <= evidence[key] <= 1
            assert set(item["source_profile"]) == {"profiled", "family", "quality"}
        answers = set(query["answers"])
        if answers:
            answerable += 1
            positions = [index for index, receipt in enumerate(ranked, 1) if receipt in answers]
            recalled += int(bool(positions) and positions[0] <= 5)
            reciprocal_ranks.append(1 / positions[0] if positions else 0.0)
        if query["stratum"] == "stale-conflict":
            stale_total += 1
            stale_correct += int(bool(ranked) and ranked[0] in answers)
        elif query["stratum"] == "source-quality":
            quality_total += 1
            quality_correct += int(
                bool(ranked) and ranked[0] in set(query["high_quality_answers"])
            )
        elif query["stratum"] == "privacy-canary":
            privacy_hits += len(ranked)
        elif query["stratum"] == "no-answer":
            false_positives += int(bool(ranked))
    return {
        "recall_at_5": recalled / answerable,
        "mrr": sum(reciprocal_ranks) / answerable,
        "stale_conflict_accuracy": stale_correct / stale_total,
        "high_quality_source_precision": quality_correct / quality_total,
        "privacy_canary_hits": privacy_hits,
        "no_answer_false_positives": false_positives,
    }


def main() -> None:
    store = BrainStore(os.environ["RECALL_DATABASE_URL"])
    store.migrate()
    with store.connect() as connection:
        connection.execute(
            "TRUNCATE entities,chunks,items,sessions,projection_watermarks,"
            "source_profiles,source_events,ingest_batches,collector_credentials,"
            "source_grants,sources,dead_letters,audit_events RESTART IDENTITY CASCADE"
        )
    corpus = rows("corpus.jsonl")
    queries = rows("queries.jsonl")
    profiles = rows("profiles.jsonl")
    events = [envelope(value) for value in corpus]
    acknowledgement, replay = store.ingest("federation-eval-v1", events)
    assert acknowledgement["inserted"] == len(events) and replay is False
    replayed, replay = store.ingest("federation-eval-v1", events)
    assert replay is True and replayed == acknowledgement

    for profile in profiles:
        receipt = store.set_source_profile(profile)
        assert receipt == {"status": "configured", **profile}
    scoreboard = store.federation_scoreboard()
    assert scoreboard["profiled_sources"] == 5
    assert scoreboard["unprofiled_sources"] == 0
    assert len(scoreboard["groups"]) == 4
    rendered_scoreboard = json.dumps(scoreboard)
    assert all(profile["source_id"] not in rendered_scoreboard for profile in profiles)

    spoofed = {**events[0], "native_id": "profile-spoof", "source_quality": "authoritative"}
    try:
        store.ingest("profile-spoof", [spoofed])
    except ValueError as error:
        assert str(error) == "source profile is host-controlled"
    else:
        raise AssertionError("ingest client changed source quality")

    measured = metrics(store, queries)
    assert measured["recall_at_5"] >= 0.95
    assert measured["mrr"] >= 0.90
    assert measured["stale_conflict_accuracy"] == 1.0
    assert measured["high_quality_source_precision"] >= 0.90
    assert measured["privacy_canary_hits"] == 0
    assert measured["no_answer_false_positives"] == 0

    quality_query = next(value for value in queries if value["id"] == "quality-01")
    unscoped = store.search(quality_query["query"], limit=5)
    assert unscoped["results"][0]["receipt"] in quality_query["answers"]
    scoped = store.search(
        quality_query["query"], limit=5,
        authorized_source="synthetic:research:unrated",
    )
    assert scoped["results"]
    assert {value["source_id"] for value in scoped["results"]} == {
        "synthetic:research:unrated",
    }
    assert scoped["results"][0]["evidence"]["corroborating_families"] == 1

    corroboration_query = next(value for value in queries if value["id"] == "corroboration-01")
    corroborated = store.search(corroboration_query["query"], limit=5)
    assert corroborated["results"][0]["receipt"] in corroboration_query["answers"]
    assert corroborated["results"][0]["evidence"]["corroborating_families"] == 2

    winner = next(value for value in corpus if value["receipt"] == quality_query["answers"][0])
    tombstone = envelope(
        winner, kind="tombstone",
        content={"target_native_id": winner["native_id"]},
    )
    deleted, replay = store.ingest("federation-delete", [tombstone])
    assert not replay and deleted["receipts"][0].endswith("?rev=2")
    assert store.resolve(winner["receipt"]) is None
    assert store.search(quality_query["query"], limit=5)["results"][0]["receipt"] != winner["receipt"]

    sample_queries = queries[:10]
    before_rebuild = [
        [item["receipt"] for item in store.search(value["query"], limit=5)["results"]]
        for value in sample_queries
    ]
    rebuilt = store.rebuild()
    assert rebuilt["items_before"] == rebuilt["items_after"]
    after_rebuild = [
        [item["receipt"] for item in store.search(value["query"], limit=5)["results"]]
        for value in sample_queries
    ]
    assert before_rebuild == after_rebuild
    restarted = BrainStore(os.environ["RECALL_DATABASE_URL"])
    assert [
        item["receipt"] for item in restarted.search(sample_queries[1]["query"], limit=5)["results"]
    ] == after_rebuild[1]

    bounded_started = time.monotonic()
    with store.connect() as connection:
        try:
            with connection.transaction():
                store._execute_bounded(
                    connection, "SELECT pg_sleep(1)", [], time.monotonic() + 0.03,
                )
        except SearchDeadlineExceeded:
            pass
        else:
            raise AssertionError("federated query escaped the deadline")
    assert time.monotonic() - bounded_started < 0.25

    with store.connect() as connection:
        assert connection.execute(
            "SELECT count(*) AS n FROM source_events WHERE native_id='profile-spoof'"
        ).fetchone()["n"] == 0
        assert connection.execute(
            "SELECT count(*) AS n FROM schema_migrations WHERE version=7"
        ).fetchone()["n"] == 1

    print(json.dumps({
        "status": "pass", "metrics": measured,
        "summary": {
            "source_profiles": 5, "source_families": 4,
            "ingest_profile_spoofs": 0, "cross_source_effects": 0,
            "replay_duplicates": 0, "corroborating_families": 2,
            "exact_deletions": 1, "rebuild_equivalent": True,
            "restart_equivalent": True, "deadline_exceeded_safely": True,
        },
    }, sort_keys=True))


if __name__ == "__main__":
    main()
