from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from connectors.cowork_local import CoworkLocalError, project_cowork_record


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


if __name__ == "__main__":
    unittest.main()
