"""Frozen scoring rules for recap-session-boundary-v2 candidates."""

from __future__ import annotations

import re
from typing import Any


SECRET_RE = re.compile(
    r"(?i)(?:api[_-]?key|token|secret|password)\s*[=:]\s*\S+|"
    r"\b(?:sk|xox[baprs]|gh[psou])[-_][A-Za-z0-9_-]{16,}\b|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bAKIA[A-Z0-9]{16}\b"
)


def ratio(numerator: int, denominator: int) -> float:
    return 1.0 if denominator == 0 else numerator / denominator


def score_case(gold: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    gold_claims = set(gold.get("significant", []))
    claims = candidate.get("claims", [])
    candidate_keys = [claim.get("key") for claim in claims]
    supported = {
        claim.get("key") for claim in claims
        if claim.get("evidence_ids") and claim.get("key") in gold_claims
    }
    unsupported = [
        claim.get("key") for claim in claims
        if not claim.get("evidence_ids") or claim.get("key") not in gold_claims
    ]
    coverage = candidate.get("coverage", {})
    expected_paths = set(gold.get("git_paths", []))
    actual_paths = set(candidate.get("git_paths", []))
    expected_tests = gold.get("tests", {})
    actual_tests = candidate.get("tests", {})
    rendered = str(candidate.get("rendered", ""))
    words = len(rendered.split())
    return {
        "significant_action_recall": ratio(len(supported), len(gold_claims)),
        "significant_action_precision": ratio(len(supported), len(set(candidate_keys))),
        "unsupported_factual_claims": len(unsupported),
        "duplicate_normalized_evidence": int(coverage.get("duplicate", 0)),
        "unaccounted_normalized_evidence": int(coverage.get("unaccounted", 0)),
        "changed_path_coverage": ratio(len(expected_paths & actual_paths), len(expected_paths)),
        "observed_test_outcome_accuracy": ratio(
            sum(actual_tests.get(key) == value for key, value in expected_tests.items()),
            len(expected_tests),
        ),
        "secret_leakage": len(SECRET_RE.findall(rendered)),
        "default_recap_words": words,
        "default_recap_within_limit": words <= 2500,
        "boundary_correct": candidate.get("boundary") == gold.get("boundary", candidate.get("boundary"))
        and bool(candidate.get("boundary_correct", True)),
    }


METRIC_CONTRACT = {
    "exact_session_boundary_accuracy": "correct cases / all cases",
    "significant_action_event_recall": "supported gold claims / gold claims",
    "significant_action_precision": "supported gold claims / candidate claims",
    "unsupported_factual_claims": "candidate claims missing valid evidence or gold meaning",
    "duplicate_unaccounted_normalized_evidence": "duplicate + missing event assignments",
    "changed_path_observed_commit_coverage": "expected paths and commits represented",
    "observed_test_outcome_accuracy": "exact state match across observed and discussed-only checks",
    "secret_private_fixture_leakage": "credential-shaped matches in rendered/public output",
    "100k_manifest_peak_rss_mib": "maximum resident set measured by resource.getrusage",
    "100k_manifest_latency_seconds": "monotonic wall time on named runner",
    "default_recap_words": "whitespace-delimited words; ledger excluded",
}
