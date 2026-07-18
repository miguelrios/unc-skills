from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

LATEST_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = frozenset(
    {"2025-03-26", "2025-06-18", LATEST_PROTOCOL_VERSION}
)
REQUEST_METHODS = ("initialize", "ping", "tools/list", "tools/call")
NOTIFICATION_METHODS = ("notifications/initialized",)


@dataclass(frozen=True)
class McpProtocolError(Exception):
    code: int
    message: str


def _object(value: Any, name: str) -> dict:
    if not isinstance(value, dict):
        raise McpProtocolError(-32602, f"{name} must be an object")
    return value


def _string(value: Any, name: str, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise McpProtocolError(-32602, f"{name} must be a non-empty string")
    return value


def _integer(
    value: Any,
    name: str,
    *,
    default: int,
    minimum: int = 0,
    maximum: int = 100,
) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise McpProtocolError(-32602, f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise McpProtocolError(
            -32602, f"{name} must be between {minimum} and {maximum}"
        )
    return value


def _boolean(value: Any, name: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise McpProtocolError(-32602, f"{name} must be a boolean")
    return value


def _date_time(value: Any, name: str) -> str:
    text = _string(value, name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise McpProtocolError(
            -32602,
            f"{name} must be a timezone-aware ISO-8601 timestamp",
        ) from None
    if parsed.tzinfo is None:
        raise McpProtocolError(
            -32602,
            f"{name} must be a timezone-aware ISO-8601 timestamp",
        )
    return text


READ_TOOLS = (
    {
        "name": "recall_related",
        "description": (
            "Find Recall evidence related to a working directory or branch. "
            "Use this to recover nearby work context when exact terms are unknown."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string"},
                "branch": {"type": "string"},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 10,
                },
                "mains_only": {"type": "boolean", "default": False},
                "fast": {"type": "boolean", "default": False},
            },
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "recall_search",
        "description": (
            "Search the owner's authorized Recall evidence using a natural-language "
            "question. Results include stable recall:// receipts for follow-up."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "maxLength": 8192,
                    "description": "A natural-language question or search phrase.",
                },
                "filters": {"type": "object", "default": {}},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 10,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "recall_show",
        "description": (
            "Resolve a recall:// receipt and return its authorized surrounding context."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "around": {
                    "type": "string",
                    "format": "date-time",
                },
                "tail": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "default": 0,
                },
                "prompts": {"type": "boolean", "default": False},
            },
            "required": ["target"],
            "additionalProperties": False,
        },
        "outputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True},
    },
)
WRITE_TOOLS = (
    {
        "name": "recall_capture",
        "description": (
            "Deliberately save one user-selected memory. Its source and origin "
            "are bound by the host credential, not by model arguments."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "schema_version",
                "title",
                "body",
                "occurred_at",
                "provenance",
            ],
            "properties": {
                "schema_version": {"const": 1},
                "title": {"type": "string", "minLength": 1, "maxLength": 500},
                "body": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 32000,
                },
                "occurred_at": {"type": "string", "format": "date-time"},
                "tags": {
                    "type": "array",
                    "maxItems": 20,
                    "uniqueItems": True,
                    "items": {
                        "type": "string",
                        "maxLength": 64,
                        "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$",
                    },
                },
                "provenance": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["uri"],
                    "properties": {
                        "uri": {
                            "type": "string",
                            "format": "uri",
                            "pattern": "^(?:https|manual|connector|export):",
                        }
                    },
                },
            },
        },
        "outputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "recall_forget",
        "description": (
            "Forget one prior deliberate capture from this credential's exact "
            "capture source."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["receipt"],
            "properties": {
                "receipt": {"type": "string", "minLength": 1},
            },
        },
        "outputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    },
)


def _write_enabled(principal: dict) -> bool:
    return (
        "write" in principal.get("scopes", ())
        and isinstance(principal.get("source_id"), str)
        and isinstance(principal.get("principal_id"), str)
        and isinstance(principal.get("capture_origin"), str)
    )


def _tools_for(principal: dict) -> tuple[dict, ...]:
    return READ_TOOLS + (WRITE_TOOLS if _write_enabled(principal) else ())


def _reject_extra(arguments: dict, allowed: frozenset[str]) -> None:
    unknown = sorted(set(arguments) - allowed)
    if unknown:
        raise McpProtocolError(-32602, f"unknown tool arguments: {', '.join(unknown)}")


def _call_tool(store, principal: dict, name: str, arguments: dict) -> dict:
    authorized_source = principal.get(
        "authorized_sources",
        principal.get("source_id"),
    )
    if name == "recall_search":
        _reject_extra(arguments, frozenset({"query", "filters", "limit"}))
        query = _string(arguments.get("query"), "query")
        if len(query) > 8192:
            raise McpProtocolError(-32602, "query must be at most 8192 characters")
        filters = _object(arguments.get("filters", {}), "filters")
        limit = _integer(
            arguments.get("limit"),
            "limit",
            default=10,
            minimum=1,
            maximum=20,
        )
        return store.search(query, filters, limit, authorized_source)
    if name == "recall_show":
        _reject_extra(arguments, frozenset({"target", "around", "tail", "prompts"}))
        target = _string(arguments.get("target"), "target")
        around = (
            _date_time(arguments["around"], "around")
            if "around" in arguments
            else None
        )
        tail = _integer(arguments.get("tail"), "tail", default=0, minimum=0)
        if around is not None and tail > 0:
            raise McpProtocolError(
                -32602,
                "around and positive tail are mutually exclusive",
            )
        prompts = _boolean(arguments.get("prompts"), "prompts")
        result = store.show(
            target,
            around=around,
            tail=tail,
            prompts=prompts,
            authorized_source=authorized_source,
        )
        if result is None:
            raise McpProtocolError(-32602, "receipt not found")
        return result
    if name == "recall_related":
        _reject_extra(
            arguments,
            frozenset({"cwd", "branch", "limit", "mains_only", "fast"}),
        )
        cwd = _string(arguments.get("cwd"), "cwd", required=False)
        branch = _string(arguments.get("branch"), "branch", required=False)
        limit = _integer(
            arguments.get("limit"),
            "limit",
            default=10,
            minimum=1,
            maximum=20,
        )
        mains_only = _boolean(arguments.get("mains_only"), "mains_only")
        fast = _boolean(arguments.get("fast"), "fast")
        return store.related(
            cwd=cwd,
            branch=branch,
            limit=limit,
            mains_only=mains_only,
            fast=fast,
            authorized_source=authorized_source,
        )
    if name == "recall_capture":
        if not _write_enabled(principal):
            raise McpProtocolError(-32602, "unknown tool")
        _reject_extra(
            arguments,
            frozenset({
                "schema_version",
                "title",
                "body",
                "occurred_at",
                "tags",
                "provenance",
            }),
        )
        return store.capture(principal, arguments)
    if name == "recall_forget":
        if not _write_enabled(principal):
            raise McpProtocolError(-32602, "unknown tool")
        _reject_extra(arguments, frozenset({"receipt"}))
        receipt = _string(arguments.get("receipt"), "receipt")
        return store.forget_capture(principal, receipt)
    raise McpProtocolError(-32602, "unknown tool")


def _tool_result(value: dict) -> dict:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(value, default=str, sort_keys=True),
            }
        ],
        "structuredContent": value,
        "isError": False,
    }


def dispatch(store, principal: dict, message: Any) -> dict | None:
    request = _object(message, "request")
    request_id = request.get("id")
    if request.get("jsonrpc") != "2.0":
        raise McpProtocolError(-32600, "invalid JSON-RPC version")
    method = _string(request.get("method"), "method")
    params = _object(request.get("params", {}), "params")

    if "id" not in request:
        if method == "notifications/initialized":
            return None
        raise McpProtocolError(-32600, "unsupported notification")

    if method == "initialize":
        requested = params.get("protocolVersion")
        selected = (
            requested
            if requested in SUPPORTED_PROTOCOL_VERSIONS
            else LATEST_PROTOCOL_VERSION
        )
        result = {
            "protocolVersion": selected,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": "recall",
                "version": "1",
                "description": "Private, source-scoped personal evidence retrieval.",
            },
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": list(_tools_for(principal))}
    elif method == "tools/call":
        name = _string(params.get("name"), "name")
        if name not in {tool["name"] for tool in _tools_for(principal)}:
            raise McpProtocolError(-32602, "unknown tool")
        arguments = _object(params.get("arguments", {}), "arguments")
        result = _tool_result(_call_tool(store, principal, name, arguments))
    else:
        raise McpProtocolError(-32601, "method not found")

    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_response(error: McpProtocolError, request_id: Any = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": error.code, "message": error.message},
    }
