from __future__ import annotations

import hashlib
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2] / "server"
sys.path.insert(0, str(SERVER))

from recall_server.federation import (
    SourceProfile,
    federation_rank_components,
    freshness_score,
    normalized_evidence,
)
from recall_server.projectors import canonical_json, redact_text, validate_envelope


FIXTURES = Path(__file__).with_name("federation_eval_v1")
NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
MUTANTS = {
    "source_blind", "freshness_inverted", "trust_spoofing",
    "corroboration_disabled", "privacy_bypass", "false_answer",
}


def rows(name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURES / name).read_text().splitlines()]


class FrozenFederationEvalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads((FIXTURES / "manifest.json").read_text())
        cls.profiles = {
            value["source_id"]: SourceProfile.from_mapping(value)
            for value in rows("profiles.jsonl")
        }
        cls.corpus = rows("corpus.jsonl")
        cls.queries = rows("queries.jsonl")

    def test_manifest_is_frozen_public_and_meets_case_floor(self) -> None:
        for name, digest in self.manifest["sha256"].items():
            self.assertEqual(hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest(), digest)
        self.assertEqual(self.manifest["counts"], {
            "source_profiles": 5, "source_families": 4,
            "corpus_records": 50, "queries": 30,
            "source-quality": 8, "stale-conflict": 6,
            "corroboration": 6, "privacy-canary": 4, "no-answer": 6,
        })
        self.assertEqual(self.manifest["contamination_guard"], "pass")
        self.assertGreaterEqual(self.manifest["counts"]["queries"], 24)

    def test_source_profile_contract_is_closed_and_host_owned(self) -> None:
        value = self.profiles["synthetic:capture:authoritative"]
        self.assertEqual(value.family, "deliberate_capture")
        self.assertEqual(value.quality, "authoritative")
        with self.assertRaises(ValueError):
            SourceProfile.from_mapping({
                **value.to_mapping(), "quality": "model-claimed-best",
            })
        with self.assertRaises(ValueError):
            SourceProfile.from_mapping({
                **value.to_mapping(), "model_score": 1.0,
            })
        content = {"text": "synthetic host authority probe"}
        envelope = {
            "schema_version": 1, "source_id": value.source_id,
            "native_id": "authority-probe", "kind": "memory",
            "occurred_at": "2026-07-14T12:00:00Z",
            "observed_at": "2026-07-14T12:00:01Z",
            "principal_id": "owner", "visibility": "private",
            "content_type": "application/json", "content": content,
            "content_sha256": hashlib.sha256(canonical_json(content)).hexdigest(),
            "source_profile": value.to_mapping(),
        }
        with self.assertRaisesRegex(ValueError, "host-controlled"):
            validate_envelope(envelope)

    def rank(self, query: dict, mutant: str | None = None) -> list[str]:
        privacy = mutant != "privacy_bypass"
        candidates = []
        for ordinal, candidate in enumerate(self.corpus):
            text = redact_text(candidate["text"]) if privacy else candidate["text"]
            if query["query"].casefold() in text.casefold():
                candidates.append({**candidate, "rank_text": text, "ordinal": ordinal})
        if not candidates and mutant == "false_answer":
            candidate = self.corpus[0]
            candidates = [{**candidate, "rank_text": candidate["text"], "ordinal": 0}]

        families_by_evidence: dict[str, set[str]] = {}
        for candidate in candidates:
            profile = self.profiles[candidate["source_id"]]
            families_by_evidence.setdefault(
                normalized_evidence(candidate["rank_text"]), set(),
            ).add(profile.family)

        ranked = []
        for candidate in candidates:
            profile = self.profiles[candidate["source_id"]]
            quality = profile.quality
            if mutant == "source_blind":
                quality = "standard"
            elif mutant == "trust_spoofing":
                quality = candidate.get("claimed_quality", quality)
            fresh = freshness_score(
                candidate["occurred_at"], now=NOW,
                half_life_days=profile.freshness_half_life_days,
            )
            if mutant == "freshness_inverted":
                fresh = 1.0 - fresh
            corroborating = len(families_by_evidence[normalized_evidence(candidate["rank_text"])])
            if mutant == "corroboration_disabled":
                corroborating = 1
            evidence = federation_rank_components(
                lexical_score=1.0, freshness_score=fresh, quality=quality,
                corroborating_families=corroborating,
            )
            ranked.append((evidence["rank_score"], -candidate["ordinal"], candidate["receipt"]))
        ranked.sort(reverse=True)
        return [receipt for _score, _ordinal, receipt in ranked]

    def metrics(self, mutant: str | None = None) -> dict[str, float | int]:
        reciprocal_ranks = []
        recalled = 0
        answerable = 0
        stale_correct = 0
        stale_total = 0
        quality_correct = 0
        quality_total = 0
        privacy_hits = 0
        false_positives = 0
        for query in self.queries:
            ranked = self.rank(query, mutant)
            answers = set(query["answers"])
            if answers:
                answerable += 1
                positions = [index for index, receipt in enumerate(ranked, 1) if receipt in answers]
                if positions and positions[0] <= 5:
                    recalled += 1
                reciprocal_ranks.append(1 / positions[0] if positions else 0.0)
            if query["stratum"] == "stale-conflict":
                stale_total += 1
                stale_correct += int(bool(ranked) and ranked[0] in answers)
            elif query["stratum"] == "source-quality":
                quality_total += 1
                quality_correct += int(
                    bool(ranked) and ranked[0] in set(query["high_quality_answers"])
                )
            elif query["stratum"] == "privacy-canary":
                privacy_hits += len(ranked)
            elif query["stratum"] == "no-answer":
                false_positives += int(bool(ranked))
        return {
            "recall_at_5": recalled / answerable,
            "mrr": sum(reciprocal_ranks) / answerable,
            "stale_conflict_accuracy": stale_correct / stale_total,
            "high_quality_source_precision": quality_correct / quality_total,
            "privacy_canary_hits": privacy_hits,
            "no_answer_false_positives": false_positives,
        }

    @staticmethod
    def passes(metrics: dict[str, float | int]) -> bool:
        return (
            metrics["recall_at_5"] >= 0.95
            and metrics["mrr"] >= 0.90
            and metrics["stale_conflict_accuracy"] == 1.0
            and metrics["high_quality_source_precision"] >= 0.90
            and metrics["privacy_canary_hits"] == 0
            and metrics["no_answer_false_positives"] == 0
        )

    def test_frozen_scoreboard_meets_every_gate(self) -> None:
        metrics = self.metrics()
        self.assertTrue(self.passes(metrics), metrics)

    def test_required_mutants_each_fail_at_least_one_gate(self) -> None:
        for mutant in sorted(MUTANTS):
            with self.subTest(mutant=mutant):
                metrics = self.metrics(mutant)
                self.assertFalse(self.passes(metrics), metrics)


if __name__ == "__main__":
    unittest.main()
