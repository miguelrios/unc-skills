"""Typed, explicitly selected X activity streams."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping, Protocol

from connectors.remote_api import RemoteApiError
from connectors.sdk import (
    ConnectorContractError,
    ConnectorPage,
    ConnectorRecordV2,
    ConnectorUpstreamError,
    SOURCE_ID,
)


ALLOWED_STREAMS = ("bookmark", "home", "mention", "own")
OPERATIONS = {
    "bookmark": "bookmarks.list",
    "home": "home.list",
    "mention": "mentions.list",
    "own": "own.list",
}
MAX_CURSOR_BYTES = 8_192
MAX_ITEMS = 500
MAX_TEXT_BYTES = 500_000
MAX_VALUE_BYTES = 4_096
METRIC_FIELDS = (
    "bookmark_count",
    "impression_count",
    "like_count",
    "quote_count",
    "reply_count",
    "repost_count",
)


class JsonRail(Protocol):
    def request(
        self,
        operation_id: str,
        *,
        path: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> Any: ...


def _string(value: Any, label: str, *, maximum: int = MAX_VALUE_BYTES) -> str:
    if not isinstance(value, str) or not value or len(value.encode()) > maximum:
        raise ConnectorContractError(f"{label} is invalid")
    return value


def _optional_string(value: Any, label: str) -> str | None:
    return None if value is None else _string(value, label)


def _identifier(value: Any, label: str) -> str:
    result = _string(value, label)
    if not result.isascii() or not result.isdigit() or len(result) > 19:
        raise ConnectorContractError(f"{label} is invalid")
    return result


def _source(value: str) -> str:
    if not isinstance(value, str) or not SOURCE_ID.fullmatch(value):
        raise ConnectorContractError("source_id is invalid")
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


def _timestamp(value: Any, label: str) -> str:
    raw = _string(value, label)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ConnectorContractError(f"{label} is invalid") from None
    if parsed.tzinfo is None:
        raise ConnectorContractError(f"{label} is invalid")
    return raw


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise ConnectorContractError("x post text is invalid")
    encoded = value.encode(errors="replace")[:MAX_TEXT_BYTES]
    return encoded.decode(errors="ignore")


def _streams(value: tuple[str, ...]) -> tuple[str, ...]:
    if (
        not isinstance(value, tuple)
        or not value
        or len(value) != len(set(value))
        or any(item not in ALLOWED_STREAMS for item in value)
        or value != tuple(item for item in ALLOWED_STREAMS if item in value)
    ):
        raise ConnectorContractError("x streams are invalid")
    return value


def _cursor(
    *,
    stream: int,
    page: str | None,
    watermarks: Mapping[str, str | None],
    max_seen: str | None,
    cycle: int,
) -> str:
    try:
        raw = json.dumps(
            {
                "v": 1,
                "stream": stream,
                "page": page,
                "watermarks": dict(watermarks),
                "max_seen": max_seen,
                "cycle": cycle,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise ConnectorContractError("connector cursor is invalid") from None
    if len(raw.encode()) > MAX_CURSOR_BYTES:
        raise ConnectorContractError("connector cursor is invalid")
    return raw


def _state(raw: str | None, streams: tuple[str, ...]) -> dict[str, Any]:
    if raw is None:
        return {
            "v": 1,
            "stream": 0,
            "page": None,
            "watermarks": {stream: None for stream in streams},
            "max_seen": None,
            "cycle": 0,
        }
    if not isinstance(raw, str) or not raw or len(raw.encode()) > MAX_CURSOR_BYTES:
        raise ConnectorContractError("connector cursor is invalid")
    try:
        value = json.loads(
            raw,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (json.JSONDecodeError, ValueError):
        raise ConnectorContractError("connector cursor is invalid") from None
    if (
        not isinstance(value, dict)
        or set(value) != {
            "v", "stream", "page", "watermarks", "max_seen", "cycle",
        }
        or value.get("v") != 1
        or type(value.get("cycle")) is not int
        or not 0 <= value["cycle"] <= 2_147_483_647
        or type(value.get("stream")) is not int
        or not 0 <= value["stream"] < len(streams)
        or value.get("page") is not None
        and not isinstance(value["page"], str)
        or not isinstance(value.get("watermarks"), dict)
        or tuple(value["watermarks"]) != streams
    ):
        raise ConnectorContractError("connector cursor is invalid")
    _optional_string(value["page"], "connector page cursor")
    _optional_string(value.get("max_seen"), "connector maximum")
    for watermark in value["watermarks"].values():
        if watermark is not None:
            _identifier(watermark, "connector watermark")
    if value["max_seen"] is not None:
        _identifier(value["max_seen"], "connector maximum")
    return value


def _maximum(current: str | None, candidate: str) -> str:
    return candidate if current is None or int(candidate) > int(current) else current


def _record(post: Mapping[str, Any], *, stream: str) -> ConnectorRecordV2:
    post_id = _identifier(post.get("id"), "x post id")
    author_id = _identifier(post.get("author_id"), "x author id")
    created_at = _timestamp(post.get("created_at"), "x created timestamp")
    conversation = _identifier(
        post.get("conversation_id", post_id),
        "x conversation id",
    )
    native_id = f"x:{stream}:{post_id}"
    content: dict[str, Any] = {
        "author_id": author_id,
        "content_fidelity": "complete",
        "created_at": created_at,
        "post_id": native_id,
        "source_url": f"https://x.com/i/web/status/{post_id}",
        "stream_type": stream,
        "surface": "x",
        "text": _text(post.get("text")),
        "thread_id": f"x:{stream}:{conversation}",
    }
    metrics_value = post.get("public_metrics")
    if metrics_value is not None:
        metrics = _mapping(metrics_value, "x public metrics")
        selected = {}
        for field in METRIC_FIELDS:
            if field not in metrics:
                continue
            item = metrics[field]
            if type(item) is not int or item < 0:
                raise ConnectorContractError("x public metric is invalid")
            selected[field] = item
        if selected:
            content["metrics"] = selected
    for raw_reference in _items(post.get("referenced_tweets"), "x references"):
        reference = _mapping(raw_reference, "x reference")
        if reference.get("type") == "replied_to":
            content["reply_to_id"] = (
                f"x:{stream}:{_identifier(reference.get('id'), 'x reply id')}"
            )
            break
    return ConnectorRecordV2.from_mapping({
        "schema_version": 2,
        "native_id": native_id,
        "native_parent_id": (
            f"x:{stream}:{conversation}" if conversation != post_id else None
        ),
        "occurred_at": created_at,
        "content": {"kind": "social_post.v1", **content},
        "provenance": {"uri": "connector://x-activity"},
        "deleted": False,
    })


class XActivityConnector:
    connector_id = "x.activity"

    def __init__(
        self,
        *,
        rail: JsonRail,
        source_id: str,
        user_id: str,
        streams: tuple[str, ...],
        page_size: int = 100,
    ):
        if not callable(getattr(rail, "request", None)):
            raise ConnectorContractError("remote rail is invalid")
        self.rail = rail
        self.source_id = _source(source_id)
        self.user_id = _identifier(user_id, "x user id")
        self.streams = _streams(streams)
        if type(page_size) is not int or not 5 <= page_size <= 100:
            raise ConnectorContractError("page_size is invalid")
        self.page_size = page_size

    def pull(self, cursor: str | None) -> ConnectorPage:
        state = _state(cursor, self.streams)
        stream = self.streams[state["stream"]]
        query: dict[str, Any] = {"max_results": self.page_size}
        if state["page"]:
            query["pagination_token"] = state["page"]
        watermark = state["watermarks"][stream]
        if watermark and stream != "bookmark":
            query["since_id"] = watermark
        try:
            response = _mapping(
                self.rail.request(
                    OPERATIONS[stream],
                    path={"user_id": self.user_id},
                    query=query,
                ),
                "x response",
            )
        except RemoteApiError as error:
            code = {
                "authority_revoked": "connector_authority_revoked",
                "authority_forbidden": "connector_authority_forbidden",
                "response_invalid": "connector_schema_drift",
            }.get(error.code, "connector_upstream_error")
            raise ConnectorUpstreamError(code) from None
        errors = response.get("errors")
        if errors is not None and _items(errors, "x errors"):
            raise ConnectorUpstreamError("connector_upstream_error")
        records = []
        maximum = state["max_seen"]
        for raw in _items(response.get("data"), "x posts"):
            post = _mapping(raw, "x post")
            record = _record(post, stream=stream)
            maximum = _maximum(maximum, post["id"])
            records.append(record)
        meta = _mapping(response.get("meta", {}), "x metadata")
        next_page = _optional_string(meta.get("next_token"), "x page cursor")
        watermarks = dict(state["watermarks"])
        if next_page:
            return ConnectorPage(
                records=tuple(records),
                next_cursor=_cursor(
                    stream=state["stream"],
                    page=next_page,
                    watermarks=watermarks,
                    max_seen=maximum,
                    cycle=state["cycle"],
                ),
                has_more=True,
            )
        watermarks[stream] = maximum
        if state["stream"] + 1 < len(self.streams):
            next_index = state["stream"] + 1
            next_stream = self.streams[next_index]
            return ConnectorPage(
                records=tuple(records),
                next_cursor=_cursor(
                    stream=next_index,
                    page=None,
                    watermarks=watermarks,
                    max_seen=watermarks[next_stream],
                    cycle=state["cycle"],
                ),
                has_more=True,
            )
        first = self.streams[0]
        return ConnectorPage(
            records=tuple(records),
            next_cursor=_cursor(
                stream=0,
                page=None,
                watermarks=watermarks,
                max_seen=watermarks[first],
                cycle=(
                    0
                    if state["cycle"] == 2_147_483_647
                    else state["cycle"] + 1
                ),
            ),
            has_more=False,
        )


__all__ = ["XActivityConnector"]
