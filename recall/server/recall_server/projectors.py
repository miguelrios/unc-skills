from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import PROJECTOR_VERSION

SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|bearer|authorization)[\"']?\s*[=:]\s*[\"']?\S{8,}"
)


def redact_text(value: str) -> str:
    return "\n".join("[REDACTED]" if SECRET_RE.search(line) else line for line in value.splitlines())


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
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def advisory_lock_key(source_id: str, native_id: str) -> str:
    # PostgreSQL text cannot contain NUL. Unit Separator is boundary-preserving
    # for the validated source/native identifiers and safe for hashtextextended.
    return source_id + "\x1f" + native_id


def content_sha256(envelope: dict) -> str:
    return hashlib.sha256(canonical_json(envelope["content"])).hexdigest()


def validate_envelope(envelope: dict) -> dict:
    required = (
        "schema_version", "source_id", "native_id", "kind", "occurred_at",
        "observed_at", "principal_id", "visibility", "content_type", "content",
        "content_sha256",
    )
    missing = [key for key in required if key not in envelope]
    if missing:
        raise ValueError("missing fields: " + ",".join(missing))
    if envelope["schema_version"] != 1:
        raise ValueError("unsupported schema_version")
    if envelope["visibility"] not in {"private", "shared"}:
        raise ValueError("unsupported visibility")
    actual = content_sha256(envelope)
    if envelope["content_sha256"] != actual:
        raise ValueError("content_sha256 mismatch")
    return envelope


def event_receipt(source_id: str, native_id: str, revision: int) -> str:
    return f"recall://{source_id}/{native_id}?rev={revision}"


def item_receipt(source_id: str, native_id: str, revision: int, ordinal: int) -> str:
    return f"{event_receipt(source_id, native_id, revision)}#item={ordinal}"


def project(envelope: dict, revision: int) -> tuple[list[dict], dict]:
    """Return sanitized items and session metadata for one canonical event."""
    kind = envelope["kind"]
    session_id = envelope.get("native_parent_id") or envelope["native_id"]
    content = envelope["content"]
    items: list[dict] = []
    metadata: dict[str, Any] = {"projector_version": PROJECTOR_VERSION}

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
        for ordinal, (timestamp, surface, text, _entities) in enumerate(parsed):
            items.append({
                "ordinal": ordinal,
                "occurred_at": timestamp,
                "role": surface,
                "surface": surface,
                "text_redacted": engine.clean_text(text),
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
    items.append({
        "ordinal": 0,
        "occurred_at": envelope["occurred_at"],
        "role": role,
        "surface": surface,
        "text_redacted": redact_text(text),
        "receipt": item_receipt(envelope["source_id"], envelope["native_id"], revision, 0),
    })
    return items, metadata
