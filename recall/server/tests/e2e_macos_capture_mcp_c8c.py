#!/usr/bin/env python3
"""Packaged Darwin/arm64 proof for deliberate stdio MCP capture."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
from pathlib import Path

from client.mac import BrainClient


def rpc(process: subprocess.Popen, request: dict) -> dict:
    process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
    process.stdin.flush()
    line = process.stdout.readline()
    if not line:
        raise AssertionError("MCP process ended before response")
    return json.loads(line)


def call(process: subprocess.Popen, request_id: int, name: str, arguments: dict) -> dict:
    response = rpc(process, {
        "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    assert "error" not in response, response.get("error")
    return json.loads(response["result"]["content"][0]["text"])


def capture(label: str, nonce: str, index: int, body: str) -> dict:
    return {
        "schema_version": 1,
        "title": f"Synthetic {label} capture",
        "body": body,
        "occurred_at": f"2026-07-14T14:0{index}:00Z",
        "tags": ["synthetic", "c8c"],
        "provenance": {"uri": f"manual://c8c-{label}"},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--wrapper", type=Path, required=True)
    args = parser.parse_args()
    token = json.load(sys.stdin)
    if not isinstance(token, str) or not token:
        raise ValueError("stdin must contain one scoped credential")

    root = args.workspace / "synthetic-capture-c8c"
    token_file = root / "scoped-token.json"
    canary = f"c8c-{args.nonce}-secret-canary"
    process = None
    result = None
    try:
        root.mkdir(parents=True)
        descriptor = os.open(token_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump({"token": token}, output)
        assert token_file.stat().st_mode & 0o777 == 0o600
        preview = subprocess.run([
            str(args.wrapper), "mcp-config-preview",
            "--endpoint", args.endpoint, "--source-id", args.source_id,
            "--capture-origin", "synthetic-c8c",
            "--visibility", "private", "--token-file", str(token_file),
            "--privacy-mode", "scrub",
        ], check=True, text=True, capture_output=True)
        config = json.loads(preview.stdout)
        assert config["network_requests"] == 0 and config["writes"] == 0
        assert token not in preview.stdout and preview.stderr == ""

        process = subprocess.Popen([
            str(args.wrapper), "mcp-serve",
            "--endpoint", args.endpoint, "--source-id", args.source_id,
            "--capture-origin", "synthetic-c8c",
            "--visibility", "private", "--token-file", str(token_file),
            "--privacy-mode", "scrub",
        ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        initialized = rpc(process, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25", "capabilities": {},
                "clientInfo": {"name": "synthetic-c8c-client", "version": "1.0.0"},
            },
        })
        assert initialized["result"]["protocolVersion"] == "2025-11-25"
        tools = rpc(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        assert {item["name"] for item in tools["result"]["tools"]} == {
            "recall_capture", "recall_forget", "recall_doctor",
        }

        origins = ("codex", "claude-code", "chatgpt-cowork", "grep.app")
        values = [
            capture(origin, args.nonce, index, (
                f"c8c-safe-{origin}-{args.nonce}"
                if origin != "chatgpt-cowork"
                else f"c8c-safe-{origin}-{args.nonce} api_key={canary} keep aftermath"
            ))
            for index, origin in enumerate(origins)
        ]
        receipts = []
        native_ids = []
        for index, value in enumerate(values, 10):
            outcome = call(process, index, "recall_capture", value)
            rendered = json.dumps(outcome)
            assert value["body"] not in rendered and canary not in rendered
            receipts.append(outcome["receipt"])
            native_ids.append(outcome["native_id"])
        replay = call(process, 20, "recall_capture", values[0])
        assert replay["receipt"] == receipts[0] and replay["native_id"] == native_ids[0]
        assert replay["replay"] is True

        brain = BrainClient(
            endpoint=args.endpoint, token=token, source_id=args.source_id,
            principal_id="owner", visibility="private",
        )
        assert brain.doctor()["source_events"] == 4
        for origin in origins:
            assert brain.search(f"c8c-safe-{origin}-{args.nonce}", limit=5)["results"]
        assert brain.search(canary, limit=5)["results"] == []
        resolved = brain.resolve(receipts[2])
        assert f"c8c-safe-chatgpt-cowork-{args.nonce}" in json.dumps(resolved)
        assert canary not in json.dumps(resolved)
        doctor = call(process, 21, "recall_doctor", {})
        assert doctor["live_items"] == 4

        first_delete = call(process, 30, "recall_forget", {"receipt": receipts[0]})
        repeated_delete = call(process, 31, "recall_forget", {"receipt": receipts[0]})
        assert first_delete["receipt"] == repeated_delete["receipt"]
        for index, receipt in enumerate(receipts[1:], 32):
            call(process, index, "recall_forget", {"receipt": receipt})
        assert brain.doctor()["live_items"] == 0
        for origin in origins:
            assert brain.search(f"c8c-safe-{origin}-{args.nonce}", limit=5)["results"] == []
        try:
            brain.resolve(receipts[0])
        except urllib.error.HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("forgotten receipt still resolved")
        result = {
            "status": "pass",
            "summary": {
                "protocol_version": "2025-11-25", "tools": 3,
                "source_labels": 4, "bound_origins": 1, "retry_added_events": 0,
                "same_retry_receipt": True, "canary_search_hits": 0,
                "resolved_canary_hits": 0, "forgotten": 4,
                "live_items_after_forget": 0,
            },
        }
    finally:
        if process is not None:
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
