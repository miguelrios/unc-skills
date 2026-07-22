from __future__ import annotations

import base64
import hashlib
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
from tests.test_connector_sdk import FakeArchive, FakeBrain


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
    def connector(
        self,
        rail: FakeRail,
        *,
        include_attachments: bool = False,
    ) -> GmailConnector:
        return GmailConnector(
            rail=rail,
            source_id="synthetic:google:gmail",
            own_addresses=("owner@example.invalid",),
            label_ids=("INBOX",),
            query="newer_than:30d",
            include_spam_trash=False,
            include_attachments=include_attachments,
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
        self.assertEqual(first.records[0].content["content_fidelity"], "complete")
        self.assertNotIn("content_omissions", first.records[0].content)
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

    def test_complete_body_prefers_external_plain_part_over_html_alternative(self):
        rail = FakeRail()
        rail.add("gmail.messages.list", {"messages": [{"id": "m1"}]})
        message = gmail_message("m1")
        message["payload"] = {
            "mimeType": "multipart/alternative",
            "headers": message["payload"]["headers"],
            "body": {"size": 0},
            "parts": [
                {
                    "mimeType": "text/plain",
                    "filename": "",
                    "headers": [{
                        "name": "Content-Type",
                        "value": "text/plain; charset=utf-8",
                    }],
                    "body": {"attachmentId": "body-part-1", "size": 28},
                },
                {
                    "mimeType": "text/html",
                    "filename": "",
                    "headers": [],
                    "body": {"data": encoded("<p>Duplicate HTML body</p>")},
                },
            ],
        }
        rail.add("gmail.messages.get", message)
        rail.add(
            "gmail.messages.attachments.get",
            {"size": 28, "data": encoded("Complete external plain body")},
        )

        record = self.connector(rail).pull(None).records[0]

        self.assertEqual(record.content["text"], "Complete external plain body")
        self.assertEqual(record.content["format"], "text/plain")
        self.assertEqual(record.content["content_fidelity"], "complete")
        self.assertEqual(
            rail.calls[-1],
            (
                "gmail.messages.attachments.get",
                {"userId": "me", "messageId": "m1", "id": "body-part-1"},
            ),
        )

    def test_html_only_body_is_converted_to_searchable_text(self):
        rail = FakeRail()
        rail.add("gmail.messages.list", {"messages": [{"id": "m1"}]})
        message = gmail_message("m1")
        message["payload"] = {
            "mimeType": "text/html",
            "headers": message["payload"]["headers"],
            "body": {
                "data": encoded(
                    "<html><head><style>hidden</style></head><body>"
                    "<h1>Quarterly update</h1>"
                    "<p>Revenue grew &amp; churn fell.</p>"
                    "<script>privateTracker()</script></body></html>"
                )
            },
        }
        rail.add("gmail.messages.get", message)

        record = self.connector(rail).pull(None).records[0]

        self.assertEqual(
            record.content["text"],
            "Quarterly update\nRevenue grew & churn fell.",
        )
        self.assertEqual(record.content["format"], "text/html-derived")
        self.assertEqual(record.content["content_fidelity"], "complete")

    def test_all_body_sections_are_kept_and_file_attachments_are_explicit(self):
        rail = FakeRail()
        rail.add("gmail.messages.list", {"messages": [{"id": "m1"}]})
        message = gmail_message("m1")
        message["payload"] = {
            "mimeType": "multipart/mixed",
            "headers": message["payload"]["headers"],
            "body": {"size": 0},
            "parts": [
                {
                    "mimeType": "text/plain",
                    "filename": "",
                    "headers": [],
                    "body": {"data": encoded("Opening section")},
                },
                {
                    "mimeType": "text/plain",
                    "filename": "",
                    "headers": [],
                    "body": {"data": encoded("Closing section")},
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "board-pack.pdf",
                    "headers": [{
                        "name": "Content-Disposition",
                        "value": "attachment; filename=board-pack.pdf",
                    }],
                    "body": {"attachmentId": "file-1", "size": 12345},
                },
            ],
        }
        rail.add("gmail.messages.get", message)

        record = self.connector(rail).pull(None).records[0]

        self.assertEqual(record.content["text"], "Opening section\n\nClosing section")
        self.assertEqual(record.content["content_fidelity"], "partial")
        self.assertEqual(record.content["content_omissions"], ["file_attachments"])
        self.assertEqual(
            record.content["attachments"],
            [{
                "mime_type": "application/pdf",
                "name": "board-pack.pdf",
                "size_bytes": 12345,
            }],
        )
        self.assertFalse(any(
            call[0] == "gmail.messages.attachments.get" for call in rail.calls
        ))

    def test_enabled_attachment_is_exactly_archived_and_searchably_projected(self):
        rail = FakeRail()
        rail.add("gmail.messages.list", {"messages": [{"id": "m1"}]})
        message = gmail_message("m1")
        message["payload"] = {
            "mimeType": "multipart/mixed",
            "headers": message["payload"]["headers"],
            "body": {"size": 0},
            "parts": [
                {
                    "mimeType": "text/plain",
                    "filename": "",
                    "headers": [],
                    "body": {"data": encoded("Message body")},
                },
                {
                    "mimeType": "text/plain",
                    "filename": "notes.txt",
                    "headers": [{
                        "name": "Content-Disposition",
                        "value": "attachment; filename=notes.txt",
                    }],
                    "body": {"attachmentId": "file-1", "size": 34},
                },
            ],
        }
        exact = b"Searchable synthetic attachment text"
        rail.add("gmail.messages.get", message)
        rail.add(
            "gmail.messages.attachments.get",
            {"size": len(exact), "data": base64.urlsafe_b64encode(exact).decode().rstrip("=")},
        )
        connector = self.connector(rail, include_attachments=True)

        page = connector.pull(None)

        self.assertEqual(len(page.records), 2)
        parent, attachment = page.records
        self.assertEqual(parent.content["content_fidelity"], "complete")
        self.assertNotIn("content_omissions", parent.content)
        self.assertTrue(attachment.native_id.startswith("gmail-attachment:m1:"))
        self.assertEqual(attachment.native_parent_id, "gmail:m1")
        self.assertEqual(attachment.content["text"], exact.decode())
        self.assertEqual(
            attachment.content["artifact_content_sha256"],
            hashlib.sha256(exact).hexdigest(),
        )
        self.assertEqual(attachment.content["content_fidelity"], "complete")
        self.assertEqual(attachment.archive_payload, exact)
        self.assertEqual(attachment.archive_media_type, "text/plain")
        self.assertEqual(
            parent.content["attachments"][0]["document_id"],
            attachment.content["document_id"],
        )

        with tempfile.TemporaryDirectory() as temporary:
            archive = FakeArchive()
            brain = FakeBrain()
            runner = ConnectorRunner(
                connector=connector,
                brain=brain,
                archive=archive,
                tenant_id="tenant:synthetic",
                principal_id="principal:owner",
                spool_path=Path(temporary) / "gmail.db",
                privacy=PrivacyPolicy(mode="scrub"),
            )
            # Use the already-observed page as a frozen connector response.
            connector.pull = lambda _cursor: page
            self.assertEqual(runner.run_once()["archived"], 2)
            self.assertIn(exact, archive.objects.values())
            self.assertNotIn(
                base64.b64encode(exact).decode(),
                json.dumps(list(brain.events.values())),
            )
            runner.close()

    def test_enabled_attachment_failures_are_explicit_and_never_fetched_unnecessarily(self):
        cases = (
            ("application/octet-stream", 12, "attachment_unsupported_type"),
            ("application/pdf", 17 * 1024 * 1024, "attachment_size_limit"),
        )
        for media_type, size, omission in cases:
            with self.subTest(omission=omission):
                rail = FakeRail()
                rail.add("gmail.messages.list", {"messages": [{"id": "m1"}]})
                message = gmail_message("m1")
                message["payload"] = {
                    "mimeType": "multipart/mixed",
                    "headers": message["payload"]["headers"],
                    "body": {"size": 0},
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "filename": "",
                            "headers": [],
                            "body": {"data": encoded("Message body")},
                        },
                        {
                            "mimeType": media_type,
                            "filename": "synthetic.bin",
                            "headers": [],
                            "body": {"attachmentId": "file-1", "size": size},
                        },
                    ],
                }
                rail.add("gmail.messages.get", message)

                page = self.connector(rail, include_attachments=True).pull(None)

                self.assertEqual(len(page.records), 1)
                self.assertEqual(page.records[0].content["content_fidelity"], "partial")
                self.assertEqual(page.records[0].content["content_omissions"], [omission])
                self.assertFalse(any(
                    operation == "gmail.messages.attachments.get"
                    for operation, _params in rail.calls
                ))

    def test_duplicate_attachment_identity_is_fetched_and_emitted_once(self) -> None:
        rail = FakeRail()
        rail.add("gmail.messages.list", {"messages": [{"id": "m1"}]})
        message = gmail_message("m1")
        attachment = {
            "mimeType": "text/plain",
            "filename": "duplicate.txt",
            "headers": [],
            "body": {"attachmentId": "same-file", "size": 15},
        }
        message["payload"] = {
            "mimeType": "multipart/mixed",
            "headers": message["payload"]["headers"],
            "body": {"size": 0},
            "parts": [
                {
                    "mimeType": "text/plain",
                    "filename": "",
                    "headers": [],
                    "body": {"data": encoded("Message body")},
                },
                attachment,
                dict(attachment),
            ],
        }
        exact = b"Duplicate-proof!"
        rail.add("gmail.messages.get", message)
        rail.add(
            "gmail.messages.attachments.get",
            {"size": len(exact), "data": base64.urlsafe_b64encode(exact).decode().rstrip("=")},
        )

        page = self.connector(rail, include_attachments=True).pull(None)

        self.assertEqual(len(page.records), 2)
        self.assertEqual(len(page.records[0].content["attachments"]), 1)
        self.assertEqual(
            sum(
                operation == "gmail.messages.attachments.get"
                for operation, _params in rail.calls
            ),
            1,
        )

    def test_raw_revision_changes_even_when_normalized_text_is_identical(self) -> None:
        records = []
        for exact in (b"Same text\r\n", b"Same text\n"):
            rail = FakeRail()
            rail.add("gmail.messages.list", {"messages": [{"id": "m1"}]})
            message = gmail_message("m1")
            message["payload"] = {
                "mimeType": "multipart/mixed",
                "headers": message["payload"]["headers"],
                "body": {"size": 0},
                "parts": [
                    {
                        "mimeType": "text/plain",
                        "filename": "",
                        "headers": [],
                        "body": {"data": encoded("Message body")},
                    },
                    {
                        "mimeType": "text/plain",
                        "filename": "same.txt",
                        "headers": [],
                        "body": {"attachmentId": "stable-file", "size": len(exact)},
                    },
                ],
            }
            rail.add("gmail.messages.get", message)
            rail.add("gmail.messages.attachments.get", {
                "size": len(exact),
                "data": base64.urlsafe_b64encode(exact).decode().rstrip("="),
            })
            records.append(self.connector(rail, include_attachments=True).pull(None).records[1])

        self.assertEqual(records[0].native_id, records[1].native_id)
        self.assertEqual(records[0].content["text"], records[1].content["text"])
        self.assertNotEqual(
            records[0].content["artifact_content_sha256"],
            records[1].content["artifact_content_sha256"],
        )

    def test_snippet_fallback_is_never_silently_reported_as_complete(self):
        rail = FakeRail()
        rail.add("gmail.messages.list", {"messages": [{"id": "m1"}]})
        message = gmail_message("m1")
        message["payload"]["body"] = {"size": 0}
        rail.add("gmail.messages.get", message)

        record = self.connector(rail).pull(None).records[0]

        self.assertEqual(record.content["text"], "Synthetic snippet")
        self.assertEqual(record.content["content_fidelity"], "partial")
        self.assertEqual(
            record.content["content_omissions"],
            ["body_part_unavailable", "snippet_fallback"],
        )

    def test_oversized_unicode_body_is_bounded_and_explicitly_partial(self):
        rail = FakeRail()
        rail.add("gmail.messages.list", {"messages": [{"id": "m1"}]})
        rail.add("gmail.messages.get", gmail_message("m1", text="🧠" * 150_000))

        record = self.connector(rail).pull(None).records[0]

        self.assertEqual(record.content["content_fidelity"], "partial")
        self.assertIn("body_truncated", record.content["content_omissions"])
        self.assertLessEqual(
            len(json.dumps(record.content["text"]).encode()),
            500_000,
        )

    def test_decoded_body_survives_an_inaccurate_provider_size(self):
        rail = FakeRail()
        rail.add("gmail.messages.list", {"messages": [{"id": "m1"}]})
        message = gmail_message("m1", text="Complete body despite size drift")
        message["payload"]["body"]["size"] = 1
        rail.add("gmail.messages.get", message)

        record = self.connector(rail).pull(None).records[0]

        self.assertEqual(record.content["text"], "Complete body despite size drift")
        self.assertEqual(record.content["content_fidelity"], "partial")
        self.assertEqual(record.content["content_omissions"], ["body_size_mismatch"])

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
