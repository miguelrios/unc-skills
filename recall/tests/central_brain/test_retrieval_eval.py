from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path

RECALL = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RECALL))

from evals.retrieval import (  # noqa: E402
    EvaluationInputError,
    aggregate_private_report,
    guard_private_paths,
    score_rankings,
    validate_cases,
)
from evals.runner import enforce_holdout_output, evaluate_store  # noqa: E402


def case(case_id: str, answers: list[str], *, stratum: str = "semantic-paraphrase", **extra) -> dict:
    return {"id": case_id, "stratum": stratum, "query": f"query-{case_id}", "answers": answers, **extra}


class RetrievalMetricContractTest(unittest.TestCase):
    def test_scores_binary_rankings_and_negatives(self) -> None:
        cases = [
            case("first", ["recall://source/one"]),
            case("second", ["recall://source/two", "recall://source/three"]),
            case("negative", [], stratum="negative"),
        ]
        rankings = {
            "first": ["recall://source/one?rev=1#item=0", "recall://source/noise?rev=1#item=0"],
            "second": ["recall://source/noise", "recall://source/three?rev=2#item=4"],
            "negative": [],
        }

        report = score_rankings(cases, rankings, {key: 10.0 for key in rankings})

        positive = report["aggregate"]
        self.assertEqual(positive["positive_queries"], 2)
        self.assertEqual(positive["negative_queries"], 1)
        self.assertAlmostEqual(positive["hit@1"], 0.5)
        self.assertAlmostEqual(positive["recall@5"], 0.75)
        self.assertAlmostEqual(positive["mrr"], 0.75)
        self.assertAlmostEqual(positive["precision@5"], 0.2)
        self.assertAlmostEqual(positive["negative_false_hit_rate"], 0.0)
        self.assertTrue(all(math.isfinite(value) for value in report["numeric_values"]))

    def test_forbidden_results_and_missing_rankings_are_not_silent(self) -> None:
        cases = [case(
            "isolation", ["recall://allowed/item"], stratum="privacy-auth",
            forbidden_source_ids=["denied"],
        )]
        report = score_rankings(
            cases,
            {"isolation": ["recall://denied/item?rev=1#item=0"]},
            {"isolation": 1.0},
        )
        self.assertEqual(report["aggregate"]["forbidden_hit_rate"], 1.0)
        self.assertEqual(report["aggregate"]["unauthorized_hit_rate"], 1.0)
        with self.assertRaisesRegex(EvaluationInputError, "missing rankings"):
            score_rankings(cases, {}, {})

    def test_rejects_duplicate_ids_missing_qrels_and_nonfinite_values(self) -> None:
        with self.assertRaisesRegex(EvaluationInputError, "duplicate query id"):
            validate_cases([case("same", []), case("same", [])])
        with self.assertRaisesRegex(EvaluationInputError, "answers"):
            validate_cases([{"id": "missing", "stratum": "negative", "query": "nothing"}])
        with self.assertRaisesRegex(EvaluationInputError, "duplicate answer"):
            validate_cases([case("dupe", ["recall://s/a", "recall://s/a?rev=1#item=0"])])
        with self.assertRaisesRegex(EvaluationInputError, "finite"):
            score_rankings([case("one", [])], {"one": []}, {"one": float("nan")})


class PrivateEvalBoundaryTest(unittest.TestCase):
    def test_private_input_and_output_must_stay_outside_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / "repo"
            private = root / "private"
            repo.mkdir()
            private.mkdir()
            private.chmod(0o700)
            private_input = private / "queries.jsonl"
            private_output = private / "aggregate.json"
            private_input.write_text("synthetic-private-placeholder\n")
            private_input.chmod(0o600)

            guard_private_paths(repo, private_input, private_output)
            with self.assertRaisesRegex(EvaluationInputError, "inside repository"):
                guard_private_paths(repo, repo / "queries.jsonl", private_output)
            with self.assertRaisesRegex(EvaluationInputError, "inside repository"):
                guard_private_paths(repo, private_input, repo / "aggregate.json")
            with self.assertRaisesRegex(EvaluationInputError, "symlink"):
                link = private / "link.jsonl"
                link.symlink_to(private_input)
                guard_private_paths(repo, link, private_output)

            private_input.chmod(0o644)
            with self.assertRaisesRegex(EvaluationInputError, "mode 0600"):
                guard_private_paths(repo, private_input, private_output)

    def test_holdout_can_only_emit_aggregate_output(self) -> None:
        with self.assertRaisesRegex(EvaluationInputError, "aggregate-only"):
            enforce_holdout_output(Path("queries-holdout.jsonl"), aggregate_only=False)
        enforce_holdout_output(Path("queries-holdout.jsonl"), aggregate_only=True)
        enforce_holdout_output(Path("queries-dev.jsonl"), aggregate_only=False)

    def test_private_report_contains_only_aggregate_fields(self) -> None:
        public_report = {
            "schema_version": "recall.retrieval-eval.v1",
            "aggregate": {"queries": 2, "recall@5": 0.5},
            "strata": {"semantic-paraphrase": {"queries": 2, "recall@5": 0.5}},
            "cases": [{"id": "private-id", "query": "private words", "rankings": ["receipt"]}],
            "pins": {"corpus_sha256": "a" * 64, "git_sha": "b" * 40},
        }
        private = aggregate_private_report(public_report, run_id="run-opaque")
        self.assertEqual(set(private), {"schema_version", "run_id", "aggregate", "strata", "pins"})
        self.assertNotIn("private-id", str(private))
        self.assertNotIn("private words", str(private))
        self.assertNotIn("receipt", str(private))


class LiveStoreAdapterTest(unittest.TestCase):
    def test_exercises_search_and_session_window_without_faking_source_routing(self) -> None:
        class Store:
            def search(self, query, filters, limit, authorized_source):
                self.call = (query, filters, limit, authorized_source)
                return {
                    "results": [{"receipt": "recall://synthetic:cowork/turn-2?rev=1#item=0"}],
                    "diagnostics": {"elapsed_ms": 4.0},
                }

            def show(self, target, *, around, authorized_source):
                return {"chunks": [
                    {"receipt": "recall://synthetic:cowork/turn-1?rev=1#item=0"},
                    {"receipt": "recall://synthetic:cowork/turn-2?rev=1#item=0"},
                    {"receipt": "recall://synthetic:cowork/turn-3?rev=1#item=0"},
                ]}

        store = Store()
        cases = [case(
            "window", ["recall://synthetic:cowork/turn-2"],
            stratum="session-reconstruction",
            route_source_id="synthetic:cowork",
            around="2026-07-15T12:00:00Z",
            expected_window=[
                "recall://synthetic:cowork/turn-1",
                "recall://synthetic:cowork/turn-2",
                "recall://synthetic:cowork/turn-3",
            ],
        )]

        report = evaluate_store(store, cases, source_routing_supported=False)

        self.assertEqual(store.call, ("query-window", {}, 5, None))
        self.assertEqual(report["behavior"]["session_reconstruction_accuracy"], 1.0)
        self.assertEqual(report["aggregate"]["backend_error_rate"], 1.0)
        self.assertEqual(report["cases"][0]["backend_error"], "source_routing_unsupported")


if __name__ == "__main__":
    unittest.main()
