from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from evals.private_holdout import (
    EvaluationInputError,
    bootstrap_from_recap,
    score_remote_holdout,
    validate_private_holdout,
)


def private_dir(root: Path, name: str) -> Path:
    path = root / name
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def private_write(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(0o600)


class PrivateOwnerHoldoutTest(unittest.TestCase):
    def test_validates_fifty_cases_and_emits_aggregates_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = private_dir(root, "private")
            holdout = directory / "holdout.jsonl"
            rows = [{
                "id": f"owner-{index:03d}",
                "stratum": "owner-natural-language",
                "query": f"What happened in synthetic task {index:03d}?",
                "answers": [f"recall://synthetic:source/task-{index:03d}"],
                "match_method": "owner-reviewed",
            } for index in range(50)]
            private_write(
                holdout,
                "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            )

            receipt = validate_private_holdout(holdout)

            self.assertEqual(receipt["case_count"], 50)
            self.assertEqual(receipt["positive_cases"], 50)
            self.assertEqual(receipt["qrel_count"], 50)
            self.assertEqual(receipt["strata_count"], 1)
            self.assertEqual(receipt["source_count"], 1)
            self.assertNotIn("query", json.dumps(receipt))
            self.assertNotIn("task-001", json.dumps(receipt))

    def test_validates_synthetic_paraphrases_without_owner_review_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = private_dir(root, "private")
            holdout = directory / "holdout.jsonl"
            rows = [{
                "id": f"semantic-{index:03d}",
                "stratum": "owner-semantic",
                "query": f"What decision did the indexed evidence describe for topic {index:03d}?",
                "answers": [f"recall://synthetic:source/task-{index:03d}"],
                "match_method": "synthetic-paraphrase-from-indexed-evidence",
            } for index in range(50)]
            private_write(
                holdout,
                "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            )

            receipt = validate_private_holdout(holdout)

            self.assertEqual(receipt["case_count"], 50)
            self.assertEqual(receipt["positive_cases"], 50)
            self.assertEqual(receipt["strata_count"], 1)

    def test_rejects_private_boundary_schema_and_qrel_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = private_dir(root, "private")
            holdout = directory / "holdout.jsonl"
            row = {
                "id": "owner-one",
                "stratum": "owner-natural-language",
                "query": "What happened in this sufficiently long question?",
                "answers": [],
                "match_method": "exact-indexed-evidence",
            }
            private_write(holdout, json.dumps(row) + "\n")
            with self.assertRaisesRegex(EvaluationInputError, "case floor"):
                validate_private_holdout(holdout)
            with self.assertRaisesRegex(EvaluationInputError, "positive private case"):
                validate_private_holdout(holdout, minimum_cases=1)
            holdout.chmod(0o644)
            with self.assertRaisesRegex(EvaluationInputError, "mode-0600"):
                validate_private_holdout(holdout, minimum_cases=1)
            holdout.unlink()
            holdout.symlink_to(recap := directory / "target.jsonl")
            private_write(recap, json.dumps(row) + "\n")
            with self.assertRaisesRegex(EvaluationInputError, "symlinks"):
                validate_private_holdout(holdout, minimum_cases=1)

    def test_bootstrap_uses_only_exact_export_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = private_dir(root, "private")
            events = directory / "events.jsonl"
            recap = directory / "recap.json"
            output = directory / "holdout.jsonl"
            rows = [{
                "role": "user",
                "text": f"What was decided for exact synthetic question {index:03d}?",
                "_redactions": 0,
                "receipt": f"recall://synthetic:source/{index:03d}?rev=1#item=0",
            } for index in range(55)]
            payload = "".join(json.dumps(row) + "\n" for row in rows)
            private_write(events, payload)
            private_write(recap, json.dumps({
                "ledger": {"events": {
                    "path": str(events),
                    "sha256": hashlib.sha256(payload.encode()).hexdigest(),
                }}
            }))

            receipt = bootstrap_from_recap(recap, output, case_count=50)

            self.assertEqual(receipt["case_count"], 50)
            cases = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertTrue(all(
                case["answers"][0].startswith("recall://synthetic:source/")
                for case in cases
            ))
            self.assertTrue(all(
                case["match_method"] == "exact-session-export-evidence"
                for case in cases
            ))

    def test_bootstrap_fails_when_export_receipts_cannot_prove_qrels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = private_dir(root, "private")
            events = directory / "events.jsonl"
            recap = directory / "recap.json"
            output = directory / "holdout.jsonl"
            payload = "".join(json.dumps({
                "role": "user",
                "text": f"What was decided for approximate question {index:03d}?",
                "_redactions": 0,
            }) + "\n" for index in range(55))
            private_write(events, payload)
            private_write(recap, json.dumps({
                "ledger": {"events": {
                    "path": str(events),
                    "sha256": hashlib.sha256(payload.encode()).hexdigest(),
                }}
            }))
            with self.assertRaisesRegex(EvaluationInputError, "proved 0 of 50"):
                bootstrap_from_recap(
                    recap,
                    output,
                    case_count=50,
                )
            self.assertFalse(output.exists())

    def test_remote_score_persists_aggregate_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.name", "Synthetic"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "config", "user.email", "synthetic@example.test"],
                check=True,
            )
            (repo / "README").write_text("synthetic\n")
            subprocess.run(["git", "-C", str(repo), "add", "README"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "synthetic"], check=True)
            directory = private_dir(root, "private")
            holdout = directory / "holdout.jsonl"
            output = directory / "aggregate.json"
            rows = [{
                "id": f"owner-{index:03d}",
                "stratum": "owner-natural-language",
                "query": f"What happened in scored synthetic task {index:03d}?",
                "answers": [f"recall://synthetic:source/task-{index:03d}"],
                "match_method": "owner-reviewed",
            } for index in range(50)]
            private_write(
                holdout,
                "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            )

            def search(query: str) -> dict:
                index = int(query.rsplit(" ", 1)[-1].rstrip("?"))
                results = (
                    [{"receipt": f"recall://synthetic:source/task-{index:03d}"}]
                    if index % 2 == 0 else []
                )
                return {"results": results, "diagnostics": {"elapsed_ms": 10}}

            report = score_remote_holdout(
                holdout,
                output,
                search,
                repo_root=repo,
                run_id="synthetic-run",
                workers=2,
            )

            self.assertEqual(report["aggregate"]["recall@5"], 0.5)
            self.assertEqual(report["aggregate"]["backend_error_rate"], 0.0)
            rendered = output.read_text()
            self.assertNotIn("scored synthetic task", rendered)
            self.assertNotIn("task-001", rendered)
            self.assertNotIn("cases", report)


if __name__ == "__main__":
    unittest.main()
