"""Closed, deterministic capture-v1 client for deliberate agent memory."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from client.mac import MemoryClient, _envelope, canonical_json
from privacy.policy import PrivacyPolicy, summarize_receipts


CAPTURE_SCHEMA_VERSION = 1
MAX_CAPTURE_BYTES = 1_000_000
ORIGIN = re.compile(r"[a-z][a-z0-9_.-]{1,63}\Z")
TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z")
ALLOWED_PROVENANCE_SCHEMES = {"https", "manual", "connector", "export"}


class CaptureContractError(ValueError):
    pass


def _text(value: Any, label: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > limit:
        raise CaptureContractError(f"capture {label} is invalid")
    return value


def _timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise CaptureContractError("capture timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise CaptureContractError("capture timestamp is invalid") from error
    if parsed.tzinfo is None:
        raise CaptureContractError("capture timestamp is invalid")
    return value


def validate_capture(value: Any) -> dict[str, Any]:
    required = {"schema_version", "title", "body", "origin", "occurred_at", "provenance"}
    allowed = required | {"tags"}
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        raise CaptureContractError("capture schema is invalid")
    if value["schema_version"] != CAPTURE_SCHEMA_VERSION:
        raise CaptureContractError("capture schema version is unsupported")
    title = _text(value["title"], "title", 500)
    body = _text(value["body"], "body", MAX_CAPTURE_BYTES)
    origin = value["origin"]
    if not isinstance(origin, str) or not ORIGIN.fullmatch(origin):
        raise CaptureContractError("capture origin is invalid")
    tags = value.get("tags", [])
    if not isinstance(tags, list) or len(tags) > 20 or not all(isinstance(tag, str) and TAG.fullmatch(tag) for tag in tags):
        raise CaptureContractError("capture tags are invalid")
    if len(tags) != len(set(tags)):
        raise CaptureContractError("capture tags contain duplicates")
    provenance = value["provenance"]
    if not isinstance(provenance, dict) or set(provenance) != {"uri"}:
        raise CaptureContractError("capture provenance is invalid")
    uri = provenance["uri"]
    parsed = urlparse(uri) if isinstance(uri, str) else None
    if (
        not parsed or parsed.scheme not in ALLOWED_PROVENANCE_SCHEMES
        or (parsed.scheme == "https" and not parsed.hostname)
        or parsed.username or parsed.password or parsed.query or parsed.fragment
    ):
        raise CaptureContractError("capture provenance is invalid")
    normalized = {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "title": title,
        "body": body,
        "origin": origin,
        "occurred_at": _timestamp(value["occurred_at"]),
        "tags": sorted(tags),
        "provenance": {"uri": uri},
    }
    try:
        encoded = canonical_json(normalized)
    except (TypeError, ValueError) as error:
        raise CaptureContractError("capture must be finite JSON") from error
    if len(encoded) > MAX_CAPTURE_BYTES:
        raise CaptureContractError("capture exceeds the byte limit")
    return normalized


class CaptureClient(MemoryClient):
    def validate(self, value: Any) -> dict[str, Any]:
        return validate_capture(value)

    def capture(self, value: Any) -> dict[str, Any]:
        normalized = validate_capture(value)
        native_id = "capture_" + hashlib.sha256(canonical_json(normalized)).hexdigest()[:40]
        candidate = {
            "content": {
                "schema_version": CAPTURE_SCHEMA_VERSION,
                "title": normalized["title"],
                "body": normalized["body"],
                "origin": normalized["origin"],
                "tags": normalized["tags"],
            },
            "provenance": normalized["provenance"],
        }
        decision = self.privacy.apply(candidate)
        privacy = summarize_receipts([decision.receipt()], self.privacy.mode)
        if decision.action == "drop":
            return {
                "status": "privacy_filtered", "native_id": native_id,
                "privacy": {**privacy, "action": "drop"},
            }
        event = _envelope(
            source_id=self.source_id, native_id=native_id, kind="capture",
            content=decision.value["content"], principal_id=self.principal_id,
            visibility=self.visibility, provenance=decision.value["provenance"],
            occurred_at=normalized["occurred_at"],
        )
        acknowledgement = self._ingest_prepared(
            [event], privacy if self.privacy.mode != "off" else None,
        )
        result = {
            "status": acknowledgement.get("status", "committed"),
            "native_id": native_id,
            "replay": bool(acknowledgement.get("replay", False)),
        }
        receipts = acknowledgement.get("receipts", [])
        if receipts:
            result["receipt"] = receipts[0]
        if "privacy" in acknowledgement:
            actions = acknowledgement["privacy"]["actions"]
            action = next(iter(actions)) if len(actions) == 1 else "mixed"
            result["privacy"] = {**acknowledgement["privacy"], "action": action}
        return result

    def forget(self, receipt: str) -> dict[str, Any]:
        deleted = self.delete(receipt)
        return {
            "status": deleted["acknowledgement"].get("status", "committed"),
            "native_id": deleted["native_id"], "receipt": deleted["receipt"],
            "replay": bool(deleted["acknowledgement"].get("replay", False)),
        }
