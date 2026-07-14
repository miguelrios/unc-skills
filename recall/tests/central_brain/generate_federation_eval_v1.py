#!/usr/bin/env python3
"""Generate the frozen synthetic federation-ranking evaluation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


FREEZE_EPOCH = 1783987200
FILES = ("profiles.jsonl", "corpus.jsonl", "queries.jsonl")
PROFILES = (
    {
        "source_id": "synthetic:coding:trusted-current", "family": "coding_history",
        "quality": "trusted", "freshness_half_life_days": 180,
    },
    {
        "source_id": "synthetic:coding:trusted-stale", "family": "coding_history",
        "quality": "trusted", "freshness_half_life_days": 180,
    },
    {
        "source_id": "synthetic:capture:authoritative", "family": "deliberate_capture",
        "quality": "authoritative", "freshness_half_life_days": 365,
    },
    {
        "source_id": "synthetic:export:standard", "family": "user_export",
        "quality": "standard", "freshness_half_life_days": 90,
    },
    {
        "source_id": "synthetic:research:unrated", "family": "third_party_research",
        "quality": "unrated", "freshness_half_life_days": 365,
    },
)


def row(case: str, source_id: str, ordinal: int, text: str, occurred_at: str) -> dict:
    native_id = f"{case}-{ordinal}"
    return {
        "case_id": case, "source_id": source_id, "native_id": native_id,
        "occurred_at": occurred_at, "text": text,
        "receipt": f"recall://{source_id}/{native_id}?rev=1#item=0",
    }


def write_jsonl(path: Path, values: list[dict]) -> None:
    path.write_text("".join(json.dumps(value, sort_keys=True) + "\n" for value in values))


def main() -> None:
    output = Path(__file__).with_name("federation_eval_v1")
    output.mkdir(parents=True, exist_ok=True)
    corpus: list[dict] = []
    queries: list[dict] = []

    for index in range(1, 9):
        case = f"quality-{index:02d}"
        marker = f"quality-marker-{index:02d}"
        winner = row(
            case, "synthetic:capture:authoritative", 1,
            f"{marker} host-approved decision", "2026-07-13T12:00:00Z",
        )
        decoy = row(
            case, "synthetic:research:unrated", 2,
            f"{marker} unverified external claim", "2026-07-13T12:00:00Z",
        )
        decoy["claimed_quality"] = "authoritative"
        corpus.extend((decoy, winner))
        queries.append({
            "id": case, "stratum": "source-quality", "query": marker,
            "answers": [winner["receipt"]], "high_quality_answers": [winner["receipt"]],
        })

    for index in range(1, 7):
        case = f"freshness-{index:02d}"
        marker = f"freshness-marker-{index:02d}"
        current = row(
            case, "synthetic:coding:trusted-current", 1,
            f"{marker} current operational state", "2026-07-13T12:00:00Z",
        )
        stale = row(
            case, "synthetic:coding:trusted-stale", 2,
            f"{marker} superseded operational state", "2024-01-13T12:00:00Z",
        )
        corpus.extend((current, stale))
        queries.append({
            "id": case, "stratum": "stale-conflict", "query": marker,
            "answers": [current["receipt"]], "forbidden": [stale["receipt"]],
        })

    for index in range(1, 7):
        case = f"corroboration-{index:02d}"
        marker = f"corroboration-marker-{index:02d}"
        shared = f"{marker} independently confirmed resolution"
        coding = row(
            case, "synthetic:coding:trusted-current", 1, shared,
            "2026-07-13T12:00:00Z",
        )
        exported = row(
            case, "synthetic:export:standard", 2, shared,
            "2026-07-13T12:00:00Z",
        )
        decoy = row(
            case, "synthetic:capture:authoritative", 3,
            f"{marker} single-source contradiction", "2026-07-13T12:00:00Z",
        )
        corpus.extend((decoy, coding, exported))
        queries.append({
            "id": case, "stratum": "corroboration", "query": marker,
            "answers": [coding["receipt"], exported["receipt"]],
            "forbidden": [decoy["receipt"]],
        })

    canaries = []
    for index in range(1, 5):
        case = f"privacy-{index:02d}"
        marker = f"privacy-safe-marker-{index:02d}"
        canary = f"SYNTHETIC_SECRET_CANARY_{index:02d}_DO_NOT_INDEX"
        canaries.append(canary)
        corpus.append(row(
            case, "synthetic:research:unrated", 1,
            f"{marker}\napi_key={canary}\nkeep safe aftermath",
            "2026-07-13T12:00:00Z",
        ))
        queries.append({
            "id": case, "stratum": "privacy-canary", "query": canary,
            "answers": [],
        })

    for index in range(1, 7):
        queries.append({
            "id": f"negative-{index:02d}", "stratum": "no-answer",
            "query": f"nonexistent-federation-answer-{index:02d}", "answers": [],
        })

    corpus_text = "\n".join(value["text"].casefold() for value in corpus)
    contaminated = [
        value["id"] for value in queries
        if value["stratum"] == "no-answer" and value["query"].casefold() in corpus_text
    ]
    if contaminated:
        raise SystemExit(f"negative contamination: {contaminated}")

    write_jsonl(output / "profiles.jsonl", list(PROFILES))
    write_jsonl(output / "corpus.jsonl", corpus)
    write_jsonl(output / "queries.jsonl", queries)
    hashes = {
        name: hashlib.sha256((output / name).read_bytes()).hexdigest()
        for name in FILES
    }
    manifest = {
        "schema_version": 1,
        "freeze_epoch": FREEZE_EPOCH,
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "counts": {
            "source_profiles": len(PROFILES), "source_families": 4,
            "corpus_records": len(corpus), "queries": len(queries),
            "source-quality": 8, "stale-conflict": 6, "corroboration": 6,
            "privacy-canary": 4, "no-answer": 6,
        },
        "sha256": hashes,
        "privacy_canary_sha256": [
            hashlib.sha256(value.encode()).hexdigest() for value in canaries
        ],
        "contamination_guard": "pass",
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(manifest["counts"], sort_keys=True))


if __name__ == "__main__":
    main()
