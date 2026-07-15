#!/usr/bin/env python3
"""Measure the pre-Recap Recall+git workflow against the frozen L0 contract."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
RECALL = ROOT / "recall" / "skills" / "recall" / "scripts" / "recall.py"
CORPUS = Path(__file__).with_name("corpus.json")


def load_recall():
    spec = importlib.util.spec_from_file_location("recall_baseline", RECALL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def write_claude(path: Path, count: int, secret: bool = False) -> None:
    records = []
    for index in range(count):
        text = f"synthetic event {index:04d}"
        if secret and index == 2:
            text = "api_key=sk-" + "A" * 32
        records.append({
            "type": "user" if index % 2 == 0 else "assistant",
            "timestamp": f"2026-07-14T00:{index // 60:02d}:{index % 60:02d}Z",
            "cwd": "/tmp/recap-eval",
            "gitBranch": "eval",
            "message": {"content": text},
        })
    path.write_text("".join(json.dumps(record) + "\n" for record in records))


def write_codex(path: Path) -> None:
    records = [
        {"timestamp": "2026-07-14T01:00:00Z", "type": "session_meta", "payload": {
            "cwd": "/tmp/recap-eval", "git_branch": "eval", "model": "test"}},
        {"timestamp": "2026-07-14T01:00:01Z", "type": "response_item", "payload": {
            "role": "user", "content": [{"type": "input_text", "text": "codex event"}]}},
    ]
    path.write_text("".join(json.dumps(record) + "\n" for record in records))


def capture_show(recall, target: Path) -> tuple[int, list[str]]:
    output = io.StringIO()
    with redirect_stdout(output):
        code = recall.show(SimpleNamespace(target=str(target), tail=0, around=None, prompts=False))
    return code, output.getvalue().splitlines()


def measure() -> dict:
    recall = load_recall()
    with tempfile.TemporaryDirectory(prefix="recap-baseline-") as temporary:
        root = Path(temporary)
        claude = root / "session.jsonl"
        long_claude = root / "long.jsonl"
        secret = root / "secret.jsonl"
        codex = root / "rollout-synthetic.jsonl"
        write_claude(claude, 8)
        write_claude(long_claude, 1205)
        write_claude(secret, 7, secret=True)
        write_codex(codex)

        code, lines = capture_show(recall, claude)
        long_code, long_lines = capture_show(recall, long_claude)
        secret_code, secret_lines = capture_show(recall, secret)
        codex_code, codex_lines = capture_show(recall, codex)

    checks = {
        "exact_target": code == 0 and len(lines) == 8,
        "stable_order": lines[0].endswith("synthetic event 0000") and lines[-1].endswith("synthetic event 0007"),
        "unbounded_local_read": long_code == 0 and len(long_lines) == 1205,
        "remote_pagination": False,
        "machine_manifest": False,
        "current_session_identity": False,
        "coverage_accounting": False,
        "git_provenance": False,
        "parent_child_scope": True,
        "secret_redaction": secret_code == 0 and all("sk-" not in line for line in secret_lines),
        "observed_test_reporting": False,
        "claude_and_codex": codex_code == 0 and any("codex event" in line for line in codex_lines),
    }
    passed = sum(checks.values())
    return {
        "schema_version": 1,
        "system": "Recall show plus manual git inspection before Recap",
        "corpus": json.loads(CORPUS.read_text())["name"],
        "checks": checks,
        "passed": passed,
        "total": len(checks),
        "score": round(passed / len(checks), 4),
        "limitations": [
            "Central show caps a full read at 1000 chunks and exposes no cursor.",
            "No machine-readable session manifest or coverage ledger exists.",
            "Git state is inspected separately and cannot be attributed automatically.",
            "Current-session and child-session identity are not a stable public contract.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = measure()
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
