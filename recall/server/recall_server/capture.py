from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from .projectors import canonical_json, redact_text


CAPTURE_ORIGIN_RE = re.compile(r"[a-z][a-z0-9_.-]{1,63}\Z")
TAG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z")
ALLOWED_PROVENANCE_SCHEMES = {"https", "manual", "connector", "export"}
MAX_CAPTURE_BODY_CHARS = 32_000
MAX_CAPTURE_BODY_BYTES = 128 * 1024
MAX_CAPTURE_ENCODED_BYTES = 224 * 1024


def _text(
    value: Any,
    label: str,
    *,
    character_limit: int,
    byte_limit: int,
) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > character_limit
        or len(value.encode()) > byte_limit
    ):
        raise ValueError(f"capture {label} is invalid")
    return value


def _timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("capture timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("capture timestamp is invalid") from None
    if parsed.tzinfo is None:
        raise ValueError("capture timestamp is invalid")
    return value


def _provenance(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"uri"}:
        raise ValueError("capture provenance is invalid")
    uri = value["uri"]
    parsed = urlparse(uri) if isinstance(uri, str) else None
    if (
        not parsed
        or parsed.scheme not in ALLOWED_PROVENANCE_SCHEMES
        or (parsed.scheme == "https" and not parsed.hostname)
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("capture provenance is invalid")
    return {"uri": uri}


def build_capture_event(arguments: Any, principal: dict) -> tuple[dict, dict]:
    required = {
        "schema_version",
        "title",
        "body",
        "occurred_at",
        "provenance",
    }
    allowed = required | {"tags"}
    if (
        not isinstance(arguments, dict)
        or set(arguments) - allowed
        or required - set(arguments)
        or type(arguments["schema_version"]) is not int
        or arguments["schema_version"] != 1
    ):
        raise ValueError("capture schema is invalid")
    origin = principal.get("capture_origin")
    source_id = principal.get("source_id")
    principal_id = principal.get("principal_id")
    if (
        not isinstance(origin, str)
        or not CAPTURE_ORIGIN_RE.fullmatch(origin)
        or not isinstance(source_id, str)
        or not isinstance(principal_id, str)
        or not principal_id
    ):
        raise ValueError("capture authority is invalid")
    title = _text(
        arguments["title"],
        "title",
        character_limit=500,
        byte_limit=2_000,
    )
    body = _text(
        arguments["body"],
        "body",
        character_limit=MAX_CAPTURE_BODY_CHARS,
        byte_limit=MAX_CAPTURE_BODY_BYTES,
    )
    tags = arguments.get("tags", [])
    if (
        not isinstance(tags, list)
        or len(tags) > 20
        or not all(isinstance(tag, str) and TAG_RE.fullmatch(tag) for tag in tags)
        or len(tags) != len(set(tags))
    ):
        raise ValueError("capture tags are invalid")
    occurred_at = _timestamp(arguments["occurred_at"])
    provenance = _provenance(arguments["provenance"])
    normalized = {
        "schema_version": 1,
        "title": title,
        "body": body,
        "origin": origin,
        "occurred_at": occurred_at,
        "tags": sorted(tags),
        "provenance": provenance,
    }
    encoded = canonical_json(normalized)
    if len(encoded) > MAX_CAPTURE_ENCODED_BYTES:
        raise ValueError("capture exceeds the byte limit")
    native_id = "capture_" + hashlib.sha256(encoded).hexdigest()[:40]
    scrubbed_title = redact_text(title)
    scrubbed_body = redact_text(body)
    content = {
        "schema_version": 1,
        "title": scrubbed_title,
        "body": scrubbed_body,
        "origin": origin,
        "tags": sorted(tags),
    }
    event = {
        "schema_version": 1,
        "source_id": source_id,
        "native_id": native_id,
        "native_parent_id": native_id,
        "kind": "capture",
        "occurred_at": occurred_at,
        "observed_at": occurred_at,
        "principal_id": principal_id,
        "visibility": "private",
        "content_type": "application/json",
        "content": content,
        "provenance": provenance,
        "content_sha256": hashlib.sha256(canonical_json(content)).hexdigest(),
    }
    return event, {
        "mode": "scrub",
        "changed_fields": int(scrubbed_title != title) + int(scrubbed_body != body),
    }


def parse_capture_receipt(receipt: str, source_id: str) -> tuple[str, int, str]:
    event_part = receipt.split("#", 1)[0] if isinstance(receipt, str) else ""
    try:
        base, revision_text = event_part.rsplit("?rev=", 1)
        receipt_source, native_id = base.removeprefix("recall://").split("/", 1)
        revision = int(revision_text)
    except (TypeError, ValueError):
        raise ValueError("invalid capture receipt") from None
    if (
        not base.startswith("recall://")
        or receipt_source != source_id
        or not native_id.startswith("capture_")
        or revision < 1
    ):
        raise ValueError("invalid capture receipt")
    return native_id, revision, event_part


def build_forget_event(
    *,
    source_id: str,
    principal_id: str,
    native_id: str,
    deleted_receipt: str,
    captured_at: datetime,
) -> dict:
    occurred_at = (
        captured_at.astimezone(timezone.utc) + timedelta(microseconds=1)
    ).isoformat().replace("+00:00", "Z")
    content = {
        "target_native_id": native_id,
        "deleted_receipt": deleted_receipt,
    }
    return {
        "schema_version": 1,
        "source_id": source_id,
        "native_id": native_id,
        "native_parent_id": native_id,
        "kind": "tombstone",
        "occurred_at": occurred_at,
        "observed_at": occurred_at,
        "principal_id": principal_id,
        "visibility": "private",
        "content_type": "application/json",
        "content": content,
        "provenance": {"uri": "manual://recall-forget"},
        "content_sha256": hashlib.sha256(canonical_json(content)).hexdigest(),
    }
