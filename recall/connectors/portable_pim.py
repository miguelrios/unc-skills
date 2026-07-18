"""Bounded portable imports for mail, iCalendar, and vCard files."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from pathlib import Path
from typing import Any

from connectors.local_file import read_stable_file
from connectors.sdk import (
    IDENTITY,
    SOURCE_ID,
    ConnectorContractError,
    ConnectorPage,
    ConnectorRecordV2,
)


EPOCH = "1970-01-01T00:00:00Z"
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_RECORDS = 500_000
MAX_TEXT_CHARS = 750_000
MAX_LINE_BYTES = 1_000_000
MAX_CYCLE = 2_147_483_647
CURSOR = re.compile(r"portable-v1:(\d{1,10}):(\d{1,10}):([0-9a-f]{64})\Z")


def _canonical_tuple(
    values: tuple[str, ...], label: str, *, prefix: str | None = None
) -> tuple[str, ...]:
    if (
        not isinstance(values, tuple)
        or len(values) > 10_000
        or any(
            not isinstance(value, str)
            or not value
            or len(value) > 512
            or (prefix is not None and not IDENTITY.fullmatch(value))
            or (prefix is not None and not value.startswith(prefix))
            for value in values
        )
        or len(values) != len(set(values))
        or values != tuple(sorted(values))
    ):
        raise ConnectorContractError(f"portable_{label}_invalid")
    return values


def _bounded(value: str, label: str, *, maximum: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(value, str) or "\x00" in value or len(value) > maximum:
        raise ConnectorContractError(f"portable_{label}_invalid")
    return value


def _digest(records: list[ConnectorRecordV2]) -> str:
    value = hashlib.sha256()
    for record in records:
        value.update(json.dumps(
            record.to_mapping(), sort_keys=True, separators=(",", ":"),
        ).encode())
    return value.hexdigest()


class _SnapshotImport:
    connector_id = ""
    native_prefix = ""
    record_kind = ""

    def __init__(
        self,
        *,
        path: Path,
        source_id: str,
        archive_id: str,
        owner_identifiers: tuple[str, ...] = (),
        removed_native_ids: tuple[str, ...] = (),
        page_size: int = 500,
    ):
        if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
            raise ConnectorContractError("portable_source_id_invalid")
        if not isinstance(archive_id, str) or not IDENTITY.fullmatch(archive_id):
            raise ConnectorContractError("portable_archive_id_invalid")
        if type(page_size) is not int or not 1 <= page_size <= 500:
            raise ConnectorContractError("portable_page_size_invalid")
        self.path = Path(path)
        read_stable_file(self.path, maximum_bytes=MAX_FILE_BYTES)
        self.source_id = source_id
        self.archive_id = archive_id
        owners = tuple(
            sorted(value.lower() for value in _canonical_tuple(owner_identifiers, "owners"))
        )
        if len(owners) != len(set(owners)):
            raise ConnectorContractError("portable_owners_invalid")
        self.owner_identifiers = owners
        self.removed_native_ids = _canonical_tuple(
            removed_native_ids, "removed_ids", prefix=self.native_prefix,
        )
        self.page_size = page_size

    def _live_records(self) -> list[ConnectorRecordV2]:
        raise NotImplementedError

    def _records(self) -> list[ConnectorRecordV2]:
        records = {record.native_id: record for record in self._live_records()}
        for native_id in self.removed_native_ids:
            records[native_id] = ConnectorRecordV2(
                schema_version=2,
                native_id=native_id,
                occurred_at=EPOCH,
                content={"kind": self.record_kind},
                provenance={
                    "uri": f"connector://{self.connector_id}/{native_id}",
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
            raise ConnectorContractError("portable_cursor_invalid")
        match = CURSOR.fullmatch(value)
        if match is None or int(match.group(1)) > MAX_CYCLE:
            raise ConnectorContractError("portable_cursor_invalid")
        return int(match.group(1)), int(match.group(2)), match.group(3)

    def pull(self, cursor: str | None) -> ConnectorPage:
        cycle, offset, previous_digest = self._cursor(cursor)
        records = self._records()
        if len(records) > MAX_RECORDS:
            raise ConnectorContractError("portable_too_many_records")
        digest = _digest(records)
        if offset > len(records) or (offset and previous_digest != digest):
            offset = 0
        end = min(len(records), offset + self.page_size)
        has_more = end < len(records)
        next_cursor = (
            f"portable-v1:{cycle}:{end}:{digest}"
            if has_more
            else f"portable-v1:{0 if cycle == MAX_CYCLE else cycle + 1}:0:{digest}"
        )
        return ConnectorPage(
            records=tuple(records[offset:end]),
            next_cursor=next_cursor,
            has_more=has_more,
        )


def _mail_timestamp(value: str | None) -> str:
    if not value:
        return EPOCH
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        return EPOCH


def _mail_body(message: Any) -> str:
    values = []
    parts = message.walk() if message.is_multipart() else (message,)
    for part in parts:
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        if part.get_content_type() != "text/plain":
            continue
        try:
            content = part.get_content()
        except (LookupError, UnicodeError):
            raise ConnectorContractError("portable_mail_encoding_invalid") from None
        if not isinstance(content, str):
            raise ConnectorContractError("portable_mail_encoding_invalid")
        values.append(content)
    return _bounded("\n".join(values), "mail_text")


def _mbox_messages(raw: bytes) -> list[bytes]:
    messages: list[bytearray] = []
    for line in raw.splitlines(keepends=True):
        if len(line) > MAX_LINE_BYTES:
            raise ConnectorContractError("portable_mail_line_too_large")
        if line.startswith(b"From "):
            messages.append(bytearray())
        elif not messages:
            if line.strip():
                raise ConnectorContractError("portable_mail_format_invalid")
        else:
            messages[-1].extend(line[1:] if line.startswith(b">From ") else line)
    if not messages or any(not value for value in messages):
        raise ConnectorContractError("portable_mail_format_invalid")
    return [bytes(value) for value in messages]


class MailImportConnector(_SnapshotImport):
    connector_id = "portable.mail"
    native_prefix = "mail:"
    record_kind = "communication_message.v1"

    def _live_records(self) -> list[ConnectorRecordV2]:
        raw = read_stable_file(self.path, maximum_bytes=MAX_FILE_BYTES)
        suffix = self.path.suffix.lower()
        if suffix == ".eml":
            values = [raw]
        elif suffix in {".mbox", ".mbx"}:
            values = _mbox_messages(raw)
        else:
            raise ConnectorContractError("portable_mail_type_invalid")
        if len(values) > MAX_RECORDS:
            raise ConnectorContractError("portable_too_many_records")
        records = {}
        fallback_ordinals: defaultdict[str, int] = defaultdict(int)
        for value in values:
            try:
                message = BytesParser(policy=policy.default).parsebytes(value)
            except (LookupError, ValueError):
                raise ConnectorContractError("portable_mail_format_invalid") from None
            addresses = {
                address.lower()
                for _name, address in getaddresses(
                    message.get_all("from", []) + message.get_all("to", [])
                    + message.get_all("cc", [])
                )
                if address
            }
            senders = {
                address.lower()
                for _name, address in getaddresses(message.get_all("from", []))
                if address
            }
            raw_message_id = str(message.get("message-id") or "").strip().strip("<>")
            fallback = "\0".join([
                str(message.get("date") or ""),
                str(message.get("from") or ""),
                str(message.get("to") or ""),
                str(message.get("subject") or ""),
            ])
            fallback_ordinal = fallback_ordinals[fallback]
            fallback_ordinals[fallback] += 1
            identity = raw_message_id or f"{fallback}\0{fallback_ordinal}"
            native_id = "mail:" + hashlib.sha256(
                f"{self.archive_id}\0{identity}".encode()
            ).hexdigest()
            subject = _bounded(str(message.get("subject") or ""), "mail_subject", maximum=10_000)
            thread_key = re.sub(
                r"(?i)^(?:(?:re|fwd?):\s*)+", "", subject,
            ).strip().lower()
            conversation_id = "mail-thread:" + hashlib.sha256(
                f"{self.archive_id}\0{thread_key or native_id}".encode()
            ).hexdigest()
            occurred_at = _mail_timestamp(message.get("date"))
            content = {
                "kind": self.record_kind,
                "conversation_id": conversation_id,
                "direction": (
                    "outbound"
                    if senders & set(self.owner_identifiers)
                    else "inbound"
                ),
                "format": "eml" if suffix == ".eml" else "mbox",
                "message_id": native_id,
                "sent_at": occurred_at,
                "surface": "portable_mail",
                "text": _mail_body(message),
            }
            if subject:
                content["subject"] = subject
            if senders:
                content["author_id"] = sorted(senders)[0]
            if addresses:
                content["participant_ids"] = sorted(addresses)
            records[native_id] = ConnectorRecordV2(
                schema_version=2,
                native_id=native_id,
                native_parent_id=conversation_id,
                occurred_at=occurred_at,
                content=content,
                provenance={"uri": f"connector://portable-mail/{native_id}"},
            )
        return [records[key] for key in sorted(records)]


def _unfold(raw: bytes, label: str) -> list[str]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ConnectorContractError(f"portable_{label}_encoding_invalid") from None
    lines: list[str] = []
    for line in text.splitlines():
        if len(line.encode()) > MAX_LINE_BYTES:
            raise ConnectorContractError(f"portable_{label}_line_too_large")
        if line.startswith((" ", "\t")):
            if not lines:
                raise ConnectorContractError(f"portable_{label}_format_invalid")
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _property(line: str, label: str) -> tuple[str, dict[str, str], str]:
    if ":" not in line:
        raise ConnectorContractError(f"portable_{label}_format_invalid")
    head, value = line.split(":", 1)
    parts = head.split(";")
    name = parts[0].upper()
    params = {}
    for item in parts[1:]:
        if "=" not in item:
            raise ConnectorContractError(f"portable_{label}_format_invalid")
        key, parameter = item.split("=", 1)
        params[key.upper()] = parameter
    if params.get("ENCODING", "").lower() in {"b", "base64", "quoted-printable"}:
        raise ConnectorContractError(f"portable_{label}_encoding_unsupported")
    value = (
        value.replace("\\n", "\n").replace("\\N", "\n")
        .replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
    )
    return name, params, _bounded(value, f"{label}_value")


def _components(lines: list[str], kind: str, label: str) -> list[list[str]]:
    result = []
    current = None
    for line in lines:
        upper = line.upper()
        if upper == f"BEGIN:{kind}":
            if current is not None:
                raise ConnectorContractError(f"portable_{label}_format_invalid")
            current = []
        elif upper == f"END:{kind}":
            if current is None:
                raise ConnectorContractError(f"portable_{label}_format_invalid")
            result.append(current)
            current = None
        elif current is not None:
            current.append(line)
    if current is not None or not result:
        raise ConnectorContractError(f"portable_{label}_format_invalid")
    return result


def _ical_time(value: str) -> tuple[str, bool]:
    try:
        if re.fullmatch(r"\d{8}", value):
            parsed = datetime.strptime(value, "%Y%m%d")
            return parsed.date().isoformat(), True
        if re.fullmatch(r"\d{8}T\d{6}Z", value):
            parsed = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc
            )
            return parsed.isoformat().replace("+00:00", "Z"), False
        if re.fullmatch(r"\d{8}T\d{6}", value):
            parsed = datetime.strptime(value, "%Y%m%dT%H%M%S")
            return parsed.isoformat(), False
    except ValueError:
        pass
    raise ConnectorContractError("portable_calendar_time_invalid")


def _strip_mailto(value: str) -> str:
    return value[7:] if value.lower().startswith("mailto:") else value


class CalendarImportConnector(_SnapshotImport):
    connector_id = "portable.calendar"
    native_prefix = "ical:"
    record_kind = "calendar_event.v1"

    def _live_records(self) -> list[ConnectorRecordV2]:
        lines = _unfold(
            read_stable_file(self.path, maximum_bytes=MAX_FILE_BYTES), "calendar"
        )
        if "BEGIN:VCALENDAR" not in (line.upper() for line in lines):
            raise ConnectorContractError("portable_calendar_format_invalid")
        records = {}
        for component in _components(lines, "VEVENT", "calendar"):
            values: dict[str, list[tuple[dict[str, str], str]]] = {}
            for line in component:
                name, params, value = _property(line, "calendar")
                values.setdefault(name, []).append((params, value))
            try:
                uid = values["UID"][0][1]
                start_raw = values["DTSTART"][0][1]
            except (KeyError, IndexError):
                raise ConnectorContractError("portable_calendar_format_invalid") from None
            recurrence = values.get("RECURRENCE-ID", [({}, "")])[0][1]
            native_id = "ical:" + hashlib.sha256(
                f"{self.archive_id}\0{uid}\0{recurrence}".encode()
            ).hexdigest()
            status = values.get("STATUS", [({}, "")])[0][1].upper()
            occurred_raw = values.get(
                "LAST-MODIFIED", values.get("DTSTAMP", [({}, "")])
            )[0][1]
            occurred_at = _ical_time(occurred_raw)[0] if occurred_raw else EPOCH
            if len(occurred_at) == 10:
                occurred_at += "T00:00:00Z"
            if not occurred_at.endswith("Z") and "+" not in occurred_at[-6:]:
                occurred_at += "+00:00"
            if status == "CANCELLED":
                records[native_id] = ConnectorRecordV2(
                    schema_version=2,
                    native_id=native_id,
                    occurred_at=occurred_at,
                    content={"kind": self.record_kind},
                    provenance={"uri": f"connector://portable-calendar/{native_id}"},
                    deleted=True,
                )
                continue
            start, all_day = _ical_time(start_raw)
            end = _ical_time(values.get("DTEND", [({}, start_raw)])[0][1])[0]
            title = values.get("SUMMARY", [({}, "(untitled)")])[0][1]
            content: dict[str, Any] = {
                "kind": self.record_kind,
                "calendar_id": self.archive_id,
                "event_id": uid,
                "start": start,
                "end": end,
                "title": title,
                "all_day": all_day,
                "surface": "portable_calendar",
            }
            optional = {
                "description": "DESCRIPTION",
                "location": "LOCATION",
                "organizer_id": "ORGANIZER",
                "status": "STATUS",
            }
            for field, name in optional.items():
                if values.get(name):
                    content[field] = _strip_mailto(values[name][0][1])
            attendees = [
                _strip_mailto(value)
                for _params, value in values.get("ATTENDEE", [])
            ]
            if attendees:
                content["attendee_ids"] = sorted(set(attendees))
            if recurrence:
                content["instance_id"] = recurrence
                content["series_id"] = uid
            records[native_id] = ConnectorRecordV2(
                schema_version=2,
                native_id=native_id,
                occurred_at=occurred_at,
                content=content,
                provenance={"uri": f"connector://portable-calendar/{native_id}"},
            )
        return [records[key] for key in sorted(records)]


class ContactImportConnector(_SnapshotImport):
    connector_id = "portable.contacts"
    native_prefix = "vcard:"
    record_kind = "contact_identity.v1"

    def _live_records(self) -> list[ConnectorRecordV2]:
        lines = _unfold(
            read_stable_file(self.path, maximum_bytes=MAX_FILE_BYTES), "contacts"
        )
        records = {}
        identity_ordinals: defaultdict[str, int] = defaultdict(int)
        for component in _components(lines, "VCARD", "contacts"):
            values: dict[str, list[str]] = {}
            for line in component:
                name, _params, value = _property(line, "contacts")
                values.setdefault(name, []).append(value)
            version = values.get("VERSION", [""])[0]
            if version not in {"3.0", "4.0"}:
                raise ConnectorContractError("portable_contacts_version_unsupported")
            emails = [item.lower() for item in values.get("EMAIL", []) if item]
            phones = [item for item in values.get("TEL", []) if item]
            display = values.get("FN", [""])[0]
            identity = values.get("UID", [""])[0] or (
                emails[0] if emails else phones[0] if phones else display
            )
            if not identity or not display:
                raise ConnectorContractError("portable_contacts_format_invalid")
            identity_ordinal = identity_ordinals[identity]
            identity_ordinals[identity] += 1
            identity_key = (
                identity if values.get("UID") else f"{identity}\0{identity_ordinal}"
            )
            native_id = "vcard:" + hashlib.sha256(
                f"{self.archive_id}\0{identity_key}".encode()
            ).hexdigest()
            identifier = emails[0] if emails else phones[0] if phones else None
            content: dict[str, Any] = {
                "kind": self.record_kind,
                "identity_id": identity,
                "identifier_type": (
                    "email" if emails else "phone" if phones else "vcard"
                ),
                "display_name": display,
                "role": (
                    "self"
                    if set(emails + phones) & set(self.owner_identifiers)
                    else "other"
                ),
                "surface": "portable_contacts",
            }
            if identifier:
                content["identifier"] = identifier
            if values.get("ORG"):
                content["organization"] = values["ORG"][0]
            if values.get("TITLE"):
                content["title"] = values["TITLE"][0]
            text = sorted(set(emails + phones))
            if values.get("NOTE"):
                text.extend(values["NOTE"])
            if text:
                content["text"] = "\n".join(text)
            records[native_id] = ConnectorRecordV2(
                schema_version=2,
                native_id=native_id,
                occurred_at=EPOCH,
                content=content,
                provenance={"uri": f"connector://portable-contacts/{native_id}"},
            )
        return [records[key] for key in sorted(records)]


__all__ = [
    "CalendarImportConnector",
    "ContactImportConnector",
    "MailImportConnector",
]
