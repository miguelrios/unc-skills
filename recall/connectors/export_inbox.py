"""Explicit, local-only ChatGPT/Cowork export inbox connector.

The adapter reads only a directory named by the caller.  Its catalog contains
content-free identities and provenance, never message bodies or local names.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .sdk import ConnectorPage, ConnectorRecord, MAX_PAGE_RECORDS


SCHEMA_VERSION = 1
CONNECTOR_ID = "openai.export-inbox"
SUPPORTED_SUFFIXES = {".json": "json", ".jsonl": "jsonl", ".zip": "zip"}
FORBIDDEN_ROOT_NAMES = {"desktop", "documents", "downloads", "library"}
MAX_EXPORT_BYTES = 2_000_000_000
MAX_ARCHIVE_MEMBERS = 10_000


class ExportInboxError(ValueError):
    """A content-free export validation error."""


@dataclass(frozen=True)
class _ParsedExport:
    export_id: str
    fingerprint: str
    records: tuple[ConnectorRecord, ...]


def _digest(value: str | bytes) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def _native_id(conversation_id: str, message_id: str) -> str:
    return "msg_" + _digest(f"{conversation_id}\0{message_id}")[:40]


def _rfc3339(value: Any) -> str:
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            parsed = datetime.fromtimestamp(value, timezone.utc)
        elif isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError
            parsed = parsed.astimezone(timezone.utc)
        else:
            raise ValueError
    except (OverflowError, TypeError, ValueError) as error:
        raise ExportInboxError("export contains an invalid timestamp") from error
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _required_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 10_000:
        raise ExportInboxError(f"export contains an invalid {label}")
    return value


def _safe_message_content(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {"content_type": "text", "parts": [value], "attachment_count": 0}
    if not isinstance(value, dict):
        raise ExportInboxError("export contains invalid message content")
    content_type = value.get("content_type", "text")
    if not isinstance(content_type, str) or not content_type:
        raise ExportInboxError("export contains invalid message content")
    parts = value.get("parts", [])
    if not isinstance(parts, list):
        raise ExportInboxError("export contains invalid message content")
    text_parts: list[str] = []
    attachment_types: list[str] = []
    for part in parts:
        if isinstance(part, str):
            text_parts.append(part)
        elif isinstance(part, dict):
            attachment_type = part.get("content_type", "attachment")
            attachment_types.append(
                attachment_type if isinstance(attachment_type, str) and attachment_type else "attachment"
            )
        else:
            raise ExportInboxError("export contains invalid message content")
    result: dict[str, Any] = {
        "content_type": content_type,
        "parts": text_parts,
        "attachment_count": len(attachment_types),
    }
    if attachment_types:
        result["attachment_types"] = sorted(set(attachment_types))
    return result


class ExportInboxConnector:
    """Pull records from a caller-selected export inbox with ACK-gated cursors."""

    connector_id = CONNECTOR_ID

    def __init__(self, *, inbox: Path, catalog_path: Path, source_id: str,
                 page_size: int = MAX_PAGE_RECORDS, privacy_mode: str = "off"):
        self.inbox = Path(inbox)
        self.catalog_path = Path(catalog_path)
        self.source_id = source_id
        if privacy_mode not in {"off", "scrub", "drop"}:
            raise ExportInboxError("privacy mode is invalid")
        self.privacy_mode = privacy_mode
        if not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= MAX_PAGE_RECORDS:
            raise ExportInboxError("page size is out of range")
        self.page_size = page_size
        self._validate_root()
        self.catalog_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.catalog_path)
        self.db.row_factory = sqlite3.Row
        os.chmod(self.catalog_path, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS exports(
              export_id TEXT PRIMARY KEY,
              fingerprint TEXT NOT NULL UNIQUE,
              status TEXT NOT NULL CHECK(status IN ('active','removing','removed')),
              record_count INTEGER NOT NULL,
              removal_started INTEGER NOT NULL DEFAULT 0 CHECK(removal_started IN (0,1))
            );
            CREATE TABLE IF NOT EXISTS export_records(
              export_id TEXT NOT NULL REFERENCES exports(export_id),
              native_id TEXT NOT NULL,
              occurred_at TEXT NOT NULL,
              provenance_json TEXT NOT NULL,
              PRIMARY KEY(export_id,native_id)
            );
        """)
        if "removal_started" not in {
            row["name"] for row in self.db.execute("PRAGMA table_info(exports)")
        }:
            self.db.execute(
                "ALTER TABLE exports ADD COLUMN removal_started INTEGER NOT NULL DEFAULT 0"
            )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def _validate_root(self) -> None:
        if ".." in self.inbox.parts:
            raise ExportInboxError("explicit inbox traversal is not allowed")
        if self.inbox.name.casefold() in FORBIDDEN_ROOT_NAMES:
            raise ExportInboxError("explicit inbox must not be a broad personal directory")
        if self.inbox.is_symlink():
            raise ExportInboxError("explicit inbox root must not be a symlink")
        if not self.inbox.exists() or not self.inbox.is_dir():
            raise ExportInboxError("explicit inbox root is unavailable")

    def _files(self) -> list[tuple[Path, str, int]]:
        self._validate_root()
        found: list[tuple[Path, str, int]] = []
        try:
            children = list(self.inbox.iterdir())
        except OSError as error:
            raise ExportInboxError("explicit inbox root is unavailable") from error
        for path in children:
            try:
                mode = path.lstat().st_mode
            except OSError as error:
                raise ExportInboxError("inbox entry cannot be inspected") from error
            if stat.S_ISLNK(mode):
                raise ExportInboxError("inbox symlink entries are not allowed")
            if stat.S_ISDIR(mode):
                raise ExportInboxError("nested inbox directories are not allowed")
            if not stat.S_ISREG(mode):
                continue
            if path.lstat().st_nlink != 1:
                raise ExportInboxError("hard-linked inbox entries are not allowed")
            try:
                finder_info = os.getxattr(path, "com.apple.FinderInfo", follow_symlinks=False)
            except (AttributeError, OSError):
                finder_info = b""
            if len(finder_info) >= 10 and int.from_bytes(finder_info[8:10], "big") & 0x8000:
                raise ExportInboxError("Finder alias inbox entries are not allowed")
            kind = SUPPORTED_SUFFIXES.get(path.suffix.casefold())
            if kind:
                size = path.stat().st_size
                if size > MAX_EXPORT_BYTES:
                    raise ExportInboxError("export exceeds the size limit")
                found.append((path, kind, size))
        return found

    def dry_run(self) -> dict[str, Any]:
        files = self._files()
        types: dict[str, int] = {}
        for _, kind, _ in files:
            types[kind] = types.get(kind, 0) + 1
        total_entries = sum(1 for child in self.inbox.iterdir() if child.is_file() or child.is_symlink())
        return {
            "schema_version": SCHEMA_VERSION,
            "mode": "export-inbox-inventory",
            "files": len(files),
            "bytes": sum(size for _, _, size in files),
            "types": dict(sorted(types.items())),
            "supported": len(files),
            "ignored": total_entries - len(files),
            "privacy_mode": self.privacy_mode,
            "network_requests": 0,
        }

    def _read_file(self, path: Path) -> bytes:
        descriptor = None
        try:
            before = path.lstat()
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
                raise ExportInboxError("inbox entry changed during inspection")
            if before.st_nlink != 1:
                raise ExportInboxError("hard-linked inbox entries are not allowed")
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise ExportInboxError("inbox entry changed during inspection")
            chunks: list[bytes] = []
            remaining = MAX_EXPORT_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
        except ExportInboxError:
            raise
        except OSError as error:
            raise ExportInboxError("export cannot be read") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if len(raw) > MAX_EXPORT_BYTES:
            raise ExportInboxError("export exceeds the size limit")
        return raw

    def _record(self, *, conversation_id: str, message_id: str, parent_message_id: str | None,
                occurred_at: Any, role: Any, content: Any, provenance_root: str,
                ordinal: int, title: Any = None, model: Any = None) -> ConnectorRecord:
        conversation_id = _required_string(conversation_id, "conversation id")
        message_id = _required_string(message_id, "message id")
        role = _required_string(role, "role")
        native_id = _native_id(conversation_id, message_id)
        body = _safe_message_content(content)
        body.update({"role": role, "conversation_id": conversation_id, "message_id": message_id})
        if parent_message_id is not None:
            parent_message_id = _required_string(parent_message_id, "parent message id")
            body["parent_native_id"] = _native_id(conversation_id, parent_message_id)
        if isinstance(title, str) and title:
            body["conversation_title"] = title
        if isinstance(model, str) and model:
            body["model"] = model
        return ConnectorRecord(
            schema_version=SCHEMA_VERSION,
            native_id=native_id,
            occurred_at=_rfc3339(occurred_at),
            content=body,
            provenance={"uri": f"export://{provenance_root}/{ordinal}", "ordinal": ordinal},
        )

    def _official_records(self, value: Any, provenance_root: str) -> list[ConnectorRecord]:
        if not isinstance(value, list):
            raise ExportInboxError("JSON export must be an official conversation list")
        records: list[ConnectorRecord] = []
        ordinal = 0
        for conversation in value:
            if not isinstance(conversation, dict):
                raise ExportInboxError("export contains an invalid conversation")
            conversation_id = _required_string(conversation.get("id"), "conversation id")
            mapping = conversation.get("mapping")
            if not isinstance(mapping, dict):
                raise ExportInboxError("export contains an invalid conversation tree")
            node_messages: dict[str, str] = {}
            for node_id, node in mapping.items():
                if not isinstance(node_id, str) or not isinstance(node, dict):
                    raise ExportInboxError("export contains an invalid conversation node")
                message = node.get("message")
                if message is not None:
                    if not isinstance(message, dict):
                        raise ExportInboxError("export contains an invalid message")
                    node_messages[node_id] = _required_string(message.get("id"), "message id")
            pending: list[tuple[str, str, dict[str, Any], str | None]] = []
            for _node_id, node in mapping.items():
                message = node.get("message")
                if message is None:
                    continue
                parent_node = node.get("parent")
                if parent_node is not None and not isinstance(parent_node, str):
                    raise ExportInboxError("export contains an invalid parent reference")
                parent_message = node_messages.get(parent_node) if parent_node else None
                timestamp = (
                    message.get("create_time")
                    if message.get("create_time") is not None
                    else conversation.get("create_time", conversation.get("update_time"))
                )
                pending.append((
                    _rfc3339(timestamp),
                    _required_string(message.get("id"), "message id"), message, parent_message,
                ))
            for timestamp, message_id, message, parent_message in sorted(pending, key=lambda item: (item[0], item[1])):
                author = message.get("author")
                if not isinstance(author, dict):
                    raise ExportInboxError("export contains an invalid message author")
                metadata = message.get("metadata", {})
                if not isinstance(metadata, dict):
                    raise ExportInboxError("export contains invalid message metadata")
                records.append(self._record(
                    conversation_id=conversation_id, message_id=message_id,
                    parent_message_id=parent_message, occurred_at=timestamp,
                    role=author.get("role"), content=message.get("content"),
                    provenance_root=provenance_root, ordinal=ordinal,
                    title=conversation.get("title"), model=metadata.get("model_slug"),
                ))
                ordinal += 1
        return records

    def _jsonl_records(self, raw: bytes, provenance_root: str) -> list[ConnectorRecord]:
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as error:
            raise ExportInboxError("JSONL export is not UTF-8") from error
        records: list[ConnectorRecord] = []
        for ordinal, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as error:
                raise ExportInboxError("JSONL export is malformed") from error
            if not isinstance(value, dict):
                raise ExportInboxError("JSONL export contains an invalid record")
            records.append(self._record(
                conversation_id=value.get("conversation_id"), message_id=value.get("message_id"),
                parent_message_id=value.get("parent_message_id"), occurred_at=value.get("create_time"),
                role=value.get("role"), content=value.get("content"),
                provenance_root=provenance_root, ordinal=ordinal,
            ))
        return records

    def _json_records(self, raw: bytes, provenance_root: str) -> list[ConnectorRecord]:
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ExportInboxError("JSON export is malformed") from error
        return self._official_records(value, provenance_root)

    def _archive_records(self, raw: bytes, fingerprint: str) -> list[ConnectorRecord]:
        import io

        try:
            archive = zipfile.ZipFile(io.BytesIO(raw))
        except (OSError, zipfile.BadZipFile) as error:
            raise ExportInboxError("ZIP export is malformed") from error
        records: list[ConnectorRecord] = []
        with archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise ExportInboxError("ZIP export has too many members")
            expanded_bytes = 0
            for info in members:
                member_path = PurePosixPath(info.filename)
                if member_path.is_absolute() or ".." in member_path.parts or info.filename.startswith(("/", "\\")):
                    raise ExportInboxError("ZIP export contains an unsafe member")
                member_mode = (info.external_attr >> 16) & 0o170000
                if member_mode == stat.S_IFLNK:
                    raise ExportInboxError("ZIP export contains an unsafe symlink")
                if info.is_dir():
                    continue
                suffix = member_path.suffix.casefold()
                if suffix not in {".json", ".jsonl"}:
                    continue
                if info.file_size > MAX_EXPORT_BYTES or info.compress_size > MAX_EXPORT_BYTES:
                    raise ExportInboxError("ZIP member exceeds the size limit")
                expanded_bytes += info.file_size
                if expanded_bytes > MAX_EXPORT_BYTES:
                    raise ExportInboxError("ZIP export exceeds the expansion limit")
                try:
                    member = archive.read(info)
                except (OSError, RuntimeError, zipfile.BadZipFile) as error:
                    raise ExportInboxError("ZIP member cannot be read") from error
                member_root = f"{fingerprint}/{_digest(member)[:24]}"
                if suffix == ".json":
                    try:
                        value = json.loads(member)
                    except (UnicodeDecodeError, json.JSONDecodeError) as error:
                        raise ExportInboxError("ZIP JSON member is malformed") from error
                    conversation_shaped = (
                        isinstance(value, list)
                        and any(isinstance(item, dict) and "mapping" in item for item in value)
                    )
                    if conversation_shaped:
                        records.extend(self._official_records(value, member_root))
                else:
                    try:
                        lines = [line for line in member.decode("utf-8").splitlines() if line.strip()]
                        first = json.loads(lines[0]) if lines else None
                    except (UnicodeDecodeError, json.JSONDecodeError) as error:
                        raise ExportInboxError("ZIP JSONL member is malformed") from error
                    if isinstance(first, dict) and {"conversation_id", "message_id"} <= set(first):
                        records.extend(self._jsonl_records(member, member_root))
        return records

    def _parse(self, path: Path, kind: str) -> _ParsedExport:
        raw = self._read_file(path)
        fingerprint = _digest(raw)
        export_id = "exp_" + fingerprint[:32]
        provenance_root = f"{fingerprint}/{fingerprint[:24]}"
        if kind == "json":
            records = self._json_records(raw, provenance_root)
        elif kind == "jsonl":
            records = self._jsonl_records(raw, provenance_root)
        else:
            records = self._archive_records(raw, fingerprint)
        unique: dict[str, ConnectorRecord] = {}
        for record in records:
            existing = unique.get(record.native_id)
            if existing is not None and (
                existing.occurred_at != record.occurred_at or existing.content != record.content
            ):
                raise ExportInboxError("export contains conflicting message identities")
            if existing is None or json.dumps(record.provenance, sort_keys=True) < json.dumps(existing.provenance, sort_keys=True):
                unique[record.native_id] = record
        ordered = tuple(sorted(unique.values(), key=lambda record: (record.occurred_at, record.native_id)))
        return _ParsedExport(export_id, fingerprint, ordered)

    def _scan(self) -> list[_ParsedExport]:
        parsed: dict[str, _ParsedExport] = {}
        for path, kind, _ in self._files():
            raw_fingerprint: str | None = None
            # Parsing is deliberately completed for every novel file before any
            # catalog transaction, so malformed input leaves no partial state.
            item = self._parse(path, kind)
            raw_fingerprint = item.fingerprint
            existing = self.db.execute(
                "SELECT status FROM exports WHERE fingerprint=?", (raw_fingerprint,)
            ).fetchone()
            if existing and existing["status"] == "removed":
                continue
            parsed.setdefault(raw_fingerprint, item)
        with self.db:
            for item in parsed.values():
                self.db.execute(
                    "INSERT OR IGNORE INTO exports(export_id,fingerprint,status,record_count) VALUES (?,?, 'active',?)",
                    (item.export_id, item.fingerprint, len(item.records)),
                )
                for record in item.records:
                    self.db.execute(
                        "INSERT OR IGNORE INTO export_records(export_id,native_id,occurred_at,provenance_json) VALUES (?,?,?,?)",
                        (item.export_id, record.native_id, record.occurred_at,
                         json.dumps(record.provenance, sort_keys=True, separators=(",", ":"))),
                    )
        return list(parsed.values())

    def exports(self) -> list[dict[str, Any]]:
        return [
            {"export_id": row["export_id"], "status": row["status"], "records": row["record_count"]}
            for row in self.db.execute(
                "SELECT export_id,status,record_count FROM exports ORDER BY export_id"
            )
        ]

    def queue_remove(self, export_id: str) -> dict[str, str]:
        if not isinstance(export_id, str) or not export_id.startswith("exp_"):
            raise ExportInboxError("export identity is invalid")
        row = self.db.execute("SELECT status FROM exports WHERE export_id=?", (export_id,)).fetchone()
        if row is None:
            raise ExportInboxError("export identity is unknown")
        if row["status"] == "removed":
            return {"export_id": export_id, "status": "already_removed"}
        if row["status"] == "removing":
            return {"export_id": export_id, "status": "already_queued"}
        if self.db.execute(
            "SELECT 1 FROM exports WHERE status='removing' AND removal_started=1 LIMIT 1"
        ).fetchone():
            raise ExportInboxError("another export removal is in progress")
        with self.db:
            self.db.execute(
                "UPDATE exports SET status='removing',removal_started=0 WHERE export_id=?",
                (export_id,),
            )
        return {"export_id": export_id, "status": "queued"}

    def _removing_export(self) -> str | None:
        row = self.db.execute(
            "SELECT export_id FROM exports WHERE status='removing' ORDER BY export_id LIMIT 1"
        ).fetchone()
        return row["export_id"] if row else None

    def _removal_records(self, export_id: str) -> list[ConnectorRecord]:
        rows = self.db.execute("""
            SELECT own.native_id,own.occurred_at,own.provenance_json
            FROM export_records own
            WHERE own.export_id=? AND NOT EXISTS (
              SELECT 1 FROM export_records other
              JOIN exports e ON e.export_id=other.export_id
              WHERE other.native_id=own.native_id AND other.export_id<>own.export_id
                AND e.status IN ('active','removing')
            )
            ORDER BY own.occurred_at,own.native_id
        """, (export_id,)).fetchall()
        return [ConnectorRecord(
            schema_version=SCHEMA_VERSION, native_id=row["native_id"],
            occurred_at=row["occurred_at"], content={},
            provenance={**json.loads(row["provenance_json"]), "removal": "explicit"},
            deleted=True,
        ) for row in rows]

    @staticmethod
    def _removal_cursor(cursor: str | None) -> tuple[str, int, int] | None:
        if not isinstance(cursor, str) or not cursor.startswith("v1:remove:"):
            return None
        pieces = cursor.split(":")
        if len(pieces) != 5:
            return None
        try:
            offset, total = int(pieces[3]), int(pieces[4])
        except ValueError:
            return None
        if offset < 0 or total < 0:
            return None
        return pieces[2], offset, total

    def _pull_removal(self, cursor: str | None) -> ConnectorPage | None:
        committed = self._removal_cursor(cursor)
        if committed and committed[1] == committed[2]:
            with self.db:
                self.db.execute(
                    "UPDATE exports SET status='removed' WHERE export_id=? AND status='removing'",
                    (committed[0],),
                )
        export_id = self._removing_export()
        if export_id is None:
            return None
        with self.db:
            self.db.execute(
                "UPDATE exports SET removal_started=1 WHERE export_id=?", (export_id,)
            )
        records = self._removal_records(export_id)
        offset = committed[1] if committed and committed[0] == export_id and committed[1] < committed[2] else 0
        page_records = tuple(records[offset:offset + self.page_size])
        next_offset = offset + len(page_records)
        next_cursor = f"v1:remove:{export_id}:{next_offset}:{len(records)}"
        return ConnectorPage(records=page_records, next_cursor=next_cursor, has_more=next_offset < len(records))

    def pull(self, cursor: str | None) -> ConnectorPage:
        removal = self._pull_removal(cursor)
        if removal is not None:
            return removal
        exports = self._scan()
        unique: dict[str, ConnectorRecord] = {}
        for item in sorted(exports, key=lambda value: value.fingerprint):
            for record in item.records:
                unique.setdefault(record.native_id, record)
        records = sorted(unique.values(), key=lambda record: (record.occurred_at, record.native_id))
        snapshot = _digest("\n".join(
            [item.fingerprint for item in sorted(exports, key=lambda item: item.fingerprint)]
            + [record.native_id for record in records]
        ))[:32]
        prefix = f"v1:data:{snapshot}:"
        offset = 0
        if isinstance(cursor, str) and cursor.startswith(prefix):
            try:
                offset = int(cursor[len(prefix):])
            except ValueError:
                offset = 0
            if not 0 <= offset <= len(records):
                offset = 0
        page_records = tuple(records[offset:offset + self.page_size])
        next_offset = offset + len(page_records)
        next_cursor = f"{prefix}{next_offset}"
        return ConnectorPage(records=page_records, next_cursor=next_cursor, has_more=next_offset < len(records))
