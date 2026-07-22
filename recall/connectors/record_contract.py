"""Dependency-free loader for the shared typed connector record contract."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


OMISSION_CODE = re.compile(r"[a-z][a-z0-9_]{2,63}\Z")


def _json_copy(value: Any, label: str) -> Any:
    try:
        encoded = json.dumps(value, allow_nan=False, separators=(",", ":"))
        return json.loads(encoded)
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"{label} is invalid") from error


def load_typed_record_fields() -> dict[str, dict[str, Any]]:
    path = Path(__file__).resolve().parents[1] / "contracts" / "connector_v2.json"
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("connector v2 contract is unavailable") from error
    if not isinstance(value, dict) or value.get("schema_version") != 2:
        raise RuntimeError("connector v2 contract is invalid")
    kinds = value.get("record_kinds")
    if not isinstance(kinds, dict) or not kinds:
        raise RuntimeError("connector v2 contract is invalid")
    result = {}
    for kind, schema in kinds.items():
        if (
            not isinstance(kind, str)
            or not isinstance(schema, dict)
            or set(schema) != {"required", "optional", "properties"}
            or not isinstance(schema["required"], list)
            or not isinstance(schema["optional"], list)
            or not isinstance(schema["properties"], dict)
        ):
            raise RuntimeError("connector v2 contract is invalid")
        required = set(schema["required"])
        optional = set(schema["optional"])
        if (
            "kind" not in required
            or "content_fidelity" not in required
            or "content_omissions" not in optional
            or required & optional
            or required | optional != set(schema["properties"])
        ):
            raise RuntimeError("connector v2 contract is invalid")
        properties = _json_copy(schema["properties"], "connector v2 properties")
        result[kind] = {
            "required": required,
            "optional": optional,
            "properties": properties,
        }
    return result


TYPED_RECORD_FIELDS = load_typed_record_fields()


def validate_content_fidelity(content: dict[str, Any], *, deleted: bool = False) -> None:
    """Reject dishonest or unstable fidelity claims before records reach durable state."""
    if deleted:
        return
    fidelity = content.get("content_fidelity")
    omissions = content.get("content_omissions")
    valid_omissions = (
        isinstance(omissions, list)
        and bool(omissions)
        and omissions == sorted(set(omissions))
        and all(isinstance(item, str) and OMISSION_CODE.fullmatch(item) for item in omissions)
    )
    if (
        (fidelity == "complete" and omissions is not None)
        or (fidelity == "partial" and not valid_omissions)
        or fidelity not in {"complete", "partial"}
    ):
        raise ValueError("content fidelity is invalid")
    if content.get("format") == "snippet" and (
        fidelity != "partial" or "snippet_fallback" not in omissions
    ):
        raise ValueError("content fidelity is invalid")
