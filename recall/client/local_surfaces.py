"""Content-free structural preview for local desktop conversation surfaces."""

from __future__ import annotations

import json
import plistlib
import stat
from pathlib import Path
from typing import Any


MAX_METADATA_LINE_BYTES = 1_000_000
MAX_PLIST_BYTES = 1_000_000


class LocalSurfaceError(ValueError):
    """Closed preview error that never contains a local path or source value."""


def _regular(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(details.st_mode) and not stat.S_ISLNK(details.st_mode)


def _app_class(app: Path) -> dict[str, Any]:
    info = app / "Contents" / "Info.plist"
    if not app.exists():
        return {"installed": False, "display_class": "unknown", "runtime_family": "unknown"}
    if app.is_symlink() or not app.is_dir() or not _regular(info):
        return {"installed": True, "display_class": "unknown", "runtime_family": "unknown"}
    try:
        if info.stat().st_size > MAX_PLIST_BYTES:
            raise ValueError
        value = plistlib.loads(info.read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return {"installed": True, "display_class": "unknown", "runtime_family": "unknown"}
    if not isinstance(value, dict):
        return {"installed": True, "display_class": "unknown", "runtime_family": "unknown"}
    schemes = {
        scheme
        for item in value.get("CFBundleURLTypes", [])
        if isinstance(item, dict)
        for scheme in item.get("CFBundleURLSchemes", [])
        if isinstance(scheme, str)
    }
    codex_desktop = value.get("CFBundleIdentifier") == "com.openai.codex" and "codex" in schemes
    chatgpt = codex_desktop and value.get("CFBundleDisplayName") == "ChatGPT"
    return {
        "installed": True,
        "display_class": "chatgpt" if chatgpt else "unknown",
        "runtime_family": "codex-desktop" if codex_desktop else "unknown",
    }


def _claude_app_class(app: Path) -> dict[str, Any]:
    info = app / "Contents" / "Info.plist"
    if not app.exists():
        return {"installed": False, "display_class": "unknown", "runtime_family": "unknown"}
    if app.is_symlink() or not app.is_dir() or not _regular(info):
        return {"installed": True, "display_class": "unknown", "runtime_family": "unknown"}
    try:
        if info.stat().st_size > MAX_PLIST_BYTES:
            raise ValueError
        value = plistlib.loads(info.read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return {"installed": True, "display_class": "unknown", "runtime_family": "unknown"}
    claude = (
        isinstance(value, dict)
        and value.get("CFBundleIdentifier") == "com.anthropic.claudefordesktop"
        and value.get("CFBundleDisplayName") == "Claude"
    )
    return {
        "installed": True,
        "display_class": "claude" if claude else "unknown",
        "runtime_family": "claude-desktop" if claude else "unknown",
    }


def _rollout_originator(path: Path) -> str | None:
    try:
        if not _regular(path):
            return None
        with path.open("rb") as source:
            line = source.readline(MAX_METADATA_LINE_BYTES + 1)
        if not line.endswith(b"\n") or len(line) > MAX_METADATA_LINE_BYTES:
            return None
        value = json.loads(line)
        if not isinstance(value, dict) or value.get("type") != "session_meta":
            return None
        payload = value.get("payload")
        originator = payload.get("originator") if isinstance(payload, dict) else None
        return originator if isinstance(originator, str) else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def mac_local_surface_preview(*, app: Path, codex_root: Path) -> dict[str, Any]:
    """Classify the current ChatGPT/Codex desktop surface without reading record bodies."""

    app = Path(app).expanduser()
    codex_root = Path(codex_root).expanduser()
    try:
        root_details = codex_root.lstat()
    except OSError:
        raise LocalSurfaceError("codex_root_unavailable") from None
    if stat.S_ISLNK(root_details.st_mode) or not stat.S_ISDIR(root_details.st_mode):
        raise LocalSurfaceError("codex_root_unsafe")

    counts = {
        "eligible_rollouts": 0,
        "other_rollouts": 0,
        "unreadable_files": 0,
        "unsafe_files": 0,
    }
    for path in sorted(codex_root.rglob("rollout-*.jsonl")):
        if path.is_symlink() or not _regular(path):
            counts["unsafe_files"] += 1
            continue
        originator = _rollout_originator(path)
        if originator is None:
            counts["unreadable_files"] += 1
        elif originator == "Codex Desktop":
            counts["eligible_rollouts"] += 1
        else:
            counts["other_rollouts"] += 1
    return {
        "schema_version": 1,
        "mode": "mac-local-surface-preview",
        "network_requests": 0,
        "record_body_reads": 0,
        "app": _app_class(app),
        "codex_desktop": counts,
        "consumer_chat_history": {
            "status": "not_claimed_by_codex_rollouts",
            "eligible_records": 0,
        },
    }


def mac_claude_surface_preview(*, app: Path, support_root: Path) -> dict[str, Any]:
    """Report eligible Claude surfaces without opening any conversation or app-state record."""

    app = Path(app).expanduser()
    support_root = Path(support_root).expanduser()
    try:
        details = support_root.lstat()
    except OSError:
        raise LocalSurfaceError("support_root_unavailable") from None
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise LocalSurfaceError("support_root_unsafe")

    cowork = support_root / "local-agent-mode-sessions"
    eligible = 0
    unsafe = 0
    if cowork.is_dir() and not cowork.is_symlink():
        for path in sorted(cowork.glob("*/*/local_*/.claude/projects/*/*.jsonl")):
            if path.is_symlink() or not _regular(path):
                unsafe += 1
            else:
                eligible += 1

    state_store_classes = sum(
        int(path.is_dir() and not path.is_symlink())
        for path in (support_root / "IndexedDB", support_root / "Local Storage")
    )
    return {
        "schema_version": 1,
        "mode": "mac-claude-surface-preview",
        "network_requests": 0,
        "record_body_reads": 0,
        "app": _claude_app_class(app),
        "cowork": {
            "status": "supported_distinct_surface",
            "eligible_project_logs": eligible,
            "unsafe_files": unsafe,
        },
        "ordinary_chat": {
            "status": "not_locally_supported_on_probed_install",
            "eligible_record_files": 0,
            "excluded_app_state_store_classes": state_store_classes,
        },
    }
