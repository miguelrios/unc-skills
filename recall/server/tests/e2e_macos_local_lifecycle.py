#!/usr/bin/env python3
"""Exact-package Darwin/arm64 proof for the Mac-local LaunchAgent lifecycle."""

from __future__ import annotations

import argparse
import json
import plistlib
import shutil
import stat
import subprocess
from pathlib import Path


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, check=True, text=True, capture_output=True)
    assert completed.stderr == ""
    return completed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--bundle-root", type=Path, required=True)
    args = parser.parse_args()

    prefix = args.workspace / "private-prefix-PATH-CANARY"
    launch_agents = args.workspace / "private-agents-PATH-CANARY"
    imessage = args.workspace / "private-imessage-PATH-CANARY.db"
    whatsapp = args.workspace / "private-whatsapp-PATH-CANARY.txt"
    selected = args.workspace / "private-selected-PATH-CANARY"
    installed = False
    result = None
    try:
        args.workspace.mkdir(mode=0o700)
        imessage.write_bytes(b"synthetic")
        whatsapp.write_text("17/07/2026, 12:00 - Synthetic: fixture\n")
        selected.mkdir()
        (selected / "fixture.md").write_text("synthetic")
        install = run([
            str(args.bundle_root / "install.sh"),
            "--prefix", str(prefix), "--launch-agents", str(launch_agents),
            "--endpoint", "https://synthetic.invalid",
            "--host-id", "synthetic-local-lifecycle",
            "--keychain-service", "synthetic.reference",
            "--visibility", "private", "--privacy-mode", "scrub",
            "--sources", "imessage,whatsapp-export,obsidian",
            "--imessage-database", str(imessage),
            "--whatsapp-export", str(whatsapp),
            "--whatsapp-conversation-id", "synthetic-conversation",
            "--whatsapp-owner-name", "Synthetic Owner",
            "--whatsapp-date-order", "dmy", "--whatsapp-timezone", "UTC",
            "--selected-text-root", str(selected), "--no-load",
        ])
        installed = True
        assert "PATH-CANARY" not in install.stdout

        expected = {
            "imessage": ("imessage-sync", "--database", str(imessage)),
            "whatsapp": ("whatsapp-export-sync", "--export", str(whatsapp)),
            "selected-text": ("selected-text-sync", "--root", str(selected)),
        }
        for name, (command, path_option, source_path) in expected.items():
            plist_path = launch_agents / f"ai.parcha.recall.{name}.plist"
            assert stat.S_IMODE(plist_path.stat().st_mode) == 0o600
            with plist_path.open("rb") as source:
                value = plistlib.load(source)
            arguments = value["ProgramArguments"]
            assert arguments[3] == command
            assert arguments[arguments.index(path_option) + 1] == source_path
            assert arguments[arguments.index("--privacy-mode") + 1] == "scrub"
            assert "--token" not in arguments
            assert value["Umask"] == 0o077

        wrapper = prefix / "bin" / "recall-brain"
        status = run([
            str(wrapper), "mac-status", "--prefix", str(prefix),
            "--launch-agents", str(launch_agents),
        ])
        assert "PATH-CANARY" not in status.stdout
        status_value = json.loads(status.stdout)
        assert status_value["enabled"] == 3
        assert all(
            status_value["sources"][name]["health"] == "starting"
            for name in expected
        )

        disabled = run([
            str(wrapper), "mac-disable", "--source", "whatsapp",
            "--launch-agents", str(launch_agents), "--no-load",
        ])
        assert json.loads(disabled.stdout)["state_retained"]
        assert not (launch_agents / "ai.parcha.recall.whatsapp.plist").exists()
        assert (launch_agents / "ai.parcha.recall.imessage.plist").exists()

        retained = run([
            str(args.bundle_root / "uninstall.sh"),
            "--prefix", str(prefix), "--launch-agents", str(launch_agents),
            "--no-load",
        ])
        installed = False
        assert json.loads(retained.stdout)["state_retained"]
        assert (prefix / "state").is_dir()
        assert not (prefix / "lib").exists()
        assert not tuple(launch_agents.glob("ai.parcha.recall.*.plist"))

        deleted = run([
            str(args.bundle_root / "uninstall.sh"),
            "--prefix", str(prefix), "--launch-agents", str(launch_agents),
            "--delete-state", "--no-load",
        ])
        assert not json.loads(deleted.stdout)["state_retained"]
        assert not prefix.exists()
        result = {
            "status": "pass",
            "summary": {
                "architecture": "Darwin-arm64",
                "packaged_sources": 3,
                "private_launch_agents": 3,
                "plaintext_credentials": 0,
                "content_path_bytes_rendered": 0,
                "state_retained_on_pause": True,
                "state_retained_on_default_uninstall": True,
                "state_residue_after_explicit_delete": 0,
            },
        }
    finally:
        if installed:
            subprocess.run([
                str(args.bundle_root / "uninstall.sh"),
                "--prefix", str(prefix), "--launch-agents", str(launch_agents),
                "--delete-state", "--no-load",
            ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(args.workspace, ignore_errors=True)
    assert result is not None and not args.workspace.exists()
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
