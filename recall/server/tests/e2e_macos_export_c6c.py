#!/usr/bin/env python3
"""Packaged-runtime C6C proof for reversible supported-export replay."""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import os
import shutil
import sys
import urllib.error
import zipfile
from pathlib import Path

from client.mac import (
    BrainClient,
    ExportImporter,
    MemoryClient,
    PrivacyError,
    load_keychain_token,
    store_keychain_token,
)


ITEM_NOT_FOUND = -25300


def delete_keychain_token(service: str, account: str) -> int:
    security = ctypes.CDLL(ctypes.util.find_library("Security"))
    security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
    security.SecKeychainItemDelete.restype = ctypes.c_int32
    service_bytes = service.encode()
    account_bytes = account.encode()
    item = ctypes.c_void_p()
    status = security.SecKeychainFindGenericPassword(
        None,
        len(service_bytes), ctypes.c_char_p(service_bytes),
        len(account_bytes), ctypes.c_char_p(account_bytes),
        None, None, ctypes.byref(item),
    )
    if status == 0:
        try:
            status = security.SecKeychainItemDelete(item)
        finally:
            core = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
            core.CFRelease.argtypes = [ctypes.c_void_p]
            core.CFRelease(item)
    return status


def doctor_counts(client: BrainClient) -> tuple[int, int]:
    doctor = client.doctor()
    return doctor["source_events"], doctor["live_items"]


def expected_provenance(inventory: dict) -> list[dict]:
    return [record["provenance"] for record in inventory["records"]]


def assert_receipts(
    client: BrainClient, receipts: list[str], provenances: list[dict]
) -> None:
    assert len(receipts) == len(provenances)
    for receipt, provenance in zip(receipts, provenances, strict=True):
        resolved = client.resolve(receipt)
        actual = resolved["event"]["provenance"]
        assert actual["uri"] == provenance["uri"]
        assert actual["archive"] == provenance["archive"]
        assert actual["member"] == provenance["member"]
        assert actual["original_path"] == provenance["original_path"]
        assert resolved["items"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--keychain-service", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--nonce", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()

    credential = json.load(sys.stdin)
    if not isinstance(credential, str) or not credential:
        raise ValueError("stdin must contain one credential string")

    export_root = args.workspace / "synthetic-exports"
    receipts: list[str] = []
    tombstoned: set[str] = set()
    client = None
    result = None
    try:
        export_root.mkdir(parents=True)
        jsonl = export_root / "chatgpt-synthetic.jsonl"
        jsonl.write_text(
            json.dumps({"text": f"c6c-jsonl-first-{args.nonce}"}) + "\n"
            + json.dumps({"text": f"c6c-jsonl-second-{args.nonce}"}) + "\n"
        )
        archive = export_root / "cowork-synthetic.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr(
                "conversations/alpha.json",
                json.dumps([{"text": f"c6c-zip-alpha-{args.nonce}"}]),
            )
            output.writestr(
                "cowork/beta.jsonl",
                json.dumps({"text": f"c6c-zip-target-{args.nonce}"}) + "\n"
                + json.dumps({"text": f"c6c-zip-tail-{args.nonce}"}) + "\n",
            )

        store_keychain_token(args.keychain_service, args.source_id, credential)
        assert load_keychain_token(args.keychain_service, args.source_id) == credential
        client = BrainClient(
            endpoint=args.endpoint,
            token=credential,
            source_id=args.source_id,
            principal_id="owner",
            visibility="private",
        )
        memory = MemoryClient(
            endpoint=args.endpoint,
            token=credential,
            source_id=args.source_id,
            principal_id="owner",
            visibility="private",
        )
        importer = ExportImporter(
            source_id=args.source_id, principal_id="owner", visibility="private"
        )

        assert doctor_counts(client) == (0, 0)
        jsonl_inventory = importer.inventory([jsonl])
        zip_inventory = importer.inventory([archive])
        assert len(jsonl_inventory["records"]) == 2
        assert len(zip_inventory["records"]) == 3

        first_jsonl = importer.import_with(client, [jsonl])
        first_zip = importer.import_with(client, [archive])
        jsonl_receipts = first_jsonl["acknowledgement"]["receipts"]
        zip_receipts = first_zip["acknowledgement"]["receipts"]
        receipts = [*jsonl_receipts, *zip_receipts]
        assert first_jsonl["acknowledgement"]["inserted"] == 2
        assert first_zip["acknowledgement"]["inserted"] == 3
        assert doctor_counts(client) == (5, 5)
        assert_receipts(client, jsonl_receipts, expected_provenance(jsonl_inventory))
        assert_receipts(client, zip_receipts, expected_provenance(zip_inventory))

        replay_jsonl = importer.import_with(client, [jsonl])
        replay_zip = importer.import_with(client, [archive])
        assert replay_jsonl["acknowledgement"]["replay"] is True
        assert replay_zip["acknowledgement"]["replay"] is True
        assert replay_jsonl["acknowledgement"]["receipts"] == jsonl_receipts
        assert replay_zip["acknowledgement"]["receipts"] == zip_receipts
        assert doctor_counts(client) == (5, 5)

        target = f"c6c-zip-target-{args.nonce}"
        searched = client.search(target, limit=5)
        assert searched["results"]
        target_hit = searched["results"][0]
        assert target_hit["source_id"] == args.source_id
        assert target_hit["path"].endswith("/cowork/beta.jsonl#record=0")
        assert target_hit["text"] == target

        traversal = export_root / "traversal.zip"
        with zipfile.ZipFile(traversal, "w") as output:
            output.writestr("../escape.json", json.dumps({"text": "must-not-write"}))
        before_attack = doctor_counts(client)
        try:
            importer.import_with(client, [traversal])
        except PrivacyError:
            traversal_rejected = True
        else:
            raise AssertionError("archive traversal reached ingest")
        assert doctor_counts(client) == before_attack

        linked = export_root / "linked.jsonl"
        os.symlink(jsonl, linked)
        try:
            importer.import_with(client, [linked])
        except PrivacyError:
            symlink_rejected = True
        else:
            raise AssertionError("symlink export input reached ingest")
        assert doctor_counts(client) == before_attack

        for receipt in receipts:
            deletion = memory.delete(receipt)
            tombstoned.add(receipt)
            assert deletion["receipt"].endswith("?rev=2")
        assert doctor_counts(client) == (10, 0)
        assert client.search(args.nonce, limit=10)["results"] == []
        for receipt in receipts:
            try:
                client.resolve(receipt)
            except urllib.error.HTTPError as error:
                assert error.code == 404
            else:
                raise AssertionError("tombstoned export receipt still resolves")

        result = {
            "status": "pass",
            "summary": {
                "jsonl_items": 2,
                "zip_items": 3,
                "resolved_provenance": 5,
                "jsonl_replay": True,
                "zip_replay": True,
                "canonical_events_before_rollback": 5,
                "live_items_before_rollback": 5,
                "unique_nonce_member_exact": True,
                "traversal_rejected_before_write": traversal_rejected,
                "symlink_rejected_before_write": symlink_rejected,
                "source_events_after_rollback": 10,
                "live_items_after_rollback": 0,
                "old_receipts_hidden": 5,
                "credential_bytes_rendered": False,
            },
        }
    finally:
        if client is not None:
            fallback = MemoryClient(
                endpoint=args.endpoint,
                token=credential,
                source_id=args.source_id,
                principal_id="owner",
                visibility="private",
            )
            for receipt in receipts:
                if receipt not in tombstoned:
                    try:
                        fallback.delete(receipt)
                    except Exception:
                        pass
        status = delete_keychain_token(args.keychain_service, args.source_id)
        if status not in {0, ITEM_NOT_FOUND}:
            raise RuntimeError(f"Keychain cleanup failed with OSStatus {status}")
        shutil.rmtree(export_root, ignore_errors=True)

    try:
        load_keychain_token(args.keychain_service, args.source_id)
    except RuntimeError as error:
        if f"OSStatus {ITEM_NOT_FOUND}" not in str(error):
            raise
    else:
        raise AssertionError("Keychain credential residue remains")
    assert not export_root.exists()
    assert result is not None
    result["summary"]["keychain_residue"] = 0
    result["summary"]["generated_export_residue"] = 0
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
