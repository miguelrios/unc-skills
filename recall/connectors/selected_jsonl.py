"""Closed-schema JSONL import from one explicitly selected local root."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path

from connectors.local_file import explicit_root, read_stable_file
from connectors.portable_pim import EPOCH, _bounded
from connectors.sdk import (
    IDENTITY,
    SOURCE_ID,
    ConnectorContractError,
    ConnectorPage,
    ConnectorRecordV2,
)


MAX_FILES = 10_000
MAX_FILE_BYTES = 2_000_000
MAX_TOTAL_BYTES = 1_000_000_000
MAX_LINE_BYTES = 1_000_000
MAX_RECORDS = 250_000
MAX_CYCLE = 2_147_483_647
CURSOR = re.compile(r"selected-jsonl-v1:(\d{1,10}):(\d{1,10}):([0-9a-f]{64})\Z")
RECORD_FIELDS = {"id", "occurred_at", "text", "title"}


class SelectedJsonlConnector:
    connector_id = "portable.jsonl"

    def __init__(
        self,
        *,
        root: Path,
        source_id: str,
        removed_native_ids: tuple[str, ...] = (),
        max_depth: int = 8,
        page_size: int = 500,
    ):
        if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
            raise ConnectorContractError("jsonl_source_id_invalid")
        if type(max_depth) is not int or not 0 <= max_depth <= 32:
            raise ConnectorContractError("jsonl_max_depth_invalid")
        if type(page_size) is not int or not 1 <= page_size <= 500:
            raise ConnectorContractError("jsonl_page_size_invalid")
        if (
            not isinstance(removed_native_ids, tuple)
            or len(removed_native_ids) > 10_000
            or len(removed_native_ids) != len(set(removed_native_ids))
            or removed_native_ids != tuple(sorted(removed_native_ids))
            or any(
                not isinstance(value, str)
                or not value.startswith("jsonl:")
                or not IDENTITY.fullmatch(value)
                for value in removed_native_ids
            )
        ):
            raise ConnectorContractError("jsonl_removed_ids_invalid")
        self.root = explicit_root(root)
        self.source_id = source_id
        self.removed_native_ids = removed_native_ids
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
                raise ConnectorContractError("jsonl_tree_unavailable") from None
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                path = Path(entry.path)
                try:
                    details = path.lstat()
                except OSError:
                    raise ConnectorContractError("jsonl_path_unavailable") from None
                if stat.S_ISLNK(details.st_mode):
                    raise ConnectorContractError("jsonl_symlink")
                if stat.S_ISDIR(details.st_mode):
                    if depth < self.max_depth:
                        pending.append((path, depth + 1))
                    continue
                if stat.S_ISREG(details.st_mode) and path.suffix.lower() == ".jsonl":
                    paths.append(path)
                    if len(paths) > MAX_FILES:
                        raise ConnectorContractError("jsonl_too_many_files")
        return sorted(paths, key=lambda path: path.relative_to(self.root).as_posix())

    def _records(self) -> list[ConnectorRecordV2]:
        records = {}
        total_bytes = 0
        total_records = 0
        for path in self._paths():
            raw = read_stable_file(
                path, root=self.root, maximum_bytes=MAX_FILE_BYTES
            )
            total_bytes += len(raw)
            if total_bytes > MAX_TOTAL_BYTES:
                raise ConnectorContractError("jsonl_total_too_large")
            relative = path.relative_to(self.root).as_posix()
            for raw_line in raw.splitlines():
                if not raw_line.strip():
                    continue
                if len(raw_line) > MAX_LINE_BYTES:
                    raise ConnectorContractError("jsonl_line_too_large")
                try:
                    value = json.loads(
                        raw_line,
                        parse_constant=lambda _value: (
                            _ for _ in ()
                        ).throw(ValueError()),
                    )
                except (
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                    ValueError,
                    RecursionError,
                ):
                    raise ConnectorContractError("jsonl_record_invalid") from None
                if (
                    not isinstance(value, dict)
                    or set(value) - RECORD_FIELDS
                    or not {"id", "text"}.issubset(value)
                    or not isinstance(value["id"], str)
                    or not IDENTITY.fullmatch(value["id"])
                    or not isinstance(value["text"], str)
                    or (
                        "title" in value
                        and not isinstance(value["title"], str)
                    )
                    or (
                        "occurred_at" in value
                        and not isinstance(value["occurred_at"], str)
                    )
                ):
                    raise ConnectorContractError("jsonl_record_invalid")
                native_id = "jsonl:" + hashlib.sha256(
                    f"{relative}\0{value['id']}".encode()
                ).hexdigest()
                if native_id in records:
                    raise ConnectorContractError("jsonl_duplicate_identity")
                occurred_at = value.get("occurred_at", EPOCH)
                records[native_id] = ConnectorRecordV2(
                    schema_version=2,
                    native_id=native_id,
                    occurred_at=occurred_at,
                    content={
                        "kind": "document.v1",
                        "document_id": native_id,
                        "mime_type": "application/x-ndjson",
                        "name": _bounded(
                            value.get("title") or value["id"],
                            "jsonl_title",
                            maximum=10_000,
                        ),
                        "surface": "selected_jsonl",
                        "text": _bounded(value["text"], "jsonl_text"),
                    },
                    provenance={"uri": f"connector://selected-jsonl/{native_id}"},
                )
                total_records += 1
                if total_records > MAX_RECORDS:
                    raise ConnectorContractError("jsonl_too_many_records")
        for native_id in self.removed_native_ids:
            records[native_id] = ConnectorRecordV2(
                schema_version=2,
                native_id=native_id,
                occurred_at=EPOCH,
                content={"kind": "document.v1"},
                provenance={
                    "uri": f"connector://selected-jsonl/{native_id}",
                    "explicit_owner_removal": True,
                },
                deleted=True,
            )
        return [records[key] for key in sorted(records)]

    @staticmethod
    def _cursor(value: str | None) -> tuple[int, int, str]:
        if value is None:
            return 0, 0, "0" * 64
        if not isinstance(value, str):
            raise ConnectorContractError("jsonl_cursor_invalid")
        match = CURSOR.fullmatch(value)
        if match is None or int(match.group(1)) > MAX_CYCLE:
            raise ConnectorContractError("jsonl_cursor_invalid")
        return int(match.group(1)), int(match.group(2)), match.group(3)

    def pull(self, cursor: str | None) -> ConnectorPage:
        cycle, offset, previous_digest = self._cursor(cursor)
        records = self._records()
        digest = hashlib.sha256()
        for record in records:
            digest.update(json.dumps(
                record.to_mapping(), sort_keys=True, separators=(",", ":"),
            ).encode())
        current_digest = digest.hexdigest()
        if offset > len(records) or (offset and previous_digest != current_digest):
            offset = 0
        end = min(len(records), offset + self.page_size)
        has_more = end < len(records)
        next_cursor = (
            f"selected-jsonl-v1:{cycle}:{end}:{current_digest}"
            if has_more
            else (
                f"selected-jsonl-v1:"
                f"{0 if cycle == MAX_CYCLE else cycle + 1}:0:{current_digest}"
            )
        )
        return ConnectorPage(
            records=tuple(records[offset:end]),
            next_cursor=next_cursor,
            has_more=has_more,
        )


__all__ = ["SelectedJsonlConnector"]
