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
    store_keychain_token,
)
from collector.collector import Collector
from privacy.policy import AgenticJudge, PrivacyPolicy, load_scoped_virtual_key


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


def _privacy(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--privacy-mode", choices=("off", "scrub", "drop"), default=os.environ.get("RECALL_PRIVACY_MODE", "off"))
    parser.add_argument("--privacy-judge-base-url", default=os.environ.get("RECALL_PRIVACY_JUDGE_BASE_URL"))
    parser.add_argument("--privacy-judge-key-file", default=os.environ.get("RECALL_PRIVACY_JUDGE_KEY_FILE"))
    parser.add_argument("--privacy-judge-model", default=os.environ.get("RECALL_PRIVACY_JUDGE_MODEL"))
    parser.add_argument("--privacy-judge-failure", choices=("drop", "ignore"), default="drop")


def _privacy_policy(args) -> PrivacyPolicy:
    values = (args.privacy_judge_base_url, args.privacy_judge_key_file, args.privacy_judge_model)
    if any(values) and not all(values):
        raise SystemExit("privacy judge requires base URL, private virtual-key file, and model")
    judge = AgenticJudge(
        base_url=args.privacy_judge_base_url,
        virtual_key=load_scoped_virtual_key(Path(args.privacy_judge_key_file)),
        model=args.privacy_judge_model,
    ) if all(values) else None
    return PrivacyPolicy(mode=args.privacy_mode, judge=judge, judge_failure=args.privacy_judge_failure)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Consent-first Recall Brain client")
    commands = root.add_subparsers(dest="command", required=True)
    dry = commands.add_parser("dry-run")
    dry.add_argument("--visibility", choices=("private", "shared"), required=True)
    dry.add_argument("--claude-root")
    dry.add_argument("--codex-root")

    keychain = commands.add_parser("keychain-store")
    keychain.add_argument("--service", required=True)
    keychain.add_argument("--account", required=True)

    collect = commands.add_parser("collect")
    _connection(collect)
    _privacy(collect)
    collect.add_argument("--harness", choices=("claude", "codex"), required=True)
    collect.add_argument("--root", required=True)
    collect.add_argument("--spool", required=True)

    export = commands.add_parser("export")
    _connection(export)
    _privacy(export)
    export.add_argument("--dry-run", action="store_true")
    export.add_argument("inputs", nargs="+")

    put = commands.add_parser("put")
    _connection(put)
    _privacy(put)
    put.add_argument("--text")
    put.add_argument("--provenance-uri", default="manual://recall_put")

    preview = commands.add_parser("privacy-preview")
    _privacy(preview)

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
    if args.command == "keychain-store":
        store_keychain_token(args.service, args.account, sys.stdin.read().rstrip("\r\n"))
        print(json.dumps({"status": "stored", "service": args.service, "account": args.account}, sort_keys=True))
        return
    if args.command == "privacy-preview":
        raw = sys.stdin.read()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        print(json.dumps(_privacy_policy(args).apply(value).receipt(), sort_keys=True))
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
    privacy = _privacy_policy(args) if args.command in {"collect", "export", "put"} else PrivacyPolicy(mode="off")
    if args.command == "collect":
        collector = Collector(
            root=Path(args.root), harness=args.harness, source_id=args.source_id,
            spool_path=Path(args.spool), endpoint=args.endpoint, token=token,
            principal_id=args.principal_id, visibility=args.visibility,
            privacy=privacy,
        )
        try:
            result = {"scan": collector.scan(), "flush": collector.flush(), "doctor": collector.doctor()}
        finally:
            collector.close()
    elif args.command == "export":
        importer = ExportImporter(source_id=args.source_id, principal_id=args.principal_id, visibility=args.visibility, privacy=privacy)
        inventory = importer.inventory([Path(value) for value in args.inputs])
        if args.dry_run:
            result = {**inventory, "records": len(inventory["records"])}
        else:
            result = importer.import_with(BrainClient(**common, privacy=privacy), [Path(value) for value in args.inputs])
    elif args.command == "put":
        text = args.text if args.text is not None else sys.stdin.read()
        result = MemoryClient(**common, privacy=privacy).put(text, provenance={"uri": args.provenance_uri})
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
