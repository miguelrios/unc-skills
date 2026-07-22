from __future__ import annotations

import json
import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from connectors import workspace_rail
from connectors.workspace_rail import (
    GWS_RELEASE,
    WorkspaceRail,
    WorkspaceRailError,
    build_argv,
)


class WorkspaceRailContractTest(unittest.TestCase):
    def test_release_is_immutable_and_required_platforms_have_checksums(self):
        self.assertEqual(GWS_RELEASE.version, "0.22.5")
        self.assertEqual(
            GWS_RELEASE.tag_commit,
            "705fb0ecac6f4249679958f6325b809b63fdde17",
        )
        self.assertEqual(set(GWS_RELEASE.sha256), {
            "aarch64-apple-darwin",
            "aarch64-unknown-linux-gnu",
            "x86_64-unknown-linux-gnu",
        })
        self.assertTrue(all(len(value) == 64 for value in GWS_RELEASE.sha256.values()))

    def test_argv_is_exact_read_only_and_has_no_passthrough(self):
        argv = build_argv("gmail.history.list", {
            "userId": "me", "startHistoryId": "100", "maxResults": 50,
        })
        self.assertEqual(argv[:5], (
            "/opt/recall/vendor/gws/0.22.5/gws", "gmail", "users", "history", "list",
        ))
        self.assertEqual(argv[-2:], ("--format", "json"))
        self.assertEqual(json.loads(argv[6]), {
            "maxResults": 50, "startHistoryId": "100", "userId": "me",
        })
        attachment = build_argv("gmail.messages.attachments.get", {
            "userId": "me", "messageId": "message-1", "id": "body-part-1",
        })
        self.assertEqual(attachment[:7], (
            "/opt/recall/vendor/gws/0.22.5/gws", "gmail", "users", "messages",
            "attachments", "get", "--params",
        ))
        for operation, params in (
            ("gmail.messages.send", {"userId": "me"}),
            ("gmail.history.list", {"userId": "me", "shell": "synthetic"}),
            ("calendar.events.delete", {"calendarId": "primary", "eventId": "x"}),
            ("drive.files.export", {"fileId": "x", "mimeType": "text/plain", "output": "/tmp/x"}),
        ):
            with self.subTest(operation=operation), self.assertRaises(WorkspaceRailError):
                build_argv(operation, params)

    def test_process_has_minimal_environment_and_never_uses_a_shell(self):
        with tempfile.TemporaryDirectory() as directory:
            credential = Path(directory) / "google.json"
            credential.write_text("synthetic")
            os.chmod(credential, 0o600)
            completed = mock.Mock(returncode=0, stdout=b'{"history":[],"historyId":"101"}', stderr=b"")
            with mock.patch("connectors.workspace_rail._run_bounded", return_value=completed) as run:
                result = WorkspaceRail(credential_path=credential).run(
                    "gmail.history.list",
                    {"userId": "me", "startHistoryId": "100", "maxResults": 50},
                )
            self.assertEqual(result["historyId"], "101")
            kwargs = run.call_args.kwargs
            self.assertEqual(kwargs["environment"], {
                "GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE": str(credential),
                "NO_COLOR": "1",
            })
            self.assertLessEqual(kwargs["timeout_seconds"], 60)
            self.assertIn("shell=False", inspect.getsource(workspace_rail._run_bounded))
            self.assertIn("os.killpg", inspect.getsource(workspace_rail._stop_process_group))

    def test_credential_reference_is_revalidated_and_requires_a_private_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            credential = root / "google.json"
            credential.write_text("synthetic")
            os.chmod(credential, 0o600)
            rail = WorkspaceRail(credential_path=credential)
            os.chmod(credential, 0o644)
            with self.assertRaisesRegex(WorkspaceRailError, "credential_reference_invalid"):
                rail.run("gmail.history.list", {"userId": "me", "startHistoryId": "100"})
            os.chmod(credential, 0o600)
            os.chmod(root, 0o755)
            with self.assertRaisesRegex(WorkspaceRailError, "credential_reference_invalid"):
                WorkspaceRail(credential_path=credential)

    def test_empty_invalid_oversized_timeout_and_secret_errors_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            credential = Path(directory) / "google.json"
            credential.write_text("synthetic")
            os.chmod(credential, 0o600)
            rail = WorkspaceRail(credential_path=credential, max_output_bytes=128)
            failures = (
                mock.Mock(returncode=0, stdout=b"", stderr=b""),
                mock.Mock(returncode=0, stdout=b"not-json", stderr=b""),
                mock.Mock(returncode=0, stdout=b"{}", stderr=b""),
                mock.Mock(returncode=0, stdout=b'{"historyId":"101","unexpected":true}', stderr=b""),
                mock.Mock(returncode=0, stdout=b"{" + b"x" * 256, stderr=b""),
                mock.Mock(returncode=2, stdout=b"", stderr=b"synthetic-secret-value"),
            )
            for completed in failures:
                with self.subTest(returncode=completed.returncode), \
                     mock.patch("connectors.workspace_rail._run_bounded", return_value=completed), \
                     self.assertRaises(WorkspaceRailError) as raised:
                    rail.run("gmail.history.list", {"userId": "me", "startHistoryId": "100"})
                self.assertNotIn("synthetic-secret-value", str(raised.exception))
                self.assertNotIn(str(credential), str(raised.exception))

    def test_structured_cli_errors_map_only_status_to_content_free_codes(self):
        expected = {
            401: "authority_revoked",
            403: "authority_forbidden",
            404: "not_found",
            410: "cursor_expired",
            429: "rate_limited",
        }
        with tempfile.TemporaryDirectory() as directory:
            credential = Path(directory) / "google.json"
            credential.write_text("synthetic")
            os.chmod(credential, 0o600)
            rail = WorkspaceRail(credential_path=credential)
            for status, code in expected.items():
                private_message = f"private-upstream-detail-{status}"
                completed = mock.Mock(
                    returncode=1,
                    stdout=json.dumps({
                        "error": {
                            "code": status,
                            "message": private_message,
                            "reason": "syntheticReason",
                        },
                    }).encode(),
                    stderr=b"other-private-detail",
                )
                with self.subTest(status=status), \
                     mock.patch("connectors.workspace_rail._run_bounded", return_value=completed), \
                     self.assertRaisesRegex(WorkspaceRailError, code) as raised:
                    rail.run(
                        "gmail.history.list",
                        {"userId": "me", "startHistoryId": "100"},
                    )
                self.assertEqual(raised.exception.code, code)
                self.assertNotIn(private_message, str(raised.exception))
                self.assertNotIn("other-private-detail", str(raised.exception))

    def test_docs_export_uses_internal_private_output_and_returns_bounded_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            credential = Path(directory) / "google.json"
            credential.write_text("synthetic")
            os.chmod(credential, 0o600)

            def execute(argv, **_kwargs):
                output = Path(argv[argv.index("--output") + 1])
                self.assertEqual(output.parent.stat().st_mode & 0o777, 0o700)
                output.write_bytes(b"synthetic document")
                return mock.Mock(returncode=0, stdout=b"", stderr=b"")

            with mock.patch("connectors.workspace_rail._run_bounded", side_effect=execute):
                value = WorkspaceRail(credential_path=credential).export_document(
                    file_id="synthetic-file-1", mime_type="text/plain",
                )
            self.assertEqual(value, b"synthetic document")
            with self.assertRaises(WorkspaceRailError):
                WorkspaceRail(credential_path=credential).export_document(
                    file_id="synthetic-file-1", mime_type="application/octet-stream",
                )

    def test_real_process_boundary_kills_timeout_and_output_overflow(self):
        with self.assertRaisesRegex(WorkspaceRailError, "output_too_large"):
            workspace_rail._run_bounded(
                (sys.executable, "-c", "import sys; sys.stdout.write('x' * 1024)"),
                environment={}, timeout_seconds=2, max_output_bytes=128,
            )
        with self.assertRaisesRegex(WorkspaceRailError, "upstream_timeout"):
            workspace_rail._run_bounded(
                (sys.executable, "-c", "import time; time.sleep(5)"),
                environment={}, timeout_seconds=0.05, max_output_bytes=128,
            )


if __name__ == "__main__":
    unittest.main()
