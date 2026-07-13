import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PACKAGE = Path(__file__).resolve().parents[1]
SCRIPT = PACKAGE / "skills" / "desloppify" / "scripts" / "desloppify_portable.py"
sys.path.insert(0, str(SCRIPT.parent))
import desloppify_portable as portable  # noqa: E402


class AdapterUnitTest(unittest.TestCase):
    def test_version_compatibility(self):
        self.assertTrue(portable.version_at_least("desloppify 1.0", "1.0"))
        self.assertTrue(portable.version_at_least("1.2.3", "1.0"))
        self.assertFalse(portable.version_at_least("0.9.9", "1.0"))
        self.assertIsNone(portable.version_at_least("unknown", "1.0"))
        self.assertTrue(portable.version_supported("1.9.9"))
        self.assertFalse(portable.version_supported("2.0.0"))
        self.assertIsNone(portable.version_supported("desloppify 1.0rc1"))
        self.assertIsNone(portable.version_supported("helper 1.5; desloppify 0.9.9"))

    def test_harness_detection_uses_presence_not_secret_values(self):
        self.assertEqual(portable.detect_harness({"CODEX_THREAD_ID": "secret-session"}), "codex")
        self.assertEqual(portable.detect_harness({"CLAUDECODE": "1"}), "claude-code")
        self.assertEqual(portable.detect_harness({"TOKEN": "codex"}), "generic")
        self.assertEqual(portable.detect_harness({}, "hermes"), "hermes")
        with self.assertRaisesRegex(ValueError, "unsupported harness"):
            portable.detect_harness({}, "future-agent")

    def test_old_python_is_not_required_to_be_executed_to_evaluate_floor(self):
        with mock.patch.object(portable.sys, "version_info", (3, 10, 14)):
            with mock.patch.object(portable, "upstream_status", return_value={
                "installed": True,
                "executable": "/bin/desloppify",
                "version": "1.0",
                "version_source": "command",
                "probe_status": "ok",
                "supported_spec": ">=1,<2",
                "minimum_compatible": "1.0",
                "compatible": True,
            }), mock.patch.object(portable, "project_status", return_value={
                "requested_path": "/tmp/repo",
                "git_root": "/tmp/repo",
                "git_root_status": "ok",
                "ignore_probe_status": "ok",
                "tracked_probe_status": "ok",
                "desloppify_ignored": True,
                "desloppify_tracked_files": [],
                "scope_check": "agent-review-required",
            }):
                report = portable.build_report(Path("/tmp/repo"), "generic", {})
        self.assertFalse(report["python"]["compatible"])
        self.assertFalse(report["ready"])

    def test_project_status_preserves_independent_probe_failures(self):
        with mock.patch.object(
            portable, "_git_root", return_value=(Path("/tmp/repo"), "ok")
        ), mock.patch.object(
            portable.shutil, "which", return_value="/usr/bin/git"
        ), mock.patch.object(
            portable,
            "_run_capture",
            side_effect=[(None, "timeout"), (None, "execution_error")],
        ):
            status = portable.project_status(Path("/tmp/repo"))
        self.assertEqual(status["git_root_status"], "ok")
        self.assertEqual(status["ignore_probe_status"], "timeout")
        self.assertEqual(status["tracked_probe_status"], "execution_error")
        self.assertIsNone(status["desloppify_ignored"])
        self.assertIsNone(status["desloppify_tracked_files"])


class AdapterProcessTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / ".gitignore").write_text(".desloppify/\n")

    def tearDown(self):
        self.temp.cleanup()

    def _fake_cli(self, body: str) -> Path:
        path = self.bin / "desloppify"
        path.write_text("#!/usr/bin/env python3\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def _env(self, *, isolated_path=False):
        env = dict(os.environ)
        env["PATH"] = str(self.bin) if isolated_path else f"{self.bin}:{env.get('PATH', '')}"
        return env

    def test_doctor_json_is_stable_redacted_and_read_only(self):
        self._fake_cli("print('desloppify 1.0')\n")
        marker = self.repo / "keep.txt"
        marker.write_text("unchanged")
        env = self._env()
        secret = "super-secret-model-key"
        env["MODEL_PROVIDER_TOKEN"] = secret
        env["CODEX_THREAD_ID"] = "private-session-id"
        before = sorted(str(path.relative_to(self.repo)) for path in self.repo.rglob("*"))
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "doctor", "--json", "--project", str(self.repo)],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        after = sorted(str(path.relative_to(self.repo)) for path in self.repo.rglob("*"))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["ready"])
        self.assertEqual(report["harness"]["detected"], "codex")
        self.assertEqual(report["harness"]["review_route"], "native-batch:codex")
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertNotIn("private-session-id", result.stdout + result.stderr)
        self.assertEqual(before, after)
        self.assertEqual(marker.read_text(), "unchanged")

    def test_doctor_does_not_echo_arbitrary_version_output(self):
        secret = "leaked-by-broken-version-command"
        self._fake_cli(
            "import sys\n"
            "print('desloppify 1.0')\n"
            f"print({secret!r}, file=sys.stderr)\n"
        )
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "doctor", "--json", "--project", str(self.repo)],
            env=self._env(),
            text=True,
            stdout=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertNotIn(secret, result.stdout)
        self.assertNotIn("version_output", result.stdout)

    def test_doctor_reports_missing_cli_without_installing(self):
        env = self._env(isolated_path=True)
        before = sorted(self.bin.iterdir())
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "doctor", "--json", "--project", str(self.repo)],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        report = json.loads(result.stdout)
        self.assertFalse(report["upstream"]["installed"])
        self.assertEqual(before, sorted(self.bin.iterdir()))
        self.assertIn("scans never auto-update", report["upstream"]["update"])

    def test_human_doctor_output_covers_ready_and_action_needed(self):
        self._fake_cli("print('desloppify 1.0')\n")
        ready = subprocess.run(
            [sys.executable, str(SCRIPT), "doctor", "--project", str(self.repo)],
            env=self._env(),
            text=True,
            stdout=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(ready.returncode, 0)
        self.assertIn("portable doctor — ready", ready.stdout)
        self.assertIn("ok  Desloppify 1.0", ready.stdout)
        self.assertIn("ok  .desloppify/ is ignored", ready.stdout)

        (self.bin / "desloppify").unlink()
        missing = subprocess.run(
            [sys.executable, str(SCRIPT), "doctor", "--project", str(self.repo)],
            env=self._env(isolated_path=True),
            text=True,
            stdout=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(missing.returncode, 1)
        self.assertIn("portable doctor — action needed", missing.stdout)
        self.assertIn("!!  Desloppify is not installed", missing.stdout)
        self.assertIn("could not be verified", missing.stdout)
        self.assertIn("verify the project is a Git worktree", missing.stdout)

    def test_doctor_detects_unignored_and_tracked_state(self):
        self._fake_cli("print('desloppify 1.0')\n")
        (self.repo / ".gitignore").write_text("")
        state = self.repo / ".desloppify"
        state.mkdir()
        (state / "state.json").write_text("{}")
        subprocess.run(["git", "-C", str(self.repo), "add", "-f", ".desloppify/state.json"], check=True)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "doctor", "--json", "--project", str(self.repo)],
            env=self._env(),
            text=True,
            stdout=subprocess.PIPE,
            check=False,
        )
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["project"]["desloppify_ignored"])
        self.assertEqual(report["project"]["desloppify_tracked_files"], [".desloppify/state.json"])

    def test_doctor_checks_nested_project_state_relative_to_git_root(self):
        self._fake_cli("print('desloppify 1.0')\n")
        nested = self.repo / "packages" / "api"
        nested.mkdir(parents=True)
        (self.repo / ".gitignore").write_text("packages/api/.desloppify/\n")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "doctor", "--json", "--project", str(nested)],
            env=self._env(),
            text=True,
            stdout=subprocess.PIPE,
            check=False,
        )
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0)
        self.assertTrue(report["project"]["desloppify_ignored"])

    def test_doctor_rejects_upstream_below_compatibility_floor(self):
        self._fake_cli("print('desloppify 0.9.9')\n")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "doctor", "--json", "--project", str(self.repo)],
            env=self._env(),
            text=True,
            stdout=subprocess.PIPE,
            check=False,
        )
        report = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(report["upstream"]["compatible"])

    def test_doctor_rejects_unparseable_nonzero_and_future_major_versions(self):
        for output, exit_code in (
            ("version unknown", 0),
            ("desloppify 1.0 error", 4),
            ("desloppify 2.0", 0),
            ("desloppify 1.0rc1", 0),
            ("helper 1.5; desloppify 0.9.9", 0),
        ):
            with self.subTest(output=output, exit_code=exit_code):
                self._fake_cli(f"print({output!r})\nraise SystemExit({exit_code})\n")
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), "doctor", "--json", "--project", str(self.repo)],
                    env=self._env(),
                    text=True,
                    stdout=subprocess.PIPE,
                    check=False,
                )
                report = json.loads(result.stdout)
                self.assertEqual(result.returncode, 1)
                self.assertIsNot(report["upstream"]["compatible"], True)

    def test_run_exec_failure_returns_126_without_traceback(self):
        path = self.bin / "desloppify"
        path.write_text("not an executable format")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "run", "--", "status"],
            env=self._env(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 126)
        self.assertIn("could not launch", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_run_preserves_adversarial_arguments_and_exit_code(self):
        output = self.root / "argv.json"
        sentinel = self.root / "must-not-exist"
        self._fake_cli(
            "import json, os, pathlib, sys\n"
            "pathlib.Path(os.environ['ARGV_OUTPUT']).write_text(json.dumps(sys.argv[1:]))\n"
            "raise SystemExit(23)\n"
        )
        env = self._env()
        env["ARGV_OUTPUT"] = str(output)
        adversarial = f"$(touch {sentinel})"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "run", "--", "scan", "--path", "space path", adversarial, "; echo nope"],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 23)
        self.assertEqual(json.loads(output.read_text()), ["scan", "--path", "space path", adversarial, "; echo nope"])
        self.assertFalse(sentinel.exists())

    def test_run_missing_cli_returns_127(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "run", "--", "status"],
            env=self._env(isolated_path=True),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 127)
        self.assertIn("uv tool install", result.stderr)


if __name__ == "__main__":
    unittest.main()
