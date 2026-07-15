from __future__ import annotations

import json
import plistlib
import shutil
import tempfile
import unittest
from pathlib import Path

from client.local_surfaces import (
    LocalSurfaceError,
    mac_claude_surface_preview,
    mac_local_surface_preview,
)


FIXTURES = Path(__file__).parent / "local_surfaces_v1"


class MacLocalSurfacePreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = self.root / "ChatGPT.app"
        info = self.app / "Contents" / "Info.plist"
        info.parent.mkdir(parents=True)
        with info.open("wb") as output:
            plistlib.dump({
                "CFBundleDisplayName": "ChatGPT",
                "CFBundleIdentifier": "com.openai.codex",
                "CFBundleShortVersionString": "synthetic-version-canary",
                "CFBundleURLTypes": [{"CFBundleURLSchemes": ["codex"]}],
            }, output)
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()

    @staticmethod
    def write_rollout(path: Path, originator: str, marker: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join([
            json.dumps({
                "timestamp": "2026-07-15T00:00:00Z",
                "type": "session_meta",
                "payload": {"originator": originator, "cwd": "/synthetic/private-path-canary"},
            }),
            json.dumps({
                "timestamp": "2026-07-15T00:00:01Z",
                "type": "response_item",
                "payload": {"role": "user", "content": marker},
            }),
            "",
        ]))

    def test_preview_classifies_current_chatgpt_app_as_codex_desktop_without_content(self) -> None:
        marker = "synthetic-private-message-canary"
        desktop = self.sessions / "2026" / "rollout-desktop.jsonl"
        desktop.parent.mkdir(parents=True)
        shutil.copy(FIXTURES / "codex_desktop_rollout.jsonl", desktop)
        self.write_rollout(
            self.sessions / "2026" / "rollout-cli.jsonl", "codex_exec", "other-canary"
        )
        (self.sessions / "rollout-malformed.jsonl").write_text("not-json\n")
        outside = self.root / "rollout-outside.jsonl"
        self.write_rollout(outside, "Codex Desktop", "symlink-canary")
        (self.sessions / "rollout-alias.jsonl").symlink_to(outside)

        result = mac_local_surface_preview(app=self.app, codex_root=self.sessions)

        self.assertEqual(result, {
            "schema_version": 1,
            "mode": "mac-local-surface-preview",
            "network_requests": 0,
            "record_body_reads": 0,
            "app": {
                "installed": True,
                "display_class": "chatgpt",
                "runtime_family": "codex-desktop",
            },
            "codex_desktop": {
                "eligible_rollouts": 1,
                "other_rollouts": 1,
                "unreadable_files": 1,
                "unsafe_files": 1,
            },
            "consumer_chat_history": {
                "status": "not_claimed_by_codex_rollouts",
                "eligible_records": 0,
            },
        })
        rendered = json.dumps(result, sort_keys=True)
        for private in (marker, "other-canary", "symlink-canary", "private-path-canary",
                        "synthetic-version-canary", str(self.root)):
            self.assertNotIn(private, rendered)

    def test_preview_fails_closed_for_aliased_or_open_ended_roots(self) -> None:
        alias = self.root / "sessions-alias"
        alias.symlink_to(self.sessions, target_is_directory=True)
        with self.assertRaisesRegex(LocalSurfaceError, "root"):
            mac_local_surface_preview(app=self.app, codex_root=alias)

        with self.assertRaisesRegex(LocalSurfaceError, "root"):
            mac_local_surface_preview(app=self.app, codex_root=self.root / "missing")

    def test_unknown_app_never_claims_chatgpt_or_consumer_history(self) -> None:
        info = self.app / "Contents" / "Info.plist"
        with info.open("wb") as output:
            plistlib.dump({
                "CFBundleDisplayName": "private-display-canary",
                "CFBundleIdentifier": "example.private.bundle",
                "CFBundleURLTypes": [{"CFBundleURLSchemes": ["private-scheme-canary"]}],
            }, output)

        result = mac_local_surface_preview(app=self.app, codex_root=self.sessions)

        self.assertEqual(result["app"], {
            "installed": True,
            "display_class": "unknown",
            "runtime_family": "unknown",
        })
        self.assertEqual(result["consumer_chat_history"]["eligible_records"], 0)
        self.assertNotIn("private", json.dumps(result, sort_keys=True))


class MacClaudeSurfacePreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = self.root / "Claude.app"
        info = self.app / "Contents" / "Info.plist"
        info.parent.mkdir(parents=True)
        with info.open("wb") as output:
            plistlib.dump({
                "CFBundleDisplayName": "Claude",
                "CFBundleIdentifier": "com.anthropic.claudefordesktop",
                "CFBundleShortVersionString": "private-version-canary",
            }, output)
        self.support = self.root / "Claude"
        (self.support / "IndexedDB").mkdir(parents=True)
        (self.support / "Local Storage").mkdir()
        (self.support / "IndexedDB" / "private-store-canary.ldb").write_text(
            "private-record-body-canary"
        )
        projects = (
            self.support / "local-agent-mode-sessions" / "synthetic" /
            "project" / "local_one" / ".claude" / "projects" / "synthetic"
        )
        projects.mkdir(parents=True)
        (projects / "one.jsonl").write_text("private-cowork-body-canary\n")
        (projects / "two.jsonl").write_text("private-cowork-body-canary\n")

    def test_preview_separates_cowork_from_unsupported_ordinary_chat_without_bodies(self) -> None:
        result = mac_claude_surface_preview(app=self.app, support_root=self.support)

        self.assertEqual(result, {
            "schema_version": 1,
            "mode": "mac-claude-surface-preview",
            "network_requests": 0,
            "record_body_reads": 0,
            "app": {
                "installed": True,
                "display_class": "claude",
                "runtime_family": "claude-desktop",
            },
            "cowork": {
                "status": "supported_distinct_surface",
                "eligible_project_logs": 2,
                "unsafe_files": 0,
            },
            "ordinary_chat": {
                "status": "not_locally_supported_on_probed_install",
                "eligible_record_files": 0,
                "excluded_app_state_store_classes": 2,
            },
        })
        rendered = json.dumps(result, sort_keys=True)
        for private in (
            "private-store-canary", "private-record-body-canary",
            "private-cowork-body-canary", "private-version-canary", str(self.root),
        ):
            self.assertNotIn(private, rendered)

    def test_preview_counts_unsafe_cowork_alias_and_rejects_unsafe_root(self) -> None:
        projects = next(
            (self.support / "local-agent-mode-sessions").rglob("projects")
        ) / "synthetic"
        outside = self.root / "outside.jsonl"
        outside.write_text("private-alias-body-canary\n")
        (projects / "alias.jsonl").symlink_to(outside)

        result = mac_claude_surface_preview(app=self.app, support_root=self.support)
        self.assertEqual(result["cowork"]["eligible_project_logs"], 2)
        self.assertEqual(result["cowork"]["unsafe_files"], 1)

        alias = self.root / "support-alias"
        alias.symlink_to(self.support, target_is_directory=True)
        with self.assertRaisesRegex(LocalSurfaceError, "support_root_unsafe"):
            mac_claude_surface_preview(app=self.app, support_root=alias)


if __name__ == "__main__":
    unittest.main()
