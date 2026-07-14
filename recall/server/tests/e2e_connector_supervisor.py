#!/usr/bin/env python3
"""Fresh-PostgreSQL proof for restart-safe, isolated connector supervision."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "recall"))
sys.path.insert(0, str(ROOT / "recall/server"))

from connectors.sdk import ConnectorPage, ConnectorRecord, ConnectorRunner
from connectors.supervisor import (
    ConnectorSupervisor,
    ScheduleDefinition,
    ScheduledJob,
    SupervisorStore,
    aggregate_supervisor_status,
)
from privacy.policy import PrivacyPolicy
from recall_server.db import BrainStore
from recall_server.projectors import canonical_json


SOURCE_A = "grep-ai:synthetic:supervisor-a"
SOURCE_B = "chatgpt:synthetic:supervisor-b"
KEY_A = "1" * 64
KEY_B = "2" * 64


def record(native_id: str, text: str) -> ConnectorRecord:
    return ConnectorRecord.from_mapping({
        "schema_version": 1,
        "native_id": native_id,
        "occurred_at": "2026-07-14T08:00:00Z",
        "content": {"text": text},
        "provenance": {"uri": f"connector://supervisor/{native_id}"},
        "deleted": False,
    })


class OnePage:
    def __init__(self, connector_id: str, source_id: str, value: ConnectorRecord):
        self.connector_id = connector_id
        self.source_id = source_id
        self.value = value
        self.pulls = 0

    def pull(self, cursor):
        self.pulls += 1
        assert cursor is None
        return ConnectorPage(records=(self.value,), next_cursor="acknowledged", has_more=False)


class StoreWriter:
    def __init__(self, store: BrainStore, *, fail_after_commit: bool = False):
        self.store = store
        self.fail_after_commit = fail_after_commit
        self.ingests = 0
        self.replays = 0

    def ingest(self, events):
        self.ingests += 1
        key = "supervisor-e2e-" + hashlib.sha256(canonical_json(events)).hexdigest()
        acknowledgement, replay = self.store.ingest(key, events)
        self.replays += int(replay)
        if self.fail_after_commit:
            self.fail_after_commit = False
            raise OSError("synthetic lost acknowledgement")
        return {**acknowledgement, "replay": replay}


def definition(job_key: str, connector_id: str) -> ScheduleDefinition:
    return ScheduleDefinition.from_mapping({
        "schema_version": 1,
        "job_key": job_key,
        "connector_id": connector_id,
        "generation": 1,
        "enabled": True,
        "interval_seconds": 100,
        "jitter_seconds": 0,
        "transient_base_seconds": 5,
        "max_backoff_seconds": 20,
        "lease_seconds": 30,
        "max_rate_limit_seconds": 60,
    })


def truncate(store: BrainStore) -> None:
    with store.connect() as connection:
        connection.execute(
            "TRUNCATE chunks,items,sessions,projection_watermarks,source_events,"
            "ingest_batches,source_grants,sources,dead_letters,audit_events RESTART IDENTITY CASCADE"
        )


def main() -> None:
    store = BrainStore(os.environ["RECALL_DATABASE_URL"])
    store.migrate()
    truncate(store)
    with tempfile.TemporaryDirectory(prefix="recall-supervisor-e2e-") as temporary:
        root = Path(temporary)
        state_path = root / "supervisor.db"
        connector_a = OnePage(
            "grep.ai", SOURCE_A,
            record("supervised-a", "supervisor alpha restart marker"),
        )
        connector_b = OnePage(
            "openai.export-inbox", SOURCE_B,
            record("supervised-b", "supervisor beta isolation marker"),
        )
        writer_a = StoreWriter(store, fail_after_commit=True)
        writer_b = StoreWriter(store)
        runner_a = ConnectorRunner(
            connector=connector_a, brain=writer_a, spool_path=root / "a.db",
            privacy=PrivacyPolicy(mode="scrub"),
        )
        runner_b = ConnectorRunner(
            connector=connector_b, brain=writer_b, spool_path=root / "b.db",
            privacy=PrivacyPolicy(mode="scrub"),
        )
        item_a = definition(KEY_A, "grep.ai")
        item_b = definition(KEY_B, "openai.export-inbox")
        supervisor_store = SupervisorStore(state_path)
        supervisor = ConnectorSupervisor(supervisor_store, jitter=lambda *_: 0)
        first = supervisor.tick((
            ScheduledJob(item_a, runner_a.run_once),
            ScheduledJob(item_b, runner_b.run_once),
        ), now=0)
        assert first == {
            "schema_version": 1, "configured": 2, "ran": 2,
            "outcomes": {"success": 1, "transient": 1},
        }
        assert connector_a.pulls == 1
        assert connector_b.pulls == 1
        assert store.search("supervisor beta isolation marker", authorized_source=SOURCE_B)["results"]
        supervisor_store.close(); runner_a.close(); runner_b.close()

        recovered_a = ConnectorRunner(
            connector=connector_a, brain=writer_a, spool_path=root / "a.db",
            privacy=PrivacyPolicy(mode="scrub"),
        )
        recovered_b = ConnectorRunner(
            connector=connector_b, brain=writer_b, spool_path=root / "b.db",
            privacy=PrivacyPolicy(mode="scrub"),
        )
        recovered_results = []
        recovered_store = SupervisorStore(state_path)
        recovered = ConnectorSupervisor(recovered_store, jitter=lambda *_: 0)
        second = recovered.tick((
            ScheduledJob(item_a, lambda: recovered_results.append(recovered_a.run_once()) or recovered_results[-1]),
            ScheduledJob(item_b, recovered_b.run_once),
        ), now=5)
        assert second == {
            "schema_version": 1, "configured": 2, "ran": 1,
            "outcomes": {"success": 1},
        }
        assert recovered_results[0]["replayed"] == 1
        assert connector_a.pulls == 1
        assert connector_b.pulls == 1
        assert writer_a.replays == 1
        alpha = store.search("supervisor alpha restart marker", authorized_source=SOURCE_A)["results"]
        beta = store.search("supervisor beta isolation marker", authorized_source=SOURCE_B)["results"]
        assert len(alpha) == len(beta) == 1
        assert store.resolve(alpha[0]["receipt"])
        assert store.resolve(beta[0]["receipt"])
        with store.connect() as connection:
            assert connection.execute("SELECT count(*) AS n FROM source_events").fetchone()["n"] == 2
            assert connection.execute("SELECT count(*) AS n FROM items WHERE deleted_at IS NULL").fetchone()["n"] == 2
        status = aggregate_supervisor_status(state_path, now=6)
        assert status["jobs"] == 2
        assert status["outcomes"]["success"] == 2
        assert KEY_A not in json.dumps(status) and KEY_B not in json.dumps(status)
        recovered_store.close(); recovered_a.close(); recovered_b.close()

        truncate(store)
        with store.connect() as connection:
            live_after_cleanup = connection.execute(
                "SELECT count(*) AS n FROM items WHERE deleted_at IS NULL"
            ).fetchone()["n"]
        for candidate in root.glob("supervisor.db*"):
            candidate.unlink()
        scheduler_residue = len(list(root.glob("supervisor.db*")))
        assert live_after_cleanup == 0
        assert scheduler_residue == 0
        print(json.dumps({
            "status": "pass",
            "scheduled_sources": 2,
            "first_tick_ran": 2,
            "isolated_progress_after_failure": 1,
            "restart_replays": writer_a.replays,
            "source_refetches_on_restart": connector_a.pulls - 1,
            "source_events_before_cleanup": 2,
            "resolved_sources": 2,
            "duplicate_acknowledged_pages": 0,
            "live_after_cleanup": live_after_cleanup,
            "scheduler_residue": scheduler_residue,
            "private_content_rendered": False,
        }, sort_keys=True))


if __name__ == "__main__":
    main()
