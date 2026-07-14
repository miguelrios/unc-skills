import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/recap/scripts/recap.py"
RECALL = ROOT.parent / "recall/skills/recall/scripts/recall.py"


def load_recap():
    spec = importlib.util.spec_from_file_location("recap_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_session(path: Path, secret: bool = False) -> None:
    texts = ["set the goal", "edited src/example.py", "tests passed"]
    if secret:
        texts[1] = "api_key=sk-" + "A" * 32
    records = [
        {
            "type": "user" if index % 2 == 0 else "assistant",
            "timestamp": f"2026-07-14T00:00:0{index}Z",
            "cwd": str(path.parent),
            "gitBranch": "main",
            "message": {"content": text},
        }
        for index, text in enumerate(texts)
    ]
    path.write_text("".join(json.dumps(record) + "\n" for record in records))


class RecapCollectorTest(unittest.TestCase):
    def setUp(self):
        self.recap = load_recap()

    def args(self, session: Path, output: Path | None = None, repo: Path | None = None):
        return SimpleNamespace(
            current=False, session=str(session), output=str(output) if output else None,
            repo=str(repo) if repo else None, recall_script=str(RECALL),
        )

    def test_collect_is_ordered_redacted_and_structurally_honest(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = Path(temporary) / "session.jsonl"
            write_session(session, secret=True)
            manifest = self.recap.collect(self.args(session))
            self.assertEqual([event["ordinal"] for event in manifest["events"]], [0, 1, 2])
            self.assertNotIn("sk-", json.dumps(manifest))
            self.assertEqual(manifest["coverage"]["redacted_lines"], 1)
            self.assertEqual(manifest["coverage"]["semantic_accounting"], "not_performed")
            self.assertTrue(manifest["coverage"]["source_complete"])
            self.assertTrue(self.recap.validate_manifest(manifest)["valid"])

    def test_validation_detects_tampered_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = Path(temporary) / "session.jsonl"
            write_session(session)
            manifest = self.recap.collect(self.args(session))
            manifest["events"][1]["text"] = "tampered"
            result = self.recap.validate_manifest(manifest)
            self.assertFalse(result["valid"])
            self.assertIn("event 1 text digest mismatch", result["errors"])

    def test_private_write_uses_0600_and_rejects_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "private"
            output = root / "manifest.json"
            self.recap.private_write(output, {"safe": True})
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            target = root / "target.json"
            link = root / "link.json"
            target.write_text("unchanged")
            link.symlink_to(target)
            with self.assertRaises(self.recap.RecapError):
                self.recap.private_write(link, {"unsafe": True})
            self.assertEqual(target.read_text(), "unchanged")

    def test_git_snapshot_labels_current_state_without_causation(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "recap@example.invalid"], check=True)
            (repo / "tracked.txt").write_text("base\n")
            subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
            (repo / "new.txt").write_text("new\n")
            snapshot = self.recap.git_snapshot(str(repo))
            self.assertTrue(snapshot["available"])
            self.assertEqual(snapshot["attribution"], "current_state_only")
            self.assertIn("new.txt", snapshot["changed_paths"])

    def test_current_codex_identity_requires_one_exact_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary) / ".codex"
            session = codex_home / "sessions/2026/07/14/rollout-2026-07-14T00-00-00-12345678-1234-1234-1234-123456789abc.jsonl"
            session.parent.mkdir(parents=True)
            write_session(session)
            previous = {key: os.environ.get(key) for key in ("CODEX_HOME", "CODEX_THREAD_ID")}
            os.environ["CODEX_HOME"] = str(codex_home)
            os.environ["CODEX_THREAD_ID"] = "12345678-1234-1234-1234-123456789abc"
            try:
                self.assertEqual(self.recap.resolve_current(), session)
                duplicate = session.with_name("rollout-copy-12345678-1234-1234-1234-123456789abc.jsonl")
                write_session(duplicate)
                with self.assertRaises(self.recap.RecapError):
                    self.recap.resolve_current()
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value


if __name__ == "__main__":
    unittest.main()
