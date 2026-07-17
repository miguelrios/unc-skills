from __future__ import annotations

import http.client
import json
import os
import sys
import threading
import types
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

SERVER = Path(__file__).resolve().parents[2] / "server"
sys.path.insert(0, str(SERVER))

try:
    import psycopg  # noqa: F401
except ModuleNotFoundError:
    psycopg = types.ModuleType("psycopg")
    psycopg_rows = types.ModuleType("psycopg.rows")
    psycopg_rows.dict_row = object()
    psycopg.rows = psycopg_rows
    sys.modules["psycopg"] = psycopg
    sys.modules["psycopg.rows"] = psycopg_rows

from recall_server.app import Handler  # noqa: E402


class FakeStore:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def authenticate_bearer(self, token: str, scope: str) -> dict | None:
        self.calls.append(("authenticate", token, scope))
        if token != "synthetic-read-token" or scope != "read":
            return None
        return {
            "name": "synthetic-grep-agent",
            "source_id": "synthetic:grep-capture",
            "principal_id": "synthetic-owner",
            "scopes": ["read"],
        }

    def authorized_source_ids(self, principal_id: str) -> list[str]:
        self.calls.append(("authorized_sources", principal_id))
        return ["synthetic:codex", "synthetic:cowork"]

    def search(self, query, filters, limit, authorized_source):
        self.calls.append(("search", query, filters, limit, authorized_source))
        return {
            "query": query,
            "results": [
                {
                    "receipt": "recall://synthetic:codex/item-1?rev=1",
                    "text": "Synthetic retrieval result",
                }
            ],
        }

    def show(self, target, *, around, tail, prompts, authorized_source):
        self.calls.append(("show", target, around, tail, prompts, authorized_source))
        return {
            "receipt": target,
            "text": "Synthetic detail",
        }

    def related(self, *, cwd, branch, limit, mains_only, fast, authorized_source):
        self.calls.append(
            (
                "related",
                cwd,
                branch,
                limit,
                mains_only,
                fast,
                authorized_source,
            )
        )
        return {"results": []}


class FailingStore(FakeStore):
    def search(self, query, filters, limit, authorized_source):
        raise RuntimeError("private payload must not escape")


class McpHttpServer:
    def __init__(self, store: FakeStore) -> None:
        Handler.store = store
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(
        self,
        method: str,
        body: dict | None = None,
        *,
        path: str = "/mcp",
        token: str | None = "synthetic-read-token",
        origin: str | None = None,
        protocol: str | None = None,
        accept: str | None = "application/json, text/event-stream",
    ) -> tuple[int, dict[str, str], bytes]:
        headers: dict[str, str] = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if origin is not None:
            headers["Origin"] = origin
        if protocol is not None:
            headers["MCP-Protocol-Version"] = protocol
        if accept is not None:
            headers["Accept"] = accept
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(payload))
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=2
        )
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        result_headers = {key.casefold(): value for key, value in response.getheaders()}
        connection.close()
        return response.status, result_headers, raw


def request(method: str, request_id: int = 1, params: dict | None = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }


class RemoteMcpContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeStore()
        self.environment = mock.patch.dict(
            os.environ,
            {
                "RECALL_AUTH_REQUIRED": "1",
                "RECALL_MCP_ALLOWED_ORIGINS": "https://grep.synthetic.invalid",
            },
            clear=False,
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()

    def test_initialize_and_closed_deterministic_tool_list(self) -> None:
        with McpHttpServer(self.store) as server:
            status, headers, raw = server.request(
                "POST",
                request(
                    "initialize",
                    params={
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "synthetic-grep", "version": "1"},
                    },
                ),
            )
            self.assertEqual(status, 200)
            self.assertEqual(headers["content-type"], "application/json")
            initialized = json.loads(raw)
            self.assertEqual(initialized["result"]["protocolVersion"], "2025-11-25")
            self.assertEqual(
                initialized["result"]["capabilities"],
                {"tools": {"listChanged": False}},
            )

            status, _, raw = server.request(
                "POST",
                request("tools/list", request_id=2),
                protocol="2025-11-25",
            )
            self.assertEqual(status, 200)
            names = [tool["name"] for tool in json.loads(raw)["result"]["tools"]]
            self.assertEqual(
                names,
                ["recall_related", "recall_search", "recall_show"],
            )

    def test_search_is_natural_language_and_source_scoped(self) -> None:
        with McpHttpServer(self.store) as server:
            status, _, raw = server.request(
                "POST",
                request(
                    "tools/call",
                    params={
                        "name": "recall_search",
                        "arguments": {
                            "query": "What did we decide about the synthetic rollout?",
                            "limit": 5,
                        },
                    },
                ),
                protocol="2025-11-25",
            )
        self.assertEqual(status, 200)
        result = json.loads(raw)["result"]
        self.assertFalse(result.get("isError", False))
        self.assertEqual(
            result["structuredContent"]["results"][0]["receipt"],
            "recall://synthetic:codex/item-1?rev=1",
        )
        self.assertIn(
            (
                "search",
                "What did we decide about the synthetic rollout?",
                {},
                5,
                ["synthetic:codex", "synthetic:cowork"],
            ),
            self.store.calls,
        )

    def test_public_profile_hides_every_non_mcp_route_before_store_io(self) -> None:
        self.environment.stop()
        self.environment = mock.patch.dict(
            os.environ,
            {
                "RECALL_AUTH_REQUIRED": "1",
                "RECALL_HTTP_PROFILE": "public-mcp",
                "RECALL_TRUST_TAILSCALE_HEADERS": "0",
            },
            clear=False,
        )
        self.environment.start()
        with McpHttpServer(self.store) as server:
            for method, path in (
                ("GET", "/metrics"),
                ("GET", "/v1/doctor"),
                ("GET", "/v1/receipts/resolve?receipt=synthetic"),
                ("POST", "/v1/search"),
                ("POST", "/v1/ingest/batches"),
                ("DELETE", "/mcp"),
            ):
                with self.subTest(method=method, path=path):
                    self.store.calls.clear()
                    status, _, _ = server.request(method, path=path)
                    self.assertEqual(status, 404)
                    self.assertEqual(self.store.calls, [])

    def test_principal_without_grants_is_never_treated_as_unrestricted(self) -> None:
        self.store.authorized_source_ids = mock.Mock(return_value=[])
        with McpHttpServer(self.store) as server:
            status, _, _ = server.request(
                "POST",
                request(
                    "tools/call",
                    params={
                        "name": "recall_search",
                        "arguments": {"query": "synthetic denied"},
                    },
                ),
                protocol="2025-11-25",
            )
        self.assertEqual(status, 200)
        search_call = next(call for call in self.store.calls if call[0] == "search")
        self.assertEqual(search_call[-1], [])

    def test_remote_mcp_is_read_only_and_unknown_tools_do_not_touch_store(self) -> None:
        with McpHttpServer(self.store) as server:
            status, _, raw = server.request(
                "POST",
                request(
                    "tools/call",
                    params={"name": "recall_ingest", "arguments": {}},
                ),
                protocol="2025-11-25",
            )
        self.assertEqual(status, 200)
        response = json.loads(raw)
        self.assertEqual(response["error"]["code"], -32602)
        self.assertNotIn("ingest", {call[0] for call in self.store.calls})

    def test_auth_origin_protocol_and_accept_fail_closed_before_tool_io(self) -> None:
        cases = [
            {"token": None, "expected": 401},
            {
                "origin": "https://attacker.synthetic.invalid",
                "expected": 403,
            },
            {"protocol": "2024-11-05", "expected": 400},
            {"accept": "application/json", "expected": 406},
        ]
        for case in cases:
            with self.subTest(case=case):
                self.store.calls.clear()
                options = {
                    key: value for key, value in case.items() if key not in {"expected"}
                }
                with McpHttpServer(self.store) as server:
                    status, _, _ = server.request(
                        "POST",
                        request(
                            "tools/call",
                            params={
                                "name": "recall_search",
                                "arguments": {"query": "synthetic"},
                            },
                        ),
                        **options,
                    )
                self.assertEqual(status, case["expected"])
                self.assertNotIn("search", {call[0] for call in self.store.calls})

    def test_get_explicitly_declines_sse_and_requires_auth(self) -> None:
        with McpHttpServer(self.store) as server:
            status, headers, raw = server.request("GET", accept="text/event-stream")
            self.assertEqual(status, 405)
            self.assertEqual(headers["allow"], "POST")
            self.assertEqual(raw, b"")

            status, _, _ = server.request("GET", token=None, accept="text/event-stream")
            self.assertEqual(status, 401)

    def test_stateless_call_survives_server_restart_without_session_affinity(
        self,
    ) -> None:
        with McpHttpServer(self.store) as first:
            initialized, headers, _ = first.request(
                "POST",
                request(
                    "initialize",
                    params={
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "clientInfo": {"name": "synthetic-grep", "version": "1"},
                    },
                ),
            )
            self.assertEqual(initialized, 200)
            self.assertNotIn("mcp-session-id", headers)

        with McpHttpServer(self.store) as restarted:
            status, _, raw = restarted.request(
                "POST",
                request(
                    "tools/call",
                    params={
                        "name": "recall_search",
                        "arguments": {"query": "synthetic restart"},
                    },
                ),
                protocol="2025-11-25",
            )
        self.assertEqual(status, 200)
        self.assertIn("result", json.loads(raw))

    def test_notifications_are_accepted_without_response_body(self) -> None:
        notification = {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        }
        with McpHttpServer(self.store) as server:
            status, _, raw = server.request("POST", notification, protocol="2025-11-25")
        self.assertEqual(status, 202)
        self.assertEqual(raw, b"")

    def test_backend_failures_are_content_free_json_rpc_errors(self) -> None:
        with McpHttpServer(FailingStore()) as server:
            status, _, raw = server.request(
                "POST",
                request(
                    "tools/call",
                    params={
                        "name": "recall_search",
                        "arguments": {"query": "synthetic failure"},
                    },
                ),
                protocol="2025-11-25",
            )
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(raw)["error"],
            {"code": -32603, "message": "tool execution failed"},
        )
        self.assertNotIn(b"private payload", raw)


if __name__ == "__main__":
    unittest.main()
