"""Bounded read-only snapshots for browsers, Apple Notes, and Hermes sessions."""

from __future__ import annotations

import hashlib
import json
import math
import plistlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from connectors.local_file import read_stable_file
from connectors.local_sqlite import ReadOnlySQLiteSnapshot
from connectors.sdk import (
    SOURCE_ID,
    ConnectorContractError,
    ConnectorPage,
    ConnectorRecordV2,
)


CURSOR = re.compile(r"local-snapshot-v1:(\d{1,10}):(\d{1,10}):([0-9a-f]{64})\Z")
IDENTITY = re.compile(r"[A-Za-z0-9_.:@-]{1,256}\Z")
MAX_CYCLE = 2_147_483_647
MAX_RECORDS = 500_000
MAX_BOOKMARK_BYTES = 64 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000
HERMES_SCHEMA_VERSION = 22
APPLE_EPOCH_SECONDS = 978_307_200
CHROME_EPOCH_SECONDS = 11_644_473_600


class LocalActivitySchemaError(ConnectorContractError):
    """A content-free unsupported or unavailable local source condition."""


def _hash(label: str, *values: Any) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(str(value).encode())
        digest.update(b"\0")
    return f"{label}:{digest.hexdigest()}"


def _iso_unix(value: Any, *, error_code: str) -> str:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
    ):
        raise LocalActivitySchemaError(error_code)
    try:
        return datetime.fromtimestamp(
            float(value), tz=timezone.utc
        ).isoformat().replace("+00:00", "Z")
    except (OSError, OverflowError, ValueError):
        raise LocalActivitySchemaError(error_code) from None


def _date_selector(value: str | None, *, error_code: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise ConnectorContractError(error_code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ConnectorContractError(error_code) from None
    if parsed.tzinfo is None:
        raise ConnectorContractError(error_code)
    return parsed.astimezone(timezone.utc)


def _table_columns(
    connection: sqlite3.Connection,
    table: str,
    *,
    allowed: frozenset[str],
    error_code: str,
) -> frozenset[str]:
    if table not in allowed:
        raise LocalActivitySchemaError(error_code)
    try:
        return frozenset(
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})")
        )
    except sqlite3.Error:
        raise LocalActivitySchemaError(error_code) from None


class _SnapshotConnector:
    page_size: int
    date_min: datetime | None
    date_max: datetime | None

    def _records(self) -> list[ConnectorRecordV2]:
        raise NotImplementedError

    @staticmethod
    def _cursor(value: str | None) -> tuple[int, int, str]:
        if value is None:
            return 0, 0, "0" * 64
        if not isinstance(value, str):
            raise ConnectorContractError("local_snapshot_cursor_invalid")
        match = CURSOR.fullmatch(value)
        if match is None:
            raise ConnectorContractError("local_snapshot_cursor_invalid")
        cycle = int(match.group(1))
        if cycle > MAX_CYCLE:
            raise ConnectorContractError("local_snapshot_cursor_invalid")
        return cycle, int(match.group(2)), match.group(3)

    def _selected(self, occurred_at: str) -> bool:
        occurred = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        return (
            (self.date_min is None or occurred >= self.date_min)
            and (self.date_max is None or occurred <= self.date_max)
        )

    def pull(self, cursor: str | None) -> ConnectorPage:
        cycle, offset, prior_digest = self._cursor(cursor)
        records = sorted(
            self._records(),
            key=lambda record: (record.occurred_at or "", record.native_id),
        )
        if len(records) > MAX_RECORDS:
            raise LocalActivitySchemaError("local_snapshot_too_many_records")
        digest = hashlib.sha256()
        for record in records:
            digest.update(json.dumps(
                record.to_mapping(),
                sort_keys=True,
                separators=(",", ":"),
            ).encode())
        current_digest = digest.hexdigest()
        if offset > len(records) or (offset and prior_digest != current_digest):
            offset = 0
        end = min(len(records), offset + self.page_size)
        has_more = end < len(records)
        next_cursor = (
            f"local-snapshot-v1:{cycle}:{end}:{current_digest}"
            if has_more
            else f"local-snapshot-v1:{0 if cycle == MAX_CYCLE else cycle + 1}:0:{current_digest}"
        )
        return ConnectorPage(
            records=tuple(records[offset:end]),
            next_cursor=next_cursor,
            has_more=has_more,
        )


class BrowserActivityConnector(_SnapshotConnector):
    """Read one explicit Safari or Chrome history/profile selection."""

    connector_id = "local.browser"
    _SCHEMAS = {
        "safari": {
            "history_items": {"id", "url"},
            "history_visits": {"id", "history_item", "visit_time", "title"},
        },
        "chrome": {
            "urls": {"id", "url", "title"},
            "visits": {"id", "url", "visit_time"},
        },
    }

    def __init__(
        self,
        *,
        browser: str,
        history: Path | None,
        bookmarks: Path | None,
        source_id: str,
        date_min: str | None = None,
        date_max: str | None = None,
        page_size: int = 500,
    ):
        if browser not in self._SCHEMAS:
            raise ConnectorContractError("browser_kind_invalid")
        if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
            raise ConnectorContractError("browser_source_id_invalid")
        if history is None and bookmarks is None:
            raise ConnectorContractError("browser_selection_empty")
        if type(page_size) is not int or not 1 <= page_size <= 500:
            raise ConnectorContractError("browser_page_size_invalid")
        self.browser = browser
        self.connector_id = (
            "apple.safari" if browser == "safari" else "google.chrome"
        )
        self.history = Path(history) if history is not None else None
        self.bookmarks = Path(bookmarks) if bookmarks is not None else None
        self.source_id = source_id
        self.page_size = page_size
        self.date_min = _date_selector(
            date_min, error_code="browser_date_selector_invalid"
        )
        self.date_max = _date_selector(
            date_max, error_code="browser_date_selector_invalid"
        )
        if (
            self.date_min is not None
            and self.date_max is not None
            and self.date_min > self.date_max
        ):
            raise ConnectorContractError("browser_date_range_invalid")
        if self.history is not None:
            self._probe_history()
        if self.bookmarks is not None:
            self._read_bookmarks()

    def _probe_history(self) -> None:
        assert self.history is not None
        required = self._SCHEMAS[self.browser]
        try:
            with ReadOnlySQLiteSnapshot(self.history) as connection:
                columns = {
                    table: _table_columns(
                        connection,
                        table,
                        allowed=frozenset(required),
                        error_code="unsupported_browser_schema",
                    )
                    for table in required
                }
        except LocalActivitySchemaError:
            raise
        except ConnectorContractError:
            raise LocalActivitySchemaError("browser_unavailable") from None
        if any(
            not expected.issubset(columns[table])
            for table, expected in required.items()
        ):
            raise LocalActivitySchemaError("unsupported_browser_schema")

    @staticmethod
    def _url(value: Any) -> str | None:
        if not isinstance(value, str) or not value or len(value) > 8192:
            return None
        try:
            parsed = urlsplit(value)
            if (
                parsed.scheme.casefold() not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
            ):
                return None
            return urlunsplit((
                parsed.scheme.casefold(),
                parsed.netloc,
                parsed.path,
                parsed.query,
                "",
            ))
        except ValueError:
            return None

    def _document(
        self,
        *,
        native_key: Any,
        url: Any,
        title: Any,
        occurred_at: str,
        surface: str,
    ) -> ConnectorRecordV2 | None:
        safe_url = self._url(url)
        if safe_url is None or not self._selected(occurred_at):
            return None
        safe_title = (
            title.strip()[:10_000]
            if isinstance(title, str) and title.strip()
            else urlsplit(safe_url).hostname or "Browser activity"
        )
        native_id = _hash(
            f"{self.browser}-activity",
            surface,
            native_key,
        )
        return ConnectorRecordV2(
            schema_version=2,
            native_id=native_id,
            occurred_at=occurred_at,
            content={
                "kind": "document.v1",
                "document_id": native_id,
                "mime_type": "text/uri-list",
                "modified_at": occurred_at,
                "name": safe_title,
                "source_url": safe_url,
                "surface": f"{self.browser}-{surface}",
                "text": f"{safe_title}\n{safe_url}",
            },
            provenance={
                "uri": f"connector://{self.browser}-activity/{native_id}"
            },
        )

    def _history_records(self) -> list[ConnectorRecordV2]:
        if self.history is None:
            return []
        query = (
            "SELECT v.id AS visit_id,i.url,v.visit_time,v.title "
            "FROM history_visits v JOIN history_items i "
            "ON i.id=v.history_item ORDER BY v.id LIMIT ?"
            if self.browser == "safari"
            else
            "SELECT v.id AS visit_id,u.url,v.visit_time,u.title "
            "FROM visits v JOIN urls u ON u.id=v.url ORDER BY v.id LIMIT ?"
        )
        try:
            with ReadOnlySQLiteSnapshot(self.history) as connection:
                rows = connection.execute(
                    query, (MAX_RECORDS + 1,)
                ).fetchall()
        except (ConnectorContractError, sqlite3.Error):
            raise LocalActivitySchemaError("browser_unavailable") from None
        if len(rows) > MAX_RECORDS:
            raise LocalActivitySchemaError("local_snapshot_too_many_records")
        records = []
        for row in rows:
            try:
                timestamp = (
                    float(row["visit_time"]) + APPLE_EPOCH_SECONDS
                    if self.browser == "safari"
                    else float(row["visit_time"]) / 1_000_000
                    - CHROME_EPOCH_SECONDS
                )
            except (TypeError, ValueError):
                raise LocalActivitySchemaError(
                    "unsupported_browser_schema"
                ) from None
            occurred_at = _iso_unix(
                timestamp, error_code="unsupported_browser_schema"
            )
            record = self._document(
                native_key=row["visit_id"],
                url=row["url"],
                title=row["title"],
                occurred_at=occurred_at,
                surface="history",
            )
            if record is not None:
                records.append(record)
        return records

    def _bookmark_time(self, value: Any) -> str:
        if isinstance(value, datetime):
            timestamp = value.astimezone(timezone.utc)
            return timestamp.isoformat().replace("+00:00", "Z")
        if self.browser == "chrome" and isinstance(value, str) and value.isdigit():
            return _iso_unix(
                int(value) / 1_000_000 - CHROME_EPOCH_SECONDS,
                error_code="unsupported_browser_bookmarks",
            )
        return "1970-01-01T00:00:00Z"

    def _safari_bookmarks(
        self,
        value: Any,
        *,
        path: tuple[int, ...] = (),
        budget: list[int] | None = None,
    ) -> list[ConnectorRecordV2]:
        budget = [0] if budget is None else budget
        budget[0] += 1
        if budget[0] > MAX_RECORDS:
            raise LocalActivitySchemaError("local_snapshot_too_many_records")
        if not isinstance(value, dict) or len(path) > 32:
            raise LocalActivitySchemaError("unsupported_browser_bookmarks")
        records = []
        url = value.get("URLString")
        if url is not None:
            title_value = value.get("URIDictionary", {})
            title = (
                title_value.get("title")
                if isinstance(title_value, dict)
                else None
            )
            occurred_at = self._bookmark_time(
                value.get("ReadingList", {}).get("DateAdded")
                if isinstance(value.get("ReadingList"), dict)
                else None
            )
            record = self._document(
                native_key=value.get("WebBookmarkUUID") or url,
                url=url,
                title=title,
                occurred_at=occurred_at,
                surface="bookmark",
            )
            if record is not None:
                records.append(record)
        children = value.get("Children", [])
        if not isinstance(children, list):
            raise LocalActivitySchemaError("unsupported_browser_bookmarks")
        for index, child in enumerate(children):
            records.extend(
                self._safari_bookmarks(
                    child, path=(*path, index), budget=budget
                )
            )
        return records

    def _chrome_bookmarks(
        self,
        value: Any,
        *,
        path: tuple[str, ...] = (),
        budget: list[int] | None = None,
    ) -> list[ConnectorRecordV2]:
        budget = [0] if budget is None else budget
        budget[0] += 1
        if budget[0] > MAX_RECORDS:
            raise LocalActivitySchemaError("local_snapshot_too_many_records")
        if not isinstance(value, dict) or len(path) > 32:
            raise LocalActivitySchemaError("unsupported_browser_bookmarks")
        records = []
        if value.get("type") == "url":
            native_key = value.get("id") or "/".join(path)
            record = self._document(
                native_key=native_key,
                url=value.get("url"),
                title=value.get("name"),
                occurred_at=self._bookmark_time(value.get("date_added")),
                surface="bookmark",
            )
            if record is not None:
                records.append(record)
        children = value.get("children", [])
        if not isinstance(children, list):
            raise LocalActivitySchemaError("unsupported_browser_bookmarks")
        for index, child in enumerate(children):
            records.extend(
                self._chrome_bookmarks(
                    child, path=(*path, str(index)), budget=budget
                )
            )
        return records

    def _read_bookmarks(self) -> list[ConnectorRecordV2]:
        if self.bookmarks is None:
            return []
        try:
            raw = read_stable_file(
                self.bookmarks, maximum_bytes=MAX_BOOKMARK_BYTES
            )
            if self.browser == "safari":
                value = plistlib.loads(raw)
                records = self._safari_bookmarks(value)
            else:
                value = json.loads(raw)
                roots = value.get("roots") if isinstance(value, dict) else None
                if not isinstance(roots, dict):
                    raise LocalActivitySchemaError(
                        "unsupported_browser_bookmarks"
                    )
                records = []
                budget = [0]
                for name, root in sorted(roots.items()):
                    records.extend(
                        self._chrome_bookmarks(
                            root, path=(name,), budget=budget
                        )
                    )
        except LocalActivitySchemaError:
            raise
        except (
            ConnectorContractError,
            json.JSONDecodeError,
            plistlib.InvalidFileException,
            TypeError,
            ValueError,
        ):
            raise LocalActivitySchemaError(
                "unsupported_browser_bookmarks"
            ) from None
        if len(records) > MAX_RECORDS:
            raise LocalActivitySchemaError("local_snapshot_too_many_records")
        return records

    def _records(self) -> list[ConnectorRecordV2]:
        return self._history_records() + self._read_bookmarks()


class AppleNotesConnector(_SnapshotConnector):
    """Read title and snippet only from one explicitly selected Notes store."""

    connector_id = "apple.notes"
    _TABLES = frozenset({"ZICNOTEDATA", "ZICCLOUDSYNCINGOBJECT"})
    _REQUIRED = {
        "ZICNOTEDATA": {"Z_PK"},
        "ZICCLOUDSYNCINGOBJECT": {
            "Z_PK",
            "ZIDENTIFIER",
            "ZTITLE1",
            "ZSNIPPET",
            "ZCREATIONDATE1",
            "ZMODIFICATIONDATE1",
            "ZISPASSWORDPROTECTED",
            "ZNOTEDATA",
        },
    }

    def __init__(
        self,
        *,
        database: Path,
        source_id: str,
        date_min: str | None = None,
        date_max: str | None = None,
        page_size: int = 500,
    ):
        if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
            raise ConnectorContractError("notes_source_id_invalid")
        if type(page_size) is not int or not 1 <= page_size <= 500:
            raise ConnectorContractError("notes_page_size_invalid")
        self.database = Path(database)
        self.source_id = source_id
        self.page_size = page_size
        self.date_min = _date_selector(
            date_min, error_code="notes_date_selector_invalid"
        )
        self.date_max = _date_selector(
            date_max, error_code="notes_date_selector_invalid"
        )
        if (
            self.date_min is not None
            and self.date_max is not None
            and self.date_min > self.date_max
        ):
            raise ConnectorContractError("notes_date_range_invalid")
        try:
            with ReadOnlySQLiteSnapshot(self.database) as connection:
                columns = {
                    table: _table_columns(
                        connection,
                        table,
                        allowed=self._TABLES,
                        error_code="unsupported_notes_schema",
                    )
                    for table in self._REQUIRED
                }
        except LocalActivitySchemaError:
            raise
        except ConnectorContractError:
            raise LocalActivitySchemaError("notes_unavailable") from None
        if any(
            not required.issubset(columns[table])
            for table, required in self._REQUIRED.items()
        ):
            raise LocalActivitySchemaError("unsupported_notes_schema")

    def _records(self) -> list[ConnectorRecordV2]:
        query = """
            SELECT n.Z_PK,n.ZIDENTIFIER,n.ZTITLE1,n.ZSNIPPET,
                   n.ZCREATIONDATE1,n.ZMODIFICATIONDATE1,
                   n.ZISPASSWORDPROTECTED
            FROM ZICCLOUDSYNCINGOBJECT n
            JOIN ZICNOTEDATA d ON d.Z_PK=n.ZNOTEDATA
            ORDER BY n.Z_PK
            LIMIT ?
        """
        try:
            with ReadOnlySQLiteSnapshot(self.database) as connection:
                rows = connection.execute(
                    query, (MAX_RECORDS + 1,)
                ).fetchall()
        except (ConnectorContractError, sqlite3.Error):
            raise LocalActivitySchemaError("notes_unavailable") from None
        if len(rows) > MAX_RECORDS:
            raise LocalActivitySchemaError("local_snapshot_too_many_records")
        records = []
        for row in rows:
            if row["ZISPASSWORDPROTECTED"] not in (0, 1):
                raise LocalActivitySchemaError("unsupported_notes_schema")
            if row["ZISPASSWORDPROTECTED"] == 1:
                continue
            identifier = row["ZIDENTIFIER"]
            if not isinstance(identifier, str) or not identifier:
                raise LocalActivitySchemaError("unsupported_notes_schema")
            try:
                modified_value = (
                    float(row["ZMODIFICATIONDATE1"])
                    + APPLE_EPOCH_SECONDS
                )
            except (TypeError, ValueError):
                raise LocalActivitySchemaError(
                    "unsupported_notes_schema"
                ) from None
            modified = _iso_unix(
                modified_value, error_code="unsupported_notes_schema"
            )
            if not self._selected(modified):
                continue
            title = (
                row["ZTITLE1"].strip()[:10_000]
                if isinstance(row["ZTITLE1"], str)
                and row["ZTITLE1"].strip()
                else "Untitled note"
            )
            snippet = row["ZSNIPPET"]
            if snippet is not None and not isinstance(snippet, str):
                raise LocalActivitySchemaError("unsupported_notes_schema")
            native_id = _hash("apple-note", identifier)
            content = {
                "kind": "document.v1",
                "document_id": native_id,
                "mime_type": "text/plain",
                "modified_at": modified,
                "name": title,
                "surface": "apple-notes-snippet",
            }
            if snippet:
                content["text"] = snippet[:MAX_TEXT_CHARS]
            records.append(ConnectorRecordV2(
                schema_version=2,
                native_id=native_id,
                occurred_at=modified,
                content=content,
                provenance={
                    "uri": f"connector://apple-notes/{native_id}"
                },
            ))
        return records


class HermesSessionConnector(_SnapshotConnector):
    """Read user/assistant turns from one pinned Hermes state database."""

    connector_id = "hermes.sessions"
    _TABLES = frozenset({"schema_version", "sessions", "messages"})
    _REQUIRED = {
        "schema_version": {"version"},
        "sessions": {"id", "source", "title", "started_at", "ended_at"},
        "messages": {
            "id",
            "session_id",
            "role",
            "content",
            "timestamp",
            "active",
            "compacted",
        },
    }

    def __init__(
        self,
        *,
        database: Path,
        source_id: str,
        sources: tuple[str, ...] = (),
        roles: tuple[str, ...] = ("assistant", "user"),
        date_min: str | None = None,
        date_max: str | None = None,
        page_size: int = 500,
    ):
        if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
            raise ConnectorContractError("hermes_source_id_invalid")
        for value, label in ((sources, "sources"), (roles, "roles")):
            if (
                not isinstance(value, tuple)
                or (label == "sources" and not value)
                or len(value) > 64
                or len(value) != len(set(value))
                or value != tuple(sorted(value))
                or any(
                    not isinstance(item, str)
                    or not IDENTITY.fullmatch(item)
                    for item in value
                )
            ):
                raise ConnectorContractError(f"hermes_{label}_invalid")
        if not roles or set(roles) - {"assistant", "user"}:
            raise ConnectorContractError("hermes_roles_invalid")
        if type(page_size) is not int or not 1 <= page_size <= 500:
            raise ConnectorContractError("hermes_page_size_invalid")
        self.database = Path(database)
        self.source_id = source_id
        self.sources = sources
        self.roles = roles
        self.page_size = page_size
        self.date_min = _date_selector(
            date_min, error_code="hermes_date_selector_invalid"
        )
        self.date_max = _date_selector(
            date_max, error_code="hermes_date_selector_invalid"
        )
        if (
            self.date_min is not None
            and self.date_max is not None
            and self.date_min > self.date_max
        ):
            raise ConnectorContractError("hermes_date_range_invalid")
        try:
            with ReadOnlySQLiteSnapshot(self.database) as connection:
                columns = {
                    table: _table_columns(
                        connection,
                        table,
                        allowed=self._TABLES,
                        error_code="unsupported_hermes_schema",
                    )
                    for table in self._REQUIRED
                }
                versions = connection.execute(
                    "SELECT version FROM schema_version"
                ).fetchall()
        except LocalActivitySchemaError:
            raise
        except (ConnectorContractError, sqlite3.Error):
            raise LocalActivitySchemaError("hermes_unavailable") from None
        if (
            any(
                not required.issubset(columns[table])
                for table, required in self._REQUIRED.items()
            )
            or len(versions) != 1
            or versions[0]["version"] != HERMES_SCHEMA_VERSION
        ):
            raise LocalActivitySchemaError("unsupported_hermes_schema")

    def _records(self) -> list[ConnectorRecordV2]:
        source_clause = ""
        values: list[Any] = list(self.roles)
        if self.sources:
            source_clause = (
                " AND s.source IN ("
                + ",".join("?" for _ in self.sources)
                + ")"
            )
            values.extend(self.sources)
        query = (
            "SELECT m.id,m.session_id,m.role,m.content,m.timestamp,"
            "m.active,m.compacted,s.source,s.title "
            "FROM messages m JOIN sessions s ON s.id=m.session_id "
            "WHERE m.role IN ("
            + ",".join("?" for _ in self.roles)
            + ")"
            + source_clause
            + " ORDER BY m.id LIMIT ?"
        )
        values.append(MAX_RECORDS + 1)
        try:
            with ReadOnlySQLiteSnapshot(self.database) as connection:
                rows = connection.execute(query, values).fetchall()
        except (ConnectorContractError, sqlite3.Error):
            raise LocalActivitySchemaError("hermes_unavailable") from None
        if len(rows) > MAX_RECORDS:
            raise LocalActivitySchemaError("local_snapshot_too_many_records")
        records = []
        for row in rows:
            if row["active"] not in (0, 1) or row["compacted"] not in (0, 1):
                raise LocalActivitySchemaError("unsupported_hermes_schema")
            occurred_at = _iso_unix(
                row["timestamp"],
                error_code="unsupported_hermes_schema",
            )
            if not self._selected(occurred_at):
                continue
            conversation_id = _hash("hermes-session", row["session_id"])
            native_id = _hash(
                "hermes-message", row["session_id"], row["id"]
            )
            provenance = {
                "uri": f"connector://hermes-session/{native_id}"
            }
            if row["active"] == 0:
                records.append(ConnectorRecordV2(
                    schema_version=2,
                    native_id=native_id,
                    native_parent_id=conversation_id,
                    occurred_at=occurred_at,
                    content={"kind": "communication_message.v1"},
                    provenance=provenance,
                    deleted=True,
                ))
                continue
            text = row["content"]
            if not isinstance(text, str) or not text:
                continue
            if len(text) > MAX_TEXT_CHARS:
                text = text[:MAX_TEXT_CHARS]
            role = row["role"]
            source = row["source"]
            if (
                not isinstance(source, str)
                or not IDENTITY.fullmatch(source)
            ):
                raise LocalActivitySchemaError(
                    "unsupported_hermes_schema"
                )
            content = {
                "kind": "communication_message.v1",
                "conversation_id": conversation_id,
                "direction": "outbound" if role == "user" else "inbound",
                "format": "hermes-session-v22",
                "message_id": native_id,
                "role": role,
                "sent_at": occurred_at,
                "surface": f"hermes-{source}",
                "text": text,
            }
            title = row["title"]
            if isinstance(title, str) and title:
                content["subject"] = title[:10_000]
            records.append(ConnectorRecordV2(
                schema_version=2,
                native_id=native_id,
                native_parent_id=conversation_id,
                occurred_at=occurred_at,
                content=content,
                provenance=provenance,
            ))
        return records


__all__ = [
    "AppleNotesConnector",
    "BrowserActivityConnector",
    "HermesSessionConnector",
    "LocalActivitySchemaError",
]
