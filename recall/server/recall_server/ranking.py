from __future__ import annotations

DEFAULT_SEARCH_DEADLINE_MS = 300


def retrieval_leg_order(identifiers: list[str]) -> tuple[str, ...]:
    """Exact identifiers get the deadline before any potentially broad phrase."""
    return ("entity", "identifier") if identifiers else ("phrase", "entity", "partial", "all")


def should_run_partial(*, candidate_count: int, result_limit: int) -> bool:
    """One incidental exact hit must not suppress bounded structural evidence."""
    return candidate_count < result_limit


def evidence_rank_components(*, legs: set[str], surface: str, lexical_rank: float,
                             matched_count: int, informative_count: int,
                             has_identifier: bool, recency_factor: float) -> dict:
    """Return an observable, content-free evidence vector and its ordering key."""
    if "identifier" in legs or ("entity" in legs and has_identifier):
        evidence_class, class_priority = "identifier", 4
    elif "phrase" in legs:
        evidence_class, class_priority = "phrase", 3
    elif "entity" in legs:
        evidence_class, class_priority = "error-entity", 2
    elif legs & {"pair", "anchor"}:
        evidence_class, class_priority = "structural", 1
    else:
        evidence_class, class_priority = "broad", 0
    origin_priority = 1 if evidence_class == "phrase" and surface == "tool_input" else 0
    surface_weight = (
        {"user": 4.0, "assistant": 2.0, "tool_input": 1.5, "tool_output": 1.0}.get(surface, 1.0)
        if evidence_class in {"structural", "broad"}
        else 1.0
    )
    lexical_score = max(0.01, float(lexical_rank)) * surface_weight * recency_factor
    coverage = matched_count / max(1, informative_count)
    return {
        "evidence_class": evidence_class,
        "class_priority": class_priority,
        "origin_priority": origin_priority,
        "matched_count": matched_count,
        "informative_count": informative_count,
        "coverage": round(coverage, 6),
        "lexical_score": round(lexical_score, 9),
        "rank_key": [class_priority, origin_priority, lexical_score, coverage],
    }
