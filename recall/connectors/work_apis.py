"""Typed GitHub, Linear, Slack, and Notion pull connectors."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import urlsplit

from connectors.remote_api import BoundedJsonRail, RemoteApiError, RemoteOperation
from connectors.sdk import (
    ConnectorContractError,
    ConnectorPage,
    ConnectorRecordV2,
    ConnectorUpstreamError,
    SOURCE_ID,
)


EPOCH = "1970-01-01T00:00:00Z"
MAX_ITEMS = 500
MAX_TEXT_BYTES = 500_000
MAX_VALUE_BYTES = 4_096


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


def _optional_string(value: Any, label: str, *, maximum: int = MAX_VALUE_BYTES) -> str | None:
    return None if value is None else _string(value, label, maximum=maximum)


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


def _text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    encoded = value.encode(errors="replace")[:MAX_TEXT_BYTES]
    return encoded.decode(errors="ignore")


def _timestamp(value: Any, label: str, *, fallback: str | None = None) -> str:
    if not isinstance(value, str) or not value:
        if fallback is not None:
            return fallback
        raise ConnectorContractError(f"{label} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        if fallback is not None:
            return fallback
        raise ConnectorContractError(f"{label} is invalid") from None
    if parsed.tzinfo is None:
        if fallback is not None:
            return fallback
        raise ConnectorContractError(f"{label} is invalid")
    return value


def _slack_timestamp(value: Any, label: str) -> str:
    raw = _string(value, label)
    whole, separator, fraction = raw.partition(".")
    if not whole.isdigit() or (separator and (not fraction.isdigit() or len(fraction) > 6)):
        raise ConnectorContractError(f"{label} is invalid")
    try:
        parsed = datetime.fromtimestamp(
            int(whole),
            timezone.utc,
        ).replace(microsecond=int((fraction + "000000")[:6]))
    except (ValueError, OverflowError, OSError):
        raise ConnectorContractError(f"{label} is invalid") from None
    rendered = parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return rendered


def _slack_oldest(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    seconds = int(parsed.timestamp())
    return f"{seconds}.{parsed.microsecond:06d}"


def _url(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value.encode()) > MAX_VALUE_BYTES:
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    return value


def _cursor(*, page: Any, watermark: str, max_seen: str) -> str:
    try:
        raw = json.dumps(
            {"v": 1, "page": page, "watermark": watermark, "max_seen": max_seen},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise ConnectorContractError("connector cursor is invalid") from None
    if len(raw.encode()) > MAX_VALUE_BYTES:
        raise ConnectorContractError("connector cursor is invalid")
    return raw


def _state(raw: str | None) -> dict[str, Any]:
    if raw is None:
        return {"v": 1, "page": None, "watermark": EPOCH, "max_seen": EPOCH}
    if not isinstance(raw, str) or not raw or len(raw.encode()) > MAX_VALUE_BYTES:
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
        or set(value) != {"v", "page", "watermark", "max_seen"}
        or value.get("v") != 1
    ):
        raise ConnectorContractError("connector cursor is invalid")
    _timestamp(value.get("watermark"), "connector cursor watermark")
    _timestamp(value.get("max_seen"), "connector cursor maximum")
    return value


def _max_timestamp(current: str, candidate: str) -> str:
    left = datetime.fromisoformat(current.replace("Z", "+00:00"))
    right = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    return candidate if right > left else current


def _record(
    *,
    native_id: str,
    occurred_at: str,
    kind: str,
    provenance_uri: str,
    content: Mapping[str, Any] | None = None,
    parent: str | None = None,
    deleted: bool = False,
) -> ConnectorRecordV2:
    return ConnectorRecordV2.from_mapping({
        "schema_version": 2,
        "native_id": native_id,
        "native_parent_id": parent,
        "occurred_at": occurred_at,
        "content": {"kind": kind} if deleted else {"kind": kind, **dict(content or {})},
        "provenance": {"uri": provenance_uri},
        "deleted": deleted,
    })


def _translate(error: RemoteApiError) -> None:
    code = {
        "authority_revoked": "connector_authority_revoked",
        "authority_forbidden": "connector_authority_forbidden",
        "response_invalid": "connector_schema_drift",
        "content_type_invalid": "connector_schema_drift",
    }.get(error.code, "connector_upstream_error")
    raise ConnectorUpstreamError(code) from None


def _page_size(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 100:
        raise ConnectorContractError("page_size is invalid")
    return value


def github_rail(*, authority_path: Path, **options: Any) -> BoundedJsonRail:
    return BoundedJsonRail(
        origin="https://api.github.com",
        authority_path=authority_path,
        authorization_scheme="Bearer",
        operations={
            "issues.list": RemoteOperation(
                method="GET",
                path_template="/repos/{owner}/{repo}/issues",
                path_fields=("owner", "repo"),
                query_fields=("direction", "page", "per_page", "since", "sort", "state"),
            ),
        },
        fixed_headers={"X-GitHub-Api-Version": "2022-11-28"},
        **options,
    )


class GitHubActivityConnector:
    connector_id = "github.activity"

    def __init__(
        self,
        *,
        rail: JsonRail,
        source_id: str,
        owner: str,
        repository: str,
        page_size: int = 100,
    ):
        if not callable(getattr(rail, "request", None)):
            raise ConnectorContractError("remote rail is invalid")
        self.rail = rail
        self.source_id = _source(source_id)
        self.owner = _string(owner, "github owner")
        self.repository = _string(repository, "github repository")
        self.page_size = _page_size(page_size)

    def pull(self, cursor: str | None) -> ConnectorPage:
        state = _state(cursor)
        if state["page"] is not None and (
            type(state["page"]) is not int or state["page"] < 2
        ):
            raise ConnectorContractError("connector cursor is invalid")
        page_number = state["page"] or 1
        query: dict[str, Any] = {
            "direction": "asc",
            "page": page_number,
            "per_page": self.page_size,
            "sort": "updated",
            "state": "all",
        }
        if state["watermark"] != EPOCH:
            query["since"] = state["watermark"]
        try:
            values = self.rail.request(
                "issues.list",
                path={"owner": self.owner, "repo": self.repository},
                query=query,
            )
        except RemoteApiError as error:
            _translate(error)
        records = []
        maximum = state["max_seen"]
        for raw in _items(values, "github issues response"):
            issue = _mapping(raw, "github issue")
            number = issue.get("number")
            if type(number) is not int or number <= 0:
                raise ConnectorContractError("github issue number is invalid")
            updated = _timestamp(issue.get("updated_at"), "github updated timestamp")
            maximum = _max_timestamp(maximum, updated)
            pull_request = isinstance(issue.get("pull_request"), dict)
            type_name = "pull-request" if pull_request else "issue"
            title = _string(issue.get("title"), "github title", maximum=MAX_TEXT_BYTES)
            state_name = _optional_string(issue.get("state"), "github state") or ""
            labels = []
            for raw_label in _items(issue.get("labels"), "github labels"):
                label = _mapping(raw_label, "github label")
                name = _optional_string(label.get("name"), "github label")
                if name:
                    labels.append(name)
            user = issue.get("user")
            login = (
                _optional_string(user.get("login"), "github user")
                if isinstance(user, dict)
                else None
            )
            native = f"github:{self.owner}/{self.repository}:{type_name}:{number}"
            content: dict[str, Any] = {
                "document_id": native,
                "mime_type": f"application/vnd.github.{type_name}+json",
                "name": title,
                "modified_at": updated,
                "surface": "github",
                "text": "\n".join(
                    item for item in (title, state_name, " ".join(labels), _text(issue.get("body")))
                    if item
                ),
            }
            url = _url(issue.get("html_url"))
            if url:
                content["source_url"] = url
            if login:
                content["participant_ids"] = [login]
            records.append(_record(
                native_id=native,
                occurred_at=updated,
                kind="document.v1",
                content=content,
                provenance_uri="connector://github-activity",
            ))
        has_more = len(records) == self.page_size
        return ConnectorPage(
            records=tuple(records),
            next_cursor=_cursor(
                page=page_number + 1 if has_more else None,
                watermark=state["watermark"] if has_more else maximum,
                max_seen=maximum,
            ),
            has_more=has_more,
        )


LINEAR_ISSUES_QUERY = """
query RecallIssues($team_id: ID!, $watermark: DateTimeOrDuration!, $after: String, $first: Int!) {
  issues(
    filter: {team: {id: {eq: $team_id}}, updatedAt: {gte: $watermark}}
    orderBy: updatedAt
    first: $first
    after: $after
  ) {
    nodes {
      id identifier title description url createdAt updatedAt
      state { name }
      assignee { id }
      labels { nodes { name } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
""".strip()


def linear_rail(*, authority_path: Path, **options: Any) -> BoundedJsonRail:
    return BoundedJsonRail(
        origin="https://api.linear.app",
        authority_path=authority_path,
        authorization_scheme="Bearer",
        operations={
            "issues.list": RemoteOperation(
                method="POST",
                path_template="/graphql",
                path_fields=(),
                query_fields=(),
                json_fields=("variables",),
                fixed_json={"query": LINEAR_ISSUES_QUERY},
            ),
        },
        **options,
    )


class LinearActivityConnector:
    connector_id = "linear.activity"

    def __init__(self, *, rail: JsonRail, source_id: str, team_id: str, page_size: int = 100):
        if not callable(getattr(rail, "request", None)):
            raise ConnectorContractError("remote rail is invalid")
        self.rail = rail
        self.source_id = _source(source_id)
        self.team_id = _string(team_id, "linear team")
        self.page_size = _page_size(page_size)

    def pull(self, cursor: str | None) -> ConnectorPage:
        state = _state(cursor)
        if state["page"] is not None and not isinstance(state["page"], str):
            raise ConnectorContractError("connector cursor is invalid")
        try:
            response = _mapping(self.rail.request(
                "issues.list",
                json_body={"variables": {
                    "team_id": self.team_id,
                    "watermark": state["watermark"],
                    "after": state["page"],
                    "first": self.page_size,
                }},
            ), "linear response")
        except RemoteApiError as error:
            _translate(error)
        if response.get("errors") is not None:
            _items(response["errors"], "linear errors")
            raise ConnectorUpstreamError("connector_upstream_error")
        data = _mapping(response.get("data"), "linear data")
        issues = _mapping(data.get("issues"), "linear issues")
        records = []
        maximum = state["max_seen"]
        for raw in _items(issues.get("nodes"), "linear issue nodes"):
            issue = _mapping(raw, "linear issue")
            item_id = _string(issue.get("id"), "linear issue id")
            identifier = _string(issue.get("identifier"), "linear identifier")
            title = _string(issue.get("title"), "linear title", maximum=MAX_TEXT_BYTES)
            updated = _timestamp(issue.get("updatedAt"), "linear updated timestamp")
            maximum = _max_timestamp(maximum, updated)
            state_value = issue.get("state")
            state_name = (
                _optional_string(state_value.get("name"), "linear state")
                if isinstance(state_value, dict)
                else None
            )
            labels = []
            label_value = issue.get("labels")
            if isinstance(label_value, dict):
                for raw_label in _items(label_value.get("nodes"), "linear labels"):
                    label = _mapping(raw_label, "linear label")
                    name = _optional_string(label.get("name"), "linear label")
                    if name:
                        labels.append(name)
            content: dict[str, Any] = {
                "document_id": f"linear:{item_id}",
                "mime_type": "application/vnd.linear.issue+json",
                "name": title,
                "modified_at": updated,
                "surface": "linear",
                "text": "\n".join(
                    item for item in (
                        f"{identifier} {title}",
                        state_name or "",
                        " ".join(labels),
                        _text(issue.get("description")),
                    ) if item
                ),
            }
            url = _url(issue.get("url"))
            if url:
                content["source_url"] = url
            assignee = issue.get("assignee")
            if isinstance(assignee, dict):
                assignee_id = _optional_string(assignee.get("id"), "linear assignee")
                if assignee_id:
                    content["participant_ids"] = [assignee_id]
            records.append(_record(
                native_id=f"linear:{item_id}",
                occurred_at=updated,
                kind="document.v1",
                content=content,
                provenance_uri="connector://linear-activity",
            ))
        page_info = _mapping(issues.get("pageInfo"), "linear page info")
        has_more = page_info.get("hasNextPage")
        if type(has_more) is not bool:
            raise ConnectorContractError("linear pagination is invalid")
        next_page = _optional_string(page_info.get("endCursor"), "linear page cursor")
        if has_more != (next_page is not None):
            raise ConnectorContractError("linear pagination is invalid")
        return ConnectorPage(
            records=tuple(records),
            next_cursor=_cursor(
                page=next_page,
                watermark=state["watermark"] if has_more else maximum,
                max_seen=maximum,
            ),
            has_more=has_more,
        )


def slack_rail(*, authority_path: Path, **options: Any) -> BoundedJsonRail:
    return BoundedJsonRail(
        origin="https://slack.com",
        authority_path=authority_path,
        authorization_scheme="Bearer",
        operations={
            "messages.history": RemoteOperation(
                method="GET",
                path_template="/api/conversations.history",
                path_fields=(),
                query_fields=("channel", "cursor", "inclusive", "limit", "oldest"),
            ),
        },
        **options,
    )


class SlackMessagesConnector:
    connector_id = "slack.messages"

    def __init__(self, *, rail: JsonRail, source_id: str, channel_id: str, page_size: int = 100):
        if not callable(getattr(rail, "request", None)):
            raise ConnectorContractError("remote rail is invalid")
        self.rail = rail
        self.source_id = _source(source_id)
        self.channel_id = _string(channel_id, "slack channel")
        self.page_size = _page_size(page_size)

    def pull(self, cursor: str | None) -> ConnectorPage:
        state = _state(cursor)
        if state["page"] is not None and not isinstance(state["page"], str):
            raise ConnectorContractError("connector cursor is invalid")
        query: dict[str, Any] = {
            "channel": self.channel_id,
            "inclusive": True,
            "limit": self.page_size,
        }
        if state["page"]:
            query["cursor"] = state["page"]
        elif state["watermark"] != EPOCH:
            query["oldest"] = _slack_oldest(state["watermark"])
        try:
            response = _mapping(
                self.rail.request("messages.history", query=query),
                "slack response",
            )
            if response.get("ok") is not True and response.get("error") == "invalid_cursor":
                if state["page"] is None:
                    raise ConnectorUpstreamError("connector_upstream_error")
                query.pop("cursor", None)
                if state["watermark"] != EPOCH:
                    query["oldest"] = _slack_oldest(state["watermark"])
                response = _mapping(
                    self.rail.request("messages.history", query=query),
                    "slack response",
                )
        except RemoteApiError as error:
            _translate(error)
        if response.get("ok") is not True:
            _optional_string(response.get("error"), "slack error")
            raise ConnectorUpstreamError("connector_upstream_error")
        records_by_id: dict[str, ConnectorRecordV2] = {}
        maximum = state["max_seen"]
        for raw in _items(response.get("messages"), "slack messages"):
            event = _mapping(raw, "slack message")
            event_ts = _string(event.get("ts"), "slack event timestamp")
            maximum = _max_timestamp(maximum, _slack_timestamp(event_ts, "slack event timestamp"))
            subtype = event.get("subtype")
            if subtype == "message_deleted":
                deleted_ts = _string(event.get("deleted_ts"), "slack deleted timestamp")
                records_by_id[f"slack:{deleted_ts}"] = _record(
                    native_id=f"slack:{deleted_ts}",
                    occurred_at=_slack_timestamp(event_ts, "slack event timestamp"),
                    kind="communication_message.v1",
                    deleted=True,
                    provenance_uri="connector://slack-messages",
                )
                continue
            message = event
            edited_at = None
            if subtype == "message_changed":
                message = _mapping(event.get("message"), "slack changed message")
                edited = message.get("edited")
                if isinstance(edited, dict):
                    edited_at = _slack_timestamp(edited.get("ts"), "slack edited timestamp")
            message_ts = _string(message.get("ts"), "slack message timestamp")
            sent_at = _slack_timestamp(message_ts, "slack message timestamp")
            user = _optional_string(
                message.get("user", message.get("bot_id")),
                "slack author",
            )
            content: dict[str, Any] = {
                "conversation_id": f"slack-channel:{self.channel_id}",
                "direction": "system",
                "message_id": f"slack:{message_ts}",
                "sent_at": sent_at,
                "surface": "slack",
                "text": _text(message.get("text")),
            }
            if user:
                content["author_id"] = user
                content["participant_ids"] = [user]
            if edited_at:
                content["edited_at"] = edited_at
            thread_ts = message.get("thread_ts")
            parent = (
                f"slack:{_string(thread_ts, 'slack thread timestamp')}"
                if isinstance(thread_ts, str) and thread_ts != message_ts
                else f"slack-channel:{self.channel_id}"
            )
            records_by_id[f"slack:{message_ts}"] = _record(
                native_id=f"slack:{message_ts}",
                parent=parent,
                occurred_at=sent_at,
                kind="communication_message.v1",
                content=content,
                provenance_uri="connector://slack-messages",
            )
        metadata = _mapping(response.get("response_metadata", {}), "slack response metadata")
        next_page = metadata.get("next_cursor")
        if next_page == "":
            next_page = None
        next_page = _optional_string(next_page, "slack page cursor")
        has_more = response.get("has_more")
        if type(has_more) is not bool or has_more != (next_page is not None):
            raise ConnectorContractError("slack pagination is invalid")
        return ConnectorPage(
            records=tuple(records_by_id.values()),
            next_cursor=_cursor(
                page=next_page,
                watermark=state["watermark"] if has_more else maximum,
                max_seen=maximum,
            ),
            has_more=has_more,
        )


def notion_rail(*, authority_path: Path, **options: Any) -> BoundedJsonRail:
    return BoundedJsonRail(
        origin="https://api.notion.com",
        authority_path=authority_path,
        authorization_scheme="Bearer",
        operations={
            "search.list": RemoteOperation(
                method="POST",
                path_template="/v1/search",
                path_fields=(),
                query_fields=(),
                json_fields=("page_size", "start_cursor"),
                fixed_json={
                    "sort": {
                        "direction": "ascending",
                        "timestamp": "last_edited_time",
                    },
                },
            ),
        },
        fixed_headers={"Notion-Version": "2026-03-11"},
        **options,
    )


def _notion_title(properties: Any) -> str:
    if not isinstance(properties, dict):
        return "Untitled"
    for key in sorted(properties):
        prop = properties[key]
        if not isinstance(prop, dict) or prop.get("type") != "title":
            continue
        parts = []
        for raw in _items(prop.get("title"), "notion title"):
            item = _mapping(raw, "notion title item")
            plain = _optional_string(
                item.get("plain_text"),
                "notion title text",
                maximum=MAX_TEXT_BYTES,
            )
            if plain:
                parts.append(plain)
        return _text("".join(parts)) or "Untitled"
    return "Untitled"


class NotionWorkspaceConnector:
    connector_id = "notion.workspace"

    def __init__(self, *, rail: JsonRail, source_id: str, page_size: int = 100):
        if not callable(getattr(rail, "request", None)):
            raise ConnectorContractError("remote rail is invalid")
        self.rail = rail
        self.source_id = _source(source_id)
        self.page_size = _page_size(page_size)

    def pull(self, cursor: str | None) -> ConnectorPage:
        state = _state(cursor)
        if state["page"] is not None and not isinstance(state["page"], str):
            raise ConnectorContractError("connector cursor is invalid")
        body: dict[str, Any] = {"page_size": self.page_size}
        if state["page"]:
            body["start_cursor"] = state["page"]
        try:
            response = _mapping(
                self.rail.request("search.list", json_body=body),
                "notion response",
            )
        except RemoteApiError as error:
            _translate(error)
        if response.get("object") != "list":
            raise ConnectorContractError("notion response is invalid")
        records = []
        maximum = state["max_seen"]
        for raw in _items(response.get("results"), "notion results"):
            item = _mapping(raw, "notion result")
            object_type = _string(item.get("object"), "notion object type")
            if object_type not in {"page", "data_source"}:
                raise ConnectorContractError("notion object type is invalid")
            item_id = _string(item.get("id"), "notion object id")
            updated = _timestamp(item.get("last_edited_time"), "notion edited timestamp")
            maximum = _max_timestamp(maximum, updated)
            native = f"notion:{item_id}"
            if item.get("in_trash") is True:
                records.append(_record(
                    native_id=native,
                    occurred_at=updated,
                    kind="document.v1",
                    deleted=True,
                    provenance_uri="connector://notion-workspace",
                ))
                continue
            title = _notion_title(item.get("properties"))
            content: dict[str, Any] = {
                "document_id": native,
                "mime_type": f"application/vnd.notion.{object_type}+json",
                "modified_at": updated,
                "name": title,
                "surface": "notion",
                "text": title,
            }
            url = _url(item.get("url"))
            if url:
                content["source_url"] = url
            records.append(_record(
                native_id=native,
                occurred_at=updated,
                kind="document.v1",
                content=content,
                provenance_uri="connector://notion-workspace",
            ))
        has_more = response.get("has_more")
        if type(has_more) is not bool:
            raise ConnectorContractError("notion pagination is invalid")
        next_page = _optional_string(response.get("next_cursor"), "notion page cursor")
        if has_more != (next_page is not None):
            raise ConnectorContractError("notion pagination is invalid")
        return ConnectorPage(
            records=tuple(records),
            next_cursor=_cursor(
                page=next_page,
                watermark=state["watermark"] if has_more else maximum,
                max_seen=maximum,
            ),
            has_more=has_more,
        )


__all__ = [
    "GitHubActivityConnector",
    "LinearActivityConnector",
    "NotionWorkspaceConnector",
    "SlackMessagesConnector",
    "github_rail",
    "linear_rail",
    "notion_rail",
    "slack_rail",
]
