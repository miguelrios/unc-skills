from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from client import cli as client_cli
from connectors.sdk import (
    ConnectorContractError,
    ConnectorPage,
    ConnectorRateLimited,
    ConnectorRecord,
    ConnectorRunError,
    ConnectorRunner,
    seed_acknowledged_records,
)
from privacy.policy import PrivacyPolicy
from server.recall_server.archive import FilesystemArchiveStore


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
        self.duplicate_events = 0
        self.fail_after_commit = False
        self.unauthorized = False

    def ingest(self, events: list[dict]) -> dict:
        self.calls += 1
        if self.unauthorized:
            raise PermissionError("synthetic unauthorized response with payload that must stay private")
        receipts = []
        inserted = duplicates = 0
        for event in events:
            key = (event["source_id"], event["native_id"], event["content_sha256"])
            if key in self.events:
                duplicates += 1
            else:
                self.events[key] = event
                inserted += 1
            receipts.append(f"recall://{event['source_id']}/{event['native_id']}?rev=1")
        self.duplicate_events += duplicates
        if self.fail_after_commit:
            self.fail_after_commit = False
            raise OSError("synthetic lost acknowledgement")
        return {
            "status": "committed", "inserted": inserted,
            "duplicate_events": duplicates, "receipts": receipts, "replay": False,
        }


class FakeArchive:
    def __init__(self):
        self.calls = 0
        self.objects: dict[str, bytes] = {}
        self.fail_before_commit = False
        self.fail_after_commit = False
        self.invalid_reference = False
        self.reject_forgotten = False

    def put_raw(
        self,
        *,
        tenant_id: str,
        source_id: str,
        native_id: str,
        payload: bytes,
        media_type: str,
        created_at: str,
    ) -> dict:
        self.calls += 1
        if self.reject_forgotten:
            raise ConnectorRunError("archive_identity_forgotten")
        if self.fail_before_commit:
            raise OSError("synthetic archive unavailable with private detail")
        digest = hashlib.sha256(payload).hexdigest()
        self.objects.setdefault(digest, payload)
        if self.fail_after_commit:
            self.fail_after_commit = False
            raise OSError("synthetic archive acknowledgement lost")
        reference = {
            "contract": "recall.artifact-ref.v1",
            "schema_version": 1,
            "tenant_id": tenant_id,
            "source_id": source_id,
            "artifact_id": "art_" + digest[:16],
            "storage_backend": "s3",
            "object_key": f"objects/{digest[:2]}/{digest}",
            "content_sha256": digest,
            "size_bytes": len(payload),
            "media_type": media_type,
            "encryption": "sse-s3",
            "version_id": "version-" + digest[:16],
            "created_at": created_at,
        }
        if self.invalid_reference:
            reference["tenant_id"] = "tenant:wrong"
        return reference


def record(native_id: str, text: str, *, deleted: bool = False,
           native_parent_id: str | None = None) -> ConnectorRecord:
    return ConnectorRecord.from_mapping({
        "schema_version": 1,
        "native_id": native_id,
        "native_parent_id": native_parent_id,
        "occurred_at": "2026-07-14T00:00:00Z",
        "content": {"text": text},
        "provenance": {"uri": f"https://example.invalid/items/{native_id}"},
        "deleted": deleted,
    })


class ConnectorContractTest(unittest.TestCase):
    def test_record_and_page_are_closed_versioned_contracts(self) -> None:
        value = record("stable-1", "safe")
        self.assertEqual(value.native_id, "stable-1")
        self.assertIsNone(value.native_parent_id)
        with self.assertRaisesRegex(ConnectorContractError, "unknown"):
            ConnectorRecord.from_mapping({
                "schema_version": 1, "native_id": "stable-1",
                "native_parent_id": None,
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
                "native_parent_id": None,
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

    def test_parent_session_identity_survives_privacy_spool_and_envelope(self) -> None:
        connector = SyntheticConnector({
            None: ConnectorPage(
                records=(record("session-a/turn-1", "safe", native_parent_id="session-a"),),
                next_cursor="done", has_more=False,
            ),
        })
        brain = FakeBrain()
        runner = self.runner(connector, brain)
        self.assertEqual(runner.run_once()["acked"], 1)
        event = next(iter(brain.events.values()))
        self.assertEqual(event["native_parent_id"], "session-a")
        runner.close()

    def test_cursor_commits_only_after_brain_ack_and_replay_is_idempotent(self) -> None:
        connector = SyntheticConnector({
            None: ConnectorPage(records=(record("one", "safe one"),), next_cursor="page-1", has_more=True),
            "page-1": ConnectorPage(records=(), next_cursor="page-1", has_more=False),
        })
        brain = FakeBrain(); brain.fail_after_commit = True
        runner = self.runner(connector, brain)
        with self.assertRaisesRegex(ConnectorRunError, "brain_unavailable"):
            runner.run_once()
        self.assertEqual(
            runner.db.execute("SELECT count(*) FROM acknowledged_records").fetchone()[0], 0,
        )
        self.assertFalse(runner.doctor()["checkpointed"])
        self.assertEqual(runner.doctor()["pending"], 1)
        runner.close()

        recovered = self.runner(connector, brain)
        result = recovered.run_once()
        self.assertEqual(result["replayed"], 1)
        self.assertTrue(recovered.doctor()["checkpointed"])
        self.assertEqual(len(brain.events), 1)
        self.assertEqual(
            recovered.db.execute("SELECT count(*) FROM acknowledged_records").fetchone()[0], 1,
        )
        self.assertEqual(connector.pulls, [None])
        recovered.run_once()
        self.assertEqual(connector.pulls, [None, "page-1"])
        recovered.close()

    def test_raw_archive_precedes_private_spool_and_brain(self) -> None:
        canary = "archive-raw-canary-77"
        connector = SyntheticConnector({
            None: ConnectorPage(
                records=(record("archive-one", f"api_key={canary} safe context"),),
                next_cursor="done",
                has_more=False,
            ),
        })
        archive = FakeArchive()
        brain = FakeBrain()
        runner = ConnectorRunner(
            connector=connector,
            brain=brain,
            archive=archive,
            tenant_id="tenant:synthetic",
            principal_id="principal:owner",
            spool_path=self.spool,
            privacy=PrivacyPolicy(mode="scrub"),
        )
        result = runner.run_once()
        self.assertEqual(result["archived"], 1)
        self.assertEqual(len(archive.objects), 1)
        self.assertIn(canary.encode(), next(iter(archive.objects.values())))
        event = next(iter(brain.events.values()))
        self.assertNotIn(canary, json.dumps(event))
        self.assertNotIn(canary.encode(), self.spool_bytes())
        artifact = event["provenance"]["artifact_ref"]
        self.assertEqual(artifact["tenant_id"], "tenant:synthetic")
        self.assertEqual(artifact["source_id"], connector.source_id)
        runner.close()

    def test_archive_failure_never_stages_calls_brain_or_moves_cursor(self) -> None:
        connector = SyntheticConnector({
            None: ConnectorPage(
                records=(record("archive-failure", "safe"),),
                next_cursor="done",
                has_more=False,
            ),
        })
        archive = FakeArchive()
        archive.fail_before_commit = True
        brain = FakeBrain()
        runner = ConnectorRunner(
            connector=connector,
            brain=brain,
            archive=archive,
            tenant_id="tenant:synthetic",
            principal_id="principal:owner",
            spool_path=self.spool,
        )
        with self.assertRaisesRegex(ConnectorRunError, "archive_unavailable"):
            runner.run_once()
        self.assertEqual(brain.calls, 0)
        self.assertEqual(runner.doctor()["pending"], 0)
        self.assertFalse(runner.doctor()["checkpointed"])
        runner.close()

    def test_lost_archive_ack_replays_one_object_then_commits(self) -> None:
        connector = SyntheticConnector({
            None: ConnectorPage(
                records=(record("archive-replay", "safe"),),
                next_cursor="done",
                has_more=False,
            ),
        })
        archive = FakeArchive()
        archive.fail_after_commit = True
        brain = FakeBrain()
        runner = ConnectorRunner(
            connector=connector,
            brain=brain,
            archive=archive,
            tenant_id="tenant:synthetic",
            principal_id="principal:owner",
            spool_path=self.spool,
        )
        with self.assertRaisesRegex(ConnectorRunError, "archive_unavailable"):
            runner.run_once()
        self.assertEqual(len(archive.objects), 1)
        self.assertEqual(runner.doctor()["pending"], 0)
        self.assertEqual(runner.run_once()["acked"], 1)
        self.assertEqual(len(archive.objects), 1)
        self.assertEqual(archive.calls, 2)
        self.assertEqual(brain.calls, 1)
        self.assertTrue(runner.doctor()["checkpointed"])
        runner.close()

    def test_archive_configuration_and_reference_fail_closed(self) -> None:
        connector = SyntheticConnector({
            None: ConnectorPage(
                records=(record("archive-invalid", "safe"),),
                next_cursor="done",
                has_more=False,
            ),
        })
        with self.assertRaisesRegex(ConnectorContractError, "configured together"):
            ConnectorRunner(
                connector=connector,
                brain=FakeBrain(),
                archive=FakeArchive(),
                spool_path=self.spool,
            )
        with self.assertRaisesRegex(ConnectorContractError, "configured together"):
            ConnectorRunner(
                connector=connector,
                brain=FakeBrain(),
                tenant_id="tenant:synthetic",
                spool_path=self.spool,
            )

        archive = FakeArchive()
        archive.invalid_reference = True
        runner = ConnectorRunner(
            connector=connector,
            brain=FakeBrain(),
            archive=archive,
            tenant_id="tenant:synthetic",
            principal_id="principal:owner",
            spool_path=self.spool,
        )
        with self.assertRaisesRegex(ConnectorRunError, "archive_invalid_reference"):
            runner.run_once()
        self.assertEqual(runner.doctor()["pending"], 0)
        self.assertEqual(runner.doctor()["last_error_code"], "archive_invalid_reference")
        runner.close()

    def test_filesystem_archive_gateway_runs_end_to_end(self) -> None:
        canary = "real-filesystem-raw-canary-32"
        connector = SyntheticConnector({
            None: ConnectorPage(
                records=(record("archive-real", f"api_key={canary} context"),),
                next_cursor="done",
                has_more=False,
            ),
        })
        archive = FilesystemArchiveStore(
            Path(self.temporary.name) / "raw-archive",
            namespace_key=b"n" * 32,
        )
        brain = FakeBrain()
        runner = ConnectorRunner(
            connector=connector,
            brain=brain,
            archive=archive,
            tenant_id="tenant:synthetic",
            principal_id="principal:owner",
            spool_path=self.spool,
            privacy=PrivacyPolicy(mode="scrub"),
        )
        self.assertEqual(runner.run_once()["archived"], 1)
        event = next(iter(brain.events.values()))
        reference = event["provenance"]["artifact_ref"]
        stored = archive.root / reference["object_key"] / "data"
        self.assertIn(canary.encode(), stored.read_bytes())
        self.assertNotIn(canary, json.dumps(event))
        self.assertNotIn(canary.encode(), self.spool_bytes())
        runner.close()

    def test_forgotten_archive_identity_is_suppressed_and_cursor_advances(self) -> None:
        connector = SyntheticConnector({
            None: ConnectorPage(
                records=(record("forgotten-one", "must not resurrect"),),
                next_cursor="done",
                has_more=False,
            ),
        })
        archive = FakeArchive()
        archive.reject_forgotten = True
        brain = FakeBrain()
        runner = ConnectorRunner(
            connector=connector,
            brain=brain,
            archive=archive,
            tenant_id="tenant:synthetic",
            principal_id="principal:owner",
            spool_path=self.spool,
        )
        result = runner.run_once()
        self.assertEqual(result["forgotten"], 1)
        self.assertEqual(result["staged"], 0)
        self.assertEqual(brain.calls, 0)
        self.assertTrue(runner.doctor()["checkpointed"])
        runner.close()

    def test_reordered_acknowledged_record_is_not_resubmitted(self) -> None:
        repeated = record("reordered-one", "same acknowledged content")
        connector = SyntheticConnector({
            None: ConnectorPage(records=(repeated,), next_cursor="first", has_more=True),
            "first": ConnectorPage(records=(repeated,), next_cursor="second", has_more=False),
        })
        brain = FakeBrain()
        runner = self.runner(connector, brain)
        self.assertEqual(runner.run_once()["acked"], 1)
        second = runner.run_once()
        self.assertEqual(second["acked"], 0)
        self.assertEqual(brain.calls, 1)
        self.assertEqual(brain.duplicate_events, 0)
        self.assertEqual(second["deduplicated"], 1)
        runner.close()

    def test_rejected_or_malformed_ack_never_advances_or_purges(self) -> None:
        def invalid_brain(overrides):
            class InvalidBrain:
                def ingest(self, events):
                    event = events[0]
                    acknowledgement = {
                        "status": "committed",
                        "inserted": 1,
                        "duplicate_events": 0,
                        "receipts": [
                            f"recall://{event['source_id']}/{event['native_id']}?rev=1"
                        ],
                        "replay": False,
                    }
                    acknowledgement.update(overrides)
                    return acknowledgement
            return InvalidBrain()

        cases = (
            {"status": "rejected"},
            {"receipts": ["not-a-receipt"]},
            {"inserted": 0},
            {"replay": "false"},
        )
        for index, overrides in enumerate(cases):
            with self.subTest(overrides=overrides):
                connector = SyntheticConnector({None: ConnectorPage(
                    records=(record(f"invalid-{index}", "must remain pending"),),
                    next_cursor="next", has_more=False,
                )})
                runner = ConnectorRunner(
                    connector=connector,
                    brain=invalid_brain(overrides),
                    spool_path=Path(self.temporary.name) / f"invalid-{index}.db",
                )
                with self.assertRaisesRegex(ConnectorRunError, "brain_invalid_acknowledgement"):
                    runner.run_once()
                self.assertFalse(runner.doctor()["checkpointed"])
                self.assertEqual(runner.doctor()["pending"], 1)
                self.assertEqual(
                    runner.db.execute("SELECT count(*) FROM acknowledged_records").fetchone()[0],
                    0,
                )
                runner.close()

    def test_changed_content_and_tombstone_ingest_but_old_versions_stay_suppressed(self) -> None:
        connector = SyntheticConnector({
            None: ConnectorPage(
                records=(record("versioned-one", "first version"),),
                next_cursor="first", has_more=True,
            ),
            "first": ConnectorPage(
                records=(record("versioned-one", "second version"),),
                next_cursor="second", has_more=True,
            ),
            "second": ConnectorPage(
                records=(record("versioned-one", "deleted", deleted=True),),
                next_cursor="deleted", has_more=True,
            ),
            "deleted": ConnectorPage(
                records=(record("versioned-one", "first version"),),
                next_cursor="done", has_more=False,
            ),
        })
        brain = FakeBrain()
        runner = self.runner(connector, brain)
        self.assertEqual(runner.run_once()["acked"], 1)
        self.assertEqual(runner.run_once()["acked"], 1)
        self.assertEqual(runner.run_once()["acked"], 1)
        final = runner.run_once()
        self.assertEqual(final["acked"], 0)
        self.assertEqual(final["deduplicated"], 1)
        self.assertEqual(brain.calls, 3)
        self.assertEqual(brain.duplicate_events, 0)
        self.assertEqual(
            runner.db.execute("SELECT count(*) FROM acknowledged_records").fetchone()[0], 3,
        )
        runner.close()

    def test_acknowledged_ledger_contains_hashes_only(self) -> None:
        native_id = "ledger-native-marker-73"
        content_marker = "ledger-content-marker-84"
        connector = SyntheticConnector({None: ConnectorPage(
            records=(record(native_id, content_marker),), next_cursor="done", has_more=False,
        )})
        runner = self.runner(connector, FakeBrain())
        runner.run_once()
        row = runner.db.execute(
            "SELECT native_sha256,content_sha256 FROM acknowledged_records"
        ).fetchone()
        self.assertEqual(
            row["native_sha256"],
            hashlib.sha256(f"{runner.source_id}\0{native_id}".encode()).hexdigest(),
        )
        self.assertRegex(row["native_sha256"], r"\A[0-9a-f]{64}\Z")
        self.assertRegex(row["content_sha256"], r"\A[0-9a-f]{64}\Z")
        self.assertNotIn(native_id.encode(), self.spool_bytes())
        self.assertNotIn(content_marker.encode(), self.spool_bytes())
        runner.close()

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


class AcknowledgedSeedTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        os.chmod(self.root, 0o700)
        self.spool = self.root / "connector.db"
        connector = SyntheticConnector({})
        runner = ConnectorRunner(connector=connector, brain=FakeBrain(), spool_path=self.spool)
        runner.close()
        self.seed = self.root / "seed.json"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_seed(self, value: object, *, path: Path | None = None, mode: int = 0o600) -> Path:
        target = path or self.seed
        target.write_text(json.dumps(value))
        os.chmod(target, mode)
        return target

    @staticmethod
    def pair(native: str = "a", content: str = "b") -> dict[str, str]:
        return {
            "native_sha256": hashlib.sha256(native.encode()).hexdigest(),
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        }

    def test_seed_is_idempotent_content_free_and_suppresses_first_upgraded_sweep(self) -> None:
        native_id = "already-central"
        repeated = record(native_id, "already central content")
        connector = SyntheticConnector({None: ConnectorPage(
            records=(repeated,), next_cursor="done", has_more=False,
        )})
        event_runner = ConnectorRunner(
            connector=connector, brain=FakeBrain(), spool_path=self.root / "event.db",
        )
        event = event_runner._event(repeated, repeated.content, repeated.provenance)
        event_runner.close()
        pair = {
            "native_sha256": hashlib.sha256(
                f"{connector.source_id}\0{native_id}".encode()
            ).hexdigest(),
            "content_sha256": event["content_sha256"],
        }
        self.write_seed({"schema_version": 1, "records": [pair]})
        result = seed_acknowledged_records(spool_path=self.spool, seed_path=self.seed)
        self.assertEqual(result, {
            "schema_version": 1, "seeded": 1, "already_acknowledged": 0,
        })
        again = seed_acknowledged_records(spool_path=self.spool, seed_path=self.seed)
        self.assertEqual(again, {
            "schema_version": 1, "seeded": 0, "already_acknowledged": 1,
        })
        brain = FakeBrain()
        upgraded = ConnectorRunner(connector=connector, brain=brain, spool_path=self.spool)
        synced = upgraded.run_once()
        self.assertEqual(synced["deduplicated"], 1)
        self.assertEqual(synced["acked"], 0)
        self.assertEqual(brain.calls, 0)
        upgraded.close()

    def test_seed_rejects_non_hash_data_duplicates_uppercase_and_open_schema(self) -> None:
        pair = self.pair()
        invalid = [
            {"schema_version": 1, "records": [{**pair, "native_sha256": "raw-id"}]},
            {"schema_version": 1, "records": [{**pair, "native_sha256": pair["native_sha256"].upper()}]},
            {"schema_version": 1, "records": [pair, pair]},
            {"schema_version": 1, "records": [{**pair, "extra": "not-closed"}]},
            {"schema_version": 1, "records": [pair], "extra": True},
        ]
        for index, value in enumerate(invalid):
            with self.subTest(index=index):
                self.write_seed(value)
                with self.assertRaises(ConnectorContractError):
                    seed_acknowledged_records(spool_path=self.spool, seed_path=self.seed)
        with sqlite3.connect(self.spool) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM acknowledged_records").fetchone()[0], 0)

    def test_seed_rejects_relative_paths_bad_modes_symlinks_and_unsafe_parent(self) -> None:
        manifest = {"schema_version": 1, "records": [self.pair()]}
        self.write_seed(manifest)
        with self.assertRaisesRegex(ConnectorContractError, "absolute"):
            seed_acknowledged_records(spool_path=self.spool, seed_path=Path("seed.json"))
        self.write_seed(manifest, mode=0o644)
        with self.assertRaisesRegex(ConnectorContractError, "0600"):
            seed_acknowledged_records(spool_path=self.spool, seed_path=self.seed)
        self.write_seed(manifest)
        link = self.root / "seed-link.json"
        link.symlink_to(self.seed)
        with self.assertRaisesRegex(ConnectorContractError, "non-symlink"):
            seed_acknowledged_records(spool_path=self.spool, seed_path=link)
        os.chmod(self.root, 0o755)
        with self.assertRaisesRegex(ConnectorContractError, "0700"):
            seed_acknowledged_records(spool_path=self.spool, seed_path=self.seed)

    def test_seed_cli_emits_counts_without_paths_or_hashes(self) -> None:
        pair = self.pair("cli-native", "cli-content")
        self.write_seed({"schema_version": 1, "records": [pair]})
        output = io.StringIO()
        with mock.patch("sys.argv", [
            "recall-brain", "connector-spool-seed-acknowledged",
            "--spool", str(self.spool), "--input", str(self.seed),
        ]), mock.patch("sys.stdout", output):
            client_cli.main()
        rendered = output.getvalue()
        self.assertEqual(json.loads(rendered)["seeded"], 1)
        self.assertNotIn(str(self.spool), rendered)
        self.assertNotIn(str(self.seed), rendered)
        self.assertNotIn(pair["native_sha256"], rendered)
        self.assertNotIn(pair["content_sha256"], rendered)

    def test_seed_rejects_pending_work_without_modifying_ledger(self) -> None:
        pair = self.pair("pending-native", "pending-content")
        self.write_seed({"schema_version": 1, "records": [pair]})
        with sqlite3.connect(self.spool) as db:
            db.execute(
                "INSERT INTO pages(cursor_before,cursor_after,has_more,created_at) VALUES (?,?,?,?)",
                ("null", '"next"', 0, 1.0),
            )
        with self.assertRaisesRegex(ConnectorContractError, "no pending"):
            seed_acknowledged_records(spool_path=self.spool, seed_path=self.seed)
        with sqlite3.connect(self.spool) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM acknowledged_records").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
