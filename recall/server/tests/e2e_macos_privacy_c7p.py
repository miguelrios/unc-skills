#!/usr/bin/env python3
"""Packaged arm64 macOS proof for C7P pre-ingest privacy."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from client.mac import BrainClient, ExportImporter, MemoryClient
from collector.collector import Collector
from privacy.policy import PrivacyPolicy


def fixture(harness: str, text: str, timestamp: str) -> dict:
    if harness == "claude":
        return {"type": "user", "timestamp": timestamp, "message": {"content": text}}
    return {
        "type": "response_item",
        "timestamp": timestamp,
        "payload": {
            "type": "message", "role": "user",
            "content": [{"type": "input_text", "text": text}],
        },
    }


def assert_not_found(client: BrainClient, canary: str) -> None:
    assert client.search(canary, limit=10)["results"] == []


def delete_all(client: MemoryClient, receipts: list[str]) -> None:
    if receipts:
        result = client.delete_many(receipts)
        assert len(result["acknowledgement"]["receipts"]) == len(receipts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--claude-drop-source", required=True)
    parser.add_argument("--claude-scrub-source", required=True)
    parser.add_argument("--codex-drop-source", required=True)
    parser.add_argument("--codex-scrub-source", required=True)
    args = parser.parse_args()
    credentials = json.load(sys.stdin)
    if not isinstance(credentials, list) or len(credentials) != 4 or not all(isinstance(item, str) and item for item in credentials):
        raise ValueError("stdin must contain four non-empty scoped credentials")

    sources = {
        ("claude", "drop"): args.claude_drop_source,
        ("claude", "scrub"): args.claude_scrub_source,
        ("codex", "drop"): args.codex_drop_source,
        ("codex", "scrub"): args.codex_scrub_source,
    }
    tokens = dict(zip(sources, credentials, strict=True))
    root = args.workspace / "synthetic-privacy-c7p"
    committed: dict[tuple[str, str], list[str]] = {key: [] for key in sources}
    summary = {
        "collector_paths": 0, "export_paths": 0, "memory_paths": 0,
        "canary_search_hits": 0, "spool_canary_hits": 0,
        "replay_added_events": 0, "cleanup_live_items": 0,
    }
    try:
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for (harness, mode), source_id in sources.items():
            token = tokens[(harness, mode)]
            policy = PrivacyPolicy(mode=mode)
            client = BrainClient(
                endpoint=args.endpoint, token=token, source_id=source_id,
                principal_id="owner", visibility="private",
            )
            memory = MemoryClient(
                endpoint=args.endpoint, token=token, source_id=source_id,
                principal_id="owner", visibility="private",
            )
            harness_root = root / harness / mode
            harness_root.mkdir(parents=True)
            transcript = harness_root / ("rollout-synthetic.jsonl" if harness == "codex" else "synthetic.jsonl")
            collector_canary = f"c7p-{args.nonce}-{harness}-{mode}-collector-secret"
            safe_marker = f"c7p-{args.nonce}-{harness}-{mode}-safe"
            transcript.write_text(
                json.dumps(fixture(harness, f"api_key={collector_canary}", timestamp)) + "\n" +
                json.dumps(fixture(harness, safe_marker, timestamp)) + "\n"
            )
            spool = root / f"{harness}-{mode}.db"
            offline = Collector(
                root=harness_root, harness=harness, source_id=source_id,
                spool_path=spool, endpoint="http://127.0.0.1:1", token=token,
                principal_id="owner", visibility="private", privacy=policy,
            )
            try:
                scan = offline.scan()
                expected = 1 if mode == "drop" else 2
                assert scan["records_queued"] == expected
                assert scan["privacy"]["actions"] == {mode: 1, "keep": 1}
                assert collector_canary.encode() not in spool.read_bytes()
                assert offline.doctor()["pending"] == expected
                assert offline.doctor()["committed_files"] == 0
            finally:
                offline.close()

            online = Collector(
                root=harness_root, harness=harness, source_id=source_id,
                spool_path=spool, endpoint=args.endpoint, token=token,
                principal_id="owner", visibility="private", privacy=policy,
            )
            try:
                recovery = online.flush()
                assert recovery["acked"] == expected
                receipts = [row["receipt"] for row in online.db.execute(
                    "SELECT receipt FROM outbox WHERE state='acked' ORDER BY id"
                )]
                committed[(harness, mode)].extend(receipts)
                assert online.flush()["acked"] == 0
                assert client.search(safe_marker, limit=5)["results"]
                assert_not_found(client, collector_canary)
                for receipt in receipts:
                    resolved = client.resolve(receipt)
                    assert collector_canary not in json.dumps(resolved)
                summary["collector_paths"] += 1
            finally:
                online.close()

            export_canary = f"c7p-{args.nonce}-{harness}-{mode}-export-secret"
            export_path = root / f"{harness}-{mode}-export.jsonl"
            export_path.write_text(
                json.dumps({"text": f"api_key={export_canary}"}) + "\n" +
                json.dumps({"text": f"{safe_marker}-export"}) + "\n"
            )
            importer = ExportImporter(
                source_id=source_id, principal_id="owner", visibility="private",
                privacy=policy,
            )
            imported = importer.import_with(client, [export_path])
            export_receipts = imported["acknowledgement"]["receipts"]
            assert len(export_receipts) == expected
            committed[(harness, mode)].extend(export_receipts)
            assert_not_found(client, export_canary)
            for receipt in export_receipts:
                assert export_canary not in json.dumps(client.resolve(receipt))
            summary["export_paths"] += 1

            memory_canary = f"c7p-{args.nonce}-{harness}-{mode}-memory-secret"
            protected_memory = MemoryClient(
                endpoint=args.endpoint, token=token, source_id=source_id,
                principal_id="owner", visibility="private", privacy=policy,
            ).put(f"keep memory context api_key={memory_canary} after")
            if mode == "drop":
                assert protected_memory["privacy"]["action"] == "drop"
                assert "receipt" not in protected_memory
            else:
                committed[(harness, mode)].append(protected_memory["receipt"])
                assert memory_canary not in json.dumps(client.resolve(protected_memory["receipt"]))
            assert_not_found(client, memory_canary)
            summary["memory_paths"] += 1

        for key, receipts in committed.items():
            source_id = sources[key]
            delete_all(MemoryClient(
                endpoint=args.endpoint, token=tokens[key], source_id=source_id,
                principal_id="owner", visibility="private",
            ), receipts)
            committed[key].clear()
            doctor = BrainClient(
                endpoint=args.endpoint, token=tokens[key], source_id=source_id,
                principal_id="owner", visibility="private",
            ).doctor()
            assert doctor["live_items"] == 0
    finally:
        for key, receipts in committed.items():
            if receipts:
                try:
                    delete_all(MemoryClient(
                        endpoint=args.endpoint, token=tokens[key], source_id=sources[key],
                        principal_id="owner", visibility="private",
                    ), receipts)
                except Exception:
                    pass
        shutil.rmtree(root, ignore_errors=True)

    assert not root.exists()
    summary["local_residue"] = 0
    print(json.dumps({"status": "pass", "summary": summary}, sort_keys=True))


if __name__ == "__main__":
    main()
