from __future__ import annotations

import hashlib
import hmac
import json
import unittest

from client.mcp import McpProtocolError, McpServer
from connectors import kit
from connectors.managed_auth import (
    ManagedAuthConnector,
    ManagedPage,
    ManagedProjection,
    ManagedRecord,
    verify_nango_wakeup,
)
from connectors.registry import (
    REGISTRY,
    ConnectorRegistryError,
    definition,
    preview,
    validate_policy,
)
from connectors.sdk import ConnectorContractError, ConnectorRunError


class Transport:
    def __init__(self, source_id, pages):
        self.bound_source_id = source_id
        self.pages = list(pages)
        self.fetches = []
        self.revocations = []

    def fetch_records(
        self, *, connection_id, provider_config_key, model, cursor
    ):
        self.fetches.append(
            (connection_id, provider_config_key, model, cursor)
        )
        return self.pages.pop(0)

    def revoke(self, *, connection_id, provider_config_key):
        self.revocations.append((connection_id, provider_config_key))


def mapper(record):
    return ManagedProjection(
        occurred_at=record.data.get("updated_at", "2026-07-18T10:00:00Z"),
        content={
            "kind": "document.v1",
            "content_fidelity": "complete",
            "document_id": record.id,
            "mime_type": "text/plain",
            "name": record.data.get("title", "Synthetic"),
            "surface": "managed_auth_synthetic",
            "text": record.data["text"],
        },
        provenance={"uri": "connector://managed-auth/synthetic"},
    )


class Backend:
    def __init__(self):
        self.calls = 0

    def capture(self, _value):
        self.calls += 1
        return {"status": "committed"}

    def forget(self, _receipt):
        self.calls += 1
        return {"status": "committed"}

    def doctor(self):
        self.calls += 1
        return {"status": "ok"}


class ManagedAuthAdapterTest(unittest.TestCase):
    def test_nango_shaped_wakeup_is_signed_closed_and_bounded(self):
        secret = b"synthetic-webhook-secret-material"
        body = json.dumps({
            "connectionId": "connection-test",
            "providerConfigKey": "provider-test",
            "model": "SyntheticDocument",
            "responseResults": {"added": 1, "updated": 2, "deleted": 0},
            "modifiedAfter": "2026-07-18T10:00:00Z",
        }, separators=(",", ":")).encode()
        signature = hmac.new(secret, body, hashlib.sha256).hexdigest()
        wakeup = verify_nango_wakeup(
            body=body, signature=signature, secret=secret,
        )
        self.assertEqual(wakeup.connection_id, "connection-test")
        self.assertEqual(wakeup.changed_records, 3)
        with self.assertRaisesRegex(ConnectorContractError, "signature_invalid"):
            verify_nango_wakeup(
                body=body, signature="0" * 64, secret=secret,
            )
        with self.assertRaisesRegex(ConnectorContractError, "wakeup_invalid"):
            verify_nango_wakeup(
                body=body[:-1] + b',"extra":true}',
                signature=hmac.new(
                    secret, body[:-1] + b',"extra":true}', hashlib.sha256
                ).hexdigest(),
                secret=secret,
            )

    def test_cursored_records_deletions_authority_and_revoke(self):
        source_id = "managed:synthetic:test"
        transport = Transport(source_id, [ManagedPage(
            records=(
                ManagedRecord(
                    id="record-1", last_action="ADDED", deleted_at=None,
                    data={"text": "synthetic managed marker"},
                ),
                ManagedRecord(
                    id="record-2", last_action="DELETED",
                    deleted_at="2026-07-18T10:00:00Z", data={},
                ),
            ),
            next_cursor="opaque-next-1",
            has_more=False,
        )])
        connector = ManagedAuthConnector(
            source_id=source_id,
            connection_id="connection-test",
            provider_config_key="provider-test",
            model="SyntheticDocument",
            record_kind="document.v1",
            transport=transport,
            mapper=mapper,
        )
        page = connector.pull(None)
        self.assertEqual(len(page.records), 2)
        self.assertFalse(page.records[0].deleted)
        self.assertTrue(page.records[1].deleted)
        self.assertEqual(page.next_cursor, "opaque-next-1")
        self.assertEqual(transport.fetches[0][-1], None)
        wrong_body = json.dumps({
            "connectionId": "connection-other",
            "providerConfigKey": "provider-test",
            "model": "SyntheticDocument",
            "responseResults": {"added": 1, "updated": 0, "deleted": 0},
            "modifiedAfter": "2026-07-18T10:00:00Z",
        }, separators=(",", ":")).encode()
        secret = b"synthetic-webhook-secret-material"
        with self.assertRaisesRegex(
            ConnectorContractError, "wakeup_binding_invalid"
        ):
            connector.accept_wakeup(
                body=wrong_body,
                signature=hmac.new(
                    secret, wrong_body, hashlib.sha256
                ).hexdigest(),
                secret=secret,
            )
        connector.revoke()
        self.assertEqual(
            transport.revocations, [("connection-test", "provider-test")]
        )
        with self.assertRaisesRegex(ConnectorRunError, "authority_revoked"):
            connector.pull(page.next_cursor)

        with self.assertRaisesRegex(
            ConnectorContractError, "source_authority_mismatch"
        ):
            ManagedAuthConnector(
                source_id="managed:other:test",
                connection_id="connection-test",
                provider_config_key="provider-test",
                model="SyntheticDocument",
                record_kind="document.v1",
                transport=transport,
                mapper=mapper,
            )

    def test_record_and_page_wire_reject_ambiguous_success(self):
        with self.assertRaises(ConnectorContractError):
            ManagedRecord(
                id="record-1", last_action="DELETED", deleted_at=None, data={},
            )
        with self.assertRaises(ConnectorContractError):
            ManagedRecord(
                id="record-1", last_action="UPDATED",
                deleted_at="2026-07-18T10:00:00Z",
                data={"text": "ambiguous"},
            )
        with self.assertRaises(ConnectorContractError):
            ManagedPage(records=(), next_cursor="", has_more=False)

    def test_default_install_is_zero_transfer_and_capture_is_isolated(self):
        self.assertIs(kit.ManagedAuthConnector, ManagedAuthConnector)
        self.assertIs(kit.verify_nango_wakeup, verify_nango_wakeup)
        self.assertNotIn(
            "managed-auth", {item.connector_id for item in REGISTRY}
        )
        self.assertEqual(preview()["network_requests"], 0)
        capture = definition("recall.capture")
        self.assertEqual(capture.authority_slots, ("brain",))
        self.assertEqual(capture.mode, "push")
        self.assertEqual(definition("portable.feed").authority_slots, (
            "brain", "source",
        ))
        with self.assertRaisesRegex(
            ConnectorRegistryError, "visibility_not_allowed"
        ):
            validate_policy(
                "portable.feed", visibility="shared", privacy_mode="scrub",
                authorities={"brain", "source"},
            )

        backend = Backend()
        server = McpServer(backend, capture_origin="synthetic-client")
        with self.assertRaisesRegex(McpProtocolError, "capture_invalid"):
            server.handle({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {
                    "name": "recall_capture",
                    "arguments": {
                        "schema_version": 1,
                        "title": "Synthetic",
                        "body": "Synthetic deliberate capture",
                        "occurred_at": "2026-07-18T10:00:00Z",
                        "provenance": {"uri": "manual://synthetic"},
                        "source_id": "portable:feed:test",
                    },
                },
            })
        self.assertEqual(backend.calls, 0)
