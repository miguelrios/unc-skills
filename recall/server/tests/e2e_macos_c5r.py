#!/usr/bin/env python3
"""Black-box C5R proof from the installed Mac client through tailnet HTTPS.

All inputs must be synthetic except the content-free dry-run performed before
this script. Credentials are resolved by source-scoped Keychain account and
are never rendered.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import urllib.error
from pathlib import Path

from client.mac import BrainClient, ExportImporter, MemoryClient, load_keychain_token


def client(endpoint: str, service: str, source_id: str, *, memory: bool = False):
    cls = MemoryClient if memory else BrainClient
    return cls(
        endpoint=endpoint,
        token=load_keychain_token(service, source_id),
        source_id=source_id,
        principal_id="owner",
        visibility="private",
    )


def rank_for(result: dict, event_receipt: str) -> int:
    for rank, item in enumerate(result["results"], 1):
        if item["receipt"].split("#", 1)[0] == event_receipt:
            return rank
    raise AssertionError("committed receipt was not returned by source-scoped search")


def assert_deleted(brain: BrainClient, marker: str, receipt: str) -> None:
    assert all(
        item["receipt"].split("#", 1)[0] != receipt
        for item in brain.search(marker, limit=5)["results"]
    )
    try:
        brain.resolve(receipt)
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
    else:
        raise AssertionError("deleted receipt still resolves")


def recovered_source(
    *, endpoint: str, service: str, source_id: str, spool: Path, marker: str,
) -> tuple[dict, BrainClient, str]:
    connection = sqlite3.connect(spool)
    try:
        pending = connection.execute("SELECT count(*) FROM outbox WHERE state='pending'").fetchone()[0]
        acked = connection.execute("SELECT count(*) FROM outbox WHERE state='acked'").fetchone()[0]
        committed = connection.execute(
            "SELECT count(*) FROM files WHERE status != 'tombstone' AND committed_offset=scanned_offset"
        ).fetchone()[0]
        receipt = connection.execute(
            "SELECT receipt FROM outbox WHERE state='acked' ORDER BY id"
        ).fetchone()[0]
    finally:
        connection.close()
    assert (pending, acked, committed) == (0, 1, 1)
    brain = client(endpoint, service, source_id)
    searched = brain.search(marker, limit=5)
    rank = rank_for(searched, receipt)
    resolved = brain.resolve(receipt)
    assert marker in json.dumps(resolved, sort_keys=True)
    doctor = brain.doctor()
    assert doctor["source_events"] == acked
    assert doctor["live_items"] >= 1
    return {
        "pending": pending,
        "acked": acked,
        "committed_files": committed,
        "rank": rank,
        "source_events": doctor["source_events"],
    }, brain, receipt


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--keychain-service", required=True)
    parser.add_argument("--claude-source", required=True)
    parser.add_argument("--codex-source", required=True)
    parser.add_argument("--memory-source", required=True)
    parser.add_argument("--export-source", required=True)
    parser.add_argument("--claude-spool", type=Path, required=True)
    parser.add_argument("--codex-spool", type=Path, required=True)
    parser.add_argument("--claude-marker", required=True)
    parser.add_argument("--codex-marker", required=True)
    parser.add_argument("--memory-marker", required=True)
    parser.add_argument("--export-marker", required=True)
    parser.add_argument("--export-path", type=Path, required=True)
    parser.add_argument("--recovery-seconds", type=float, required=True)
    args = parser.parse_args()
    assert 0 <= args.recovery_seconds < 30

    claude, claude_client, claude_receipt = recovered_source(
        endpoint=args.endpoint, service=args.keychain_service,
        source_id=args.claude_source, spool=args.claude_spool,
        marker=args.claude_marker,
    )
    codex, codex_client, codex_receipt = recovered_source(
        endpoint=args.endpoint, service=args.keychain_service,
        source_id=args.codex_source, spool=args.codex_spool,
        marker=args.codex_marker,
    )

    memory = client(args.endpoint, args.keychain_service, args.memory_source, memory=True)
    memory_put = memory.put(args.memory_marker, provenance={"uri": "manual://c5r-live-prove"})
    memory_rank = rank_for(memory.search(args.memory_marker, limit=5), memory_put["receipt"])
    memory_resolved = memory.resolve(memory_put["receipt"])
    assert memory_resolved["items"][0]["text_redacted"] == args.memory_marker
    memory.delete(memory_put["receipt"])
    assert_deleted(memory, args.memory_marker, memory_put["receipt"])
    memory_doctor = memory.doctor()
    assert memory_doctor["source_events"] == 2 and memory_doctor["live_items"] == 0

    exporter = ExportImporter(
        source_id=args.export_source, principal_id="owner", visibility="private"
    )
    export_client = client(args.endpoint, args.keychain_service, args.export_source)
    first_export = exporter.import_with(export_client, [args.export_path])
    after_first = export_client.doctor()
    second_export = exporter.import_with(export_client, [args.export_path])
    after_replay = export_client.doctor()
    assert first_export["acknowledgement"]["inserted"] == 1
    assert second_export["acknowledgement"]["replay"] is True
    assert after_first["source_events"] == after_replay["source_events"] == 1
    export_receipt = first_export["acknowledgement"]["receipts"][0]
    export_rank = rank_for(export_client.search(args.export_marker, limit=5), export_receipt)
    export_resolved = export_client.resolve(export_receipt)
    provenance = export_resolved["event"]["provenance"]
    assert provenance["archive"] == args.export_path.name
    assert provenance["member"].endswith("#record=0")
    MemoryClient(
        endpoint=args.endpoint,
        token=load_keychain_token(args.keychain_service, args.export_source),
        source_id=args.export_source,
        principal_id="owner",
        visibility="private",
    ).delete(export_receipt)
    assert_deleted(export_client, args.export_marker, export_receipt)

    for summary, source_id, brain, marker, receipt in (
        (claude, args.claude_source, claude_client, args.claude_marker, claude_receipt),
        (codex, args.codex_source, codex_client, args.codex_marker, codex_receipt),
    ):
        MemoryClient(
            endpoint=args.endpoint,
            token=load_keychain_token(args.keychain_service, source_id),
            source_id=source_id,
            principal_id="owner",
            visibility="private",
        ).delete(receipt)
        assert_deleted(brain, marker, receipt)
        doctor = brain.doctor()
        assert doctor["source_events"] == 2 and doctor["live_items"] == 0
        summary["final_source_events"] = doctor["source_events"]
        summary["final_live_items"] = doctor["live_items"]

    export_doctor = export_client.doctor()
    assert export_doctor["source_events"] == 2 and export_doctor["live_items"] == 0
    print(json.dumps({
        "status": "green",
        "offline_recovery_seconds": args.recovery_seconds,
        "claude": claude,
        "codex": codex,
        "memory": {
            "rank": memory_rank,
            "receipt_exact": True,
            "deleted_search_and_receipt": True,
            "final_source_events": memory_doctor["source_events"],
            "final_live_items": memory_doctor["live_items"],
        },
        "export": {
            "rank": export_rank,
            "replay": True,
            "replay_revision_delta": 0,
            "member_provenance": provenance["member"],
            "deleted_search_and_receipt": True,
            "final_source_events": export_doctor["source_events"],
            "final_live_items": export_doctor["live_items"],
        },
        "all_synthetic_receipts_deleted": True,
        "credential_bytes_rendered": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
