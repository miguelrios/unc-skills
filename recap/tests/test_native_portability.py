import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1]
RECALL_PACKAGE = PACKAGE.parent / "recall"


class NativePortabilityTest(unittest.TestCase):
    def install(self, home: Path, harness: str) -> tuple[Path, Path]:
        skill_root = home / (".agents/skills" if harness == "codex" else ".claude/skills")
        recap = skill_root / "recap"
        recall = skill_root / "recall"
        recap.parent.mkdir(parents=True)
        shutil.copytree(PACKAGE / "skills/recap", recap)
        shutil.copytree(RECALL_PACKAGE / "skills/recall", recall)
        self.assertEqual(list(skill_root.rglob("recap/SKILL.md")), [recap / "SKILL.md"])
        return recap, recall

    def run_recap(self, home: Path, cwd: Path, argv: list[str], extra_env: dict[str, str]):
        environment = {
            **os.environ,
            "HOME": str(home),
            "RECALL_MODE": "local",
            "RECALL_SESSION_CURSOR_DB": str(home / ".recall/session-export-cursors.db"),
            "RECALL_EXPORT_SOURCE_ID": "portable:test",
        }
        environment.pop("CODEX_THREAD_ID", None)
        environment.pop("CLAUDE_SESSION_ID", None)
        environment.update(extra_env)
        result = subprocess.run(
            [sys.executable, "scripts/recap.py", *argv], cwd=cwd, env=environment,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_clean_codex_install_discovers_current_and_prior_sessions(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            recap, _recall = self.install(home, "codex")
            thread_id = "12345678-1234-1234-1234-123456789abc"
            session = home / ".codex/sessions/2026/07/14" / f"rollout-now-{thread_id}.jsonl"
            prior = session.with_name("rollout-prior.jsonl")
            fork = session.with_name("rollout-fork.jsonl")
            session.parent.mkdir(parents=True)
            session.write_text("\n".join([
                json.dumps({"type": "session_meta", "payload": {"id": thread_id}}),
                json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "now"}}),
            ]) + "\n")
            prior.write_text("\n".join([
                json.dumps({"type": "session_meta", "payload": {"id": "prior-id"}}),
                json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "prior"}}),
            ]) + "\n")
            fork.write_text("\n".join([
                json.dumps({"type": "session_meta", "payload": {
                    "id": "fork-id", "forked_from_id": thread_id,
                }}),
                json.dumps({"type": "event_msg", "payload": {"type": "agent_message", "message": "fork"}}),
            ]) + "\n")
            environment = {
                "CODEX_THREAD_ID": thread_id,
                "RECALL_CODEX_ROOT": str(home / ".codex/sessions"),
                "RECALL_CLAUDE_ROOT": str(home / ".claude/projects"),
            }
            current_receipt = self.run_recap(
                home, recap, ["collect", "--current", "--output", str(home / ".recap/current.json")],
                environment,
            )
            prior_receipt = self.run_recap(
                home, recap, ["collect", "--session", str(prior), "--output", str(home / ".recap/prior.json")],
                environment,
            )
            chain_receipt = self.run_recap(
                home, recap,
                ["collect-set", "--session", str(session), "--chain", "--output", str(home / ".recap/chain.json")],
                environment,
            )
            self.assertTrue(current_receipt["valid"])
            self.assertTrue(prior_receipt["valid"])
            self.assertEqual((current_receipt["event_count"], prior_receipt["event_count"]), (1, 1))
            self.assertEqual((chain_receipt["member_count"], chain_receipt["event_count"]), (2, 2))

    def test_clean_claude_install_discovers_current_children(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            recap, _recall = self.install(home, "claude")
            session_id = "87654321-4321-4321-4321-cba987654321"
            main = home / ".claude/projects/project" / f"{session_id}.jsonl"
            child = main.parent / "subagents/agent-child.jsonl"
            main.parent.mkdir(parents=True)
            child.parent.mkdir(parents=True)
            main.write_text(json.dumps({
                "sessionId": session_id, "type": "user", "message": {"content": "main"},
            }) + "\n")
            child.write_text(json.dumps({
                "sessionId": session_id, "agentId": "child-id", "isSidechain": True,
                "type": "assistant", "message": {"content": "child"},
            }) + "\n")
            receipt = self.run_recap(
                home, recap,
                ["collect-set", "--current", "--include-children", "--output", str(home / ".recap/set.json")],
                {
                    "CLAUDE_SESSION_ID": session_id,
                    "RECALL_CLAUDE_ROOT": str(home / ".claude/projects"),
                    "RECALL_CODEX_ROOT": str(home / ".codex/sessions"),
                },
            )
            self.assertTrue(receipt["valid"])
            self.assertEqual((receipt["member_count"], receipt["event_count"]), (2, 2))

    def test_pi_contract_is_explicitly_indexed_sessions_only(self):
        skill = (PACKAGE / "skills/recap/SKILL.md").read_text()
        readme = (PACKAGE / "README.md").read_text()
        self.assertIn("remote-only Recall", skill)
        self.assertIn("not substitute `--current` or a Codex result", skill)
        self.assertIn("not currently expose this graph", skill)
        self.assertIn("pi can run the skill", readme)
        self.assertIn("against those sessions", readme)
        self.assertIn("its own transcript format is not yet indexed", readme)


if __name__ == "__main__":
    unittest.main()
