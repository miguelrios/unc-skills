#!/usr/bin/env python3
"""Collect and validate a private, evidence-addressed coding-session manifest."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "recap.manifest.v0.1"
UUID_RE = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
SECRET_PATTERNS = (
    re.compile(r"(?i)(?:api[_-]?key|token|secret|password|authorization)\s*[=:]\s*\S+"),
    re.compile(r"\b(?:sk|xox[baprs]|gh[psou])[-_][A-Za-z0-9_-]{16,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
)


class RecapError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sanitize(text: str) -> tuple[str, int]:
    redactions = 0
    safe_lines = []
    for line in text.splitlines():
        if line == "[redacted-secret-line]":
            safe_lines.append(line)
            redactions += 1
        elif any(pattern.search(line) for pattern in SECRET_PATTERNS):
            safe_lines.append("[redacted-secret-line]")
            redactions += 1
        else:
            safe_lines.append(line)
    return "\n".join(safe_lines), redactions


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
    if thread:
        root = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "sessions"
        return find_exact(root, thread, codex=True)
    session = os.environ.get("CLAUDE_SESSION_ID")
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
        Path.home() / ".codex/skills/recall/scripts/recall.py",
        Path.home() / ".agents/skills/recall/scripts/recall.py",
    ])
    return candidates


def load_recall(explicit: str | None):
    for candidate in recall_candidates(explicit):
        if not candidate.is_file():
            continue
        spec = importlib.util.spec_from_file_location("recap_recall_adapter", candidate)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if hasattr(module, "direct_chunks"):
            return module, candidate
    raise RecapError("Recall parser not found; install Recall or pass --recall-script")


def run_git(repo: Path, args: list[str], timeout: float = 8.0) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], text=True, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 124, ""
    return result.returncode, result.stdout.rstrip("\n")


def git_snapshot(cwd: str | None) -> dict[str, Any]:
    if not cwd:
        return {"available": False, "reason": "session has no cwd metadata"}
    code, root_text = run_git(Path(cwd), ["rev-parse", "--show-toplevel"])
    if code != 0 or not root_text:
        return {"available": False, "reason": "session cwd is not an accessible git worktree"}
    root = Path(root_text)
    _, head = run_git(root, ["rev-parse", "HEAD"])
    _, branch = run_git(root, ["branch", "--show-current"])
    status_code, status = run_git(root, ["status", "--porcelain=v1", "--untracked-files=all"])
    changed = []
    if status_code == 0:
        for line in status.splitlines():
            if len(line) >= 4:
                value = line[3:]
                changed.append(value.split(" -> ", 1)[-1])
    return {
        "available": True,
        "repo_root": str(root),
        "head": head or None,
        "branch": branch or None,
        "status_porcelain": status.splitlines(),
        "changed_paths": sorted(set(changed)),
        "snapshot_at": utc_now(),
        "attribution": "current_state_only",
    }


def private_write(path: Path, value: dict[str, Any]) -> None:
    path = path.expanduser()
    if path.is_symlink():
        raise RecapError("refusing to write through a symlink")
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
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


def collect(args: argparse.Namespace) -> dict[str, Any]:
    started = time.monotonic()
    path = resolve_current() if args.current else Path(args.session).expanduser().resolve()
    if not path.is_file():
        raise RecapError("session file does not exist")
    before = path.stat()
    recall, recall_path = load_recall(args.recall_script)
    parsed = recall.direct_chunks(path)
    after = path.stat()
    native_id = session_id_from_path(path)
    events = []
    redaction_count = 0
    cwd = None
    for ordinal, (timestamp, surface, text, entities) in enumerate(parsed):
        safe_text, redactions = sanitize(str(text))
        redaction_count += redactions
        text_hash = sha256_bytes(safe_text.encode())
        event_id = sha256_bytes(
            f"{native_id}\0{ordinal}\0{timestamp}\0{surface}\0{text_hash}".encode()
        )[:24]
        events.append({
            "ordinal": ordinal,
            "event_id": event_id,
            "timestamp": timestamp,
            "surface": surface,
            "text": safe_text,
            "text_sha256": text_hash,
            "entities": [{"kind": kind, "value": value} for kind, value in entities],
        })
    metadata = {}
    try:
        _ignored, metadata = recall.parse_file(path, harness_for(path))
    except (AttributeError, OSError, ValueError, json.JSONDecodeError):
        metadata = {}
    cwd = metadata.get("cwd") if isinstance(metadata, dict) else None
    stable = before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "scope": {
            "harness": harness_for(path),
            "native_session_id": native_id,
            "session_path": str(path),
            "source_sha256": sha256_file(path),
            "source_size_before": before.st_size,
            "source_size_after": after.st_size,
            "source_stable_during_collection": stable,
            "children_included": False,
            "continuations_included": False,
        },
        "session_metadata": metadata if isinstance(metadata, dict) else {},
        "collector": {
            "recap_version": SCHEMA_VERSION,
            "recall_parser": str(recall_path),
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
        },
        "coverage": {
            "first_ordinal": 0 if events else None,
            "last_ordinal": len(events) - 1 if events else None,
            "observed_events": len(events),
            "manifest_events": len(events),
            "redacted_lines": redaction_count,
            "source_complete": stable,
            "structural_unaccounted_events": 0,
            "duplicate_event_ids": 0,
            "semantic_accounting": "not_performed",
        },
        "git": git_snapshot(args.repo or cwd),
        "events": events,
    }
    return manifest


def validate_manifest(value: dict[str, Any]) -> dict[str, Any]:
    errors = []
    if value.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported schema_version")
    events = value.get("events")
    if not isinstance(events, list):
        errors.append("events must be a list")
        events = []
    ordinals = [event.get("ordinal") for event in events if isinstance(event, dict)]
    if ordinals != list(range(len(events))):
        errors.append("event ordinals are not contiguous and ordered")
    event_ids = [event.get("event_id") for event in events if isinstance(event, dict)]
    if len(event_ids) != len(set(event_ids)):
        errors.append("event IDs are not unique")
    native_id = value.get("scope", {}).get("native_session_id")
    for ordinal, event in enumerate(events):
        if not isinstance(event, dict):
            errors.append(f"event {ordinal} is not an object")
            continue
        text = event.get("text")
        if not isinstance(text, str):
            errors.append(f"event {ordinal} text is not a string")
            continue
        text_hash = sha256_bytes(text.encode())
        if event.get("text_sha256") != text_hash:
            errors.append(f"event {ordinal} text digest mismatch")
        expected_id = sha256_bytes(
            f"{native_id}\0{ordinal}\0{event.get('timestamp')}\0{event.get('surface')}\0{text_hash}".encode()
        )[:24]
        if event.get("event_id") != expected_id:
            errors.append(f"event {ordinal} ID mismatch")
    coverage = value.get("coverage", {})
    if coverage.get("observed_events") != len(events) or coverage.get("manifest_events") != len(events):
        errors.append("coverage counts do not match events")
    return {
        "valid": not errors,
        "errors": errors,
        "event_count": len(events),
        "manifest_sha256": sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()),
        "source_complete": bool(coverage.get("source_complete")),
    }


def content_free_receipt(manifest: dict[str, Any], output: Path) -> dict[str, Any]:
    validation = validate_manifest(manifest)
    return {
        "schema_version": "recap.receipt.v0.1",
        "manifest_sha256": validation["manifest_sha256"],
        "session_boundary_sha256": sha256_bytes(json.dumps(manifest["scope"], sort_keys=True).encode()),
        "harness": manifest["scope"]["harness"],
        "event_count": validation["event_count"],
        "redacted_lines": manifest["coverage"]["redacted_lines"],
        "source_complete": validation["source_complete"],
        "valid": validation["valid"],
        "duration_ms": manifest["collector"]["duration_ms"],
        "output_mode": oct(output.stat().st_mode & 0o777),
    }


def command_collect(args: argparse.Namespace) -> int:
    manifest = collect(args)
    output = Path(args.output).expanduser().resolve()
    private_write(output, manifest)
    receipt = content_free_receipt(manifest, output)
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["valid"] else 2


def command_validate(args: argparse.Namespace) -> int:
    path = Path(args.manifest).expanduser()
    value = json.loads(path.read_text())
    result = validate_manifest(value)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["valid"] else 2


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    collect_parser = commands.add_parser("collect", help="write a private manifest and print a safe receipt")
    target = collect_parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--current", action="store_true")
    target.add_argument("--session")
    collect_parser.add_argument("--output", required=True)
    collect_parser.add_argument("--repo", help="explicit repository/worktree to snapshot")
    collect_parser.add_argument("--recall-script")
    collect_parser.set_defaults(func=command_collect)
    validate_parser = commands.add_parser("validate", help="validate structural completeness")
    validate_parser.add_argument("manifest")
    validate_parser.set_defaults(func=command_validate)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        return args.func(args)
    except (RecapError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"recap: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
