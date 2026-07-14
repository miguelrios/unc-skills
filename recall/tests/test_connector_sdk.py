from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from connectors.sdk import (
    ConnectorContractError,
    ConnectorPage,
    ConnectorRateLimited,
    ConnectorRecord,
    ConnectorRunError,
    ConnectorRunner,
)
from privacy.policy import PrivacyPolicy


class SyntheticConnector:
    connector_id = "synthetic.pull"
    source_id = "synthetic:connector:test"

    def __init__(self, pages: dict[str | None, ConnectorPage]):
        self.pages = pages
        self.pulls: list[str | None] = []
        self.external_secret = "synthetic-external-secret-must-never-spool"

    def pull(self, cursor: str | None) -> ConnectorPage:
        self.pulls.append(cursor)
        return self.pages[cursor]


class FakeBrain:
    def __init__(self):
        self.calls = 0
        self.events: dict[tuple[str, str, str], dict] = {}
        self.fail_after_commit = False
        self.unauthorized = False

    def ingest(self, events: list[dict]) -> dict:
        self.calls += 1
        if self.unauthorized:
            raise PermissionError("synthetic unauthorized response with payload that must stay private")
        receipts = []
        for event in events:
            key = (event["source_id"], event["native_id"], event["content_sha256"])
            self.events[key] = event
            receipts.append(f"recall://{event['source_id']}/{event['native_id']}?rev=1")
        if self.fail_after_commit:
            self.fail_after_commit = False
            raise OSError("synthetic lost acknowledgement")
        return {
            "status": "committed", "inserted": len(events),
            "duplicate_events": 0, "receipts": receipts, "replay": False,
        }


def record(native_id: str, text: str, *, deleted: bool = False) -> ConnectorRecord:
    return ConnectorRecord.from_mapping({
        "schema_version": 1,
        "native_id": native_id,
        "occurred_at": "2026-07-14T00:00:00Z",
        "content": {"text": text},
        "provenance": {"uri": f"https://example.invalid/items/{native_id}"},
        "deleted": deleted,
    })


class ConnectorContractTest(unittest.TestCase):
    def test_record_and_page_are_closed_versioned_contracts(self) -> None:
        value = record("stable-1", "safe")
        self.assertEqual(value.native_id, "stable-1")
        with self.assertRaisesRegex(ConnectorContractError, "unknown"):
            ConnectorRecord.from_mapping({
                "schema_version": 1, "native_id": "stable-1",
                "occurred_at": "2026-07-14T00:00:00Z", "content": {},
                "provenance": {"uri": "https://example.invalid/1"},
                "deleted": False, "surprise": True,
            })
        with self.assertRaises(ConnectorContractError):
            record("unstable id with spaces", "safe")
        with self.assertRaises(ConnectorContractError):
            record("ambiguous?rev=1", "safe")
        with self.assertRaises(ConnectorContractError):
            record("oversized", "x" * 1_000_001)
        with self.assertRaises(ConnectorContractError):
            ConnectorRecord.from_mapping({
                "schema_version": 1, "native_id": "query-uri",
                "occurred_at": "2026-07-14T00:00:00Z", "content": {},
                "provenance": {"uri": "https://example.invalid/item?access_token=synthetic"},
                "deleted": False,
            })
        with self.assertRaises(ConnectorContractError):
            ConnectorPage(records=(value,) * 501, next_cursor="next", has_more=True)
        with self.assertRaises(ConnectorContractError):
            ConnectorPage(records=(value, value), next_cursor="next", has_more=False)

    def test_page_requires_forward_cursor_semantics(self) -> None:
        with self.assertRaises(ConnectorContractError):
            ConnectorPage(records=(record("one", "safe"),), next_cursor=None, has_more=True)
        with self.assertRaises(ConnectorContractError):
            ConnectorPage(records=(record("one", "safe"),), next_cursor="", has_more=False)


class ConnectorRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.spool = Path(self.temporary.name) / "connector.db"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def runner(self, connector, brain, *, privacy=None, enabled=True) -> ConnectorRunner:
        return ConnectorRunner(
            connector=connector, brain=brain, spool_path=self.spool,
            privacy=privacy or PrivacyPolicy(mode="off"), enabled=enabled,
        )

    def spool_bytes(self) -> bytes:
        return b"".join(
            path.read_bytes() for path in self.spool.parent.glob(self.spool.name + "*")
            if path.is_file()
        )

    def test_cursor_commits_only_after_brain_ack_and_replay_is_idempotent(self) -> None:
        connector = SyntheticConnector({
            None: ConnectorPage(records=(record("one", "safe one"),), next_cursor="page-1", has_more=True),
            "page-1": ConnectorPage(records=(), next_cursor="page-1", has_more=False),
        })
        brain = FakeBrain(); brain.fail_after_commit = True
        runner = self.runner(connector, brain)
        with self.assertRaisesRegex(ConnectorRunError, "brain_unavailable"):
            runner.run_once()
        self.assertFalse(runner.doctor()["checkpointed"])
        self.assertEqual(runner.doctor()["pending"], 1)
        runner.close()

        recovered = self.runner(connector, brain)
        result = recovered.run_once()
        self.assertEqual(result["replayed"], 1)
        self.assertTrue(recovered.doctor()["checkpointed"])
        self.assertEqual(len(brain.events), 1)
        self.assertEqual(connector.pulls, [None])
        recovered.run_once()
        self.assertEqual(connector.pulls, [None, "page-1"])
        recovered.close()

    def test_pending_payload_is_durable_but_acknowledged_bytes_are_purged(self) -> None:
        marker = "synthetic-ack-purge-marker-91"
        connector = SyntheticConnector({
            None: ConnectorPage(
                records=(record("purge-one", marker),), next_cursor="purged", has_more=False,
            ),
        })
        brain = FakeBrain(); brain.fail_after_commit = True
        runner = self.runner(connector, brain)
        with self.assertRaisesRegex(ConnectorRunError, "brain_unavailable"):
            runner.run_once()
        self.assertIn(marker.encode(), self.spool_bytes())
        runner.close()

        recovered = self.runner(connector, brain)
        self.assertEqual(recovered.run_once()["replayed"], 1)
        self.assertNotIn(marker.encode(), self.spool_bytes())
        self.assertTrue(recovered.doctor()["checkpointed"])
        recovered.close()

    def test_drop_and_scrub_happen_before_spool_and_network(self) -> None:
        canary = "connector-private-canary-77"
        connector = SyntheticConnector({None: ConnectorPage(
            records=(record("secret", f"api_key={canary}"), record("safe", "keep this context")),
            next_cursor="done", has_more=False,
        )})
        brain = FakeBrain()
        runner = self.runner(connector, brain, privacy=PrivacyPolicy(mode="drop"))
        result = runner.run_once()
        self.assertEqual(result["privacy"]["actions"], {"drop": 1, "keep": 1})
        self.assertEqual(len(brain.events), 1)
        self.assertNotIn(canary.encode(), self.spool.read_bytes())
        self.assertNotIn(connector.external_secret.encode(), self.spool.read_bytes())
        self.assertNotIn(canary, json.dumps(runner.doctor()))
        runner.close()

        scrub_spool = Path(self.temporary.name) / "scrub.db"
        scrub_connector = SyntheticConnector({None: ConnectorPage(
            records=(record("secret", f"keep before api_key={canary} keep after"),),
            next_cursor="done", has_more=False,
        )})
        scrub_brain = FakeBrain()
        scrub = ConnectorRunner(
            connector=scrub_connector, brain=scrub_brain, spool_path=scrub_spool,
            privacy=PrivacyPolicy(mode="scrub"),
        )
        scrub.run_once()
        payload = next(iter(scrub_brain.events.values()))
        self.assertNotIn(canary, json.dumps(payload))
        self.assertIn("keep before", json.dumps(payload))
        self.assertNotIn(canary.encode(), scrub_spool.read_bytes())
        scrub.close()

    def test_all_dropped_page_advances_without_brain_request(self) -> None:
        connector = SyntheticConnector({None: ConnectorPage(
            records=(record("secret", "password=synthetic-secret"),),
            next_cursor="after-drop", has_more=False,
        )})
        brain = FakeBrain()
        runner = self.runner(connector, brain, privacy=PrivacyPolicy(mode="drop"))
        result = runner.run_once()
        self.assertEqual(result["status"], "committed")
        self.assertEqual(brain.calls, 0)
        self.assertTrue(runner.doctor()["checkpointed"])
        runner.close()

    def test_tombstone_bypasses_failed_contextual_judge(self) -> None:
        def unavailable(_text: str):
            raise OSError("judge unavailable")

        connector = SyntheticConnector({None: ConnectorPage(
            records=(record("deleted-one", "never inspect this", deleted=True),),
            next_cursor="after-delete", has_more=False,
        )})
        brain = FakeBrain()
        runner = self.runner(connector, brain, privacy=PrivacyPolicy(mode="scrub", judge=unavailable))
        runner.run_once()
        event = next(iter(brain.events.values()))
        self.assertEqual(event["kind"], "tombstone")
        self.assertTrue(runner.doctor()["checkpointed"])
        runner.close()

    def test_disabled_and_unauthorized_fail_without_cursor_movement(self) -> None:
        connector = SyntheticConnector({None: ConnectorPage(
            records=(record("one", "safe"),), next_cursor="next", has_more=False,
        )})
        brain = FakeBrain()
        disabled = self.runner(connector, brain, enabled=False)
        self.assertEqual(disabled.run_once()["status"], "disabled")
        self.assertEqual(connector.pulls, [])
        disabled.close()

        self.spool.unlink()
        brain.unauthorized = True
        enabled = self.runner(connector, brain)
        with self.assertRaisesRegex(ConnectorRunError, "brain_unauthorized"):
            enabled.run_once()
        doctor = enabled.doctor()
        self.assertFalse(doctor["checkpointed"])
        self.assertEqual(doctor["pending"], 1)
        self.assertEqual(doctor["last_error_code"], "brain_unauthorized")
        self.assertNotIn("payload", json.dumps(doctor))
        enabled.close()

    def test_rate_limit_is_bounded_content_free_and_does_not_move_cursor(self) -> None:
        class Limited(SyntheticConnector):
            def pull(self, cursor):
                raise ConnectorRateLimited(retry_after_seconds=99999)

        connector = Limited({})
        runner = self.runner(connector, FakeBrain())
        result = runner.run_once()
        self.assertEqual(result["status"], "backoff")
        self.assertEqual(result["error_code"], "connector_rate_limited")
        self.assertLessEqual(result["retry_after_seconds"], 3600)
        self.assertFalse(runner.doctor()["checkpointed"])
        self.assertEqual(runner.doctor()["pending"], 0)
        runner.close()

    def test_provenance_is_private_and_cursor_is_never_exposed_by_doctor(self) -> None:
        canary = "provenance-secret-canary-88"
        value = ConnectorRecord.from_mapping({
            "schema_version": 1, "native_id": "with-provenance",
            "occurred_at": "2026-07-14T00:00:00Z",
            "content": {"text": "safe content"},
            "provenance": {
                "uri": "https://example.invalid/items/with-provenance",
                "note": f"api_key={canary}",
            },
            "deleted": False,
        })
        cursor = "opaque-private-cursor-must-not-be-in-doctor"
        connector = SyntheticConnector({None: ConnectorPage(
            records=(value,), next_cursor=cursor, has_more=False,
        )})
        brain = FakeBrain()
        runner = self.runner(connector, brain, privacy=PrivacyPolicy(mode="scrub"))
        runner.run_once()
        rendered_event = json.dumps(next(iter(brain.events.values())))
        rendered_doctor = json.dumps(runner.doctor())
        self.assertNotIn(canary, rendered_event)
        self.assertNotIn(canary.encode(), self.spool.read_bytes())
        self.assertNotIn(cursor, rendered_doctor)
        self.assertTrue(runner.doctor()["checkpointed"])
        runner.close()


if __name__ == "__main__":
    unittest.main()
