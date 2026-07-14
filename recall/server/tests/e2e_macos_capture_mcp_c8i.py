#!/usr/bin/env python3
"""Packaged Darwin/arm64 proof for host-attested federated MCP capture."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import urllib.error
from pathlib import Path

from client.mac import BrainClient


PROFILES = (
    ("codex", "openai-codex"),
    ("claude-code", "anthropic-claude-code"),
    ("claude-desktop", "anthropic-claude-desktop"),
    ("chatgpt-remote", "openai-chatgpt-remote"),
)


def rpc(process: subprocess.Popen, request: dict) -> dict:
    process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
    process.stdin.flush()
    line = process.stdout.readline()
    if not line:
        raise AssertionError("MCP process ended before response")
    return json.loads(line)


def tool(process: subprocess.Popen, request_id: int, name: str, arguments: dict) -> dict:
    return rpc(process, {
        "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })


def value(response: dict) -> dict:
    assert "error" not in response, response.get("error")
    return json.loads(response["result"]["content"][0]["text"])


def private_file(path: Path, payload: dict) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as output:
        json.dump(payload, output, separators=(",", ":"))
    assert path.stat().st_mode & 0o777 == 0o600


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--wrapper", type=Path, required=True)
    args = parser.parse_args()
    credentials = json.load(__import__("sys").stdin)
    if not isinstance(credentials, dict) or set(credentials) != {name for name, _ in PROFILES}:
        raise ValueError("stdin must contain the four closed profile authorities")

    root = args.workspace / "synthetic-capture-c8i"
    processes: dict[str, subprocess.Popen] = {}
    clients: dict[str, BrainClient] = {}
    receipts: dict[str, str] = {}
    markers: dict[str, str] = {}
    result = None
    try:
        root.mkdir(parents=True, mode=0o700)
        assert root.stat().st_mode & 0o777 == 0o700
        for index, (name, origin) in enumerate(PROFILES, 1):
            authority = credentials[name]
            if (
                not isinstance(authority, dict) or set(authority) != {"source_id", "token"}
                or not isinstance(authority["source_id"], str)
                or not isinstance(authority["token"], str) or not authority["token"]
            ):
                raise ValueError("profile authority is invalid")
            token_file = root / f"{name}.json"
            private_file(token_file, {"token": authority["token"]})
            command = [
                str(args.wrapper), "mcp-serve",
                "--endpoint", args.endpoint,
                "--source-id", authority["source_id"],
                "--capture-origin", origin,
                "--visibility", "private",
                "--token-file", str(token_file),
                "--privacy-mode", "scrub",
            ]
            preview = subprocess.run(
                [str(args.wrapper), "mcp-config-preview", *command[2:]],
                check=True, text=True, capture_output=True,
            )
            rendered = json.loads(preview.stdout)
            assert rendered["network_requests"] == 0 and rendered["writes"] == 0
            assert origin in rendered["mcpServers"]["recall"]["args"]
            assert authority["token"] not in preview.stdout and preview.stderr == ""

            process = subprocess.Popen(
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True,
            )
            processes[name] = process
            initialized = rpc(process, {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25", "capabilities": {},
                    "clientInfo": {"name": f"synthetic-{name}", "version": "1.0.0"},
                },
            })
            assert initialized["result"]["protocolVersion"] == "2025-11-25"
            listed = rpc(process, {
                "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
            })
            tools = {item["name"]: item for item in listed["result"]["tools"]}
            assert set(tools) == {"recall_capture", "recall_forget", "recall_doctor"}
            schema = tools["recall_capture"]["inputSchema"]
            assert "origin" not in schema["properties"] and "origin" not in schema["required"]

            marker = f"c8i-safe-{name}-{args.nonce}"
            markers[name] = marker
            capture = {
                "schema_version": 1,
                "title": f"Synthetic {name} capture",
                "body": marker,
                "occurred_at": f"2026-07-14T15:0{index}:00Z",
                "tags": ["synthetic", "c8i"],
                "provenance": {"uri": f"manual://c8i-{name}"},
            }
            captured = value(tool(process, 10, "recall_capture", capture))
            assert marker not in json.dumps(captured)
            receipts[name] = captured["receipt"]
            replay = value(tool(process, 11, "recall_capture", capture))
            assert replay["receipt"] == receipts[name] and replay["replay"] is True

            spoofed = tool(process, 12, "recall_capture", {**capture, "origin": "spoofed-host"})
            assert spoofed["error"] == {"code": -32602, "message": "capture_invalid"}
            clients[name] = BrainClient(
                endpoint=args.endpoint, token=authority["token"],
                source_id=authority["source_id"], principal_id="owner",
                visibility="private",
            )

        for name, _origin in PROFILES:
            assert clients[name].doctor()["live_items"] == 1
            assert clients[name].search(markers[name], limit=5)["results"]
        assert clients[PROFILES[0][0]].search(markers[PROFILES[1][0]], limit=5)["results"] == []

        cross_delete = tool(
            processes[PROFILES[0][0]], 20, "recall_forget",
            {"receipt": receipts[PROFILES[1][0]]},
        )
        assert cross_delete["error"] == {"code": -32000, "message": "capture_unavailable"}
        assert clients[PROFILES[1][0]].doctor()["live_items"] == 1

        for index, (name, _origin) in enumerate(PROFILES, 30):
            forgotten = value(tool(
                processes[name], index, "recall_forget", {"receipt": receipts[name]},
            ))
            assert forgotten["receipt"].endswith("?rev=2")
            assert clients[name].doctor()["live_items"] == 0
            assert clients[name].search(markers[name], limit=5)["results"] == []
            try:
                clients[name].resolve(receipts[name])
            except urllib.error.HTTPError as error:
                assert error.code == 404
            else:
                raise AssertionError("forgotten receipt still resolved")

        result = {
            "status": "pass",
            "summary": {
                "host_profiles": 4, "bound_origins": 4,
                "origin_spoofs": 0, "cross_source_deletes": 0,
                "retry_added_events": 0, "forgotten": 4,
                "live_items_after_forget": 0,
            },
        }
    finally:
        for process in processes.values():
            if process.stdin:
                process.stdin.close()
            process.wait(timeout=10)
            stderr = process.stderr.read() if process.stderr else ""
            if result is not None:
                assert process.returncode == 0 and stderr == ""
        shutil.rmtree(root, ignore_errors=True)
    assert not root.exists() and result is not None
    result["summary"]["credential_file_residue"] = 0
    result["summary"]["local_residue"] = 0
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
