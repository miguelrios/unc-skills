"""Closed, content-free inventory and health policy for Recall input surfaces."""

from __future__ import annotations

from dataclasses import dataclass
import re
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from privacy.policy import PrivacyPolicy


IDENTITY = re.compile(r"[a-z][a-z0-9_.-]{2,63}\Z")
COMMAND = re.compile(r"[a-z][a-z0-9-]{2,63}\Z")
MODES = {"pull", "push"}
AUTHORITIES = {"brain", "source"}
VISIBILITIES = {"private", "shared"}
PRIVACY_MODES = {"off", "scrub", "drop"}
CHECKPOINTS = {"none", "ack_cursor"}
DELETIONS = {"explicit_receipt", "explicit_export_reference"}
RUNTIME_ERROR_CODES = {
    "brain_invalid_acknowledgement", "brain_unauthorized", "brain_unavailable",
    "connector_disabled", "connector_invalid_page", "connector_rate_limited",
    "connector_spool_error", "connector_unavailable",
    "grep_ai_conflict", "grep_ai_forbidden", "grep_ai_idempotency_key_in_flight",
    "grep_ai_idempotency_key_reuse_mismatch", "grep_ai_idempotency_key_ttl_expired",
    "grep_ai_insufficient_credits", "grep_ai_internal_error", "grep_ai_invalid_error",
    "grep_ai_job_not_found", "grep_ai_rate_limited", "grep_ai_redirect_rejected",
    "grep_ai_resource_not_found", "grep_ai_unauthenticated", "grep_ai_unknown_error",
    "grep_ai_unavailable", "grep_ai_upstream_unavailable", "grep_ai_validation_error",
}
FIELDS = {
    "schema_version", "connector_id", "command", "mode", "authority_slots",
    "visibility_modes", "privacy_modes", "checkpoint", "deletion",
}


class ConnectorRegistryError(ValueError):
    pass


def _closed_tuple(value: Any, label: str, allowed: set[str]) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ConnectorRegistryError(f"invalid_{label}")
    result = tuple(value)
    if any(not isinstance(item, str) or item not in allowed for item in result):
        raise ConnectorRegistryError(f"invalid_{label}")
    if len(result) != len(set(result)):
        raise ConnectorRegistryError(f"duplicate_{label}")
    return result


@dataclass(frozen=True)
class ConnectorDefinition:
    schema_version: int
    connector_id: str
    command: str
    mode: str
    authority_slots: tuple[str, ...]
    visibility_modes: tuple[str, ...]
    privacy_modes: tuple[str, ...]
    checkpoint: str
    deletion: str

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ConnectorRegistryError("invalid_schema_version")
        if not isinstance(self.connector_id, str) or not IDENTITY.fullmatch(self.connector_id):
            raise ConnectorRegistryError("invalid_connector_id")
        if not isinstance(self.command, str) or not COMMAND.fullmatch(self.command):
            raise ConnectorRegistryError("invalid_command")
        if self.mode not in MODES:
            raise ConnectorRegistryError("invalid_mode")
        for value, label, allowed in (
            (self.authority_slots, "authority_slots", AUTHORITIES),
            (self.visibility_modes, "visibility_modes", VISIBILITIES),
            (self.privacy_modes, "privacy_modes", PRIVACY_MODES),
        ):
            normalized = _closed_tuple(value, label, allowed)
            object.__setattr__(self, label, normalized)
        if "brain" not in self.authority_slots:
            raise ConnectorRegistryError("brain_authority_required")
        if self.checkpoint not in CHECKPOINTS:
            raise ConnectorRegistryError("invalid_checkpoint")
        if self.deletion not in DELETIONS:
            raise ConnectorRegistryError("invalid_deletion")
        if self.mode == "push" and self.checkpoint != "none":
            raise ConnectorRegistryError("invalid_push_checkpoint")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ConnectorDefinition":
        if not isinstance(value, Mapping) or set(value) != FIELDS:
            raise ConnectorRegistryError("invalid_definition_shape")
        return cls(**dict(value))

    def to_public(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "connector_id": self.connector_id,
            "command": self.command,
            "mode": self.mode,
            "authority_slots": list(self.authority_slots),
            "visibility_modes": list(self.visibility_modes),
            "privacy_modes": list(self.privacy_modes),
            "checkpoint": self.checkpoint,
            "deletion": self.deletion,
        }


REGISTRY = (
    ConnectorDefinition.from_mapping({
        "schema_version": 1, "connector_id": "recall.capture", "command": "mcp-serve",
        "mode": "push", "authority_slots": ["brain"],
        "visibility_modes": ["private", "shared"], "privacy_modes": ["drop", "off", "scrub"],
        "checkpoint": "none", "deletion": "explicit_receipt",
    }),
    ConnectorDefinition.from_mapping({
        "schema_version": 1, "connector_id": "chatgpt.export_inbox", "command": "export-inbox-sync",
        "mode": "pull", "authority_slots": ["brain"],
        "visibility_modes": ["private"], "privacy_modes": ["drop", "off", "scrub"],
        "checkpoint": "ack_cursor", "deletion": "explicit_export_reference",
    }),
    ConnectorDefinition.from_mapping({
        "schema_version": 1, "connector_id": "grep.ai", "command": "grep-ai-sync",
        "mode": "pull", "authority_slots": ["brain", "source"],
        "visibility_modes": ["private"], "privacy_modes": ["drop", "scrub"],
        "checkpoint": "ack_cursor", "deletion": "explicit_receipt",
    }),
)
_BY_ID = {item.connector_id: item for item in REGISTRY}


def definition(connector_id: str) -> ConnectorDefinition:
    try:
        return _BY_ID[connector_id]
    except (KeyError, TypeError):
        raise ConnectorRegistryError("unknown_connector") from None


def validate_policy(connector_id: str, *, visibility: str, privacy_mode: str,
                    authorities: set[str]) -> ConnectorDefinition:
    item = definition(connector_id)
    if visibility not in item.visibility_modes:
        raise ConnectorRegistryError("visibility_not_allowed")
    if privacy_mode not in item.privacy_modes:
        raise ConnectorRegistryError("privacy_mode_not_allowed")
    if not isinstance(authorities, set) or authorities != set(item.authority_slots):
        raise ConnectorRegistryError("authority_slots_mismatch")
    return item


def preview() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "connector-registry-preview",
        "credential_reads": 0,
        "source_reads": 0,
        "network_requests": 0,
        "writes": 0,
        "connectors": [item.to_public() for item in REGISTRY],
    }


def _base_status(item: ConnectorDefinition, enabled: bool, privacy_mode: str,
                 authorities: set[str]) -> dict[str, Any]:
    if not isinstance(enabled, bool):
        raise ConnectorRegistryError("invalid_enabled")
    if privacy_mode not in item.privacy_modes:
        raise ConnectorRegistryError("privacy_mode_not_allowed")
    if (
        not isinstance(authorities, set)
        or set(authorities) - AUTHORITIES
        or set(authorities) - set(item.authority_slots)
    ):
        raise ConnectorRegistryError("invalid_authority_slots")
    configured = set(item.authority_slots).issubset(authorities)
    return {
        "schema_version": 1,
        "connector_id": item.connector_id,
        "enabled": enabled,
        "configured": configured,
        "mode": item.mode,
        "privacy_mode": privacy_mode,
        "privacy_policy_version": PrivacyPolicy(mode=privacy_mode).apply({}).policy_version,
        "checkpointed": False,
        "pending": 0,
        "pending_pages": 0,
        "error_code": None,
    }


def aggregate_status(connector_id: str, enabled: bool, privacy_mode: str,
                     authorities: set[str], spool_path: Path | None) -> dict[str, Any]:
    item = definition(connector_id)
    result = _base_status(item, enabled, privacy_mode, authorities)
    if not enabled:
        return {**result, "health": "disabled"}
    if not result["configured"]:
        return {**result, "health": "reference_missing"}
    if item.checkpoint == "none":
        return {**result, "health": "ready"}
    if spool_path is None:
        return {**result, "health": "local_state_unavailable"}
    path = Path(spool_path).expanduser()
    if not path.is_file() or path.is_symlink():
        return {**result, "health": "local_state_unavailable"}
    try:
        uri = path.resolve(strict=True).as_uri() + "?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        try:
            tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if not {"meta", "pages", "outbox"}.issubset(tables):
                raise ConnectorRegistryError("local_state_invalid")
            metadata = dict(connection.execute(
                "SELECT key,value FROM meta WHERE key IN ('committed_cursor','last_error_code')"
            ))
            pending_pages = connection.execute("SELECT count(*) FROM pages").fetchone()[0]
            pending = connection.execute("SELECT count(*) FROM outbox").fetchone()[0]
        finally:
            connection.close()
    except ConnectorRegistryError:
        raise
    except (OSError, sqlite3.Error):
        return {**result, "health": "local_state_unavailable"}
    error_code = metadata.get("last_error_code")
    if error_code is not None and error_code not in RUNTIME_ERROR_CODES:
        raise ConnectorRegistryError("local_state_invalid")
    result.update({
        "checkpointed": "committed_cursor" in metadata,
        "pending": int(pending),
        "pending_pages": int(pending_pages),
        "error_code": error_code,
    })
    result["health"] = "degraded" if error_code or pending or pending_pages else "ready"
    return result
