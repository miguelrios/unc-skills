from __future__ import annotations

import json
import unittest
from pathlib import Path

from connectors import ConnectorDefinitionV3 as PackagedConnectorDefinitionV3
from connectors.kit import (
    CONNECTOR_KIT_API_VERSION,
    ConnectorAuth,
    ConnectorDefinitionV3,
    ConnectorPlacement,
    ConnectorPolicy,
    ConnectorRecordV2,
    ConnectorSync,
    decode_page_wire,
    encode_page_wire,
)
from connectors.registry import ConnectorRegistryError
from connectors.sdk import ConnectorContractError, ConnectorPage


def communication_record(native_id: str = "message-1") -> ConnectorRecordV2:
    return ConnectorRecordV2.from_mapping({
        "schema_version": 2,
        "native_id": native_id,
        "native_parent_id": "conversation-1",
        "occurred_at": "2026-07-18T00:00:00Z",
        "content": {
            "kind": "communication_message.v1",
            "conversation_id": "conversation-1",
            "message_id": native_id,
            "direction": "inbound",
            "text": "synthetic",
        },
        "provenance": {"uri": "connector://synthetic-v3"},
        "deleted": False,
    })


def manifest_mapping() -> dict:
    return {
        "schema_version": 3,
        "connector_id": "synthetic.remote",
        "command": "remote-sync",
        "mode": "pull",
        "authority_slots": ["brain", "source"],
        "source_family": "communications",
        "record_kinds": ["communication_message.v1"],
        "placement": {
            "execution": "remote_worker",
            "acquisition": ["poll"],
        },
        "auth": {
            "kind": "oauth2",
            "minimum_scopes": ["https://example.invalid/read"],
        },
        "sync": {
            "backfill_modes": ["full", "incremental"],
            "checkpoint": "ack_cursor",
            "edit_semantics": "content_revision",
            "deletion_semantics": "explicit_upstream",
            "reconciliation": True,
        },
        "policy": {
            "visibility_modes": ["private"],
            "privacy_modes": ["drop", "scrub"],
            "default_privacy_mode": "scrub",
            "retention_modes": ["source_controlled"],
            "attachment_capability": False,
        },
        "selection_fields": ["account", "labels"],
    }


class ConnectorDefinitionV3Test(unittest.TestCase):
    def test_nested_manifest_is_closed_canonical_and_content_free(self):
        item = ConnectorDefinitionV3.from_mapping(manifest_mapping())
        self.assertEqual(item.schema_version, 3)
        self.assertEqual(item.execution_placement, "remote_worker")
        self.assertEqual(item.acquisition_modes, ("poll",))
        self.assertEqual(item.minimum_external_scopes, ("https://example.invalid/read",))
        self.assertEqual(item.visibility_modes, ("private",))
        self.assertEqual(item.privacy_modes, ("drop", "scrub"))
        self.assertEqual(item.checkpoint, "ack_cursor")
        self.assertEqual(item.to_public(), manifest_mapping())
        self.assertEqual(CONNECTOR_KIT_API_VERSION, "recall.connector-kit.v1")
        self.assertIs(PackagedConnectorDefinitionV3, ConnectorDefinitionV3)

    def test_facets_are_public_composable_values(self):
        item = ConnectorDefinitionV3(
            schema_version=3,
            connector_id="synthetic.local",
            command="bridge-sync",
            mode="pull",
            authority_slots=("brain", "source"),
            source_family="communications",
            record_kinds=("communication_message.v1",),
            placement=ConnectorPlacement(
                execution="source_local", acquisition=("snapshot",),
            ),
            auth=ConnectorAuth(
                kind="os_permission", minimum_scopes=("macos.full_disk_access",),
            ),
            sync=ConnectorSync(
                backfill_modes=("full", "incremental"),
                checkpoint="ack_cursor",
                edit_semantics="content_revision",
                deletion_semantics="explicit_upstream",
                reconciliation=True,
            ),
            policy=ConnectorPolicy(
                visibility_modes=("private",),
                privacy_modes=("drop", "scrub"),
                default_privacy_mode="scrub",
                retention_modes=("source_controlled",),
                attachment_capability=False,
            ),
            selection_fields=("chats",),
        )
        self.assertEqual(item.auth.kind, "os_permission")
        self.assertEqual(item.placement.execution, "source_local")

    def test_unknown_execution_and_credential_shapes_fail_closed(self):
        cases = []
        unknown = manifest_mapping()
        unknown["executable"] = "/tmp/plugin"
        cases.append(unknown)

        credential = manifest_mapping()
        credential["auth"] = {**credential["auth"], "token": "synthetic-secret"}
        cases.append(credential)

        recipe = manifest_mapping()
        recipe["placement"] = {
            **recipe["placement"], "url_template": "https://example.invalid/{path}",
        }
        cases.append(recipe)

        for value in cases:
            with self.subTest(value=value), self.assertRaises(ConnectorRegistryError):
                ConnectorDefinitionV3.from_mapping(value)

    def test_invalid_facet_combinations_fail_closed(self):
        cases = []
        os_remote = manifest_mapping()
        os_remote["auth"] = {
            "kind": "os_permission", "minimum_scopes": ["macos.full_disk_access"],
        }
        cases.append(os_remote)

        export_poll = manifest_mapping()
        export_poll["auth"] = {"kind": "selected_export", "minimum_scopes": []}
        cases.append(export_poll)

        local_webhook = manifest_mapping()
        local_webhook["placement"] = {
            "execution": "source_local", "acquisition": ["webhook"],
        }
        cases.append(local_webhook)

        unscoped_oauth = manifest_mapping()
        unscoped_oauth["auth"] = {"kind": "oauth2", "minimum_scopes": []}
        cases.append(unscoped_oauth)

        for value in cases:
            with self.subTest(value=value), self.assertRaises(ConnectorRegistryError):
                ConnectorDefinitionV3.from_mapping(value)

    def test_lists_are_sorted_unique_and_bounded(self):
        for field, value in (
            ("selection_fields", ["labels", "account"]),
            ("selection_fields", ["account", "account"]),
            ("record_kinds", ["communication_message.v1"] * 2),
        ):
            manifest = manifest_mapping()
            manifest[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ConnectorRegistryError):
                ConnectorDefinitionV3.from_mapping(manifest)

        manifest = manifest_mapping()
        manifest["selection_fields"] = [f"field_{index}" for index in range(33)]
        with self.assertRaises(ConnectorRegistryError):
            ConnectorDefinitionV3.from_mapping(manifest)


class ConnectorPageWireTest(unittest.TestCase):
    def test_published_wire_schema_matches_the_runtime_envelope(self):
        schema_path = Path(__file__).resolve().parents[1] / "contracts" / "connector_page_v1.json"
        schema = json.loads(schema_path.read_text())
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            set(schema["required"]),
            {"api_version", "records", "next_cursor", "has_more"},
        )
        self.assertEqual(
            schema["properties"]["api_version"]["const"],
            "recall.connector-page.v1",
        )
        self.assertEqual(schema["properties"]["records"]["maxItems"], 500)

    def test_page_wire_round_trips_typed_records_exactly(self):
        page = ConnectorPage(
            records=(communication_record(),),
            next_cursor="page-2",
            has_more=True,
        )
        encoded = encode_page_wire(page)
        self.assertIsInstance(encoded, bytes)
        self.assertNotIn(b"source_id", encoded)
        self.assertEqual(decode_page_wire(encoded), page)
        self.assertEqual(json.loads(encoded)["api_version"], "recall.connector-page.v1")

    def test_page_wire_is_closed_bounded_and_rejects_vague_success(self):
        valid = json.loads(encode_page_wire(ConnectorPage(
            records=(communication_record(),), next_cursor="done", has_more=False,
        )))
        cases = (
            {**valid, "executable": "plugin"},
            {**valid, "api_version": "recall.connector-page.v0"},
            {**valid, "next_cursor": ""},
            {**valid, "records": [{**valid["records"][0], "schema_version": 99}]},
        )
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ConnectorContractError):
                decode_page_wire(json.dumps(value).encode())
        with self.assertRaises(ConnectorContractError):
            decode_page_wire(b"{}")
        with self.assertRaises(ConnectorContractError):
            decode_page_wire(b"not-json")
        with self.assertRaises(ConnectorContractError):
            decode_page_wire(b"x" * 9_000_000)


if __name__ == "__main__":
    unittest.main()
