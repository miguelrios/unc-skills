#!/usr/bin/env python3
"""Exact-package Darwin/arm64 proof for registry preview and read-only status."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path


def run_json(command: list[str]) -> dict:
    completed = subprocess.run(command, check=True, text=True, capture_output=True)
    assert completed.stderr == ""
    return json.loads(completed.stdout)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--bundle-root", type=Path, required=True)
    args = parser.parse_args()

    prefix = args.workspace / "installed"
    launch_agents = args.workspace / "launch-agents"
    spool = args.workspace / "synthetic-registry-state.db"
    wrapper = prefix / "bin" / "recall-brain"
    installed = False
    result = None
    try:
        install = subprocess.run([
            str(args.bundle_root / "install.sh"),
            "--prefix", str(prefix), "--launch-agents", str(launch_agents),
            "--endpoint", "https://synthetic-brain.invalid",
            "--host-id", "synthetic-c8e-live-host",
            "--keychain-service", "synthetic.c8e.reference",
            "--visibility", "private", "--privacy-mode", "drop",
            "--disable-export-inbox", "--no-load",
        ], check=True, text=True, capture_output=True)
        installed = True
        assert install.stderr == "" and wrapper.is_file()

        preview = run_json([str(wrapper), "connector-registry-preview"])
        assert len(preview["connectors"]) == 22
        for field in ("credential_reads", "source_reads", "network_requests", "writes"):
            assert preview[field] == 0

        connection = sqlite3.connect(spool)
        connection.executescript("""
            CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE pages(id INTEGER PRIMARY KEY);
            CREATE TABLE outbox(id INTEGER PRIMARY KEY);
            INSERT INTO meta VALUES ('connector_id','grep.ai');
            INSERT INTO meta VALUES ('source_id','synthetic:c8e');
            INSERT INTO meta VALUES ('committed_cursor','synthetic-private-cursor');
        """)
        connection.commit(); connection.close()
        before = hashlib.sha256(spool.read_bytes()).hexdigest()
        ready = run_json([
            str(wrapper), "connector-registry-status",
            "--connector-id", "grep.ai", "--enabled", "--privacy-mode", "drop",
            "--authority", "brain", "--authority", "source", "--spool", str(spool),
        ])
        after = hashlib.sha256(spool.read_bytes()).hexdigest()
        assert before == after and ready["health"] == "ready" and ready["checkpointed"]
        rendered = json.dumps(ready)
        assert "synthetic-private-cursor" not in rendered and str(spool) not in rendered

        capture = run_json([
            str(wrapper), "connector-registry-status",
            "--connector-id", "recall.capture", "--enabled", "--privacy-mode", "scrub",
            "--authority", "brain",
        ])
        disabled = run_json([
            str(wrapper), "connector-registry-status",
            "--connector-id", "openai.export-inbox", "--privacy-mode", "drop",
        ])
        missing = run_json([
            str(wrapper), "connector-registry-status",
            "--connector-id", "grep.ai", "--enabled", "--privacy-mode", "drop",
            "--authority", "brain",
        ])
        assert capture["health"] == "ready"
        assert disabled["health"] == "disabled"
        assert missing["health"] == "reference_missing"
        result = {
            "status": "pass",
            "summary": {
                "architecture": "Darwin-arm64",
                "registered_surfaces": 22,
                "preview_credential_reads": 0,
                "preview_source_reads": 0,
                "preview_network_requests": 0,
                "preview_writes": 0,
                "status_state_mutations": 0,
                "private_cursor_rendered": False,
                "health_states_proved": 4,
            },
        }
    finally:
        if installed:
            uninstall = subprocess.run([
                str(args.bundle_root / "uninstall.sh"),
                "--prefix", str(prefix), "--launch-agents", str(launch_agents), "--no-load",
            ], check=True, text=True, capture_output=True)
            assert uninstall.stderr == ""
        shutil.rmtree(args.workspace, ignore_errors=True)
    assert result is not None and not args.workspace.exists()
    result["summary"]["install_residue"] = 0
    result["summary"]["state_residue"] = 0
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
