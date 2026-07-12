from __future__ import annotations

import argparse
import json
import os

from .app import serve
from .db import BrainStore


def main() -> None:
    ap = argparse.ArgumentParser(prog="recall-server")
    ap.add_argument("--dsn", default=os.environ.get("RECALL_DATABASE_URL"))
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    sub.add_parser("rebuild")
    sub.add_parser("export")
    server = sub.add_parser("serve"); server.add_argument("--host", default="127.0.0.1"); server.add_argument("--port", type=int, default=8788)
    args = ap.parse_args()
    if not args.dsn:
        ap.error("--dsn or RECALL_DATABASE_URL is required")
    store = BrainStore(args.dsn)
    if args.command == "migrate":
        store.migrate(); print(json.dumps({"status": "ok", "schema_version": 1}))
    elif args.command == "rebuild":
        print(json.dumps(store.rebuild(), sort_keys=True))
    elif args.command == "export":
        for envelope in store.export_raw(): print(json.dumps(envelope, sort_keys=True))
    else:
        serve(args.dsn, args.host, args.port)


if __name__ == "__main__":
    main()
