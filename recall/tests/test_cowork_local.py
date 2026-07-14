from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from connectors.cowork_local import (
    CoworkLocalConnector,
    CoworkLocalError,
    project_cowork_record,
)
from connectors.sdk import ConnectorContractError, ConnectorRunError, ConnectorRunner
from privacy.policy import PrivacyPolicy


ROOT = Path(__file__).parent / "cowork_local_v1"
CORPUS = ROOT / "corpus.jsonl"
MANIFEST = ROOT / "manifest.json"


class FrozenCoworkLocalContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = json.loads(MANIFEST.read_text())
        cls.cases = [json.loads(line) for line in CORPUS.read_text().splitlines()]

    def test_manifest_freezes_synthetic_corpus_and_closed_policy(self) -> None:
        self.assertEqual(self.manifest["schema_version"], 1)
        self.assertEqual(
            hashlib.sha256(CORPUS.read_bytes()).hexdigest(),
            self.manifest["corpus_sha256"],
        )
        expected = self.manifest["expected"]
        self.assertEqual(len(self.cases), expected["cases"])
        counts = {label: 0 for label in ("keep", "skip", "error")}
        for case in self.cases:
            counts[case["expected"]] += 1
        self.assertEqual(counts, {
            "keep": expected["kept"],
            "skip": expected["skipped"],
            "error": expected["errors"],
        })
        self.assertEqual(self.manifest["policy"], {
            "allowed_record_types": ["assistant", "user"],
            "allowed_content_blocks": ["text"],
            "deletion": "explicit_receipt_only",
            "excluded_surfaces": [
                "attachments", "audit", "metadata", "reasoning", "system",
                "tool_input", "tool_output",
            ],
        })

    def test_projection_matches_allowlist_and_never_copies_excluded_surfaces(self) -> None:
        kept = []
        errors = 0
        for case in self.cases:
            try:
                projected = project_cowork_record(case["record"])
            except CoworkLocalError:
                errors += 1
                self.assertEqual(case["expected"], "error", case["case_id"])
                continue
            if projected is None:
                self.assertEqual(case["expected"], "skip", case["case_id"])
            else:
                self.assertEqual(case["expected"], "keep", case["case_id"])
                kept.append(projected)
        expected = self.manifest["expected"]
        self.assertEqual(len(kept), expected["kept"])
        self.assertEqual(errors, expected["errors"])
        rendered = json.dumps([record.content for record in kept], sort_keys=True)
        for forbidden in (
            "synthetic private tool payload", "synthetic tool output",
            "Synthetic hidden instruction", "synthetic attachment body",
            "Synthetic internal metadata", "Synthetic initial prompt",
            "Synthetic system prompt", "futureMessageField", "futureField",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_native_identity_is_path_independent_replay_stable_and_parented(self) -> None:
        source = self.cases[1]["record"]
        first = project_cowork_record(source)
        reordered = project_cowork_record(json.loads(json.dumps(source, sort_keys=True)))
        self.assertIsNotNone(first)
        self.assertEqual(first, reordered)
        self.assertEqual(first.native_id, "session-alpha/msg-assistant-001")
        self.assertEqual(first.content["parent_native_id"], "session-alpha/msg-user-001")
        self.assertNotIn("path", json.dumps(first.provenance).lower())

    def test_archive_and_absence_have_no_implicit_tombstone_surface(self) -> None:
        archived = next(case for case in self.cases if case["case_id"] == "excluded-archived-metadata")
        self.assertIsNone(project_cowork_record(archived["record"]))
        kept = [
            project_cowork_record(case["record"])
            for case in self.cases if case["expected"] == "keep"
        ]
        self.assertTrue(all(record is not None and record.deleted is False for record in kept))


class FakeBrain:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.requests: list[list[dict]] = []

    def ingest(self, events: list[dict]) -> dict:
        self.requests.append(json.loads(json.dumps(events)))
        if self.fail:
            raise OSError("synthetic unavailable")
        return {
            "receipts": [
                f"recall://synthetic/{event['native_id']}?rev=1" for event in events
            ]
        }


class LocalCoworkConnectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.root = self.home / "local-agent-mode-sessions"
        self.project = (
            self.root / "account-synthetic" / "workspace-synthetic" / "local_session-alpha"
            / ".claude" / "projects" / "synthetic-project"
        )
        self.project.mkdir(parents=True)
        self.transcript = self.project / "conversation-alpha.jsonl"
        self.spool = self.home / "private-state" / "cowork.db"
        self.cases = [json.loads(line) for line in CORPUS.read_text().splitlines()]

    def records(self, *expected: str) -> list[dict]:
        allowed = set(expected)
        return [case["record"] for case in self.cases if case["expected"] in allowed]

    def write_records(self, records: list[dict], *, trailing_partial: str = "") -> None:
        rendered = "".join(json.dumps(record, sort_keys=True) + "\n" for record in records)
        self.transcript.write_text(rendered + trailing_partial)

    def connector(self, *, page_size: int = 500) -> CoworkLocalConnector:
        return CoworkLocalConnector(
            root=self.root,
            source_id="cowork:mac:synthetic",
            page_size=page_size,
        )

    def runner(self, brain: FakeBrain, *, mode: str = "scrub") -> ConnectorRunner:
        return ConnectorRunner(
            connector=self.connector(),
            brain=brain,
            spool_path=self.spool,
            privacy=PrivacyPolicy(mode=mode),
        )

    def test_exact_project_tree_is_paginated_path_free_and_excludes_ambient_files(self) -> None:
        self.write_records(self.records("keep", "skip"))
        local_root = self.project.parents[2]
        (local_root / "audit.jsonl").write_text(json.dumps(self.records("keep")[0]) + "\n")
        (local_root.with_suffix(".json")).write_text(json.dumps({
            "initialMessage": "synthetic excluded metadata",
            "systemPrompt": "synthetic excluded system prompt",
        }))

        connector = self.connector(page_size=2)
        cursor = None
        records = []
        for _ in range(3):
            page = connector.pull(cursor)
            records.extend(page.records)
            cursor = page.next_cursor
        self.assertEqual(len(records), 5)
        self.assertEqual(len({record.native_id for record in records}), 5)
        self.assertFalse(page.has_more)
        rendered = json.dumps([record.content for record in records])
        self.assertNotIn("excluded metadata", rendered)
        self.assertNotIn("excluded system", rendered)
        self.assertNotIn(str(self.home), json.dumps([record.provenance for record in records]))
        self.assertNotIn(str(self.home), cursor)

    def test_runner_scrubs_before_spool_and_network_and_drop_omits_the_record(self) -> None:
        canary = "synthetic-cowork-key"
        pat_canary = "github_pat_synthetic_cowork_canary"
        secret = json.loads(json.dumps(next(
            case["record"] for case in self.cases
            if case["case_id"] == "eligible-privacy-canaries"
        )))
        secret["message"]["content"] += f" access_token={pat_canary}"
        self.write_records([secret])
        unavailable = FakeBrain(fail=True)
        scrub = self.runner(unavailable, mode="scrub")
        with self.assertRaisesRegex(ConnectorRunError, "brain_unavailable"):
            scrub.run_once()
        self.assertEqual(len(unavailable.requests), 1)
        rendered_request = json.dumps(unavailable.requests)
        self.assertNotIn(canary, rendered_request)
        self.assertNotIn(pat_canary, rendered_request)
        self.assertNotIn("synthetic@example.invalid", rendered_request)
        self.assertNotIn("212-555-0199", rendered_request)
        self.assertNotIn("1 Synthetic Way", rendered_request)
        scrub.close()
        for artifact in self.spool.parent.glob("cowork.db*"):
            self.assertNotIn(canary.encode(), artifact.read_bytes())
            self.assertNotIn(pat_canary.encode(), artifact.read_bytes())

        drop_spool = self.home / "drop-state" / "cowork.db"
        drop_brain = FakeBrain()
        drop = ConnectorRunner(
            connector=self.connector(), brain=drop_brain, spool_path=drop_spool,
            privacy=PrivacyPolicy(mode="drop"),
        )
        result = drop.run_once()
        self.assertEqual(result["dropped"], 1)
        self.assertEqual(result["acked"], 0)
        self.assertEqual(drop_brain.requests, [])
        drop.close()
        for artifact in drop_spool.parent.glob("cowork.db*"):
            self.assertNotIn(canary.encode(), artifact.read_bytes())
            self.assertNotIn(pat_canary.encode(), artifact.read_bytes())

    def test_replay_append_change_and_missing_file_never_duplicate_or_delete(self) -> None:
        initial = self.records("keep")[:2]
        self.write_records(initial)
        brain = FakeBrain()
        runner = self.runner(brain)
        first = runner.run_once()
        self.assertEqual(first["acked"], 2)
        second = runner.run_once()
        self.assertEqual(second["acked"], 0)
        self.assertEqual(second["deduplicated"], 2)

        appended = self.records("keep")[2]
        self.write_records([*initial, appended])
        third = runner.run_once()
        self.assertEqual(third["acked"], 1)
        self.assertEqual(third["deduplicated"], 2)

        changed = json.loads(json.dumps(appended))
        changed["message"]["content"] = "Synthetic changed revision"
        self.write_records([*initial, changed])
        fourth = runner.run_once()
        self.assertEqual(fourth["acked"], 1)
        self.assertEqual(fourth["deduplicated"], 2)

        self.transcript.unlink()
        fifth = runner.run_once()
        self.assertEqual(fifth["acked"], 0)
        all_events = [event for request in brain.requests for event in request]
        self.assertTrue(all(event["kind"] == "connector_record" for event in all_events))
        self.assertFalse(any(event["kind"] == "tombstone" for event in all_events))
        runner.close()

    def test_partial_line_is_deferred_and_malformed_oversized_or_unsafe_files_fail_closed(self) -> None:
        valid = self.records("keep")[0]
        appended = self.records("keep")[1]
        partial = json.dumps(appended)[:-1]
        self.write_records([valid], trailing_partial=partial)
        first = self.connector().pull(None)
        self.assertEqual(len(first.records), 1)
        with self.transcript.open("a") as output:
            output.write("}\n")
        second = self.connector().pull(first.next_cursor)
        self.assertEqual({record.native_id for record in second.records}, {
            "session-alpha/msg-user-001", "session-alpha/msg-assistant-001",
        })

        malformed = next(case["record"] for case in self.cases if case["expected"] == "error")
        self.write_records([valid])
        runner = self.runner(FakeBrain())
        self.assertEqual(runner.run_once()["acked"], 1)
        committed_cursor = runner._cursor()
        self.write_records([malformed])
        with self.assertRaisesRegex(ConnectorContractError, "cowork_local_invalid_record"):
            runner.run_once()
        self.assertEqual(runner._cursor(), committed_cursor)
        self.assertEqual(runner.doctor()["pending_pages"], 0)
        runner.close()

        self.write_records([valid])
        with mock.patch("connectors.cowork_local.MAX_FILE_BYTES", 10):
            with self.assertRaisesRegex(ConnectorContractError, "cowork_local_file_too_large"):
                self.connector().pull(None)

        target = self.home / "outside.jsonl"
        target.write_text(json.dumps(valid) + "\n")
        self.transcript.unlink()
        self.transcript.symlink_to(target)
        with self.assertRaisesRegex(ConnectorContractError, "cowork_local_symlink"):
            self.connector().pull(None)

        self.transcript.unlink()
        self.write_records([valid])
        real_fstat = os.fstat

        def changed_fstat(descriptor: int):
            details = real_fstat(descriptor)
            values = list(details)
            values[1] += 1
            return os.stat_result(values)

        with mock.patch("connectors.cowork_local.os.fstat", side_effect=changed_fstat):
            with self.assertRaisesRegex(ConnectorContractError, "cowork_local_replaced"):
                self.connector().pull(None)


if __name__ == "__main__":
    unittest.main()
