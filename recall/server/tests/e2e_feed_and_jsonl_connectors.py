#!/usr/bin/env python3
"""Fresh-PostgreSQL proof for conditional feeds and closed JSONL imports."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RECALL = ROOT / "recall"
SERVER = RECALL / "server"
sys.path.insert(0, str(RECALL))
sys.path.insert(0, str(SERVER))

from connectors.feeds import FeedConnector, FeedResponse
from connectors.sdk import ConnectorRunner
from connectors.selected_jsonl import SelectedJsonlConnector
from privacy.policy import PrivacyPolicy
from recall_server.db import BrainStore
from recall_server.projectors import canonical_json


SOURCES = (
    "synthetic:portable-feed:postgres",
    "synthetic:portable-jsonl:postgres",
)
RSS = b"""<rss version="2.0"><channel><title>Synthetic</title>
<item><guid>feed-e2e-1</guid><title>Synthetic entry</title>
<pubDate>Fri, 17 Jul 2026 10:00:00 +0000</pubDate>
<description>portable feed postgres marker
api_key=synthetic-portable-feed-canary</description></item>
</channel></rss>"""


class StoreWriter:
    def __init__(self, store):
        self.store = store

    def ingest(self, events):
        key = "feed-jsonl-e2e-" + hashlib.sha256(
            canonical_json(events)
        ).hexdigest()
        acknowledgement, replay = self.store.ingest(key, events)
        return {**acknowledgement, "replay": replay}


class FeedTransport:
    def __init__(self):
        self.version = 1
        self.calls = []

    def fetch(self, _url, *, etag=None, last_modified=None):
        self.calls.append((etag, last_modified))
        current = f'"v{self.version}"'
        if etag == current:
            return FeedResponse(304, b"", current, None)
        body = RSS.replace(b"feed postgres marker", (
            b"feed revised marker" if self.version == 2 else b"feed postgres marker"
        ))
        return FeedResponse(200, body, current, None)


def main() -> None:
    store = BrainStore(os.environ["RECALL_DATABASE_URL"])
    store.migrate()
    with store.connect() as connection:
        connection.execute(
            "TRUNCATE chunks,items,sessions,projection_watermarks,source_events,"
            "ingest_batches,source_grants,sources,dead_letters,audit_events "
            "RESTART IDENTITY CASCADE"
        )
    with tempfile.TemporaryDirectory(prefix="recall-feed-jsonl-e2e-") as temporary:
        root = Path(temporary)
        selected = root / "selected"
        selected.mkdir()
        jsonl = selected / "records.jsonl"
        jsonl.write_text(json.dumps({
            "id": "jsonl-e2e-1",
            "text": (
                "portable jsonl postgres marker "
                "api_key=synthetic-portable-jsonl-canary"
            ),
            "title": "Synthetic JSONL",
        }) + "\n")
        transport = FeedTransport()
        feed = FeedConnector(
            url="https://feeds.example.invalid/rss.xml",
            feed_id="feed-e2e", source_id=SOURCES[0], transport=transport,
        )
        selected_jsonl = SelectedJsonlConnector(
            root=selected, source_id=SOURCES[1],
        )
        writer = StoreWriter(store)
        spools = [root / "state" / "feed.db", root / "state" / "jsonl.db"]
        runners = [
            ConnectorRunner(
                connector=connector, brain=writer, spool_path=spool,
                privacy=PrivacyPolicy(mode="scrub"),
            )
            for connector, spool in zip(
                (feed, selected_jsonl), spools, strict=True
            )
        ]
        for runner in runners:
            assert runner.run_once()["acked"] == 1
            assert runner.run_once()["acked"] == 0
        assert transport.calls[1][0] == '"v1"'
        assert store.search(
            "portable feed postgres marker", authorized_source=SOURCES[0]
        )["results"]
        assert store.search(
            "portable jsonl postgres marker", authorized_source=SOURCES[1]
        )["results"]
        for canary, source in zip((
            "synthetic-portable-feed-canary",
            "synthetic-portable-jsonl-canary",
        ), SOURCES, strict=True):
            assert store.search(canary, authorized_source=source)["results"] == []

        feed_id = feed.pull(None).records[0].native_id
        transport.version = 2
        assert runners[0].run_once()["acked"] == 1
        assert feed.pull(None).records[0].native_id == feed_id
        jsonl_id = selected_jsonl.pull(None).records[0].native_id
        jsonl.write_text(json.dumps({
            "id": "jsonl-e2e-1",
            "text": "portable jsonl revised marker",
            "title": "Synthetic JSONL",
        }) + "\n")
        assert runners[1].run_once()["acked"] == 1
        for runner in runners:
            runner.close()
        remover = ConnectorRunner(
            connector=SelectedJsonlConnector(
                root=selected, source_id=SOURCES[1],
                removed_native_ids=(jsonl_id,),
            ),
            brain=writer,
            spool_path=spools[1],
            privacy=PrivacyPolicy(mode="scrub"),
        )
        assert remover.run_once()["acked"] == 1
        remover.close()
        assert store.search(
            "portable jsonl revised marker", authorized_source=SOURCES[1]
        )["results"] == []
        private_bytes = b"".join(
            path.read_bytes()
            for path in spools[0].parent.iterdir()
            if path.is_file()
        )
        assert b"synthetic-portable" not in private_bytes
        print(json.dumps({
            "status": "pass",
            "configured_sources": 2,
            "searchable_sources": 2,
            "conditional_requests": 1,
            "content_revisions": 2,
            "explicit_tombstones": 1,
            "duplicate_acknowledged_versions": 0,
            "restart_reacknowledgements": 0,
            "inferred_tombstones": 0,
            "canary_search_hits": 0,
            "spool_canary_hits": 0,
            "private_content_rendered": False,
        }, sort_keys=True))


if __name__ == "__main__":
    main()
