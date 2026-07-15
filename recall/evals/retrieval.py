from __future__ import annotations

import math
import os
import stat
import statistics
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit


SCHEMA_VERSION = "recall.retrieval-eval.v1"
DEFAULT_K = (1, 3, 5)


class EvaluationInputError(ValueError):
    """The evaluation input cannot produce a trustworthy score."""


def receipt_identity(receipt: str) -> str:
    if not isinstance(receipt, str) or not receipt.startswith("recall://"):
        raise EvaluationInputError("receipt must use recall://")
    return receipt.split("#", 1)[0].split("?", 1)[0]


def receipt_source(receipt: str) -> str:
    parsed = urlsplit(receipt_identity(receipt))
    if parsed.scheme != "recall" or not parsed.netloc:
        raise EvaluationInputError("receipt must include a source")
    return parsed.netloc


def validate_cases(cases: list[dict]) -> None:
    if not isinstance(cases, list) or not cases:
        raise EvaluationInputError("cases must be a non-empty list")
    seen: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            raise EvaluationInputError(f"case {index} must be an object")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise EvaluationInputError(f"case {index} has invalid query id")
        if case_id in seen:
            raise EvaluationInputError(f"duplicate query id: {case_id}")
        seen.add(case_id)
        if not isinstance(case.get("stratum"), str) or not case["stratum"]:
            raise EvaluationInputError(f"case {case_id} has invalid stratum")
        if not isinstance(case.get("query"), str) or not case["query"].strip():
            raise EvaluationInputError(f"case {case_id} has invalid query")
        answers = case.get("answers")
        if not isinstance(answers, list) or not all(isinstance(value, str) for value in answers):
            raise EvaluationInputError(f"case {case_id} answers/qrels are required")
        normalized = [receipt_identity(value) for value in answers]
        if len(set(normalized)) != len(normalized):
            raise EvaluationInputError(f"case {case_id} has a duplicate answer")
        for field in ("forbidden",):
            values = case.get(field, [])
            if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
                raise EvaluationInputError(f"case {case_id} has invalid {field}")
            for value in values:
                receipt_identity(value)
        forbidden_sources = case.get("forbidden_source_ids", [])
        if not isinstance(forbidden_sources, list) or not all(
            isinstance(value, str) and value for value in forbidden_sources
        ):
            raise EvaluationInputError(f"case {case_id} has invalid forbidden_source_ids")


def _dcg(relevance: list[int]) -> float:
    return sum(value / math.log2(rank + 2) for rank, value in enumerate(relevance))


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _mean(values: list[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _aggregate(rows: list[dict], k_values: tuple[int, ...]) -> dict:
    positives = [row for row in rows if row["positive"]]
    negatives = [row for row in rows if not row["positive"]]
    output: dict[str, int | float] = {
        "queries": len(rows),
        "positive_queries": len(positives),
        "negative_queries": len(negatives),
        "mrr": _mean([row["reciprocal_rank"] for row in positives]),
        "negative_false_hit_rate": _mean([float(row["result_count"] > 0) for row in negatives]),
        "forbidden_hit_rate": _mean([float(row["forbidden_hit"]) for row in rows]),
        "unauthorized_hit_rate": _mean([float(row["unauthorized_hit"]) for row in rows]),
        "latency_p50_ms": _percentile([row["latency_ms"] for row in rows], 0.50),
        "latency_p95_ms": _percentile([row["latency_ms"] for row in rows], 0.95),
        "backend_error_rate": _mean([float(bool(row["backend_error"])) for row in rows]),
    }
    for k in k_values:
        output[f"hit@{k}"] = _mean([row[f"hit@{k}"] for row in positives])
        output[f"recall@{k}"] = _mean([row[f"recall@{k}"] for row in positives])
        output[f"precision@{k}"] = _mean([row[f"precision@{k}"] for row in positives])
        output[f"ndcg@{k}"] = _mean([row[f"ndcg@{k}"] for row in positives])
    return output


def _numeric_values(value) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, dict):
        result: list[float] = []
        for child in value.values():
            result.extend(_numeric_values(child))
        return result
    if isinstance(value, list):
        result = []
        for child in value:
            result.extend(_numeric_values(child))
        return result
    return []


def score_rankings(
    cases: list[dict],
    rankings: dict[str, list[str]],
    latencies_ms: dict[str, float],
    *,
    backend_errors: dict[str, str] | None = None,
    k_values: tuple[int, ...] = DEFAULT_K,
) -> dict:
    validate_cases(cases)
    if not k_values or any(not isinstance(k, int) or k <= 0 for k in k_values):
        raise EvaluationInputError("k values must be positive integers")
    case_ids = {case["id"] for case in cases}
    missing = sorted(case_ids - set(rankings))
    if missing:
        raise EvaluationInputError(f"missing rankings for {len(missing)} cases")
    extra = sorted(set(rankings) - case_ids)
    if extra:
        raise EvaluationInputError(f"rankings contain {len(extra)} unknown cases")
    if set(latencies_ms) != case_ids:
        raise EvaluationInputError("latencies must cover every case exactly")
    errors = backend_errors or {}
    if not set(errors).issubset(case_ids):
        raise EvaluationInputError("backend errors contain unknown cases")

    rows: list[dict] = []
    for case in cases:
        case_id = case["id"]
        latency = latencies_ms[case_id]
        if not isinstance(latency, (int, float)) or not math.isfinite(latency) or latency < 0:
            raise EvaluationInputError(f"latency for {case_id} must be finite and non-negative")
        values = rankings[case_id]
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise EvaluationInputError(f"ranking for {case_id} must be a receipt list")
        ranking = [receipt_identity(value) for value in values]
        answers = {receipt_identity(value) for value in case["answers"]}
        forbidden = {receipt_identity(value) for value in case.get("forbidden", [])}
        forbidden_sources = set(case.get("forbidden_source_ids", []))
        relevant = [int(value in answers) for value in ranking]
        first_rank = next((index + 1 for index, value in enumerate(relevant) if value), None)
        row = {
            "id": case_id,
            "stratum": case["stratum"],
            "positive": bool(answers),
            "result_count": len(ranking),
            "reciprocal_rank": 0.0 if first_rank is None else 1.0 / first_rank,
            "forbidden_hit": any(
                value in forbidden or receipt_source(value) in forbidden_sources for value in ranking
            ),
            "unauthorized_hit": case["stratum"] == "privacy-auth" and any(
                receipt_source(value) in forbidden_sources for value in ranking
            ),
            "latency_ms": float(latency),
            "backend_error": str(errors.get(case_id, "")),
        }
        for k in k_values:
            relevant_at_k = sum(relevant[:k])
            ideal = [1] * min(len(answers), k)
            row[f"hit@{k}"] = float(relevant_at_k > 0)
            row[f"recall@{k}"] = relevant_at_k / len(answers) if answers else 0.0
            row[f"precision@{k}"] = relevant_at_k / k if answers else 0.0
            ideal_dcg = _dcg(ideal)
            row[f"ndcg@{k}"] = _dcg(relevant[:k]) / ideal_dcg if ideal_dcg else 0.0
        rows.append(row)

    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["stratum"]].append(row)
    report = {
        "schema_version": SCHEMA_VERSION,
        "aggregate": _aggregate(rows, k_values),
        "strata": {name: _aggregate(values, k_values) for name, values in sorted(grouped.items())},
        "cases": rows,
    }
    numeric = _numeric_values(report)
    if not all(math.isfinite(value) for value in numeric):
        raise EvaluationInputError("all metrics must be finite")
    report["numeric_values"] = numeric
    return report


def _resolved_without_symlink(path: Path) -> Path:
    path = Path(path).expanduser()
    if path.is_symlink():
        raise EvaluationInputError("private eval paths cannot be symlinks")
    parent = path if path.is_dir() else path.parent
    while parent != parent.parent:
        if parent.is_symlink():
            raise EvaluationInputError("private eval paths cannot use a symlink parent")
        parent = parent.parent
    return path.resolve(strict=False)


def guard_private_paths(repo_root: Path, input_path: Path, output_path: Path) -> None:
    repo = Path(repo_root).resolve(strict=True)
    for label, path in (("input", input_path), ("output", output_path)):
        resolved = _resolved_without_symlink(Path(path))
        if resolved == repo or repo in resolved.parents:
            raise EvaluationInputError(f"private {label} path is inside repository")
    if not Path(input_path).is_file():
        raise EvaluationInputError("private input must be a regular file")
    if not os.access(Path(input_path), os.R_OK):
        raise EvaluationInputError("private input is not readable")
    input_mode = stat.S_IMODE(Path(input_path).stat().st_mode)
    if input_mode != 0o600:
        raise EvaluationInputError("private input must use mode 0600")
    if stat.S_IMODE(Path(input_path).parent.stat().st_mode) & 0o077:
        raise EvaluationInputError("private input parent must not grant group/world access")
    output = Path(output_path)
    if output.exists() or output.is_symlink():
        raise EvaluationInputError("private output must be a new file")
    if not output.parent.is_dir() or output.parent.is_symlink():
        raise EvaluationInputError("private output parent must be a real directory")
    if stat.S_IMODE(output.parent.stat().st_mode) & 0o077:
        raise EvaluationInputError("private output parent must not grant group/world access")


def aggregate_private_report(report: dict, *, run_id: str) -> dict:
    if not isinstance(run_id, str) or not run_id or len(run_id) > 160:
        raise EvaluationInputError("private run id is invalid")
    required = {"schema_version", "aggregate", "strata", "pins"}
    missing = required - set(report)
    if missing:
        raise EvaluationInputError("private report is missing aggregate fields")
    return {
        "schema_version": report["schema_version"],
        "run_id": run_id,
        "aggregate": report["aggregate"],
        "strata": report["strata"],
        "pins": report["pins"],
    }
