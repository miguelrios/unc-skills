#!/usr/bin/env python3
"""Packaged Darwin/arm64 proof for the explicit export-inbox lifecycle."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.error
from pathlib import Path

from client.mac import BrainClient
from connectors.export_inbox import ExportInboxConnector
from connectors.sdk import ConnectorRunError, ConnectorRunner
from privacy.policy import PrivacyPolicy


def conversation(nonce: str, canary: str) -> list[dict]:
    return [{
        "id": f"synthetic-conversation-{nonce}",
        "title": "Synthetic export inbox proof",
        "mapping": {
            "root": {"id": "root", "parent": None, "children": ["user"], "message": None},
            "user": {
                "id": "user", "parent": "root", "children": ["assistant"],
                "message": {
                    "id": f"synthetic-user-{nonce}", "author": {"role": "user"},
                    "create_time": "2026-07-14T12:00:00Z",
                    "content": {"content_type": "text", "parts": [f"c8b-safe-marker-{nonce}"]},
                    "metadata": {},
                },
            },
            "assistant": {
                "id": "assistant", "parent": "user", "children": [],
                "message": {
                    "id": f"synthetic-assistant-{nonce}", "author": {"role": "assistant"},
                    "create_time": "2026-07-14T12:00:01Z",
                    "content": {
                        "content_type": "multimodal_text",
                        "parts": [
                            f"keep aftermath api_key={canary}",
                            {"content_type": "image_asset_pointer", "asset_pointer": "file-service://synthetic", "name": "synthetic.png"},
                        ],
                    },
                    "metadata": {"model_slug": "synthetic-model"},
                },
            },
        },
    }]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    token = json.load(sys.stdin)
    if not isinstance(token, str) or not token:
        raise ValueError("stdin must contain one scoped credential")

    root = args.workspace / "synthetic-export-inbox-c8b"
    inbox = root / "explicit-inbox"
    catalog = root / "state" / "catalog.db"
    spool = root / "state" / "spool.db"
    canary = f"c8b-{args.nonce}-secret-canary"
    marker = f"c8b-safe-marker-{args.nonce}"
    result = None
    connector = None
    runner = None
    try:
        inbox.mkdir(parents=True)
        (inbox / "renamed.json").write_text(json.dumps(conversation(args.nonce, canary)))
        (inbox / "renamed.jsonl").write_text(json.dumps({
            "conversation_id": f"synthetic-cowork-{args.nonce}",
            "message_id": f"synthetic-cowork-message-{args.nonce}",
            "parent_message_id": None,
            "create_time": "2026-07-14T12:00:02Z",
            "role": "assistant",
            "content": {"content_type": "text", "parts": [f"c8b-cowork-marker-{args.nonce}"]},
        }) + "\n")
        connector = ExportInboxConnector(
            inbox=inbox, catalog_path=catalog, source_id=args.source_id,
            privacy_mode="scrub",
        )
        inventory = connector.dry_run()
        assert inventory["files"] == 2 and inventory["network_requests"] == 0
        assert inventory["privacy_mode"] == "scrub"
        assert canary not in json.dumps(inventory)

        offline = BrainClient(
            endpoint="http://127.0.0.1:1", token=token, source_id=args.source_id,
            principal_id="owner", visibility="private",
        )
        runner = ConnectorRunner(
            connector=connector, brain=offline, spool_path=spool,
            privacy=PrivacyPolicy(mode="scrub"),
        )
        try:
            runner.run_once()
        except ConnectorRunError as error:
            assert error.error_code == "brain_unavailable"
        else:
            raise AssertionError("offline ingest unexpectedly acknowledged")
        assert runner.doctor()["pending"] == 3
        assert canary.encode() not in spool.read_bytes()
        runner.close()
        runner = None

        unavailable = root / "inbox-temporarily-unavailable"
        inbox.rename(unavailable)
        online = BrainClient(
            endpoint=args.endpoint, token=token, source_id=args.source_id,
            principal_id="owner", visibility="private",
        )
        runner = ConnectorRunner(
            connector=connector, brain=online, spool_path=spool,
            privacy=PrivacyPolicy(mode="scrub"),
        )
        recovered = runner.run_once()
        assert recovered["acked"] == 3 and recovered["replayed"] == 1
        unavailable.rename(inbox)
        assert online.doctor()["source_events"] == 3
        safe = online.search(marker, limit=5)
        cowork = online.search(f"c8b-cowork-marker-{args.nonce}", limit=5)
        assert safe["results"] and cowork["results"]
        receipt = safe["results"][0]["receipt"].split("#", 1)[0]
        resolved = online.resolve(receipt)
        assert marker in json.dumps(resolved)
        assert canary not in json.dumps(resolved)
        assert online.search(canary, limit=5)["results"] == []

        for path in tuple(inbox.iterdir()):
            path.unlink()
        no_delete = connector.pull(runner._cursor())
        assert not any(record.deleted for record in no_delete.records)
        assert online.search(marker, limit=5)["results"]

        for item in connector.exports():
            assert connector.queue_remove(item["export_id"])["status"] == "queued"
        for _ in range(8):
            runner.run_once()
            if all(item["status"] == "removed" for item in connector.exports()):
                break
        else:
            raise AssertionError("explicit removal did not converge")
        assert online.doctor()["live_items"] == 0
        assert online.search(marker, limit=5)["results"] == []
        try:
            online.resolve(receipt)
        except urllib.error.HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("removed export receipt still resolved")
        for database in (catalog, spool):
            assert canary.encode() not in database.read_bytes()
        result = {
            "status": "pass",
            "summary": {
                "inventory_files": 2, "inventory_network_requests": 0,
                "offline_staged": 3, "replayed_pages": 1,
                "searchable_records": 3, "canary_search_hits": 0,
                "spool_canary_hits": 0, "explicit_tombstones": 3,
                "live_items_after_removal": 0,
            },
        }
    finally:
        if runner is not None:
            runner.close()
        if connector is not None:
            connector.close()
        shutil.rmtree(root, ignore_errors=True)
    assert not root.exists()
    assert result is not None
    result["summary"]["local_residue"] = 0
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
