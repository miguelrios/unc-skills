"""Minimal stdlib MCP-over-stdio server for deliberate Recall capture."""

from __future__ import annotations

import json
from typing import Any, TextIO

from client.capture import CaptureContractError, ORIGIN, validate_capture


PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = {PROTOCOL_VERSION, "2025-06-18"}


class McpProtocolError(ValueError):
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


CAPTURE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "title", "body", "occurred_at", "provenance"],
    "properties": {
        "schema_version": {"const": 1},
        "title": {"type": "string", "minLength": 1, "maxLength": 500},
        "body": {"type": "string", "minLength": 1, "maxLength": 1000000},
        "occurred_at": {"type": "string", "format": "date-time"},
        "tags": {"type": "array", "maxItems": 20, "uniqueItems": True, "items": {"type": "string"}},
        "provenance": {
            "type": "object", "additionalProperties": False, "required": ["uri"],
            "properties": {"uri": {"type": "string"}},
        },
    },
}


TOOLS = (
    {
        "name": "recall_capture",
        "description": "Deliberately save one user-selected evidence item and return its receipt.",
        "inputSchema": CAPTURE_SCHEMA,
    },
    {
        "name": "recall_forget",
        "description": "Forget one prior capture by its exact receipt.",
        "inputSchema": {
            "type": "object", "additionalProperties": False, "required": ["receipt"],
            "properties": {"receipt": {"type": "string", "minLength": 1}},
        },
    },
    {
        "name": "recall_doctor",
        "description": "Return content-free health for this exact capture source.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}},
    },
)


def _result(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(value, sort_keys=True, separators=(",", ":"))}],
        "isError": False,
    }


class McpServer:
    def __init__(self, backend, *, capture_origin: str):
        if not isinstance(capture_origin, str) or not ORIGIN.fullmatch(capture_origin):
            raise CaptureContractError("capture origin is invalid")
        self.backend = backend
        self.capture_origin = capture_origin

    def handle(self, request: Any) -> dict[str, Any] | None:
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            raise McpProtocolError(-32600, "request_invalid")
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})
        if method == "notifications/initialized":
            return None
        if method == "initialize":
            if (
                not isinstance(params, dict)
                or not isinstance(params.get("protocolVersion"), str)
                or not isinstance(params.get("capabilities"), dict)
                or not isinstance(params.get("clientInfo"), dict)
            ):
                raise McpProtocolError(-32602, "initialize_invalid")
            negotiated = (
                params["protocolVersion"]
                if params["protocolVersion"] in SUPPORTED_PROTOCOL_VERSIONS
                else PROTOCOL_VERSION
            )
            return {"jsonrpc": "2.0", "id": request_id, "result": {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "recall-capture", "version": "1.0.0"},
            }}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": list(TOOLS)}}
        if method != "tools/call" or not isinstance(params, dict) or set(params) != {"name", "arguments"}:
            raise McpProtocolError(-32601, "method_unknown")
        name = params["name"]
        arguments = params["arguments"]
        try:
            if name == "recall_capture":
                if not isinstance(arguments, dict) or "origin" in arguments:
                    raise CaptureContractError("capture schema is invalid")
                value = self.backend.capture(validate_capture({
                    **arguments, "origin": self.capture_origin,
                }))
            elif name == "recall_forget":
                if not isinstance(arguments, dict) or set(arguments) != {"receipt"} or not isinstance(arguments["receipt"], str):
                    raise CaptureContractError("forget schema is invalid")
                value = self.backend.forget(arguments["receipt"])
            elif name == "recall_doctor":
                if arguments != {}:
                    raise CaptureContractError("doctor schema is invalid")
                value = self.backend.doctor()
            else:
                raise McpProtocolError(-32601, "tool_unknown")
        except McpProtocolError:
            raise
        except Exception as error:
            if isinstance(error, CaptureContractError):
                raise McpProtocolError(-32602, "capture_invalid") from None
            raise McpProtocolError(-32000, "capture_unavailable") from None
        return {"jsonrpc": "2.0", "id": request_id, "result": _result(value)}


def serve(server: McpServer, input_stream: TextIO, output_stream: TextIO, error_stream: TextIO) -> None:
    for line in input_stream:
        if not line.strip():
            continue
        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get("id") if isinstance(request, dict) else None
            response = server.handle(request)
        except json.JSONDecodeError:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "json_invalid"}}
        except McpProtocolError as error:
            response = {"jsonrpc": "2.0", "id": request_id, "error": {"code": error.code, "message": error.message}}
        except Exception:
            response = {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": "capture_unavailable"}}
        if response is not None:
            output_stream.write(json.dumps(response, sort_keys=True, separators=(",", ":")) + "\n")
            output_stream.flush()
