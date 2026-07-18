"""Content-free lifecycle surfaces for the installed Recall macOS utility."""

from __future__ import annotations

import hashlib
import json
import os
import math
import plistlib
import sqlite3
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
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


def _plist_arguments(path: Path) -> list[str]:
    if not _regular(path):
        raise MacUtilityError("invalid_launch_agent")
    try:
        if path.stat().st_size > 1_000_000:
            raise MacUtilityError("invalid_launch_agent")
        with path.open("rb") as source:
            value = plistlib.load(source)
        arguments = value.get("ProgramArguments") if isinstance(value, dict) else None
        if not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
            raise MacUtilityError("invalid_launch_agent")
        return arguments
    except MacUtilityError:
        raise
    except (OSError, plistlib.InvalidFileException):
        raise MacUtilityError("invalid_launch_agent") from None


def _option(arguments: list[str], name: str) -> str:
    try:
        value = arguments[arguments.index(name) + 1]
    except (ValueError, IndexError):
        raise MacUtilityError("missing_keychain_reference") from None
    if not value:
        raise MacUtilityError("missing_keychain_reference")
    return value


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


def revoke_source(name: str, *, launch_agents: Path, no_load: bool = False) -> dict[str, Any]:
    """Disable one source and delete only the credential reference pinned in its plist."""

    if name not in SOURCE_SPECS:
        raise MacUtilityError("unknown_source")
    spec = SOURCE_SPECS[name]
    arguments = _plist_arguments(Path(launch_agents) / f"{spec.label}.plist")
    service = _option(arguments, "--keychain-service")
    account = _option(arguments, "--keychain-account")
    disable_source(name, launch_agents=launch_agents, no_load=no_load)
    try:
        from client.mac import delete_keychain_token

        deleted = delete_keychain_token(service, account)
    except (OSError, RuntimeError, ValueError):
        raise MacUtilityError("keychain_revoke_failed") from None
    return {
        "schema_version": 1,
        "mode": "mac-revoke",
        "source": name,
        "enabled": False,
        "credential_revoked": bool(deleted),
        "state_retained": True,
    }


def _state_root(prefix: Path) -> Path:
    root = Path(prefix) / "state"
    try:
        details = root.lstat()
    except FileNotFoundError:
        return root
    except OSError:
        raise MacUtilityError("local_state_unavailable") from None
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise MacUtilityError("local_state_unsafe")
    return root


def reset_local_source(
    name: str,
    *,
    prefix: Path,
    launch_agents: Path,
    confirmation: str,
    no_load: bool = False,
) -> dict[str, Any]:
    """Pause one source and delete only its local checkpoints and content-free logs."""

    if name not in SOURCE_SPECS:
        raise MacUtilityError("unknown_source")
    if confirmation != name:
        raise MacUtilityError("confirmation_mismatch")
    disable_source(name, launch_agents=launch_agents, no_load=no_load)
    root = _state_root(Path(prefix))
    spool = root / SOURCE_SPECS[name].spool_name
    candidates = [
        spool,
        Path(str(spool) + "-wal"),
        Path(str(spool) + "-shm"),
        Path(str(spool) + ".stdout.log"),
        Path(str(spool) + ".stderr.log"),
    ]
    if name == "chatgpt-export":
        candidates.extend([
            root / "chatgpt-export-catalog.db",
            root / "chatgpt-export-catalog.db-wal",
            root / "chatgpt-export-catalog.db-shm",
        ])
    try:
        for path in candidates:
            path.unlink(missing_ok=True)
    except OSError:
        raise MacUtilityError("local_state_remove_failed") from None
    return {
        "schema_version": 1,
        "mode": "mac-reset-local",
        "source": name,
        "enabled": False,
        "local_state_retained": False,
        "central_evidence_retained": True,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _integrity(prefix: Path) -> dict[str, int | str]:
    root = Path(prefix)
    try:
        root_details = root.lstat()
        if stat.S_ISLNK(root_details.st_mode) or not stat.S_ISDIR(root_details.st_mode):
            raise OSError
    except OSError:
        return {"status": "invalid", "checked_files": 0, "mismatches": 1}
    manifest_path = root / "MANIFEST.json"
    if not _regular(manifest_path):
        return {"status": "unavailable", "checked_files": 0, "mismatches": 0}
    try:
        if manifest_path.stat().st_size > 10_000_000:
            raise ValueError
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format") != "recall-macos-v2":
            raise ValueError
        entries = manifest["files"]
        if not isinstance(entries, list) or len(entries) > 100_000:
            raise ValueError
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return {"status": "invalid", "checked_files": 0, "mismatches": 1}
    checked = 0
    mismatches = 0
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            mismatches += 1
            continue
        try:
            relative = PurePosixPath(entry["path"])
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise ValueError
            if not (
                relative.parts[0] in {"bin", "lib", "runtime"}
                or entry["path"] == "RUNTIME_LOCK.json"
            ):
                continue
            path = root.joinpath(*relative.parts)
            for parent in path.parents:
                if parent == root:
                    break
                if stat.S_ISLNK(parent.lstat().st_mode):
                    raise ValueError
            checked += 1
            kind = entry.get("type")
            details = path.lstat()
            if kind == "symlink":
                if not stat.S_ISLNK(details.st_mode) or os.readlink(path) != entry.get("target"):
                    mismatches += 1
            elif kind == "file":
                if (
                    not stat.S_ISREG(details.st_mode)
                    or details.st_size != entry.get("bytes")
                    or details.st_size > 1_000_000_000
                    or _sha256_file(path) != entry.get("sha256")
                ):
                    mismatches += 1
            else:
                mismatches += 1
        except (IndexError, OSError, TypeError, ValueError):
            mismatches += 1
    return {
        "status": "verified" if mismatches == 0 else "mismatch",
        "checked_files": checked,
        "mismatches": mismatches,
    }


def support_report(
    *, prefix: Path, launch_agents: Path, now: float | None = None
) -> dict[str, Any]:
    """Return support diagnostics that cannot contain paths, content, or credentials."""

    status_value = mac_status(prefix=prefix, launch_agents=launch_agents, now=now)
    return {
        "schema_version": 1,
        "mode": "mac-support",
        "enabled": status_value["enabled"],
        "source_classes": status_value["source_classes"],
        "sources": status_value["sources"],
        "package_integrity": _integrity(Path(prefix)),
    }
