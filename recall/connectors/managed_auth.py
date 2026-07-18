"""Disabled-by-default provider-neutral managed-auth adapter contract."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Protocol

from connectors.sdk import (
    IDENTITY,
    SOURCE_ID,
    TYPED_RECORD_FIELDS,
    ConnectorContractError,
    ConnectorPage,
    ConnectorRecordV2,
    ConnectorRunError,
)


MAX_WAKEUP_BYTES = 65_536
MAX_RECORD_BYTES = 1_000_000
MAX_RECORDS = 500
MAX_CURSOR_BYTES = 2_048
SIGNATURE = re.compile(r"[0-9a-f]{64}\Z")
ACTIONS = {"ADDED", "DELETED", "UPDATED"}
WAKEUP_FIELDS = {
    "connectionId",
    "model",
    "modifiedAfter",
    "providerConfigKey",
    "responseResults",
}
RESULT_FIELDS = {"added", "deleted", "updated"}


def _timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConnectorContractError(f"managed_{label}_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ConnectorContractError(f"managed_{label}_invalid") from None
    if parsed.tzinfo is None:
        raise ConnectorContractError(f"managed_{label}_invalid")
    return value


def _identity(value: Any, label: str) -> str:
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise ConnectorContractError(f"managed_{label}_invalid")
    return value


def _closed_json(raw: bytes, label: str) -> Any:
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    try:
        return json.loads(
            raw,
            object_pairs_hook=object_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
    ):
        raise ConnectorContractError(f"managed_{label}_invalid") from None


@dataclass(frozen=True)
class ManagedWakeup:
    connection_id: str
    provider_config_key: str
    model: str
    modified_after: str
    added: int
    updated: int
    deleted: int

    @property
    def changed_records(self) -> int:
        return self.added + self.updated + self.deleted


def verify_nango_wakeup(
    *,
    body: bytes,
    signature: str,
    secret: bytes,
) -> ManagedWakeup:
    if (
        not isinstance(body, bytes)
        or not body
        or len(body) > MAX_WAKEUP_BYTES
        or not isinstance(secret, bytes)
        or not 16 <= len(secret) <= 4_096
        or not isinstance(signature, str)
        or not SIGNATURE.fullmatch(signature)
    ):
        raise ConnectorContractError("managed_wakeup_signature_invalid")
    expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ConnectorContractError("managed_wakeup_signature_invalid")
    value = _closed_json(body, "wakeup")
    if not isinstance(value, dict) or set(value) != WAKEUP_FIELDS:
        raise ConnectorContractError("managed_wakeup_invalid")
    results = value["responseResults"]
    if (
        not isinstance(results, dict)
        or set(results) != RESULT_FIELDS
        or any(
            type(results[field]) is not int
            or not 0 <= results[field] <= 1_000_000_000
            for field in RESULT_FIELDS
        )
        or sum(results.values()) > 1_000_000_000
    ):
        raise ConnectorContractError("managed_wakeup_invalid")
    return ManagedWakeup(
        connection_id=_identity(value["connectionId"], "connection_id"),
        provider_config_key=_identity(
            value["providerConfigKey"], "provider_config_key"
        ),
        model=_identity(value["model"], "model"),
        modified_after=_timestamp(value["modifiedAfter"], "modified_after"),
        added=results["added"],
        updated=results["updated"],
        deleted=results["deleted"],
    )


@dataclass(frozen=True)
class ManagedRecord:
    id: str
    last_action: str
    deleted_at: str | None
    data: Mapping[str, Any]

    def __post_init__(self) -> None:
        _identity(self.id, "record_id")
        if self.last_action not in ACTIONS:
            raise ConnectorContractError("managed_record_action_invalid")
        if not isinstance(self.data, Mapping):
            raise ConnectorContractError("managed_record_data_invalid")
        try:
            encoded = json.dumps(
                dict(self.data),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
            copied = json.loads(encoded)
        except (TypeError, ValueError, RecursionError):
            raise ConnectorContractError("managed_record_data_invalid") from None
        if len(encoded) > MAX_RECORD_BYTES:
            raise ConnectorContractError("managed_record_data_invalid")
        if self.last_action == "DELETED":
            if copied or self.deleted_at is None:
                raise ConnectorContractError("managed_record_delete_invalid")
            _timestamp(self.deleted_at, "deleted_at")
        elif self.deleted_at is not None or not copied:
            raise ConnectorContractError("managed_record_state_invalid")
        object.__setattr__(self, "data", copied)


@dataclass(frozen=True)
class ManagedPage:
    records: tuple[ManagedRecord, ...]
    next_cursor: str
    has_more: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.records, tuple)
            or len(self.records) > MAX_RECORDS
            or any(not isinstance(record, ManagedRecord) for record in self.records)
            or len({record.id for record in self.records}) != len(self.records)
            or not isinstance(self.next_cursor, str)
            or not self.next_cursor
            or len(self.next_cursor.encode()) > MAX_CURSOR_BYTES
            or any(
                ord(character) < 33 or ord(character) > 126
                for character in self.next_cursor
            )
            or not isinstance(self.has_more, bool)
            or (self.has_more and not self.records)
        ):
            raise ConnectorContractError("managed_page_invalid")


@dataclass(frozen=True)
class ManagedProjection:
    occurred_at: str
    content: Mapping[str, Any]
    provenance: Mapping[str, Any]
    native_parent_id: str | None = None

    def __post_init__(self) -> None:
        _timestamp(self.occurred_at, "projection_timestamp")
        if (
            not isinstance(self.content, Mapping)
            or not isinstance(self.provenance, Mapping)
            or (
                self.native_parent_id is not None
                and (
                    not isinstance(self.native_parent_id, str)
                    or not IDENTITY.fullmatch(self.native_parent_id)
                )
            )
        ):
            raise ConnectorContractError("managed_projection_invalid")
        object.__setattr__(self, "content", dict(self.content))
        object.__setattr__(self, "provenance", dict(self.provenance))


class ManagedTransport(Protocol):
    bound_source_id: str

    def fetch_records(
        self,
        *,
        connection_id: str,
        provider_config_key: str,
        model: str,
        cursor: str | None,
    ) -> ManagedPage: ...

    def revoke(
        self,
        *,
        connection_id: str,
        provider_config_key: str,
    ) -> None: ...


ManagedMapper = Callable[[ManagedRecord], ManagedProjection]


class ManagedAuthConnector:
    """Adapt one explicitly constructed managed sync to the Recall pull contract."""

    connector_id = "managed-auth"

    def __init__(
        self,
        *,
        source_id: str,
        connection_id: str,
        provider_config_key: str,
        model: str,
        record_kind: str,
        transport: ManagedTransport,
        mapper: ManagedMapper,
    ):
        if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
            raise ConnectorContractError("managed_source_id_invalid")
        self.connection_id = _identity(connection_id, "connection_id")
        self.provider_config_key = _identity(
            provider_config_key, "provider_config_key"
        )
        self.model = _identity(model, "model")
        if record_kind not in TYPED_RECORD_FIELDS:
            raise ConnectorContractError("managed_record_kind_invalid")
        if (
            transport is None
            or getattr(transport, "bound_source_id", None) != source_id
        ):
            raise ConnectorContractError("managed_source_authority_mismatch")
        if not callable(mapper):
            raise ConnectorContractError("managed_mapper_invalid")
        self.source_id = source_id
        self.record_kind = record_kind
        self.transport = transport
        self.mapper = mapper
        self.revoked = False

    def accept_wakeup(
        self,
        *,
        body: bytes,
        signature: str,
        secret: bytes,
    ) -> ManagedWakeup:
        wakeup = verify_nango_wakeup(
            body=body, signature=signature, secret=secret
        )
        if (
            wakeup.connection_id != self.connection_id
            or wakeup.provider_config_key != self.provider_config_key
            or wakeup.model != self.model
        ):
            raise ConnectorContractError("managed_wakeup_binding_invalid")
        return wakeup

    def pull(self, cursor: str | None) -> ConnectorPage:
        if self.revoked:
            raise ConnectorRunError("connector_authority_revoked")
        if cursor is not None and (
            not isinstance(cursor, str)
            or not cursor
            or len(cursor.encode()) > MAX_CURSOR_BYTES
        ):
            raise ConnectorContractError("managed_cursor_invalid")
        page = self.transport.fetch_records(
            connection_id=self.connection_id,
            provider_config_key=self.provider_config_key,
            model=self.model,
            cursor=cursor,
        )
        if not isinstance(page, ManagedPage):
            raise ConnectorContractError("managed_page_invalid")
        projected = []
        for record in page.records:
            native_id = "managed:" + hashlib.sha256(
                f"{self.connection_id}\0{self.model}\0{record.id}".encode()
            ).hexdigest()
            if record.last_action == "DELETED":
                projected.append(ConnectorRecordV2(
                    schema_version=2,
                    native_id=native_id,
                    occurred_at=record.deleted_at or "",
                    content={"kind": self.record_kind},
                    provenance={
                        "uri": f"connector://managed-auth/{native_id}",
                        "explicit_managed_delete": True,
                    },
                    deleted=True,
                ))
                continue
            projection = self.mapper(record)
            if (
                not isinstance(projection, ManagedProjection)
                or projection.content.get("kind") != self.record_kind
            ):
                raise ConnectorContractError("managed_projection_invalid")
            projected.append(ConnectorRecordV2(
                schema_version=2,
                native_id=native_id,
                native_parent_id=projection.native_parent_id,
                occurred_at=projection.occurred_at,
                content=dict(projection.content),
                provenance=dict(projection.provenance),
            ))
        return ConnectorPage(
            records=tuple(projected),
            next_cursor=page.next_cursor,
            has_more=page.has_more,
        )

    def revoke(self) -> None:
        if self.revoked:
            return
        self.transport.revoke(
            connection_id=self.connection_id,
            provider_config_key=self.provider_config_key,
        )
        self.revoked = True


__all__ = [
    "ManagedAuthConnector",
    "ManagedPage",
    "ManagedProjection",
    "ManagedRecord",
    "ManagedTransport",
    "ManagedWakeup",
    "verify_nango_wakeup",
]
