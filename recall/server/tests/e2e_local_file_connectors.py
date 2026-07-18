#!/usr/bin/env python3
"""Fresh-PostgreSQL proof for WhatsApp export and selected-text connectors."""

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

from connectors.local_files import SelectedTextConnector
from connectors.sdk import ConnectorRunner
from connectors.whatsapp_export import WhatsAppExportConnector
from privacy.policy import PrivacyPolicy
from recall_server.db import BrainStore
from recall_server.projectors import canonical_json


WHATSAPP_SOURCE = "synthetic:whatsapp:postgres"
TEXT_SOURCE = "synthetic:selected-text:postgres"


class StoreWriter:
    def __init__(self, store: BrainStore):
        self.store = store

    def ingest(self, events):
        key = "local-file-e2e-" + hashlib.sha256(
            canonical_json(events)
        ).hexdigest()
        acknowledgement, replay = self.store.ingest(key, events)
        return {**acknowledgement, "replay": replay}


def whatsapp(path: Path) -> None:
    path.write_text(
        "[1/2/26, 3:04:05 PM] Synthetic Friend: "
        "whatsapp postgres safe marker\n"
        "[1/2/26, 3:05:05 PM] Synthetic Owner: "
        "api_key=synthetic-whatsapp-e2e-canary\n",
        encoding="utf-8",
    )


def main() -> None:
    store = BrainStore(os.environ["RECALL_DATABASE_URL"])
    store.migrate()
    with store.connect() as connection:
        connection.execute(
            "TRUNCATE chunks,items,sessions,projection_watermarks,source_events,"
            "ingest_batches,source_grants,sources,dead_letters,audit_events "
            "RESTART IDENTITY CASCADE"
        )
    with tempfile.TemporaryDirectory(prefix="recall-local-files-e2e-") as temporary:
        root = Path(temporary)
        export = root / "selected-chat.txt"
        notes = root / "selected-notes"
        notes.mkdir()
        whatsapp(export)
        (notes / "safe.md").write_text(
            "selected text postgres safe marker",
            encoding="utf-8",
        )
        (notes / "private.txt").write_text(
            "api_key=synthetic-selected-text-e2e-canary",
            encoding="utf-8",
        )
        writer = StoreWriter(store)
        whatsapp_spool = root / "state" / "whatsapp.db"
        text_spool = root / "state" / "selected-text.db"

        def whatsapp_runner():
            return ConnectorRunner(
                connector=WhatsAppExportConnector(
                    export=export,
                    source_id=WHATSAPP_SOURCE,
                    conversation_id="synthetic-conversation",
                    owner_names=("Synthetic Owner",),
                    date_order="mdy",
                    timezone_name="UTC",
                ),
                brain=writer,
                spool_path=whatsapp_spool,
                privacy=PrivacyPolicy(mode="scrub"),
            )

        def text_runner():
            return ConnectorRunner(
                connector=SelectedTextConnector(
                    root=notes,
                    source_id=TEXT_SOURCE,
                ),
                brain=writer,
                spool_path=text_spool,
                privacy=PrivacyPolicy(mode="scrub"),
            )

        wa = whatsapp_runner()
        text = text_runner()
        assert wa.run_once()["acked"] == 2
        assert text.run_once()["acked"] == 2
        assert wa.run_once()["acked"] == 0
        assert text.run_once()["acked"] == 0
        assert store.search(
            "whatsapp postgres safe marker",
            authorized_source=WHATSAPP_SOURCE,
        )["results"]
        assert store.search(
            "selected text postgres safe marker",
            authorized_source=TEXT_SOURCE,
        )["results"]
        assert store.search(
            "synthetic-whatsapp-e2e-canary",
            authorized_source=WHATSAPP_SOURCE,
        )["results"] == []
        assert store.search(
            "synthetic-selected-text-e2e-canary",
            authorized_source=TEXT_SOURCE,
        )["results"] == []

        export.write_text(
            "[1/2/26, 3:04:05 PM] Synthetic Friend: "
            "whatsapp postgres revised marker\n"
            "[1/2/26, 3:05:05 PM] Synthetic Owner: "
            "api_key=synthetic-whatsapp-e2e-canary\n",
            encoding="utf-8",
        )
        (notes / "safe.md").write_text(
            "selected text postgres revised marker",
            encoding="utf-8",
        )
        assert wa.run_once()["acked"] == 1
        assert text.run_once()["acked"] == 1
        wa.close()
        text.close()

        restarted_wa = whatsapp_runner()
        restarted_text = text_runner()
        assert restarted_wa.run_once()["acked"] == 0
        assert restarted_text.run_once()["acked"] == 0
        restarted_wa.close()
        restarted_text.close()
        private_bytes = b"".join(
            path.read_bytes()
            for path in (whatsapp_spool.parent).iterdir()
            if path.is_file()
        )
        assert b"synthetic-whatsapp-e2e-canary" not in private_bytes
        assert b"synthetic-selected-text-e2e-canary" not in private_bytes
        print(json.dumps({
            "status": "pass",
            "configured_sources": 2,
            "searchable_sources": 2,
            "content_revisions": 2,
            "duplicate_acknowledged_versions": 0,
            "restart_reacknowledgements": 0,
            "inferred_tombstones": 0,
            "canary_search_hits": 0,
            "spool_canary_hits": 0,
            "linked_device_credentials": 0,
            "private_content_rendered": False,
        }, sort_keys=True))


if __name__ == "__main__":
    main()
