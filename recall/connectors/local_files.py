"""Explicit selected-text and Obsidian-root connector."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from datetime import datetime, timezone
from pathlib import Path

from connectors.local_file import explicit_root, read_stable_file
from connectors.sdk import (
    SOURCE_ID,
    ConnectorContractError,
    ConnectorPage,
    ConnectorRecordV2,
)


CONNECTOR_ID = "local.selected-text"
ALLOWED_EXTENSIONS = {".md", ".markdown", ".txt"}
MAX_FILES = 10_000
MAX_FILE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 1_000_000_000
CURSOR = re.compile(r"selected-text-v1:(\d{1,10}):(\d{1,10}):([0-9a-f]{64})\Z")
MAX_CYCLE = 2_147_483_647


class SelectedTextConnector:
    connector_id = CONNECTOR_ID

    def __init__(
        self,
        *,
        root: Path,
        source_id: str,
        extensions: tuple[str, ...] = (".markdown", ".md", ".txt"),
        max_depth: int = 8,
        page_size: int = 500,
    ):
        if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
            raise ConnectorContractError("selected_text_source_id_invalid")
        if (
            not isinstance(extensions, tuple)
            or not extensions
            or any(not isinstance(item, str) for item in extensions)
            or set(extensions) - ALLOWED_EXTENSIONS
            or len(extensions) != len(set(extensions))
            or extensions != tuple(sorted(extensions))
        ):
            raise ConnectorContractError("selected_text_extensions_invalid")
        if type(max_depth) is not int or not 0 <= max_depth <= 32:
            raise ConnectorContractError("selected_text_max_depth_invalid")
        if type(page_size) is not int or not 1 <= page_size <= 500:
            raise ConnectorContractError("selected_text_page_size_invalid")
        self.root = explicit_root(root)
        metadata = self.root / ".obsidian"
        try:
            metadata_details = metadata.lstat()
        except OSError:
            self.surface = "selected-text"
        else:
            if stat.S_ISLNK(metadata_details.st_mode):
                raise ConnectorContractError("selected_text_metadata_symlink")
            self.surface = (
                "obsidian"
                if stat.S_ISDIR(metadata_details.st_mode)
                else "selected-text"
            )
        self.source_id = source_id
        self.extensions = extensions
        self.max_depth = max_depth
        self.page_size = page_size

    def _paths(self) -> list[Path]:
        paths = []
        pending = [(self.root, 0)]
        while pending:
            directory, depth = pending.pop()
            try:
                entries = sorted(os.scandir(directory), key=lambda item: item.name)
            except OSError:
                raise ConnectorContractError("selected_text_tree_unavailable") from None
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                path = Path(entry.path)
                try:
                    details = path.lstat()
                except OSError:
                    raise ConnectorContractError("selected_text_path_unavailable") from None
                if stat.S_ISLNK(details.st_mode):
                    raise ConnectorContractError("selected_text_symlink")
                if stat.S_ISDIR(details.st_mode):
                    if depth < self.max_depth:
                        pending.append((path, depth + 1))
                    continue
                if (
                    stat.S_ISREG(details.st_mode)
                    and path.suffix.lower() in self.extensions
                ):
                    paths.append(path)
                    if len(paths) > MAX_FILES:
                        raise ConnectorContractError("selected_text_too_many_files")
        return sorted(paths, key=lambda path: path.relative_to(self.root).as_posix())

    def _records(self) -> tuple[list[ConnectorRecordV2], str]:
        records = []
        total = 0
        for path in self._paths():
            raw = read_stable_file(
                path,
                root=self.root,
                maximum_bytes=MAX_FILE_BYTES,
            )
            total += len(raw)
            if total > MAX_TOTAL_BYTES:
                raise ConnectorContractError("selected_text_total_too_large")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise ConnectorContractError("selected_text_encoding_invalid") from None
            if "\x00" in text:
                raise ConnectorContractError("selected_text_encoding_invalid")
            relative = path.relative_to(self.root).as_posix()
            native_id = "text:" + hashlib.sha256(relative.encode()).hexdigest()
            try:
                modified = datetime.fromtimestamp(
                    path.stat().st_mtime,
                    tz=timezone.utc,
                ).isoformat().replace("+00:00", "Z")
            except (OSError, OverflowError, ValueError):
                raise ConnectorContractError(
                    "selected_text_path_unavailable"
                ) from None
            mime_type = (
                "text/markdown"
                if path.suffix.lower() in {".md", ".markdown"}
                else "text/plain"
            )
            records.append(ConnectorRecordV2(
                schema_version=2,
                native_id=native_id,
                occurred_at=modified,
                content={
                    "kind": "document.v1",
                    "document_id": native_id,
                    "mime_type": mime_type,
                    "modified_at": modified,
                    "name": relative,
                    "surface": self.surface,
                    "text": text,
                },
                provenance={"uri": f"connector://selected-text/{native_id}"},
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
            raise ConnectorContractError("selected_text_cursor_invalid")
        match = CURSOR.fullmatch(value)
        if match is None:
            raise ConnectorContractError("selected_text_cursor_invalid")
        cycle = int(match.group(1))
        if cycle > MAX_CYCLE:
            raise ConnectorContractError("selected_text_cursor_invalid")
        return cycle, int(match.group(2)), match.group(3)

    def pull(self, cursor: str | None) -> ConnectorPage:
        cycle, offset, previous_digest = self._cursor(cursor)
        records, digest = self._records()
        if offset > len(records) or (offset and previous_digest != digest):
            offset = 0
        end = min(len(records), offset + self.page_size)
        has_more = end < len(records)
        next_cursor = (
            f"selected-text-v1:{cycle}:{end}:{digest}"
            if has_more
            else f"selected-text-v1:{0 if cycle == MAX_CYCLE else cycle + 1}:0:{digest}"
        )
        return ConnectorPage(
            records=tuple(records[offset:end]),
            next_cursor=next_cursor,
            has_more=has_more,
        )


__all__ = ["SelectedTextConnector"]
