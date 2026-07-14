#!/usr/bin/env python3
"""Render the L0 baseline with explicit unavailable labels for absent capabilities."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def metric(value, available: bool, evidence: str) -> dict:
    return {"value": value, "available": available, "evidence": evidence}


def report() -> dict:
    return {
        "schema_version": 2,
        "baseline": "Recall show/related plus separate manual git commands",
        "metrics": {
            "exact_session_boundary_accuracy": metric(1.0, True, "exact explicit local paths only"),
            "significant_action_event_recall": metric("unavailable", False, "no normalized claim ledger"),
            "significant_action_precision": metric("unavailable", False, "no normalized claim ledger"),
            "unsupported_factual_claims": metric("unavailable", False, "no evidence-to-claim validator"),
            "duplicate_unaccounted_normalized_evidence": metric("unavailable", False, "no exactly-once accounting"),
            "changed_path_observed_commit_coverage": metric("unavailable", False, "git queried separately without session attribution"),
            "observed_test_outcome_accuracy": metric("unavailable", False, "tests are unclassified transcript text"),
            "secret_private_fixture_leakage": metric(0, True, "synthetic Recall redaction probe"),
            "100k_manifest_peak_rss_mib": metric("unavailable", False, "no manifest collector"),
            "100k_manifest_latency_seconds": metric("unavailable", False, "no manifest collector"),
            "default_recap_words": metric("unavailable", False, "no recap renderer"),
        },
        "hard_gaps": ["remote pagination", "current identity contract", "git provenance", "semantic coverage ledger"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    value = report()
    text = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(text)
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
