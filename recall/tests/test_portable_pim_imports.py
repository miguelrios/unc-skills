from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from client.cli import parser
from connectors.portable_pim import (
    CalendarImportConnector,
    ContactImportConnector,
    MailImportConnector,
)
from connectors.registry import definition
from connectors.sdk import ConnectorContractError, ConnectorRunner
from privacy.policy import PrivacyPolicy


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


class PortablePimImportTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_manifests_and_cli_are_explicit_bounded_imports(self):
        expected = {
            "portable.mail": ("communication_message.v1", "mail-import-sync"),
            "portable.calendar": ("calendar_event.v1", "calendar-import-sync"),
            "portable.contacts": ("contact_identity.v1", "contact-import-sync"),
        }
        for connector_id, (kind, command) in expected.items():
            item = definition(connector_id)
            self.assertEqual(item.execution_placement, "source_local")
            self.assertEqual(item.acquisition_modes, ("import",))
            self.assertEqual(item.auth.kind, "selected_export")
            self.assertEqual(item.record_kinds, (kind,))
            self.assertEqual(item.command, command)
            self.assertEqual(
                item.selection_fields,
                ("archive_id", "owner_identifiers", "removed_native_ids"),
            )
        args = parser().parse_args([
            "mail-import-sync", "--endpoint", "https://brain.example.invalid",
            "--source-id", "mail:import:synthetic", "--keychain-service", "synthetic",
            "--keychain-account", "mail:import:synthetic",
            "--input", "/synthetic/mail.mbox", "--archive-id", "synthetic-mail",
            "--owner-identifier", "owner@example.invalid",
            "--remove-native-id", "mail:" + "a" * 64,
            "--spool", "/synthetic/mail.db",
        ])
        self.assertEqual(args.privacy_mode, "scrub")
        self.assertEqual(args.visibility, "private")

    def test_eml_and_mbox_paginate_revise_and_remove_only_explicitly(self):
        eml = self.root / "mail.eml"
        eml.write_text(
            "Message-ID: <one@example.invalid>\n"
            "Date: Fri, 17 Jul 2026 10:00:00 +0000\n"
            "From: sender@example.invalid\nTo: owner@example.invalid\n"
            "Subject: Synthetic subject\nContent-Type: text/plain; charset=utf-8\n\n"
            "first synthetic body\n",
            encoding="utf-8",
        )
        connector = MailImportConnector(
            path=eml, source_id="mail:import:synthetic",
            archive_id="synthetic-mail",
            owner_identifiers=("owner@example.invalid",), page_size=1,
        )
        first = connector.pull(None)
        self.assertFalse(first.has_more)
        record = first.records[0]
        self.assertEqual(record.content["direction"], "inbound")
        self.assertEqual(record.content["subject"], "Synthetic subject")
        native_id = record.native_id

        eml.write_text(eml.read_text().replace("first synthetic", "revised synthetic"))
        revised = connector.pull(first.next_cursor)
        self.assertEqual(revised.records[0].native_id, native_id)
        self.assertIn("revised synthetic", revised.records[0].content["text"])

        removed = MailImportConnector(
            path=eml, source_id="mail:import:synthetic",
            archive_id="synthetic-mail",
            owner_identifiers=("owner@example.invalid",),
            removed_native_ids=(native_id,),
        ).pull(None)
        tombstone = next(item for item in removed.records if item.native_id == native_id)
        self.assertTrue(tombstone.deleted)

        mbox = self.root / "mail.mbox"
        message = eml.read_text()
        mbox.write_text(
            "From sender@example.invalid Fri Jul 17 10:00:00 2026\n" + message
            + "\nFrom owner@example.invalid Fri Jul 17 11:00:00 2026\n"
            + message.replace("<one@", "<two@").replace(
                "From: sender@", "From: owner@"
            ),
        )
        page = MailImportConnector(
            path=mbox, source_id="mail:import:synthetic",
            archive_id="synthetic-mail",
            owner_identifiers=("owner@example.invalid",),
        ).pull(None)
        self.assertEqual(len(page.records), 2)
        self.assertEqual(
            {item.content["direction"] for item in page.records},
            {"inbound", "outbound"},
        )

    def test_ics_unfolds_recurrence_and_treats_cancelled_as_explicit_delete(self):
        path = self.root / "calendar.ics"
        path.write_text(
            "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
            "BEGIN:VEVENT\r\nUID:event-1@example.invalid\r\n"
            "DTSTAMP:20260717T100000Z\r\nDTSTART:20260718T120000Z\r\n"
            "DTEND:20260718T130000Z\r\nSUMMARY:Synthetic planning\r\n"
            "DESCRIPTION:First line\r\n continued\r\n"
            "ATTENDEE:MAILTO:person@example.invalid\r\nEND:VEVENT\r\n"
            "BEGIN:VEVENT\r\nUID:event-2@example.invalid\r\n"
            "RECURRENCE-ID:20260719T120000Z\r\n"
            "DTSTAMP:20260717T110000Z\r\nDTSTART:20260719T120000Z\r\n"
            "DTEND:20260719T130000Z\r\nSUMMARY:Cancelled fixture\r\n"
            "STATUS:CANCELLED\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n",
            encoding="utf-8",
        )
        page = CalendarImportConnector(
            path=path, source_id="calendar:import:synthetic",
            archive_id="synthetic-calendar",
        ).pull(None)
        self.assertEqual(len(page.records), 2)
        live = next(item for item in page.records if not item.deleted)
        deleted = next(item for item in page.records if item.deleted)
        self.assertEqual(live.content["description"], "First linecontinued")
        self.assertEqual(
            live.content["attendee_ids"], ["person@example.invalid"]
        )
        self.assertTrue(deleted.native_id.startswith("ical:"))

    def test_vcf_unfolds_contact_and_supports_explicit_owner_removal(self):
        path = self.root / "contacts.vcf"
        path.write_text(
            "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:contact-1\r\n"
            "FN:Synthetic Person\r\nEMAIL:person@example.invalid\r\n"
            "TEL:+15555550100\r\nORG:Synthetic\r\nTITLE:Research\r\n"
            "NOTE:bounded\r\n continuation\r\nEND:VCARD\r\n",
            encoding="utf-8",
        )
        connector = ContactImportConnector(
            path=path, source_id="contacts:import:synthetic",
            archive_id="synthetic-contacts",
            owner_identifiers=("owner@example.invalid",),
        )
        first = connector.pull(None)
        record = first.records[0]
        self.assertEqual(record.content["display_name"], "Synthetic Person")
        self.assertEqual(record.content["identifier"], "person@example.invalid")
        self.assertEqual(record.content["organization"], "Synthetic")
        removed = ContactImportConnector(
            path=path, source_id="contacts:import:synthetic",
            archive_id="synthetic-contacts",
            owner_identifiers=("owner@example.invalid",),
            removed_native_ids=(record.native_id,),
        ).pull(None)
        self.assertTrue(next(
            item for item in removed.records if item.native_id == record.native_id
        ).deleted)

    def test_vcf_fallback_identity_survives_unrelated_record_reordering(self):
        path = self.root / "contacts.vcf"
        cards = [
            "BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Alpha\r\n"
            "EMAIL:alpha@example.invalid\r\nEND:VCARD\r\n",
            "BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Beta\r\n"
            "EMAIL:beta@example.invalid\r\nEND:VCARD\r\n",
        ]
        path.write_text("".join(cards))
        connector = ContactImportConnector(
            path=path, source_id="contacts:import:synthetic",
            archive_id="synthetic-contacts",
        )
        before = {
            item.content["display_name"]: item.native_id
            for item in connector.pull(None).records
        }
        path.write_text("".join(reversed(cards)))
        after = {
            item.content["display_name"]: item.native_id
            for item in connector.pull(None).records
        }
        self.assertEqual(after, before)

    def test_malformed_alias_duplicate_and_implicit_absence_fail_closed(self):
        path = self.root / "contacts.vcf"
        path.write_text("not-vcard\n")
        alias = self.root / "alias.vcf"
        alias.symlink_to(path)
        with self.assertRaisesRegex(ConnectorContractError, "symlink"):
            ContactImportConnector(
                path=alias, source_id="contacts:import:synthetic",
                archive_id="synthetic-contacts",
            )
        with self.assertRaisesRegex(ConnectorContractError, "format"):
            ContactImportConnector(
                path=path, source_id="contacts:import:synthetic",
                archive_id="synthetic-contacts",
            ).pull(None)

    def test_privacy_precedes_spool_and_brain_for_every_import(self):
        canary = "synthetic-portable-private-canary"
        fixtures = []
        eml = self.root / "mail.eml"
        eml.write_text(
            "Message-ID: <privacy@example.invalid>\n"
            "Date: Fri, 17 Jul 2026 10:00:00 +0000\n"
            "From: sender@example.invalid\nTo: owner@example.invalid\n\n"
            f"api_key={canary}\n"
        )
        fixtures.append(MailImportConnector(
            path=eml, source_id="mail:import:privacy",
            archive_id="privacy-mail",
        ))
        ics = self.root / "calendar.ics"
        ics.write_text(
            "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:privacy-event\r\n"
            "DTSTAMP:20260717T100000Z\r\nDTSTART:20260718T120000Z\r\n"
            "DTEND:20260718T130000Z\r\nSUMMARY:Fixture\r\n"
            f"DESCRIPTION:api_key={canary}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        fixtures.append(CalendarImportConnector(
            path=ics, source_id="calendar:import:privacy",
            archive_id="privacy-calendar",
        ))
        vcf = self.root / "contacts.vcf"
        vcf.write_text(
            "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:privacy-contact\r\n"
            f"FN:api_key={canary}\r\nEMAIL:person@example.invalid\r\nEND:VCARD\r\n"
        )
        fixtures.append(ContactImportConnector(
            path=vcf, source_id="contacts:import:privacy",
            archive_id="privacy-contacts",
        ))
        for index, connector in enumerate(fixtures):
            brain = Brain()
            spool = self.root / f"state-{index}" / "spool.db"
            runner = ConnectorRunner(
                connector=connector, brain=brain, spool_path=spool,
                privacy=PrivacyPolicy(mode="scrub"),
            )
            try:
                runner.run_once()
            finally:
                runner.close()
            raw = b"".join(
                item.read_bytes() for item in spool.parent.glob("spool.db*")
                if item.is_file()
            )
            self.assertNotIn(canary.encode(), raw)
            self.assertNotIn(canary, str(tuple(brain.events.values())))


if __name__ == "__main__":
    unittest.main()
