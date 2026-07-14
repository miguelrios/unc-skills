import importlib.util
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
EVAL = ROOT / "eval_v2"


class RecapEvalV2Test(unittest.TestCase):
    def test_frozen_payload_hashes(self):
        manifest = json.loads((EVAL / "manifest.json").read_text())
        for name in ("cases", "gold_ledger", "scoring"):
            path = EVAL / manifest[name]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), manifest[f"{name}_sha256"])

    def test_gate_surface_is_complete(self):
        cases = [json.loads(line) for line in (EVAL / "cases.jsonl").read_text().splitlines() if line]
        self.assertEqual(len(cases), 19)
        joined = json.dumps(cases)
        for marker in ("100000", "parent-child", "continuation", "cross_worktree", "multi_repo",
                       "test-outcomes", "external-side-effects", "partial", "expired", "remote_pages",
                       "must_abstain"):
            self.assertIn(marker, joined)

    def test_gold_has_every_case(self):
        cases = {json.loads(line)["id"] for line in (EVAL / "cases.jsonl").read_text().splitlines() if line}
        gold = {json.loads(line)["case"] for line in (EVAL / "gold_ledger.jsonl").read_text().splitlines() if line}
        self.assertEqual(cases, gold)

    def test_scorer_pins_every_metric(self):
        spec = importlib.util.spec_from_file_location("recap_scoring", EVAL / "scoring.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(len(module.METRIC_CONTRACT), 11)

    def test_baseline_has_non_null_metric_values(self):
        spec = importlib.util.spec_from_file_location("recap_baseline", EVAL / "baseline_report.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        report = module.report()
        self.assertEqual(set(report["metrics"]), {
            "exact_session_boundary_accuracy", "significant_action_event_recall",
            "significant_action_precision", "unsupported_factual_claims",
            "duplicate_unaccounted_normalized_evidence", "changed_path_observed_commit_coverage",
            "observed_test_outcome_accuracy", "secret_private_fixture_leakage",
            "100k_manifest_peak_rss_mib", "100k_manifest_latency_seconds", "default_recap_words",
        })
        for value in report["metrics"].values():
            self.assertIsNotNone(value["value"])
            self.assertTrue(value["evidence"])

    def test_materializer_builds_sessions_and_git_states(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "corpus"
            subprocess.run(["python3", str(EVAL / "build_corpus.py"), "--output", str(output)], check=True)
            build = json.loads((output / "BUILD.json").read_text())
            self.assertEqual(build, {"case_count": 19, "schema_version": 2, "session_count": 18})
            self.assertTrue((output / "sessions/rollout-long-10k.jsonl").exists())
            self.assertGreater((output / "sessions/session-huge-100k.jsonl").stat().st_size, 1_000_000)
            dirty = subprocess.run(
                ["git", "-C", str(output / "repos/dirty"), "status", "--porcelain", "--untracked-files=all"],
                text=True, stdout=subprocess.PIPE, check=True,
            ).stdout
            self.assertIn("src/staged.py", dirty)
            self.assertIn("src/unstaged.py", dirty)
            self.assertIn("notes/untracked.txt", dirty)


if __name__ == "__main__":
    unittest.main()
