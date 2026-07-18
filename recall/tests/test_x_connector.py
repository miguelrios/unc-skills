from __future__ import annotations

import copy
import json
import tempfile
import unittest
from collections import defaultdict, deque
from pathlib import Path

from connectors.sdk import ConnectorRunError, ConnectorRunner, ConnectorUpstreamError
from connectors.work_apis import x_rail
from connectors.x_activity import XActivityConnector
from privacy.policy import PrivacyPolicy
from tests.test_connector_sdk import FakeBrain


class FakeRail:
    def __init__(self):
        self.responses = defaultdict(deque)
        self.calls = []

    def add(self, operation, *responses):
        self.responses[operation].extend(responses)

    def request(self, operation, **parameters):
        self.calls.append((operation, copy.deepcopy(parameters)))
        if not self.responses[operation]:
            raise AssertionError(f"unexpected synthetic operation {operation}")
        value = self.responses[operation].popleft()
        if isinstance(value, Exception):
            raise value
        return value


def post(
    post_id: str,
    *,
    text: str = "Synthetic X post",
    author: str = "888",
) -> dict:
    return {
        "id": post_id,
        "text": text,
        "author_id": author,
        "conversation_id": post_id,
        "created_at": "2026-07-18T01:00:00.000Z",
        "public_metrics": {
            "like_count": 2,
            "reply_count": 1,
            "repost_count": 0,
            "quote_count": 0,
        },
    }


class XRailTest(unittest.TestCase):
    def test_four_read_operations_and_fields_are_immutable_code(self):
        rail = x_rail(authority_path=Path("/synthetic/private/authority"))
        self.assertEqual(rail.origin, "https://api.x.com")
        self.assertEqual(
            tuple(sorted(rail.operations)),
            ("bookmarks.list", "home.list", "mentions.list", "own.list"),
        )
        for operation in rail.operations.values():
            self.assertIn("tweet.fields", operation.fixed_query)
            self.assertNotIn("tweet.fields", operation.query_fields)


class XActivityConnectorTest(unittest.TestCase):
    def test_selection_is_explicit_and_home_is_not_implicit(self):
        rail = FakeRail()
        rail.add("own.list", {
            "data": [post("100")],
            "meta": {},
        })
        connector = XActivityConnector(
            rail=rail,
            source_id="synthetic:x:activity",
            user_id="999",
            streams=("own",),
        )
        page = connector.pull(None)
        self.assertFalse(page.has_more)
        self.assertEqual(rail.calls[0][0], "own.list")
        self.assertEqual(page.records[0].content["stream_type"], "own")
        self.assertEqual(page.records[0].native_id, "x:own:100")

    def test_each_stream_paginates_then_advances_with_independent_watermark(self):
        rail = FakeRail()
        rail.add(
            "bookmarks.list",
            {
                "data": [post("100")],
                "meta": {"next_token": "bookmark-page-2"},
            },
            {
                "data": [post("105")],
                "meta": {},
            },
            {
                "data": [],
                "meta": {},
            },
        )
        rail.add(
            "mentions.list",
            {
                "data": [post("200")],
                "meta": {},
            },
            {
                "data": [],
                "meta": {},
            },
        )
        connector = XActivityConnector(
            rail=rail,
            source_id="synthetic:x:multi",
            user_id="999",
            streams=("bookmark", "mention"),
            page_size=10,
        )
        first = connector.pull(None)
        self.assertTrue(first.has_more)
        second = connector.pull(first.next_cursor)
        self.assertTrue(second.has_more)
        third = connector.pull(second.next_cursor)
        self.assertFalse(third.has_more)
        self.assertEqual(
            [
                record.content["stream_type"]
                for page in (first, second, third)
                for record in page.records
            ],
            ["bookmark", "bookmark", "mention"],
        )
        next_cycle = connector.pull(third.next_cursor)
        self.assertNotIn("since_id", rail.calls[3][1]["query"])
        self.assertNotIn("pagination_token", rail.calls[3][1]["query"])
        connector.pull(next_cycle.next_cursor)
        self.assertEqual(rail.calls[4][1]["query"]["since_id"], "200")
        self.assertNotIn("pagination_token", rail.calls[4][1]["query"])
        self.assertFalse(any(record.deleted for record in next_cycle.records))

    def test_reply_metrics_partial_errors_and_provider_details(self):
        rail = FakeRail()
        value = post("300")
        value["conversation_id"] = "250"
        value["referenced_tweets"] = [{"type": "replied_to", "id": "275"}]
        rail.add(
            "home.list",
            {"data": [value], "meta": {}},
            {
                "data": [post("301")],
                "errors": [{"detail": "private provider detail"}],
                "meta": {},
            },
        )
        connector = XActivityConnector(
            rail=rail,
            source_id="synthetic:x:home",
            user_id="999",
            streams=("home",),
        )
        page = connector.pull(None)
        record = page.records[0]
        self.assertEqual(record.content["reply_to_id"], "x:home:275")
        self.assertEqual(record.content["thread_id"], "x:home:250")
        self.assertEqual(record.content["metrics"]["like_count"], 2)
        with self.assertRaisesRegex(
            ConnectorUpstreamError,
            "^connector_upstream_error$",
        ) as raised:
            connector.pull(page.next_cursor)
        self.assertNotIn("private provider detail", str(raised.exception))

    def test_runner_keeps_cursor_behind_ack_and_scrubs_before_spool(self):
        rail = FakeRail()
        rail.add("bookmarks.list", {
            "data": [post("400", text="X marker api_key=x-private-canary")],
            "meta": {},
        })
        connector = XActivityConnector(
            rail=rail,
            source_id="synthetic:x:runner",
            user_id="999",
            streams=("bookmark",),
        )
        brain = FakeBrain()
        brain.fail_after_commit = True
        with tempfile.TemporaryDirectory() as directory:
            spool = Path(directory) / "x.db"
            runner = ConnectorRunner(
                connector=connector,
                brain=brain,
                spool_path=spool,
                privacy=PrivacyPolicy(mode="scrub"),
            )
            with self.assertRaisesRegex(ConnectorRunError, "brain_unavailable"):
                runner.run_once()
            self.assertFalse(runner.doctor()["checkpointed"])
            self.assertEqual(runner.doctor()["pending"], 1)
            self.assertNotIn("x-private-canary", spool.read_bytes().decode(errors="ignore"))
            self.assertEqual(runner.run_once()["replayed"], 1)
            self.assertTrue(runner.doctor()["checkpointed"])
            rendered = json.dumps(tuple(brain.events.values()), sort_keys=True)
            self.assertNotIn("x-private-canary", rendered)
            runner.close()


if __name__ == "__main__":
    unittest.main()
