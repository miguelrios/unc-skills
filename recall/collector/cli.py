from __future__ import annotations

import argparse
import json
import os
import stat
import time
from pathlib import Path

from .collector import Collector


def token_from_file(path: str) -> str:
    token_path = Path(path).expanduser()
    mode = stat.S_IMODE(token_path.stat().st_mode)
    if mode & 0o077:
        raise PermissionError("collector token file must not be accessible by group or other")
    data = json.loads(token_path.read_text())
    token = data.get("token")
    if not isinstance(token, str) or not token:
        raise ValueError("token file has no token")
    return token


def main() -> None:
    parser = argparse.ArgumentParser(description="Acknowledgement-gated Recall history collector")
    parser.add_argument("command", choices=("scan", "flush", "run", "watch", "doctor", "locate", "recover"))
    parser.add_argument("--harness", choices=("claude", "codex"), required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--spool", required=True)
    parser.add_argument("--endpoint", default=os.environ.get("RECALL_ENDPOINT", ""))
    parser.add_argument("--token-file", default=os.environ.get("RECALL_COLLECTOR_TOKEN_FILE", ""))
    parser.add_argument("--principal-id", default="owner")
    parser.add_argument("--visibility", choices=("private", "shared"), default="private")
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--receipt")
    args = parser.parse_args()
    if args.command in {"flush", "run", "watch"} and (not args.endpoint or not args.token_file):
        parser.error("--endpoint and --token-file are required for network commands")
    collector = Collector(
        root=Path(args.root), harness=args.harness, source_id=args.source_id,
        spool_path=Path(args.spool), endpoint=args.endpoint or "http://127.0.0.1:1",
        token=token_from_file(args.token_file) if args.token_file else "unused",
        principal_id=args.principal_id,
        visibility=args.visibility,
    )
    if args.shard_count < 1 or not 0 <= args.shard_index < args.shard_count:
        parser.error("shard index must be within shard count")
    collector.shard_count = args.shard_count
    collector.shard_index = args.shard_index
    try:
        if args.command == "scan":
            print(json.dumps(collector.scan(), sort_keys=True))
        elif args.command == "flush":
            print(json.dumps(collector.flush(), sort_keys=True))
        elif args.command == "run":
            print(json.dumps({"scan": collector.scan(), "flush": collector.flush(), "doctor": collector.doctor()}, sort_keys=True))
        elif args.command == "doctor":
            print(json.dumps(collector.doctor(), sort_keys=True))
        elif args.command == "recover":
            print(json.dumps(collector.recover_dead_payloads(), sort_keys=True))
        elif args.command == "locate":
            if not args.receipt:
                parser.error("--receipt is required for locate")
            print(json.dumps(collector.locate_receipt(args.receipt), sort_keys=True))
        else:
            while True:
                print(json.dumps({"scan": collector.scan(), "flush": collector.flush(), "doctor": collector.doctor(include_dead_letters=False)}, sort_keys=True), flush=True)
                time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        collector.close()


if __name__ == "__main__":
    main()
