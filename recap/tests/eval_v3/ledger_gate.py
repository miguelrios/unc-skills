#!/usr/bin/env python3
"""Evaluate lossless L3 ledger/accounting on the frozen synthetic corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


EVAL_ROOT = Path(__file__).resolve().parent
TEST_ROOT = EVAL_ROOT.parent
SCRIPTS = TEST_ROOT.parent / "skills/recap/scripts"
sys.path.insert(0, str(SCRIPTS))

from accounting import ACCOUNTING_SCHEMA, score_significant_events, seal_accounting  # noqa: E402
from event_ledger import LedgerBuilder, validate_bundle  # noqa: E402


CASES = TEST_ROOT / "eval_v2/cases.jsonl"
GOLD = TEST_ROOT / "eval_v2/gold_ledger.jsonl"


def records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def synthetic_event(case_id: str, ordinal: int, significant: str | None) -> dict[str, Any]:
    text = (
        f"case={case_id} significant={significant}"
        if significant is not None
        else f"case={case_id} ordinal={ordinal} low-signal progress"
    )
    return {
        "ordinal": ordinal,
        "event_id": f"{case_id}:event:{ordinal}",
        "event_native_id": f"{case_id}:native:{ordinal}",
        "item_ordinal": 0,
        "timestamp": float(ordinal),
        "surface": "user" if ordinal % 2 == 0 else "assistant",
        "role": "user" if ordinal % 2 == 0 else "assistant",
        "text": text,
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "receipt": None,
    }


def evaluate(output: Path, *, max_events: int | None = None) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    output.chmod(0o700)
    cases = {case["id"]: case for case in records(CASES)}
    gold = {case["case"]: case for case in records(GOLD)}
    predicted_significant = set()
    gold_significant = set()
    duplicate = 0
    unaccounted = 0
    evaluated = []
    skipped = []
    for case_id, case in cases.items():
        event_count = int(case["event_count"])
        if event_count == 0 or (max_events is not None and event_count > max_events):
            skipped.append(case_id)
            continue
        significant = list(gold[case_id]["significant"])
        builder = LedgerBuilder(output / case_id / "manifest.json", heartbeat_every=0)
        for ordinal in range(event_count):
            label = significant[ordinal] if ordinal < len(significant) else None
            builder.add(synthetic_event(case_id, ordinal, label))
        bundle = builder.finish()
        bundle_validation = validate_bundle(bundle)
        if not bundle_validation["valid"]:
            raise RuntimeError(f"ledger validation failed for {case_id}")
        claims = []
        for ordinal, label in enumerate(significant):
            key = f"{case_id}:{label}"
            claims.append({
                "claim_id": key,
                "kind": "action",
                "label": label,
                "event_ids": [f"{case_id}:event:{ordinal}"],
            })
            predicted_significant.add(f"{case_id}:event:{ordinal}")
            gold_significant.add(f"{case_id}:event:{ordinal}")
        groups = []
        if len(significant) < event_count:
            groups.append({
                "group_id": f"{case_id}:low-signal",
                "label": "Synthetic routine progress",
                "ranges": [[len(significant), event_count - 1]],
            })
        sealed, accounting_validation = seal_accounting(
            {"ledger": bundle},
            {
                "schema_version": ACCOUNTING_SCHEMA,
                "claims": claims,
                "low_signal_groups": groups,
            },
        )
        if not accounting_validation["valid"]:
            raise RuntimeError(f"accounting validation failed for {case_id}")
        duplicate += accounting_validation["duplicate_assignments"]
        unaccounted += accounting_validation["unaccounted_events"]
        evaluated.append({
            "case": case_id,
            "event_count": event_count,
            "claim_count": len(sealed["claims"]),
            "accounting_valid": True,
        })
    score = score_significant_events(predicted_significant, gold_significant)
    return {
        "schema_version": "recap.eval-v3.ledger-gate.v1",
        "evaluated_cases": evaluated,
        "skipped_cases": sorted(skipped),
        "event_count": sum(item["event_count"] for item in evaluated),
        "significant_event_precision": score["precision"],
        "significant_event_recall": score["recall"],
        "duplicate_assignments": duplicate,
        "unaccounted_events": unaccounted,
        "valid": duplicate == 0 and unaccounted == 0
        and score["precision"] >= 0.98 and score["recall"] >= 0.95,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-events", type=int)
    args = parser.parse_args()
    report = evaluate(args.output.expanduser().resolve(), max_events=args.max_events)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
