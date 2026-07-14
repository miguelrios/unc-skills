#!/usr/bin/env python3
"""Exact-package Darwin/arm64 proof for deterministic supervisor mechanics."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path


KEY_A = "1" * 64
KEY_B = "2" * 64
KEY_C = "3" * 64


def run_json(command: list[str], *, env=None) -> dict:
    completed = subprocess.run(command, check=True, text=True, capture_output=True, env=env)
    assert completed.stderr == ""
    return json.loads(completed.stdout)


def definition(schedule_type, key: str, connector_id: str, *, lease: int = 20):
    return schedule_type.from_mapping({
        "schema_version": 1,
        "job_key": key,
        "connector_id": connector_id,
        "generation": 1,
        "enabled": True,
        "interval_seconds": 100,
        "jitter_seconds": 0,
        "transient_base_seconds": 5,
        "max_backoff_seconds": 20,
        "lease_seconds": lease,
        "max_rate_limit_seconds": 60,
    })


def prove(workspace: Path) -> dict:
    from connectors.supervisor import (
        ConnectorSupervisor,
        ScheduleDefinition,
        ScheduledJob,
        SupervisorStore,
        aggregate_supervisor_status,
    )

    state = workspace / "supervisor.db"
    item_a = definition(ScheduleDefinition, KEY_A, "grep.ai")
    item_b = definition(ScheduleDefinition, KEY_B, "openai.export-inbox")
    calls = {"a": 0, "b": 0, "c": 0}

    def transient_then_success():
        calls["a"] += 1
        if calls["a"] == 1:
            raise RuntimeError("synthetic private exception text")
        return {"status": "committed"}

    first_store = SupervisorStore(state)
    first = ConnectorSupervisor(first_store, jitter=lambda *_: 0).tick((
        ScheduledJob(item_a, transient_then_success),
        ScheduledJob(item_b, lambda: calls.__setitem__("b", calls["b"] + 1) or {"status": "committed"}),
    ), now=0)
    first_store.close()
    assert first["outcomes"] == {"success": 1, "transient": 1}
    assert calls == {"a": 1, "b": 1, "c": 0}

    recovered_store = SupervisorStore(state)
    recovered = ConnectorSupervisor(recovered_store, jitter=lambda *_: 0)
    second = recovered.tick((
        ScheduledJob(item_a, transient_then_success),
        ScheduledJob(item_b, lambda: calls.__setitem__("b", calls["b"] + 1) or {"status": "committed"}),
    ), now=5)
    assert second["outcomes"] == {"success": 1}
    assert calls == {"a": 2, "b": 1, "c": 0}

    item_c = definition(ScheduleDefinition, KEY_C, "grep.ai", lease=2)
    recovered_store.reconcile(item_c, now=5)
    assert recovered_store.acquire(item_c, now=5, lease_token="f" * 64)
    recovered_store.close()

    expired_store = SupervisorStore(state)
    expired = ConnectorSupervisor(expired_store, jitter=lambda *_: 0)
    third = expired.tick((
        ScheduledJob(item_a, transient_then_success),
        ScheduledJob(item_b, lambda: {"status": "committed"}),
        ScheduledJob(item_c, lambda: calls.__setitem__("c", calls["c"] + 1) or {"status": "committed"}),
    ), now=7)
    assert third["outcomes"] == {"success": 1}
    assert calls["c"] == 1
    expired_store.close()

    status = aggregate_supervisor_status(state, now=8)
    rendered = json.dumps(status, sort_keys=True)
    assert status["jobs"] == 3
    assert status["outcomes"]["success"] == 3
    assert all(key not in rendered for key in (KEY_A, KEY_B, KEY_C))
    assert "synthetic private exception text" not in state.read_bytes().decode(errors="ignore")
    assert state.stat().st_mode & 0o777 == 0o600
    return {
        "status": "pass",
        "summary": {
            "architecture": f"{platform.system()}-{platform.machine()}",
            "deterministic_ticks": 3,
            "isolated_progress_after_failure": 1,
            "durable_backoff_resume": 1,
            "expired_lease_recovery": 1,
            "duplicate_active_leases": 0,
            "private_exception_text_rendered": False,
            "state_mode": "0600",
            "status_jobs": status["jobs"],
            "status_mutations": 0,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--phase", choices=("install", "prove"), default="install")
    args = parser.parse_args()
    if args.phase == "prove":
        print(json.dumps(prove(args.workspace), sort_keys=True))
        return

    prefix = args.workspace / "installed"
    launch_agents = args.workspace / "launch-agents"
    wrapper = prefix / "bin" / "recall-brain"
    installed = False
    result = None
    try:
        install = subprocess.run([
            str(args.bundle_root / "install.sh"),
            "--prefix", str(prefix), "--launch-agents", str(launch_agents),
            "--endpoint", "https://synthetic-brain.invalid",
            "--host-id", "synthetic-c8f-live-host",
            "--keychain-service", "synthetic.c8f.reference",
            "--visibility", "private", "--privacy-mode", "drop",
            "--disable-export-inbox", "--no-load",
        ], check=True, text=True, capture_output=True)
        installed = True
        assert install.stderr == "" and wrapper.is_file()
        preview = run_json([str(wrapper), "connector-supervisor-preview"])
        for field in ("credential_reads", "source_reads", "network_requests", "writes"):
            assert preview[field] == 0

        os.chmod(args.workspace, 0o700)
        env = {**os.environ, "PYTHONPATH": str(prefix / "lib")}
        result = run_json([
            str(prefix / "runtime" / "bin" / "python3"), str(Path(__file__).resolve()),
            "--phase", "prove", "--workspace", str(args.workspace),
            "--bundle-root", str(args.bundle_root),
        ], env=env)
        state = args.workspace / "supervisor.db"
        before = state.read_bytes()
        status = run_json([
            str(wrapper), "connector-supervisor-status",
            "--state", str(state), "--now", "8",
        ])
        after = state.read_bytes()
        assert before == after
        assert status["jobs"] == 3 and status["outcomes"]["success"] == 3
        rendered = json.dumps(status)
        assert all(key not in rendered for key in (KEY_A, KEY_B, KEY_C))
        result["summary"]["preview_credential_reads"] = 0
        result["summary"]["preview_source_reads"] = 0
        result["summary"]["preview_network_requests"] = 0
        result["summary"]["preview_writes"] = 0
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
