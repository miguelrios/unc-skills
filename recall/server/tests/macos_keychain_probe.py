#!/usr/bin/env python3
"""Content-free Keychain lifecycle probe for a GUI LaunchAgent.

Secret values arrive only on stdin (normally a mode-0600 FIFO). The probe
prints counts/status, never credential bytes, and removes every item in a
finally block.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import sys

from client.mac import load_keychain_token, store_keychain_token


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", required=True)
    parser.add_argument("--account", action="append", required=True)
    args = parser.parse_args()
    values = json.load(sys.stdin)
    if not isinstance(values, list) or len(values) != len(args.account):
        raise ValueError("stdin must be a JSON list with one value per account")
    if not all(isinstance(value, str) and value for value in values):
        raise ValueError("every stdin value must be a non-empty string")

    try:
        for account, value in zip(args.account, values, strict=True):
            store_keychain_token(args.service, account, value)
        loaded = [load_keychain_token(args.service, account) for account in args.account]
        if loaded != values or len(set(loaded)) != len(loaded):
            raise AssertionError("Keychain accounts were not independently scoped")
    finally:
        statuses = [delete_keychain_token(args.service, account) for account in args.account]
        unexpected = [status for status in statuses if status not in {0, ITEM_NOT_FOUND}]
        if unexpected:
            raise RuntimeError(f"Keychain cleanup failed with OSStatus values {unexpected}")

    for account in args.account:
        try:
            load_keychain_token(args.service, account)
        except RuntimeError as exc:
            if f"OSStatus {ITEM_NOT_FOUND}" not in str(exc):
                raise
        else:
            raise AssertionError("Keychain probe left credential residue")
    print(json.dumps({
        "status": "green",
        "accounts": len(args.account),
        "independently_scoped": True,
        "credential_bytes_rendered": False,
        "residue": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
