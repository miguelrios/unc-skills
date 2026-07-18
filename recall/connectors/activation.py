"""Closed activation intent and content-free lifecycle for registered connectors."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from types import MappingProxyType
from typing import Any, Mapping

from connectors.host import AuthorityReference, ConnectorHostError
from connectors.registry import (
    REGISTRY,
    ConnectorDefinition,
    ConnectorDefinitionV3,
    ConnectorRegistryError,
    activation_surface,
    definition,
)


MAX_CONFIG_BYTES = 64 * 1024
SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{2,255}\Z")
PRINCIPAL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}\Z")
CONFIG_FIELDS = {
    "schema_version",
    "connector_id",
    "source_id",
    "principal_id",
    "privacy_mode",
    "authority_references",
    "selectors",
}
TRANSITIONS = {
    "enable": ({"configured"}, "enabled"),
    "pause": ({"enabled"}, "paused"),
    "resume": ({"paused"}, "enabled"),
    "revoke": ({"configured", "enabled", "paused"}, "revoked"),
    "uninstall": ({"configured", "enabled", "paused", "revoked"}, "uninstalled"),
}
STATES = {"configured", "enabled", "paused", "revoked", "uninstalled"}
ACTIONS = {"configure", *TRANSITIONS}
DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class ActivationError(ValueError):
    pass


def activation_catalog() -> dict[str, Any]:
    """Return registry-derived activation truth without inspecting runtime state."""

    return {
        "schema_version": 1,
        "mode": "integration-activation-catalog",
        "credential_reads": 0,
        "source_reads": 0,
        "network_requests": 0,
        "writes": 0,
        "connectors": sorted(
            (activation_surface(item) for item in REGISTRY),
            key=lambda value: value["connector_id"],
        ),
    }


def _required_references(
    item: ConnectorDefinition | ConnectorDefinitionV3,
) -> set[str]:
    if isinstance(item, ConnectorDefinitionV3):
        if item.mode == "push":
            return set()
        required = {"brain"}
        if item.auth.kind in {"api_token", "oauth2"}:
            required.add("source")
        return required
    return set(item.authority_slots)


def _selector_value(value: Any) -> Any:
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if not -(2**31) <= value <= 2**31 - 1:
            raise ActivationError("invalid_selectors")
        return value
    if isinstance(value, str):
        if not value or "\x00" in value or len(value.encode()) > 4_096:
            raise ActivationError("invalid_selectors")
        return value
    if isinstance(value, list):
        if len(value) > 128:
            raise ActivationError("invalid_selectors")
        copied = [_selector_value(item) for item in value]
        if any(not isinstance(item, str) for item in copied):
            raise ActivationError("invalid_selectors")
        if copied != sorted(copied) or len(copied) != len(set(copied)):
            raise ActivationError("invalid_selectors")
        return copied
    raise ActivationError("invalid_selectors")


@dataclass(frozen=True)
class ActivationConfig:
    connector_id: str
    source_id: str
    principal_id: str
    privacy_mode: str
    authority_references: Mapping[str, AuthorityReference]
    selectors: Mapping[str, Any]
    digest: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ActivationConfig":
        if not isinstance(value, Mapping) or set(value) != CONFIG_FIELDS:
            raise ActivationError("invalid_config_fields")
        if value["schema_version"] != 1 or isinstance(value["schema_version"], bool):
            raise ActivationError("invalid_schema_version")
        connector_id = value["connector_id"]
        try:
            item = definition(connector_id)
        except ConnectorRegistryError as error:
            raise ActivationError("unknown_connector") from error
        source_id = value["source_id"]
        principal_id = value["principal_id"]
        if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
            raise ActivationError("invalid_source_id")
        if not isinstance(principal_id, str) or not PRINCIPAL_ID.fullmatch(principal_id):
            raise ActivationError("invalid_principal_id")
        privacy_mode = value["privacy_mode"]
        if privacy_mode not in item.privacy_modes:
            raise ActivationError("invalid_privacy_mode")

        raw_references = value["authority_references"]
        if (
            not isinstance(raw_references, Mapping)
            or set(raw_references) != _required_references(item)
        ):
            raise ActivationError("authority_reference_mismatch")
        references: dict[str, AuthorityReference] = {}
        try:
            for slot, reference in raw_references.items():
                references[slot] = AuthorityReference.from_mapping(reference)
        except ConnectorHostError as error:
            raise ActivationError("invalid_authority_reference") from error
        fingerprints = [reference.fingerprint() for reference in references.values()]
        if len(fingerprints) != len(set(fingerprints)):
            raise ActivationError("authority_reference_alias")

        raw_selectors = value["selectors"]
        allowed_selectors = (
            set(item.selection_fields)
            if isinstance(item, ConnectorDefinitionV3)
            else set()
        )
        if not isinstance(raw_selectors, Mapping) or set(raw_selectors) - allowed_selectors:
            raise ActivationError("invalid_selectors")
        selectors = {
            key: _selector_value(selector)
            for key, selector in sorted(raw_selectors.items())
        }
        try:
            canonical = json.dumps(
                value,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        except (TypeError, ValueError):
            raise ActivationError("invalid_config") from None
        return cls(
            connector_id=connector_id,
            source_id=source_id,
            principal_id=principal_id,
            privacy_mode=privacy_mode,
            authority_references=MappingProxyType(references),
            selectors=MappingProxyType(selectors),
            digest=hashlib.sha256(canonical).hexdigest(),
        )


def load_activation_config(path: Path) -> ActivationConfig:
    """Load a private regular config without following links or rendering its path."""

    path = Path(path)
    try:
        parent = path.parent.lstat()
        metadata = path.lstat()
    except OSError:
        raise ActivationError("config_unavailable") from None
    if not stat.S_ISDIR(parent.st_mode) or stat.S_IMODE(parent.st_mode) & 0o077:
        raise ActivationError("config_parent_not_private")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ActivationError("config_not_regular")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ActivationError("config_not_private")
    if metadata.st_size > MAX_CONFIG_BYTES:
        raise ActivationError("config_too_large")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != metadata.st_dev
            or opened.st_ino != metadata.st_ino
            or opened.st_size > MAX_CONFIG_BYTES
        ):
            raise ActivationError("config_changed")
        raw = os.read(descriptor, MAX_CONFIG_BYTES + 1)
    except ActivationError:
        raise
    except OSError:
        raise ActivationError("config_unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ActivationError("config_invalid_json") from None
    return ActivationConfig.from_mapping(value)


def preview_activation_config(config: ActivationConfig) -> dict[str, Any]:
    if not isinstance(config, ActivationConfig):
        raise ActivationError("invalid_config")
    item = definition(config.connector_id)
    surface = activation_surface(item)
    counts = {"file": 0, "keychain": 0}
    for reference in config.authority_references.values():
        counts[reference.kind] += 1
    result: dict[str, Any] = {
        "schema_version": 1,
        "mode": "integration-activation-preview",
        **surface,
        "privacy_mode": config.privacy_mode,
        "selector_count": len(config.selectors),
        "authority_reference_counts": counts,
        "credential_reads": 0,
        "source_reads": 0,
        "network_requests": 0,
        "writes": 0,
    }
    if config.connector_id == "custom.webhook":
        result.update({
            "required_capability": "webhook",
            "source_bound": True,
            "principal_bound": True,
            "credential_issued": False,
        })
    return result


class ActivationStore:
    """Private content-free state machine; provider configuration remains external."""

    def __init__(
        self,
        path: Path,
        *,
        create: bool = True,
        read_only: bool = False,
    ):
        if create and read_only:
            raise ActivationError("invalid_state_mode")
        self.path = Path(path)
        expected = self._prepare_path(create=create)
        self.connection: sqlite3.Connection | None = None
        if expected is None:
            return
        if read_only:
            self.connection = sqlite3.connect(
                f"{self.path.absolute().as_uri()}?mode=ro",
                uri=True,
            )
        else:
            self.connection = sqlite3.connect(self.path)
        opened = self.path.lstat()
        if (
            stat.S_ISLNK(opened.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != expected.st_dev
            or opened.st_ino != expected.st_ino
            or stat.S_IMODE(opened.st_mode) != 0o600
        ):
            self.connection.close()
            raise ActivationError("state_changed")
        if create:
            self.connection.execute("PRAGMA journal_mode=DELETE")
            self.connection.execute("""
                CREATE TABLE IF NOT EXISTS activations(
                    connector_id TEXT PRIMARY KEY,
                    config_digest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    last_action TEXT NOT NULL
                )
            """)
            self.connection.commit()

    def _prepare_path(self, *, create: bool) -> os.stat_result | None:
        try:
            parent = self.path.parent.lstat()
        except OSError:
            raise ActivationError("state_parent_unavailable") from None
        if not stat.S_ISDIR(parent.st_mode) or stat.S_IMODE(parent.st_mode) & 0o077:
            raise ActivationError("state_parent_not_private")
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            if not create:
                return None
            descriptor: int | None = None
            try:
                descriptor = os.open(
                    self.path,
                    os.O_RDWR
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
                metadata = os.fstat(descriptor)
            except OSError:
                raise ActivationError("state_unavailable") from None
            finally:
                if descriptor is not None:
                    os.close(descriptor)
        except OSError:
            raise ActivationError("state_unavailable") from None
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise ActivationError("state_not_regular")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ActivationError("state_not_private")
        return metadata

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()

    def _row(self, connector_id: str) -> tuple[str, str, int, str] | None:
        if self.connection is None:
            return None
        try:
            row = self.connection.execute(
                "SELECT config_digest,state,revision,last_action "
                "FROM activations WHERE connector_id=?",
                (connector_id,),
            ).fetchone()
        except sqlite3.DatabaseError:
            raise ActivationError("state_invalid") from None
        if row is None:
            return None
        if (
            not isinstance(row, tuple)
            or len(row) != 4
            or not isinstance(row[0], str)
            or not DIGEST.fullmatch(row[0])
            or row[1] not in STATES
            or type(row[2]) is not int
            or not 1 <= row[2] <= 2**63 - 1
            or row[3] not in ACTIONS
        ):
            raise ActivationError("state_invalid")
        return row

    @staticmethod
    def _result(
        connector_id: str,
        state: str,
        revision: int,
        replay: bool,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "mode": "integration-activation-lifecycle",
            "connector_id": connector_id,
            "state": state,
            "revision": revision,
            "replay": replay,
        }

    def configure(self, config: ActivationConfig) -> dict[str, Any]:
        if not isinstance(config, ActivationConfig):
            raise ActivationError("invalid_config")
        if self.connection is None:
            raise ActivationError("state_unavailable")
        with self.connection:
            row = self._row(config.connector_id)
            if row is None:
                self.connection.execute(
                    "INSERT INTO activations VALUES (?,?,?,?,?)",
                    (config.connector_id, config.digest, "configured", 1, "configure"),
                )
                return self._result(config.connector_id, "configured", 1, False)
            digest, state, revision, last_action = row
            if digest == config.digest and state == "configured" and last_action == "configure":
                return self._result(config.connector_id, state, revision, True)
            if state not in {"configured", "paused", "revoked", "uninstalled"}:
                raise ActivationError("invalid_transition")
            revision += 1
            self.connection.execute(
                "UPDATE activations SET config_digest=?,state='configured',"
                "revision=?,last_action='configure' WHERE connector_id=?",
                (config.digest, revision, config.connector_id),
            )
            return self._result(config.connector_id, "configured", revision, False)

    def transition(self, connector_id: str, action: str) -> dict[str, Any]:
        try:
            definition(connector_id)
        except ConnectorRegistryError as error:
            raise ActivationError("unknown_connector") from error
        transition = TRANSITIONS.get(action)
        if transition is None:
            raise ActivationError("invalid_action")
        if self.connection is None:
            raise ActivationError("activation_not_configured")
        allowed, target = transition
        with self.connection:
            row = self._row(connector_id)
            if row is None:
                raise ActivationError("activation_not_configured")
            _digest, state, revision, last_action = row
            if state == target and last_action == action:
                return self._result(connector_id, state, revision, True)
            if state not in allowed:
                raise ActivationError("invalid_transition")
            revision += 1
            self.connection.execute(
                "UPDATE activations SET state=?,revision=?,last_action=? "
                "WHERE connector_id=?",
                (target, revision, action, connector_id),
            )
            return self._result(connector_id, target, revision, False)

    def status(self, connector_id: str) -> dict[str, Any]:
        try:
            definition(connector_id)
        except ConnectorRegistryError as error:
            raise ActivationError("unknown_connector") from error
        row = self._row(connector_id)
        if row is None:
            return {
                "schema_version": 1,
                "mode": "integration-activation-status",
                "connector_id": connector_id,
                "state": "absent",
                "revision": 0,
            }
        _digest, state, revision, _last_action = row
        return {
            "schema_version": 1,
            "mode": "integration-activation-status",
            "connector_id": connector_id,
            "state": state,
            "revision": revision,
        }


__all__ = [
    "ActivationConfig",
    "ActivationError",
    "ActivationStore",
    "activation_catalog",
    "load_activation_config",
    "preview_activation_config",
]
