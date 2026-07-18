"""Read-only iMessage projection from one explicitly selected Messages database."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from connectors.local_sqlite import ReadOnlySQLiteSnapshot, table_columns
from connectors.sdk import (
    SOURCE_ID,
    ConnectorContractError,
    ConnectorPage,
    ConnectorRecordV2,
)


CONNECTOR_ID = "apple.imessage"
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
CURSOR = re.compile(r"imessage-v1:(\d{1,10}):(\d{1,20})\Z")
REQUIRED_COLUMNS = {
    "message": {
        "ROWID",
        "date",
        "guid",
        "handle_id",
        "is_from_me",
        "service",
        "text",
    },
    "handle": {"ROWID", "id", "service"},
    "chat": {"ROWID", "chat_identifier", "guid", "service_name"},
    "chat_message_join": {"chat_id", "message_id"},
}
MAX_CYCLE = 2_147_483_647


class IMessageSchemaError(ConnectorContractError):
    """A content-free unsupported Messages schema condition."""


def _digest(label: str, value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise IMessageSchemaError("unsupported_imessage_schema")
    return f"{label}:{hashlib.sha256(value.encode()).hexdigest()}"


def _apple_time(value: Any) -> str:
    if type(value) is not int:
        raise IMessageSchemaError("unsupported_imessage_schema")
    seconds = value / 1_000_000_000 if abs(value) > 10_000_000_000 else value
    try:
        timestamp = APPLE_EPOCH + timedelta(seconds=seconds)
    except OverflowError:
        raise IMessageSchemaError("unsupported_imessage_schema") from None
    return timestamp.isoformat().replace("+00:00", "Z")


def _optional_apple_time(value: Any) -> str | None:
    if value in (None, 0):
        return None
    return _apple_time(value)


def _next_cycle(value: int) -> int:
    return 0 if value == MAX_CYCLE else value + 1


class IMessageConnector:
    connector_id = CONNECTOR_ID

    def __init__(
        self,
        *,
        database: Path,
        source_id: str,
        page_size: int = 100,
        chat_ids: tuple[str, ...] = (),
        date_min: str | None = None,
        date_max: str | None = None,
    ):
        if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
            raise ConnectorContractError("imessage_source_id_invalid")
        if type(page_size) is not int or not 1 <= page_size <= 500:
            raise ConnectorContractError("imessage_page_size_invalid")
        if (
            not isinstance(chat_ids, tuple)
            or len(chat_ids) > 128
            or len(chat_ids) != len(set(chat_ids))
            or chat_ids != tuple(sorted(chat_ids))
            or any(not isinstance(item, str) or not item for item in chat_ids)
        ):
            raise ConnectorContractError("imessage_chat_ids_invalid")
        self.database = Path(database)
        self.source_id = source_id
        self.page_size = page_size
        self.chat_ids = chat_ids
        self.date_min = self._selector_date(date_min)
        self.date_max = self._selector_date(date_max)
        if (
            self.date_min is not None
            and self.date_max is not None
            and self.date_min > self.date_max
        ):
            raise ConnectorContractError("imessage_date_range_invalid")
        self._columns = self._probe()

    @staticmethod
    def _selector_date(value: str | None) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, str) or len(value) > 64:
            raise ConnectorContractError("imessage_date_selector_invalid")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ConnectorContractError("imessage_date_selector_invalid") from None
        if parsed.tzinfo is None:
            raise ConnectorContractError("imessage_date_selector_invalid")
        return parsed.astimezone(timezone.utc)

    def _probe(self) -> dict[str, frozenset[str]]:
        try:
            with ReadOnlySQLiteSnapshot(self.database) as connection:
                columns = {
                    table: table_columns(connection, table)
                    for table in REQUIRED_COLUMNS
                }
        except ConnectorContractError:
            raise
        if any(
            not required.issubset(columns[table])
            for table, required in REQUIRED_COLUMNS.items()
        ):
            raise IMessageSchemaError("unsupported_imessage_schema")
        return columns

    @staticmethod
    def _cursor(value: str | None) -> tuple[int, int]:
        if value is None:
            return 0, 0
        if not isinstance(value, str):
            raise ConnectorContractError("imessage_cursor_invalid")
        match = CURSOR.fullmatch(value)
        if match is None:
            raise ConnectorContractError("imessage_cursor_invalid")
        cycle, rowid = int(match.group(1)), int(match.group(2))
        if cycle > MAX_CYCLE:
            raise ConnectorContractError("imessage_cursor_invalid")
        return cycle, rowid

    def _record(self, row: sqlite3.Row) -> ConnectorRecordV2 | None:
        occurred_at = _apple_time(row["date"])
        occurred = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        if self.date_min is not None and occurred < self.date_min:
            return None
        if self.date_max is not None and occurred > self.date_max:
            return None
        chat_identifier = row["chat_identifier"] or row["chat_guid"]
        if not isinstance(chat_identifier, str) or not chat_identifier:
            chat_identifier = row["message_guid"]
        if self.chat_ids and chat_identifier not in self.chat_ids:
            return None
        native_id = _digest("imsg", row["message_guid"])
        conversation_id = _digest("imsg-chat", chat_identifier)
        provenance = {"uri": f"connector://apple-imessage/{native_id}"}
        if row["is_deleted"] not in (0, 1):
            raise IMessageSchemaError("unsupported_imessage_schema")
        if row["is_deleted"] == 1:
            return ConnectorRecordV2(
                schema_version=2,
                native_id=native_id,
                native_parent_id=conversation_id,
                occurred_at=occurred_at,
                content={"kind": "communication_message.v1"},
                provenance=provenance,
                deleted=True,
            )
        text = row["text"]
        if not isinstance(text, str) or not text:
            return None
        direction = "outbound" if row["is_from_me"] == 1 else "inbound"
        if row["is_from_me"] not in (0, 1):
            raise IMessageSchemaError("unsupported_imessage_schema")
        content: dict[str, Any] = {
            "kind": "communication_message.v1",
            "conversation_id": conversation_id,
            "direction": direction,
            "message_id": native_id,
            "sent_at": occurred_at,
            "surface": "imessage",
            "text": text,
        }
        author = row["handle_id_value"]
        if direction == "outbound":
            content["role"] = "owner"
        elif isinstance(author, str) and author:
            content["author_id"] = author
            content["participant_ids"] = [author]
        edited_at = _optional_apple_time(row["date_edited"])
        if edited_at is not None:
            content["edited_at"] = edited_at
        return ConnectorRecordV2(
            schema_version=2,
            native_id=native_id,
            native_parent_id=conversation_id,
            occurred_at=occurred_at,
            content=content,
            provenance=provenance,
        )

    def pull(self, cursor: str | None) -> ConnectorPage:
        cycle, after_rowid = self._cursor(cursor)
        edited = (
            "m.date_edited"
            if "date_edited" in self._columns["message"]
            else "0"
        )
        deleted = (
            "m.is_deleted"
            if "is_deleted" in self._columns["message"]
            else "0"
        )
        query = f"""
            SELECT
              m.ROWID AS message_rowid,
              m.guid AS message_guid,
              m.text,
              m.service,
              m.date,
              m.is_from_me,
              {edited} AS date_edited,
              {deleted} AS is_deleted,
              h.id AS handle_id_value,
              c.guid AS chat_guid,
              c.chat_identifier
            FROM message AS m
            LEFT JOIN handle AS h ON h.ROWID=m.handle_id
            LEFT JOIN chat AS c ON c.ROWID=(
              SELECT min(j.chat_id)
              FROM chat_message_join AS j
              WHERE j.message_id=m.ROWID
            )
            WHERE m.ROWID>?
            ORDER BY m.ROWID
            LIMIT ?
        """
        try:
            with ReadOnlySQLiteSnapshot(self.database) as connection:
                rows = connection.execute(
                    query,
                    (after_rowid, self.page_size + 1),
                ).fetchall()
        except sqlite3.Error:
            raise IMessageSchemaError("unsupported_imessage_schema") from None
        page_rows = rows[: self.page_size]
        records = tuple(
            record
            for row in page_rows
            if (record := self._record(row)) is not None
        )
        has_more = len(rows) > self.page_size
        last_rowid = int(page_rows[-1]["message_rowid"]) if page_rows else after_rowid
        next_cursor = (
            f"imessage-v1:{cycle}:{last_rowid}"
            if has_more
            else f"imessage-v1:{_next_cycle(cycle)}:0"
        )
        return ConnectorPage(
            records=records,
            next_cursor=next_cursor,
            has_more=has_more,
        )


__all__ = ["IMessageConnector", "IMessageSchemaError"]
