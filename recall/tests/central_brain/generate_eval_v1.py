#!/usr/bin/env python3
"""Generate the frozen central-brain v1 evaluation without live/private data."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

FREEZE_EPOCH = 1783891200

PARAPHRASES = [
    ("circuit breaker", "stopped repeated upstream failures with a trip-and-cooldown guard"),
    ("database migration", "moved durable records to the new relational schema without downtime"),
    ("retry budget", "capped repeated attempts so degraded dependencies could recover"),
    ("token refresh", "renewed short-lived credentials before requests began failing"),
    ("cache invalidation", "removed stale computed state after the source changed"),
    ("queue backpressure", "slowed producers when workers could not drain pending jobs"),
    ("schema drift", "handled old and new event shapes during a rolling upgrade"),
    ("dead letter", "quarantined malformed jobs for inspection instead of dropping them"),
    ("graceful shutdown", "finished active work before the process stopped accepting jobs"),
    ("rate limiting", "bounded request bursts independently for each caller"),
    ("distributed lock", "prevented two workers from performing the same singleton task"),
    ("event replay", "rebuilt projections deterministically from immutable source records"),
    ("secret redaction", "removed credentials before text entered logs or derived indexes"),
    ("source isolation", "kept one principal from seeing another source with the same slug"),
    ("temporal fact", "preserved the prior value while marking the newer value current"),
    ("offline spool", "buffered local changes until network connectivity returned"),
    ("idempotent ingest", "made repeated delivery return the original acknowledgement"),
    ("tailnet service", "published a localhost application only to authorized private peers"),
    ("receipt resolution", "reopened the exact source window behind a search result"),
    ("gap analysis", "called out stale or missing evidence instead of inventing an answer"),
    ("cross-device recall", "found work captured on a different machine and harness"),
    ("projection watermark", "tracked how far each derived view had processed the event log"),
    ("tombstone", "recorded deletion while retaining an auditable history marker"),
    ("audience binding", "rejected a token minted for a different protected resource"),
]

NEGATIVES = [
    "kubernetes aardvark admission policy",
    "quantum orchard scheduler",
    "satellite origami telemetry",
    "cobalt submarine payroll",
    "volcanic keyboard firmware",
    "antarctic invoice reconciliation",
    "ceramic compiler register allocator",
    "lunar bakery inventory",
    "neutrino customer support routing",
    "mangrove websocket compression",
    "telescope tax withholding",
    "alpaca kernel panic triage",
    "glacier advertising attribution",
    "marble dns zone transfer",
    "seahorse database vacuum tuning",
    "paprika oauth device flow",
    "zeppelin mobile crash symbolication",
    "bonsai video transcoding pipeline",
]


def split_for(case_id: str) -> str:
    return "holdout" if int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16) % 4 == 0 else "dev"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def main() -> None:
    out = Path(__file__).with_name("eval_v1")
    out.mkdir(parents=True, exist_ok=True)
    corpus: list[dict] = []
    queries: list[dict] = []

    for idx, (topic, paraphrase) in enumerate(PARAPHRASES, 1):
        case_id = f"p{idx:03d}"
        source = "codex:linux" if idx % 2 else "claude:mac"
        native_id = f"session-{idx:03d}:turn-7"
        receipt = f"recall://{source}/{native_id}"
        corpus.append({
            "source_id": source,
            "native_id": native_id,
            "occurred_at": f"2026-06-{(idx % 27) + 1:02d}T12:00:00Z",
            "content": f"Decision record {idx}: {topic}. Evidence marker ev-{idx:03d}.",
            "receipt": receipt,
            "principal_id": "owner",
        })
        queries.append({
            "id": case_id,
            "stratum": "semantic-paraphrase",
            "query": paraphrase,
            "answers": [receipt],
            "split": split_for(case_id),
        })

    for idx, text in enumerate(NEGATIVES, 1):
        case_id = f"n{idx:03d}"
        queries.append({
            "id": case_id,
            "stratum": "negative",
            "query": text,
            "answers": [],
            "split": split_for(case_id),
        })

    # Same native IDs and content words across principals/sources pin isolation at retrieval,
    # show/receipt resolution, and future graph traversal boundaries.
    for idx in range(1, 13):
        case_id = f"i{idx:03d}"
        allowed = f"private:owner-{idx % 3}"
        denied = f"private:decoy-{idx % 2}"
        native_id = f"collision-{idx:03d}"
        token = f"isolation-token-{idx:03d}"
        allowed_receipt = f"recall://{allowed}/{native_id}"
        corpus.extend([
            {"source_id": allowed, "native_id": native_id, "occurred_at": "2026-06-20T12:00:00Z", "content": f"{token} allowed evidence", "receipt": allowed_receipt, "principal_id": "owner"},
            {"source_id": denied, "native_id": native_id, "occurred_at": "2026-06-20T12:00:01Z", "content": f"{token} denied decoy", "receipt": f"recall://{denied}/{native_id}", "principal_id": "intruder"},
        ])
        queries.append({
            "id": case_id,
            "stratum": "source-isolation",
            "query": token,
            "filters": {"principal_id": "owner", "source_id": allowed},
            "answers": [allowed_receipt],
            "forbidden_source_ids": [denied],
            "split": split_for(case_id),
        })

    # Guards are run before freeze: no complete fuzzy/negative prompt may occur in source text.
    corpus_text = "\n".join(row["content"].casefold() for row in corpus)
    contaminated = [q["id"] for q in queries if q["stratum"] in {"semantic-paraphrase", "negative"} and q["query"].casefold() in corpus_text]
    if contaminated:
        raise SystemExit(f"query contamination: {contaminated}")

    dev = [q for q in queries if q["split"] == "dev"]
    holdout = [q for q in queries if q["split"] == "holdout"]
    write_jsonl(out / "corpus.jsonl", corpus)
    write_jsonl(out / "queries-dev.jsonl", dev)
    write_jsonl(out / "queries-holdout.jsonl", holdout)
    hashes = {name: hashlib.sha256((out / name).read_bytes()).hexdigest() for name in ("corpus.jsonl", "queries-dev.jsonl", "queries-holdout.jsonl")}
    manifest = {
        "version": 1,
        "freeze_epoch": FREEZE_EPOCH,
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "counts": {
            "corpus_records": len(corpus),
            "queries": len(queries),
            "dev": len(dev),
            "holdout": len(holdout),
            "semantic-paraphrase": len(PARAPHRASES),
            "negative": len(NEGATIVES),
            "source-isolation": 12,
        },
        "sha256": hashes,
        "contamination_guard": "pass",
        "holdout_policy": "After this manifest is committed, only byte hashing and aggregate scoring may read holdout; no tuning or per-query output.",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
