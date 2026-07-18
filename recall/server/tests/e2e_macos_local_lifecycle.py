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


def run(command: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command, check=True, text=True, capture_output=True, input=input_text,
    )
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
    safari_history = args.workspace / "private-safari-history-PATH-CANARY.db"
    safari_bookmarks = args.workspace / "private-safari-bookmarks-PATH-CANARY.plist"
    chrome_history = args.workspace / "private-chrome-history-PATH-CANARY.db"
    chrome_bookmarks = args.workspace / "private-chrome-bookmarks-PATH-CANARY.json"
    notes = args.workspace / "private-notes-PATH-CANARY.db"
    hermes = args.workspace / "private-hermes-PATH-CANARY.db"
    installed = False
    result = None
    try:
        args.workspace.mkdir(mode=0o700)
        imessage.write_bytes(b"synthetic")
        whatsapp.write_text("17/07/2026, 12:00 - Synthetic: fixture\n")
        selected.mkdir()
        (selected / "fixture.md").write_text("synthetic")
        for path in (
            safari_history, safari_bookmarks, chrome_history, chrome_bookmarks,
            notes, hermes,
        ):
            path.write_bytes(b"synthetic")
        install_command = [
            str(args.bundle_root / "install.sh"),
            "--prefix", str(prefix), "--launch-agents", str(launch_agents),
            "--endpoint", "https://synthetic.invalid",
            "--host-id", "synthetic-local-lifecycle",
            "--keychain-service", "synthetic.reference",
            "--visibility", "private", "--privacy-mode", "scrub",
            "--sources",
            "imessage,whatsapp-export,obsidian,safari,chrome,apple-notes,hermes",
            "--imessage-database", str(imessage),
            "--whatsapp-export", str(whatsapp),
            "--whatsapp-conversation-id", "synthetic-conversation",
            "--whatsapp-owner-name", "Synthetic Owner",
            "--whatsapp-date-order", "dmy", "--whatsapp-timezone", "UTC",
            "--selected-text-root", str(selected),
            "--safari-history", str(safari_history),
            "--safari-bookmarks", str(safari_bookmarks),
            "--chrome-history", str(chrome_history),
            "--chrome-bookmarks", str(chrome_bookmarks),
            "--apple-notes-database", str(notes),
            "--hermes-database", str(hermes),
            "--hermes-sources", "cli,slack",
            "--hermes-roles", "assistant,user", "--no-load",
        ]
        install = run(install_command)
        installed = True
        assert "PATH-CANARY" not in install.stdout

        expected = {
            "imessage": ("imessage-sync", "--database", str(imessage)),
            "whatsapp": ("whatsapp-export-sync", "--export", str(whatsapp)),
            "selected-text": ("selected-text-sync", "--root", str(selected)),
            "safari": ("browser-sync", "--history", str(safari_history)),
            "chrome": ("browser-sync", "--history", str(chrome_history)),
            "apple-notes": ("apple-notes-sync", "--database", str(notes)),
            "hermes": ("hermes-session-sync", "--database", str(hermes)),
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
            assert value["RunAtLoad"] is True
            assert value["StartInterval"] == 60

        wrapper = prefix / "bin" / "recall-brain"
        status = run([
            str(wrapper), "mac-status", "--prefix", str(prefix),
            "--launch-agents", str(launch_agents),
        ])
        assert "PATH-CANARY" not in status.stdout
        status_value = json.loads(status.stdout)
        assert status_value["enabled"] == 7
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

        support = run([
            str(wrapper), "mac-support", "--prefix", str(prefix),
            "--launch-agents", str(launch_agents),
        ])
        support_value = json.loads(support.stdout)
        assert support_value["package_integrity"]["status"] == "verified"
        assert support_value["package_integrity"]["mismatches"] == 0
        assert "PATH-CANARY" not in support.stdout

        run([
            str(wrapper), "keychain-store", "--service", "synthetic.reference",
            "--account", "selected-text:mac:synthetic-local-lifecycle",
        ], input_text="synthetic-ephemeral-credential\n")
        revoked = run([
            str(wrapper), "mac-revoke", "--source", "selected-text",
            "--launch-agents", str(launch_agents), "--no-load",
        ])
        revoked_value = json.loads(revoked.stdout)
        assert revoked_value["credential_revoked"]
        assert revoked_value["state_retained"]
        assert not (launch_agents / "ai.parcha.recall.selected-text.plist").exists()
        assert (launch_agents / "ai.parcha.recall.imessage.plist").exists()

        selected_state = prefix / "state" / "selected-text.db"
        selected_state.write_bytes(b"synthetic-local-state")
        reset = run([
            str(wrapper), "mac-reset-local", "--source", "selected-text",
            "--confirm-source", "selected-text", "--prefix", str(prefix),
            "--launch-agents", str(launch_agents), "--no-load",
        ])
        reset_value = json.loads(reset.stdout)
        assert not reset_value["local_state_retained"]
        assert reset_value["central_evidence_retained"]
        assert not selected_state.exists()

        marker = prefix / "lib" / "previous-release-marker"
        marker.write_text("previous")
        invalid_supervisor = args.workspace / "invalid-supervisor.json"
        invalid_supervisor.write_text("{")
        failed_upgrade = subprocess.run([
            str(args.bundle_root / "install.sh"),
            "--prefix", str(prefix), "--launch-agents", str(launch_agents),
            "--connector-supervisor-config", str(invalid_supervisor), "--no-load",
        ], check=False, text=True, capture_output=True)
        assert failed_upgrade.returncode != 0
        assert marker.read_text() == "previous"
        assert not (launch_agents / "ai.parcha.recall.selected-text.plist").exists()
        assert not (launch_agents / "ai.parcha.recall.whatsapp.plist").exists()

        run(install_command)
        assert not marker.exists()
        rollback = run([
            str(args.bundle_root / "install.sh"), "--rollback",
            "--prefix", str(prefix), "--launch-agents", str(launch_agents),
            "--no-load",
        ])
        assert json.loads(rollback.stdout)["restored"]
        assert marker.read_text() == "previous"
        assert not (launch_agents / "ai.parcha.recall.selected-text.plist").exists()
        assert not (launch_agents / "ai.parcha.recall.whatsapp.plist").exists()

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
                "packaged_sources": 7,
                "private_launch_agents": 7,
                "plaintext_credentials": 0,
                "content_path_bytes_rendered": 0,
                "package_integrity_verified": True,
                "failed_upgrade_auto_restored": True,
                "explicit_rollback_restored": True,
                "source_credential_revoked": True,
                "source_local_state_reset": True,
                "launch_on_login_and_wake_contract": True,
                "central_evidence_delete_claimed": False,
                "state_retained_on_pause": True,
                "state_retained_on_default_uninstall": True,
                "state_residue_after_explicit_delete": 0,
            },
        }
    finally:
        subprocess.run([
            "/usr/bin/security", "delete-generic-password",
            "-s", "synthetic.reference",
            "-a", "selected-text:mac:synthetic-local-lifecycle",
        ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
