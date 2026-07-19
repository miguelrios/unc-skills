#!/usr/bin/env python3
"""Local-API plus fresh-PostgreSQL Grep AI connector lifecycle proof."""

from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import sys
import tempfile
import threading
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "recall"))
sys.path.insert(0, str(ROOT / "recall/server"))

from connectors.grep_ai import GrepAIConnector, GrepAIResponse
from connectors.sdk import ConnectorPage, ConnectorRecord, ConnectorRunner
from privacy.policy import PrivacyPolicy
from recall_server.db import BrainStore
from recall_server.projectors import canonical_json


SOURCE = "grep-ai:synthetic:postgres"
OTHER_SOURCE = "grep-ai:synthetic:other"
KEY = "parcha-synthetic-" + "b" * 32
CANARY = "grep-ai-private-canary-77"
PROVIDER_CANARY = "sk-proj-" + "A" * 48
RAW_CANARY = "grep-ai-raw-response-canary-98"
CORPUS = ROOT / "recall/tests/grep_ai_v2/corpus.jsonl"


def fixtures() -> tuple[dict, dict]:
    rows = {row["case"]: row for row in map(json.loads, CORPUS.read_text().splitlines())}
    safe, private = rows["complete"], rows["sensitive-complete"]
    details = {**safe["details"], **private["details"]}
    private_detail = details[private["list"]["items"][0]["job_id"]]
    private_detail["report"]["markdown"] += " standalone " + PROVIDER_CANARY
    next(iter(details.values()))["context"] = {"ignored": RAW_CANARY}
    page = {
        "items": [safe["list"]["items"][0], private["list"]["items"][0]],
        "next_cursor": None, "has_more": False,
    }
    return page, details


class APIState:
    def __init__(self):
        self.page, self.details = fixtures()
        self.requests = 0


class Handler(BaseHTTPRequestHandler):
    state: APIState

    def log_message(self, _format, *_args):
        return

    def do_GET(self):
        self.state.requests += 1
        if self.headers.get("Authorization") != "Bearer " + KEY:
            self.send_response(401); self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(json.dumps({"error": {"code": "unauthenticated"}}).encode())
            return
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/v2/research":
            value = self.state.page
        elif parsed.path.startswith("/api/v2/research/"):
            value = self.state.details[parsed.path.rsplit("/", 1)[-1]]
        else:
            self.send_response(404); self.end_headers(); return
        body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)


class LocalHTTPTransport:
    def __init__(self, base: str):
        self.base = base

    def request(self, *, path, query, headers, timeout, max_bytes):
        url = self.base + path + (("?" + urllib.parse.urlencode(query)) if query else "")
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return GrepAIResponse(
                    status=response.status,
                    headers={key.casefold(): value for key, value in response.headers.items()},
                    body=response.read(max_bytes + 1),
                    final_url="https://api.grep.ai" + path,
                )
        except urllib.error.HTTPError as error:
            return GrepAIResponse(
                status=error.code,
                headers={key.casefold(): value for key, value in error.headers.items()},
                body=error.read(max_bytes + 1),
                final_url="https://api.grep.ai" + path,
            )


class StoreWriter:
    def __init__(self, store: BrainStore, *, fail_after_commit: bool = False):
        self.store = store
        self.fail_after_commit = fail_after_commit

    def ingest(self, events):
        key = "grep-ai-e2e-" + hashlib.sha256(canonical_json(events)).hexdigest()
        acknowledgement, replay = self.store.ingest(key, events)
        if self.fail_after_commit:
            self.fail_after_commit = False
            raise OSError("synthetic lost acknowledgement")
        return {**acknowledgement, "replay": replay}


class DeleteOne:
    connector_id = "grep.ai"

    def __init__(self, source_id: str, record: ConnectorRecord):
        self.source_id = source_id
        self.record = record

    def pull(self, _cursor):
        tombstone = ConnectorRecord(
            schema_version=1, native_id=self.record.native_id,
            occurred_at="2026-07-14T07:00:00Z", content={},
            provenance=self.record.provenance, deleted=True,
        )
        return ConnectorPage(records=(tombstone,), next_cursor="grep-ai-delete:done", has_more=False)


def main() -> None:
    state = APIState()
    Handler.state = state
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    store = BrainStore(os.environ["RECALL_DATABASE_URL"])
    store.migrate()
    with store.connect() as connection:
        connection.execute(
            "TRUNCATE chunks,items,sessions,projection_watermarks,source_events,"
            "ingest_batches,source_grants,sources,dead_letters,audit_events RESTART IDENTITY CASCADE"
        )
    try:
        with tempfile.TemporaryDirectory(prefix="recall-grep-ai-e2e-") as temporary:
            root = Path(temporary)
            adapter = GrepAIConnector(
                api_key=KEY, source_id=SOURCE,
                transport=LocalHTTPTransport(f"http://127.0.0.1:{server.server_port}"),
                page_size=10,
            )
            writer = StoreWriter(store, fail_after_commit=True)
            spool = root / "grep-ai.db"
            runner = ConnectorRunner(
                connector=adapter, brain=writer, spool_path=spool,
                privacy=PrivacyPolicy(mode="drop"),
            )
            try:
                runner.run_once(); raise AssertionError("lost acknowledgement was not raised")
            except Exception as error:
                assert str(error) == "brain_unavailable"
            requests_after_commit = state.requests
            assert requests_after_commit == 3
            assert CANARY.encode() not in spool.read_bytes()
            assert PROVIDER_CANARY.encode() not in spool.read_bytes()
            assert RAW_CANARY.encode() not in spool.read_bytes()
            runner.close()

            recovered = ConnectorRunner(
                connector=adapter, brain=writer, spool_path=spool,
                privacy=PrivacyPolicy(mode="drop"),
            )
            replay = recovered.run_once()
            assert replay["replayed"] == 1
            assert state.requests == requests_after_commit
            safe_results = store.search("Safe report citation", authorized_source=SOURCE)["results"]
            assert len(safe_results) == 1
            assert store.search(CANARY, authorized_source=SOURCE)["results"] == []
            assert store.search(PROVIDER_CANARY, authorized_source=SOURCE)["results"] == []
            safe_native_id = safe_results[0]["native_id"]
            recovered.close()
            record = ConnectorRecord(
                schema_version=1, native_id=safe_native_id,
                occurred_at="2026-07-14T00:02:00Z", content={},
                provenance={"uri": "connector://grep-ai/completed-research", "provider": "grep.ai"},
                deleted=False,
            )
            wrong = ConnectorRunner(
                connector=DeleteOne(OTHER_SOURCE, record), brain=StoreWriter(store),
                spool_path=root / "wrong.db", privacy=PrivacyPolicy(mode="drop"),
            )
            wrong.run_once(); wrong.close()
            assert store.search("Safe report citation", authorized_source=SOURCE)["results"]
            deletion = ConnectorRunner(
                connector=DeleteOne(SOURCE, record), brain=StoreWriter(store),
                spool_path=root / "delete.db", privacy=PrivacyPolicy(mode="drop"),
            )
            deletion.run_once(); deletion.close()
            assert store.search("Safe report citation", authorized_source=SOURCE)["results"] == []
            with store.connect() as connection:
                live = connection.execute(
                    "SELECT count(*) AS n FROM items WHERE source_id=%s AND deleted_at IS NULL", (SOURCE,)
                ).fetchone()["n"]
            assert live == 0
            print(json.dumps({
                "status": "pass", "local_api_requests": state.requests,
                "api_refetches_on_replay": 0, "safe_search_hits_before_delete": 1,
                "safe_search_hits_after_delete": 0, "canary_search_hits": 0,
                "raw_response_spool_bytes": 0, "cross_source_deletes": 0,
                "live_after_delete": live,
            }, sort_keys=True))
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == "__main__":
    main()
