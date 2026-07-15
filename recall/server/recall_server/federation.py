"""Host-controlled source profiles and bounded federation rank components."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


SOURCE_FAMILIES = frozenset({
    "coding_history", "deliberate_capture", "user_export",
    "third_party_research",
})
QUALITY_SCORES = {
    "unrated": 0.25,
    "standard": 0.60,
    "trusted": 0.80,
    "authoritative": 1.00,
}
PROFILE_FIELDS = {
    "source_id", "family", "quality", "freshness_half_life_days",
}


@dataclass(frozen=True)
class SourceProfile:
    source_id: str
    family: str
    quality: str
    freshness_half_life_days: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SourceProfile":
        if not isinstance(value, Mapping) or set(value) != PROFILE_FIELDS:
            raise ValueError("source profile schema is invalid")
        source_id = value["source_id"]
        family = value["family"]
        quality = value["quality"]
        half_life = value["freshness_half_life_days"]
        if not isinstance(source_id, str) or not source_id or len(source_id) > 200:
            raise ValueError("source profile source is invalid")
        if family not in SOURCE_FAMILIES:
            raise ValueError("source profile family is invalid")
        if quality not in QUALITY_SCORES:
            raise ValueError("source profile quality is invalid")
        if not isinstance(half_life, int) or isinstance(half_life, bool) or not 1 <= half_life <= 3650:
            raise ValueError("source profile freshness is invalid")
        return cls(source_id, family, quality, half_life)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "family": self.family,
            "quality": self.quality,
            "freshness_half_life_days": self.freshness_half_life_days,
        }


def normalized_evidence(text: str) -> str:
    if not isinstance(text, str):
        raise ValueError("evidence text is invalid")
    return re.sub(r"\s+", " ", text).strip().casefold()


def freshness_score(occurred_at: str | datetime, *, now: datetime,
                    half_life_days: int) -> float:
    if not isinstance(half_life_days, int) or not 1 <= half_life_days <= 3650:
        raise ValueError("freshness half-life is invalid")
    if isinstance(occurred_at, str):
        try:
            occurred = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("occurred_at is invalid") from error
    elif isinstance(occurred_at, datetime):
        occurred = occurred_at
    else:
        raise ValueError("occurred_at is invalid")
    if occurred.tzinfo is None or now.tzinfo is None:
        raise ValueError("freshness timestamps must be timezone-aware")
    age_days = max(0.0, (now.astimezone(timezone.utc) - occurred.astimezone(timezone.utc)).total_seconds() / 86400)
    return round(1.0 / (1.0 + age_days / half_life_days), 9)


def federation_rank_components(*, lexical_score: float, freshness_score: float,
                               quality: str, corroborating_families: int) -> dict[str, Any]:
    if quality not in QUALITY_SCORES:
        raise ValueError("source quality is invalid")
    if not isinstance(corroborating_families, int) or corroborating_families < 1:
        raise ValueError("corroborating family count is invalid")
    lexical = max(0.0, float(lexical_score))
    lexical_component = lexical / (1.0 + lexical)
    freshness = min(1.0, max(0.0, float(freshness_score)))
    quality_component = QUALITY_SCORES[quality]
    corroboration = min(1.0, float(corroborating_families - 1))
    rank_score = (
        0.45 * lexical_component
        + 0.30 * quality_component
        + 0.10 * freshness
        + 0.15 * corroboration
    )
    return {
        "lexical_component": round(lexical_component, 9),
        "freshness_component": round(freshness, 9),
        "quality": quality,
        "quality_component": round(quality_component, 9),
        "corroborating_families": corroborating_families,
        "corroboration_component": round(corroboration, 9),
        "rank_score": round(rank_score, 9),
    }
