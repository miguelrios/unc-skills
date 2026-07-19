"""Build and validate a private owner-question retrieval holdout.

Questions and qrels remain in a mode-0600 JSONL file outside git. The only printable result is a
content-free aggregate receipt. Bootstrap cases require an exact text match in an already-indexed
Recall result; they never treat an approximate top hit as ground truth.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import platform
import re
import stat
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from .retrieval import (
    EvaluationInputError,
    aggregate_private_report,
    receipt_identity,
    score_rankings,
    validate_cases,
)
from .runner import git_dirty, git_sha, scorer_sha256


SCHEMA_VERSION = "recall.private-owner-holdout.v1"
ALLOWED_STRATA = frozenset({
    "owner-natural-language",
    "owner-semantic",
    "owner-temporal",
    "owner-cross-source",
    "owner-no-answer",
})
REDACTION_MARKERS = (
    "[redacted-secret",
    "[redacted-private",
    "[REDACTED",
)
MAX_PRIVATE_INPUT_BYTES = 256 * 1024 * 1024


def _private_path(path: Path, *, exists: bool) -> Path:
    path = Path(path).expanduser()
    absolute = path if path.is_absolute() else Path.cwd() / path
    for candidate in (absolute, *absolute.parents):
        if candidate.is_symlink():
            raise EvaluationInputError("private holdout paths cannot traverse symlinks")
    resolved = absolute.resolve(strict=exists)
    if not resolved.parent.is_dir() or stat.S_IMODE(resolved.parent.stat().st_mode) & 0o077:
        raise EvaluationInputError("private holdout parent must be owner-only")
    if exists:
        details = resolved.stat()
        if not stat.S_ISREG(details.st_mode) or stat.S_IMODE(details.st_mode) != 0o600:
            raise EvaluationInputError("private holdout input must be a mode-0600 regular file")
    elif resolved.exists():
        raise EvaluationInputError("private holdout output must be new")
    return resolved


def _read_private(path: Path, *, maximum: int = MAX_PRIVATE_INPUT_BYTES) -> bytes:
    try:
        before = path.lstat()
    except OSError as error:
        raise EvaluationInputError("private holdout input is unavailable") from error
    if before.st_size > maximum:
        raise EvaluationInputError("private holdout input exceeds byte bound")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise EvaluationInputError("private holdout input is unavailable") from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or stat.S_IMODE(opened.st_mode) != 0o600
        ):
            raise EvaluationInputError("private holdout input changed during validation")
        chunks: list[bytes] = []
        remaining = opened.st_size + 1
        while remaining:
            chunk = os.read(descriptor, min(1_048_576, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) != opened.st_size:
            raise EvaluationInputError("private holdout input changed during validation")
        return payload
    finally:
        os.close(descriptor)


def _parse_jsonl(payload: bytes) -> list[dict[str, Any]]:
    try:
        lines = payload.decode().splitlines()
    except UnicodeDecodeError as error:
        raise EvaluationInputError("private holdout input is not UTF-8") from error
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise EvaluationInputError(
                f"private holdout line {line_number} is invalid JSON"
            ) from error
        if not isinstance(value, dict):
            raise EvaluationInputError("private holdout cases must be objects")
        values.append(value)
    return values


def _load_jsonl(path: Path) -> tuple[list[dict[str, Any]], bytes]:
    payload = _read_private(path)
    return _parse_jsonl(payload), payload


def validate_private_holdout(path: Path, *, minimum_cases: int = 50) -> dict[str, Any]:
    source = _private_path(path, exists=True)
    cases, payload = _load_jsonl(source)
    if len(cases) < minimum_cases:
        raise EvaluationInputError("private holdout does not meet the case floor")
    validate_cases(cases)
    query_digests: set[str] = set()
    qrels = 0
    strata = Counter()
    sources: set[str] = set()
    for case in cases:
        if set(case) != {"id", "stratum", "query", "answers", "match_method"}:
            raise EvaluationInputError("private holdout cases must use the closed schema")
        if case["stratum"] not in ALLOWED_STRATA:
            raise EvaluationInputError("private holdout stratum is unsupported")
        if case["match_method"] not in {
            "exact-indexed-evidence",
            "exact-session-export-evidence",
            "owner-reviewed",
            "owner-reviewed-no-answer",
        }:
            raise EvaluationInputError("private holdout qrel method is unsupported")
        if case["match_method"] == "owner-reviewed-no-answer" and case["answers"]:
            raise EvaluationInputError("no-answer case cannot contain qrels")
        if case["match_method"] != "owner-reviewed-no-answer" and not case["answers"]:
            raise EvaluationInputError("positive private case requires qrels")
        digest = hashlib.sha256(case["query"].strip().casefold().encode()).hexdigest()
        if digest in query_digests:
            raise EvaluationInputError("private holdout contains duplicate questions")
        query_digests.add(digest)
        qrels += len(case["answers"])
        strata[case["stratum"]] += 1
        sources.update(urlsplit(answer).netloc for answer in case["answers"])
    return {
        "schema_version": SCHEMA_VERSION,
        "case_count": len(cases),
        "positive_cases": sum(bool(case["answers"]) for case in cases),
        "negative_cases": sum(not case["answers"] for case in cases),
        "qrel_count": qrels,
        "strata_count": len(strata),
        "source_count": len(sources),
        "manifest_sha256": hashlib.sha256(payload).hexdigest(),
    }


def _normalize(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def _recap_events(manifest_path: Path) -> list[dict[str, Any]]:
    try:
        manifest = json.loads(_read_private(manifest_path))
    except json.JSONDecodeError as error:
        raise EvaluationInputError("Recap manifest is invalid JSON") from error
    try:
        descriptor = manifest["ledger"]["events"]
        events_path = Path(descriptor["path"])
        expected_sha256 = descriptor["sha256"]
    except (KeyError, TypeError) as error:
        raise EvaluationInputError("Recap manifest has no exact event ledger") from error
    events_path = _private_path(events_path, exists=True)
    payload = _read_private(events_path)
    if hashlib.sha256(payload).hexdigest() != expected_sha256:
        raise EvaluationInputError("Recap event ledger digest mismatch")
    return _parse_jsonl(payload)


def bootstrap_from_recap(
    recap_manifest: Path,
    output: Path,
    *,
    case_count: int = 50,
    skip_newest_events: int = 0,
) -> dict[str, Any]:
    """Create an exact owner baseline from Recap's validated central export receipts."""
    if case_count < 50 or case_count > 500:
        raise EvaluationInputError("private holdout case count is invalid")
    manifest_path = _private_path(recap_manifest, exists=True)
    output_path = _private_path(output, exists=False)
    events = _recap_events(manifest_path)
    if not 0 <= skip_newest_events < len(events):
        raise EvaluationInputError("private holdout freshness boundary is invalid")
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    eligible_events = events[:-skip_newest_events] if skip_newest_events else events
    for event in reversed(eligible_events):
        if event.get("role") != "user" or event.get("_redactions"):
            continue
        query = _normalize(event.get("text", ""))
        if (
            not 20 <= len(query) <= 8192
            or any(marker in query for marker in REDACTION_MARKERS)
        ):
            continue
        query_sha256 = hashlib.sha256(query.casefold().encode()).hexdigest()
        if query_sha256 in seen:
            continue
        if not isinstance(event.get("receipt"), str):
            continue
        answer = receipt_identity(event["receipt"])
        source = urlsplit(answer).netloc
        selected.append({
            "id": "owner-" + hashlib.sha256(
                f"{query_sha256}\0{source}".encode()
            ).hexdigest()[:20],
            "stratum": "owner-natural-language",
            "query": query,
            "answers": [answer],
            "match_method": "exact-session-export-evidence",
        })
        seen.add(query_sha256)
        if len(selected) == case_count:
            break
    if len(selected) != case_count:
        raise EvaluationInputError(
            f"Recap proved {len(selected)} of {case_count} exact owner holdout qrels"
        )
    payload = "".join(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n" for case in selected)
    descriptor = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w") as target:
        target.write(payload)
    return validate_private_holdout(output_path, minimum_cases=case_count)


def score_remote_holdout(
    holdout: Path,
    output: Path,
    search: Callable[[str], dict[str, Any]],
    *,
    repo_root: Path,
    run_id: str,
    workers: int = 4,
) -> dict[str, Any]:
    """Score one private holdout while persisting aggregate output only."""
    input_path = _private_path(holdout, exists=True)
    output_path = _private_path(output, exists=False)
    receipt = validate_private_holdout(input_path)
    cases, _payload = _load_jsonl(input_path)
    if not 1 <= workers <= 8:
        raise EvaluationInputError("private holdout worker count is invalid")

    def run(case: dict[str, Any]) -> tuple[str, list[str], float, str]:
        started = time.monotonic()
        try:
            response = search(case["query"])
            results = response.get("results") if isinstance(response, dict) else None
            if not isinstance(results, list):
                raise EvaluationInputError("Recall search response is invalid")
            receipts = [
                value["receipt"]
                for value in results[:20]
                if isinstance(value, dict) and isinstance(value.get("receipt"), str)
            ]
            elapsed = (response.get("diagnostics") or {}).get("elapsed_ms")
            latency = (
                float(elapsed)
                if isinstance(elapsed, (int, float)) and math.isfinite(elapsed)
                else (time.monotonic() - started) * 1000
            )
            return case["id"], receipts, latency, ""
        except Exception as error:
            return case["id"], [], (time.monotonic() - started) * 1000, type(error).__name__

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="recall-score") as executor:
        rows = list(executor.map(run, cases))
    rankings = {case_id: values for case_id, values, _latency, _error in rows}
    latencies = {case_id: latency for case_id, _values, latency, _error in rows}
    errors = {case_id: error for case_id, _values, _latency, error in rows if error}
    report = score_rankings(cases, rankings, latencies, backend_errors=errors)
    report["pins"] = {
        "git_sha": git_sha(Path(repo_root)),
        "git_dirty": git_dirty(Path(repo_root)),
        "holdout_sha256": receipt["manifest_sha256"],
        "holdout_cases": receipt["case_count"],
        "python": platform.python_version(),
        "scorer_schema": "recall.retrieval-eval.v1",
        "scorer_sha256": scorer_sha256(),
    }
    aggregate = aggregate_private_report(report, run_id=run_id)
    payload = (json.dumps(aggregate, indent=2, sort_keys=True) + "\n").encode()
    descriptor = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        target.write(payload)
    return aggregate


def _remote_search(recall_script: Path) -> Callable[[str], dict[str, Any]]:
    script = Path(recall_script).resolve(strict=True)
    spec = importlib.util.spec_from_file_location("recall_private_holdout_runtime", script)
    if spec is None or spec.loader is None:
        raise EvaluationInputError("Recall runtime could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    def search(query: str) -> dict[str, Any]:
        try:
            return module.remote_request(
                "POST",
                "/v1/search",
                {"query": query, "filters": {}, "limit": 10},
            )
        except Exception as error:
            raise EvaluationInputError("Recall search failed closed") from error

    return search


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="recall-private-holdout")
    subcommands = value.add_subparsers(dest="command", required=True)
    validate = subcommands.add_parser("validate")
    validate.add_argument("--input", required=True)
    validate.add_argument("--minimum-cases", type=int, default=50)
    bootstrap = subcommands.add_parser("bootstrap-recap")
    bootstrap.add_argument("--recap-manifest", required=True)
    bootstrap.add_argument("--output", required=True)
    bootstrap.add_argument("--case-count", type=int, default=50)
    bootstrap.add_argument("--skip-newest-events", type=int, default=0)
    score = subcommands.add_parser("score-remote")
    score.add_argument("--input", required=True)
    score.add_argument("--output", required=True)
    score.add_argument("--recall-script", required=True)
    score.add_argument("--repo-root", required=True)
    score.add_argument("--run-id", required=True)
    score.add_argument("--workers", type=int, default=4)
    return value


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "validate":
            result = validate_private_holdout(
                Path(args.input), minimum_cases=args.minimum_cases,
            )
        elif args.command == "bootstrap-recap":
            result = bootstrap_from_recap(
                Path(args.recap_manifest),
                Path(args.output),
                case_count=args.case_count,
                skip_newest_events=args.skip_newest_events,
            )
        else:
            result = score_remote_holdout(
                Path(args.input),
                Path(args.output),
                _remote_search(Path(args.recall_script)),
                repo_root=Path(args.repo_root),
                run_id=args.run_id,
                workers=args.workers,
            )
    except EvaluationInputError as error:
        raise SystemExit(f"private holdout rejected: {error}") from None
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
