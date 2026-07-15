#!/usr/bin/env python3
"""Generate Recall's frozen, fully synthetic natural-language retrieval evaluation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


FREEZE_EPOCH = 1784073600
FILES = ("corpus.jsonl", "queries-dev.jsonl", "queries-holdout.jsonl")
STRATA = (
    "exact-identifier",
    "semantic-paraphrase",
    "source-routed",
    "temporal-state",
    "cross-session-synthesis",
    "workflow-gotcha",
    "session-reconstruction",
    "negative-premise",
    "privacy-auth",
    "deletion-forgetting",
    "deduplication",
)
SEMANTIC = (
    ("circuit breaker", "stopped repeated upstream failures with a trip and cooldown guard"),
    ("database migration", "moved durable records into a new relational layout without downtime"),
    ("retry budget", "limited repeated attempts so a degraded dependency could recover"),
    ("token refresh", "renewed short lived credentials before requests started failing"),
    ("cache invalidation", "removed stale computed state after its source changed"),
    ("queue backpressure", "slowed producers when workers could not drain pending jobs"),
    ("schema drift", "accepted old and new event shapes through a rolling upgrade"),
    ("dead letter", "quarantined malformed jobs for inspection instead of dropping them"),
    ("graceful shutdown", "finished active work before refusing new jobs"),
    ("rate limiting", "bounded request bursts separately for each caller"),
    ("distributed lock", "prevented two workers from running one singleton operation"),
    ("event replay", "rebuilt a derived view from immutable source records"),
)


def split(case_id: str) -> str:
    return "holdout" if int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16) % 4 == 0 else "dev"


def receipt(source: str, native_id: str) -> str:
    return f"recall://{source}/{native_id}?rev=1#item=0"


def record(
    source: str,
    native_id: str,
    text: str | dict,
    occurred_at: str,
    *,
    parent: str | None = None,
    principal: str = "owner",
    kind: str = "message",
    replay: bool = False,
) -> dict:
    return {
        "source_id": source,
        "native_id": native_id,
        "native_parent_id": parent,
        "kind": kind,
        "occurred_at": occurred_at,
        "principal_id": principal,
        "content": text,
        "replay": replay,
    }


def query(case_id: str, stratum: str, text: str, answers: list[str], **extra) -> dict:
    return {
        "id": case_id,
        "stratum": stratum,
        "query": text,
        "answers": answers,
        "split": split(case_id),
        **extra,
    }


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def main() -> None:
    output = Path(__file__).with_name("retrieval_eval_v2")
    output.mkdir(parents=True, exist_ok=True)
    corpus: list[dict] = []
    queries: list[dict] = []

    for index in range(1, 13):
        marker = f"SYNTH-TRACE-{index:02d}-B7Q"
        source = "synthetic:codex"
        native = f"exact-{index:02d}"
        corpus.append(record(source, native, f"Verification marker {marker} closed the task.", f"2026-06-{index:02d}T08:00:00Z"))
        queries.append(query(f"exact-{index:02d}", "exact-identifier", marker, [receipt(source, native)]))

    for index, (topic, paraphrase) in enumerate(SEMANTIC, 1):
        source = "synthetic:assistant"
        native = f"semantic-{index:02d}"
        corpus.append(record(source, native, f"Architecture decision: {topic}.", f"2026-06-{index:02d}T09:00:00Z"))
        queries.append(query(f"semantic-{index:02d}", "semantic-paraphrase", paraphrase, [receipt(source, native)]))

    for index in range(1, 13):
        topic = f"mosaic project {index:02d}"
        wanted_source = "synthetic:cowork"
        decoy_source = "synthetic:codex"
        wanted = f"route-{index:02d}-wanted"
        decoy = f"route-{index:02d}-decoy"
        stamp = f"2026-06-{index:02d}T10:00:00Z"
        corpus.extend((
            record(wanted_source, wanted, f"{topic} selected the amber rollout.", stamp),
            record(decoy_source, decoy, f"{topic} selected the indigo rollout.", stamp),
        ))
        queries.append(query(
            f"route-{index:02d}", "source-routed",
            f"In Cowork, what did we select for {topic}?",
            [receipt(wanted_source, wanted)],
            route_source_id=wanted_source,
            forbidden_source_ids=[decoy_source],
        ))

    for index in range(1, 13):
        marker = f"temporal-marker-{index:02d}"
        source = "synthetic:state"
        stale = f"temporal-{index:02d}-stale"
        current = f"temporal-{index:02d}-current"
        corpus.extend((
            record(source, stale, f"{marker} status is cedar and superseded.", "2025-01-01T12:00:00Z"),
            record(source, current, f"{marker} status is cobalt and current.", "2026-07-01T12:00:00Z"),
        ))
        queries.append(query(
            f"temporal-{index:02d}", "temporal-state", f"What is the current {marker} status?",
            [receipt(source, current)], forbidden=[receipt(source, stale)],
        ))

    for index in range(1, 13):
        marker = f"synthesis-marker-{index:02d}"
        source = "synthetic:multi"
        first = f"synthesis-{index:02d}-a"
        second = f"synthesis-{index:02d}-b"
        corpus.extend((
            record(source, first, f"{marker} constraint: retain the audit trail.", "2026-06-20T12:00:00Z", parent=f"episode-{index:02d}-a"),
            record(source, second, f"{marker} outcome: the replay stayed deterministic.", "2026-06-21T12:00:00Z", parent=f"episode-{index:02d}-b"),
        ))
        queries.append(query(
            f"synthesis-{index:02d}", "cross-session-synthesis",
            f"Gather the constraint and outcome for {marker}.",
            [receipt(source, first), receipt(source, second)],
        ))

    for index, (topic, paraphrase) in enumerate(SEMANTIC, 1):
        source = "synthetic:workflow"
        native = f"workflow-{index:02d}"
        corpus.append(record(source, native, f"Runbook gotcha: {topic} was required before deploy.", f"2026-06-{index:02d}T13:00:00Z"))
        queries.append(query(
            f"workflow-{index:02d}", "workflow-gotcha",
            f"How did we handle the deployment when we {paraphrase}?",
            [receipt(source, native)],
        ))

    for index in range(1, 13):
        source = "synthetic:session"
        parent = f"conversation-{index:02d}"
        before = f"session-{index:02d}-turn-1"
        target = f"session-{index:02d}-turn-2"
        after = f"session-{index:02d}-turn-3"
        day = f"{index:02d}"
        corpus.extend((
            record(source, before, f"context opening {index:02d}", f"2026-06-{day}T14:00:00Z", parent=parent),
            record(source, target, f"window-marker-{index:02d} decision", f"2026-06-{day}T14:01:00Z", parent=parent),
            record(source, after, f"context closing {index:02d}", f"2026-06-{day}T14:02:00Z", parent=parent),
        ))
        queries.append(query(
            f"session-{index:02d}", "session-reconstruction", f"window-marker-{index:02d}",
            [receipt(source, target)],
            around=f"2026-06-{day}T14:01:00Z",
            expected_window=[receipt(source, before), receipt(source, target), receipt(source, after)],
        ))

    for index in range(1, 13):
        queries.append(query(
            f"negative-{index:02d}", "negative-premise",
            f"nonexistent obsidian orchard premise {index:02d}", [],
        ))

    for index in range(1, 13):
        marker = f"auth-marker-{index:02d}"
        allowed_source = f"synthetic:allowed-{index % 3}"
        denied_source = f"synthetic:denied-{index % 2}"
        native = f"auth-{index:02d}"
        corpus.extend((
            record(allowed_source, native, f"{marker} allowed evidence", "2026-06-22T12:00:00Z", principal="owner"),
            record(denied_source, native, f"{marker} denied evidence", "2026-06-22T12:00:01Z", principal="intruder"),
        ))
        queries.append(query(
            f"auth-{index:02d}", "privacy-auth", marker,
            [receipt(allowed_source, native)],
            filters={"authorized_source": allowed_source},
            forbidden_source_ids=[denied_source],
        ))

    for index in range(1, 13):
        source = "synthetic:deletion"
        native = f"deleted-{index:02d}"
        corpus.extend((
            record(source, native, f"deleted-marker-{index:02d} temporary evidence", "2026-06-23T12:00:00Z"),
            record(
                source, native, {"target_native_id": native}, "2026-06-23T12:01:00Z",
                kind="tombstone",
            ),
        ))
        queries.append(query(
            f"deleted-{index:02d}", "deletion-forgetting", f"deleted-marker-{index:02d}", [],
        ))

    for index in range(1, 13):
        source = "synthetic:dedupe"
        native = f"dedupe-{index:02d}"
        corpus.append(record(
            source, native, f"dedupe-marker-{index:02d} stable evidence",
            "2026-06-24T12:00:00Z", replay=True,
        ))
        queries.append(query(
            f"dedupe-{index:02d}", "deduplication", f"dedupe-marker-{index:02d}",
            [receipt(source, native)],
        ))

    counts = {stratum: sum(value["stratum"] == stratum for value in queries) for stratum in STRATA}
    if any(value != 12 for value in counts.values()):
        raise SystemExit(f"stratum count mismatch: {counts}")
    if len({value["id"] for value in queries}) != len(queries):
        raise SystemExit("duplicate query IDs")
    corpus_text = "\n".join(
        json.dumps(value["content"], sort_keys=True).casefold() for value in corpus
    )
    contaminated = [
        value["id"] for value in queries
        if value["stratum"] == "negative-premise" and value["query"].casefold() in corpus_text
    ]
    if contaminated:
        raise SystemExit(f"negative contamination: {contaminated}")

    dev = [value for value in queries if value["split"] == "dev"]
    holdout = [value for value in queries if value["split"] == "holdout"]
    write_jsonl(output / "corpus.jsonl", corpus)
    write_jsonl(output / "queries-dev.jsonl", dev)
    write_jsonl(output / "queries-holdout.jsonl", holdout)
    hashes = {name: hashlib.sha256((output / name).read_bytes()).hexdigest() for name in FILES}
    manifest = {
        "schema_version": 2,
        "freeze_epoch": FREEZE_EPOCH,
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "counts": {
            "corpus_records": len(corpus),
            "queries": len(queries),
            "dev": len(dev),
            "holdout": len(holdout),
            **counts,
        },
        "sha256": hashes,
        "contamination_guard": "pass",
        "holdout_policy": "Only aggregate scoring may be published for holdout; no tuning or per-query inspection.",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
