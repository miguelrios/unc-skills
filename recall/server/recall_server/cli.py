from __future__ import annotations

import argparse
import json
import os

from . import SCHEMA_VERSION
from .app import serve, serve_unix
from .db import BrainStore


def main() -> None:
    ap = argparse.ArgumentParser(prog="recall-server")
    ap.add_argument("--dsn", default=os.environ.get("RECALL_DATABASE_URL"))
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    sub.add_parser("rebuild")
    sub.add_parser("export")
    create_token = sub.add_parser("token-create"); create_token.add_argument("name"); create_token.add_argument("--source"); create_token.add_argument("--scopes", default="read,write")
    revoke_token = sub.add_parser("token-revoke"); revoke_token.add_argument("name")
    server = sub.add_parser("serve"); server.add_argument("--host", default="127.0.0.1"); server.add_argument("--port", type=int, default=8788); server.add_argument("--unix-socket")
    args = ap.parse_args()
    if not args.dsn:
        ap.error("--dsn or RECALL_DATABASE_URL is required")
    store = BrainStore(args.dsn)
    if args.command == "migrate":
        store.migrate(); print(json.dumps({"status": "ok", "schema_version": SCHEMA_VERSION}))
    elif args.command == "rebuild":
        print(json.dumps(store.rebuild(), sort_keys=True))
    elif args.command == "export":
        for envelope in store.export_raw(): print(json.dumps(envelope, sort_keys=True))
    elif args.command == "token-create":
        print(json.dumps(store.create_collector_token(args.name, args.source, [scope.strip() for scope in args.scopes.split(",") if scope.strip()]), sort_keys=True))
    elif args.command == "token-revoke":
        print(json.dumps({"revoked": store.revoke_collector_token(args.name)}))
    else:
        if args.unix_socket:
            serve_unix(args.dsn, args.unix_socket)
        else:
            serve(args.dsn, args.host, args.port)


if __name__ == "__main__":
    main()
