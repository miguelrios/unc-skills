from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2] / "server"
sys.path.insert(0, str(SERVER))

from recall_server.projectors import advisory_lock_key, canonical_json, partial_lexical_probes, preferred_phrase_probe, project, redact_text, validate_envelope
from recall_server.db import structural_surface_weight


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

    def test_projection_carries_shared_normalized_entities(self) -> None:
        marker = "DEADBEEF-1234-1234-1234-123456789ABC"
        items, _ = project(envelope(content={"role": "user", "text": f"/tmp/trace.json {marker} ConnectTimeout"}), 1)
        self.assertIn({"kind": "file_path", "value": "/tmp/trace.json", "normalized": "/tmp/trace.json"}, items[0]["entities"])
        self.assertIn({"kind": "uuid", "value": marker.lower(), "normalized": marker.lower()}, items[0]["entities"])
        self.assertIn({"kind": "error", "value": "ConnectTimeout", "normalized": "connecttimeout"}, items[0]["entities"])

    def test_redaction_does_not_use_semantic_matching(self) -> None:
        self.assertEqual(redact_text("a harmless discussion about password rotation"), "a harmless discussion about password rotation")

    def test_partial_probes_prefer_structural_anchors_and_are_bounded(self) -> None:
        probes = partial_lexical_probes(
            ["foreign-key", "violation", "check_result", "agent_instance_id"],
            has_time_filter=False,
        )
        self.assertEqual(probes[0], ("agent_instance_id check_result", "pair", 2))
        self.assertIn(("agent_instance_id", "anchor", 2), probes)
        self.assertLessEqual(len(probes), 3)
        self.assertEqual(
            partial_lexical_probes(["greptile", "review", "passes"], has_time_filter=True)[-1],
            ("greptile", "time-anchor", 1),
        )

    def test_compact_parser_phrase_is_preferred_over_the_full_question(self) -> None:
        self.assertEqual(
            preferred_phrase_probe([
                "what was that 504 gateway timeout we hit from a tool call",
                "timeout we hit from a tool", "504 gateway timeout",
            ]),
            "504 gateway timeout",
        )

    def test_structural_command_evidence_outranks_echoed_output(self) -> None:
        self.assertEqual(structural_surface_weight("tool_input"), 2.0)
        self.assertEqual(structural_surface_weight("tool_output"), 1.0)
        self.assertEqual(structural_surface_weight("user"), 1.0)


if __name__ == "__main__":
    unittest.main()
