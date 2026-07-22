"""Typed Google Workspace pull connectors over the pinned read-only CLI rail."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import Message
from email.utils import getaddresses
from html.parser import HTMLParser
from typing import Any, Mapping, Protocol

from connectors.sdk import (
    ConnectorContractError,
    ConnectorPage,
    ConnectorRateLimited,
    ConnectorRecordV2,
    ConnectorUpstreamError,
    SOURCE_ID,
)
from connectors.workspace_rail import WorkspaceRailError


MAX_TEXT_BYTES = 500_000
MAX_GMAIL_PART_BYTES = 750_000
MAX_SELECTOR_BYTES = 4_096
MAX_ITEMS = 500
EPOCH = "1970-01-01T00:00:00Z"
GOOGLE_DOCUMENT_MIME = "application/vnd.google-apps.document"
MIME_TYPE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}/"
    r"[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]{0,126}\Z"
)


class WorkspaceClient(Protocol):
    def run(self, operation: str, params: Mapping[str, Any]) -> Any: ...

    def export_document(self, *, file_id: str, mime_type: str = "text/plain") -> bytes: ...


def _source(value: str) -> str:
    if not isinstance(value, str) or not SOURCE_ID.fullmatch(value):
        raise ConnectorContractError("source_id is invalid")
    return value


def _bounded_string(value: Any, label: str, *, maximum: int = MAX_SELECTOR_BYTES) -> str:
    if not isinstance(value, str) or not value or len(value.encode()) > maximum:
        raise ConnectorContractError(f"{label} is invalid")
    return value


def _optional_string(
    value: Any,
    label: str,
    *,
    maximum: int = MAX_SELECTOR_BYTES,
) -> str | None:
    if value is None:
        return None
    return _bounded_string(value, label, maximum=maximum)


def _decimal_string(value: Any, label: str) -> str:
    result = _bounded_string(value, label)
    if not result.isascii() or not result.isdigit():
        raise ConnectorContractError(f"{label} is invalid")
    return result


def _closed_strings(
    value: tuple[str, ...],
    label: str,
    *,
    maximum_items: int = 64,
) -> tuple[str, ...]:
    if (
        not isinstance(value, tuple)
        or len(value) > maximum_items
        or len(value) != len(set(value))
        or value != tuple(sorted(value))
    ):
        raise ConnectorContractError(f"{label} is invalid")
    for item in value:
        _bounded_string(item, label)
    return value


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConnectorContractError(f"{label} is invalid")
    return value


def _items(value: Any, label: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_ITEMS:
        raise ConnectorContractError(f"{label} is invalid")
    return value


def _cursor(value: Mapping[str, Any]) -> str:
    try:
        rendered = json.dumps(
            dict(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise ConnectorContractError("connector cursor is invalid") from None
    if not rendered or len(rendered.encode()) > 4_096:
        raise ConnectorContractError("connector cursor is invalid")
    return rendered


def _parse_cursor(raw: str, shapes: Mapping[str, set[str]]) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw or len(raw.encode()) > 4_096:
        raise ConnectorContractError("connector cursor is invalid")
    try:
        value = json.loads(
            raw,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ConnectorContractError("connector cursor is invalid") from None
    if not isinstance(value, dict) or value.get("v") != 1:
        raise ConnectorContractError("connector cursor is invalid")
    phase = value.get("phase")
    expected = shapes.get(phase)
    if expected is None or set(value) != expected:
        raise ConnectorContractError("connector cursor is invalid")
    for key, item in value.items():
        if key not in {"v", "phase"} and item is not None:
            _bounded_string(item, "connector cursor field")
    return value


def _timestamp(value: Any, *, fallback: str = EPOCH) -> str:
    if not isinstance(value, str) or not value:
        return fallback
    candidate = value
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return fallback
    if parsed.tzinfo is None:
        return fallback
    return candidate


def _strict_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConnectorContractError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ConnectorContractError(f"{label} is invalid") from None
    if parsed.tzinfo is None:
        raise ConnectorContractError(f"{label} is invalid")
    return value


def _millisecond_timestamp(value: Any) -> str:
    try:
        milliseconds = int(value)
        parsed = datetime.fromtimestamp(milliseconds / 1000, timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return EPOCH
    return parsed.isoformat().replace("+00:00", "Z")


def _text(value: Any, *, fallback: str = "") -> str:
    if not isinstance(value, str):
        value = fallback
    encoded = value.encode(errors="replace")
    if len(encoded) > MAX_TEXT_BYTES:
        encoded = encoded[:MAX_TEXT_BYTES]
        value = encoded.decode(errors="ignore")
    return value


def _translate(error: WorkspaceRailError) -> None:
    if error.code == "rate_limited":
        raise ConnectorRateLimited(retry_after_seconds=60) from None
    code = {
        "authority_revoked": "connector_authority_revoked",
        "authority_forbidden": "connector_authority_forbidden",
        "response_schema_drift": "connector_schema_drift",
    }.get(error.code, "connector_upstream_error")
    raise ConnectorUpstreamError(code) from None


def _record(
    *,
    native_id: str,
    occurred_at: str,
    kind: str,
    content: dict[str, Any] | None = None,
    parent: str | None = None,
    deleted: bool = False,
    provenance_uri: str,
) -> ConnectorRecordV2:
    return ConnectorRecordV2.from_mapping({
        "schema_version": 2,
        "native_id": native_id,
        "native_parent_id": parent,
        "occurred_at": occurred_at,
        "content": {"kind": kind} if deleted else {"kind": kind, **(content or {})},
        "provenance": {"uri": provenance_uri},
        "deleted": deleted,
    })


def _header_values(payload: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    headers = _items(payload.get("headers"), "gmail headers")
    for raw in headers:
        item = _mapping(raw, "gmail header")
        name = item.get("name")
        value = item.get("value")
        if isinstance(name, str) and isinstance(value, str):
            result.setdefault(name.lower(), _text(value, fallback=""))
    return result


@dataclass(frozen=True)
class _GmailPartContent:
    text: str = ""
    format: str | None = None
    quality: int = 0
    omissions: tuple[str, ...] = ()
    attachments: tuple[dict[str, Any], ...] = ()


class _HTMLText(HTMLParser):
    _BLOCKS = {
        "address", "article", "aside", "blockquote", "br", "div", "dl",
        "dt", "dd", "fieldset", "figcaption", "figure", "footer", "form",
        "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li",
        "main", "nav", "ol", "p", "pre", "section", "table", "tr", "ul",
    }
    _HIDDEN = {"head", "script", "style", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if tag in self._HIDDEN:
            self.hidden_depth += 1
        elif not self.hidden_depth and tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in self._HIDDEN:
            self.hidden_depth = max(0, self.hidden_depth - 1)
        elif not self.hidden_depth and tag in self._BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


def _html_text(value: str) -> str:
    parser = _HTMLText()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, AssertionError):
        return ""
    lines = []
    for raw in "".join(parser.parts).splitlines():
        line = re.sub(r"[\t\f\v ]+", " ", raw).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def _bound_gmail_text(value: str) -> tuple[str, bool]:
    """Fit one body to the connector record/page budget without silent loss."""
    truncated = len(value.encode(errors="replace")) > MAX_TEXT_BYTES
    candidate = _text(value)
    if len(json.dumps(candidate).encode()) <= MAX_TEXT_BYTES:
        return candidate, truncated
    truncated = True
    low, high = 0, len(candidate)
    while low < high:
        midpoint = (low + high + 1) // 2
        if len(json.dumps(candidate[:midpoint]).encode()) <= MAX_TEXT_BYTES:
            low = midpoint
        else:
            high = midpoint - 1
    return candidate[:low], truncated


def _gmail_charset(part: Mapping[str, Any]) -> str:
    headers = _header_values(part)
    value = headers.get("content-type")
    if not value:
        return "utf-8"
    message = Message()
    message["content-type"] = value
    return message.get_content_charset() or "utf-8"


def _decode_gmail_data(value: Any) -> tuple[bytes | None, tuple[str, ...]]:
    if not isinstance(value, str) or not value:
        return None, ("body_part_unavailable",)
    try:
        encoded = value.encode("ascii")
        padded = encoded + b"=" * (-len(encoded) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError, base64.binascii.Error):
        return None, ("body_part_invalid",)
    if len(decoded) > MAX_GMAIL_PART_BYTES:
        return decoded[:MAX_GMAIL_PART_BYTES], ("body_truncated",)
    return decoded, ()


def _attachment_metadata(part: Mapping[str, Any]) -> dict[str, Any]:
    body = part.get("body")
    body = body if isinstance(body, dict) else {}
    value: dict[str, Any] = {
        "mime_type": _text(part.get("mimeType"), fallback="application/octet-stream"),
    }
    filename = part.get("filename")
    if isinstance(filename, str) and filename:
        value["name"] = _text(filename)
    size = body.get("size")
    if type(size) is int and size >= 0:
        value["size_bytes"] = size
    return value


def _is_file_attachment(part: Mapping[str, Any]) -> bool:
    filename = part.get("filename")
    if isinstance(filename, str) and filename:
        return True
    disposition = _header_values(part).get("content-disposition", "")
    return disposition.casefold().lstrip().startswith("attachment")


def _gmail_part_content(
    rail: WorkspaceClient,
    message_id: str,
    value: Any,
    *,
    visited: list[int],
) -> _GmailPartContent:
    part = _mapping(value, "gmail message part")
    visited[0] += 1
    if visited[0] > 256:
        return _GmailPartContent(omissions=("body_part_limit",))
    if _is_file_attachment(part):
        return _GmailPartContent(
            omissions=("file_attachments",),
            attachments=(_attachment_metadata(part),),
        )

    mime_type = part.get("mimeType")
    mime_type = mime_type.casefold() if isinstance(mime_type, str) else ""
    children = _items(part.get("parts"), "gmail message parts")
    if mime_type.startswith("multipart/") or children:
        candidates = [
            _gmail_part_content(rail, message_id, child, visited=visited)
            for child in children
        ]
        attachments = tuple(
            attachment for candidate in candidates for attachment in candidate.attachments
        )
        attachment_omissions = {
            omission
            for candidate in candidates
            for omission in candidate.omissions
            if omission == "file_attachments"
        }
        if mime_type == "multipart/alternative":
            selected = max(candidates, key=lambda item: item.quality, default=_GmailPartContent())
            return _GmailPartContent(
                text=selected.text,
                format=selected.format,
                quality=selected.quality,
                omissions=tuple(sorted(set(selected.omissions) | attachment_omissions)),
                attachments=attachments,
            )
        text_parts = [candidate.text for candidate in candidates if candidate.text]
        formats = {candidate.format for candidate in candidates if candidate.text}
        omissions = {
            omission for candidate in candidates for omission in candidate.omissions
        }
        combined, truncated = _bound_gmail_text("\n\n".join(text_parts))
        if truncated:
            omissions.add("body_truncated")
        return _GmailPartContent(
            text=combined,
            format=(formats.pop() if len(formats) == 1 else "multipart") if text_parts else None,
            quality=max((candidate.quality for candidate in candidates), default=0),
            omissions=tuple(sorted(omissions)),
            attachments=attachments,
        )

    if mime_type not in {"text/plain", "text/html"}:
        return _GmailPartContent(omissions=("non_text_body_parts",))
    body = part.get("body")
    body = body if isinstance(body, dict) else {}
    data = body.get("data")
    expected_size = body.get("size") if type(body.get("size")) is int else None
    if not data and isinstance(body.get("attachmentId"), str):
        try:
            fetched = rail.run("gmail.messages.attachments.get", {
                "userId": "me",
                "messageId": message_id,
                "id": body["attachmentId"],
            })
        except WorkspaceRailError:
            return _GmailPartContent(omissions=("body_part_unavailable",))
        fetched = _mapping(fetched, "gmail body part")
        size = fetched.get("size")
        if type(size) is not int or size < 0:
            return _GmailPartContent(omissions=("body_part_invalid",))
        expected_size = size
        data = fetched.get("data")
    raw, omissions = _decode_gmail_data(data)
    if raw is None:
        return _GmailPartContent(omissions=omissions)
    if (
        expected_size is not None
        and expected_size <= MAX_GMAIL_PART_BYTES
        and len(raw) != expected_size
    ):
        omissions = tuple(sorted(set(omissions) | {"body_size_mismatch"}))
    try:
        decoded = raw.decode(_gmail_charset(part), errors="replace")
    except LookupError:
        decoded = raw.decode("utf-8", errors="replace")
        omissions = tuple(sorted(set(omissions) | {"charset_fallback"}))
    rendered = decoded if mime_type == "text/plain" else _html_text(decoded)
    text, truncated = _bound_gmail_text(rendered)
    if truncated:
        omissions = tuple(sorted(set(omissions) | {"body_truncated"}))
    if not text:
        return _GmailPartContent(omissions=tuple(sorted(set(omissions) | {"body_part_empty"})))
    return _GmailPartContent(
        text=text,
        format="text/plain" if mime_type == "text/plain" else "text/html-derived",
        quality=2 if mime_type == "text/plain" else 1,
        omissions=omissions,
    )


def _gmail_content(
    rail: WorkspaceClient,
    message_id: str,
    payload: Mapping[str, Any],
    snippet: Any,
) -> _GmailPartContent:
    result = _gmail_part_content(rail, message_id, payload, visited=[0])
    if result.text:
        return result
    fallback = _text(snippet)
    omission = "snippet_fallback" if fallback else "body_unavailable"
    return _GmailPartContent(
        text=fallback,
        format="snippet" if fallback else None,
        omissions=tuple(sorted(set(result.omissions) | {omission})),
        attachments=result.attachments,
    )


def _addresses(*values: str) -> tuple[str, ...]:
    result = {
        address.lower()
        for _name, address in getaddresses(values)
        if isinstance(address, str) and address and len(address.encode()) <= 320
    }
    return tuple(sorted(result))


class GmailConnector:
    connector_id = "google.gmail"

    def __init__(
        self,
        *,
        rail: WorkspaceClient,
        source_id: str,
        own_addresses: tuple[str, ...] = (),
        label_ids: tuple[str, ...] = (),
        query: str | None = None,
        include_spam_trash: bool = False,
        page_size: int = 100,
    ):
        if not callable(getattr(rail, "run", None)):
            raise ConnectorContractError("workspace rail is invalid")
        self.rail = rail
        self.source_id = _source(source_id)
        self.own_addresses = tuple(
            item.lower() for item in _closed_strings(own_addresses, "own addresses")
        )
        self.label_ids = _closed_strings(label_ids, "label ids")
        self.query = _optional_string(query, "gmail query")
        if not isinstance(include_spam_trash, bool):
            raise ConnectorContractError("include_spam_trash is invalid")
        self.include_spam_trash = include_spam_trash
        if type(page_size) is not int or not 1 <= page_size <= 500:
            raise ConnectorContractError("page_size is invalid")
        self.page_size = page_size

    def _message(self, value: Any) -> tuple[ConnectorRecordV2, str]:
        message = _mapping(value, "gmail message")
        message_id = _bounded_string(message.get("id"), "gmail message id")
        thread_id = _bounded_string(message.get("threadId"), "gmail thread id")
        history_id = _decimal_string(message.get("historyId"), "gmail history id")
        payload = _mapping(message.get("payload"), "gmail payload")
        headers = _header_values(payload)
        senders = _addresses(headers.get("from", ""))
        recipients = _addresses(
            headers.get("to", ""),
            headers.get("cc", ""),
            headers.get("bcc", ""),
        )
        participants = tuple(sorted(set(senders) | set(recipients)))
        direction = "outbound" if set(senders) & set(self.own_addresses) else "inbound"
        body = _gmail_content(self.rail, message_id, payload, message.get("snippet"))
        content: dict[str, Any] = {
            "conversation_id": f"gmail-thread:{thread_id}",
            "message_id": f"gmail:{message_id}",
            "direction": direction,
            "text": body.text,
            "content_fidelity": "partial" if body.omissions else "complete",
            "surface": "gmail",
        }
        if body.format:
            content["format"] = body.format
        if body.omissions:
            content["content_omissions"] = list(body.omissions)
        if body.attachments:
            content["attachments"] = list(body.attachments)
        if participants:
            content["participant_ids"] = list(participants)
        if senders:
            content["author_id"] = senders[0]
        subject = headers.get("subject")
        if subject:
            content["subject"] = _text(subject)
        occurred_at = _millisecond_timestamp(message.get("internalDate"))
        content["sent_at" if direction == "outbound" else "received_at"] = occurred_at
        return _record(
            native_id=f"gmail:{message_id}",
            parent=f"gmail-thread:{thread_id}",
            occurred_at=occurred_at,
            kind="communication_message.v1",
            content=content,
            provenance_uri="connector://google-gmail",
        ), history_id

    def _get(self, message_id: str) -> tuple[ConnectorRecordV2, str]:
        value = self.rail.run("gmail.messages.get", {
            "userId": "me",
            "id": message_id,
            "format": "full",
        })
        return self._message(value)

    def _list_params(self, page: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "userId": "me",
            "maxResults": self.page_size,
            "includeSpamTrash": self.include_spam_trash,
        }
        if page:
            params["pageToken"] = page
        if self.query:
            params["q"] = self.query
        if self.label_ids:
            params["labelIds"] = list(self.label_ids)
        return params

    def _full(self, state: Mapping[str, Any] | None = None) -> ConnectorPage:
        page_token = state.get("page") if state else None
        prior_history = state.get("history") if state else None
        response = _mapping(
            self.rail.run("gmail.messages.list", self._list_params(page_token)),
            "gmail list response",
        )
        raw_messages = _items(response.get("messages"), "gmail message list")
        records = []
        history_ids = [prior_history] if isinstance(prior_history, str) else []
        for raw in raw_messages:
            summary = _mapping(raw, "gmail message summary")
            message_id = _bounded_string(summary.get("id"), "gmail message id")
            record, history_id = self._get(message_id)
            records.append(record)
            history_ids.append(history_id)
        next_page = _optional_string(response.get("nextPageToken"), "gmail page token")
        latest = max(history_ids, key=lambda item: int(item)) if history_ids else None
        if next_page:
            next_cursor = _cursor({
                "v": 1,
                "phase": "full",
                "page": next_page,
                "history": latest,
            })
            return ConnectorPage(records=tuple(records), next_cursor=next_cursor, has_more=True)
        if latest is None:
            profile = _mapping(
                self.rail.run("gmail.users.getProfile", {"userId": "me"}),
                "gmail profile",
            )
            latest = _decimal_string(profile.get("historyId"), "gmail history id")
        next_cursor = _cursor({
            "v": 1,
            "phase": "incremental",
            "page": None,
            "history": latest,
        })
        return ConnectorPage(records=tuple(records), next_cursor=next_cursor, has_more=False)

    def _incremental(self, state: Mapping[str, Any]) -> ConnectorPage:
        params: dict[str, Any] = {
            "userId": "me",
            "startHistoryId": state["history"],
            "maxResults": self.page_size,
        }
        if state["page"]:
            params["pageToken"] = state["page"]
        if self.label_ids:
            params["labelId"] = self.label_ids[0]
        response = _mapping(
            self.rail.run("gmail.history.list", params),
            "gmail history response",
        )
        changed: dict[str, str | None] = {}
        deleted: dict[str, str | None] = {}
        for raw_history in _items(response.get("history"), "gmail history"):
            history = _mapping(raw_history, "gmail history item")
            for bucket in ("messages", "messagesAdded", "labelsAdded", "labelsRemoved"):
                for raw_change in _items(history.get(bucket), "gmail history changes"):
                    change = _mapping(raw_change, "gmail history change")
                    message = change.get("message", change)
                    message = _mapping(message, "gmail history message")
                    message_id = _bounded_string(message.get("id"), "gmail message id")
                    changed[message_id] = message.get("threadId")
            for raw_change in _items(history.get("messagesDeleted"), "gmail deletions"):
                change = _mapping(raw_change, "gmail deletion")
                message = _mapping(change.get("message"), "gmail deleted message")
                message_id = _bounded_string(message.get("id"), "gmail message id")
                deleted[message_id] = message.get("threadId")
        records = []
        for message_id in sorted(deleted):
            thread_id = deleted[message_id]
            parent = (
                f"gmail-thread:{_bounded_string(thread_id, 'gmail thread id')}"
                if thread_id is not None
                else None
            )
            records.append(_record(
                native_id=f"gmail:{message_id}",
                parent=parent,
                occurred_at=EPOCH,
                kind="communication_message.v1",
                deleted=True,
                provenance_uri="connector://google-gmail",
            ))
        for message_id in sorted(set(changed) - set(deleted)):
            record, _history_id = self._get(message_id)
            records.append(record)
        next_page = _optional_string(response.get("nextPageToken"), "gmail page token")
        if next_page:
            next_cursor = _cursor({
                "v": 1,
                "phase": "incremental",
                "page": next_page,
                "history": state["history"],
            })
            return ConnectorPage(records=tuple(records), next_cursor=next_cursor, has_more=True)
        history_id = _decimal_string(response.get("historyId"), "gmail history id")
        return ConnectorPage(
            records=tuple(records),
            next_cursor=_cursor({
                "v": 1,
                "phase": "incremental",
                "page": None,
                "history": history_id,
            }),
            has_more=False,
        )

    def pull(self, cursor: str | None) -> ConnectorPage:
        if cursor is None:
            try:
                return self._full()
            except WorkspaceRailError as error:
                _translate(error)
        state = _parse_cursor(cursor, {
            "full": {"v", "phase", "page", "history"},
            "incremental": {"v", "phase", "page", "history"},
        })
        if state["phase"] == "full":
            if state["page"] is None:
                raise ConnectorContractError("connector cursor is invalid")
            if state["history"] is not None:
                _decimal_string(state["history"], "gmail history id")
        else:
            _decimal_string(state["history"], "gmail history id")
        try:
            return self._full(state) if state["phase"] == "full" else self._incremental(state)
        except WorkspaceRailError as error:
            if state["phase"] == "incremental" and error.code == "not_found":
                try:
                    return self._full()
                except WorkspaceRailError as reset_error:
                    _translate(reset_error)
            _translate(error)
        raise AssertionError("unreachable")


def _calendar_time(value: Any) -> tuple[str, bool]:
    item = _mapping(value, "calendar time")
    if isinstance(item.get("dateTime"), str):
        return _strict_timestamp(item["dateTime"], "calendar time"), False
    day = _bounded_string(item.get("date"), "calendar date")
    try:
        parsed = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        raise ConnectorContractError("calendar date is invalid") from None
    return parsed.isoformat().replace("+00:00", "Z"), True


class GoogleCalendarConnector:
    connector_id = "google.calendar"

    def __init__(
        self,
        *,
        rail: WorkspaceClient,
        source_id: str,
        calendar_id: str = "primary",
        time_min: str | None = None,
        time_max: str | None = None,
        page_size: int = 100,
    ):
        if not callable(getattr(rail, "run", None)):
            raise ConnectorContractError("workspace rail is invalid")
        self.rail = rail
        self.source_id = _source(source_id)
        self.calendar_id = _bounded_string(calendar_id, "calendar_id")
        self.time_min = _optional_string(time_min, "time_min")
        self.time_max = _optional_string(time_max, "time_max")
        if type(page_size) is not int or not 1 <= page_size <= 250:
            raise ConnectorContractError("page_size is invalid")
        self.page_size = page_size

    def _event(self, raw: Any) -> ConnectorRecordV2:
        event = _mapping(raw, "calendar event")
        event_id = _bounded_string(event.get("id"), "calendar event id")
        occurred_at = _timestamp(event.get("updated"), fallback=EPOCH)
        native_id = f"gcal:{self.calendar_id}:{event_id}"
        if event.get("status") == "cancelled":
            return _record(
                native_id=native_id,
                occurred_at=occurred_at,
                kind="calendar_event.v1",
                deleted=True,
                provenance_uri="connector://google-calendar",
            )
        start, start_all_day = _calendar_time(event.get("start"))
        end, end_all_day = _calendar_time(event.get("end"))
        content: dict[str, Any] = {
            "calendar_id": self.calendar_id,
            "event_id": event_id,
            "start": start,
            "end": end,
            "title": _text(event.get("summary") or "(untitled)"),
            "all_day": start_all_day and end_all_day,
            "status": _text(event.get("status"), fallback="confirmed"),
            "surface": "google_calendar",
        }
        optional_strings = {
            "description": event.get("description"),
            "location": event.get("location"),
            "source_url": event.get("htmlLink"),
            "series_id": event.get("recurringEventId"),
        }
        for field, value in optional_strings.items():
            if isinstance(value, str) and value:
                content[field] = _text(value)
        organizer = event.get("organizer")
        if isinstance(organizer, dict) and isinstance(organizer.get("email"), str):
            content["organizer_id"] = _text(organizer["email"])
        attendees = []
        for raw_attendee in _items(event.get("attendees"), "calendar attendees"):
            attendee = _mapping(raw_attendee, "calendar attendee")
            if isinstance(attendee.get("email"), str):
                attendees.append(_text(attendee["email"]))
        if attendees:
            content["attendee_ids"] = sorted(set(attendees))
        conference = event.get("hangoutLink")
        if isinstance(conference, str) and conference:
            content["conference_url"] = _text(conference)
        return _record(
            native_id=native_id,
            occurred_at=occurred_at,
            kind="calendar_event.v1",
            content=content,
            provenance_uri="connector://google-calendar",
        )

    def _page(self, state: Mapping[str, Any] | None) -> ConnectorPage:
        phase = state["phase"] if state else "full"
        params: dict[str, Any] = {
            "calendarId": self.calendar_id,
            "maxResults": self.page_size,
            "showDeleted": True,
            "singleEvents": True,
        }
        if state and state["page"]:
            params["pageToken"] = state["page"]
        if phase == "incremental":
            params["syncToken"] = state["sync"]
        else:
            if self.time_min:
                params["timeMin"] = self.time_min
            if self.time_max:
                params["timeMax"] = self.time_max
        response = _mapping(
            self.rail.run("calendar.events.list", params),
            "calendar response",
        )
        records = tuple(
            self._event(item)
            for item in _items(response.get("items"), "calendar events")
        )
        next_page = _optional_string(response.get("nextPageToken"), "calendar page token")
        if next_page:
            return ConnectorPage(
                records=records,
                next_cursor=_cursor({
                    "v": 1,
                    "phase": phase,
                    "page": next_page,
                    "sync": state.get("sync") if state else None,
                }),
                has_more=True,
            )
        sync = _bounded_string(response.get("nextSyncToken"), "calendar sync token")
        return ConnectorPage(
            records=records,
            next_cursor=_cursor({
                "v": 1,
                "phase": "incremental",
                "page": None,
                "sync": sync,
            }),
            has_more=False,
        )

    def pull(self, cursor: str | None) -> ConnectorPage:
        state = None if cursor is None else _parse_cursor(cursor, {
            "full": {"v", "phase", "page", "sync"},
            "incremental": {"v", "phase", "page", "sync"},
        })
        if state is not None and (
            (state["phase"] == "full" and (state["page"] is None or state["sync"] is not None))
            or (state["phase"] == "incremental" and state["sync"] is None)
        ):
            raise ConnectorContractError("connector cursor is invalid")
        try:
            return self._page(state)
        except WorkspaceRailError as error:
            if state is not None and error.code == "cursor_expired":
                try:
                    return self._page(None)
                except WorkspaceRailError as reset_error:
                    _translate(reset_error)
            _translate(error)
        raise AssertionError("unreachable")


class GoogleContactsConnector:
    connector_id = "google.contacts"

    def __init__(
        self,
        *,
        rail: WorkspaceClient,
        source_id: str,
        page_size: int = 100,
    ):
        if not callable(getattr(rail, "run", None)):
            raise ConnectorContractError("workspace rail is invalid")
        self.rail = rail
        self.source_id = _source(source_id)
        if type(page_size) is not int or not 1 <= page_size <= 500:
            raise ConnectorContractError("page_size is invalid")
        self.page_size = page_size

    def _person(self, raw: Any) -> ConnectorRecordV2:
        person = _mapping(raw, "google contact")
        identity_id = _bounded_string(person.get("resourceName"), "contact identity id")
        metadata = _mapping(person.get("metadata"), "contact metadata")
        occurred_at = EPOCH
        for raw_source in _items(metadata.get("sources"), "contact metadata sources"):
            source = _mapping(raw_source, "contact metadata source")
            candidate = _timestamp(source.get("updateTime"), fallback=EPOCH)
            if candidate > occurred_at:
                occurred_at = candidate
        native_id = f"gcontact:{identity_id}"
        if metadata.get("deleted") is True:
            return _record(
                native_id=native_id,
                occurred_at=occurred_at,
                kind="contact_identity.v1",
                deleted=True,
                provenance_uri="connector://google-contacts",
            )
        names = [
            _mapping(item, "contact name")
            for item in _items(person.get("names"), "contact names")
        ]
        emails = [
            _mapping(item, "contact email")
            for item in _items(person.get("emailAddresses"), "contact emails")
        ]
        phones = [
            _mapping(item, "contact phone")
            for item in _items(person.get("phoneNumbers"), "contact phones")
        ]
        primary_email = next(
            (_text(item["value"]) for item in emails if isinstance(item.get("value"), str)),
            None,
        )
        primary_phone = next(
            (_text(item["value"]) for item in phones if isinstance(item.get("value"), str)),
            None,
        )
        display_name = next(
            (_text(item["displayName"]) for item in names if isinstance(item.get("displayName"), str)),
            primary_email or primary_phone or identity_id,
        )
        identifier = primary_email or primary_phone
        identifier_type = "email" if primary_email else "phone" if primary_phone else "google_person"
        content: dict[str, Any] = {
            "identity_id": identity_id,
            "identifier_type": identifier_type,
            "display_name": display_name,
            "surface": "google_contacts",
        }
        if identifier:
            content["identifier"] = identifier
        organizations = _items(person.get("organizations"), "contact organizations")
        if organizations:
            organization = _mapping(organizations[0], "contact organization")
            if isinstance(organization.get("name"), str):
                content["organization"] = _text(organization["name"])
            if isinstance(organization.get("title"), str):
                content["title"] = _text(organization["title"])
        all_identifiers = [
            _text(item["value"])
            for item in (*emails, *phones)
            if isinstance(item.get("value"), str)
        ]
        if all_identifiers:
            content["text"] = "\n".join(sorted(set(all_identifiers)))
        return _record(
            native_id=native_id,
            occurred_at=occurred_at,
            kind="contact_identity.v1",
            content=content,
            provenance_uri="connector://google-contacts",
        )

    def _page(self, state: Mapping[str, Any] | None) -> ConnectorPage:
        phase = state["phase"] if state else "full"
        params: dict[str, Any] = {
            "resourceName": "people/me",
            "pageSize": self.page_size,
            "personFields": "metadata,names,emailAddresses,phoneNumbers,organizations",
        }
        if state and state["page"]:
            params["pageToken"] = state["page"]
        if phase == "incremental":
            params["syncToken"] = state["sync"]
        else:
            params["requestSyncToken"] = True
        response = _mapping(
            self.rail.run("people.people.connections.list", params),
            "contacts response",
        )
        records = tuple(
            self._person(item)
            for item in _items(response.get("connections"), "contacts")
        )
        next_page = _optional_string(response.get("nextPageToken"), "contacts page token")
        if next_page:
            return ConnectorPage(
                records=records,
                next_cursor=_cursor({
                    "v": 1,
                    "phase": phase,
                    "page": next_page,
                    "sync": state.get("sync") if state else None,
                }),
                has_more=True,
            )
        sync = _bounded_string(response.get("nextSyncToken"), "contacts sync token")
        return ConnectorPage(
            records=records,
            next_cursor=_cursor({
                "v": 1,
                "phase": "incremental",
                "page": None,
                "sync": sync,
            }),
            has_more=False,
        )

    def pull(self, cursor: str | None) -> ConnectorPage:
        state = None if cursor is None else _parse_cursor(cursor, {
            "full": {"v", "phase", "page", "sync"},
            "incremental": {"v", "phase", "page", "sync"},
        })
        if state is not None and (
            (state["phase"] == "full" and (state["page"] is None or state["sync"] is not None))
            or (state["phase"] == "incremental" and state["sync"] is None)
        ):
            raise ConnectorContractError("connector cursor is invalid")
        try:
            return self._page(state)
        except WorkspaceRailError as error:
            if state is not None and error.code == "cursor_expired":
                try:
                    return self._page(None)
                except WorkspaceRailError as reset_error:
                    _translate(reset_error)
            _translate(error)
        raise AssertionError("unreachable")


class GoogleDriveConnector:
    connector_id = "google.drive"

    def __init__(
        self,
        *,
        rail: WorkspaceClient,
        source_id: str,
        drive_id: str | None = None,
        mime_types: tuple[str, ...] = (),
        page_size: int = 100,
        include_document_text: bool = True,
    ):
        if (
            not callable(getattr(rail, "run", None))
            or not callable(getattr(rail, "export_document", None))
        ):
            raise ConnectorContractError("workspace rail is invalid")
        self.rail = rail
        self.source_id = _source(source_id)
        self.drive_id = _optional_string(drive_id, "drive_id")
        self.mime_types = _closed_strings(mime_types, "mime_types")
        if any(not MIME_TYPE.fullmatch(item) for item in self.mime_types):
            raise ConnectorContractError("mime_types is invalid")
        if type(page_size) is not int or not 1 <= page_size <= 500:
            raise ConnectorContractError("page_size is invalid")
        self.page_size = page_size
        if not isinstance(include_document_text, bool):
            raise ConnectorContractError("include_document_text is invalid")
        self.include_document_text = include_document_text

    def _file(self, raw: Any, *, removed: bool = False) -> ConnectorRecordV2:
        item = _mapping(raw, "drive file")
        file_id = _bounded_string(item.get("id"), "drive file id")
        occurred_at = _timestamp(item.get("modifiedTime"), fallback=EPOCH)
        native_id = f"gdrive:{file_id}"
        if removed or item.get("trashed") is True:
            return _record(
                native_id=native_id,
                occurred_at=occurred_at,
                kind="document.v1",
                deleted=True,
                provenance_uri="connector://google-drive",
            )
        name = _bounded_string(item.get("name"), "drive file name", maximum=MAX_TEXT_BYTES)
        mime_type = _bounded_string(item.get("mimeType"), "drive mime type")
        content: dict[str, Any] = {
            "document_id": file_id,
            "name": name,
            "mime_type": mime_type,
            "surface": "google_drive",
        }
        if occurred_at != EPOCH:
            content["modified_at"] = occurred_at
        parents = [
            _text(value)
            for value in _items(item.get("parents"), "drive parents")
            if isinstance(value, str)
        ]
        if parents:
            content["parent_id"] = parents[0]
        owners = []
        for raw_owner in _items(item.get("owners"), "drive owners"):
            owner = _mapping(raw_owner, "drive owner")
            owner_id = owner.get("permissionId") or owner.get("emailAddress")
            if isinstance(owner_id, str):
                owners.append(_text(owner_id))
        if owners:
            content["owner_ids"] = sorted(set(owners))
        if (
            self.include_document_text
            and mime_type == GOOGLE_DOCUMENT_MIME
        ):
            try:
                exported = self.rail.export_document(
                    file_id=file_id,
                    mime_type="text/plain",
                )
            except WorkspaceRailError as error:
                _translate(error)
            if not isinstance(exported, bytes):
                raise ConnectorContractError("drive export is invalid")
            content["text"] = _text(exported.decode(errors="replace"))
        return _record(
            native_id=native_id,
            occurred_at=occurred_at,
            kind="document.v1",
            content=content,
            provenance_uri="connector://google-drive",
        )

    def _file_query(self) -> str:
        clauses = ["trashed = false"]
        if self.mime_types:
            clauses.append(
                "(" + " or ".join(
                    f"mimeType = '{mime}'"
                    for mime in self.mime_types
                ) + ")"
            )
        return " and ".join(clauses)

    def _start_full(self) -> ConnectorPage:
        params: dict[str, Any] = {"supportsAllDrives": True}
        if self.drive_id:
            params["driveId"] = self.drive_id
        response = _mapping(
            self.rail.run("drive.changes.getStartPageToken", params),
            "drive start token",
        )
        start = _bounded_string(response.get("startPageToken"), "drive start token")
        return self._full({"phase": "full", "page": None, "start": start})

    def _full(self, state: Mapping[str, Any]) -> ConnectorPage:
        params: dict[str, Any] = {
            "pageSize": self.page_size,
            "q": self._file_query(),
            "spaces": "drive",
            "fields": (
                "nextPageToken,files(id,name,mimeType,modifiedTime,trashed,"
                "parents,owners(permissionId,emailAddress))"
            ),
        }
        if state["page"]:
            params["pageToken"] = state["page"]
        if self.drive_id:
            params.update({
                "corpora": "drive",
                "driveId": self.drive_id,
                "includeItemsFromAllDrives": True,
                "supportsAllDrives": True,
            })
        response = _mapping(
            self.rail.run("drive.files.list", params),
            "drive files response",
        )
        records = tuple(
            self._file(item)
            for item in _items(response.get("files"), "drive files")
        )
        next_page = _optional_string(response.get("nextPageToken"), "drive page token")
        if next_page:
            return ConnectorPage(
                records=records,
                next_cursor=_cursor({
                    "v": 1,
                    "phase": "full",
                    "page": next_page,
                    "start": state["start"],
                }),
                has_more=True,
            )
        return ConnectorPage(
            records=records,
            next_cursor=_cursor({
                "v": 1,
                "phase": "incremental",
                "page": state["start"],
            }),
            has_more=False,
        )

    def _incremental(self, state: Mapping[str, Any]) -> ConnectorPage:
        params: dict[str, Any] = {
            "pageToken": state["page"],
            "pageSize": self.page_size,
            "includeRemoved": True,
            "supportsAllDrives": True,
            "fields": (
                "nextPageToken,newStartPageToken,changes(fileId,removed,"
                "file(id,name,mimeType,modifiedTime,trashed,parents,"
                "owners(permissionId,emailAddress)))"
            ),
        }
        if self.drive_id:
            params["driveId"] = self.drive_id
            params["includeItemsFromAllDrives"] = True
        response = _mapping(
            self.rail.run("drive.changes.list", params),
            "drive changes response",
        )
        records = []
        for raw_change in _items(response.get("changes"), "drive changes"):
            change = _mapping(raw_change, "drive change")
            file_id = _bounded_string(change.get("fileId"), "drive file id")
            if change.get("removed") is True:
                records.append(self._file({"id": file_id}, removed=True))
                continue
            file_value = _mapping(change.get("file"), "drive changed file")
            if file_value.get("id") != file_id:
                raise ConnectorContractError("drive change identity mismatch")
            records.append(self._file(file_value))
        next_page = _optional_string(response.get("nextPageToken"), "drive page token")
        if next_page:
            return ConnectorPage(
                records=tuple(records),
                next_cursor=_cursor({
                    "v": 1,
                    "phase": "incremental",
                    "page": next_page,
                }),
                has_more=True,
            )
        start = _bounded_string(response.get("newStartPageToken"), "drive start token")
        return ConnectorPage(
            records=tuple(records),
            next_cursor=_cursor({
                "v": 1,
                "phase": "incremental",
                "page": start,
            }),
            has_more=False,
        )

    def pull(self, cursor: str | None) -> ConnectorPage:
        if cursor is None:
            try:
                return self._start_full()
            except WorkspaceRailError as error:
                _translate(error)
        state = _parse_cursor(cursor, {
            "full": {"v", "phase", "page", "start"},
            "incremental": {"v", "phase", "page"},
        })
        if (
            state["page"] is None
            or (state["phase"] == "full" and state["start"] is None)
        ):
            raise ConnectorContractError("connector cursor is invalid")
        try:
            return self._full(state) if state["phase"] == "full" else self._incremental(state)
        except WorkspaceRailError as error:
            if error.code == "cursor_expired":
                try:
                    return self._start_full()
                except WorkspaceRailError as reset_error:
                    _translate(reset_error)
            _translate(error)
        raise AssertionError("unreachable")


__all__ = [
    "GmailConnector",
    "GoogleCalendarConnector",
    "GoogleContactsConnector",
    "GoogleDriveConnector",
    "WorkspaceClient",
]
