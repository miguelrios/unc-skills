#!/usr/bin/env python3
"""Collect and validate a private, evidence-addressed coding-session manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from accounting import accounting_receipt, seal_accounting, validate_accounting
from event_ledger import (
    LedgerBuilder,
    LedgerError,
    chunked_events,
    iter_jsonl,
    packet_events,
    validate_bundle,
)
from git_provenance import (
    collect_git_provenance_chunks,
    git_referenced_event_ids,
    validate_git_provenance,
)
from synthesis import render_markdown, validate_synthesis
from privacy import PRIVACY_POLICY_VERSION, sanitize, sanitize_structure


SCHEMA_VERSION = "recap.manifest.v0.4"
BOUNDARY_SET_SCHEMA_VERSION = "recap.boundary-set.v1"
UUID_RE = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


class RecapError(RuntimeError):
    pass


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def changed_leaf_count(before: Any, after: Any) -> int:
    """Count values changed by a final defense scrub without recounting existing markers."""
    if isinstance(before, dict) and isinstance(after, dict):
        return sum(changed_leaf_count(value, after.get(key)) for key, value in before.items())
    if isinstance(before, list) and isinstance(after, list):
        return sum(changed_leaf_count(left, right) for left, right in zip(before, after, strict=True))
    return int(before != after)


def session_id_from_path(path: Path) -> str:
    matches = UUID_RE.findall(path.name)
    return matches[-1].lower() if matches else "path-" + sha256_bytes(str(path).encode())[:16]


def harness_for(path: Path) -> str:
    return "codex" if path.name.startswith("rollout-") else "claude"


def find_exact(root: Path, needle: str, codex: bool) -> Path:
    if not root.exists():
        raise RecapError(f"session root does not exist: {root}")
    pattern = f"*{needle}*.jsonl"
    matches = [path for path in root.rglob(pattern) if path.is_file()]
    if codex:
        matches = [path for path in matches if path.name.startswith("rollout-")]
    if len(matches) != 1:
        raise RecapError(f"exact session identity resolved to {len(matches)} files")
    return matches[0]


def resolve_current() -> Path:
    thread = os.environ.get("CODEX_THREAD_ID")
    session = os.environ.get("CLAUDE_SESSION_ID")
    if thread and session:
        raise RecapError("current harness identity is ambiguous; pass --session explicitly")
    if thread:
        root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "sessions"
        return find_exact(root, thread, codex=True)
    if session:
        root = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"
        return find_exact(root, session, codex=False)
    raise RecapError("current session identity unavailable; pass --session explicitly")


def recall_candidates(explicit: str | None) -> list[Path]:
    candidates = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    if os.environ.get("RECALL_SCRIPT"):
        candidates.append(Path(os.environ["RECALL_SCRIPT"]).expanduser())
    repo_root = Path(__file__).resolve().parents[4]
    candidates.extend([
        repo_root / "recall/skills/recall/scripts/recall.py",
        Path.home() / ".claude/skills/recall/scripts/recall.py",
        Path.home() / ".codex/skills/recall/scripts/recall.py",
        Path.home() / ".agents/skills/recall/scripts/recall.py",
    ])
    return candidates


def find_recall_script(explicit: str | None) -> Path:
    for candidate in recall_candidates(explicit):
        if candidate.is_file():
            return candidate
    raise RecapError("Recall session exporter not found; install Recall or pass --recall-script")


def recall_session_pages(script: Path, *, current: bool, session: str | None):
    cursor = None
    session_metadata = None
    expected_sequence = 0
    for _page_number in range(1_000_000):
        command = [sys.executable, str(script), "session-export", "--limit", "1000"]
        if cursor:
            command.extend(["--cursor", cursor])
        elif current:
            command.append("--current")
        else:
            command.extend(["--target", str(session)])
        result = subprocess.run(
            command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=60, check=False,
        )
        if result.returncode != 0:
            raise RecapError("Recall session export failed closed")
        try:
            page = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RecapError("Recall session export returned invalid JSON") from exc
        if page.get("schema_version") != "recall.session-export.v1":
            raise RecapError("Recall session export schema is unsupported")
        if session_metadata is None:
            session_metadata = dict(page.get("session") or {})
        elif page.get("session", {}).get("boundary_receipt") != session_metadata.get("boundary_receipt"):
            raise RecapError("Recall session boundary changed between pages")
        page_session = page.get("session", {})
        if "source_snapshot_stable" in page_session:
            session_metadata["source_snapshot_stable"] = bool(
                session_metadata.get("source_snapshot_stable", True)
                and page_session["source_snapshot_stable"]
            )
        if "source_partial_record" in page_session:
            session_metadata["source_partial_record"] = bool(
                session_metadata.get("source_partial_record", False)
                or page_session["source_partial_record"]
            )
        items = page.get("items")
        if not isinstance(items, list):
            raise RecapError("Recall session export page has no items")
        sequences = [item.get("sequence") for item in items]
        if sequences != list(range(expected_sequence, expected_sequence + len(items))):
            raise RecapError("Recall session export sequence is discontinuous")
        expected_sequence += len(items)
        yield page
        if page.get("page", {}).get("complete"):
            return
        cursor = page.get("page", {}).get("next_cursor")
        if not isinstance(cursor, str) or not cursor:
            raise RecapError("incomplete Recall page has no cursor")
    raise RecapError("Recall session export exceeded the page safety bound")


def recall_session_relations(
    script: Path, *, current: bool, session: str | None,
    include_children: bool, chain: bool,
) -> dict[str, Any]:
    command = [sys.executable, str(script), "session-relations"]
    command.append("--current" if current else "--target")
    if not current:
        command.append(str(session))
    if include_children:
        command.append("--include-children")
    if chain:
        command.append("--chain")
    result = subprocess.run(
        command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=60, check=False,
    )
    try:
        graph = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RecapError("Recall session relationship discovery returned invalid JSON") from exc
    if graph.get("schema_version") != "recall.session-relations.v1":
        raise RecapError("Recall session relationship schema is unsupported")
    if result.returncode != 0 or not graph.get("graph_complete"):
        raise RecapError("Recall could not prove a complete requested session relationship graph")
    if not isinstance(graph.get("nodes"), list) or not graph["nodes"]:
        raise RecapError("Recall session relationship graph has no nodes")
    return graph


def private_write(path: Path, value: dict[str, Any]) -> None:
    path = path.expanduser()
    if path.is_symlink():
        raise RecapError("refusing to write through a symlink")
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.stat().st_mode & 0o077:
        raise RecapError("manifest directory must have mode 0700")
    temporary = path.with_name("." + path.name + ".tmp-" + str(os.getpid()))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(temporary, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(value, output, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            output.write("\n")
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def private_write_text(path: Path, value: str) -> None:
    path = path.expanduser()
    if path.is_symlink():
        raise RecapError("refusing to write through a symlink")
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.stat().st_mode & 0o077:
        raise RecapError("output directory must have mode 0700")
    temporary = path.with_name("." + path.name + ".tmp-" + str(os.getpid()))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(value)
            if not value.endswith("\n"):
                output.write("\n")
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def private_output_path(path_value: str, *, label: str) -> Path:
    safe_value, redactions = sanitize(str(path_value))
    if redactions or safe_value != str(path_value):
        raise RecapError(f"{label} contains credential-shaped material")
    path = Path(path_value).expanduser()
    absolute = path if path.is_absolute() else Path.cwd() / path
    for candidate in (absolute, *absolute.parents):
        if candidate.is_symlink():
            raise RecapError(f"{label} must not traverse a symlink")
    resolved = path.resolve()
    safe_resolved, resolved_redactions = sanitize(str(resolved))
    if resolved_redactions or safe_resolved != str(resolved):
        raise RecapError(f"{label} resolves through credential-shaped material")
    return resolved


def session_target(value: str, *, allow_remote_receipt: bool = True) -> str:
    """Preserve central Recall receipts; normalize only native filesystem paths."""
    raw = str(value)
    safe, redactions = sanitize(raw)
    if redactions or safe != raw:
        raise RecapError("session target contains credential-shaped material")
    if raw.startswith("recall://"):
        if not allow_remote_receipt:
            raise RecapError("remote Recall receipts do not expose the native relationship graph")
        return raw
    return str(Path(raw).expanduser().resolve())


def collect(args: argparse.Namespace) -> dict[str, Any]:
    recall_path = find_recall_script(args.recall_script)
    normalized_target = None if args.current else session_target(args.session)
    output_path = private_output_path(args.output, label="manifest output")
    builder = LedgerBuilder(output_path)
    session = None
    page_count = 0
    page_receipt_chain = hashlib.sha256()
    first_page_receipt = None
    last_page_receipt = None
    final_page = None
    try:
        for page in recall_session_pages(recall_path, current=args.current, session=normalized_target):
            page_count += 1
            page_receipt = page["page"].get("page_receipt")
            page_receipt_chain.update(
                json.dumps(page_receipt, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            )
            first_page_receipt = first_page_receipt or page_receipt
            last_page_receipt = page_receipt
            page_session = page.get("session") or {}
            if session is None:
                session = dict(page_session)
                if not isinstance(session.get("native_session_id"), str):
                    raise RecapError("Recall session export omitted exact native identity")
                if not isinstance(session.get("source_id"), str):
                    raise RecapError("Recall session export omitted exact source identity")
                if session.get("harness") not in {"claude", "codex"}:
                    raise RecapError("Recall session export named an unsupported harness")
                for identity_value in (session["native_session_id"], session["source_id"]):
                    safe_identity, identity_redactions = sanitize(identity_value)
                    if identity_redactions or safe_identity != identity_value:
                        raise RecapError("Recall session export contained unsafe identity metadata")
            else:
                if "source_snapshot_stable" in page_session:
                    session["source_snapshot_stable"] = bool(
                        session.get("source_snapshot_stable", True)
                        and page_session["source_snapshot_stable"]
                    )
                if "source_partial_record" in page_session:
                    session["source_partial_record"] = bool(
                        session.get("source_partial_record", False)
                        or page_session["source_partial_record"]
                    )
            for item in page["items"]:
                safe_text, redactions = sanitize(str(item.get("text", "")))
                if sha256_bytes(safe_text.encode()) != item.get("text_sha256"):
                    raise RecapError("Recall evidence digest changed after redaction")
                event = {
                    "ordinal": item["sequence"],
                    "event_id": item["evidence_id"],
                    "event_native_id": item["event_native_id"],
                    "item_ordinal": item["item_ordinal"],
                    "timestamp": item.get("occurred_at"),
                    "surface": item.get("surface"),
                    "role": item.get("role"),
                    "text": safe_text,
                    "text_sha256": item["text_sha256"],
                    "receipt": item.get("receipt"),
                    "_redactions": redactions,
                }
                if isinstance(item.get("entities"), list) and item["entities"]:
                    safe_entities, entity_redactions = sanitize_structure(item["entities"])
                    event["entities"] = safe_entities
                    event["_redactions"] += entity_redactions
                if item.get("possibly_truncated"):
                    event["possibly_truncated"] = True
                safe_event, _event_redactions = sanitize_structure(event)
                for identity_field in ("event_id", "event_native_id", "text_sha256"):
                    if safe_event.get(identity_field) != event.get(identity_field):
                        raise RecapError("Recall event contained unsafe identity metadata")
                safe_event["_redactions"] += changed_leaf_count(event, safe_event)
                builder.add(safe_event)
            final_page = page["page"]
        ledger = builder.finish()
    except Exception:
        builder.abort()
        raise
    if session is None or final_page is None:
        raise RecapError("Recall session export returned no page")
    native_id = session.get("native_session_id")
    source_id = session.get("source_id")
    if not isinstance(native_id, str) or not isinstance(source_id, str):
        raise RecapError("Recall session export omitted exact identity")
    ledger_validation = validate_bundle(
        ledger, source_id=source_id, native_session_id=native_id,
    )
    if not ledger_validation["valid"]:
        raise RecapError("streaming event ledger failed validation")
    metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}
    explicit_repositories = (
        [str(args.repo)] if isinstance(args.repo, (str, Path)) else list(args.repo or [])
    )
    git_provenance, git_redactions = sanitize_structure(collect_git_provenance_chunks(
        chunked_events(Path(ledger["events"]["path"])), metadata, explicit_repositories,
    ))
    safe_metadata, metadata_redactions = sanitize_structure(metadata)
    safe_target, target_redactions = sanitize(str(normalized_target or ""))
    safe_recall_path, recall_path_redactions = sanitize(str(recall_path))
    redaction_count = (
        ledger["redacted_lines"] + git_redactions + metadata_redactions
        + target_redactions + recall_path_redactions
    )
    stable_value = session.get("source_snapshot_stable")
    source_stable = stable_value if isinstance(stable_value, bool) else None
    source_partial = bool(session.get("source_partial_record", False))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "scope": {
            "source_id": source_id,
            "harness": session.get("harness"),
            "native_session_id": native_id,
            "session_path": safe_metadata.get("original_path") or safe_target or None,
            "boundary_receipt": session.get("boundary_receipt"),
            "source_snapshot_stable": source_stable,
            "source_partial_record": source_partial,
            "children_included": False,
            "continuations_included": False,
        },
        "session_metadata": safe_metadata,
        "collector": {
            "recap_version": SCHEMA_VERSION,
            "recall_session_export": safe_recall_path,
            "recall_projector_version": session.get("projector_version"),
            "recall_privacy_policy_version": session.get("privacy_policy_version"),
            "recap_privacy_policy_version": PRIVACY_POLICY_VERSION,
            "page_count": page_count,
            "page_receipt_chain_sha256": page_receipt_chain.hexdigest(),
            "first_page_receipt": first_page_receipt,
            "last_page_receipt": last_page_receipt,
            "packet_cache_policy": "content_addressed_complete_prefix_tail_invalidated",
        },
        "coverage": {
            "first_ordinal": 0 if ledger["event_count"] else None,
            "last_ordinal": ledger["event_count"] - 1 if ledger["event_count"] else None,
            "observed_events": ledger["event_count"],
            "manifest_events": ledger["event_count"],
            "redacted_lines": redaction_count,
            "export_complete": bool(final_page.get("complete")),
            "source_complete": bool(final_page.get("complete")) and source_stable is not False,
            "structural_unaccounted_events": 0,
            "duplicate_event_ids": 0,
            "semantic_accounting": "packetized_not_classified",
            "packet_count": ledger["packets"]["records"],
            "episode_count": ledger["episodes"]["records"],
            "repeat_group_count": ledger["repeat_groups"]["records"],
        },
        "ledger": ledger,
        "git": git_provenance,
    }
    safe_manifest, manifest_redactions = sanitize_structure(manifest)
    if manifest_redactions and safe_manifest != manifest:
        safe_manifest["coverage"]["redacted_lines"] += changed_leaf_count(manifest, safe_manifest)
    return safe_manifest


def validate_manifest(value: dict[str, Any]) -> dict[str, Any]:
    errors = []
    if value.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported schema_version")
    native_id = value.get("scope", {}).get("native_session_id")
    source_id = value.get("scope", {}).get("source_id")
    ledger_validation = validate_bundle(
        value.get("ledger"), source_id=source_id, native_session_id=native_id,
    )
    errors.extend(ledger_validation["errors"])
    event_count = ledger_validation.get("event_count", 0)
    coverage = value.get("coverage", {})
    if coverage.get("observed_events") != event_count or coverage.get("manifest_events") != event_count:
        errors.append("coverage counts do not match events")
    errors.extend(validate_git_provenance(value.get("git"), None))
    unresolved_git_ids = git_referenced_event_ids(value.get("git"))
    event_path = Path(value.get("ledger", {}).get("events", {}).get("path", ""))
    if event_path.is_file() and unresolved_git_ids:
        for event in iter_jsonl(event_path):
            unresolved_git_ids.discard(event.get("event_id"))
            if not unresolved_git_ids:
                break
    if unresolved_git_ids:
        errors.append("git provenance references unknown event evidence")
    return {
        "valid": not errors,
        "errors": errors,
        "event_count": event_count,
        "manifest_sha256": sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()),
        "source_complete": bool(coverage.get("source_complete")),
        "export_complete": bool(coverage.get("export_complete")),
    }


def content_free_receipt(
    manifest: dict[str, Any], output: Path, duration_ms: float,
) -> dict[str, Any]:
    validation = validate_manifest(manifest)
    return {
        "schema_version": "recap.receipt.v0.3",
        "manifest_sha256": validation["manifest_sha256"],
        "session_boundary_sha256": sha256_bytes(json.dumps(manifest["scope"], sort_keys=True).encode()),
        "harness": manifest["scope"]["harness"],
        "event_count": validation["event_count"],
        "redacted_lines": manifest["coverage"]["redacted_lines"],
        "source_complete": validation["source_complete"],
        "export_complete": validation["export_complete"],
        "valid": validation["valid"],
        "duration_ms": duration_ms,
        "output_mode": oct(output.stat().st_mode & 0o777),
    }


def command_collect(args: argparse.Namespace) -> int:
    started = time.monotonic()
    manifest = collect(args)
    output = private_output_path(args.output, label="manifest output")
    private_write(output, manifest)
    receipt = content_free_receipt(
        manifest, output, round((time.monotonic() - started) * 1000, 3),
    )
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["valid"] else 2


def _boundary_directory(output: Path) -> Path:
    directory = output.with_name(output.name + ".boundaries")
    if directory.is_symlink():
        raise RecapError("boundary directory must not be a symlink")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.stat().st_mode & 0o077:
        raise RecapError("boundary directory must have mode 0700")
    return directory.resolve()


def validate_boundary_set(
    value: dict[str, Any], *, expected_output: Path | None = None,
) -> dict[str, Any]:
    errors = []
    if value.get("schema_version") != BOUNDARY_SET_SCHEMA_VERSION:
        errors.append("unsupported schema_version")
    boundary_directory = None
    try:
        boundary_directory = Path(value["boundary_directory"])
        if (
            not boundary_directory.is_absolute() or boundary_directory.is_symlink()
            or not boundary_directory.is_dir() or boundary_directory.stat().st_mode & 0o077
        ):
            raise ValueError
        boundary_directory = boundary_directory.resolve()
    except (KeyError, OSError, ValueError, TypeError):
        errors.append("boundary_directory must be an owner-private absolute directory")
    if expected_output is not None and boundary_directory is not None:
        expected_directory = expected_output.resolve().with_name(expected_output.name + ".boundaries")
        if boundary_directory != expected_directory:
            errors.append("boundary_directory does not belong to the boundary-set output")
    members = value.get("members")
    if not isinstance(members, list) or not members:
        errors.append("members must be a non-empty list")
        members = []
    selected = value.get("selected_node_id")
    requested = value.get("requested")
    if not isinstance(requested, dict) or set(requested) != {"include_children", "chain"} or not all(
        isinstance(requested.get(key), bool) for key in ("include_children", "chain")
    ) or not any(requested.values()):
        errors.append("requested modes must be closed booleans with at least one enabled")
    node_ids = [member.get("node_id") for member in members if isinstance(member, dict)]
    if not isinstance(selected, str) or node_ids.count(selected) != 1:
        errors.append("selected node must occur exactly once")
    if len(node_ids) != len(set(node_ids)) or any(not isinstance(item, str) for item in node_ids):
        errors.append("member node identities must be unique strings")
    event_count = 0
    for member in members:
        if not isinstance(member, dict):
            errors.append("member must be an object")
            continue
        try:
            path = Path(member["manifest_path"])
            if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
                raise ValueError
            path = path.resolve()
            if boundary_directory is None or path.parent != boundary_directory:
                raise ValueError
            manifest = json.loads(path.read_text())
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            errors.append("member manifest must be an owner-private regular JSON file")
            continue
        validation = validate_manifest(manifest)
        if not validation["valid"]:
            errors.append("member manifest failed validation")
        if validation["manifest_sha256"] != member.get("manifest_sha256"):
            errors.append("member manifest digest mismatch")
        if manifest.get("scope", {}).get("boundary_receipt") != member.get("boundary_receipt"):
            errors.append("member boundary receipt mismatch")
        if manifest.get("scope", {}).get("relationship_node_id") != member.get("node_id"):
            errors.append("member relationship identity mismatch")
        session_path = manifest.get("scope", {}).get("session_path")
        if not isinstance(session_path, str) or sha256_bytes(session_path.encode()) != member.get("session_path_sha256"):
            errors.append("member session path binding mismatch")
        if validation["event_count"] != member.get("event_count"):
            errors.append("member event count mismatch")
        event_count += validation["event_count"]
    edges = value.get("edges")
    if not isinstance(edges, list):
        errors.append("edges must be a list")
        edges = []
    valid_ids = set(node_ids)
    seen_edges = set()
    for edge in edges:
        if not isinstance(edge, dict):
            errors.append("edge must be an object")
            continue
        identity = (edge.get("type"), edge.get("from"), edge.get("to"))
        if identity in seen_edges:
            errors.append("duplicate relationship edge")
        seen_edges.add(identity)
        if edge.get("type") not in {"child", "continuation"}:
            errors.append("unsupported relationship edge type")
        if edge.get("from") not in valid_ids or edge.get("to") not in valid_ids:
            errors.append("relationship edge references an unknown member")
    if isinstance(selected, str) and selected in valid_ids:
        adjacency = {node_id: set() for node_id in valid_ids}
        for _edge_type, source, target in seen_edges:
            if source in valid_ids and target in valid_ids:
                adjacency[source].add(target)
                adjacency[target].add(source)
        reached, queue = {selected}, [selected]
        while queue:
            for neighbor in adjacency[queue.pop(0)] - reached:
                reached.add(neighbor)
                queue.append(neighbor)
        if reached != valid_ids:
            errors.append("boundary set contains a member disconnected from the selected node")
    digest = sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
    return {
        "valid": not errors, "errors": errors, "member_count": len(members),
        "event_count": event_count, "boundary_set_sha256": digest,
    }


def command_collect_set(args: argparse.Namespace) -> int:
    if not args.include_children and not args.chain:
        raise RecapError("collect-set requires --include-children, --chain, or both")
    started = time.monotonic()
    recall_path = find_recall_script(args.recall_script)
    normalized_target = (
        None if args.current else session_target(args.session, allow_remote_receipt=False)
    )
    graph = recall_session_relations(
        recall_path, current=args.current, session=normalized_target,
        include_children=args.include_children, chain=args.chain,
    )
    output = private_output_path(args.output, label="boundary-set output")
    directory = _boundary_directory(output)
    members = []
    for index, node in enumerate(graph["nodes"]):
        node_id = node.get("node_id")
        path = node.get("path")
        if not isinstance(node_id, str) or not isinstance(path, str):
            raise RecapError("Recall relationship node omitted exact identity or path")
        member_path = directory / f"{index:04d}-{sha256_bytes(node_id.encode())[:16]}.json"
        member_args = argparse.Namespace(
            current=False, session=path, output=str(member_path), repo=args.repo,
            recall_script=str(recall_path),
        )
        manifest = collect(member_args)
        manifest["scope"]["relationship_node_id"] = node_id
        private_write(member_path, manifest)
        validation = validate_manifest(manifest)
        if not validation["valid"]:
            raise RecapError("collected boundary manifest failed validation")
        members.append({
            "node_id": node_id,
            "harness": node.get("harness"),
            "kind": node.get("kind"),
            "parent_id": node.get("parent_id"),
            "forked_from_id": node.get("forked_from_id"),
            "selection_reasons": node.get("selection_reasons"),
            "graph_depth": node.get("graph_depth"),
            "manifest_path": str(member_path),
            "manifest_sha256": validation["manifest_sha256"],
            "boundary_receipt": manifest["scope"].get("boundary_receipt"),
            "session_path_sha256": sha256_bytes(str(path).encode()),
            "event_count": validation["event_count"],
        })
    value = {
        "schema_version": BOUNDARY_SET_SCHEMA_VERSION,
        "boundary_directory": str(directory),
        "selected_node_id": graph["selected_node_id"],
        "requested": graph["requested"],
        "members": members,
        "edges": graph["edges"],
    }
    validation = validate_boundary_set(value, expected_output=output)
    if not validation["valid"]:
        raise RecapError("boundary set failed validation")
    private_write(output, value)
    print(json.dumps({
        "schema_version": "recap.boundary-set-receipt.v1",
        "boundary_set_sha256": validation["boundary_set_sha256"],
        "member_count": validation["member_count"],
        "event_count": validation["event_count"],
        "valid": True,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "output_mode": oct(output.stat().st_mode & 0o777),
    }, sort_keys=True))
    return 0


def command_validate_set(args: argparse.Namespace) -> int:
    path, value = _load_private_json(args.boundary_set, label="boundary set")
    validation = validate_boundary_set(value, expected_output=path)
    print(json.dumps(validation, sort_keys=True))
    return 0 if validation["valid"] else 2


def command_validate(args: argparse.Namespace) -> int:
    path = Path(args.manifest).expanduser()
    value = json.loads(path.read_text())
    result = validate_manifest(value)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["valid"] else 2


def command_packet(args: argparse.Namespace) -> int:
    path = Path(args.manifest).expanduser()
    if path.is_symlink():
        raise RecapError("manifest must be an owner-private regular file")
    path = path.resolve()
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise RecapError("manifest must be an owner-private regular file")
    value = json.loads(path.read_text())
    validation = validate_manifest(value)
    if not validation["valid"]:
        raise RecapError("manifest failed validation")
    events = list(packet_events(value["ledger"], args.packet_id))
    print(json.dumps({
        "schema_version": "recap.semantic-packet.v1",
        "packet_id": args.packet_id,
        "event_count": len(events),
        "events": events,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


def _load_private_json(path_value: str, *, label: str) -> tuple[Path, dict[str, Any]]:
    path = Path(path_value).expanduser()
    if path.is_symlink():
        raise RecapError(f"{label} must be an owner-private regular file")
    path = path.resolve()
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise RecapError(f"{label} must be an owner-private regular file")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise RecapError(f"{label} must contain a JSON object")
    return path, value


def command_seal_accounting(args: argparse.Namespace) -> int:
    _, manifest = _load_private_json(args.manifest, label="manifest")
    if not validate_manifest(manifest)["valid"]:
        raise RecapError("manifest failed validation")
    _, draft = _load_private_json(args.draft, label="accounting draft")
    sealed, validation = seal_accounting(manifest, draft)
    if not validation["valid"]:
        print(json.dumps(accounting_receipt(draft, validation), sort_keys=True))
        return 2
    output = private_output_path(args.output, label="accounting output")
    private_write(output, sealed)
    print(json.dumps(accounting_receipt(sealed, validation), sort_keys=True))
    return 0


def command_validate_accounting(args: argparse.Namespace) -> int:
    _, manifest = _load_private_json(args.manifest, label="manifest")
    _, accounting = _load_private_json(args.accounting, label="accounting")
    manifest_validation = validate_manifest(manifest)
    if not manifest_validation["valid"]:
        raise RecapError("manifest failed validation")
    validation = validate_accounting(manifest, accounting)
    print(json.dumps(accounting_receipt(accounting, validation), sort_keys=True))
    return 0 if validation["valid"] else 2


def _synthesis_inputs(args: argparse.Namespace):
    _, manifest = _load_private_json(args.manifest, label="manifest")
    _, accounting = _load_private_json(args.accounting, label="accounting")
    _, draft = _load_private_json(args.draft, label="synthesis draft")
    if not validate_manifest(manifest)["valid"]:
        raise RecapError("manifest failed validation")
    return manifest, accounting, draft


def synthesis_receipt(validation: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "recap.synthesis-receipt.v1",
        "draft_sha256": validation.get("draft_sha256"),
        "item_count": validation.get("item_count", 0),
        "claim_count": validation.get("claim_count", 0),
        "low_signal_group_count": validation.get("low_signal_group_count", 0),
        "valid": bool(validation.get("valid")),
        "error_count": len(validation.get("errors", [])),
    }


def command_validate_synthesis(args: argparse.Namespace) -> int:
    manifest, accounting, draft = _synthesis_inputs(args)
    validation = validate_synthesis(manifest, accounting, draft)
    print(json.dumps(synthesis_receipt(validation), sort_keys=True))
    return 0 if validation["valid"] else 2


def command_render_synthesis(args: argparse.Namespace) -> int:
    manifest, accounting, draft = _synthesis_inputs(args)
    rendered, receipt = render_markdown(manifest, accounting, draft)
    output = private_output_path(args.output, label="render output")
    private_write_text(output, rendered)
    receipt["output_mode"] = oct(output.stat().st_mode & 0o777)
    print(json.dumps(receipt, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    collect_parser = commands.add_parser("collect", help="write a private manifest and print a safe receipt")
    target = collect_parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--current", action="store_true")
    target.add_argument("--session")
    collect_parser.add_argument("--output", required=True)
    collect_parser.add_argument(
        "--repo", action="append",
        help="explicit repository/worktree to verify now; repeat for multiple repositories",
    )
    collect_parser.add_argument("--recall-script")
    collect_parser.set_defaults(func=command_collect)
    set_parser = commands.add_parser(
        "collect-set", help="collect exact child and continuation boundaries separately",
    )
    target = set_parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--current", action="store_true")
    target.add_argument("--session")
    set_parser.add_argument("--include-children", action="store_true")
    set_parser.add_argument("--chain", action="store_true")
    set_parser.add_argument("--output", required=True)
    set_parser.add_argument("--repo", action="append")
    set_parser.add_argument("--recall-script")
    set_parser.set_defaults(func=command_collect_set)
    validate_parser = commands.add_parser("validate", help="validate structural completeness")
    validate_parser.add_argument("manifest")
    validate_parser.set_defaults(func=command_validate)
    set_validate_parser = commands.add_parser("validate-set", help="validate a private boundary set")
    set_validate_parser.add_argument("boundary_set")
    set_validate_parser.set_defaults(func=command_validate_set)
    packet_parser = commands.add_parser(
        "packet", help="print one bounded redacted packet from a private manifest",
    )
    packet_parser.add_argument("manifest")
    packet_parser.add_argument("packet_id")
    packet_parser.set_defaults(func=command_packet)
    seal_parser = commands.add_parser(
        "seal-accounting", help="seal an exhaustive private semantic-accounting draft",
    )
    seal_parser.add_argument("manifest")
    seal_parser.add_argument("draft")
    seal_parser.add_argument("--output", required=True)
    seal_parser.set_defaults(func=command_seal_accounting)
    accounting_parser = commands.add_parser(
        "validate-accounting", help="validate exactly-once semantic accounting",
    )
    accounting_parser.add_argument("manifest")
    accounting_parser.add_argument("accounting")
    accounting_parser.set_defaults(func=command_validate_accounting)
    synthesis_parser = commands.add_parser(
        "validate-synthesis", help="validate a host-agent-authored recap draft",
    )
    synthesis_parser.add_argument("manifest")
    synthesis_parser.add_argument("accounting")
    synthesis_parser.add_argument("draft")
    synthesis_parser.set_defaults(func=command_validate_synthesis)
    render_parser = commands.add_parser(
        "render-synthesis", help="render a validated recap to an owner-private file",
    )
    render_parser.add_argument("manifest")
    render_parser.add_argument("accounting")
    render_parser.add_argument("draft")
    render_parser.add_argument("--output", required=True)
    render_parser.set_defaults(func=command_render_synthesis)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return args.func(args)
    except (RecapError, LedgerError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"recap: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
