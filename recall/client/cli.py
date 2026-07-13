from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from client.mac import (
    BrainClient,
    ExportImporter,
    MemoryClient,
    dry_run_manifest,
    load_file_token,
    load_keychain_token,
)
from collector.collector import Collector


def _token(args) -> str:
    if args.token_file:
        return load_file_token(Path(args.token_file))
    return load_keychain_token(args.keychain_service, args.keychain_account)


def _auth(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--token-file")
    group.add_argument("--keychain-service")
    parser.add_argument("--keychain-account")


def _connection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--principal-id", default="owner")
    parser.add_argument("--visibility", choices=("private", "shared"), required=True)
    _auth(parser)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Consent-first Recall Brain client")
    commands = root.add_subparsers(dest="command", required=True)
    dry = commands.add_parser("dry-run")
    dry.add_argument("--visibility", choices=("private", "shared"), required=True)
    dry.add_argument("--claude-root")
    dry.add_argument("--codex-root")

    collect = commands.add_parser("collect")
    _connection(collect)
    collect.add_argument("--harness", choices=("claude", "codex"), required=True)
    collect.add_argument("--root", required=True)
    collect.add_argument("--spool", required=True)

    export = commands.add_parser("export")
    _connection(export)
    export.add_argument("--dry-run", action="store_true")
    export.add_argument("inputs", nargs="+")

    put = commands.add_parser("put")
    _connection(put)
    put.add_argument("--text")
    put.add_argument("--provenance-uri", default="manual://recall_put")

    delete = commands.add_parser("delete")
    _connection(delete)
    delete.add_argument("receipt")

    search = commands.add_parser("search")
    _connection(search)
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)

    show = commands.add_parser("show")
    _connection(show)
    show.add_argument("receipt")

    doctor = commands.add_parser("doctor")
    _connection(doctor)
    return root


def main() -> None:
    args = parser().parse_args()
    if args.command == "dry-run":
        selections = []
        if args.claude_root:
            selections.append({"harness": "claude", "root": args.claude_root})
        if args.codex_root:
            selections.append({"harness": "codex", "root": args.codex_root})
        print(json.dumps(dry_run_manifest(selections=selections, visibility=args.visibility), sort_keys=True))
        return

    if args.keychain_service and not args.keychain_account:
        raise SystemExit("--keychain-account is required with --keychain-service")
    token = _token(args)
    common = {
        "endpoint": args.endpoint,
        "token": token,
        "source_id": args.source_id,
        "principal_id": args.principal_id,
        "visibility": args.visibility,
    }
    if args.command == "collect":
        collector = Collector(
            root=Path(args.root), harness=args.harness, source_id=args.source_id,
            spool_path=Path(args.spool), endpoint=args.endpoint, token=token,
            principal_id=args.principal_id, visibility=args.visibility,
        )
        try:
            result = {"scan": collector.scan(), "flush": collector.flush(), "doctor": collector.doctor()}
        finally:
            collector.close()
    elif args.command == "export":
        importer = ExportImporter(source_id=args.source_id, principal_id=args.principal_id, visibility=args.visibility)
        inventory = importer.inventory([Path(value) for value in args.inputs])
        if args.dry_run:
            result = {**inventory, "records": len(inventory["records"])}
        else:
            result = importer.import_with(BrainClient(**common), [Path(value) for value in args.inputs])
    elif args.command == "put":
        text = args.text if args.text is not None else sys.stdin.read()
        result = MemoryClient(**common).put(text, provenance={"uri": args.provenance_uri})
    elif args.command == "delete":
        result = MemoryClient(**common).delete(args.receipt)
    elif args.command == "search":
        result = BrainClient(**common).search(args.query, limit=args.limit)
    elif args.command == "show":
        result = BrainClient(**common).resolve(args.receipt)
    else:
        result = BrainClient(**common).doctor()
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
