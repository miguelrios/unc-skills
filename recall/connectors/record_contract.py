"""Dependency-free loader for the shared typed connector record contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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

