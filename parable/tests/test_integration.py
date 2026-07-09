"""Subprocess-level tests: cmd_run against fake harness binaries, and the
node installer. No network, no real codex/pi."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "skills" / "parable" / "scripts" / "parable.py"

FAKE_CODEX = """#!/usr/bin/env bash
cat > /dev/null   # drain the plan from stdin like the real binary
echo '{"type":"thread.started","thread_id":"fake-thread-1"}'
echo '{"type":"turn.started"}'
echo '{"type":"item.completed","item":{"id":"i1","type":"agent_message","text":"done from fake codex"}}'
echo '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":2,"output_tokens":3}}'
"""

FAKE_PI = """#!/usr/bin/env bash
echo '{"type":"session","version":3,"id":"fake-pi-session"}'
echo '{"type":"turn_start"}'
echo '{"type":"tool_execution_start","toolName":"bash"}'
echo '{"type":"message_end","message":{"role":"assistant","stopReason":"stop","content":[{"type":"text","text":"done from fake pi"}],"usage":{"input":7,"output":2,"cacheRead":1,"cacheWrite":0,"cost":{"total":0.0005}}}}'
echo '{"type":"agent_end"}'
"""

CONFIG = """
[parable]
version = 1
[providers.fake-codex]
type = "codex"
base_url = "https://example.test/v1"
env_key = "FAKE_KEY"
wire_api = "responses"
[providers.fake-pi]
type = "pi"
base_url = "https://example.test/v1"
env_key = "FAKE_KEY"
[executors.cx]
provider = "fake-codex"
model = "fake/model"
effort = "low"
[executors.px]
provider = "fake-pi"
model = "fake/model"
effort = "low"
"""


def make_repo(tmp: str) -> Path:
    repo = Path(tmp) / "repo"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "parable.toml").write_text(CONFIG)
    (repo / "plan.md").write_text("Toy plan.")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


def fake_bin(tmp: str) -> Path:
    bindir = Path(tmp) / "bin"
    bindir.mkdir()
    for name, body in (("codex", FAKE_CODEX), ("pi", FAKE_PI)):
        f = bindir / name
        f.write_text(body)
        f.chmod(0o755)
    return bindir


def run_cli(repo: Path, bindir: Path, *args: str) -> subprocess.CompletedProcess:
    env = os.environ | {"PATH": f"{bindir}:{os.environ['PATH']}",
                        "FAKE_KEY": "test-key-value"}
    env.pop("PARABLE_CONFIG", None)
    return subprocess.run(["python3", str(SCRIPT), *args],
                          cwd=repo, env=env, capture_output=True, text=True, timeout=60)


class TestCmdRunEndToEnd(unittest.TestCase):
    def test_codex_run_writes_artifacts_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, bindir = make_repo(tmp), fake_bin(tmp)
            p = run_cli(repo, bindir, "run", "cx", str(repo / "plan.md"), "--slug", "toy")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("STATUS   OK", p.stdout)
            self.assertIn("SESSION  fake-thread-1", p.stdout)
            run_dir = next((repo / ".parable" / "runs").iterdir())
            meta = json.loads((run_dir / "meta.json").read_text())
            self.assertEqual(meta["harness"], "codex")
            self.assertEqual(meta["status"], "OK")
            self.assertEqual(meta["session_id"], "fake-thread-1")
            self.assertTrue((run_dir / "cmd.txt").exists())
            self.assertTrue((run_dir / "harness.jsonl").exists())

    def test_pi_run_generates_agent_dir_and_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, bindir = make_repo(tmp), fake_bin(tmp)
            p = run_cli(repo, bindir, "run", "px", str(repo / "plan.md"), "--slug", "toy")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("cost=$0.0005", p.stdout)
            run_dir = next((repo / ".parable" / "runs").iterdir())
            meta = json.loads((run_dir / "meta.json").read_text())
            self.assertEqual(meta["harness"], "pi")
            models = json.loads((run_dir / "pi-agent" / "models.json").read_text())
            self.assertEqual(models["providers"]["parable_fake-pi"]["apiKey"], "$FAKE_KEY")
            self.assertNotIn("test-key-value", (run_dir / "cmd.txt").read_text())

    def test_missing_env_key_fails_before_dispatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, bindir = make_repo(tmp), fake_bin(tmp)
            env = os.environ | {"PATH": f"{bindir}:{os.environ['PATH']}"}
            env.pop("FAKE_KEY", None)
            env.pop("PARABLE_CONFIG", None)
            p = subprocess.run(["python3", str(SCRIPT), "run", "cx", str(repo / "plan.md")],
                               cwd=repo, env=env, capture_output=True, text=True, timeout=60)
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("FAKE_KEY is not set", p.stderr)


class TestInstallerSmoke(unittest.TestCase):
    def test_install_and_error_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            p = subprocess.run(["node", str(REPO / "bin" / "parable.js"), "install",
                                "--target", str(target)],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertTrue((target / "skills" / "parable" / "SKILL.md").exists())
            self.assertTrue((target / "parable.toml").exists())
            # error path: target dir path occupied by a file must fail loudly
            blocked = Path(tmp) / "blocked"
            blocked.write_text("a file, not a dir")
            p2 = subprocess.run(["node", str(REPO / "bin" / "parable.js"), "install",
                                 "--target", str(blocked)],
                                capture_output=True, text=True, timeout=60)
            self.assertNotEqual(p2.returncode, 0)
            self.assertIn("error", (p2.stderr + p2.stdout).lower())


if __name__ == "__main__":
    unittest.main()
