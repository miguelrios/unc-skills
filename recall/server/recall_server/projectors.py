from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import PROJECTOR_VERSION

SOURCE_ID_RE = re.compile(r"[A-Za-z0-9_.:@-]{3,160}\Z")
NATIVE_ID_RE = re.compile(r"[A-Za-z0-9_.:@/=-]{1,512}\Z")
KIND_RE = re.compile(r"[A-Za-z0-9_.:-]{1,64}\Z")
CONTENT_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
ENVELOPE_FIELDS = {
    "schema_version", "source_id", "native_id", "native_parent_id", "kind",
    "occurred_at", "observed_at", "principal_id", "visibility", "content_type",
    "content", "provenance", "content_sha256",
}


def redact_text(value: str) -> str:
    safe = legacy_engine().clean_text(value)
    return safe.replace("[redacted-private-key-block]", "[REDACTED-PRIVATE-KEY]").replace(
        "[redacted-secret-line]", "[REDACTED]",
    )


@lru_cache(maxsize=1)
def legacy_engine():
    configured = Path(__file__).resolve().parents[2] / "skills/recall/scripts/recall.py"
    spec = importlib.util.spec_from_file_location("recall_local_engine", configured)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load local recall parser: {configured}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode()


def advisory_lock_key(source_id: str, native_id: str) -> str:
    # PostgreSQL text cannot contain NUL. Unit Separator is boundary-preserving
    # for the validated source/native identifiers and safe for hashtextextended.
    return source_id + "\x1f" + native_id


def effective_session_id(envelope: dict) -> str:
    """Return explicit parent identity, with a narrow legacy Cowork repair seam."""
    explicit = envelope.get("native_parent_id")
    content = envelope.get("content")
    provenance = envelope.get("provenance") or {}
    legacy = content.get("session_id") if isinstance(content, dict) else None
    if (
        envelope.get("kind") == "connector_record"
        and provenance.get("connector_id") == "anthropic.cowork-local"
        and (explicit is None or explicit == envelope.get("native_id"))
        and isinstance(legacy, str)
        and NATIVE_ID_RE.fullmatch(legacy)
    ):
        return legacy
    return explicit or envelope["native_id"]


def content_sha256(envelope: dict) -> str:
    return hashlib.sha256(canonical_json(envelope["content"])).hexdigest()


def validate_envelope(envelope: dict) -> dict:
    if not isinstance(envelope, dict):
        raise ValueError("envelope must be an object")
    if set(envelope) & {"source_profile", "source_family", "source_quality", "quality"}:
        raise ValueError("source profile is host-controlled")
    required = (
        "schema_version", "source_id", "native_id", "kind", "occurred_at",
        "observed_at", "principal_id", "visibility", "content_type", "content",
        "content_sha256",
    )
    missing = [key for key in required if key not in envelope]
    if missing:
        raise ValueError("missing fields: " + ",".join(missing))
    unknown = set(envelope) - ENVELOPE_FIELDS
    if unknown:
        raise ValueError("unknown envelope fields")
    if envelope["schema_version"] != 1 or isinstance(envelope["schema_version"], bool):
        raise ValueError("unsupported schema_version")
    if not isinstance(envelope["source_id"], str) or not SOURCE_ID_RE.fullmatch(envelope["source_id"]):
        raise ValueError("invalid source_id")
    for field in ("native_id", "native_parent_id"):
        value = envelope.get(field)
        if value is not None and (not isinstance(value, str) or not NATIVE_ID_RE.fullmatch(value)):
            raise ValueError(f"invalid {field}")
    if not isinstance(envelope["kind"], str) or not KIND_RE.fullmatch(envelope["kind"]):
        raise ValueError("invalid kind")
    for field in ("occurred_at", "observed_at"):
        value = envelope[field]
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError(f"invalid {field}")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError(f"invalid {field}") from None
        if parsed.tzinfo is None:
            raise ValueError(f"invalid {field}")
    principal = envelope["principal_id"]
    if (
        not isinstance(principal, str) or not 1 <= len(principal) <= 160
        or any(ord(character) < 32 for character in principal)
    ):
        raise ValueError("invalid principal_id")
    if envelope["visibility"] not in {"private", "shared"}:
        raise ValueError("unsupported visibility")
    if envelope["content_type"] != "application/json":
        raise ValueError("unsupported content_type")
    provenance = envelope.get("provenance", {})
    if not isinstance(provenance, dict):
        raise ValueError("provenance must be an object")
    claimed = envelope["content_sha256"]
    if not isinstance(claimed, str) or not CONTENT_SHA256_RE.fullmatch(claimed):
        raise ValueError("invalid content_sha256")
    try:
        actual = content_sha256(envelope)
        canonical_json(provenance)
    except (TypeError, ValueError):
        raise ValueError("content and provenance must be finite JSON values") from None
    if envelope["content_sha256"] != actual:
        raise ValueError("content_sha256 mismatch")
    if envelope["kind"] == "tombstone":
        content = envelope["content"]
        if not isinstance(content, dict) or content.get("target_native_id") != envelope["native_id"]:
            raise ValueError("tombstone target must match native_id")
    return envelope


def event_receipt(source_id: str, native_id: str, revision: int) -> str:
    return f"recall://{source_id}/{native_id}?rev={revision}"


def item_receipt(source_id: str, native_id: str, revision: int, ordinal: int) -> str:
    return f"{event_receipt(source_id, native_id, revision)}#item={ordinal}"


def projected_entities(engine, text: str, extra: list[tuple[str, str]] | None = None) -> list[dict]:
    values = []
    for kind, value in engine.extract_entities(text, extra):
        safe_value = redact_text(str(value))
        values.append({"kind": kind, "value": safe_value, "normalized": safe_value.casefold()})
    return values


def partial_lexical_probes(informative: list[str], *, has_time_filter: bool) -> list[tuple[str, str, int]]:
    """Bounded structural probes for one-token drift; never encode domain answers."""
    if not informative:
        return []
    scored = sorted(
        enumerate(informative),
        key=lambda item: (
            bool(re.search(r"[._/-]", item[1])),
            any(character.isdigit() for character in item[1]),
            len(item[1]),
            -item[0],
        ),
        reverse=True,
    )
    probes: list[tuple[str, str, int]] = []
    if len(scored) >= 2:
        probes.append((" ".join([scored[0][1], scored[1][1]]), "pair", 2))
    compound = [value for value in informative if re.search(r"[._/-]", value)]
    if compound:
        probes.append((max(compound, key=len), "anchor", 2))
    if has_time_filter and not compound:
        probes.append((informative[0], "time-anchor", 1))
    return probes[:3]


def preferred_phrase_probes(phrases: list[str]) -> list[str]:
    """Choose a structural phrase plus one bounded parser fallback."""
    if not phrases:
        return []
    candidates = phrases[1:] or phrases

    def score(value: str) -> tuple[int, int, int]:
        words = re.findall(r"[A-Za-z0-9_./#-]+", value)
        structural = sum(
            1
            for word in words
            if re.search(r"[0-9_./#-]", word)
            or re.search(r"(?:error|exception|timeout|violation|failed)$", word, re.I)
        )
        return structural, len(words), -len(value)

    primary = max(candidates, key=score)
    fallback = candidates[-1]
    return [primary] + ([fallback] if fallback != primary else [])


def preferred_phrase_probe(phrases: list[str]) -> str | None:
    """Compatibility helper returning the strongest bounded phrase probe."""
    probes = preferred_phrase_probes(phrases)
    return probes[0] if probes else None


def phrase_query_spec(probes: list[str]) -> tuple[str, str] | None:
    """Compile bounded phrase alternatives into one indexed PostgreSQL query."""
    if not probes:
        return None
    if len(probes) == 1:
        return probes[0], "phraseto_tsquery"
    quoted = [f'"{probe.replace(chr(34), " ")}"' for probe in probes]
    return " OR ".join(quoted), "websearch_to_tsquery"


def project(envelope: dict, revision: int) -> tuple[list[dict], dict]:
    """Return sanitized items and session metadata for one canonical event."""
    kind = envelope["kind"]
    content = envelope["content"]
    items: list[dict] = []
    metadata: dict[str, Any] = {"projector_version": PROJECTOR_VERSION}
    provenance = envelope.get("provenance", {})
    for key in ("original_path", "cwd", "branch", "slot", "harness", "privacy_policy_version"):
        if provenance.get(key) is not None:
            metadata[key] = redact_text(str(provenance[key]))

    if kind == "tombstone":
        return items, metadata

    if kind == "transcript_record":
        harness = envelope.get("provenance", {}).get("harness")
        engine = legacy_engine()
        parser = engine.claude_record if harness == "claude" else engine.codex_record if harness == "codex" else None
        if parser is None:
            raise ValueError("transcript_record requires claude or codex harness")
        parsed, parsed_meta = parser(content)
        metadata.update({key: redact_text(str(value)) for key, value in parsed_meta.items() if value is not None})
        metadata["harness"] = harness
        for ordinal, (timestamp, surface, text, entities) in enumerate(parsed):
            cleaned = engine.clean_text(text)
            items.append({
                "ordinal": ordinal,
                "occurred_at": timestamp,
                "role": surface,
                "surface": surface,
                "text_redacted": cleaned,
                "entities": projected_entities(engine, cleaned, entities),
                "receipt": item_receipt(envelope["source_id"], envelope["native_id"], revision, ordinal),
            })
        return items, metadata

    if isinstance(content, str):
        text = content
        role = None
        surface = kind
    elif isinstance(content, dict):
        text = str(content.get("text") or content.get("content") or json.dumps(content, sort_keys=True))
        role = content.get("role")
        surface = str(content.get("surface") or kind)
    else:
        text = json.dumps(content, sort_keys=True)
        role = None
        surface = kind
    cleaned = redact_text(text)
    items.append({
        "ordinal": 0,
        "occurred_at": envelope["occurred_at"],
        "role": role,
        "surface": surface,
        "text_redacted": cleaned,
        "entities": projected_entities(legacy_engine(), cleaned),
        "receipt": item_receipt(envelope["source_id"], envelope["native_id"], revision, 0),
    })
    return items, metadata
