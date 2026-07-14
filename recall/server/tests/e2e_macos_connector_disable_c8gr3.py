#!/usr/bin/env python3
"""Content-free Darwin proof for explicit connector-supervisor disable."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import time
from pathlib import Path


LABEL = "ai.parcha.recall.connector-supervisor"


def private_file(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(value)


def target() -> str:
    return f"gui/{os.getuid()}/{LABEL}"


def service_present() -> bool:
    return subprocess.run(
        ["launchctl", "print", target()], capture_output=True,
    ).returncode == 0


def wait_until(operation, *, timeout: float = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if operation():
            return
        time.sleep(0.1)
    raise AssertionError("bounded launchd condition did not converge")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--bundle-root", type=Path, required=True)
    args = parser.parse_args()

    root = args.workspace
    private = root / "private"
    inbox = root / "inbox"
    prefix = root / "installed"
    launch_agents = root / "launch-agents"
    private.mkdir(parents=True, mode=0o700)
    inbox.mkdir(mode=0o700)
    authority = private / "brain.json"
    config_path = private / "host.json"
    private_file(authority, json.dumps({"token": "synthetic-disable-brain"}))
    config = {
        "schema_version": 1,
        "jobs": [{
            "schedule": {
                "schema_version": 1,
                "job_key": "3" * 64,
                "connector_id": "openai.export-inbox",
                "generation": 1,
                "enabled": False,
                "interval_seconds": 2,
                "jitter_seconds": 0,
                "transient_base_seconds": 2,
                "max_backoff_seconds": 20,
                "lease_seconds": 30,
                "max_rate_limit_seconds": 60,
            },
            "source_id": "chatgpt:synthetic:disable-proof",
            "endpoint": "http://127.0.0.1:9",
            "brain_authority": {"kind": "file", "path": str(authority)},
            "privacy_mode": "scrub",
            "connector": {
                "inbox": str(inbox),
                "catalog": str(prefix / "state/export-catalog.db"),
                "spool": str(prefix / "state/export.db"),
                "page_size": 100,
            },
        }],
    }
    private_file(config_path, json.dumps(config, sort_keys=True, separators=(",", ":")))
    plist_path = launch_agents / f"{LABEL}.plist"
    state_path = prefix / "state/connector-supervisor.db"
    installed = False
    try:
        subprocess.run([
            str(args.bundle_root / "install.sh"),
            "--prefix", str(prefix),
            "--launch-agents", str(launch_agents),
            "--connector-supervisor-config", str(config_path),
        ], check=True, text=True, capture_output=True)
        installed = True
        wait_until(service_present)
        wait_until(state_path.is_file)
        plist = plistlib.loads(plist_path.read_bytes())
        assert plist["Label"] == LABEL

        disabled = subprocess.run([
            str(args.bundle_root / "install.sh"),
            "--prefix", str(prefix),
            "--launch-agents", str(launch_agents),
            "--disable-connector-supervisor",
        ], text=True, capture_output=True)
        facts = {
            "disable_exit_zero": disabled.returncode == 0,
            "disable_stderr_empty": disabled.stderr == "",
            "service_absent": not service_present(),
            "plist_absent": not plist_path.exists(),
            "state_retained": state_path.is_file(),
        }
        print(json.dumps(facts, sort_keys=True))
        assert all(facts.values())
    finally:
        if installed:
            subprocess.run([
                str(args.bundle_root / "uninstall.sh"),
                "--prefix", str(prefix),
                "--launch-agents", str(launch_agents),
            ], check=True, text=True, capture_output=True)
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
