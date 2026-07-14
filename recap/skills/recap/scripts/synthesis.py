#!/usr/bin/env python3
"""Validate and render host-agent-authored, evidence-bound Recap drafts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from accounting import canonical_sha256, validate_accounting
from event_ledger import iter_jsonl


SYNTHESIS_SCHEMA = "recap.synthesis-draft.v1"
SOURCE_LABELS = frozenset({"session_observed", "agent_report", "verified_now", "inference"})
NARRATIVE_ROLES = frozenset({"setup", "approach", "turning_point", "outcome", "remaining"})
DETAIL_SECTIONS = (
    "changes",
    "verification",
    "failures_recoveries",
    "final_state",
    "open_work",
)
OUTCOMES = frozenset({"passed", "failed", "unknown", "discussed_only", "unverifiable_now"})
MAX_SUMMARY_CHARS = 1_200
MAX_TITLE_CHARS = 160
MAX_RENDER_WORDS = 2_500
TOP_FIELDS = frozenset({
    "schema_version", "manifest_sha256", "accounting_sha256", "headline", "story",
    "timeline", "changes", "verification", "failures_recoveries", "final_state",
    "open_work", "coverage",
})
BASE_FIELDS = frozenset({
    "id", "summary", "source_label", "accounting_claim_ids", "evidence_ids",
    "git_evidence", "caveat",
})
SECTION_FIELDS = {
    "headline": BASE_FIELDS,
    "story": BASE_FIELDS | {"title", "narrative_role"},
    "timeline": BASE_FIELDS | {"title", "first_ordinal", "last_ordinal"},
    "changes": BASE_FIELDS | {"title", "paths", "commits"},
    "verification": BASE_FIELDS | {
        "title", "outcome", "command_event_id", "result_event_id",
    },
    "failures_recoveries": BASE_FIELDS | {"title"},
    "final_state": BASE_FIELDS | {"title"},
    "open_work": BASE_FIELDS | {"title"},
}
COMMON_REQUIRED = frozenset({
    "id", "summary", "source_label", "accounting_claim_ids", "evidence_ids",
})
SECTION_REQUIRED = {
    "headline": COMMON_REQUIRED,
    "story": COMMON_REQUIRED | {"title", "narrative_role"},
    "timeline": COMMON_REQUIRED | {"title", "first_ordinal", "last_ordinal"},
    "changes": COMMON_REQUIRED | {"title", "paths", "commits"},
    "verification": COMMON_REQUIRED | {
        "title", "outcome", "command_event_id", "result_event_id",
    },
    "failures_recoveries": COMMON_REQUIRED | {"title"},
    "final_state": COMMON_REQUIRED | {"title"},
    "open_work": COMMON_REQUIRED | {"title"},
}


def _strings(value: Any) -> list[str] | None:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        return None
    return value


def _canonical_item_id(item: dict[str, Any]) -> str | None:
    value = item.get("id")
    return value if isinstance(value, str) and value else None


def _event_claim_map(accounting: dict[str, Any]) -> tuple[dict[str, set[str]], dict[str, str]]:
    claim_events = {}
    event_claim = {}
    for claim in accounting.get("claims", []):
        claim_id = claim["claim_id"]
        events = set(claim["event_ids"])
        claim_events[claim_id] = events
        for event_id in events:
            event_claim[event_id] = claim_id
    return claim_events, event_claim


def _all_items(draft: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    items = []
    headline = draft.get("headline")
    if isinstance(headline, dict):
        items.append(("headline", headline))
    for section in ("story", "timeline", *DETAIL_SECTIONS):
        values = draft.get(section)
        if isinstance(values, list):
            items.extend((section, value) for value in values if isinstance(value, dict))
    return items


def _git_snapshots(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    repositories = manifest.get("git", {}).get("verified_now", {}).get("repositories", [])
    return {
        item["repo_root"]: item
        for item in repositories
        if isinstance(item, dict) and isinstance(item.get("repo_root"), str)
    }


def _validate_git_evidence(
    values: Any,
    snapshots: dict[str, dict[str, Any]],
) -> list[str]:
    errors = []
    if not isinstance(values, list) or not values:
        return ["verified_now item has no git_evidence"]
    for value in values:
        if not isinstance(value, dict):
            errors.append("git_evidence entry is invalid")
            continue
        if set(value) != {"repo_root", "kind", "value"}:
            errors.append("git_evidence entry has unsupported fields")
            continue
        snapshot = snapshots.get(value.get("repo_root"))
        kind = value.get("kind")
        expected = None
        valid_kind = True
        if snapshot is None:
            errors.append("git_evidence references an unknown repository")
            continue
        if kind == "head":
            expected = snapshot.get("head")
        elif kind == "branch":
            expected = snapshot.get("branch")
        elif kind == "changed_path":
            if value.get("value") not in snapshot.get("changed_paths", []):
                errors.append("git_evidence references an unknown changed path")
        elif kind == "clean":
            expected = not bool(snapshot.get("changed_paths"))
        elif kind == "available":
            expected = bool(snapshot.get("available"))
        else:
            valid_kind = False
            errors.append("git_evidence kind is unsupported")
        if valid_kind and kind != "changed_path" and value.get("value") != expected:
            errors.append(f"git_evidence {kind} value does not match verified-now state")
    return errors


def _known_changes(manifest: dict[str, Any]) -> tuple[set[str], set[str], set[str], set[str]]:
    observed = manifest.get("git", {}).get("session_observed", {})
    observed_paths = {
        item.get("path") for item in observed.get("file_mutations", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    observed_commits = {
        item.get("sha") for item in observed.get("observed_commits", [])
        if isinstance(item, dict) and isinstance(item.get("sha"), str)
    }
    current_paths = set()
    current_commits = set()
    for snapshot in _git_snapshots(manifest).values():
        current_paths.update(
            value for value in snapshot.get("changed_paths", []) if isinstance(value, str)
        )
        current_commits.update(
            item.get("sha") for item in snapshot.get("commits", [])
            if isinstance(item, dict) and isinstance(item.get("sha"), str)
        )
    return observed_paths, observed_commits, current_paths, current_commits


def _validate_change_items(manifest: dict[str, Any], values: list[Any]) -> list[str]:
    errors = []
    observed_paths, observed_commits, current_paths, current_commits = _known_changes(manifest)
    for item in values:
        if not isinstance(item, dict):
            continue
        paths = item.get("paths", [])
        commits = item.get("commits", [])
        if _strings(paths) is None or _strings(commits) is None:
            errors.append("change paths and commits must be string lists")
            continue
        source = item.get("source_label")
        allowed_paths = {
            "verified_now": current_paths,
            "session_observed": observed_paths,
        }.get(source, observed_paths | current_paths)
        allowed_commits = {
            "verified_now": current_commits,
            "session_observed": observed_commits,
        }.get(source, observed_commits | current_commits)
        if any(path not in allowed_paths for path in paths):
            errors.append("change references a path absent from git evidence")
        if any(commit not in allowed_commits for commit in commits):
            errors.append("change references a commit absent from git evidence")
    return errors


def _test_commands(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    values = manifest.get("git", {}).get("session_observed", {}).get("test_commands", [])
    return {
        item["event_id"]: item
        for item in values
        if isinstance(item, dict) and isinstance(item.get("event_id"), str)
    }


def _validate_verification_items(manifest: dict[str, Any], values: list[Any]) -> list[str]:
    errors = []
    commands = _test_commands(manifest)
    for item in values:
        if not isinstance(item, dict):
            continue
        outcome = item.get("outcome")
        if outcome not in OUTCOMES:
            errors.append("verification outcome is unsupported")
            continue
        command_id = item.get("command_event_id")
        result_id = item.get("result_event_id")
        if command_id is None:
            if outcome not in {"discussed_only", "unverifiable_now"} or result_id is not None:
                errors.append("verification without an observed command cannot claim a run outcome")
            continue
        command = commands.get(command_id)
        if command is None:
            errors.append("verification references a fabricated test command")
            continue
        result = command.get("result")
        expected_outcome = result.get("status") if isinstance(result, dict) else "unknown"
        expected_result_id = result.get("event_id") if isinstance(result, dict) else None
        if outcome != expected_outcome or result_id != expected_result_id:
            errors.append("verification outcome does not match observed test evidence")
    return errors


def validate_synthesis(
    manifest: dict[str, Any],
    accounting: dict[str, Any],
    draft: Any,
) -> dict[str, Any]:
    errors = []
    accounting_validation = validate_accounting(manifest, accounting)
    if not accounting_validation["valid"]:
        return {"valid": False, "errors": ["accounting overlay is invalid"]}
    if not isinstance(draft, dict) or draft.get("schema_version") != SYNTHESIS_SCHEMA:
        return {"valid": False, "errors": ["synthesis schema is unsupported"]}
    if set(draft) != TOP_FIELDS:
        errors.append("synthesis top-level fields are not the closed contract")
    expected_manifest_sha = canonical_sha256(manifest)
    expected_accounting_sha = canonical_sha256(accounting)
    if draft.get("manifest_sha256") != expected_manifest_sha:
        errors.append("synthesis targets a different manifest")
    if draft.get("accounting_sha256") != expected_accounting_sha:
        errors.append("synthesis targets a different accounting overlay")

    headline = draft.get("headline")
    if not isinstance(headline, dict):
        errors.append("headline must be one evidence-bound item")
    for section in ("story", "timeline", *DETAIL_SECTIONS):
        if not isinstance(draft.get(section), list):
            errors.append(f"{section} must be a list")
        elif any(not isinstance(item, dict) for item in draft[section]):
            errors.append(f"{section} contains a non-object item")
    if not draft.get("story") or not draft.get("timeline"):
        errors.append("story and timeline must both be non-empty")

    claim_events, event_claim = _event_claim_map(accounting)
    known_claims = set(claim_events)
    low_group_ids = {
        group["group_id"] for group in accounting.get("low_signal_groups", [])
    }
    coverage = draft.get("coverage")
    if not isinstance(coverage, dict) or set(coverage) != {"low_signal_group_ids"}:
        errors.append("coverage must contain only low_signal_group_ids")
    else:
        values = _strings(coverage.get("low_signal_group_ids"))
        if values is None or len(values) != len(set(values)) or set(values) != low_group_ids:
            errors.append("coverage does not name every low-signal group exactly once")

    all_items = _all_items(draft)
    item_ids = []
    referenced_event_ids = set()
    item_claims: dict[str, list[str]] = {}
    snapshots = _git_snapshots(manifest)
    for section, item in all_items:
        item_id = _canonical_item_id(item)
        if item_id is None:
            errors.append(f"{section} item has no ID")
            continue
        item_ids.append(item_id)
        if set(item) - SECTION_FIELDS[section]:
            errors.append(f"{item_id} has unsupported fields")
        if SECTION_REQUIRED[section] - set(item):
            errors.append(f"{item_id} is missing required fields")
        summary = item.get("summary")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > MAX_SUMMARY_CHARS:
            errors.append(f"{item_id} has an invalid summary")
        if section != "headline":
            title = item.get("title")
            if not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE_CHARS:
                errors.append(f"{item_id} has an invalid title")
        source = item.get("source_label")
        if source not in SOURCE_LABELS:
            errors.append(f"{item_id} has an unsupported source label")
        claim_ids = _strings(item.get("accounting_claim_ids"))
        event_ids = _strings(item.get("evidence_ids"))
        if claim_ids is None or event_ids is None:
            errors.append(f"{item_id} has invalid evidence lists")
            continue
        if len(claim_ids) != len(set(claim_ids)) or len(event_ids) != len(set(event_ids)):
            errors.append(f"{item_id} repeats claim or event evidence")
        if any(claim_id not in known_claims for claim_id in claim_ids):
            errors.append(f"{item_id} references an unknown accounting claim")
        allowed_events = set().union(*(claim_events.get(value, set()) for value in claim_ids))
        if any(event_id not in allowed_events for event_id in event_ids):
            errors.append(f"{item_id} has unsupported event evidence")
        if source != "verified_now" and (not claim_ids or not event_ids):
            errors.append(f"{item_id} has no session evidence")
        if source == "verified_now":
            errors.extend(_validate_git_evidence(item.get("git_evidence"), snapshots))
        elif item.get("git_evidence") not in (None, []):
            errors.append(f"{item_id} attaches current git evidence to a historical source")
        if source == "inference" and (
            not isinstance(item.get("caveat"), str) or not item["caveat"].strip()
        ):
            errors.append(f"{item_id} inference has no caveat")
        item_claims[item_id] = claim_ids
        referenced_event_ids.update(event_ids)
    if len(item_ids) != len(set(item_ids)):
        errors.append("synthesis item IDs are not unique")

    event_details = {}
    event_path = Path(manifest.get("ledger", {}).get("events", {}).get("path", ""))
    if event_path.is_file() and referenced_event_ids:
        for event in iter_jsonl(event_path):
            if event.get("event_id") in referenced_event_ids:
                event_details[event["event_id"]] = event
                if len(event_details) == len(referenced_event_ids):
                    break
    if set(event_details) != referenced_event_ids:
        errors.append("synthesis references event evidence absent from the ledger")
    for section, item in all_items:
        if item.get("source_label") == "agent_report" and any(
            event_details.get(event_id, {}).get("surface") != "assistant"
            for event_id in item.get("evidence_ids", [])
        ):
            errors.append(f"{item.get('id')} agent_report is not supported by assistant evidence")

    story_claims = []
    story_partitions = []
    for item in draft.get("story", []) if isinstance(draft.get("story"), list) else []:
        if not isinstance(item, dict):
            continue
        claims = item_claims.get(str(item.get("id")), [])
        expected_events = set().union(*(claim_events.get(value, set()) for value in claims))
        if set(item.get("evidence_ids", [])) != expected_events:
            errors.append(f"{item.get('id')} does not carry all evidence for its story claims")
        story_claims.extend(claims)
        story_partitions.append(frozenset(expected_events))
    if len(story_claims) != len(set(story_claims)):
        errors.append("story repeats an accounting claim")
    if set(story_claims) != known_claims:
        errors.append("story does not cover every accounting claim exactly once")

    timeline_claims = set()
    timeline_events = []
    timeline_partitions = []
    for item in draft.get("timeline", []) if isinstance(draft.get("timeline"), list) else []:
        if not isinstance(item, dict):
            continue
        claims = item_claims.get(str(item.get("id")), [])
        evidence = item.get("evidence_ids", [])
        timeline_claims.update(claims)
        timeline_events.extend(evidence)
        timeline_partitions.append(frozenset(evidence))
    if len(timeline_events) != len(set(timeline_events)):
        errors.append("timeline repeats significant event evidence")
    if set(timeline_events) != set(event_claim):
        errors.append("timeline does not cover every significant event exactly once")
    if timeline_claims != known_claims:
        errors.append("timeline does not reference every accounting claim")

    normalized_story = sorted(tuple(sorted(value)) for value in story_partitions)
    normalized_timeline = sorted(tuple(sorted(value)) for value in timeline_partitions)
    if len(event_claim) > 1 and normalized_story == normalized_timeline:
        errors.append("story and timeline use the same grouping instead of different views")

    previous_last = -1
    for item in draft.get("timeline", []) if isinstance(draft.get("timeline"), list) else []:
        if not isinstance(item, dict):
            continue
        evidence = item.get("evidence_ids", [])
        ordinals = [
            event_details[event_id]["ordinal"] for event_id in evidence if event_id in event_details
        ]
        if not ordinals:
            errors.append(f"{item.get('id')} timeline item has no ordinal evidence")
            continue
        first, last = min(ordinals), max(ordinals)
        if item.get("first_ordinal") != first or item.get("last_ordinal") != last:
            errors.append(f"{item.get('id')} timeline ordinal bounds are incorrect")
        if first <= previous_last:
            errors.append("timeline items overlap or are not chronological")
        previous_last = last

    for item in draft.get("story", []) if isinstance(draft.get("story"), list) else []:
        if isinstance(item, dict) and item.get("narrative_role") not in NARRATIVE_ROLES:
            errors.append(f"{item.get('id')} has an unsupported narrative role")

    errors.extend(_validate_change_items(
        manifest, draft.get("changes", []) if isinstance(draft.get("changes"), list) else [],
    ))
    errors.extend(_validate_verification_items(
        manifest,
        draft.get("verification", []) if isinstance(draft.get("verification"), list) else [],
    ))
    return {
        "valid": not errors,
        "errors": errors,
        "item_count": len(all_items),
        "claim_count": len(known_claims),
        "low_signal_group_count": len(low_group_ids),
        "draft_sha256": canonical_sha256(draft),
    }


def _source_tag(item: dict[str, Any]) -> str:
    claims = ", ".join(item.get("accounting_claim_ids", [])) or "current-git"
    return f"[{item.get('source_label')}; {claims}]"


def _render_summary(item: dict[str, Any]) -> str:
    summary = item["summary"]
    if item.get("source_label") == "inference":
        summary += f" Caveat: {item['caveat']}"
    return summary


def render_markdown(
    manifest: dict[str, Any],
    accounting: dict[str, Any],
    draft: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    validation = validate_synthesis(manifest, accounting, draft)
    if not validation["valid"]:
        raise ValueError("synthesis draft is invalid")
    scope = manifest["scope"]
    coverage = manifest["coverage"]
    lines = [
        "# Session recap",
        "",
        (
            f"Scope: {scope.get('harness')} native session; "
            f"{coverage.get('observed_events')} observed events; "
            f"source_complete={str(bool(coverage.get('source_complete'))).lower()}."
        ),
        "",
        f"**{_render_summary(draft['headline'])}** {_source_tag(draft['headline'])}",
        "",
        "## Story",
        "",
    ]
    for item in draft["story"]:
        lines.extend([
            f"### {item['title']}", "", f"{_render_summary(item)} {_source_tag(item)}", "",
        ])
    lines.extend(["## Timeline", ""])
    for item in draft["timeline"]:
        lines.append(
            f"- **{item['first_ordinal']}–{item['last_ordinal']}: {item['title']}** — "
            f"{_render_summary(item)} {_source_tag(item)}"
        )
    labels = {
        "changes": "What changed",
        "verification": "Verification",
        "failures_recoveries": "Failures and recoveries",
        "final_state": "Final state",
        "open_work": "Open work",
    }
    for section in DETAIL_SECTIONS:
        lines.extend(["", f"## {labels[section]}", ""])
        values = draft[section]
        if not values:
            lines.append("- None supported by the selected session evidence.")
        for item in values:
            suffix = f" Outcome: {item['outcome']}." if section == "verification" else ""
            lines.append(
                f"- **{item['title']}** — {_render_summary(item)}{suffix} {_source_tag(item)}"
            )
    groups = {group["group_id"]: group for group in accounting["low_signal_groups"]}
    lines.extend(["", "## Coverage", ""])
    for group_id in draft["coverage"]["low_signal_group_ids"]:
        group = groups[group_id]
        lines.append(f"- {group['label']}: {group['count']} events (`{group_id}`).")
    lines.append("")
    rendered = "\n".join(lines)
    words = len(rendered.split())
    if words > MAX_RENDER_WORDS:
        raise ValueError("rendered recap exceeds the 2,500-word limit")
    receipt = {
        "schema_version": "recap.render-receipt.v1",
        "render_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "word_count": words,
        "item_count": validation["item_count"],
        "claim_count": validation["claim_count"],
        "valid": True,
    }
    return rendered, receipt
