from __future__ import annotations

import argparse
import json
import logging
import os

from . import SCHEMA_VERSION
from .app import serve, serve_unix
from .db import BrainStore
from .federation import QUALITY_SCORES, SOURCE_FAMILIES
from .semantic import SemanticRuntime


def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(prog="recall-server")
    ap.add_argument("--dsn", default=os.environ.get("RECALL_DATABASE_URL"))
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    sub.add_parser("rebuild")
    backfill_entities = sub.add_parser("backfill-entities")
    backfill_entities.add_argument("--batch-size", type=int, default=5000)
    backfill_entities.add_argument("--max-batches", type=int)
    backfill_redaction = sub.add_parser("backfill-redaction")
    backfill_redaction.add_argument("--batch-size", type=int, default=5000)
    backfill_redaction.add_argument("--max-batches", type=int)
    backfill_redaction.add_argument("--workers", type=int, default=1)
    backfill_cowork = sub.add_parser("backfill-cowork-sessions")
    backfill_cowork.add_argument("--batch-size", type=int, default=5000)
    backfill_cowork.add_argument("--max-batches", type=int)
    backfill_embeddings = sub.add_parser("backfill-embeddings")
    backfill_embeddings.add_argument("--batch-size", type=int, default=128)
    backfill_embeddings.add_argument("--max-batches", type=int)
    backfill_embeddings.add_argument("--source-id")
    sub.add_parser("export")
    create_token = sub.add_parser("token-create"); create_token.add_argument("name"); create_token.add_argument("--source"); create_token.add_argument("--scopes", default="read,write"); create_token.add_argument("--output", required=True, help="write the one-time plaintext credential to a new mode-0600 file")
    revoke_token = sub.add_parser("token-revoke"); revoke_token.add_argument("name")
    source_profile = sub.add_parser("source-profile-set")
    source_profile.add_argument("source_id")
    source_profile.add_argument("--family", choices=sorted(SOURCE_FAMILIES), required=True)
    source_profile.add_argument("--quality", choices=sorted(QUALITY_SCORES), required=True)
    source_profile.add_argument("--freshness-half-life-days", type=int, required=True)
    source_alias = sub.add_parser("source-alias-set")
    source_alias.add_argument("alias")
    source_alias.add_argument("source_id")
    sub.add_parser("federation-scoreboard")
    server = sub.add_parser("serve"); server.add_argument("--host", default="127.0.0.1"); server.add_argument("--port", type=int, default=8788); server.add_argument("--unix-socket")
    args = ap.parse_args()
    if not args.dsn:
        ap.error("--dsn or RECALL_DATABASE_URL is required")
    store = BrainStore(args.dsn, semantic_runtime=SemanticRuntime.from_env())
    if args.command == "migrate":
        store.migrate(); print(json.dumps({"status": "ok", "schema_version": SCHEMA_VERSION}))
    elif args.command == "rebuild":
        print(json.dumps(store.rebuild(), sort_keys=True))
    elif args.command == "backfill-entities":
        print(json.dumps(store.backfill_entities(args.batch_size, args.max_batches), sort_keys=True))
    elif args.command == "backfill-redaction":
        print(json.dumps(store.backfill_redaction(
            args.batch_size, args.max_batches, args.workers,
        ), sort_keys=True))
    elif args.command == "backfill-cowork-sessions":
        print(json.dumps(store.backfill_cowork_sessions(
            args.batch_size, args.max_batches,
        ), sort_keys=True))
    elif args.command == "backfill-embeddings":
        print(json.dumps(store.embed_pending(
            args.batch_size, args.max_batches, args.source_id,
        ), sort_keys=True))
    elif args.command == "export":
        for envelope in store.export_raw(): print(json.dumps(envelope, sort_keys=True))
    elif args.command == "token-create":
        credential = store.create_collector_token(args.name, args.source, [scope.strip() for scope in args.scopes.split(",") if scope.strip()])
        payload = (json.dumps(credential, sort_keys=True) + "\n").encode()
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
        print(json.dumps({key: value for key, value in credential.items() if key != "token"}, sort_keys=True))
    elif args.command == "token-revoke":
        print(json.dumps({"revoked": store.revoke_collector_token(args.name)}))
    elif args.command == "source-profile-set":
        print(json.dumps(store.set_source_profile({
            "source_id": args.source_id,
            "family": args.family,
            "quality": args.quality,
            "freshness_half_life_days": args.freshness_half_life_days,
        }), sort_keys=True))
    elif args.command == "source-alias-set":
        print(json.dumps(store.set_source_alias(args.alias, args.source_id), sort_keys=True))
    elif args.command == "federation-scoreboard":
        print(json.dumps(store.federation_scoreboard(), sort_keys=True))
    else:
        if args.unix_socket:
            serve_unix(args.dsn, args.unix_socket)
        else:
            serve(args.dsn, args.host, args.port)


if __name__ == "__main__":
    main()
