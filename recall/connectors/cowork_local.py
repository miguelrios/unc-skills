"""Privacy-minimal projection contract for Claude Cowork local project records."""

from __future__ import annotations

import re
from typing import Any, Mapping

from connectors.sdk import ConnectorContractError, ConnectorRecord


CONNECTOR_ID = "anthropic.cowork-local"
MAX_TEXT_CHARS = 500_000
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@=-]{0,127}\Z")


class CoworkLocalError(ValueError):
    """A stable, content-free local Cowork record contract failure."""


def _required_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
        raise CoworkLocalError(f"invalid_{label}")
    return value


def _natural_language(value: Any) -> str | None:
    if isinstance(value, str):
        text = value
    elif isinstance(value, list):
        parts: list[str] = []
        for block in value:
            if not isinstance(block, Mapping) or block.get("type") != "text":
                continue
            candidate = block.get("text")
            if not isinstance(candidate, str):
                raise CoworkLocalError("invalid_text_block")
            if candidate:
                parts.append(candidate)
        text = "\n".join(parts)
    else:
        raise CoworkLocalError("invalid_message_content")
    if not text:
        return None
    if len(text) > MAX_TEXT_CHARS:
        raise CoworkLocalError("message_text_too_large")
    return text


def project_cowork_record(value: Mapping[str, Any]) -> ConnectorRecord | None:
    """Project one eligible Cowork message without copying ambient session metadata.

    Selection is intentionally small: only non-meta user/assistant records and only their
    natural-language string/text blocks. Unknown record and block types are skipped so an
    additive application schema cannot silently widen the privacy boundary.
    """

    if not isinstance(value, Mapping):
        raise CoworkLocalError("record_not_object")
    record_type = value.get("type")
    if record_type not in {"user", "assistant"}:
        return None
    if value.get("isMeta") is True:
        return None
    message = value.get("message")
    if not isinstance(message, Mapping) or message.get("role") != record_type:
        raise CoworkLocalError("invalid_message_role")
    session_id = _required_id(value.get("sessionId"), "session_id")
    message_id = _required_id(value.get("uuid"), "message_id")
    text = _natural_language(message.get("content"))
    if text is None:
        return None
    timestamp = value.get("timestamp")
    if not isinstance(timestamp, str):
        raise CoworkLocalError("invalid_timestamp")
    native_id = f"{session_id}/{message_id}"
    content = {
        "role": record_type,
        "text": text,
        "session_id": session_id,
        "message_id": message_id,
    }
    parent = value.get("parentUuid")
    if parent is not None:
        parent_id = _required_id(parent, "parent_message_id")
        content["parent_native_id"] = f"{session_id}/{parent_id}"
    try:
        return ConnectorRecord(
            schema_version=1,
            native_id=native_id,
            occurred_at=timestamp,
            content=content,
            provenance={"uri": f"connector://{CONNECTOR_ID}/{native_id}"},
        )
    except ConnectorContractError as error:
        raise CoworkLocalError("invalid_connector_record") from error
