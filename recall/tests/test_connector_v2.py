from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from connectors.registry import definition, preview
from connectors.sdk import (
    ConnectorContractError, ConnectorPage, ConnectorRecord, ConnectorRecordV2,
    ConnectorRunner,
)
from tests.test_connector_sdk import FakeBrain, SyntheticConnector


class ConnectorRecordV2Test(unittest.TestCase):
    def record(self, kind: str, **content):
        return ConnectorRecordV2.from_mapping({
            "schema_version": 2,
            "native_id": "synthetic-record-1",
            "native_parent_id": "synthetic-parent-1",
            "occurred_at": "2026-07-16T00:00:00Z",
            "content": {"kind": kind, **content},
            "provenance": {"uri": "connector://synthetic-v2"},
            "deleted": False,
        })

    def test_v1_remains_strict_and_v2_kinds_are_closed(self):
        legacy = ConnectorRecord.from_mapping({
            "schema_version": 1,
            "native_id": "synthetic-legacy-1",
            "occurred_at": "2026-07-16T00:00:00Z",
            "content": {"text": "synthetic"},
            "provenance": {"uri": "connector://synthetic-v1"},
            "deleted": False,
        })
        self.assertEqual(legacy.schema_version, 1)
        with self.assertRaisesRegex(ConnectorContractError, "unsupported connector record"):
            ConnectorRecord.from_mapping({**legacy.to_mapping(), "schema_version": 2})
        with self.assertRaisesRegex(ConnectorContractError, "record kind"):
            self.record("runtime_plugin.v1", text="synthetic")

    def test_each_typed_kind_has_a_minimum_closed_shape(self):
        rows = (
            ("communication_message.v1", {
                "conversation_id": "thread-1", "message_id": "message-1",
                "direction": "inbound", "text": "synthetic",
            }),
            ("calendar_event.v1", {
                "calendar_id": "calendar-1", "event_id": "event-1",
                "start": "2026-07-16T00:00:00Z", "end": "2026-07-16T01:00:00Z",
                "title": "Synthetic event",
            }),
            ("contact_identity.v1", {
                "identity_id": "person-1", "identifier_type": "email",
                "display_name": "Synthetic Person",
            }),
            ("social_post.v1", {
                "post_id": "post-1", "author_id": "person-1", "text": "synthetic",
                "stream_type": "own",
            }),
            ("document.v1", {
                "document_id": "document-1", "name": "Synthetic document",
                "mime_type": "text/plain",
            }),
        )
        for kind, content in rows:
            with self.subTest(kind=kind):
                record = self.record(kind, **content)
                self.assertEqual(record.record_kind, kind)
                self.assertEqual(record.to_mapping()["content"]["kind"], kind)
                missing = dict(content)
                missing.pop(next(iter(content)))
                with self.assertRaises(ConnectorContractError):
                    self.record(kind, **missing)
                with self.assertRaisesRegex(ConnectorContractError, "unknown content fields"):
                    self.record(kind, **content, executable="synthetic")

    def test_authoritative_tombstone_needs_only_the_closed_record_kind(self):
        value = ConnectorRecordV2.from_mapping({
            "schema_version": 2, "native_id": "deleted-message-1",
            "occurred_at": "2026-07-16T00:00:00Z",
            "content": {"kind": "communication_message.v1"},
            "provenance": {"uri": "connector://synthetic-v2"}, "deleted": True,
        })
        self.assertTrue(value.deleted)
        with self.assertRaisesRegex(ConnectorContractError, "unknown content fields"):
            ConnectorRecordV2.from_mapping({
                **value.to_mapping(),
                "content": {"kind": "communication_message.v1", "guess": True},
            })

    def test_typed_values_and_closed_enums_fail_before_the_spool(self):
        with self.assertRaisesRegex(ConnectorContractError, "invalid field values"):
            self.record(
                "communication_message.v1", conversation_id="thread-1",
                message_id="message-1", direction="sideways", text="synthetic",
            )
        with self.assertRaisesRegex(ConnectorContractError, "invalid field values"):
            self.record(
                "calendar_event.v1", calendar_id="calendar-1", event_id="event-1",
                start="2026-07-16T00:00:00Z", end="2026-07-16T01:00:00Z",
                title="Synthetic event", attendee_ids="not-a-list",
            )


class ConnectorManifestV2Test(unittest.TestCase):
    def test_google_definitions_are_static_v3_and_content_free(self):
        expected = {
            "google.gmail": ("communications", ("communication_message.v1",)),
            "google.calendar": ("schedule", ("calendar_event.v1",)),
            "google.contacts": ("contacts", ("contact_identity.v1",)),
            "google.drive": ("documents", ("document.v1",)),
        }
        for connector_id, (family, kinds) in expected.items():
            item = definition(connector_id)
            self.assertEqual(item.schema_version, 3)
            self.assertEqual(item.source_family, family)
            self.assertEqual(item.record_kinds, kinds)
            self.assertEqual(item.execution_placement, "remote_worker")
            self.assertEqual(item.authority_slots, ("brain", "source"))
            self.assertEqual(item.default_privacy_mode, "scrub")
        value = preview()
        self.assertEqual(value["schema_version"], 2)
        self.assertEqual(value["credential_reads"], 0)
        self.assertEqual(value["source_reads"], 0)
        self.assertEqual(value["network_requests"], 0)
        self.assertFalse(set(str(value).lower().split()) & {"token", "credential"})


class ConnectorRunnerV2Test(unittest.TestCase):
    def test_runner_preserves_typed_contract_through_ack_gated_envelope(self):
        typed = ConnectorRecordV2.from_mapping({
            "schema_version": 2, "native_id": "message-1", "native_parent_id": "thread-1",
            "occurred_at": "2026-07-16T00:00:00Z",
            "content": {
                "kind": "communication_message.v1", "conversation_id": "thread-1",
                "message_id": "message-1", "direction": "inbound", "text": "synthetic",
            },
            "provenance": {"uri": "connector://synthetic-v2"}, "deleted": False,
        })
        connector = SyntheticConnector({None: ConnectorPage(
            records=(typed,), next_cursor="complete", has_more=False,
        )})
        brain = FakeBrain()
        with tempfile.TemporaryDirectory() as directory:
            runner = ConnectorRunner(
                connector=connector, brain=brain,
                spool_path=Path(directory) / "state.db",
            )
            self.assertEqual(runner.run_once()["acked"], 1)
            event = next(iter(brain.events.values()))
            self.assertEqual(event["kind"], "connector_record")
            self.assertEqual(event["content"]["kind"], "communication_message.v1")
            self.assertEqual(event["provenance"]["connector_schema_version"], 2)
            self.assertEqual(event["native_parent_id"], "thread-1")
            runner.close()


if __name__ == "__main__":
    unittest.main()
