from __future__ import annotations

import hashlib
import io
import json
import unittest
from pathlib import Path
from unittest import mock

from client import cli as client_cli
from client.capture import CaptureClient, CaptureContractError
from client.mcp import McpProtocolError, McpServer, serve
from privacy.policy import PrivacyPolicy


FIXTURES = Path(__file__).with_name("capture_v1")


class FakeResponse:
    def __init__(self, body: dict):
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


class CaptureContractTest(unittest.TestCase):
    def rows(self) -> list[dict]:
        return [json.loads(line) for line in (FIXTURES / "corpus.jsonl").read_text().splitlines()]

    def client(self, *, privacy: PrivacyPolicy | None = None) -> CaptureClient:
        return CaptureClient(
            endpoint="https://brain.example.ts.net", token="synthetic-transport-token",
            source_id="capture:mac:synthetic", principal_id="owner",
            visibility="private", privacy=privacy,
        )

    def test_frozen_manifest_and_closed_capture_schema(self) -> None:
        manifest = json.loads((FIXTURES / "manifest.json").read_text())
        self.assertEqual(
            hashlib.sha256((FIXTURES / "corpus.jsonl").read_bytes()).hexdigest(),
            manifest["sha256"]["corpus.jsonl"],
        )
        rows = self.rows()
        self.assertEqual(manifest["expected"], {
            "cases": 8, "valid": 4, "invalid": 4, "origins": 4,
            "privacy_canaries": 1,
        })
        accepted = 0
        for row in rows:
            if row["valid"]:
                normalized = self.client().validate(row["capture"])
                self.assertEqual(normalized["schema_version"], 1)
                accepted += 1
            else:
                with self.assertRaises(CaptureContractError):
                    self.client().validate(row["capture"])
        self.assertEqual(accepted, 4)

    def test_exact_retry_is_one_deterministic_event_and_same_receipt(self) -> None:
        capture = self.rows()[0]["capture"]
        requests = []

        def open_request(request, **_kwargs):
            requests.append(request)
            event = json.loads(request.data)["events"][0]
            return FakeResponse({
                "status": "committed", "inserted": 1 if len(requests) == 1 else 0,
                "duplicate_events": 0 if len(requests) == 1 else 1,
                "receipts": [f"recall://{event['source_id']}/{event['native_id']}?rev=1"],
                "replay": len(requests) > 1,
            })

        with mock.patch("urllib.request.urlopen", side_effect=open_request):
            first = self.client().capture(capture)
            second = self.client().capture(capture)
        self.assertEqual(first["native_id"], second["native_id"])
        self.assertEqual(first["receipt"], second["receipt"])
        self.assertEqual(requests[0].get_header("Idempotency-key"), requests[1].get_header("Idempotency-key"))
        self.assertEqual(requests[0].data, requests[1].data)
        self.assertNotIn(b"synthetic-transport-token", requests[0].data)

    def test_privacy_scrub_and_drop_precede_request_without_echo(self) -> None:
        capture = self.rows()[2]["capture"]
        canary = "synthetic-capture-secret-canary-101"
        requests = []

        def open_request(request, **_kwargs):
            requests.append(request)
            event = json.loads(request.data)["events"][0]
            return FakeResponse({
                "status": "committed", "inserted": 1, "duplicate_events": 0,
                "receipts": [f"recall://{event['source_id']}/{event['native_id']}?rev=1"],
                "replay": False,
            })

        with mock.patch("urllib.request.urlopen", side_effect=open_request):
            scrubbed = self.client(privacy=PrivacyPolicy(mode="scrub")).capture(capture)
        self.assertEqual(len(requests), 1)
        self.assertNotIn(canary, requests[0].data.decode())
        self.assertIn("Keep safe Cowork context", requests[0].data.decode())
        self.assertNotIn(canary, json.dumps(scrubbed))

        with mock.patch("urllib.request.urlopen") as opened:
            dropped = self.client(privacy=PrivacyPolicy(mode="drop")).capture(capture)
        opened.assert_not_called()
        self.assertEqual(dropped["status"], "privacy_filtered")
        self.assertNotIn(canary, json.dumps(dropped))

    def test_oversized_capture_fails_before_request(self) -> None:
        capture = {**self.rows()[0]["capture"], "body": "x" * 1_000_001}
        with mock.patch("urllib.request.urlopen") as opened:
            with self.assertRaises(CaptureContractError):
                self.client().capture(capture)
        opened.assert_not_called()


class FakeCapture:
    def __init__(self):
        self.calls = []

    def capture(self, arguments):
        self.calls.append(("capture", arguments))
        return {"status": "committed", "receipt": "recall://capture:mac:synthetic/capture_abc?rev=1"}

    def forget(self, receipt):
        self.calls.append(("forget", receipt))
        return {"status": "committed", "receipt": "recall://capture:mac:synthetic/capture_abc?rev=2"}

    def doctor(self):
        self.calls.append(("doctor", None))
        return {"status": "ok", "source_id": "capture:mac:synthetic"}


class McpProtocolTest(unittest.TestCase):
    @staticmethod
    def initialize(request_id: int = 1, version: str = "2025-11-25") -> dict:
        return {
            "jsonrpc": "2.0", "id": request_id, "method": "initialize",
            "params": {
                "protocolVersion": version, "capabilities": {},
                "clientInfo": {"name": "synthetic-client", "version": "1.0.0"},
            },
        }

    def test_initialize_list_and_three_tools_are_closed(self) -> None:
        backend = FakeCapture()
        server = McpServer(backend, capture_origin="synthetic-client")
        initialized = server.handle(self.initialize())
        self.assertEqual(initialized["result"]["protocolVersion"], "2025-11-25")
        compatible = server.handle(self.initialize(9, "2025-06-18"))
        self.assertEqual(compatible["result"]["protocolVersion"], "2025-06-18")
        listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        tools = {tool["name"]: tool for tool in listed["result"]["tools"]}
        self.assertEqual(set(tools), {"recall_capture", "recall_forget", "recall_doctor"})
        self.assertFalse(tools["recall_capture"]["inputSchema"].get("additionalProperties", True))

        capture = json.loads((FIXTURES / "corpus.jsonl").read_text().splitlines()[0])["capture"]
        arguments = {key: value for key, value in capture.items() if key != "origin"}
        called = server.handle({
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "recall_capture", "arguments": arguments},
        })
        rendered = json.dumps(called)
        self.assertIn("recall://", rendered)
        self.assertNotIn(capture["body"], rendered)

    def test_capture_origin_is_bound_by_host_and_cannot_be_spoofed(self) -> None:
        backend = FakeCapture()
        server = McpServer(backend, capture_origin="openai-codex")
        listed = server.handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {},
        })
        schema = next(
            tool["inputSchema"] for tool in listed["result"]["tools"]
            if tool["name"] == "recall_capture"
        )
        self.assertNotIn("origin", schema["properties"])
        self.assertNotIn("origin", schema["required"])

        capture = json.loads((FIXTURES / "corpus.jsonl").read_text().splitlines()[0])["capture"]
        arguments = {key: value for key, value in capture.items() if key != "origin"}
        server.handle({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "recall_capture", "arguments": arguments},
        })
        self.assertEqual(backend.calls[-1][1]["origin"], "openai-codex")

        with self.assertRaisesRegex(McpProtocolError, "capture_invalid"):
            server.handle({
                "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {
                    "name": "recall_capture",
                    "arguments": {**arguments, "origin": "anthropic-claude"},
                },
            })
        self.assertEqual(len(backend.calls), 1)

    def test_invalid_bound_origin_fails_before_authority_or_network(self) -> None:
        with self.assertRaises(CaptureContractError):
            McpServer(FakeCapture(), capture_origin="caller supplied origin")
        arguments = [
            "recall-brain", "mcp-serve",
            "--endpoint", "https://brain.example.ts.net",
            "--source-id", "capture:mac:synthetic",
            "--capture-origin", "caller supplied origin",
            "--visibility", "private",
            "--token-file", "/synthetic/private-reference.json",
            "--privacy-mode", "drop",
        ]
        errors = io.StringIO()
        with mock.patch("sys.argv", arguments), mock.patch("sys.stderr", errors), \
             mock.patch("client.cli.load_file_token") as loaded, \
             mock.patch("urllib.request.urlopen") as opened, \
             self.assertRaises(SystemExit):
            client_cli.main()
        loaded.assert_not_called()
        opened.assert_not_called()
        self.assertNotIn("/synthetic/private-reference.json", errors.getvalue())

    def test_stdio_protocol_never_echoes_invalid_sensitive_arguments(self) -> None:
        canary = "synthetic-mcp-invalid-canary-102"
        requests = "\n".join([
            json.dumps(self.initialize()),
            json.dumps({
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "recall_capture", "arguments": {"body": canary}},
            }),
        ]) + "\n"
        output = io.StringIO()
        errors = io.StringIO()
        serve(
            McpServer(FakeCapture(), capture_origin="synthetic-client"),
            io.StringIO(requests), output, errors,
        )
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertIn("error", lines[1])
        self.assertNotIn(canary, output.getvalue())
        self.assertNotIn(canary, errors.getvalue())

    def test_config_preview_is_reference_only_read_only_and_private_by_default(self) -> None:
        arguments = [
            "recall-brain", "mcp-config-preview",
            "--endpoint", "https://brain.example.ts.net",
            "--source-id", "capture:mac:synthetic",
            "--capture-origin", "openai-codex",
            "--keychain-service", "ai.parcha.recall.synthetic",
            "--keychain-account", "capture:mac:synthetic",
            "--privacy-mode", "scrub",
        ]
        output = io.StringIO()
        with mock.patch("sys.argv", arguments), mock.patch("sys.stdout", output), \
             mock.patch("client.cli.load_keychain_token") as keychain, \
             mock.patch("urllib.request.urlopen") as opened:
            client_cli.main()
        keychain.assert_not_called()
        opened.assert_not_called()
        preview = json.loads(output.getvalue())
        self.assertEqual(preview["network_requests"], 0)
        self.assertEqual(preview["writes"], 0)
        server = preview["mcpServers"]["recall"]
        rendered = json.dumps(server)
        self.assertIn("mcp-serve", rendered)
        self.assertIn("capture:mac:synthetic", rendered)
        self.assertIn("openai-codex", rendered)
        self.assertIn("ai.parcha.recall.synthetic", rendered)
        self.assertIn("private", rendered)
        self.assertNotIn("synthetic-transport-token", rendered)

    def test_mcp_serve_loads_credential_only_inside_backend_process(self) -> None:
        arguments = [
            "recall-brain", "mcp-serve",
            "--endpoint", "https://brain.example.ts.net",
            "--source-id", "capture:mac:synthetic",
            "--capture-origin", "openai-codex",
            "--visibility", "private",
            "--token-file", "/synthetic/private-reference.json",
            "--privacy-mode", "drop",
        ]
        with mock.patch("sys.argv", arguments), \
             mock.patch("client.cli.load_file_token", return_value="synthetic-secret") as loaded, \
             mock.patch("client.cli.serve_mcp") as served:
            client_cli.main()
        loaded.assert_called_once()
        backend = served.call_args.args[0].backend
        self.assertEqual(backend.source_id, "capture:mac:synthetic")
        self.assertEqual(backend.visibility, "private")
        self.assertEqual(backend.privacy.mode, "drop")
        self.assertEqual(served.call_args.args[0].capture_origin, "openai-codex")


if __name__ == "__main__":
    unittest.main()
