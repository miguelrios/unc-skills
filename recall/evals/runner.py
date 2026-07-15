from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

RECALL_ROOT = Path(__file__).resolve().parents[1]
SERVER_ROOT = RECALL_ROOT / "server"
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from recall_server.projectors import canonical_json  # noqa: E402

from .retrieval import (  # noqa: E402
    EvaluationInputError,
    aggregate_private_report,
    guard_private_paths,
    receipt_identity,
    score_rankings,
    validate_cases,
)


ALLOWED_SEARCH_FILTERS = {"since", "until", "cwd", "branch", "harness"}


def scorer_sha256() -> str:
    digest = hashlib.sha256()
    for path in (Path(__file__).with_name("retrieval.py"), Path(__file__)):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    values: list[dict] = []
    for line_number, line in enumerate(Path(path).read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvaluationInputError(f"invalid JSONL at line {line_number}") from exc
        if not isinstance(value, dict):
            raise EvaluationInputError(f"JSONL line {line_number} must be an object")
        values.append(value)
    if not values:
        raise EvaluationInputError("JSONL input is empty")
    return values


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def evaluate_store(
    store,
    cases: list[dict],
    *,
    source_routing_supported: bool = False,
    limit: int = 5,
) -> dict:
    validate_cases(cases)
    rankings: dict[str, list[str]] = {}
    latencies: dict[str, float] = {}
    backend_errors: dict[str, str] = {}
    session_scores: list[float] = []

    for case in cases:
        case_id = case["id"]
        raw_filters = dict(case.get("filters", {}))
        authorized_source = raw_filters.pop("authorized_source", None)
        filters = {key: value for key, value in raw_filters.items() if key in ALLOWED_SEARCH_FILTERS}
        route_source = case.get("route_source_id")
        if route_source:
            if source_routing_supported:
                filters["source_id"] = route_source
            else:
                backend_errors[case_id] = "source_routing_unsupported"
        started = time.monotonic()
        try:
            result = store.search(case["query"], filters, limit, authorized_source)
            hits = result.get("results", [])
            rankings[case_id] = [value["receipt"] for value in hits if isinstance(value, dict) and value.get("receipt")]
            elapsed = result.get("diagnostics", {}).get("elapsed_ms")
            latencies[case_id] = float(elapsed) if isinstance(elapsed, (int, float)) else (time.monotonic() - started) * 1000
        except Exception as exc:  # Backend failures are a scored outcome, not a benchmark crash.
            rankings[case_id] = []
            latencies[case_id] = (time.monotonic() - started) * 1000
            backend_errors[case_id] = type(exc).__name__

        if case["stratum"] == "session-reconstruction":
            expected = [receipt_identity(value) for value in case.get("expected_window", [])]
            actual: list[str] = []
            if rankings[case_id]:
                try:
                    shown = store.show(
                        rankings[case_id][0],
                        around=case.get("around"),
                        authorized_source=authorized_source,
                    )
                    actual = [receipt_identity(value["receipt"]) for value in (shown or {}).get("chunks", [])]
                except Exception as exc:
                    backend_errors[case_id] = type(exc).__name__
            session_scores.append(float(bool(expected) and actual == expected))

    report = score_rankings(
        cases,
        rankings,
        latencies,
        backend_errors=backend_errors,
    )
    deletion_cases = [value for value in cases if value["stratum"] == "deletion-forgetting"]
    deletion_resurrections = sum(bool(rankings[value["id"]]) for value in deletion_cases)
    report["behavior"] = {
        "session_reconstruction_accuracy": sum(session_scores) / len(session_scores) if session_scores else 0.0,
        "deletion_resurrection_rate": deletion_resurrections / len(deletion_cases) if deletion_cases else 0.0,
    }
    report["rankings"] = rankings
    return report


def canonical_envelope(row: dict) -> dict:
    required = {"source_id", "native_id", "kind", "occurred_at", "principal_id", "content"}
    missing = required - set(row)
    if missing:
        raise EvaluationInputError(f"corpus row is missing {sorted(missing)}")
    content = row["content"]
    value = {
        "schema_version": 1,
        "source_id": row["source_id"],
        "native_id": row["native_id"],
        "native_parent_id": row.get("native_parent_id"),
        "kind": row["kind"],
        "occurred_at": row["occurred_at"],
        "observed_at": row["occurred_at"],
        "principal_id": row["principal_id"],
        "visibility": "private",
        "content_type": "application/json",
        "content": content,
        "provenance": {"harness": "synthetic", "uri": "synthetic://retrieval-eval"},
    }
    value["content_sha256"] = hashlib.sha256(canonical_json(content)).hexdigest()
    return value


def load_synthetic_corpus(store, rows: list[dict]) -> dict:
    with store.connect() as connection:
        count = connection.execute("SELECT count(*) AS n FROM source_events").fetchone()["n"]
    if count:
        raise EvaluationInputError("live evaluation requires an empty disposable database")

    replay_results: list[bool] = []
    for index, row in enumerate(rows):
        envelope = canonical_envelope(row)
        digest = hashlib.sha256(canonical_json(envelope)).hexdigest()[:24]
        acknowledgement, replay = store.ingest(f"retrieval-eval-{index:04d}-{digest}", [envelope])
        if replay or acknowledgement["inserted"] != 1:
            raise EvaluationInputError("initial synthetic ingest was not unique")
        if row.get("replay"):
            duplicate, duplicate_replay = store.ingest(f"retrieval-eval-duplicate-{index:04d}", [envelope])
            replay_results.append(
                not duplicate_replay
                and duplicate["inserted"] == 0
                and duplicate["duplicate_events"] == 1
            )
    with store.connect() as connection:
        final_count = connection.execute("SELECT count(*) AS n FROM source_events").fetchone()["n"]
    return {
        "corpus_rows": len(rows),
        "source_events": final_count,
        "deduplication_accuracy": sum(replay_results) / len(replay_results) if replay_results else 0.0,
    }


def git_sha(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return completed.stdout.strip()


def git_dirty(repo_root: Path) -> bool:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain"],
        check=True,
        capture_output=True,
        timeout=5,
    )
    return bool(completed.stdout)


def verified_repo_root(requested: Path) -> Path:
    requested = Path(requested).resolve(strict=True)
    completed = subprocess.run(
        ["git", "-C", str(requested), "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    actual = Path(completed.stdout.strip()).resolve(strict=True)
    if requested != actual:
        raise EvaluationInputError("repo root must be the actual git top level")
    return actual


def safe_pins(repo_root: Path, corpus: Path, queries: Path) -> dict:
    repo_root = verified_repo_root(repo_root)
    return {
        "git_sha": git_sha(repo_root),
        "git_dirty": git_dirty(repo_root),
        "corpus_sha256": sha256_file(corpus),
        "queries_sha256": sha256_file(queries),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "scorer_schema": "recall.retrieval-eval.v1",
        "scorer_sha256": scorer_sha256(),
    }


def enforce_holdout_output(queries_path: Path, *, aggregate_only: bool) -> None:
    if "holdout" in Path(queries_path).name.casefold() and not aggregate_only:
        raise EvaluationInputError("holdout scoring requires aggregate-only output")


def public_report(report: dict, *, aggregate_only: bool) -> dict:
    output = {key: value for key, value in report.items() if key not in {"numeric_values", "rankings"}}
    if aggregate_only:
        output.pop("cases", None)
    return output


def write_json(path: Path, value: dict, *, private: bool) -> None:
    payload = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    path = Path(path)
    if private:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


def run_live(args) -> dict:
    from recall_server.db import BrainStore

    corpus_path = Path(args.corpus)
    queries_path = Path(args.queries)
    enforce_holdout_output(queries_path, aggregate_only=args.aggregate_only)
    store = BrainStore(args.dsn)
    store.migrate()
    ingest = load_synthetic_corpus(store, load_jsonl(corpus_path))
    report = evaluate_store(
        store,
        load_jsonl(queries_path),
        source_routing_supported=args.source_routing_supported,
    )
    report["behavior"]["deduplication_accuracy"] = ingest["deduplication_accuracy"]
    report["pins"] = safe_pins(Path(args.repo_root), corpus_path, queries_path)
    report["ingest"] = ingest
    output = public_report(report, aggregate_only=args.aggregate_only)
    write_json(Path(args.output), output, private=False)
    return output


def run_score(args) -> dict:
    repo_root = verified_repo_root(Path(args.repo_root))
    queries_path = Path(args.queries)
    rankings_path = Path(args.rankings)
    output_path = Path(args.output)
    enforce_holdout_output(queries_path, aggregate_only=args.aggregate_only or args.private)
    if args.private:
        guard_private_paths(repo_root, queries_path, output_path)
        guard_private_paths(repo_root, rankings_path, output_path)
    cases = load_jsonl(queries_path)
    result_rows = load_jsonl(rankings_path)
    result_by_id = {row.get("id"): row for row in result_rows}
    if len(result_by_id) != len(result_rows):
        raise EvaluationInputError("duplicate ranking result id")
    case_ids = {case["id"] for case in cases}
    if set(result_by_id) != case_ids:
        raise EvaluationInputError("ranking results must cover every query exactly")
    rankings = {case["id"]: result_by_id.get(case["id"], {}).get("receipts", []) for case in cases}
    latencies = {case["id"]: result_by_id.get(case["id"], {}).get("latency_ms", 0.0) for case in cases}
    errors = {
        case["id"]: result_by_id[case["id"]]["backend_error"]
        for case in cases
        if case["id"] in result_by_id and result_by_id[case["id"]].get("backend_error")
    }
    report = score_rankings(cases, rankings, latencies, backend_errors=errors)
    report["pins"] = {
        "git_sha": git_sha(repo_root),
        "git_dirty": git_dirty(repo_root),
        "corpus_sha256": sha256_file(queries_path),
        "queries_sha256": sha256_file(rankings_path),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "scorer_schema": "recall.retrieval-eval.v1",
        "scorer_sha256": scorer_sha256(),
    }
    output = aggregate_private_report(report, run_id=args.run_id) if args.private else public_report(report, aggregate_only=args.aggregate_only)
    write_json(output_path, output, private=args.private)
    return output


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="recall-retrieval-eval")
    subcommands = value.add_subparsers(dest="command", required=True)
    live = subcommands.add_parser("live")
    live.add_argument("--dsn", required=True)
    live.add_argument("--corpus", required=True)
    live.add_argument("--queries", required=True)
    live.add_argument("--output", required=True)
    live.add_argument("--repo-root", default=str(RECALL_ROOT.parent))
    live.add_argument("--aggregate-only", action="store_true")
    live.add_argument("--source-routing-supported", action="store_true")
    score = subcommands.add_parser("score")
    score.add_argument("--queries", required=True)
    score.add_argument("--rankings", required=True)
    score.add_argument("--output", required=True)
    score.add_argument("--repo-root", default=str(RECALL_ROOT.parent))
    score.add_argument("--aggregate-only", action="store_true")
    score.add_argument("--private", action="store_true")
    score.add_argument("--run-id", default="public")
    return value


def main() -> None:
    args = parser().parse_args()
    try:
        result = run_live(args) if args.command == "live" else run_score(args)
    except EvaluationInputError as exc:
        raise SystemExit(f"evaluation input rejected: {exc}") from None
    print(json.dumps({"status": "ok", "schema_version": result["schema_version"]}, sort_keys=True))


if __name__ == "__main__":
    main()
