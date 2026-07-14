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


SOURCE = "synthetic:capture:postgres"
FIXTURE = ROOT / "recall/tests/capture_v1/corpus.jsonl"


class StoreCapture(CaptureClient):
    def __init__(self, store: BrainStore, *, source_id: str = SOURCE, mode: str = "scrub"):
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
        server = McpServer(StoreCapture(store))
        receipts = []
        for index, capture in enumerate(valid, 1):
            outcome = result_text(call(server, index, "recall_capture", capture))
            receipts.append(outcome["receipt"])
        assert len(set(receipts)) == 4
        before_retry = store.doctor(SOURCE)
        replay = result_text(call(server, 10, "recall_capture", valid[0]))
        assert replay["receipt"] == receipts[0] and replay["replay"] is True
        after_retry = store.doctor(SOURCE)
        assert before_retry["source_events"] == after_retry["source_events"] == 4
        assert before_retry["live_items"] == after_retry["live_items"] == 4

        assert store.search("bounded cobalt queue", authorized_source=SOURCE)["results"]
        assert store.search("Keep safe Cowork context", authorized_source=SOURCE)["results"]
        assert store.search("synthetic-capture-secret-canary-101", authorized_source=SOURCE)["results"] == []

        deleted = result_text(call(server, 11, "recall_forget", {"receipt": receipts[0]}))
        assert deleted["receipt"].endswith("?rev=2")
        assert store.search("bounded cobalt queue", authorized_source=SOURCE)["results"] == []
        assert store.doctor(SOURCE)["live_items"] == 3

        drop_server = McpServer(StoreCapture(store, mode="drop"))
        dropped = result_text(call(drop_server, 12, "recall_capture", valid[2]))
        assert dropped["status"] == "privacy_filtered"
        assert store.doctor(SOURCE)["source_events"] == 5

        wrong = McpServer(StoreCapture(store, source_id="synthetic:capture:other"))
        try:
            call(wrong, 13, "recall_forget", {"receipt": receipts[1]})
        except McpProtocolError as error:
            assert error.message == "capture_unavailable"
        else:
            raise AssertionError("cross-source receipt was accepted")
        assert store.doctor(SOURCE)["live_items"] == 3
        print(json.dumps({
            "status": "pass", "origins": 4, "live_after_capture": 4,
            "retry_added_events": 0, "same_receipt": True,
            "canary_search_hits": 0, "live_after_forget": 3,
            "cross_source_deletes": 0,
        }, sort_keys=True))
    finally:
        truncate(store)


if __name__ == "__main__":
    main()
