#!/usr/bin/env python3
"""Materialize public synthetic session and git fixtures from the frozen case specs."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path


CASES = Path(__file__).with_name("cases.jsonl")


def run(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=subprocess.DEVNULL)


def init_repo(path: Path) -> None:
    path.mkdir(parents=True)
    run(path, "init", "-q")
    run(path, "config", "user.name", "Recap Fixture")
    run(path, "config", "user.email", "recap@example.invalid")
    (path / "README.md").write_text("synthetic recap fixture\n")
    run(path, "add", "README.md")
    run(path, "commit", "-qm", "fixture baseline")


def timestamp(ordinal: int) -> str:
    return (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=ordinal)).isoformat().replace("+00:00", "Z")


def claude_records(case: dict) -> list[dict]:
    count = case["event_count"]
    records = []
    for ordinal in range(count):
        text = f"case={case['id']} ordinal={ordinal} low-signal progress"
        if ordinal < len(case.get("claims", [])):
            text = f"case={case['id']} significant={case['claims'][ordinal]}"
        if case["id"] == "redacted-evidence" and ordinal == 4:
            text = "api_key=sk-" + "SYNTHETICNOTAREALSECRET" * 2
        records.append({
            "type": "user" if ordinal % 2 == 0 else "assistant",
            "timestamp": timestamp(ordinal),
            "cwd": f"/synthetic/{case['id']}",
            "gitBranch": "fixture",
            "message": {"content": text},
        })
    return records


def codex_records(case: dict) -> list[dict]:
    count = case["event_count"]
    records = [{
        "type": "session_meta", "timestamp": "2026-01-01T00:00:00Z",
        "payload": {"cwd": f"/synthetic/{case['id']}", "git_branch": "fixture", "model": "fixture"},
    }]
    for ordinal in range(max(0, count - 1)):
        text = f"case={case['id']} ordinal={ordinal} low-signal progress"
        if ordinal < len(case.get("claims", [])):
            text = f"case={case['id']} significant={case['claims'][ordinal]}"
        records.append({
            "type": "response_item", "timestamp": timestamp(ordinal + 1),
            "payload": {"role": "user" if ordinal % 2 == 0 else "assistant", "content": [
                {"type": "input_text" if ordinal % 2 == 0 else "output_text", "text": text}
            ]},
        })
    return records


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as output:
        for record in records:
            output.write(json.dumps(record, sort_keys=True) + "\n")


def build_repositories(root: Path) -> None:
    committed = root / "committed"
    init_repo(committed)
    (committed / "src").mkdir()
    (committed / "src/committed.py").write_text("VALUE = 1\n")
    run(committed, "add", "src/committed.py")
    run(committed, "commit", "-qm", "add committed fixture")

    dirty = root / "dirty"
    init_repo(dirty)
    (dirty / "src").mkdir()
    (dirty / "notes").mkdir()
    (dirty / "src/unstaged.py").write_text("BASE = True\n")
    run(dirty, "add", "src/unstaged.py")
    run(dirty, "commit", "-qm", "add unstaged baseline")
    (dirty / "src/staged.py").write_text("STAGED = True\n")
    run(dirty, "add", "src/staged.py")
    (dirty / "src/unstaged.py").write_text("BASE = False\n")
    (dirty / "notes/untracked.txt").write_text("untracked\n")

    renamed = root / "rename_revert"
    init_repo(renamed)
    (renamed / "src").mkdir()
    (renamed / "src/original.py").write_text("VALUE = 1\n")
    run(renamed, "add", "src/original.py")
    run(renamed, "commit", "-qm", "add rename baseline")
    run(renamed, "mv", "src/original.py", "src/renamed.py")
    attempted = renamed / "src/attempted.py"
    attempted.write_text("REVERTED = True\n")
    attempted.unlink()

    cross = root / "cross_worktree"
    init_repo(cross)
    (cross / "src").mkdir()
    (cross / "src/amended.py").write_text("VERSION = 1\n")
    run(cross, "add", "src/amended.py")
    run(cross, "commit", "-qm", "add amend fixture")
    (cross / "src/amended.py").write_text("VERSION = 2\n")
    run(cross, "add", "src/amended.py")
    run(cross, "commit", "--amend", "-qm", "amended fixture")
    other = root / "cross_worktree-other"
    run(cross, "worktree", "add", "-qb", "other", str(other))
    (other / "src/other.py").write_text("OTHER = True\n")

    expired = root / "expired"
    init_repo(expired)
    (expired / "expired.txt").write_text("short-lived history\n")
    run(expired, "add", "expired.txt")
    run(expired, "commit", "-qm", "short-lived commit")
    run(expired, "reflog", "expire", "--expire=now", "--all")
    run(expired, "gc", "--prune=now")

    for name in ("a", "b"):
        repo = root / "multi_repo" / name
        init_repo(repo)
        (repo / "src").mkdir()
        (repo / f"src/{name}.py").write_text(f"REPO = '{name}'\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    cases = [json.loads(line) for line in CASES.read_text().splitlines() if line]
    for case in cases:
        if case["event_count"] == 0:
            continue
        records = claude_records(case) if case["harness"] == "claude" else codex_records(case)
        suffix = f"session-{case['id']}.jsonl" if case["harness"] == "claude" else f"rollout-{case['id']}.jsonl"
        write_jsonl(root / "sessions" / suffix, records)
    build_repositories(root / "repos")
    (root / "BUILD.json").write_text(json.dumps({
        "schema_version": 2, "case_count": len(cases), "session_count": sum(c["event_count"] > 0 for c in cases)
    }, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
