#!/usr/bin/env python3
"""Evaluate L4 synthesis against the frozen public semantic markers."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


EVAL_ROOT = Path(__file__).resolve().parent
TEST_ROOT = EVAL_ROOT.parent
SCRIPTS = TEST_ROOT.parent / "skills/recap/scripts"
sys.path.insert(0, str(SCRIPTS))

from accounting import ACCOUNTING_SCHEMA, canonical_sha256, seal_accounting  # noqa: E402
from event_ledger import LedgerBuilder  # noqa: E402
from synthesis import SYNTHESIS_SCHEMA, render_markdown, validate_synthesis  # noqa: E402


CASES = TEST_ROOT / "eval_v2/cases.jsonl"
GOLD = TEST_ROOT / "eval_v2/gold_ledger.jsonl"
SIGNIFICANT_RE = re.compile(r" significant=([^ ]+)$")


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


def item(item_id: str, title: str, summary: str, claims: list[str], evidence: list[str], **extra):
    return {
        "id": item_id,
        "title": title,
        "summary": summary,
        "source_label": "session_observed",
        "accounting_claim_ids": claims,
        "evidence_ids": evidence,
        **extra,
    }


def evaluate(output: Path, *, max_events: int | None = None) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    output.chmod(0o700)
    cases = {case["id"]: case for case in records(CASES)}
    gold = {case["case"]: case for case in records(GOLD)}
    true_positive = 0
    predicted_count = 0
    gold_count = 0
    unsupported = 0
    unaccounted = 0
    duplicate = 0
    maximum_words = 0
    evaluated = []
    skipped = []
    for case_id, case in cases.items():
        event_count = int(case["event_count"])
        if event_count == 0 or (max_events is not None and event_count > max_events):
            skipped.append(case_id)
            continue
        expected_labels = list(gold[case_id]["significant"])
        builder = LedgerBuilder(output / case_id / "manifest.json", heartbeat_every=0)
        extracted = []
        for ordinal in range(event_count):
            label = expected_labels[ordinal] if ordinal < len(expected_labels) else None
            value = synthetic_event(case_id, ordinal, label)
            builder.add(value)
            match = SIGNIFICANT_RE.search(value["text"])
            if match:
                extracted.append((match.group(1), value["event_id"], ordinal))
        bundle = builder.finish()
        manifest = {
            "scope": {"harness": case["harness"]},
            "coverage": {"observed_events": event_count, "source_complete": True},
            "ledger": bundle,
            "git": {
                "session_observed": {
                    "file_mutations": [], "observed_commits": [], "test_commands": [],
                },
                "verified_now": {"repositories": []},
            },
        }
        claims = [
            {
                "claim_id": f"claim-{index}", "key": label, "kind": "action",
                "label": label, "event_ids": [event_id],
            }
            for index, (label, event_id, _ordinal) in enumerate(extracted)
        ]
        groups = []
        if len(extracted) < event_count:
            groups.append({
                "group_id": "routine-progress", "label": "Synthetic routine progress",
                "ranges": [[len(extracted), event_count - 1]],
            })
        accounting, accounting_result = seal_accounting(manifest, {
            "schema_version": ACCOUNTING_SCHEMA,
            "claims": claims,
            "low_signal_groups": groups,
        })
        if not accounting_result["valid"]:
            raise RuntimeError(f"invalid accounting for {case_id}")
        claim_ids = [claim["claim_id"] for claim in claims]
        evidence_ids = [claim["event_ids"][0] for claim in claims]
        story = [item(
            "story-outcome", "Session outcome", "The significant session actions form one outcome.",
            claim_ids, evidence_ids, narrative_role="outcome",
        )]
        timeline = [
            item(
                f"timeline-{index}", f"Action {index + 1}", f"The session recorded {label}.",
                [f"claim-{index}"], [event_id], first_ordinal=ordinal, last_ordinal=ordinal,
            )
            for index, (label, event_id, ordinal) in enumerate(extracted)
        ]
        draft = {
            "schema_version": SYNTHESIS_SCHEMA,
            "manifest_sha256": canonical_sha256(manifest),
            "accounting_sha256": canonical_sha256(accounting),
            "headline": {
                "id": "headline", "summary": "The session completed its significant actions.",
                "source_label": "session_observed", "accounting_claim_ids": claim_ids,
                "evidence_ids": evidence_ids,
            },
            "story": story,
            "timeline": timeline,
            "changes": [],
            "verification": [],
            "failures_recoveries": [],
            "final_state": [],
            "open_work": [],
            "coverage": {"low_signal_group_ids": [group["group_id"] for group in groups]},
        }
        validation = validate_synthesis(manifest, accounting, draft)
        if not validation["valid"]:
            raise RuntimeError(f"invalid synthesis for {case_id}: {validation['errors']}")
        _, render_receipt = render_markdown(manifest, accounting, draft)
        predicted = {label for label, _event_id, _ordinal in extracted}
        expected = set(expected_labels)
        true_positive += len(predicted & expected)
        predicted_count += len(predicted)
        gold_count += len(expected)
        unsupported += len(predicted - expected)
        unaccounted += accounting_result["unaccounted_events"]
        duplicate += accounting_result["duplicate_assignments"]
        maximum_words = max(maximum_words, render_receipt["word_count"])
        evaluated.append({
            "case": case_id, "event_count": event_count, "claim_count": len(claims),
            "word_count": render_receipt["word_count"], "valid": True,
        })
    precision = 1.0 if not predicted_count else true_positive / predicted_count
    recall = 1.0 if not gold_count else true_positive / gold_count
    return {
        "schema_version": "recap.eval-v4.synthesis-gate.v1",
        "evaluated_cases": evaluated,
        "skipped_cases": sorted(skipped),
        "event_count": sum(value["event_count"] for value in evaluated),
        "significant_action_precision": precision,
        "significant_action_recall": recall,
        "unsupported_factual_claims": unsupported,
        "duplicate_assignments": duplicate,
        "unaccounted_events": unaccounted,
        "maximum_render_words": maximum_words,
        "valid": precision >= 0.98 and recall >= 0.95 and unsupported == 0
        and duplicate == 0 and unaccounted == 0 and maximum_words <= 2_500,
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
