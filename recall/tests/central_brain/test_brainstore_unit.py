from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2] / "server"
sys.path.insert(0, str(SERVER))

from recall_server.projectors import advisory_lock_key, canonical_json, project, redact_text, validate_envelope


def envelope(**updates):
    value = {
        "schema_version": 1,
        "source_id": "codex:laptop",
        "native_id": "session-1:turn-1",
        "native_parent_id": "session-1",
        "kind": "message",
        "occurred_at": "2026-07-12T20:00:00Z",
        "observed_at": "2026-07-12T20:00:01Z",
        "principal_id": "owner",
        "visibility": "private",
        "content_type": "application/json",
        "content": {"role": "user", "text": "remember the quartz decision"},
        "provenance": {"harness": "codex"},
    }
    value.update(updates)
    value["content_sha256"] = hashlib.sha256(canonical_json(value["content"])).hexdigest()
    return value


class EnvelopeContractTest(unittest.TestCase):
    def test_advisory_lock_key_is_postgres_text_safe_and_boundary_preserving(self) -> None:
        key = advisory_lock_key("ab", "c")
        self.assertNotIn("\x00", key)
        self.assertNotEqual(key, advisory_lock_key("a", "bc"))

    def test_canonical_hash_ignores_dict_order(self) -> None:
        self.assertEqual(canonical_json({"b": 2, "a": 1}), canonical_json({"a": 1, "b": 2}))

    def test_validation_rejects_mutation_and_unknown_visibility(self) -> None:
        valid = envelope()
        self.assertIs(validate_envelope(valid), valid)
        mutated = {**valid, "content": {"text": "changed"}}
        with self.assertRaisesRegex(ValueError, "content_sha256 mismatch"):
            validate_envelope(mutated)
        with self.assertRaisesRegex(ValueError, "visibility"):
            validate_envelope(envelope(visibility="public"))

    def test_projection_redacts_secret_line_and_preserves_safe_lines(self) -> None:
        value = envelope(content={"role": "user", "text": "safe line\nAuthorization=supersecretvalue123\nlast line"})
        items, _ = project(value, 1)
        self.assertEqual(items[0]["text_redacted"], "safe line\n[REDACTED]\nlast line")
        self.assertNotIn("supersecret", json.dumps(items))

    def test_receipts_are_source_native_revision_and_item_exact(self) -> None:
        items, _ = project(envelope(), 3)
        self.assertEqual(items[0]["receipt"], "recall://codex:laptop/session-1:turn-1?rev=3#item=0")

    def test_redaction_does_not_use_semantic_matching(self) -> None:
        self.assertEqual(redact_text("a harmless discussion about password rotation"), "a harmless discussion about password rotation")


if __name__ == "__main__":
    unittest.main()
