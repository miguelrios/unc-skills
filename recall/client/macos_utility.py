"""Content-free lifecycle surfaces for the installed Recall macOS utility."""

from __future__ import annotations

import os
import math
import plistlib
import sqlite3
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SourceSpec:
    label: str
    spool_name: str
    surface: str


SOURCE_SPECS = {
    "claude-code": SourceSpec(
        "ai.parcha.recall.claude", "claude.db", "claude-code-project-jsonl"
    ),
    "codex": SourceSpec(
        "ai.parcha.recall.codex", "codex.db", "chatgpt-codex-desktop-rollouts"
    ),
    "cowork": SourceSpec(
        "ai.parcha.recall.cowork", "cowork.db", "claude-cowork-project-jsonl"
    ),
    "chatgpt-export": SourceSpec(
        "ai.parcha.recall.chatgpt-export", "chatgpt-export-runner.db", "chatgpt-export-inbox"
    ),
    "imessage": SourceSpec(
        "ai.parcha.recall.imessage", "imessage.db", "apple-imessage-read-only-snapshot"
    ),
    "whatsapp": SourceSpec(
        "ai.parcha.recall.whatsapp", "whatsapp.db", "whatsapp-selected-text-export"
    ),
    "selected-text": SourceSpec(
        "ai.parcha.recall.selected-text", "selected-text.db", "selected-markdown-obsidian-root"
    ),
    "safari": SourceSpec(
        "ai.parcha.recall.safari", "safari.db", "selected-safari-history-bookmarks"
    ),
    "chrome": SourceSpec(
        "ai.parcha.recall.chrome", "chrome.db", "selected-chrome-history-bookmarks"
    ),
    "apple-notes": SourceSpec(
        "ai.parcha.recall.apple-notes", "apple-notes.db", "apple-notes-pinned-snippet-schema"
    ),
    "hermes": SourceSpec(
        "ai.parcha.recall.hermes", "hermes.db", "hermes-session-schema-v22"
    ),
}


class MacUtilityError(ValueError):
    """A closed error that never includes a local path or private database value."""


def _regular(path: Path) -> bool:
    try:
        details = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(details.st_mode) and not stat.S_ISLNK(details.st_mode)


def _metadata(path: Path) -> dict[str, str] | None:
    if not _regular(path):
        return None
    try:
        connection = sqlite3.connect(path.resolve(strict=True).as_uri() + "?mode=ro&immutable=1", uri=True)
        try:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if "meta" not in tables:
                return None
            return dict(connection.execute(
                "SELECT key,value FROM meta WHERE key IN "
                "('last_scan_at','last_success_epoch','committed_cursor','last_error_code')"
            ))
        finally:
            connection.close()
    except (OSError, sqlite3.Error, ValueError):
        return None


def _plist_privacy(path: Path) -> str | None:
    if not _regular(path):
        return None
    try:
        if path.stat().st_size > 1_000_000:
            return None
        with path.open("rb") as source:
            value = plistlib.load(source)
        arguments = value.get("ProgramArguments") if isinstance(value, dict) else None
        if not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
            return None
        index = arguments.index("--privacy-mode")
        mode = arguments[index + 1]
        return mode if mode in {"off", "scrub", "drop"} else None
    except (OSError, ValueError, IndexError, plistlib.InvalidFileException):
        return None
def _source_status(*, prefix: Path, launch_agents: Path, name: str, now: float) -> dict[str, Any]:
    spec = SOURCE_SPECS[name]
    plist = launch_agents / f"{spec.label}.plist"
    plist_exists = plist.exists() or plist.is_symlink()
    privacy_mode = _plist_privacy(plist)
    enabled = _regular(plist) and privacy_mode is not None
    state = prefix / "state" / spec.spool_name
    state_exists = state.exists() or state.is_symlink()
    metadata = _metadata(state)
    if plist_exists and not enabled:
        health = "invalid_local_state"
    elif not enabled:
        health = "disabled"
    elif state_exists and metadata is None:
        health = "invalid_local_state"
    elif metadata is None:
        health = "starting"
    elif metadata.get("last_error_code"):
        health = "degraded"
    else:
        health = "ready"
    last_success = None
    if metadata is not None:
        raw = metadata.get("last_success_epoch", metadata.get("last_scan_at"))
        try:
            last_success = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            health = "invalid_local_state" if enabled else health
    lag = None if last_success is None else max(0, int(now - last_success))
    checkpointed = bool(metadata and (
        "committed_cursor" in metadata or "last_scan_at" in metadata
    ))
    return {
        "enabled": enabled,
        "health": health,
        "lag_seconds": lag,
        "checkpointed": checkpointed,
        "state_present": metadata is not None,
        "privacy_mode": privacy_mode,
        "surface": spec.surface,
    }


def mac_status(*, prefix: Path, launch_agents: Path, now: float | None = None) -> dict[str, Any]:
    """Return a closed health view without paths, credentials, content, or exception text."""

    timestamp = time.time() if now is None else now
    if (
        not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool)
        or not math.isfinite(timestamp)
    ):
        raise MacUtilityError("invalid_clock")
    sources = {
        name: _source_status(
            prefix=Path(prefix), launch_agents=Path(launch_agents), name=name, now=float(timestamp)
        )
        for name in SOURCE_SPECS
    }
    return {
        "schema_version": 1,
        "mode": "mac-status",
        "source_classes": list(SOURCE_SPECS),
        "enabled": sum(item["enabled"] for item in sources.values()),
        "sources": sources,
    }


def disable_source(name: str, *, launch_agents: Path, no_load: bool = False) -> dict[str, Any]:
    """Unload one known agent and retain every source database and checkpoint."""

    if name not in SOURCE_SPECS:
        raise MacUtilityError("unknown_source")
    if not isinstance(no_load, bool):
        raise MacUtilityError("invalid_no_load")
    spec = SOURCE_SPECS[name]
    if not no_load:
        target = f"gui/{os.getuid()}/{spec.label}"
        try:
            subprocess.run(
                ["/bin/launchctl", "bootout", target], check=False,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            for _ in range(100):
                result = subprocess.run(
                    ["/bin/launchctl", "print", target], check=False,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                if result.returncode != 0:
                    break
                time.sleep(0.1)
            else:
                raise MacUtilityError("launch_agent_stop_failed")
        except OSError:
            raise MacUtilityError("launch_control_unavailable") from None
    try:
        (Path(launch_agents) / f"{spec.label}.plist").unlink(missing_ok=True)
    except OSError:
        raise MacUtilityError("launch_agent_remove_failed") from None
    return {
        "schema_version": 1,
        "mode": "mac-disable",
        "source": name,
        "enabled": False,
        "state_retained": True,
    }
