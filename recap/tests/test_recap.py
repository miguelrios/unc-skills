import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/recap/scripts/recap.py"
RECALL = ROOT.parent / "recall/skills/recall/scripts/recall.py"
sys.path.insert(0, str(SCRIPT.parent))


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
        self.old_env = {key: os.environ.get(key) for key in (
            "RECALL_CLAUDE_ROOT", "RECALL_CODEX_ROOT", "RECALL_SESSION_CURSOR_DB",
            "RECALL_EXPORT_SOURCE_ID", "RECALL_MODE", "RECALL_URL",
        )}
        os.environ["RECALL_MODE"] = "local"
        os.environ.pop("RECALL_URL", None)

    def tearDown(self):
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def args(self, session: Path, output: Path | None = None, repo: Path | None = None):
        os.environ["RECALL_CLAUDE_ROOT"] = str(session.parent)
        os.environ["RECALL_CODEX_ROOT"] = str(session.parent)
        os.environ["RECALL_SESSION_CURSOR_DB"] = str(session.parent / "session-cursors.db")
        os.environ["RECALL_EXPORT_SOURCE_ID"] = "claude:test:recap"
        output = output or session.parent / "private" / "manifest.json"
        return SimpleNamespace(
            current=False, session=str(session), output=str(output),
            repo=str(repo) if repo else None, recall_script=str(RECALL),
        )

    def test_collect_is_ordered_redacted_and_structurally_honest(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = Path(temporary) / "session.jsonl"
            write_session(session, secret=True)
            manifest = self.recap.collect(self.args(session))
            events = list(self.recap.iter_jsonl(Path(manifest["ledger"]["events"]["path"])))
            self.assertEqual([event["ordinal"] for event in events], [0, 1, 2])
            self.assertNotIn("sk-", json.dumps(events))
            self.assertEqual(manifest["coverage"]["redacted_lines"], 1)
            self.assertEqual(manifest["coverage"]["semantic_accounting"], "packetized_not_classified")
            self.assertTrue(manifest["coverage"]["source_complete"])
            self.assertEqual(manifest["collector"]["page_count"], 1)
            self.assertNotIn("page_receipts", manifest["collector"])
            self.assertIn("page_receipt_chain_sha256", manifest["collector"])
            self.assertEqual(manifest["scope"]["source_id"], "claude:test:recap")
            self.assertEqual(
                manifest["collector"]["recap_privacy_policy_version"],
                self.recap.PRIVACY_POLICY_VERSION,
            )
            self.assertIn("boundary_receipt", manifest["scope"])
            self.assertTrue(self.recap.validate_manifest(manifest)["valid"])

    def test_validation_detects_tampered_text(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = Path(temporary) / "session.jsonl"
            write_session(session)
            manifest = self.recap.collect(self.args(session))
            ledger_path = Path(manifest["ledger"]["events"]["path"])
            events = list(self.recap.iter_jsonl(ledger_path))
            events[1]["text"] = "tampered"
            ledger_path.write_text("".join(
                json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n" for event in events
            ))
            ledger_path.chmod(0o600)
            result = self.recap.validate_manifest(manifest)
            self.assertFalse(result["valid"])
            self.assertTrue(any("digest mismatch" in error for error in result["errors"]))

    def test_validation_detects_git_boundary_and_evidence_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            session = Path(temporary) / "session.jsonl"
            write_session(session)
            manifest = self.recap.collect(self.args(session))
            manifest["git"]["session_observed"]["file_mutations"] = [{
                "event_id": "invented", "result": None,
            }]
            result = self.recap.validate_manifest(manifest)
            self.assertFalse(result["valid"])
            self.assertIn("git provenance references unknown event evidence", result["errors"])

    def test_defense_in_depth_redacts_private_key_blocks(self):
        private_block = (
            "-----BEGIN " + "PRIVATE KEY-----\n" + ("Q" * 256)
            + "\n-----END " + "PRIVATE KEY-----"
        )
        safe, count = self.recap.sanitize("before\n" + private_block + "\nafter")
        self.assertEqual(count, 1)
        self.assertNotIn("Q" * 64, safe)
        self.assertIn("before", safe)
        self.assertIn("after", safe)

    def test_nested_git_metadata_redaction_never_preserves_credential_values(self):
        secret = "Z" * 40
        safe, count = self.recap.sanitize_structure({
            "commits": [{"subject": "api_key=" + secret}],
            "argv": ["git", "show", "token=" + secret],
        })
        self.assertEqual(count, 2)
        self.assertNotIn(secret, json.dumps(safe))
        self.assertEqual(safe["commits"][0]["subject"], "[redacted-secret-line]")

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

    def test_private_reader_rejects_manifest_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            private = Path(temporary) / "private"
            private.mkdir(mode=0o700)
            target = private / "target.json"
            target.write_text("{}\n")
            target.chmod(0o600)
            link = private / "link.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(self.recap.RecapError, "owner-private"):
                self.recap._load_private_json(str(link), label="manifest")

    def test_collect_rejects_output_symlink_without_touching_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = root / "session.jsonl"
            write_session(session)
            private = root / "private"
            private.mkdir(mode=0o700)
            target = private / "target.json"
            target.write_text("unchanged\n")
            target.chmod(0o600)
            link = private / "manifest.json"
            link.symlink_to(target)
            with self.assertRaisesRegex(self.recap.RecapError, "symlink"):
                self.recap.collect(self.args(session, output=link))
            self.assertEqual(target.read_text(), "unchanged\n")

    def test_private_write_never_chmods_shared_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            shared = Path(temporary) / "shared"
            shared.mkdir(mode=0o755)
            shared.chmod(0o755)
            before = shared.stat().st_mode & 0o777
            with self.assertRaisesRegex(self.recap.RecapError, "0700"):
                self.recap.private_write(shared / "manifest.json", {"unsafe": True})
            self.assertEqual(shared.stat().st_mode & 0o777, before)
            self.assertFalse((shared / "manifest.json").exists())

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
            provenance = self.recap.collect_git_provenance_chunks([[]], {}, [str(repo)])
            snapshot = provenance["verified_now"]["repositories"][0]
            self.assertTrue(snapshot["available"])
            self.assertEqual(snapshot["attribution"], "verified_now_only")
            self.assertIn("new.txt", snapshot["changed_paths"])
            self.assertEqual(
                provenance["session_end"]["state"], "unknown_unless_explicitly_observed",
            )

    def test_public_recall_entities_drive_observed_mutation_without_attributing_current_diff(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "recap@example.invalid"], check=True)
            (repo / "base.txt").write_text("base\n")
            subprocess.run(["git", "-C", str(repo), "add", "base.txt"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
            session = root / "session.jsonl"
            session.write_text("\n".join([
                json.dumps({
                    "type": "user", "timestamp": "2026-07-14T00:00:00Z",
                    "cwd": str(repo), "message": {"content": "make the change"},
                }),
                json.dumps({
                    "type": "assistant", "timestamp": "2026-07-14T00:00:01Z",
                    "cwd": str(repo), "message": {"content": [{
                        "type": "tool_use", "name": "apply_patch",
                        "input": {"patch": "*** Begin Patch\n*** Add File: src/new.py\n+VALUE = 1\n*** End Patch"},
                    }]},
                }),
                json.dumps({
                    "type": "user", "timestamp": "2026-07-14T00:00:02Z",
                    "cwd": str(repo), "message": {"content": [{
                        "type": "tool_result", "content": json.dumps({"exit_code": 0}),
                    }]},
                }),
            ]) + "\n")
            manifest = self.recap.collect(self.args(session, repo=repo))
            mutations = manifest["git"]["session_observed"]["file_mutations"]
            self.assertEqual([item["path"] for item in mutations], [str((repo / "src/new.py").resolve())])
            self.assertEqual(mutations[0]["result"]["status"], "passed")
            self.assertEqual(
                manifest["git"]["verified_now"]["repositories"][0]["attribution"],
                "verified_now_only",
            )

    def test_packet_is_bounded_and_unchanged_collection_is_byte_stable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = root / "session.jsonl"
            write_session(session)
            output = root / "private" / "manifest.json"
            args = self.args(session, output=output)
            first = self.recap.collect(args)
            self.recap.private_write(output, first)
            paths = [output] + [
                Path(receipt["path"])
                for key, receipt in first["ledger"].items()
                if isinstance(receipt, dict) and "path" in receipt
            ]
            first_bytes = {path: path.read_bytes() for path in paths}
            second = self.recap.collect(args)
            self.recap.private_write(output, second)
            self.assertEqual(first, second)
            self.assertEqual(first_bytes, {path: path.read_bytes() for path in paths})
            packet = list(self.recap.packet_events(first["ledger"], "packet-00000000"))
            self.assertEqual([event["ordinal"] for event in packet], [0, 1, 2])

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

    def test_collect_set_keeps_claude_main_and_child_boundaries_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main = root / "project" / "main.jsonl"
            child = root / "project" / "subagents" / "child.jsonl"
            main.parent.mkdir(parents=True)
            child.parent.mkdir(parents=True)
            main.write_text(json.dumps({
                "sessionId": "main-native", "type": "user", "timestamp": "2026-07-14T00:00:00Z",
                "message": {"content": "main event"},
            }) + "\n")
            child.write_text(json.dumps({
                "sessionId": "main-native", "agentId": "child-native", "isSidechain": True,
                "type": "assistant", "timestamp": "2026-07-14T00:00:01Z",
                "message": {"content": "child event"},
            }) + "\n")
            os.environ["RECALL_CLAUDE_ROOT"] = str(root)
            os.environ["RECALL_CODEX_ROOT"] = str(root / "codex")
            os.environ["RECALL_SESSION_CURSOR_DB"] = str(root / "state/session-cursors.db")
            os.environ["RECALL_EXPORT_SOURCE_ID"] = "claude:test:recap-set"
            output = root / "private" / "set.json"
            args = SimpleNamespace(
                current=False, session=str(main), output=str(output), repo=None,
                recall_script=str(RECALL), include_children=True, chain=False,
            )
            self.assertEqual(self.recap.command_collect_set(args), 0)
            boundary_set = json.loads(output.read_text())
            validation = self.recap.validate_boundary_set(boundary_set)
            self.assertTrue(validation["valid"], validation["errors"])
            self.assertEqual(validation["member_count"], 2)
            self.assertEqual(validation["event_count"], 2)
            self.assertEqual({member["node_id"] for member in boundary_set["members"]},
                             {"main-native", "child-native"})
            paths = [Path(member["manifest_path"]) for member in boundary_set["members"]]
            self.assertEqual(len(set(paths)), 2)
            self.assertTrue(all(path.stat().st_mode & 0o777 == 0o600 for path in paths))

    def test_boundary_set_validation_detects_member_cross_substitution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session = root / "session.jsonl"
            write_session(session)
            args = self.args(session)
            manifest = self.recap.collect(args)
            manifest["scope"]["relationship_node_id"] = "one"
            member_path = root / "private" / "member.json"
            self.recap.private_write(member_path, manifest)
            validation = self.recap.validate_manifest(manifest)
            value = {
                "schema_version": self.recap.BOUNDARY_SET_SCHEMA_VERSION,
                "boundary_directory": str(member_path.parent.resolve()),
                "selected_node_id": "one",
                "requested": {"include_children": True, "chain": False},
                "members": [{
                    "node_id": "one", "manifest_path": str(member_path),
                    "manifest_sha256": "0" * 64,
                    "boundary_receipt": manifest["scope"]["boundary_receipt"],
                    "session_path_sha256": self.recap.sha256_bytes(
                        manifest["scope"]["session_path"].encode()
                    ),
                    "event_count": validation["event_count"],
                }],
                "edges": [],
            }
            result = self.recap.validate_boundary_set(value)
            self.assertFalse(result["valid"])
            self.assertIn("member manifest digest mismatch", result["errors"])


if __name__ == "__main__":
    unittest.main()
