"""Bounded conditional RSS and Atom polling."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

from connectors.portable_pim import EPOCH, _bounded
from connectors.sdk import (
    IDENTITY,
    SOURCE_ID,
    ConnectorContractError,
    ConnectorPage,
    ConnectorRateLimited,
    ConnectorRecordV2,
)
from privacy.transport import open_no_redirect


MAX_FEED_BYTES = 8_000_000
MAX_FEED_ENTRIES = 500
MAX_XML_ELEMENTS = 20_000
MAX_ETAG_BYTES = 1_024
MAX_LAST_MODIFIED_BYTES = 256
CURSOR_PREFIX = "feed-v1:"


@dataclass(frozen=True)
class FeedResponse:
    status: int
    body: bytes
    etag: str | None
    last_modified: str | None

    def __post_init__(self) -> None:
        if self.status not in {200, 304}:
            raise ConnectorContractError("feed_response_invalid")
        if (
            not isinstance(self.body, bytes)
            or len(self.body) > MAX_FEED_BYTES
            or (self.status == 304 and self.body)
        ):
            raise ConnectorContractError("feed_response_invalid")
        _header(self.etag, MAX_ETAG_BYTES)
        _header(self.last_modified, MAX_LAST_MODIFIED_BYTES)


class FeedTransport(Protocol):
    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FeedResponse: ...


def _header(value: str | None, maximum: int) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode()) > maximum
        or any(ord(character) < 32 or ord(character) > 126 for character in value)
    ):
        raise ConnectorContractError("feed_header_invalid")
    return value


def _url(value: str) -> str:
    if not isinstance(value, str) or len(value.encode()) > 2_048:
        raise ConnectorContractError("feed_url_invalid")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ConnectorContractError("feed_url_invalid") from None
    hostname = parsed.hostname
    if (
        parsed.scheme != "https"
        or not hostname
        or hostname != hostname.lower()
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or parsed.query
        or port not in {None, 443}
        or "\\" in parsed.path
        or any(part == ".." for part in parsed.path.split("/"))
        or hostname == "localhost"
        or hostname.endswith((".local", ".localhost", ".internal"))
    ):
        raise ConnectorContractError("feed_url_invalid")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ConnectorContractError("feed_url_invalid")
    return urllib.parse.urlunsplit(("https", hostname, parsed.path or "/", "", ""))


def _public_host(hostname: str) -> None:
    try:
        addresses = {
            item[4][0]
            for item in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        }
    except OSError:
        raise ConnectorContractError("feed_host_unavailable") from None
    if not addresses:
        raise ConnectorContractError("feed_host_unavailable")
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            raise ConnectorContractError("feed_host_invalid") from None
        if not address.is_global:
            raise ConnectorContractError("feed_host_invalid")


class HttpsFeedTransport:
    def __init__(self, *, timeout_seconds: int = 30, opener=open_no_redirect):
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60:
            raise ValueError("invalid feed timeout")
        if not callable(opener):
            raise ValueError("invalid feed opener")
        self.timeout_seconds = timeout_seconds
        self.opener = opener

    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> FeedResponse:
        url = _url(url)
        _public_host(urllib.parse.urlsplit(url).hostname or "")
        headers = {
            "Accept": (
                "application/atom+xml, application/rss+xml, application/xml, "
                "text/xml;q=0.9"
            ),
            "User-Agent": "RecallFeed/1",
        }
        if etag is not None:
            headers["If-None-Match"] = _header(etag, MAX_ETAG_BYTES) or ""
        if last_modified is not None:
            headers["If-Modified-Since"] = (
                _header(last_modified, MAX_LAST_MODIFIED_BYTES) or ""
            )
        request = urllib.request.Request(url, method="GET", headers=headers)
        try:
            response = self.opener(request, timeout=self.timeout_seconds)
        except urllib.error.HTTPError as error:
            if error.code == 304:
                return FeedResponse(
                    304, b"", error.headers.get("ETag") or etag,
                    error.headers.get("Last-Modified") or last_modified,
                )
            if error.code == 429:
                raw = error.headers.get("Retry-After")
                try:
                    retry = int(raw) if raw is not None else 60
                except ValueError:
                    retry = 60
                raise ConnectorRateLimited(
                    retry_after_seconds=max(1, min(retry, 3_600))
                ) from None
            raise ConnectorContractError("feed_http_error") from None
        except (OSError, urllib.error.URLError):
            raise ConnectorContractError("feed_http_error") from None
        try:
            status = response.getcode()
            content_type = response.headers.get_content_type()
            if status != 200 or content_type not in {
                "application/atom+xml",
                "application/rss+xml",
                "application/xml",
                "text/xml",
            }:
                raise ConnectorContractError("feed_http_response_invalid")
            body = response.read(MAX_FEED_BYTES + 1)
            if len(body) > MAX_FEED_BYTES:
                raise ConnectorContractError("feed_http_response_invalid")
            return FeedResponse(
                200,
                body,
                response.headers.get("ETag"),
                response.headers.get("Last-Modified"),
            )
        finally:
            response.close()


def _cursor(value: str | None) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    if not isinstance(value, str) or not value.startswith(CURSOR_PREFIX):
        raise ConnectorContractError("feed_cursor_invalid")
    encoded = value.removeprefix(CURSOR_PREFIX)
    try:
        padding = "=" * (-len(encoded) % 4)
        raw = base64.urlsafe_b64decode(encoded + padding)
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise ConnectorContractError("feed_cursor_invalid") from None
    if not isinstance(data, dict) or set(data) != {"etag", "last_modified"}:
        raise ConnectorContractError("feed_cursor_invalid")
    return (
        _header(data["etag"], MAX_ETAG_BYTES),
        _header(data["last_modified"], MAX_LAST_MODIFIED_BYTES),
    )


def _next_cursor(etag: str | None, last_modified: str | None) -> str:
    raw = json.dumps(
        {"etag": etag, "last_modified": last_modified},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return CURSOR_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _local(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower()


def _child(element: ET.Element, name: str) -> ET.Element | None:
    return next((item for item in element if _local(item) == name), None)


def _text(element: ET.Element | None) -> str:
    return "" if element is None else "".join(element.itertext()).strip()


def _time(value: str) -> str:
    if not value:
        return EPOCH
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T.*", value):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError):
        raise ConnectorContractError("feed_timestamp_invalid") from None


def _source_url(value: str) -> str | None:
    if not value:
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return value


class FeedConnector:
    connector_id = "portable.feed"

    def __init__(
        self,
        *,
        url: str,
        feed_id: str,
        source_id: str,
        transport: FeedTransport | None = None,
    ):
        if not isinstance(feed_id, str) or not IDENTITY.fullmatch(feed_id):
            raise ConnectorContractError("feed_id_invalid")
        if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
            raise ConnectorContractError("feed_source_id_invalid")
        self.url = _url(url)
        self.feed_id = feed_id
        self.source_id = source_id
        self.transport = transport or HttpsFeedTransport()

    def _records(self, raw: bytes) -> tuple[ConnectorRecordV2, ...]:
        if (
            not raw
            or len(raw) > MAX_FEED_BYTES
            or b"<!DOCTYPE" in raw.upper()
            or b"<!ENTITY" in raw.upper()
        ):
            raise ConnectorContractError("feed_xml_invalid")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            raise ConnectorContractError("feed_xml_invalid") from None
        if sum(1 for _item in root.iter()) > MAX_XML_ELEMENTS:
            raise ConnectorContractError("feed_xml_invalid")
        root_name = _local(root)
        if root_name == "rss":
            channel = _child(root, "channel")
            if channel is None:
                raise ConnectorContractError("feed_xml_invalid")
            entries = [item for item in channel if _local(item) == "item"]
            mode = "rss"
        elif root_name == "feed":
            entries = [item for item in root if _local(item) == "entry"]
            mode = "atom"
        else:
            raise ConnectorContractError("feed_xml_invalid")
        if len(entries) > MAX_FEED_ENTRIES:
            raise ConnectorContractError("feed_too_many_entries")
        records = []
        for entry in entries:
            if mode == "rss":
                title = _text(_child(entry, "title"))
                link = _text(_child(entry, "link"))
                identity = _text(_child(entry, "guid")) or link
                body = _text(_child(entry, "description")) or _text(
                    _child(entry, "content")
                )
                published = _text(_child(entry, "pubdate"))
            else:
                title = _text(_child(entry, "title"))
                identity = _text(_child(entry, "id"))
                link_element = _child(entry, "link")
                link = (
                    link_element.attrib.get("href", "")
                    if link_element is not None
                    else ""
                )
                body = _text(_child(entry, "content")) or _text(
                    _child(entry, "summary")
                )
                published = _text(_child(entry, "updated")) or _text(
                    _child(entry, "published")
                )
            if not identity:
                identity = hashlib.sha256(
                    f"{title}\0{published}\0{body}".encode()
                ).hexdigest()
            native_id = "feed:" + hashlib.sha256(
                f"{self.feed_id}\0{identity}".encode()
            ).hexdigest()
            occurred_at = _time(published)
            content: dict[str, Any] = {
                "kind": "document.v1",
                "document_id": native_id,
                "mime_type": (
                    "application/rss+xml"
                    if mode == "rss"
                    else "application/atom+xml"
                ),
                "name": _bounded(title or "(untitled)", "feed_title", maximum=10_000),
                "surface": "rss" if mode == "rss" else "atom",
                "text": _bounded(body, "feed_text"),
            }
            source_url = _source_url(link)
            if source_url is not None:
                content["source_url"] = source_url
            if occurred_at != EPOCH:
                content["modified_at"] = occurred_at
            records.append(ConnectorRecordV2(
                schema_version=2,
                native_id=native_id,
                occurred_at=occurred_at,
                content=content,
                provenance={"uri": f"connector://portable-feed/{native_id}"},
            ))
        return tuple(sorted(records, key=lambda record: record.native_id))

    def pull(self, cursor: str | None) -> ConnectorPage:
        etag, last_modified = _cursor(cursor)
        response = self.transport.fetch(
            self.url, etag=etag, last_modified=last_modified
        )
        next_etag = response.etag if response.etag is not None else etag
        next_modified = (
            response.last_modified
            if response.last_modified is not None
            else last_modified
        )
        return ConnectorPage(
            records=() if response.status == 304 else self._records(response.body),
            next_cursor=_next_cursor(next_etag, next_modified),
            has_more=False,
        )


__all__ = ["FeedConnector", "FeedResponse", "HttpsFeedTransport"]
