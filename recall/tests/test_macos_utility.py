from __future__ import annotations

import json
import plistlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from client.cli import parser
from client.macos_utility import SOURCE_SPECS, disable_source, mac_status


class MacUtilityLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / "adversarial-private-home-CANARY"
        self.prefix = self.home / "private-install-CREDENTIAL-CANARY"
        self.agents = self.home / "private-agents-PATH-CANARY"
        (self.prefix / "state").mkdir(parents=True)
        self.agents.mkdir(parents=True)

    def _state(self, name: str, metadata: dict[str, str]) -> None:
        path = self.prefix / "state" / SOURCE_SPECS[name].spool_name
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        connection.executemany("INSERT INTO meta(key,value) VALUES (?,?)", metadata.items())
        connection.commit()
        connection.close()

    def _enable(self, name: str) -> None:
        spec = SOURCE_SPECS[name]
        with (self.agents / f"{spec.label}.plist").open("wb") as output:
            plistlib.dump({"ProgramArguments": [
                "synthetic-runtime", "-m", "client.cli", "synthetic-command",
                "--privacy-mode", "scrub",
            ]}, output)

    def test_status_is_closed_content_free_and_reports_health_lag_checkpoint(self) -> None:
        self.assertEqual(
            list(SOURCE_SPECS),
            [
                "claude-code", "codex", "cowork", "chatgpt-export",
                "imessage", "whatsapp", "selected-text", "safari", "chrome",
                "apple-notes", "hermes",
            ],
        )
        self._enable("claude-code")
        self._enable("cowork")
        self._state("claude-code", {"last_scan_at": "190"})
        self._state("cowork", {
            "committed_cursor": json.dumps("opaque"),
            "last_success_epoch": "195",
            "last_error_code": "brain_unavailable",
        })

        result = mac_status(prefix=self.prefix, launch_agents=self.agents, now=200)

        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["mode"], "mac-status")
        self.assertEqual(result["source_classes"], list(SOURCE_SPECS))
        self.assertEqual(result["sources"]["claude-code"], {
            "enabled": True, "health": "ready", "lag_seconds": 10,
            "checkpointed": True, "state_present": True,
            "privacy_mode": "scrub", "surface": "claude-code-project-jsonl",
        })
        self.assertEqual(result["sources"]["cowork"], {
            "enabled": True, "health": "degraded", "lag_seconds": 5,
            "checkpointed": True, "state_present": True,
            "privacy_mode": "scrub", "surface": "claude-cowork-project-jsonl",
        })
        self.assertEqual(result["sources"]["codex"]["health"], "disabled")
        self.assertEqual(
            result["sources"]["codex"]["surface"],
            "chatgpt-codex-desktop-rollouts",
        )
        self.assertEqual(
            result["sources"]["imessage"]["surface"],
            "apple-imessage-read-only-snapshot",
        )
        self.assertEqual(
            result["sources"]["whatsapp"]["surface"],
            "whatsapp-selected-text-export",
        )
        self.assertEqual(
            result["sources"]["selected-text"]["surface"],
            "selected-markdown-obsidian-root",
        )
        self.assertEqual(
            result["sources"]["hermes"]["surface"],
            "hermes-session-schema-v22",
        )
        rendered = json.dumps(result, sort_keys=True)
        for forbidden in (str(self.home), "CANARY", "CREDENTIAL", "PATH"):
            self.assertNotIn(forbidden, rendered)

    def test_status_maps_symlink_corruption_and_private_errors_to_closed_codes(self) -> None:
        target = self.home / "outside-private-target"
        target.write_text("synthetic")
        spec = SOURCE_SPECS["codex"]
        (self.agents / f"{spec.label}.plist").symlink_to(target)
        (self.prefix / "state" / spec.spool_name).write_bytes(b"private malformed bytes CANARY")

        result = mac_status(prefix=self.prefix, launch_agents=self.agents, now=200)

        self.assertEqual(result["sources"]["codex"]["health"], "invalid_local_state")
        self.assertNotIn("CANARY", json.dumps(result))
        self.assertNotIn(str(target), json.dumps(result))

    def test_per_source_disable_removes_only_agent_and_preserves_all_state(self) -> None:
        for name in SOURCE_SPECS:
            self._enable(name)
            self._state(name, {"last_scan_at": "190"})
        before = {path.name: path.read_bytes() for path in (self.prefix / "state").iterdir()}

        result = disable_source("cowork", launch_agents=self.agents, no_load=True)

        self.assertEqual(result, {
            "schema_version": 1, "mode": "mac-disable", "source": "cowork",
            "enabled": False, "state_retained": True,
        })
        self.assertFalse((self.agents / f"{SOURCE_SPECS['cowork'].label}.plist").exists())
        self.assertTrue((self.agents / f"{SOURCE_SPECS['codex'].label}.plist").exists())
        after = {path.name: path.read_bytes() for path in (self.prefix / "state").iterdir()}
        self.assertEqual(after, before)

    def test_disable_uses_the_fixed_system_launchctl_binary(self) -> None:
        self._enable("cowork")
        with mock.patch("client.macos_utility.subprocess.run", return_value=mock.Mock(returncode=1)) as run:
            disable_source("cowork", launch_agents=self.agents)
        self.assertEqual(run.call_args_list[0].args[0][0], "/bin/launchctl")
        self.assertEqual(run.call_args_list[1].args[0][0], "/bin/launchctl")

    def test_cli_has_safe_defaults_and_closed_source_choices(self) -> None:
        cowork = parser().parse_args([
            "cowork-local-sync", "--endpoint", "https://brain.example.invalid:9443",
            "--source-id", "cowork:mac:synthetic", "--keychain-service", "synthetic",
            "--keychain-account", "cowork:mac:synthetic", "--root", "/synthetic/root",
            "--spool", "/synthetic/state.db",
        ])
        self.assertEqual(cowork.privacy_mode, "scrub")
        self.assertEqual(cowork.visibility, "private")
        status = parser().parse_args(["mac-status"])
        self.assertTrue(status.prefix.endswith("RecallBrain"))
        self.assertTrue(status.launch_agents.endswith("LaunchAgents"))
        disabled = parser().parse_args(["mac-disable", "--source", "cowork", "--no-load"])
        self.assertEqual(disabled.source, "cowork")
        with self.assertRaises(SystemExit):
            parser().parse_args([
                "cowork-local-sync", "--endpoint", "https://brain.example.invalid:9443",
                "--source-id", "cowork:mac:synthetic", "--keychain-service", "synthetic",
                "--keychain-account", "cowork:mac:synthetic", "--root", "/synthetic/root",
                "--spool", "/synthetic/state.db", "--privacy-mode", "off",
            ])


if __name__ == "__main__":
    unittest.main()
