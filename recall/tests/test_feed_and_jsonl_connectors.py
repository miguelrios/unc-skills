from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from email.message import Message
from pathlib import Path
from unittest import mock

from client.cli import parser
from connectors.feeds import FeedConnector, FeedResponse, HttpsFeedTransport
from connectors.registry import definition
from connectors.selected_jsonl import SelectedJsonlConnector
from connectors.sdk import ConnectorContractError, ConnectorRunner
from privacy.policy import PrivacyPolicy


RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Synthetic feed</title>
<item><guid>entry-1</guid><title>First entry</title>
<link>https://example.invalid/entry-1</link>
<pubDate>Fri, 17 Jul 2026 10:00:00 +0000</pubDate>
<description>synthetic feed marker</description></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Synthetic Atom</title>
<entry><id>urn:synthetic:entry-2</id><title>Second entry</title>
<updated>2026-07-18T10:00:00Z</updated>
<content>synthetic atom marker</content></entry>
</feed>"""


class FeedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def fetch(self, url, *, etag=None, last_modified=None):
        self.calls.append((url, etag, last_modified))
        return self.responses.pop(0)


class HttpResponse:
    def __init__(self, body=RSS):
        self.body = body
        self.headers = Message()
        self.headers["Content-Type"] = "application/rss+xml"
        self.headers["ETag"] = '"network"'
        self.closed = False

    def getcode(self):
        return 200

    def read(self, maximum):
        return self.body[:maximum]

    def close(self):
        self.closed = True


class Brain:
    def __init__(self):
        self.events = {}

    def ingest(self, events):
        inserted = 0
        duplicates = 0
        receipts = []
        for event in events:
            key = (event["native_id"], event["content_sha256"])
            if key in self.events:
                duplicates += 1
            else:
                inserted += 1
                self.events[key] = event
            receipts.append(
                f"recall://{event['source_id']}/{event['native_id']}?rev=1"
            )
        return {
            "status": "committed",
            "inserted": inserted,
            "duplicate_events": duplicates,
            "receipts": receipts,
            "replay": bool(duplicates),
        }


class FeedAndJsonlConnectorTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_registry_contracts_are_explicit_and_bounded(self):
        feed = definition("portable.feed")
        self.assertEqual(feed.execution_placement, "either")
        self.assertEqual(feed.acquisition_modes, ("poll",))
        self.assertEqual(feed.auth.kind, "none")
        self.assertEqual(feed.selection_fields, ("feed_id", "url"))
        jsonl = definition("portable.jsonl")
        self.assertEqual(jsonl.execution_placement, "source_local")
        self.assertEqual(jsonl.acquisition_modes, ("import", "snapshot"))
        self.assertEqual(jsonl.auth.kind, "selected_export")
        self.assertEqual(
            jsonl.selection_fields, ("max_depth", "removed_native_ids", "root")
        )
        feed_args = parser().parse_args([
            "feed-sync", "--endpoint", "https://brain.example.invalid",
            "--source-id", "portable:feed:test", "--keychain-service", "synthetic",
            "--keychain-account", "portable:feed:test",
            "--url", "https://feeds.example.invalid/rss.xml",
            "--feed-id", "synthetic-feed", "--spool", "/synthetic/feed.db",
        ])
        self.assertEqual(feed_args.privacy_mode, "scrub")
        jsonl_args = parser().parse_args([
            "jsonl-import-sync", "--endpoint", "https://brain.example.invalid",
            "--source-id", "portable:jsonl:test", "--keychain-service", "synthetic",
            "--keychain-account", "portable:jsonl:test",
            "--root", "/synthetic/selected", "--spool", "/synthetic/jsonl.db",
        ])
        self.assertEqual(jsonl_args.privacy_mode, "scrub")

    def test_rss_conditional_poll_and_atom_identity(self):
        transport = FeedTransport([
            FeedResponse(200, RSS, '"v1"', "Fri, 17 Jul 2026 10:00:00 GMT"),
            FeedResponse(304, b"", '"v1"', "Fri, 17 Jul 2026 10:00:00 GMT"),
        ])
        connector = FeedConnector(
            url="https://feeds.example.invalid/rss.xml",
            feed_id="synthetic-feed", source_id="portable:feed:test",
            transport=transport,
        )
        first = connector.pull(None)
        self.assertEqual(len(first.records), 1)
        self.assertEqual(first.records[0].content["text"], "synthetic feed marker")
        unchanged = connector.pull(first.next_cursor)
        self.assertEqual(unchanged.records, ())
        self.assertEqual(
            transport.calls[1][1:],
            ('"v1"', "Fri, 17 Jul 2026 10:00:00 GMT"),
        )

        atom = FeedConnector(
            url="https://feeds.example.invalid/atom.xml",
            feed_id="synthetic-atom", source_id="portable:feed:atom",
            transport=FeedTransport([FeedResponse(200, ATOM, None, None)]),
        ).pull(None)
        self.assertEqual(atom.records[0].content["text"], "synthetic atom marker")

    def test_feed_edits_keep_identity_and_absence_never_deletes(self):
        edited = RSS.replace(b"synthetic feed marker", b"synthetic revised marker")
        empty = b"<rss version='2.0'><channel><title>Empty</title></channel></rss>"
        connector = FeedConnector(
            url="https://feeds.example.invalid/rss.xml",
            feed_id="synthetic-feed", source_id="portable:feed:test",
            transport=FeedTransport([
                FeedResponse(200, RSS, '"v1"', None),
                FeedResponse(200, edited, '"v2"', None),
                FeedResponse(200, empty, '"v3"', None),
            ]),
        )
        first = connector.pull(None)
        changed = connector.pull(first.next_cursor)
        self.assertEqual(first.records[0].native_id, changed.records[0].native_id)
        self.assertEqual(changed.records[0].content["text"], "synthetic revised marker")
        missing = connector.pull(changed.next_cursor)
        self.assertEqual(missing.records, ())

    def test_feed_malformed_oversized_and_non_https_fail_closed(self):
        with self.assertRaisesRegex(ConnectorContractError, "feed_url_invalid"):
            FeedConnector(
                url="http://feeds.example.invalid/rss.xml",
                feed_id="synthetic-feed", source_id="portable:feed:test",
                transport=FeedTransport([]),
            )
        malformed = FeedConnector(
            url="https://feeds.example.invalid/rss.xml",
            feed_id="synthetic-feed", source_id="portable:feed:test",
            transport=FeedTransport([FeedResponse(200, b"<rss>", None, None)]),
        )
        with self.assertRaisesRegex(ConnectorContractError, "feed_xml_invalid"):
            malformed.pull(None)

    def test_https_feed_transport_is_conditional_bounded_and_no_redirect(self):
        response = HttpResponse()
        requests = []

        def opener(request, *, timeout):
            requests.append((request, timeout))
            return response

        transport = HttpsFeedTransport(opener=opener)
        with mock.patch("connectors.feeds._public_host"):
            value = transport.fetch(
                "https://feeds.example.invalid/rss.xml",
                etag='"previous"',
                last_modified="Fri, 17 Jul 2026 10:00:00 GMT",
            )
        self.assertEqual(value.etag, '"network"')
        self.assertEqual(requests[0][0].get_header("If-none-match"), '"previous"')
        self.assertTrue(response.closed)

        def redirect(_request, *, timeout):
            raise urllib.error.HTTPError(
                "https://feeds.example.invalid/rss.xml",
                302, "redirect", Message(), None,
            )

        with mock.patch("connectors.feeds._public_host"):
            with self.assertRaisesRegex(ConnectorContractError, "feed_http_error"):
                HttpsFeedTransport(opener=redirect).fetch(
                    "https://feeds.example.invalid/rss.xml"
                )
        with mock.patch(
            "connectors.feeds.socket.getaddrinfo",
            return_value=[(None, None, None, None, ("127.0.0.1", 443))],
        ):
            with self.assertRaisesRegex(ConnectorContractError, "feed_host_invalid"):
                HttpsFeedTransport(opener=opener).fetch(
                    "https://feeds.example.invalid/rss.xml"
                )

    def test_closed_jsonl_projects_stable_records_and_explicit_removal(self):
        source = self.root / "selected"
        source.mkdir()
        file = source / "records.jsonl"
        file.write_text(
            json.dumps({
                "id": "record-1", "text": "synthetic jsonl marker",
                "title": "Synthetic record", "occurred_at": "2026-07-18T10:00:00Z",
            }) + "\n"
        )
        connector = SelectedJsonlConnector(
            root=source, source_id="portable:jsonl:test",
        )
        first = connector.pull(None)
        self.assertEqual(len(first.records), 1)
        native_id = first.records[0].native_id
        file.write_text(
            json.dumps({
                "id": "record-1", "text": "synthetic revised marker",
                "title": "Synthetic record", "occurred_at": "2026-07-18T10:00:00Z",
            }) + "\n"
        )
        changed = connector.pull(first.next_cursor)
        self.assertEqual(changed.records[0].native_id, native_id)
        file.unlink()
        self.assertEqual(connector.pull(changed.next_cursor).records, ())
        file.write_text(
            json.dumps({
                "id": "record-1", "text": "synthetic revised marker",
                "title": "Synthetic record", "occurred_at": "2026-07-18T10:00:00Z",
            }) + "\n"
        )
        removed = SelectedJsonlConnector(
            root=source, source_id="portable:jsonl:test",
            removed_native_ids=(native_id,),
        ).pull(None)
        self.assertTrue(removed.records[0].deleted)

    def test_jsonl_schema_aliases_and_bounds_fail_closed(self):
        source = self.root / "bad"
        source.mkdir()
        (source / "unknown.jsonl").write_text('{"id":"a","text":"x","extra":true}\n')
        with self.assertRaisesRegex(ConnectorContractError, "jsonl_record_invalid"):
            SelectedJsonlConnector(
                root=source, source_id="portable:jsonl:test",
            ).pull(None)
        alias = self.root / "alias"
        alias.symlink_to(source, target_is_directory=True)
        with self.assertRaisesRegex(ConnectorContractError, "local_root_symlink"):
            SelectedJsonlConnector(
                root=alias, source_id="portable:jsonl:test",
            )

    def test_privacy_and_ack_gate_cover_feed_and_jsonl(self):
        canary = "synthetic-feed-jsonl-private-canary"
        selected = self.root / "private"
        selected.mkdir()
        (selected / "records.jsonl").write_text(json.dumps({
            "id": "private-1", "text": f"api_key={canary}",
            "title": "Private synthetic",
        }) + "\n")
        connectors = (
            FeedConnector(
                url="https://feeds.example.invalid/rss.xml",
                feed_id="private-feed", source_id="portable:feed:private",
                transport=FeedTransport([FeedResponse(
                    200, RSS.replace(
                        b"synthetic feed marker", f"api_key={canary}".encode()
                    ), '"private"', None,
                )]),
            ),
            SelectedJsonlConnector(
                root=selected, source_id="portable:jsonl:private",
            ),
        )
        for index, connector in enumerate(connectors):
            brain = Brain()
            spool = self.root / f"state-{index}.db"
            runner = ConnectorRunner(
                connector=connector, brain=brain, spool_path=spool,
                privacy=PrivacyPolicy(mode="scrub"),
            )
            try:
                self.assertEqual(runner.run_once()["acked"], 1)
            finally:
                runner.close()
            self.assertNotIn(canary, spool.read_bytes().decode(errors="ignore"))
            self.assertNotIn(canary, json.dumps(list(brain.events.values())))
