import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/recap/scripts/git_provenance.py"
spec = importlib.util.spec_from_file_location("git_provenance_under_test", SCRIPT)
git = importlib.util.module_from_spec(spec)
spec.loader.exec_module(git)


def run(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    return result.stdout.strip()


def init_repo(path: Path) -> str:
    path.mkdir(parents=True)
    run(path, "init", "-q", "-b", "main")
    run(path, "config", "user.name", "Recap Fixture")
    run(path, "config", "user.email", "recap@example.invalid")
    (path / "tracked.txt").write_text("base\n")
    run(path, "add", "tracked.txt")
    run(path, "commit", "-qm", "base")
    return run(path, "rev-parse", "HEAD")


def event(ordinal, surface, text, tool=None):
    value = {
        "ordinal": ordinal,
        "event_id": f"event-{ordinal}",
        "surface": surface,
        "text": text,
        "entities": [],
    }
    if tool:
        value["entities"] = [{"kind": "tool", "value": tool}]
    return value


class GitProvenanceTest(unittest.TestCase):
    def test_gold_observations_cover_paths_commit_branch_and_tests_without_causation(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            init_repo(repo)
            (repo / "src").mkdir()
            (repo / "src/added.py").write_text("VALUE = 1\n")
            run(repo, "add", "src/added.py")
            run(repo, "commit", "-qm", "add observed fixture")
            commit = run(repo, "rev-parse", "HEAD")
            (repo / "dirty.txt").write_text("current only\n")

            patch = (
                "*** Begin Patch\n"
                "*** Add File: src/added.py\n+VALUE = 1\n"
                "*** Update File: tracked.txt\n@@\n-base\n+updated\n"
                "*** Delete File: removed.txt\n"
                "*** End Patch"
            )
            events = [
                event(0, "tool_input", patch, "apply_patch"),
                event(1, "tool_output", json.dumps({"exit_code": 0})),
                event(2, "tool_input", json.dumps({
                    "cmd": "git add src/added.py && git commit -m observed",
                    "workdir": str(repo),
                }), "exec_command"),
                event(3, "tool_output", json.dumps({
                    "exit_code": 0, "output": f"[main {commit[:12]}] add observed fixture",
                })),
                event(4, "tool_input", json.dumps({
                    "cmd": "git switch feature", "workdir": str(repo),
                }), "exec_command"),
                event(5, "tool_output", json.dumps({"exit_code": 1})),
                event(6, "tool_input", json.dumps({
                    "cmd": "python3 -m pytest -q", "workdir": str(repo),
                }), "exec_command"),
                event(7, "tool_output", json.dumps({"exit_code": 0})),
            ]
            provenance = git.collect_git_provenance(events, {"cwd": str(repo)})
            observed = provenance["session_observed"]
            self.assertEqual(
                {item["path"] for item in observed["file_mutations"]},
                {
                    str((repo / "src/added.py").resolve()),
                    str((repo / "tracked.txt").resolve()),
                    str((repo / "removed.txt").resolve()),
                },
            )
            self.assertEqual(
                {item["operation"] for item in observed["file_mutations"]},
                {"add", "update", "delete"},
            )
            self.assertEqual({item["sha"] for item in observed["observed_commits"]}, {commit[:12]})
            self.assertEqual(observed["branch_switches"][0]["target"], "feature")
            self.assertEqual(observed["branch_switches"][0]["result"]["status"], "failed")
            self.assertEqual(observed["test_commands"][0]["result"]["status"], "passed")
            self.assertEqual(provenance["session_end"]["state"], "unknown_unless_explicitly_observed")
            current = provenance["verified_now"]["repositories"][0]
            self.assertEqual(current["attribution"], "verified_now_only")
            self.assertIn("dirty.txt", current["changed_paths"])
            self.assertNotIn("dirty.txt", {item["path"] for item in observed["file_mutations"]})

    def test_current_status_preserves_staged_unstaged_untracked_and_rename_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            init_repo(repo)
            (repo / "original.txt").write_text("original\n")
            run(repo, "add", "original.txt")
            run(repo, "commit", "-qm", "add rename source")
            (repo / "staged.txt").write_text("staged\n")
            run(repo, "add", "staged.txt")
            (repo / "tracked.txt").write_text("changed\n")
            (repo / "untracked.txt").write_text("untracked\n")
            run(repo, "mv", "original.txt", "renamed.txt")
            snapshot = git.verified_repository_snapshot(repo)
            self.assertTrue(snapshot["available"])
            self.assertTrue({"staged.txt", "original.txt", "renamed.txt", "tracked.txt", "untracked.txt"}.issubset(
                set(snapshot["changed_paths"])
            ))
            self.assertIn("rename_or_copy", {item["kind"] for item in snapshot["status"]})

    def test_multiple_repositories_and_missing_worktree_are_explicit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, second = root / "first", root / "second"
            init_repo(first)
            init_repo(second)
            missing = root / "deleted-worktree"
            events = [
                event(0, "tool_input", json.dumps({"cmd": "git status", "workdir": str(first)}), "exec_command"),
                event(1, "tool_output", json.dumps({"exit_code": 0})),
                event(2, "tool_input", json.dumps({"cmd": "git status", "workdir": str(second)}), "exec_command"),
                event(3, "tool_output", json.dumps({"exit_code": 0})),
            ]
            provenance = git.collect_git_provenance(events, {}, [str(missing)])
            roots = {
                item["repo_root"] for item in provenance["verified_now"]["repositories"]
                if item["available"]
            }
            self.assertEqual(roots, {str(first.resolve()), str(second.resolve())})
            unavailable = [
                item for item in provenance["verified_now"]["repositories"] if not item["available"]
            ]
            self.assertEqual(len(unavailable), 1)
            self.assertIn("not an accessible git worktree", unavailable[0]["reason"])

    def test_git_probe_surface_is_fixed_read_only_and_output_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            init_repo(repo)
            with self.assertRaises(git.GitProbeError):
                git.run_git_probe(repo, "commit")
            for index in range(40):
                (repo / ("untracked-" + str(index) + "-" + "x" * 30)).write_text("x")
            result = git.run_git_probe(repo, "status", max_bytes=64)
            self.assertEqual(result["code"], 125)
            self.assertTrue(result["truncated"])
            self.assertLessEqual(len(result["output"].encode()), 64)

    def test_repository_fsmonitor_configuration_cannot_execute_during_probe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            init_repo(repo)
            sentinel = root / "executed"
            monitor = root / "monitor.sh"
            monitor.write_text("#!/bin/sh\ntouch " + str(sentinel) + "\n")
            monitor.chmod(0o700)
            run(repo, "config", "core.fsmonitor", str(monitor))
            snapshot = git.verified_repository_snapshot(repo)
            self.assertTrue(snapshot["available"])
            self.assertFalse(sentinel.exists())

    def test_command_analysis_respects_quotes_and_does_not_promote_builds_to_tests(self):
        segments = git._command_segments("python -c 'print(\";\")'; git status")
        self.assertEqual(segments, [["python", "-c", 'print(";")'], ["git", "status"]])
        self.assertIsNone(git._verification_kind(["npm", "run", "build"]))
        self.assertEqual(git._verification_kind(["npm", "run", "test:unit"]), "test")
        self.assertEqual(git._verification_kind(["ruff", "check", "."]), "check")

    def test_codex_orchestrator_adapter_reads_literal_actions_without_executing_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            init_repo(repo)
            patch = "*** Begin Patch\n*** Add File: src/orchestrated.py\n+VALUE = 1\n*** End Patch"
            source = (
                "const patch = " + json.dumps(patch) + ";\n"
                "text(await tools.apply_patch(patch));\n"
                "const r = await tools.exec_command({cmd:" + json.dumps("git status && npm test")
                + ",workdir:" + json.dumps(str(repo)) + "}); text(r.output)"
            )
            events = [
                event(0, "tool_input", source, "functions.exec"),
                event(1, "tool_output", json.dumps({"exit_code": 0})),
            ]
            events[0]["possibly_truncated"] = True
            observed = git.observed_git_provenance(events, {"cwd": str(repo)})
            self.assertEqual(
                [item["path"] for item in observed["file_mutations"]],
                [str((repo / "src/orchestrated.py").resolve())],
            )
            self.assertEqual(observed["git_commands"][0]["verb"], "status")
            self.assertEqual(observed["test_commands"][0]["argv"], ["npm", "test"])
            self.assertEqual(observed["limitations"][0]["reason"], "tool_input_possibly_truncated")

    def test_expired_reflog_and_no_git_are_uncertainty_not_invention(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            init_repo(repo)
            run(repo, "reflog", "expire", "--expire=now", "--all")
            snapshot = git.verified_repository_snapshot(repo)
            self.assertTrue(snapshot["available"])
            self.assertTrue(any(
                item.get("probe") == "reflog" and "expired" in item.get("reason", "")
                for item in snapshot["limitations"]
            ))
            no_git = git.verified_repository_snapshot(root / "plain")
            self.assertFalse(no_git["available"])
            self.assertIsNone(no_git.get("head"))

    def test_amend_revert_detached_head_and_cross_worktree_remain_current_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            init_repo(repo)
            (repo / "amended.txt").write_text("one\n")
            run(repo, "add", "amended.txt")
            run(repo, "commit", "-qm", "before amend")
            before_amend = run(repo, "rev-parse", "HEAD")
            (repo / "amended.txt").write_text("two\n")
            run(repo, "add", "amended.txt")
            run(repo, "commit", "--amend", "-qm", "after amend")
            after_amend = run(repo, "rev-parse", "HEAD")
            self.assertNotEqual(before_amend, after_amend)

            (repo / "reverted.txt").write_text("temporary\n")
            run(repo, "add", "reverted.txt")
            run(repo, "commit", "-qm", "temporary change")
            reverted_commit = run(repo, "rev-parse", "HEAD")
            run(repo, "revert", "--no-edit", reverted_commit)

            other = root / "other"
            run(repo, "worktree", "add", "-qb", "other", str(other))
            (other / "other-only.txt").write_text("dirty other worktree\n")
            run(repo, "checkout", "--detach", "HEAD")

            provenance = git.collect_git_provenance([], {}, [str(repo), str(other)])
            snapshots = {
                item["repo_root"]: item for item in provenance["verified_now"]["repositories"]
            }
            primary = snapshots[str(repo.resolve())]
            secondary = snapshots[str(other.resolve())]
            self.assertIsNone(primary["branch"])
            self.assertTrue(any("amend" in item["subject"] for item in primary["reflog"]))
            self.assertTrue(any(item["sha"] == reverted_commit for item in primary["commits"]))
            self.assertNotIn("reverted.txt", primary["changed_paths"])
            self.assertIn("other-only.txt", secondary["changed_paths"])
            self.assertEqual(len(primary["worktrees"]), 2)
            self.assertEqual(provenance["session_observed"]["file_mutations"], [])


if __name__ == "__main__":
    unittest.main()
