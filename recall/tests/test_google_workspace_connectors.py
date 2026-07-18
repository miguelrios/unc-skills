from __future__ import annotations

import base64
import json
import tempfile
import unittest
from collections import defaultdict, deque
from pathlib import Path

from connectors.google_workspace import (
    GmailConnector,
    GoogleCalendarConnector,
    GoogleContactsConnector,
    GoogleDriveConnector,
)
from connectors.sdk import (
    ConnectorContractError,
    ConnectorRateLimited,
    ConnectorRunError,
    ConnectorRunner,
    ConnectorUpstreamError,
)
from connectors.workspace_rail import WorkspaceRailError
from privacy.policy import PrivacyPolicy
from tests.test_connector_sdk import FakeBrain


def encoded(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def gmail_message(
    message_id: str,
    *,
    thread_id: str = "thread-1",
    history_id: str = "100",
    text: str = "Synthetic body",
    sender: str = "friend@example.invalid",
) -> dict:
    return {
        "id": message_id,
        "threadId": thread_id,
        "historyId": history_id,
        "internalDate": "1784332800000",
        "snippet": "Synthetic snippet",
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "To", "value": "owner@example.invalid"},
                {"name": "Subject", "value": "Synthetic subject"},
            ],
            "body": {"data": encoded(text)},
        },
    }


class FakeRail:
    def __init__(self):
        self.responses = defaultdict(deque)
        self.calls = []
        self.exports = {}

    def add(self, operation: str, *values) -> None:
        self.responses[operation].extend(values)

    def run(self, operation: str, params: dict):
        self.calls.append((operation, dict(params)))
        if not self.responses[operation]:
            raise AssertionError(f"unexpected synthetic operation {operation}")
        value = self.responses[operation].popleft()
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value(params)
        return value

    def export_document(self, *, file_id: str, mime_type: str = "text/plain") -> bytes:
        self.calls.append(("drive.files.export", {
            "fileId": file_id,
            "mimeType": mime_type,
        }))
        return self.exports[file_id]


class GmailConnectorTest(unittest.TestCase):
    def connector(self, rail: FakeRail) -> GmailConnector:
        return GmailConnector(
            rail=rail,
            source_id="synthetic:google:gmail",
            own_addresses=("owner@example.invalid",),
            label_ids=("INBOX",),
            query="newer_than:30d",
            include_spam_trash=False,
            page_size=2,
        )

    def test_full_incremental_delete_and_history_expiry_reconcile(self):
        rail = FakeRail()
        rail.add(
            "gmail.messages.list",
            {
                "messages": [{"id": "m1"}, {"id": "m2"}],
                "nextPageToken": "full-page-2",
            },
            {"messages": [{"id": "m3"}]},
        )
        rail.add(
            "gmail.messages.get",
            gmail_message("m1", history_id="100"),
            gmail_message("m2", history_id="101"),
            gmail_message("m3", history_id="102"),
        )
        connector = self.connector(rail)
        first = connector.pull(None)
        self.assertTrue(first.has_more)
        self.assertEqual([item.native_id for item in first.records], ["gmail:m1", "gmail:m2"])
        self.assertEqual(first.records[0].content["direction"], "inbound")
        self.assertEqual(first.records[0].content["text"], "Synthetic body")
        self.assertEqual(first.records[0].content["subject"], "Synthetic subject")
        second = connector.pull(first.next_cursor)
        self.assertFalse(second.has_more)
        self.assertEqual([item.native_id for item in second.records], ["gmail:m3"])

        rail.add("gmail.history.list", {
            "history": [{
                "id": "103",
                "messagesAdded": [{"message": {"id": "m4", "threadId": "thread-2"}}],
                "messagesDeleted": [{"message": {"id": "m2", "threadId": "thread-1"}}],
            }],
            "historyId": "103",
        })
        rail.add(
            "gmail.messages.get",
            gmail_message(
                "m4",
                thread_id="thread-2",
                history_id="103",
                sender="owner@example.invalid",
            ),
        )
        incremental = connector.pull(second.next_cursor)
        self.assertFalse(incremental.has_more)
        self.assertEqual(
            [(item.native_id, item.deleted) for item in incremental.records],
            [("gmail:m2", True), ("gmail:m4", False)],
        )
        self.assertEqual(incremental.records[1].content["direction"], "outbound")

        rail.add("gmail.history.list", WorkspaceRailError("not_found"))
        rail.add("gmail.messages.list", {"messages": [{"id": "m5"}]})
        rail.add("gmail.messages.get", gmail_message("m5", history_id="200"))
        reconciled = connector.pull(incremental.next_cursor)
        self.assertEqual([item.native_id for item in reconciled.records], ["gmail:m5"])
        self.assertFalse(any(item.deleted for item in reconciled.records))
        self.assertEqual(
            [operation for operation, _params in rail.calls[-3:]],
            ["gmail.history.list", "gmail.messages.list", "gmail.messages.get"],
        )

    def test_runner_keeps_google_cursor_behind_brain_ack(self):
        rail = FakeRail()
        rail.add("gmail.messages.list", {"messages": [{"id": "m1"}]})
        rail.add("gmail.messages.get", gmail_message("m1"))
        brain = FakeBrain()
        brain.fail_after_commit = True
        with tempfile.TemporaryDirectory() as directory:
            runner = ConnectorRunner(
                connector=self.connector(rail),
                brain=brain,
                spool_path=Path(directory) / "gmail.db",
                privacy=PrivacyPolicy(mode="scrub"),
            )
            with self.assertRaisesRegex(ConnectorRunError, "brain_unavailable"):
                runner.run_once()
            self.assertFalse(runner.doctor()["checkpointed"])
            self.assertEqual(runner.doctor()["pending"], 1)
            self.assertEqual(runner.run_once()["replayed"], 1)
            self.assertTrue(runner.doctor()["checkpointed"])
            self.assertEqual(len(brain.events), 1)
            rail.add("gmail.history.list", {
                "history": [{
                    "id": "101",
                    "messages": [{"id": "m1", "threadId": "thread-1"}],
                }],
                "historyId": "101",
            })
            rail.add(
                "gmail.messages.get",
                gmail_message(
                    "m1",
                    history_id="101",
                    text="api_key=google-private-canary",
                ),
            )
            self.assertEqual(runner.run_once()["acked"], 1)
            self.assertEqual(len(brain.events), 2)
            rendered = json.dumps(tuple(brain.events.values()), sort_keys=True)
            self.assertNotIn("google-private-canary", rendered)
            runner.close()

    def test_rate_limit_and_revoked_authority_are_content_free_conditions(self):
        limited = FakeRail()
        limited.add("gmail.messages.list", WorkspaceRailError("rate_limited"))
        with self.assertRaises(ConnectorRateLimited):
            self.connector(limited).pull(None)
        revoked = FakeRail()
        revoked.add("gmail.messages.list", WorkspaceRailError("authority_revoked"))
        with self.assertRaisesRegex(
            ConnectorUpstreamError,
            "connector_authority_revoked",
        ) as raised:
            self.connector(revoked).pull(None)
        self.assertEqual(raised.exception.error_code, "connector_authority_revoked")


class CalendarAndContactsConnectorTest(unittest.TestCase):
    def test_malformed_required_calendar_time_fails_closed(self):
        rail = FakeRail()
        rail.add("calendar.events.list", {
            "items": [{
                "id": "event-bad",
                "status": "confirmed",
                "summary": "Malformed synthetic event",
                "start": {"dateTime": "not-a-time"},
                "end": {"dateTime": "2026-07-18T11:00:00Z"},
            }],
            "nextSyncToken": "calendar-sync-1",
        })
        connector = GoogleCalendarConnector(
            rail=rail,
            source_id="synthetic:google:calendar",
        )
        with self.assertRaisesRegex(ConnectorContractError, "calendar time"):
            connector.pull(None)

    def test_calendar_explicit_cancel_and_410_reconciliation(self):
        rail = FakeRail()
        rail.add("calendar.events.list", {
            "items": [
                {
                    "id": "event-1",
                    "status": "confirmed",
                    "summary": "Synthetic meeting",
                    "description": "Synthetic agenda",
                    "updated": "2026-07-18T01:00:00Z",
                    "start": {"dateTime": "2026-07-18T10:00:00Z"},
                    "end": {"dateTime": "2026-07-18T11:00:00Z"},
                    "organizer": {"email": "owner@example.invalid"},
                    "attendees": [{"email": "friend@example.invalid"}],
                },
                {
                    "id": "event-2",
                    "status": "cancelled",
                    "updated": "2026-07-18T02:00:00Z",
                },
            ],
            "nextSyncToken": "calendar-sync-1",
        })
        connector = GoogleCalendarConnector(
            rail=rail,
            source_id="synthetic:google:calendar",
            calendar_id="primary",
            page_size=50,
        )
        first = connector.pull(None)
        self.assertEqual(
            [(item.native_id, item.deleted) for item in first.records],
            [("gcal:primary:event-1", False), ("gcal:primary:event-2", True)],
        )
        self.assertEqual(first.records[0].content["title"], "Synthetic meeting")
        rail.add("calendar.events.list", WorkspaceRailError("cursor_expired"), {
            "items": [{
                "id": "event-3",
                "status": "confirmed",
                "summary": "Reconciled event",
                "updated": "2026-07-18T03:00:00Z",
                "start": {"date": "2026-07-19"},
                "end": {"date": "2026-07-20"},
            }],
            "nextSyncToken": "calendar-sync-2",
        })
        reconciled = connector.pull(first.next_cursor)
        self.assertEqual([item.native_id for item in reconciled.records], [
            "gcal:primary:event-3",
        ])
        self.assertFalse(reconciled.records[0].deleted)
        self.assertTrue(reconciled.records[0].content["all_day"])
        self.assertNotIn("syncToken", rail.calls[-1][1])

    def test_contacts_project_identity_delete_and_expired_token(self):
        rail = FakeRail()
        normal = {
            "resourceName": "people/c1",
            "metadata": {
                "sources": [{"updateTime": "2026-07-18T04:00:00Z"}],
            },
            "names": [{"displayName": "Synthetic Person"}],
            "emailAddresses": [{"value": "person@example.invalid"}],
            "organizations": [{"name": "Synthetic Org", "title": "Engineer"}],
        }
        deleted = {
            "resourceName": "people/c2",
            "metadata": {
                "deleted": True,
                "sources": [{"updateTime": "2026-07-18T05:00:00Z"}],
            },
        }
        rail.add("people.people.connections.list", {
            "connections": [normal, deleted],
            "nextSyncToken": "people-sync-1",
        })
        connector = GoogleContactsConnector(
            rail=rail,
            source_id="synthetic:google:contacts",
            page_size=100,
        )
        first = connector.pull(None)
        self.assertEqual(
            [(item.native_id, item.deleted) for item in first.records],
            [("gcontact:people/c1", False), ("gcontact:people/c2", True)],
        )
        self.assertEqual(first.records[0].content["organization"], "Synthetic Org")
        rail.add(
            "people.people.connections.list",
            WorkspaceRailError("cursor_expired"),
            {"connections": [normal], "nextSyncToken": "people-sync-2"},
        )
        reconciled = connector.pull(first.next_cursor)
        self.assertEqual(len(reconciled.records), 1)
        self.assertFalse(reconciled.records[0].deleted)
        self.assertNotIn("syncToken", rail.calls[-1][1])


class DriveConnectorTest(unittest.TestCase):
    def test_backfill_captures_start_token_then_changes_and_explicit_removal(self):
        rail = FakeRail()
        rail.exports["doc-1"] = b"Synthetic document body"
        rail.add("drive.changes.getStartPageToken", {"startPageToken": "drive-start-1"})
        rail.add(
            "drive.files.list",
            {
                "files": [{
                    "id": "doc-1",
                    "name": "Synthetic Doc",
                    "mimeType": "application/vnd.google-apps.document",
                    "modifiedTime": "2026-07-18T06:00:00Z",
                    "owners": [{"permissionId": "owner-1"}],
                }],
                "nextPageToken": "files-page-2",
            },
            {"files": [{
                "id": "file-2",
                "name": "Synthetic PDF",
                "mimeType": "application/pdf",
                "modifiedTime": "2026-07-18T06:30:00Z",
            }]},
        )
        connector = GoogleDriveConnector(
            rail=rail,
            source_id="synthetic:google:drive",
            page_size=100,
            include_document_text=True,
        )
        first = connector.pull(None)
        self.assertTrue(first.has_more)
        self.assertEqual(first.records[0].content["text"], "Synthetic document body")
        second = connector.pull(first.next_cursor)
        self.assertFalse(second.has_more)
        rail.add("drive.changes.list", {
            "changes": [
                {"fileId": "file-2", "removed": True},
                {
                    "fileId": "file-3",
                    "removed": False,
                    "file": {
                        "id": "file-3",
                        "name": "Changed text",
                        "mimeType": "text/plain",
                        "modifiedTime": "2026-07-18T07:00:00Z",
                    },
                },
            ],
            "newStartPageToken": "drive-start-2",
        })
        changed = connector.pull(second.next_cursor)
        self.assertEqual(
            [(item.native_id, item.deleted) for item in changed.records],
            [("gdrive:file-2", True), ("gdrive:file-3", False)],
        )

        rail.add("drive.changes.list", WorkspaceRailError("cursor_expired"))
        rail.add("drive.changes.getStartPageToken", {"startPageToken": "drive-start-3"})
        rail.add("drive.files.list", {"files": []})
        reconciled = connector.pull(changed.next_cursor)
        self.assertEqual(reconciled.records, ())
        self.assertFalse(reconciled.has_more)
        self.assertEqual(
            [operation for operation, _params in rail.calls[-3:]],
            [
                "drive.changes.list",
                "drive.changes.getStartPageToken",
                "drive.files.list",
            ],
        )

    def test_invalid_cursor_fails_before_source_io(self):
        rail = FakeRail()
        connector = GoogleDriveConnector(
            rail=rail,
            source_id="synthetic:google:drive",
        )
        with self.assertRaises(ConnectorContractError):
            connector.pull(json.dumps({"mode": "recipe", "url": "https://bad.invalid"}))
        self.assertEqual(rail.calls, [])


if __name__ == "__main__":
    unittest.main()
