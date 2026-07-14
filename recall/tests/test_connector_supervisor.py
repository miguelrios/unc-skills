from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from client import cli as client_cli
from connectors.sdk import ConnectorContractError, ConnectorRunError
from connectors.supervisor import (
    ConnectorSupervisor,
    ScheduleDefinition,
    ScheduledJob,
    SupervisorContractError,
    SupervisorStore,
    aggregate_supervisor_status,
    preview_supervisor_policy,
)


ROOT = Path(__file__).parent / "connector_supervisor_v1"
CORPUS = ROOT / "corpus.jsonl"
MANIFEST = ROOT / "manifest.json"
KEY_A = "1" * 64
KEY_B = "2" * 64


def schedule(job_key: str = KEY_A, **changes) -> ScheduleDefinition:
    value = {
        "schema_version": 1,
        "job_key": job_key,
        "connector_id": "grep.ai",
        "generation": 1,
        "enabled": True,
        "interval_seconds": 100,
        "jitter_seconds": 10,
        "transient_base_seconds": 5,
        "max_backoff_seconds": 40,
        "lease_seconds": 20,
        "max_rate_limit_seconds": 90,
    }
    value.update(changes)
    return ScheduleDefinition.from_mapping(value)


class FrozenSupervisorContractTest(unittest.TestCase):
    def test_manifest_and_closed_definition_thresholds(self) -> None:
        manifest = json.loads(MANIFEST.read_text())
        rows = [json.loads(line) for line in CORPUS.read_text().splitlines()]
        self.assertEqual(hashlib.sha256(CORPUS.read_bytes()).hexdigest(), manifest["corpus_sha256"])
        valid = accepted = invalid = invalid_accepted = 0
        for row in rows:
            try:
                value = ScheduleDefinition.from_mapping(row["definition"])
            except SupervisorContractError:
                self.assertFalse(row["valid"], row["case"])
                invalid += 1
                continue
            accepted += 1
            valid += int(row["valid"])
            invalid_accepted += int(not row["valid"])
            self.assertEqual(value.to_public(), row["definition"])
        self.assertEqual(accepted / valid, manifest["thresholds"]["valid_acceptance"])
        self.assertEqual(invalid_accepted / invalid, manifest["thresholds"]["invalid_acceptance"])

    def test_definition_is_immutable_closed_and_requires_generation_for_policy_change(self) -> None:
        item = schedule()
        with self.assertRaises((AttributeError, TypeError)):
            item.interval_seconds = 1
        with tempfile.TemporaryDirectory() as directory:
            store = SupervisorStore(Path(directory) / "state.db")
            store.reconcile(item, now=10)
            with self.assertRaisesRegex(SupervisorContractError, "generation_required"):
                store.reconcile(schedule(interval_seconds=101), now=11)
            changed = schedule(generation=2, interval_seconds=101)
            store.reconcile(changed, now=11)
            with self.assertRaisesRegex(SupervisorContractError, "stale_generation"):
                store.reconcile(schedule(generation=1), now=11)
            state = store.snapshot(KEY_A)
            self.assertEqual((state["generation"], state["due_at"], state["failures"]), (2, 11.0, 0))
            store.close()


class SupervisorSchedulingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "private" / "supervisor.db"
        self.store = SupervisorStore(self.path)
        self.addCleanup(self.store.close)
        self.jitter_calls: list[tuple[str, int, int]] = []

        def jitter(job_key: str, failures: int, maximum: int) -> int:
            self.jitter_calls.append((job_key, failures, maximum))
            return maximum

        self.supervisor = ConnectorSupervisor(self.store, jitter=jitter)

    def test_success_cadence_failure_backoff_cap_rate_limit_and_repair(self) -> None:
        calls: list[str] = []
        outcomes = [
            {"status": "committed", "acked": 1},
            ConnectorRunError("connector_unavailable"),
            ConnectorRunError("connector_unavailable"),
            ConnectorRunError("connector_unavailable"),
            {"status": "backoff", "error_code": "connector_rate_limited", "retry_after_seconds": 5000},
            PermissionError("private authority detail must never persist"),
        ]

        def run():
            calls.append("run")
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        item = schedule()
        job = ScheduledJob(item, run)
        first = self.supervisor.tick((job,), now=0)
        self.assertEqual(first["outcomes"], {"success": 1})
        self.assertEqual(self.store.snapshot(KEY_A)["due_at"], 110.0)
        self.assertEqual(self.supervisor.tick((job,), now=109)["ran"], 0)

        expected = ((110, 125.0), (125, 145.0), (145, 175.0))
        for now, due in expected:
            self.assertEqual(self.supervisor.tick((job,), now=now)["outcomes"], {"transient": 1})
            self.assertEqual(self.store.snapshot(KEY_A)["due_at"], due)
        self.assertEqual(self.supervisor.tick((job,), now=175)["outcomes"], {"rate_limited": 1})
        self.assertEqual(self.store.snapshot(KEY_A)["due_at"], 265.0)
        self.assertEqual(self.supervisor.tick((job,), now=265)["outcomes"], {"authority": 1})
        parked = self.store.snapshot(KEY_A)
        self.assertEqual((parked["state"], parked["due_at"]), ("parked", None))
        self.assertNotIn("private authority", json.dumps(parked))
        self.assertEqual(self.supervisor.tick((job,), now=999)["ran"], 0)

        repaired = ScheduledJob(schedule(generation=2), lambda: {"status": "committed"})
        self.assertEqual(self.supervisor.tick((repaired,), now=1000)["outcomes"], {"success": 1})
        final = self.store.snapshot(KEY_A)
        self.assertEqual((final["failures"], final["last_outcome"]), (0, "success"))
        self.assertEqual(len(calls), 6)

    def test_active_lease_excludes_duplicate_and_expired_lease_recovers_after_restart(self) -> None:
        item = schedule(lease_seconds=20)
        self.store.reconcile(item, now=0)
        token = self.store.acquire(item, now=0, lease_token="a" * 64)
        self.assertEqual(token, "a" * 64)
        other = SupervisorStore(self.path)
        self.addCleanup(other.close)
        second = ConnectorSupervisor(other, jitter=lambda *_: 0)
        calls: list[int] = []
        job = ScheduledJob(item, lambda: calls.append(1) or {"status": "committed"})
        self.assertEqual(second.tick((job,), now=19)["ran"], 0)
        self.assertEqual(second.tick((job,), now=20)["outcomes"], {"success": 1})
        self.assertEqual(calls, [1])
        with self.assertRaisesRegex(SupervisorContractError, "lease_lost"):
            self.store.complete(item, "a" * 64, now=21, outcome="success", retry_after=None, jitter=0)

    def test_one_failure_never_blocks_another_due_job_and_unknown_text_is_not_stored(self) -> None:
        jobs = (
            ScheduledJob(schedule(KEY_A), lambda: (_ for _ in ()).throw(RuntimeError("secret payload canary"))),
            ScheduledJob(schedule(KEY_B, connector_id="openai.export-inbox"), lambda: {"status": "committed"}),
        )
        result = self.supervisor.tick(jobs, now=0)
        self.assertEqual((result["ran"], result["outcomes"]), (2, {"success": 1, "transient": 1}))
        self.assertNotIn("secret payload canary", self.path.read_bytes().decode(errors="ignore"))

    def test_contract_failure_parks_and_explicit_wake_unparks(self) -> None:
        job = ScheduledJob(schedule(), lambda: (_ for _ in ()).throw(ConnectorContractError("private cursor")))
        self.assertEqual(self.supervisor.tick((job,), now=0)["outcomes"], {"contract": 1})
        self.assertEqual(self.store.snapshot(KEY_A)["state"], "parked")
        self.supervisor.wake((job,), now=7)
        self.assertEqual(self.store.snapshot(KEY_A)["due_at"], 7.0)

    def test_tick_never_reads_wall_clock_or_sleeps(self) -> None:
        job = ScheduledJob(schedule(), lambda: {"status": "committed"})
        with mock.patch("connectors.supervisor.time.time", side_effect=AssertionError("wall clock")), \
             mock.patch("connectors.supervisor.time.sleep", side_effect=AssertionError("sleep")):
            self.supervisor.tick((job,), now=123)


class SupervisorWaitAndStatusTest(unittest.TestCase):
    def test_run_loop_caps_wait_wakes_and_stops_without_busy_loop(self) -> None:
        class Clock:
            value = 0.0
            def now(self): return self.value

        class Event:
            def __init__(self, clock): self.clock = clock; self.waits = []; self.set = False
            def wait(self, timeout): self.waits.append(timeout); self.clock.value += timeout; return self.set
            def clear(self): self.set = False
            def is_set(self): return self.set

        with tempfile.TemporaryDirectory() as directory:
            store = SupervisorStore(Path(directory) / "state.db")
            supervisor = ConnectorSupervisor(store, jitter=lambda *_: 0)
            clock = Clock(); wake = Event(clock); stop = Event(clock)
            calls: list[int] = []
            job = ScheduledJob(schedule(interval_seconds=1000, jitter_seconds=0), lambda: calls.append(1) or {"status": "committed"})
            cycles = supervisor.run_loop((job,), clock=clock.now, wake_event=wake, stop_event=stop, max_wait_seconds=30, max_cycles=3)
            self.assertEqual((cycles, calls, wake.waits), (3, [1], [30.0, 30.0, 30.0]))
            self.assertTrue(all(value > 0 for value in wake.waits))
            stop.set = True
            self.assertEqual(supervisor.run_loop((job,), clock=clock.now, wake_event=wake, stop_event=stop, max_wait_seconds=30), 0)
            store.close()

    def test_run_loop_wake_interrupts_wait_and_makes_job_due(self) -> None:
        class Clock:
            value = 0.0
            def now(self): return self.value

        class Stop:
            def is_set(self): return False

        class Wake:
            def __init__(self): self.calls = 0
            def wait(self, timeout):
                self.calls += 1
                return self.calls == 1
            def clear(self): return None

        with tempfile.TemporaryDirectory() as directory:
            store = SupervisorStore(Path(directory) / "state.db")
            supervisor = ConnectorSupervisor(store, jitter=lambda *_: 0)
            calls: list[int] = []
            job = ScheduledJob(
                schedule(interval_seconds=100, jitter_seconds=0),
                lambda: calls.append(1) or {"status": "committed"},
            )
            cycles = supervisor.run_loop(
                (job,), clock=Clock().now, wake_event=Wake(), stop_event=Stop(),
                max_wait_seconds=30, max_cycles=2,
            )
            self.assertEqual((cycles, calls), (2, [1, 1]))
            store.close()

    def test_private_state_permissions_symlink_rejection_and_content_free_read_only_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "state" / "supervisor.db"
            store = SupervisorStore(path)
            supervisor = ConnectorSupervisor(store, jitter=lambda *_: 0)
            jobs = (
                ScheduledJob(schedule(KEY_A), lambda: {"status": "committed"}),
                ScheduledJob(schedule(KEY_B, enabled=False, connector_id="openai.export-inbox"), lambda: {"status": "committed"}),
            )
            supervisor.tick(jobs, now=0)
            store.close()
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(path.parent).st_mode & 0o777, 0o700)
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            status = aggregate_supervisor_status(path, now=1)
            after = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(before, after)
            self.assertEqual((status["jobs"], status["enabled"], status["disabled"]), (2, 1, 1))
            rendered = json.dumps(status)
            self.assertNotIn(KEY_A, rendered); self.assertNotIn(KEY_B, rendered)
            self.assertFalse(set(status) & {"job_key", "connector_id", "source_id", "path", "cursor", "command", "error"})

            target = root / "target.db"; target.write_text("x")
            link = root / "linked.db"; link.symlink_to(target)
            with self.assertRaisesRegex(SupervisorContractError, "unsafe_state_file"):
                SupervisorStore(link)

            broken = root / "broken.db"
            broken.symlink_to(root / "missing-target.db")
            with self.assertRaisesRegex(SupervisorContractError, "unsafe_state_file"):
                SupervisorStore(broken)

    def test_preview_is_static_and_zero_io(self) -> None:
        with mock.patch("sqlite3.connect") as connect, \
             mock.patch("pathlib.Path.open") as file_open, \
             mock.patch("urllib.request.urlopen") as network:
            value = preview_supervisor_policy()
        connect.assert_not_called(); file_open.assert_not_called(); network.assert_not_called()
        self.assertEqual(value["credential_reads"], 0)
        self.assertEqual(value["source_reads"], 0)
        self.assertEqual(value["network_requests"], 0)
        self.assertEqual(value["writes"], 0)

    def test_preview_and_status_cli_are_content_free_and_status_is_read_only(self) -> None:
        import io
        with mock.patch("sys.argv", ["recall-brain", "connector-supervisor-preview"]), \
             mock.patch("sys.stdout", io.StringIO()) as output, \
             mock.patch("client.cli.load_file_token") as token, \
             mock.patch("client.cli.load_private_api_key") as source_key, \
             mock.patch("urllib.request.urlopen") as network:
            client_cli.main()
        token.assert_not_called(); source_key.assert_not_called(); network.assert_not_called()
        self.assertEqual(json.loads(output.getvalue())["writes"], 0)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.db"
            store = SupervisorStore(path)
            ConnectorSupervisor(store, jitter=lambda *_: 0).tick((
                ScheduledJob(schedule(), lambda: {"status": "committed"}),
            ), now=0)
            store.close()
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            arguments = [
                "recall-brain", "connector-supervisor-status",
                "--state", str(path), "--now", "1",
            ]
            with mock.patch("sys.argv", arguments), mock.patch("sys.stdout", io.StringIO()) as output:
                client_cli.main()
            after = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(before, after)
            rendered = output.getvalue()
            self.assertNotIn(KEY_A, rendered)
            self.assertNotIn(str(path), rendered)


if __name__ == "__main__":
    unittest.main()
