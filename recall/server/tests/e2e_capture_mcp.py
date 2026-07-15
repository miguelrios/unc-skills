#!/usr/bin/env python3
"""Fresh-PostgreSQL proof for deliberate MCP capture and forget."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "recall"))
sys.path.insert(0, str(ROOT / "recall/server"))

from client.capture import CaptureClient
from client.mcp import McpProtocolError, McpServer
from privacy.policy import PrivacyPolicy
from recall_server.db import BrainStore


PROFILES = (
    ("synthetic:capture:codex", "openai-codex"),
    ("synthetic:capture:claude-code", "anthropic-claude-code"),
    ("synthetic:capture:claude-desktop", "anthropic-claude-desktop"),
    ("synthetic:capture:chatgpt-remote", "openai-chatgpt-remote"),
)
FIXTURE = ROOT / "recall/tests/capture_v1/corpus.jsonl"


class StoreCapture(CaptureClient):
    def __init__(self, store: BrainStore, *, source_id: str = PROFILES[0][0], mode: str = "scrub"):
        super().__init__(
            endpoint="https://synthetic.invalid", token="synthetic-token",
            source_id=source_id, principal_id="owner", visibility="private",
            privacy=PrivacyPolicy(mode=mode),
        )
        self.store = store

    def _request(self, path, *, body=None, idempotency_key=None, method=None):
        assert path == "/v1/ingest/batches" and body is not None and idempotency_key
        acknowledgement, replay = self.store.ingest(idempotency_key, body["events"])
        return {**acknowledgement, "replay": replay}

    def doctor(self):
        return self.store.doctor(self.source_id)


def result_text(response: dict) -> dict:
    return json.loads(response["result"]["content"][0]["text"])


def call(server: McpServer, request_id: int, name: str, arguments: dict) -> dict:
    return server.handle({
        "jsonrpc": "2.0", "id": request_id, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })


def truncate(store: BrainStore) -> None:
    with store.connect() as connection:
        connection.execute(
            "TRUNCATE chunks,items,sessions,projection_watermarks,source_events,"
            "ingest_batches,source_grants,sources,dead_letters,audit_events RESTART IDENTITY CASCADE"
        )


def main() -> None:
    store = BrainStore(os.environ["RECALL_DATABASE_URL"])
    store.migrate()
    truncate(store)
    try:
        rows = [json.loads(line) for line in FIXTURE.read_text().splitlines()]
        valid = [row["capture"] for row in rows if row["valid"]]
        servers = {
            source_id: McpServer(
                StoreCapture(store, source_id=source_id), capture_origin=origin,
            )
            for source_id, origin in PROFILES
        }
        receipts = []
        for index, ((source_id, origin), capture) in enumerate(zip(PROFILES, valid, strict=True), 1):
            arguments = {key: value for key, value in capture.items() if key != "origin"}
            arguments["title"] = f"Synthetic {origin} profile"
            arguments["body"] = f"bounded cobalt queue {origin}"
            arguments["provenance"] = {"uri": f"manual://{origin}"}
            outcome = result_text(call(servers[source_id], index, "recall_capture", arguments))
            receipts.append(outcome["receipt"])
        assert len(set(receipts)) == 4
        first_source, first_origin = PROFILES[0]
        first_arguments = {key: value for key, value in valid[0].items() if key != "origin"}
        first_arguments.update({
            "title": f"Synthetic {first_origin} profile",
            "body": f"bounded cobalt queue {first_origin}",
            "provenance": {"uri": f"manual://{first_origin}"},
        })
        before_retry = store.doctor(first_source)
        replay = result_text(call(servers[first_source], 10, "recall_capture", first_arguments))
        assert replay["receipt"] == receipts[0] and replay["replay"] is True
        after_retry = store.doctor(first_source)
        assert before_retry["source_events"] == after_retry["source_events"] == 1
        assert before_retry["live_items"] == after_retry["live_items"] == 1

        for (source_id, origin), receipt in zip(PROFILES, receipts, strict=True):
            assert store.search(origin, authorized_source=source_id)["results"]
            resolved = store.resolve(receipt)
            assert resolved and resolved["event"]["source_id"] == source_id
            with store.connect() as connection:
                envelope = connection.execute(
                    "SELECT envelope FROM source_events WHERE source_id=%s AND native_id=%s",
                    (source_id, resolved["event"]["native_id"]),
                ).fetchone()["envelope"]
            assert envelope["content"]["origin"] == origin
        assert store.search("synthetic-capture-secret-canary-101")["results"] == []

        spoof = {**first_arguments, "origin": PROFILES[1][1]}
        try:
            call(servers[first_source], 11, "recall_capture", spoof)
        except McpProtocolError as error:
            assert error.message == "capture_invalid"
        else:
            raise AssertionError("caller-controlled origin was accepted")

        deleted = result_text(call(servers[first_source], 12, "recall_forget", {"receipt": receipts[0]}))
        assert deleted["receipt"].endswith("?rev=2")
        assert store.search(first_origin, authorized_source=first_source)["results"] == []
        assert store.doctor(first_source)["live_items"] == 0

        drop_server = McpServer(
            StoreCapture(store, source_id=first_source, mode="drop"),
            capture_origin=first_origin,
        )
        sensitive_arguments = {
            key: value for key, value in valid[2].items() if key != "origin"
        }
        dropped = result_text(call(drop_server, 13, "recall_capture", sensitive_arguments))
        assert dropped["status"] == "privacy_filtered"
        assert store.doctor(first_source)["source_events"] == 2

        try:
            call(servers[first_source], 14, "recall_forget", {"receipt": receipts[1]})
        except McpProtocolError as error:
            assert error.message == "capture_unavailable"
        else:
            raise AssertionError("cross-source receipt was accepted")
        assert store.doctor(PROFILES[1][0])["live_items"] == 1
        print(json.dumps({
            "status": "pass", "host_profiles": 4, "bound_origins": 4,
            "live_after_capture": 4,
            "retry_added_events": 0, "same_receipt": True,
            "canary_search_hits": 0, "live_after_forget": 3,
            "origin_spoofs": 0, "cross_source_deletes": 0,
        }, sort_keys=True))
    finally:
        truncate(store)


if __name__ == "__main__":
    main()
