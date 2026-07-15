#!/usr/bin/env python3
"""Packaged-runtime C6B proof for reversible explicit memory writes."""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import sys
import urllib.error

from client.mac import (
    BrainClient,
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


def rank_for(result: dict, receipt: str) -> int:
    for rank, item in enumerate(result["results"], 1):
        if item["receipt"].split("#", 1)[0] == receipt:
            return rank
    raise AssertionError("memory receipt was not returned by source-scoped search")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--keychain-service", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--read-account", required=True)
    parser.add_argument("--marker", required=True)
    args = parser.parse_args()

    credentials = json.load(sys.stdin)
    if not isinstance(credentials, list) or len(credentials) != 2:
        raise ValueError("stdin must contain the write and read credential values")
    if not all(isinstance(value, str) and value for value in credentials):
        raise ValueError("credentials must be non-empty strings")
    accounts = [args.source_id, args.read_account]
    receipt = None
    deleted = False
    result = None
    memory = None
    try:
        for account, value in zip(accounts, credentials, strict=True):
            store_keychain_token(args.keychain_service, account, value)
        write_token = load_keychain_token(args.keychain_service, args.source_id)
        read_token = load_keychain_token(args.keychain_service, args.read_account)
        assert [write_token, read_token] == credentials
        memory = MemoryClient(
            endpoint=args.endpoint,
            token=write_token,
            source_id=args.source_id,
            principal_id="owner",
            visibility="private",
        )
        put = memory.put(args.marker, provenance={"uri": "manual://c6b-synthetic-e2e"})
        receipt = put["receipt"]
        rank = rank_for(memory.search(args.marker, limit=5), receipt)
        assert rank == 1
        resolved = memory.resolve(receipt)
        assert resolved["event"]["visibility"] == "private"
        assert resolved["items"][0]["text_redacted"] == args.marker

        try:
            MemoryClient(
                endpoint=args.endpoint,
                token=write_token,
                source_id=args.source_id + "-wrong",
                principal_id="owner",
                visibility="private",
            ).delete(receipt)
        except PrivacyError:
            wrong_source_rejected = True
        else:
            raise AssertionError("wrong source deleted another source's receipt")

        try:
            MemoryClient(
                endpoint=args.endpoint,
                token=read_token,
                source_id=args.source_id,
                principal_id="owner",
                visibility="private",
            ).put(args.marker + " read-only")
        except urllib.error.HTTPError as error:
            assert error.code == 401
            read_only_write_rejected = True
        else:
            raise AssertionError("read-only credential performed a write")

        try:
            BrainClient(
                endpoint="http://127.0.0.1:1",
                token=write_token,
                source_id=args.source_id,
                principal_id="owner",
                visibility="private",
            ).search(args.marker, limit=5)
        except (OSError, urllib.error.URLError):
            transport_failed_closed = True
        else:
            raise AssertionError("unavailable transport returned a local fallback")

        deletion = memory.delete(receipt)
        deleted = True
        assert deletion["receipt"].endswith("?rev=2")
        assert memory.search(args.marker, limit=5)["results"] == []
        try:
            memory.resolve(receipt)
        except urllib.error.HTTPError as error:
            assert error.code == 404
            deleted_receipt_hidden = True
        else:
            raise AssertionError("deleted receipt still resolves")

        doctor = memory.doctor()
        assert doctor["source_events"] == 2
        assert doctor["live_items"] == 0
        result = {
            "status": "pass",
            "summary": {
                "put_receipt_exact": True,
                "search_rank": rank,
                "resolved_exact_marker": True,
                "visibility_private": True,
                "wrong_source_rejected": wrong_source_rejected,
                "read_only_write_rejected": read_only_write_rejected,
                "transport_failed_closed": transport_failed_closed,
                "delete_revision": 2,
                "deleted_search_empty": True,
                "deleted_receipt_hidden": deleted_receipt_hidden,
                "source_events": doctor["source_events"],
                "live_items": doctor["live_items"],
                "credential_bytes_rendered": False,
            },
        }
    finally:
        if receipt and not deleted and memory is not None:
            try:
                memory.delete(receipt)
            except Exception:
                pass
        statuses = [delete_keychain_token(args.keychain_service, account) for account in accounts]
        if any(status not in {0, ITEM_NOT_FOUND} for status in statuses):
            raise RuntimeError(f"Keychain cleanup failed with OSStatus values {statuses}")

    for account in accounts:
        try:
            load_keychain_token(args.keychain_service, account)
        except RuntimeError as error:
            if f"OSStatus {ITEM_NOT_FOUND}" not in str(error):
                raise
        else:
            raise AssertionError("Keychain credential residue remains")
    assert result is not None
    result["summary"]["keychain_residue"] = 0
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
