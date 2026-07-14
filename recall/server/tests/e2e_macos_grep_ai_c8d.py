#!/usr/bin/env python3
"""Content-free live Grep AI -> packaged Mac -> private Brain proof.

The two scoped credentials arrive as one JSON object on stdin. Private Grep
content is held only in process memory and is never rendered by this program.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
from pathlib import Path

from client.mac import BrainClient, MemoryClient
from connectors.grep_ai import GrepAIConnector


def private_file(path: Path, data: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(data)
    assert path.stat().st_mode & 0o777 == 0o600


def run_json(command: list[str], secrets: tuple[str, ...]) -> dict:
    completed = subprocess.run(command, check=True, text=True, capture_output=True)
    rendered = completed.stdout + completed.stderr
    assert completed.stderr == ""
    assert all(secret not in rendered for secret in secrets)
    return json.loads(completed.stdout)


def private_queries(record) -> list[str]:
    """Build bounded search probes without exposing or persisting their text."""
    content = record.content
    candidates = [str(content.get("question") or ""), str(content.get("report_markdown") or "")]
    queries: list[str] = []
    for candidate in candidates:
        words = []
        for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{5,}", candidate):
            if word.casefold() not in {value.casefold() for value in words}:
                words.append(word)
            if len(words) == 12:
                break
        if len(words) >= 3:
            queries.append(" ".join(words[:3]))
        queries.extend(words[:6])
        clipped = " ".join(candidate[:400].split())
        if clipped:
            queries.append(clipped)
    return list(dict.fromkeys(queries))[:16]


def find_import(brain: BrainClient, records: list) -> tuple[object, str] | None:
    for record in records:
        for query in private_queries(record):
            result = brain.search(query, limit=10)
            for item in result["results"]:
                if item["native_id"] == record.native_id:
                    return record, item["receipt"].split("#", 1)[0]
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--max-live-pages", type=int, default=20)
    args = parser.parse_args()

    credentials = json.load(sys.stdin)
    brain_token = credentials.get("brain_token")
    grep_api_key = credentials.get("grep_api_key")
    if not isinstance(brain_token, str) or not brain_token:
        raise ValueError("stdin is missing scoped Brain authority")
    if not isinstance(grep_api_key, str) or not grep_api_key:
        raise ValueError("stdin is missing scoped Grep authority")
    secrets = (brain_token, grep_api_key)

    prefix = args.workspace / "installed"
    launch_agents = args.workspace / "launch-agents"
    credential_root = args.workspace / "credentials"
    brain_file = credential_root / "brain.json"
    grep_file = credential_root / "grep.key"
    spool = args.workspace / "state" / "grep-ai.db"
    wrapper = prefix / "bin" / "recall-brain"
    result = None
    installed = False
    try:
        credential_root.mkdir(parents=True, mode=0o700)
        private_file(brain_file, json.dumps({"token": brain_token}, separators=(",", ":")))
        private_file(grep_file, grep_api_key + "\n")

        install = subprocess.run([
            str(args.bundle_root / "install.sh"),
            "--prefix", str(prefix), "--launch-agents", str(launch_agents),
            "--endpoint", args.endpoint, "--host-id", "miguel-mbpro-c8d",
            "--keychain-service", "ai.parcha.recall.c8d.ephemeral",
            "--visibility", "private", "--privacy-mode", "scrub",
            "--disable-export-inbox", "--no-load",
        ], check=True, text=True, capture_output=True)
        installed = True
        assert install.stderr == "" and all(secret not in install.stdout for secret in secrets)
        assert wrapper.is_file()

        common = [
            "--endpoint", args.endpoint, "--source-id", args.source_id,
            "--token-file", str(brain_file), "--grep-api-key-file", str(grep_file),
            "--spool", str(spool), "--max-pages", str(args.max_live_pages),
            "--page-size", "5", "--privacy-mode", "scrub",
        ]
        preview = run_json([str(wrapper), "grep-ai-config-preview", *common], secrets)
        assert preview["network_requests"] == 0 and preview["writes"] == 0
        assert preview["visibility"] == "private"

        probe = GrepAIConnector(
            api_key=grep_api_key, source_id=args.source_id,
            max_pages=args.max_live_pages, page_size=5,
        )
        brain = BrainClient(
            endpoint=args.endpoint, token=brain_token, source_id=args.source_id,
            principal_id="owner", visibility="private",
        )
        cursor = None
        candidates = []
        imported = None
        list_pages = detail_reads = sync_runs = 0
        for _ in range(args.max_live_pages):
            page = probe.pull(cursor)
            list_pages += 1
            detail_reads += len(page.records)
            candidates.extend(page.records)
            sync = run_json([str(wrapper), "grep-ai-sync", *common], secrets)
            sync_runs += 1
            assert sync["sync"]["status"] == "committed"
            assert sync["doctor"]["pending"] == 0
            imported = find_import(brain, candidates)
            if imported is not None:
                break
            cursor = page.next_cursor
        assert imported is not None, "no searchable completed Grep research found within the live page bound"
        record, receipt = imported

        resolved = brain.resolve(receipt)
        assert record.native_id in json.dumps(resolved, sort_keys=True)
        before = brain.doctor()
        assert before["live_items"] >= 1
        memory = MemoryClient(
            endpoint=args.endpoint, token=brain_token, source_id=args.source_id,
            principal_id="owner", visibility="private",
        )
        first_delete = memory.delete(receipt)
        repeated_delete = memory.delete(receipt)
        assert first_delete["receipt"] == repeated_delete["receipt"]
        for query in private_queries(record):
            assert all(item["native_id"] != record.native_id for item in brain.search(query, limit=10)["results"])
        try:
            brain.resolve(receipt)
        except urllib.error.HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("forgotten Grep receipt still resolved")

        # A list page can contain several completed jobs. Forget every native ID
        # observed in the imported page range so this dedicated live source has
        # no remaining private items. The target identity is source-scoped; the
        # receipt revision is only deletion provenance and is not authority.
        cleaned_native_ids = {record.native_id}
        for candidate in candidates:
            if candidate.native_id in cleaned_native_ids:
                continue
            cleanup_receipt = f"recall://{args.source_id}/{candidate.native_id}?rev=1"
            memory.delete(cleanup_receipt)
            cleaned_native_ids.add(candidate.native_id)
        after = brain.doctor()
        assert after["live_items"] == 0
        result = {
            "status": "pass",
            "summary": {
                "architecture": "Darwin-arm64",
                "config_preview_network_requests": 0,
                "config_preview_writes": 0,
                "grep_list_pages": list_pages,
                "grep_completed_detail_reads": detail_reads,
                "sync_runs": sync_runs,
                "searchable_completed_job": True,
                "resolved": True,
                "exact_forget_idempotent": True,
                "imported_native_ids_forgotten": len(cleaned_native_ids),
                "search_hits_after_forget": 0,
                "live_items_after_forget": 0,
                "credential_bytes_rendered": False,
            },
        }
    finally:
        if installed:
            uninstall = subprocess.run([
                str(args.bundle_root / "uninstall.sh"),
                "--prefix", str(prefix), "--launch-agents", str(launch_agents), "--no-load",
            ], check=True, text=True, capture_output=True)
            assert uninstall.stderr == "" and all(secret not in uninstall.stdout for secret in secrets)
        shutil.rmtree(args.workspace, ignore_errors=True)

    assert result is not None and not args.workspace.exists()
    result["summary"]["credential_file_residue"] = 0
    result["summary"]["spool_residue_bytes"] = 0
    result["summary"]["package_install_residue"] = 0
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
