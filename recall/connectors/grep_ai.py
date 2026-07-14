"""Read-only Grep AI API v2 completed-research connector."""

from __future__ import annotations

import base64
from datetime import datetime
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import ssl
import stat
from typing import Any, Mapping, Protocol
import urllib.error
import urllib.parse
import urllib.request

from connectors.sdk import (
    ConnectorContractError,
    ConnectorPage,
    ConnectorRateLimited,
    ConnectorRecord,
    ConnectorUpstreamError,
    SOURCE_ID,
)


BASE_URL = "https://api.grep.ai"
LIST_PATH = "/api/v2/research"
MAX_RESPONSE_BYTES = 2_000_000
MAX_REPORT_CHARS = 750_000
MAX_STRUCTURED_BYTES = 250_000
MAX_ITEMS = 100
USER_AGENT = "parcha-recall-grep-ai/0.1"
API_KEY = re.compile(r"parcha-[a-z0-9_-]+-[0-9a-fA-F]{32}\Z")
JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{7,127}\Z")
CURSOR_PREFIX = "gai2_"
TERMINAL_MEMORY = {"complete", "completed"}
NONTERMINAL = {
    "queued", "moderation", "planning", "running", "paused",
    "in progress", "in_progress",
}
TERMINAL_NO_MEMORY = {"failed", "blocked", "cancelled", "canceled"}
KNOWN_STATUSES = TERMINAL_MEMORY | NONTERMINAL | TERMINAL_NO_MEMORY
LIST_FIELDS = {"job_id", "status", "created_at", "updated_at", "completed_at", "effort", "question", "slug"}
LIST_REQUIRED = {"job_id", "status", "question"}
DETAIL_FIELDS = {
    "attachments", "completed_at", "context", "created_at", "effort", "expert_id",
    "is_public", "job_id", "json_schema", "question", "report", "response_language",
    "revisions", "slug", "started_at", "status", "structured_output", "updated_at",
}
DETAIL_REQUIRED = {"job_id", "status", "question"}
ERROR_CODES = {
    "invalid_request", "unauthenticated", "insufficient_credits", "forbidden",
    "job_not_found", "resource_not_found", "conflict", "validation_error",
    "rate_limited", "internal_error", "upstream_unavailable",
    "idempotency_key_reuse_mismatch", "idempotency_key_in_flight",
    "idempotency_key_ttl_expired",
}
URL = re.compile(r"https?://[^\s<>\])}]+")


class GrepAIUpstreamError(ConnectorUpstreamError):
    pass


@dataclass(frozen=True)
class GrepAIResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes
    final_url: str


class GrepAITransport(Protocol):
    def request(self, *, path: str, query: Mapping[str, str], headers: Mapping[str, str],
                timeout: float, max_bytes: int) -> GrepAIResponse: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class UrllibGrepAITransport:
    def __init__(self) -> None:
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context()), _NoRedirect(),
        )

    def request(self, *, path: str, query: Mapping[str, str], headers: Mapping[str, str],
                timeout: float, max_bytes: int) -> GrepAIResponse:
        url = BASE_URL + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        try:
            try:
                response = self.opener.open(request, timeout=timeout)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                return GrepAIResponse(
                    status=int(response.status),
                    headers={key.casefold(): value for key, value in response.headers.items()},
                    body=response.read(max_bytes + 1),
                    final_url=response.geturl(),
                )
        except (OSError, TimeoutError, urllib.error.URLError):
            raise GrepAIUpstreamError("grep_ai_unavailable") from None


def load_private_api_key(path: Path) -> str:
    key_path = Path(path).expanduser()
    try:
        metadata = key_path.lstat()
    except OSError:
        raise PermissionError("grep_ai_key_unavailable") from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PermissionError("grep_ai_key_invalid")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise PermissionError("grep_ai_key_not_private")
    if metadata.st_size > 256:
        raise PermissionError("grep_ai_key_invalid")
    try:
        descriptor = os.open(key_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or stat.S_IMODE(opened.st_mode) & 0o077
                or opened.st_size > 256
                or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
            ):
                raise PermissionError("grep_ai_key_invalid")
            value = os.read(descriptor, 257).decode("utf-8", errors="strict").strip()
        finally:
            os.close(descriptor)
    except (OSError, UnicodeDecodeError):
        raise PermissionError("grep_ai_key_invalid") from None
    return validate_api_key(value)


def validate_api_key(value: str) -> str:
    if not isinstance(value, str) or not API_KEY.fullmatch(value):
        raise PermissionError("grep_ai_key_invalid")
    return value


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                          separators=(",", ":")).encode()
    except (TypeError, ValueError):
        raise ConnectorContractError("grep_ai_invalid_json_value") from None


def _json(body: bytes) -> Any:
    if not isinstance(body, bytes) or len(body) > MAX_RESPONSE_BYTES:
        raise ConnectorContractError("grep_ai_response_too_large")
    try:
        return json.loads(
            body.decode("utf-8", errors="strict"),
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ConnectorContractError("grep_ai_invalid_json") from None


def _string(value: Any, label: str, *, maximum: int = 4096, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ConnectorContractError(f"grep_ai_invalid_{label}")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        raise ConnectorContractError(f"grep_ai_invalid_{label}")
    return value


def _content_string(value: Any, label: str, *, maximum: int, allow_empty: bool = False) -> str:
    """Validate projected prose and normalize JSON-valid transport controls."""
    if not isinstance(value, str) or (not value and not allow_empty) or len(value) > maximum:
        raise ConnectorContractError(f"grep_ai_invalid_{label}")
    return "".join(
        character if ord(character) >= 32 or character in "\n\r\t" else "\ufffd"
        for character in value
    )


def _timestamp_string(value: Any, label: str, *, nullable: bool = False) -> str | None:
    raw = _string(value, label, maximum=64, nullable=nullable)
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ConnectorContractError(f"grep_ai_invalid_{label}") from None
    if parsed.tzinfo is None:
        raise ConnectorContractError(f"grep_ai_invalid_{label}")
    return raw


def _job_id(value: Any) -> str:
    result = _string(value, "job_id", maximum=128)
    if not JOB_ID.fullmatch(result):
        raise ConnectorContractError("grep_ai_invalid_job_id")
    return result


def _fingerprint(job_id: str) -> str:
    return hashlib.sha256(job_id.encode()).hexdigest()


def encode_cursor(state: dict[str, Any]) -> str:
    raw = _canonical(state)
    return CURSOR_PREFIX + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    if not isinstance(cursor, str) or not cursor.startswith(CURSOR_PREFIX) or len(cursor) > 8192:
        raise ConnectorContractError("grep_ai_invalid_cursor")
    try:
        raw = cursor[len(CURSOR_PREFIX):]
        value = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except Exception:
        raise ConnectorContractError("grep_ai_invalid_cursor") from None
    if not isinstance(value, dict) or value.get("v") != 2 or value.get("phase") not in {"head", "sweep"}:
        raise ConnectorContractError("grep_ai_invalid_cursor")
    if value["phase"] == "head":
        if set(value) != {"v", "phase", "watermark"}:
            raise ConnectorContractError("grep_ai_invalid_cursor")
        watermark = value["watermark"]
        if watermark is not None and (not isinstance(watermark, str) or not re.fullmatch(r"[0-9a-f]{64}", watermark)):
            raise ConnectorContractError("grep_ai_invalid_cursor")
    else:
        if set(value) != {"v", "phase", "after", "stop", "head", "pages"}:
            raise ConnectorContractError("grep_ai_invalid_cursor")
        if not isinstance(value["after"], str) or not value["after"] or len(value["after"]) > 4096:
            raise ConnectorContractError("grep_ai_invalid_cursor")
        for key in ("stop", "head"):
            if value[key] is not None and (not isinstance(value[key], str) or not re.fullmatch(r"[0-9a-f]{64}", value[key])):
                raise ConnectorContractError("grep_ai_invalid_cursor")
        if not isinstance(value["pages"], int) or isinstance(value["pages"], bool) or value["pages"] < 1:
            raise ConnectorContractError("grep_ai_invalid_cursor")
    return value


def _exact_url(url: str, expected_path: str) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    return (
        parsed.scheme == "https" and parsed.hostname == "api.grep.ai" and parsed.port is None
        and parsed.path == expected_path and not parsed.username and not parsed.password
        and not parsed.fragment
    )


def _retry_after(headers: Mapping[str, str]) -> int:
    raw = next((value for key, value in headers.items() if key.casefold() == "retry-after"), None)
    try:
        value = int(raw) if raw is not None else 60
    except (TypeError, ValueError):
        value = 60
    return min(3600, max(1, value))


def _normalize_urls(markdown: str) -> str:
    def replace(match: re.Match[str]) -> str:
        try:
            parsed = urllib.parse.urlparse(match.group(0))
        except ValueError:
            raise ConnectorContractError("grep_ai_invalid_report_url") from None
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ConnectorContractError("grep_ai_invalid_report_url")
        host = parsed.hostname + ((":" + str(parsed.port)) if parsed.port else "")
        return urllib.parse.urlunparse((parsed.scheme, host, parsed.path, "", "", ""))
    return URL.sub(replace, markdown)


def _list_page(value: Any) -> tuple[list[dict[str, Any]], str | None, bool]:
    if not isinstance(value, dict) or set(value) != {"items", "next_cursor", "has_more"}:
        raise ConnectorContractError("grep_ai_invalid_list")
    items, next_cursor, has_more = value["items"], value["next_cursor"], value["has_more"]
    if not isinstance(items, list) or len(items) > MAX_ITEMS or not isinstance(has_more, bool):
        raise ConnectorContractError("grep_ai_invalid_list")
    if has_more:
        _string(next_cursor, "upstream_cursor", maximum=4096)
    elif next_cursor is not None:
        raise ConnectorContractError("grep_ai_invalid_list")
    normalized = []
    identities = set()
    for item in items:
        if not isinstance(item, dict) or not LIST_REQUIRED.issubset(item) or set(item) - LIST_FIELDS:
            raise ConnectorContractError("grep_ai_invalid_list_item")
        job_id = _job_id(item["job_id"])
        if job_id in identities:
            raise ConnectorContractError("grep_ai_duplicate_job")
        identities.add(job_id)
        status = _string(item["status"], "status", maximum=32)
        if status not in KNOWN_STATUSES:
            raise ConnectorContractError("grep_ai_invalid_status")
        _content_string(item["question"], "question", maximum=100_000, allow_empty=True)
        for key in set(item) - LIST_REQUIRED:
            if key in {"completed_at", "created_at", "updated_at"}:
                _timestamp_string(item[key], key, nullable=True)
            elif key in {"effort", "slug"}:
                if item[key] is not None:
                    _content_string(item[key], key, maximum=256, allow_empty=True)
        normalized.append(item)
    return normalized, next_cursor, has_more


def _project_detail(value: Any, expected_job_id: str, list_item: Mapping[str, Any] | None = None) -> ConnectorRecord:
    if not isinstance(value, dict) or not DETAIL_REQUIRED.issubset(value) or set(value) - DETAIL_FIELDS:
        raise ConnectorContractError("grep_ai_invalid_detail")
    job_id = _job_id(value["job_id"])
    if job_id != expected_job_id:
        raise ConnectorContractError("grep_ai_job_mismatch")
    status = _string(value["status"], "status", maximum=32)
    if status not in TERMINAL_MEMORY:
        raise ConnectorContractError("grep_ai_detail_not_complete")
    question = _content_string(value["question"], "question", maximum=100_000, allow_empty=True)
    report = value.get("report")
    if report is None:
        markdown = ""
    else:
        if not isinstance(report, dict) or "markdown" not in report or set(report) - {"markdown", "revision_sha", "widgets"}:
            raise ConnectorContractError("grep_ai_invalid_report")
        markdown = _normalize_urls(_content_string(
            report["markdown"], "report", maximum=MAX_REPORT_CHARS, allow_empty=True,
        ))
        if report.get("widgets") is not None and not isinstance(report["widgets"], list):
            raise ConnectorContractError("grep_ai_invalid_report")
        if report.get("revision_sha") is not None:
            _content_string(report["revision_sha"], "revision", maximum=256, allow_empty=True)
    structured = value.get("structured_output")
    if structured is not None and not isinstance(structured, dict):
        raise ConnectorContractError("grep_ai_invalid_structured_output")
    if structured is not None and len(_canonical(structured)) > MAX_STRUCTURED_BYTES:
        raise ConnectorContractError("grep_ai_structured_output_too_large")

    def optional_text(key: str, maximum: int) -> str | None:
        candidate = value.get(key)
        return None if candidate is None else _content_string(
            candidate, key, maximum=maximum, allow_empty=True,
        )

    effort = optional_text("effort", 64)
    expert = optional_text("expert_id", 256)
    language = optional_text("response_language", 64)
    completed_at = _timestamp_string(value.get("completed_at"), "completed_at", nullable=True)
    created_at = _timestamp_string(value.get("created_at"), "created_at", nullable=True)
    updated_at = _timestamp_string(value.get("updated_at"), "updated_at", nullable=True)
    _timestamp_string(value.get("started_at"), "started_at", nullable=True)
    if "is_public" in value and not isinstance(value["is_public"], bool):
        raise ConnectorContractError("grep_ai_invalid_visibility")
    if list_item is not None:
        completed_at = completed_at or _timestamp_string(list_item.get("completed_at"), "completed_at", nullable=True)
        created_at = created_at or _timestamp_string(list_item.get("created_at"), "created_at", nullable=True)
        updated_at = updated_at or _timestamp_string(list_item.get("updated_at"), "updated_at", nullable=True)
    occurred_at = completed_at or updated_at or created_at or "1970-01-01T00:00:00Z"
    content = {
        "provider": "grep.ai", "status": status, "question": question,
        "report_markdown": markdown, "structured_output": structured,
        "effort": effort, "expert_id": expert, "response_language": language,
        "created_at": created_at, "completed_at": completed_at,
    }
    native_id = "grep-ai-" + _fingerprint(job_id)[:48]
    return ConnectorRecord(
        schema_version=1, native_id=native_id, occurred_at=occurred_at,
        content=content,
        provenance={"uri": "connector://grep-ai/completed-research", "provider": "grep.ai"},
        deleted=False,
    )


class GrepAIConnector:
    connector_id = "grep.ai"

    def __init__(self, *, api_key: str, source_id: str,
                 transport: GrepAITransport | None = None,
                 max_pages: int = 100, page_size: int = 10, timeout: float = 20.0):
        if not isinstance(api_key, str) or not API_KEY.fullmatch(api_key):
            raise PermissionError("grep_ai_key_invalid")
        if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
            raise ConnectorContractError("grep_ai_invalid_source")
        if not isinstance(max_pages, int) or isinstance(max_pages, bool) or not 1 <= max_pages <= 1000:
            raise ConnectorContractError("grep_ai_invalid_page_cap")
        if not isinstance(page_size, int) or isinstance(page_size, bool) or not 1 <= page_size <= MAX_ITEMS:
            raise ConnectorContractError("grep_ai_invalid_page_size")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 60:
            raise ConnectorContractError("grep_ai_invalid_timeout")
        self._api_key = api_key
        self.source_id = source_id
        self.transport = transport or UrllibGrepAITransport()
        self.max_pages = max_pages
        self.page_size = page_size
        self.timeout = float(timeout)

    def _request(self, path: str, query: Mapping[str, str]) -> Any:
        if path != LIST_PATH and not re.fullmatch(r"/api/v2/research/[A-Za-z0-9_.-]{8,128}", path):
            raise ConnectorContractError("grep_ai_path_rejected")
        try:
            response = self.transport.request(
                path=path, query=query,
                headers={
                    "Authorization": "Bearer " + self._api_key,
                    "Accept": "application/json", "User-Agent": USER_AGENT,
                },
                timeout=self.timeout, max_bytes=MAX_RESPONSE_BYTES,
            )
        except (GrepAIUpstreamError, ConnectorRateLimited):
            raise
        except Exception:
            raise GrepAIUpstreamError("grep_ai_unavailable") from None
        if not isinstance(response, GrepAIResponse):
            raise GrepAIUpstreamError("grep_ai_invalid_transport")
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise ConnectorContractError("grep_ai_response_too_large")
        if not _exact_url(response.final_url, path):
            raise GrepAIUpstreamError("grep_ai_redirect_rejected")
        content_type = next((v for k, v in response.headers.items() if k.casefold() == "content-type"), "")
        if content_type.split(";", 1)[0].strip().casefold() != "application/json":
            raise ConnectorContractError("grep_ai_invalid_content_type")
        if response.status == 200:
            return _json(response.body)
        error = _json(response.body)
        if not isinstance(error, dict) or set(error) != {"error"} or not isinstance(error["error"], dict):
            raise GrepAIUpstreamError("grep_ai_invalid_error")
        code = error["error"].get("code")
        if code not in ERROR_CODES:
            raise GrepAIUpstreamError("grep_ai_unknown_error")
        if response.status == 429 and code == "rate_limited":
            raise ConnectorRateLimited(retry_after_seconds=_retry_after(response.headers))
        raise GrepAIUpstreamError("grep_ai_" + code)

    def pull(self, cursor: str | None) -> ConnectorPage:
        if cursor is None:
            stop = boundary = after = None
            pages = 0
        else:
            state = decode_cursor(cursor)
            if state["phase"] == "head":
                stop, boundary, after, pages = state["watermark"], None, None, 0
            else:
                stop, boundary, after, pages = state["stop"], state["head"], state["after"], state["pages"]
        query = {"limit": str(self.page_size)}
        if after is not None:
            query["cursor"] = after
        items, next_upstream, upstream_has_more = _list_page(self._request(LIST_PATH, query))
        pages += 1
        if pages > self.max_pages:
            raise ConnectorContractError("grep_ai_page_cap_exceeded")
        records = []
        reached_stop = False
        for item in items:
            job_id = item["job_id"]
            fingerprint = _fingerprint(job_id)
            if stop is not None and fingerprint == stop:
                reached_stop = True
                if boundary is None:
                    boundary = stop
                break
            if item["status"] in NONTERMINAL:
                # Keep the checkpoint behind every unfinished job. This makes a
                # created-newest list revisit state transitions without storing
                # private job IDs or rescanning settled history indefinitely.
                boundary = None
                continue
            if boundary is None:
                boundary = fingerprint
            if item["status"] in TERMINAL_MEMORY:
                detail_path = LIST_PATH + "/" + urllib.parse.quote(job_id, safe="")
                records.append(_project_detail(self._request(detail_path, {}), job_id, item))
        if reached_stop or not upstream_has_more:
            next_cursor = encode_cursor({"v": 2, "phase": "head", "watermark": boundary or stop})
            return ConnectorPage(records=tuple(records), next_cursor=next_cursor, has_more=False)
        if pages >= self.max_pages:
            raise ConnectorContractError("grep_ai_page_cap_exceeded")
        if not isinstance(next_upstream, str) or not next_upstream or next_upstream == after:
            raise ConnectorContractError("grep_ai_cursor_did_not_advance")
        next_cursor = encode_cursor({
            "v": 2, "phase": "sweep", "after": next_upstream,
            "stop": stop, "head": boundary, "pages": pages,
        })
        return ConnectorPage(records=tuple(records), next_cursor=next_cursor, has_more=True)
