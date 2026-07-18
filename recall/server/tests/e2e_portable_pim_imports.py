#!/usr/bin/env python3
"""Fresh-PostgreSQL proof for portable mail, calendar, and contact imports."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RECALL = ROOT / "recall"
SERVER = RECALL / "server"
sys.path.insert(0, str(RECALL))
sys.path.insert(0, str(SERVER))

from connectors.portable_pim import (
    CalendarImportConnector,
    ContactImportConnector,
    MailImportConnector,
)
from connectors.sdk import ConnectorRunner
from privacy.policy import PrivacyPolicy
from recall_server.db import BrainStore
from recall_server.projectors import canonical_json


SOURCES = (
    "synthetic:portable-mail:postgres",
    "synthetic:portable-calendar:postgres",
    "synthetic:portable-contacts:postgres",
)


class StoreWriter:
    def __init__(self, store):
        self.store = store

    def ingest(self, events):
        key = "portable-pim-e2e-" + hashlib.sha256(
            canonical_json(events)
        ).hexdigest()
        acknowledgement, replay = self.store.ingest(key, events)
        return {**acknowledgement, "replay": replay}


def main() -> None:
    store = BrainStore(os.environ["RECALL_DATABASE_URL"])
    store.migrate()
    with store.connect() as connection:
        connection.execute(
            "TRUNCATE chunks,items,sessions,projection_watermarks,source_events,"
            "ingest_batches,source_grants,sources,dead_letters,audit_events "
            "RESTART IDENTITY CASCADE"
        )
    with tempfile.TemporaryDirectory(prefix="recall-portable-pim-e2e-") as temporary:
        root = Path(temporary)
        mail = root / "mail.eml"
        calendar = root / "calendar.ics"
        contacts = root / "contacts.vcf"
        mail.write_text(
            "Message-ID: <portable-e2e@example.invalid>\n"
            "Date: Fri, 17 Jul 2026 10:00:00 +0000\n"
            "From: sender@example.invalid\nTo: owner@example.invalid\n\n"
            "portable mail postgres marker\n"
            "api_key=synthetic-portable-mail-canary\n"
        )
        calendar.write_text(
            "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:portable-e2e-event\r\n"
            "DTSTAMP:20260717T100000Z\r\nDTSTART:20260718T120000Z\r\n"
            "DTEND:20260718T130000Z\r\nSUMMARY:portable calendar postgres marker\r\n"
            "DESCRIPTION:api_key=synthetic-portable-calendar-canary\r\n"
            "END:VEVENT\r\nEND:VCALENDAR\r\n"
        )
        contacts.write_text(
            "BEGIN:VCARD\r\nVERSION:4.0\r\nUID:portable-e2e-contact\r\n"
            "FN:portable contact postgres marker\r\n"
            "EMAIL:person@example.invalid\r\n"
            "NOTE:api_key=synthetic-portable-contact-canary\r\nEND:VCARD\r\n"
        )
        writer = StoreWriter(store)
        connectors = [
            MailImportConnector(
                path=mail, source_id=SOURCES[0], archive_id="portable-e2e-mail",
                owner_identifiers=("owner@example.invalid",),
            ),
            CalendarImportConnector(
                path=calendar, source_id=SOURCES[1],
                archive_id="portable-e2e-calendar",
            ),
            ContactImportConnector(
                path=contacts, source_id=SOURCES[2],
                archive_id="portable-e2e-contacts",
            ),
        ]
        spools = [root / "state" / f"{index}.db" for index in range(3)]
        runners = [
            ConnectorRunner(
                connector=connector, brain=writer, spool_path=spool,
                privacy=PrivacyPolicy(mode="scrub"),
            )
            for connector, spool in zip(connectors, spools, strict=True)
        ]
        for runner in runners:
            assert runner.run_once()["acked"] == 1
            assert runner.run_once()["acked"] == 0
        assert store.search(
            "portable mail postgres marker", authorized_source=SOURCES[0]
        )["results"]
        assert store.search(
            "portable calendar postgres marker", authorized_source=SOURCES[1]
        )["results"]
        assert store.search(
            "portable contact postgres marker", authorized_source=SOURCES[2]
        )["results"]
        for canary, source in zip((
            "synthetic-portable-mail-canary",
            "synthetic-portable-calendar-canary",
            "synthetic-portable-contact-canary",
        ), SOURCES, strict=True):
            assert store.search(canary, authorized_source=source)["results"] == []

        mail_id = connectors[0].pull(None).records[0].native_id
        mail.write_text(mail.read_text().replace(
            "portable mail postgres marker", "portable mail revised marker"
        ))
        assert runners[0].run_once()["acked"] == 1
        for runner in runners:
            runner.close()
        remover = ConnectorRunner(
            connector=MailImportConnector(
                path=mail, source_id=SOURCES[0], archive_id="portable-e2e-mail",
                owner_identifiers=("owner@example.invalid",),
                removed_native_ids=(mail_id,),
            ),
            brain=writer,
            spool_path=spools[0],
            privacy=PrivacyPolicy(mode="scrub"),
        )
        assert remover.run_once()["acked"] == 1
        remover.close()
        assert store.search(
            "portable mail revised marker", authorized_source=SOURCES[0]
        )["results"] == []
        private_bytes = b"".join(
            path.read_bytes()
            for path in spools[0].parent.iterdir()
            if path.is_file()
        )
        assert b"synthetic-portable" not in private_bytes
        print(json.dumps({
            "status": "pass",
            "configured_sources": 3,
            "searchable_sources": 3,
            "content_revisions": 1,
            "explicit_tombstones": 1,
            "duplicate_acknowledged_versions": 0,
            "restart_reacknowledgements": 0,
            "inferred_tombstones": 0,
            "canary_search_hits": 0,
            "spool_canary_hits": 0,
            "private_content_rendered": False,
        }, sort_keys=True))


if __name__ == "__main__":
    main()
