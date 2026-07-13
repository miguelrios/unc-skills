#!/usr/bin/env python3
"""Fail if the npm package would contain local state, secrets, or caches."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_COMPONENTS = {
    ".desloppify",
    "__pycache__",
    "external_review_sessions",
    "credentials",
    "token",
}
FORBIDDEN_FILENAMES = {"query.json"}
FORBIDDEN_PREFIXES = ("review_packet", "holistic_packet")
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(rb"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(rb"sk-[A-Za-z0-9]{20,}"),
)


def main() -> int:
    try:
        files = pack_files()
        violations = audit_files(files, ROOT)
    except subprocess.TimeoutExpired as exc:
        raise SystemExit("package audit timed out while running npm pack") from exc
    except OSError as exc:
        raise SystemExit("package audit could not execute npm or read a packaged file") from exc
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise SystemExit("package audit could not parse the npm pack manifest") from exc
    if violations:
        raise SystemExit("prohibited package content:\n" + "\n".join(sorted(set(violations))))
    required = {
        "skills/desloppify/SKILL.md",
        "skills/desloppify/scripts/desloppify_portable.py",
        ".claude-plugin/plugin.json",
        ".codex-plugin/plugin.json",
        "LICENSE",
        "README.md",
    }
    missing = required - set(files)
    if missing:
        raise SystemExit("missing required package content:\n" + "\n".join(sorted(missing)))
    print(json.dumps({"status": "PASS", "file_count": len(files), "files": files}, indent=2))
    return 0


def pack_files() -> list[str]:
    npm = shutil.which("npm")
    if npm is None:
        raise FileNotFoundError("npm executable not found")
    result = subprocess.run(
        [npm, "pack", "--dry-run", "--json"],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"npm pack exited {result.returncode}")
    payload = json.loads(result.stdout)
    return [entry["path"] for entry in payload[0]["files"]]


def forbidden_path(path: str) -> bool:
    components = tuple(part.lower() for part in PurePosixPath(path).parts)
    if any(part in FORBIDDEN_COMPONENTS for part in components):
        return True
    filename = components[-1] if components else ""
    if filename in FORBIDDEN_FILENAMES or filename == ".env" or filename.startswith(".env."):
        return True
    return filename.endswith(".json") and filename.startswith(FORBIDDEN_PREFIXES)


def audit_files(files: list[str], root: Path) -> list[str]:
    """Return package paths that contain private state or secret-shaped data."""
    violations = [
        path
        for path in files
        if path.endswith((".pyc", ".tgz")) or forbidden_path(path)
    ]
    for relative in files:
        path = root / relative
        if not path.is_file():
            continue
        data = path.read_bytes()
        if any(pattern.search(data) for pattern in SECRET_PATTERNS):
            violations.append(f"{relative} (secret-shaped content)")
    return violations


if __name__ == "__main__":
    raise SystemExit(main())
