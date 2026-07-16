"""Privacy-minimal projection contract for Claude Cowork local project records."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Mapping

from connectors.sdk import ConnectorContractError, ConnectorPage, ConnectorRecord


CONNECTOR_ID = "anthropic.cowork-local"
MAX_TEXT_CHARS = 500_000
MAX_FILE_BYTES = 256_000_000
MAX_LINE_BYTES = 1_048_576
MAX_FILES = 10_000
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@=-]{0,127}\Z")
CURSOR = re.compile(r"cowork-v1:(\d{1,12}):(\d{1,12}):([0-9a-f]{64})\Z")
EMPTY_DIGEST = "0" * 64


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
            native_parent_id=session_id,
            occurred_at=timestamp,
            content=content,
            provenance={"uri": f"connector://{CONNECTOR_ID}/{native_id}"},
        )
    except ConnectorContractError as error:
        raise CoworkLocalError("invalid_connector_record") from error


class CoworkLocalConnector:
    """Bounded full-sweep Cowork connector with ACK-ledger replay suppression.

    A sweep deliberately re-reads only the explicit Cowork project-log tree. Stable native
    identities let ``ConnectorRunner`` suppress acknowledged versions, while a content change
    becomes a new revision. No local disappearance has deletion semantics.
    """

    connector_id = CONNECTOR_ID

    def __init__(self, *, root: Path, source_id: str, page_size: int = 500):
        candidate = Path(root).expanduser()
        if not candidate.is_absolute():
            raise ConnectorContractError("cowork_local_root_not_absolute")
        try:
            details = candidate.lstat()
        except OSError as error:
            raise ConnectorContractError("cowork_local_root_unavailable") from error
        if stat.S_ISLNK(details.st_mode):
            raise ConnectorContractError("cowork_local_root_symlink")
        if not stat.S_ISDIR(details.st_mode):
            raise ConnectorContractError("cowork_local_root_not_directory")
        if not isinstance(source_id, str):
            raise ConnectorContractError("cowork_local_source_id_invalid")
        if not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= 500:
            raise ConnectorContractError("cowork_local_page_size_invalid")
        self.root = candidate.resolve(strict=True)
        self.source_id = source_id
        self.page_size = page_size

    def _validate_path(self, path: Path) -> os.stat_result:
        try:
            relative = path.relative_to(self.root)
        except ValueError as error:
            raise ConnectorContractError("cowork_local_path_escape") from error
        current = self.root
        try:
            for part in relative.parts:
                current = current / part
                details = current.lstat()
                if stat.S_ISLNK(details.st_mode):
                    raise ConnectorContractError("cowork_local_symlink")
        except ConnectorContractError:
            raise
        except OSError as error:
            raise ConnectorContractError("cowork_local_path_unavailable") from error
        if not stat.S_ISREG(details.st_mode):
            raise ConnectorContractError("cowork_local_not_regular")
        if details.st_nlink != 1:
            raise ConnectorContractError("cowork_local_hard_link")
        if details.st_size > MAX_FILE_BYTES:
            raise ConnectorContractError("cowork_local_file_too_large")
        return details

    def _paths(self) -> list[Path]:
        paths = sorted(self.root.glob("*/*/local_*/.claude/projects/*/*.jsonl"))
        if len(paths) > MAX_FILES:
            raise ConnectorContractError("cowork_local_too_many_files")
        for path in paths:
            self._validate_path(path)
        return paths

    def _snapshot_lines(self, path: Path) -> list[bytes]:
        before = self._validate_path(path)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as error:
            raise ConnectorContractError("cowork_local_open_failed") from error
        lines: list[bytes] = []
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise ConnectorContractError("cowork_local_replaced")
            remaining = before.st_size
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                while remaining:
                    line = source.readline(min(MAX_LINE_BYTES + 1, remaining))
                    if not line:
                        break
                    remaining -= len(line)
                    if len(line) > MAX_LINE_BYTES:
                        raise ConnectorContractError("cowork_local_record_too_large")
                    if not line.endswith(b"\n"):
                        break
                    lines.append(line)
            try:
                after = path.lstat()
            except OSError as error:
                raise ConnectorContractError("cowork_local_replaced") from error
            if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
                raise ConnectorContractError("cowork_local_replaced")
            if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                raise ConnectorContractError("cowork_local_changed_during_read")
        finally:
            os.close(descriptor)
        return lines

    def _records(self) -> tuple[list[ConnectorRecord], str]:
        by_identity: dict[str, ConnectorRecord] = {}
        for path in self._paths():
            for line in self._snapshot_lines(path):
                try:
                    value = json.loads(line)
                    projected = project_cowork_record(value)
                except (json.JSONDecodeError, UnicodeDecodeError, CoworkLocalError) as error:
                    raise ConnectorContractError("cowork_local_invalid_record") from error
                if projected is None:
                    continue
                previous = by_identity.get(projected.native_id)
                if previous is not None and previous != projected:
                    raise ConnectorContractError("cowork_local_identity_conflict")
                by_identity[projected.native_id] = projected
        records = sorted(by_identity.values(), key=lambda record: record.native_id)
        digest = hashlib.sha256()
        for record in records:
            digest.update(json.dumps({
                "native_id": record.native_id,
                "occurred_at": record.occurred_at,
                "content": record.content,
            }, sort_keys=True, separators=(",", ":")).encode())
            digest.update(b"\n")
        return records, digest.hexdigest()

    @staticmethod
    def _parse_cursor(cursor: str | None) -> tuple[int, int, str]:
        if cursor is None:
            return 0, 0, EMPTY_DIGEST
        if not isinstance(cursor, str):
            raise ConnectorContractError("cowork_local_cursor_invalid")
        match = CURSOR.fullmatch(cursor)
        if match is None:
            raise ConnectorContractError("cowork_local_cursor_invalid")
        return int(match.group(1)), int(match.group(2)), match.group(3)

    def pull(self, cursor: str | None) -> ConnectorPage:
        cycle, offset, prior_digest = self._parse_cursor(cursor)
        records, digest = self._records()
        if offset > len(records) or (offset and prior_digest != digest):
            offset = 0
        end = min(len(records), offset + self.page_size)
        has_more = end < len(records)
        if has_more:
            next_cursor = f"cowork-v1:{cycle}:{end}:{digest}"
        else:
            next_cursor = f"cowork-v1:{cycle + 1}:0:{digest}"
        return ConnectorPage(
            records=tuple(records[offset:end]),
            next_cursor=next_cursor,
            has_more=has_more,
        )
