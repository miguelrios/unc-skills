#!/usr/bin/env python3
"""Exactly-once semantic accounting overlays for Recap event ledgers."""

from __future__ import annotations

import copy
import hashlib
from itertools import pairwise
import json
from pathlib import Path
from typing import Any

from event_ledger import iter_jsonl
from privacy import sanitize_structure


ACCOUNTING_SCHEMA = "recap.accounting.v1"
CLAIM_KINDS = frozenset(
    {
        "goal",
        "decision",
        "action",
        "change",
        "verification",
        "failure",
        "recovery",
        "external_effect",
        "final_state",
        "open_work",
    }
)


def _ranges(group: dict[str, Any]) -> list[tuple[int, int]]:
    values = []
    for value in group.get("ranges", []):
        if (
            not isinstance(value, list) or len(value) != 2
            or not all(isinstance(item, int) for item in value)
            or value[0] < 0 or value[1] < value[0]
        ):
            raise ValueError("low-signal range is invalid")
        values.append((value[0], value[1]))
    return values


def _intervals(groups: list[dict[str, Any]]) -> tuple[list[tuple[int, int, str]], list[str]]:
    intervals = []
    errors = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("group_id"), str):
            continue
        try:
            ranges = _ranges(group)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        for first, last in ranges:
            intervals.append((first, last, group["group_id"]))
    intervals.sort()
    for previous, current in pairwise(intervals):
        if current[0] <= previous[1]:
            errors.append("low-signal ranges overlap")
            break
    return intervals, errors


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def validate_accounting(
    manifest: dict[str, Any],
    accounting: Any,
    *,
    require_sealed: bool = True,
) -> dict[str, Any]:
    errors = []
    if not isinstance(accounting, dict) or accounting.get("schema_version") != ACCOUNTING_SCHEMA:
        return {"valid": False, "errors": ["accounting schema is unsupported"]}
    scrubbed_accounting, _privacy_redactions = sanitize_structure(accounting)
    if scrubbed_accounting != accounting:
        errors.append("accounting contains credential-shaped material")
    event_receipt = manifest.get("ledger", {}).get("events", {})
    if accounting.get("event_ledger_sha256") not in {None, event_receipt.get("sha256")}:
        errors.append("accounting targets a different event ledger")
    claims = accounting.get("claims")
    groups = accounting.get("low_signal_groups")
    if not isinstance(claims, list) or not isinstance(groups, list):
        return {"valid": False, "errors": ["claims and low_signal_groups must be lists"]}

    claim_ids = set()
    event_to_claim = {}
    for claim in claims:
        if not isinstance(claim, dict) or not isinstance(claim.get("claim_id"), str):
            errors.append("claim is invalid")
            continue
        if claim["claim_id"] in claim_ids:
            errors.append("claim IDs are not unique")
        claim_ids.add(claim["claim_id"])
        if claim.get("kind") not in CLAIM_KINDS or not isinstance(claim.get("label"), str):
            errors.append(f"claim {claim['claim_id']} has invalid kind or label")
        evidence = claim.get("event_ids")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"claim {claim['claim_id']} has no evidence")
            continue
        for event_id in evidence:
            if not isinstance(event_id, str):
                errors.append(f"claim {claim['claim_id']} has invalid evidence")
            elif event_id in event_to_claim:
                errors.append("event evidence is duplicated across claims")
            else:
                event_to_claim[event_id] = claim["claim_id"]

    group_ids = set()
    states = {}
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("group_id"), str):
            errors.append("low-signal group is invalid")
            continue
        group_id = group["group_id"]
        if group_id in group_ids:
            errors.append("low-signal group IDs are not unique")
        group_ids.add(group_id)
        if not isinstance(group.get("label"), str):
            errors.append(f"low-signal group {group_id} has no label")
        try:
            ranges = _ranges(group)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not ranges:
            errors.append(f"low-signal group {group_id} has no ranges")
        states[group_id] = {
            "count": 0,
            "expected_count": sum(last - first + 1 for first, last in ranges),
            "digest": hashlib.sha256(),
        }
    intervals, interval_errors = _intervals(groups)
    errors.extend(interval_errors)

    found_claim_events = set()
    interval_index = 0
    unaccounted = 0
    duplicate = 0
    event_count = 0
    event_path = Path(str(event_receipt.get("path", "")))
    if not event_path.is_file():
        errors.append("event ledger is unavailable")
    else:
        for event in iter_jsonl(event_path):
            ordinal = event.get("ordinal")
            event_id = event.get("event_id")
            while interval_index < len(intervals) and intervals[interval_index][1] < ordinal:
                interval_index += 1
            low_group = None
            if interval_index < len(intervals):
                first, last, group_id = intervals[interval_index]
                if first <= ordinal <= last:
                    low_group = group_id
            claim_id = event_to_claim.get(event_id)
            assignments = int(claim_id is not None) + int(low_group is not None)
            if assignments == 0:
                unaccounted += 1
            elif assignments > 1:
                duplicate += 1
            if claim_id:
                found_claim_events.add(event_id)
            if low_group:
                state = states[low_group]
                state["count"] += 1
                state["digest"].update((str(event_id) + "\n").encode())
            event_count += 1
    missing_claim_evidence = len(set(event_to_claim) - found_claim_events)
    if missing_claim_evidence:
        errors.append("claim evidence is absent from the event ledger")
    if unaccounted:
        errors.append(f"{unaccounted} events are unaccounted")
    if duplicate:
        errors.append(f"{duplicate} events are multiply accounted")

    for group_id, state in states.items():
        if state["count"] != state["expected_count"]:
            errors.append(f"low-signal group {group_id} extends beyond the event ledger")

    for group in groups:
        if not isinstance(group, dict) or group.get("group_id") not in states:
            continue
        state = states[group["group_id"]]
        digest = state["digest"].hexdigest()
        if require_sealed and (
            group.get("count") != state["count"] or group.get("event_ids_sha256") != digest
        ):
            errors.append(f"low-signal group {group['group_id']} seal is invalid")
    if require_sealed and accounting.get("event_ledger_sha256") != event_receipt.get("sha256"):
        errors.append("accounting is not sealed to the event ledger")
    return {
        "valid": not errors,
        "errors": errors,
        "event_count": event_count,
        "claim_count": len(claims),
        "low_signal_group_count": len(groups),
        "unaccounted_events": unaccounted,
        "duplicate_assignments": duplicate,
        "missing_claim_evidence": missing_claim_evidence,
    }


def seal_accounting(manifest: dict[str, Any], draft: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    result = validate_accounting(manifest, draft, require_sealed=False)
    if not result["valid"]:
        return draft, result
    sealed = copy.deepcopy(draft)
    event_receipt = manifest["ledger"]["events"]
    sealed["event_ledger_sha256"] = event_receipt["sha256"]
    states = {
        group["group_id"]: {"count": 0, "digest": hashlib.sha256()}
        for group in sealed["low_signal_groups"]
    }
    intervals, errors = _intervals(sealed["low_signal_groups"])
    if errors:
        return draft, {"valid": False, "errors": errors}
    interval_index = 0
    for event in iter_jsonl(Path(event_receipt["path"])):
        ordinal = event["ordinal"]
        while interval_index < len(intervals) and intervals[interval_index][1] < ordinal:
            interval_index += 1
        if interval_index < len(intervals):
            first, last, group_id = intervals[interval_index]
            if first <= ordinal <= last:
                state = states[group_id]
                state["count"] += 1
                state["digest"].update((event["event_id"] + "\n").encode())
    for group in sealed["low_signal_groups"]:
        state = states[group["group_id"]]
        group["count"] = state["count"]
        group["event_ids_sha256"] = state["digest"].hexdigest()
    return sealed, validate_accounting(manifest, sealed, require_sealed=True)


def accounting_receipt(accounting: dict[str, Any], validation: dict[str, Any]) -> dict[str, Any]:
    """Return a content-free receipt safe for terminal output."""
    return {
        "schema_version": "recap.accounting-receipt.v1",
        "accounting_sha256": canonical_sha256(accounting),
        "event_count": validation.get("event_count", 0),
        "claim_count": validation.get("claim_count", 0),
        "low_signal_group_count": validation.get("low_signal_group_count", 0),
        "unaccounted_events": validation.get("unaccounted_events", 0),
        "duplicate_assignments": validation.get("duplicate_assignments", 0),
        "valid": bool(validation.get("valid")),
    }


def score_significant_events(predicted: set[str], gold: set[str]) -> dict[str, Any]:
    true_positive = len(predicted & gold)
    return {
        "true_positive": true_positive,
        "predicted": len(predicted),
        "gold": len(gold),
        "precision": 1.0 if not predicted else true_positive / len(predicted),
        "recall": 1.0 if not gold else true_positive / len(gold),
    }
