"""Watched, network-free WhatsApp text export connector."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from connectors.local_file import read_stable_file
from connectors.sdk import (
    IDENTITY,
    SOURCE_ID,
    ConnectorContractError,
    ConnectorPage,
    ConnectorRecordV2,
)


CONNECTOR_ID = "whatsapp.export"
MAX_EXPORT_BYTES = 256 * 1024 * 1024
MAX_MESSAGES = 500_000
MAX_LINE_CHARS = 1_000_000
MAX_CYCLE = 2_147_483_647
CURSOR = re.compile(r"whatsapp-v1:(\d{1,10}):(\d{1,10}):([0-9a-f]{64})\Z")
IOS = re.compile(
    r"^\[(\d{1,2})/(\d{1,2})/(\d{2,4}), "
    r"(\d{1,2}):(\d{2})(?::(\d{2}))? ([AP]M)\] (.*)$"
)
ANDROID = re.compile(
    r"^(\d{1,2})/(\d{1,2})/(\d{2,4}), "
    r"(\d{1,2}):(\d{2})(?::(\d{2}))? - (.*)$"
)


def _canonical_tuple(
    value: tuple[str, ...],
    label: str,
    *,
    allow_empty: bool = True,
) -> tuple[str, ...]:
    if (
        not isinstance(value, tuple)
        or (not value and not allow_empty)
        or len(value) > 128
        or any(not isinstance(item, str) or not item or len(item) > 256 for item in value)
        or len(value) != len(set(value))
        or value != tuple(sorted(value))
    ):
        raise ConnectorContractError(f"whatsapp_{label}_invalid")
    return value


class WhatsAppExportConnector:
    connector_id = CONNECTOR_ID

    def __init__(
        self,
        *,
        export: Path,
        source_id: str,
        conversation_id: str,
        owner_names: tuple[str, ...],
        date_order: str,
        timezone_name: str,
        page_size: int = 500,
    ):
        if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
            raise ConnectorContractError("whatsapp_source_id_invalid")
        if (
            not isinstance(conversation_id, str)
            or not IDENTITY.fullmatch(conversation_id)
        ):
            raise ConnectorContractError("whatsapp_conversation_id_invalid")
        if date_order not in {"dmy", "mdy"}:
            raise ConnectorContractError("whatsapp_date_order_invalid")
        if (
            not isinstance(timezone_name, str)
            or not timezone_name
            or len(timezone_name) > 128
        ):
            raise ConnectorContractError("whatsapp_timezone_invalid")
        try:
            timezone = ZoneInfo(timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            raise ConnectorContractError("whatsapp_timezone_invalid") from None
        if type(page_size) is not int or not 1 <= page_size <= 500:
            raise ConnectorContractError("whatsapp_page_size_invalid")
        self.export = Path(export)
        read_stable_file(self.export, maximum_bytes=MAX_EXPORT_BYTES)
        self.source_id = source_id
        self.conversation_id = conversation_id
        self.owner_names = _canonical_tuple(owner_names, "owner_names")
        self.date_order = date_order
        self.timezone = timezone
        self.page_size = page_size

    def _timestamp(self, groups: tuple[str, ...], *, twelve_hour: bool) -> str:
        first, second, year, hour, minute, second_value, *tail = groups
        month, day = (
            (int(first), int(second))
            if self.date_order == "mdy"
            else (int(second), int(first))
        )
        year_value = int(year)
        if year_value < 100:
            year_value += 2000
        hour_value = int(hour)
        if twelve_hour:
            marker = tail[0]
            if not 1 <= hour_value <= 12:
                raise ConnectorContractError("whatsapp_export_format_invalid")
            hour_value %= 12
            if marker == "PM":
                hour_value += 12
        try:
            value = datetime(
                year_value,
                month,
                day,
                hour_value,
                int(minute),
                int(second_value or 0),
                tzinfo=self.timezone,
            )
        except ValueError:
            raise ConnectorContractError("whatsapp_export_format_invalid") from None
        return value.isoformat()

    @staticmethod
    def _author_and_text(value: str) -> tuple[str | None, str]:
        if ": " not in value:
            return None, value
        author, text = value.split(": ", 1)
        if not author or len(author) > 256:
            raise ConnectorContractError("whatsapp_export_format_invalid")
        return author, text

    def _records(self) -> tuple[list[ConnectorRecordV2], str]:
        raw = read_stable_file(self.export, maximum_bytes=MAX_EXPORT_BYTES)
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ConnectorContractError("whatsapp_export_encoding_invalid") from None
        parsed: list[list[str | None]] = []
        for line in text.splitlines():
            if len(line) > MAX_LINE_CHARS:
                raise ConnectorContractError("whatsapp_export_line_too_large")
            ios = IOS.fullmatch(line)
            android = ANDROID.fullmatch(line)
            if ios:
                timestamp = self._timestamp(ios.groups()[:7], twelve_hour=True)
                author, body = self._author_and_text(ios.group(8))
                parsed.append([timestamp, author, body])
            elif android:
                timestamp = self._timestamp(android.groups()[:6], twelve_hour=False)
                author, body = self._author_and_text(android.group(7))
                parsed.append([timestamp, author, body])
            elif parsed:
                parsed[-1][2] = f"{parsed[-1][2]}\n{line}"
            elif line:
                raise ConnectorContractError("whatsapp_export_format_invalid")
            if len(parsed) > MAX_MESSAGES:
                raise ConnectorContractError("whatsapp_export_too_many_messages")
        if not parsed:
            raise ConnectorContractError("whatsapp_export_format_invalid")
        ordinals: defaultdict[tuple[str, str], int] = defaultdict(int)
        records = []
        for timestamp, author, body in parsed:
            if not isinstance(timestamp, str) or not isinstance(body, str) or not body:
                raise ConnectorContractError("whatsapp_export_format_invalid")
            author_key = author or "system"
            identity = (timestamp, author_key)
            ordinal = ordinals[identity]
            ordinals[identity] += 1
            digest = hashlib.sha256(
                f"{self.conversation_id}\0{timestamp}\0{author_key}\0{ordinal}".encode()
            ).hexdigest()
            native_id = f"wa:{digest}"
            direction = (
                "system"
                if author is None
                else "outbound"
                if author in self.owner_names
                else "inbound"
            )
            content = {
                "kind": "communication_message.v1",
                "content_fidelity": "complete",
                "conversation_id": self.conversation_id,
                "direction": direction,
                "format": "whatsapp-text-export",
                "message_id": native_id,
                "sent_at": timestamp,
                "surface": "whatsapp",
                "text": body,
            }
            if author is not None:
                content["author_id"] = author
                content["participant_ids"] = [author]
            records.append(ConnectorRecordV2(
                schema_version=2,
                native_id=native_id,
                native_parent_id=self.conversation_id,
                occurred_at=timestamp,
                content=content,
                provenance={"uri": f"connector://whatsapp-export/{native_id}"},
            ))
        digest = hashlib.sha256()
        for record in records:
            digest.update(json.dumps(
                record.to_mapping(),
                sort_keys=True,
                separators=(",", ":"),
            ).encode())
        return records, digest.hexdigest()

    @staticmethod
    def _cursor(value: str | None) -> tuple[int, int, str]:
        if value is None:
            return 0, 0, "0" * 64
        if not isinstance(value, str):
            raise ConnectorContractError("whatsapp_cursor_invalid")
        match = CURSOR.fullmatch(value)
        if match is None:
            raise ConnectorContractError("whatsapp_cursor_invalid")
        cycle = int(match.group(1))
        if cycle > MAX_CYCLE:
            raise ConnectorContractError("whatsapp_cursor_invalid")
        return cycle, int(match.group(2)), match.group(3)

    def pull(self, cursor: str | None) -> ConnectorPage:
        cycle, offset, previous_digest = self._cursor(cursor)
        records, digest = self._records()
        if offset > len(records) or (offset and previous_digest != digest):
            offset = 0
        end = min(len(records), offset + self.page_size)
        has_more = end < len(records)
        next_cursor = (
            f"whatsapp-v1:{cycle}:{end}:{digest}"
            if has_more
            else f"whatsapp-v1:{0 if cycle == MAX_CYCLE else cycle + 1}:0:{digest}"
        )
        return ConnectorPage(
            records=tuple(records[offset:end]),
            next_cursor=next_cursor,
            has_more=has_more,
        )


__all__ = ["WhatsAppExportConnector"]
