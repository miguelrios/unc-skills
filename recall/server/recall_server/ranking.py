from __future__ import annotations

from .federation import federation_rank_components

DEFAULT_SEARCH_DEADLINE_MS = 300


def retrieval_leg_order(identifiers: list[str]) -> tuple[str, ...]:
    """Exact identifiers get the deadline before any potentially broad phrase."""
    return (
        ("exact-question", "entity", "identifier") if identifiers
        else ("exact-question", "semantic", "phrase", "entity", "partial", "all")
    )


def should_run_partial(*, candidate_count: int, result_limit: int) -> bool:
    """One incidental exact hit must not suppress bounded structural evidence."""
    return candidate_count < result_limit


def evidence_rank_components(*, legs: set[str], surface: str, lexical_rank: float,
                             matched_count: int, informative_count: int,
                             has_identifier: bool, recency_factor: float,
                             quality: str = "unrated",
                             corroborating_families: int = 1,
                             fusion_score: float = 0.0) -> dict:
    """Return an observable, content-free evidence vector and its ordering key."""
    if "identifier" in legs or ("entity" in legs and has_identifier):
        evidence_class, class_priority = "identifier", 4
    elif "answer" in legs:
        evidence_class, class_priority = "answer", 3
    elif legs & {"exact-question", "phrase"}:
        evidence_class, class_priority = "phrase", 3
    elif "entity" in legs:
        evidence_class, class_priority = "error-entity", 2
    elif legs & {"semantic", "rewrite"}:
        evidence_class, class_priority = "semantic", 2
    elif legs & {"pair", "anchor"}:
        evidence_class, class_priority = "structural", 1
    else:
        evidence_class, class_priority = "broad", 0
    origin_priority = (
        2 if evidence_class == "answer"
        else 1 if evidence_class == "phrase" and surface == "tool_input"
        else 0
    )
    surface_weight = (
        {"user": 4.0, "assistant": 2.0, "tool_input": 1.5, "tool_output": 1.0}.get(surface, 1.0)
        if evidence_class in {"structural", "broad"}
        else 1.0
    )
    # RRF makes scores from lexical rank, cosine similarity, and query rewrites
    # comparable while preserving the existing exact/phrase class priorities.
    lexical_score = max(0.01, float(lexical_rank), float(fusion_score) * 60.0) * surface_weight
    coverage = matched_count / max(1, informative_count)
    federation = federation_rank_components(
        lexical_score=lexical_score, freshness_score=recency_factor,
        quality=quality, corroborating_families=corroborating_families,
    )
    return {
        "evidence_class": evidence_class,
        "class_priority": class_priority,
        "origin_priority": origin_priority,
        "matched_count": matched_count,
        "informative_count": informative_count,
        "coverage": round(coverage, 6),
        "lexical_score": round(lexical_score, 9),
        "fusion_score": round(float(fusion_score), 9),
        **federation,
        "rank_key": [class_priority, origin_priority, federation["rank_score"], coverage],
    }
