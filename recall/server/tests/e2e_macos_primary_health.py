#!/usr/bin/env python3
"""Exact-package Darwin proof for independent primary-source health schedules."""

from __future__ import annotations

import argparse
import json
import platform
import plistlib
import shutil
import sqlite3
import subprocess
from pathlib import Path


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, check=True, text=True, capture_output=True)
    if completed.stderr:
        raise AssertionError("primary Mac lifecycle rendered unexpected stderr")
    return completed


def state(path: Path, metadata: dict[str, str], *, pending: int = 0) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
    connection.execute("CREATE TABLE outbox(state TEXT NOT NULL)")
    connection.execute("CREATE TABLE dead_letters(error_code TEXT NOT NULL)")
    connection.executemany(
        "INSERT INTO meta(key,value) VALUES (?,?)",
        metadata.items(),
    )
    connection.executemany(
        "INSERT INTO outbox(state) VALUES ('pending')",
        [()] * pending,
    )
    connection.commit()
    connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--bundle-root", type=Path, required=True)
    args = parser.parse_args()

    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise AssertionError("primary Mac health E2E requires Darwin arm64")
    root = args.workspace
    prefix = root / "installed"
    agents = root / "launch-agents"
    claude = root / "claude"
    codex = root / "codex"
    cowork = root / "cowork"
    inbox = root / "chatgpt-inbox"
    installed = False
    try:
        root.mkdir(mode=0o700)
        for path in (claude, codex, cowork, inbox):
            path.mkdir(mode=0o700)
        install = run([
            str(args.bundle_root / "install.sh"),
            "--prefix", str(prefix),
            "--launch-agents", str(agents),
            "--endpoint", "https://synthetic.invalid",
            "--host-id", "synthetic-primary-health",
            "--keychain-service", "synthetic.reference",
            "--visibility", "private",
            "--privacy-mode", "scrub",
            "--sources", "claude,codex,cowork",
            "--claude-root", str(claude),
            "--codex-root", str(codex),
            "--cowork-root", str(cowork),
            "--export-inbox", str(inbox),
            "--no-load",
        ])
        installed = True
        if str(root) in install.stdout:
            raise AssertionError("private workspace path was rendered")

        expected = {
            "claude": ("collect", "claude:mac:synthetic-primary-health"),
            "codex": ("collect", "codex:mac:synthetic-primary-health"),
            "cowork": ("cowork-local-sync", "cowork:mac:synthetic-primary-health"),
            "chatgpt-export": (
                "export-inbox-sync",
                "chatgpt-export:mac:synthetic-primary-health",
            ),
        }
        spools = set()
        for label, (command, source_id) in expected.items():
            with (agents / f"ai.parcha.recall.{label}.plist").open("rb") as source:
                value = plistlib.load(source)
            arguments = value["ProgramArguments"]
            assert value["RunAtLoad"] is True
            assert value["StartInterval"] == 30
            assert arguments[3] == command
            assert arguments[arguments.index("--source-id") + 1] == source_id
            spool = arguments[arguments.index("--spool") + 1]
            assert spool not in spools
            spools.add(spool)
            if command == "collect":
                assert arguments[arguments.index("--max-scan-records") + 1] == "1000"
                assert arguments[arguments.index("--max-scan-seconds") + 1] == "20"

        state(
            prefix / "state/claude.db",
            {"last_scan_at": "190", "last_scan_complete": "0"},
            pending=1,
        )
        state(
            prefix / "state/codex.db",
            {"last_scan_at": "190", "last_success_epoch": "195"},
        )
        state(
            prefix / "state/cowork.db",
            {
                "committed_cursor": "null",
                "last_success_epoch": "180",
                "last_error_code": "brain_unauthorized",
            },
            pending=1,
        )
        state(
            prefix / "state/chatgpt-export-runner.db",
            {"committed_cursor": "null", "last_success_epoch": "198"},
        )
        wrapper = prefix / "bin/recall-brain"
        status = json.loads(run([
            str(wrapper), "mac-status",
            "--prefix", str(prefix),
            "--launch-agents", str(agents),
            "--now", "200",
        ]).stdout)
        assert status["enabled"] == 4
        assert status["sources"]["claude-code"]["health"] == "backfilling"
        assert status["sources"]["claude-code"]["pending_count"] == 1
        assert status["sources"]["codex"]["health"] == "ready"
        assert status["sources"]["codex"]["last_ack_age_seconds"] == 5
        assert status["sources"]["cowork"]["health"] == "degraded"
        assert (
            status["sources"]["cowork"]["remediation"]
            == "rotate_brain_authority"
        )
        assert status["sources"]["chatgpt-export"]["health"] == "ready"
        assert str(root) not in json.dumps(status)

        support = json.loads(run([
            str(wrapper), "mac-support",
            "--prefix", str(prefix),
            "--launch-agents", str(agents),
        ]).stdout)
        assert support["package_integrity"]["status"] == "verified"
        assert support["package_integrity"]["mismatches"] == 0

        run([
            str(args.bundle_root / "uninstall.sh"),
            "--prefix", str(prefix),
            "--launch-agents", str(agents),
            "--delete-state", "--no-load",
        ])
        installed = False
        assert not prefix.exists()
        print(json.dumps({
            "architecture": "Darwin-arm64",
            "bounded_collectors": 2,
            "independent_schedules": 4,
            "package_integrity": "verified",
            "private_values_rendered": 0,
            "source_health_classes": 3,
            "state_residue": 0,
            "status": "pass",
        }, sort_keys=True))
    finally:
        if installed:
            subprocess.run([
                str(args.bundle_root / "uninstall.sh"),
                "--prefix", str(prefix),
                "--launch-agents", str(agents),
                "--delete-state", "--no-load",
            ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
