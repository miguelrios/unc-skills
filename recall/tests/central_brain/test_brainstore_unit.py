from __future__ import annotations

import hashlib
import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2] / "server"
sys.path.insert(0, str(SERVER))

try:
    import psycopg  # noqa: F401
except ModuleNotFoundError:
    psycopg = types.ModuleType("psycopg")
    psycopg_rows = types.ModuleType("psycopg.rows")
    psycopg_rows.dict_row = object()
    psycopg.rows = psycopg_rows
    sys.modules["psycopg"] = psycopg
    sys.modules["psycopg.rows"] = psycopg_rows

from recall_server import SCHEMA_VERSION
from recall_server import cli as server_cli
from recall_server.app import Handler, serve, serve_unix
from recall_server.db import BrainStore
from recall_server.projectors import advisory_lock_key, canonical_json, partial_lexical_probes, phrase_query_spec, preferred_phrase_probe, preferred_phrase_probes, project, redact_text, validate_envelope
from recall_server.ranking import DEFAULT_SEARCH_DEADLINE_MS, evidence_rank_components, retrieval_leg_order, should_run_partial


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


class SchemaMigrationContractTest(unittest.TestCase):
    def test_migration_versions_are_unique_contiguous_and_current(self) -> None:
        migrations = sorted((SERVER / "schema").glob("*.sql"))
        versions = [int(path.name.split("_", 1)[0]) for path in migrations]
        self.assertEqual(versions, list(range(1, SCHEMA_VERSION + 1)))
        for version, path in zip(versions, migrations, strict=True):
            self.assertRegex(
                path.read_text(),
                rf"schema_migrations\(version\) VALUES \({version}\)",
            )


class SourceScopedReadContractTest(unittest.TestCase):
    def test_resolve_filters_by_the_authenticated_collector_source(self) -> None:
        connection = mock.MagicMock()
        connection.execute.return_value.fetchone.return_value = None
        context = mock.MagicMock()
        context.__enter__.return_value = connection
        store = BrainStore("postgresql://synthetic.invalid/recall")
        store.connect = mock.MagicMock(return_value=context)

        self.assertIsNone(
            store.resolve(
                "recall://source-a/item-1?rev=1",
                authorized_source="source-a",
            )
        )
        sql, params = connection.execute.call_args.args
        self.assertIn("event.source_id=%s", sql)
        self.assertEqual(params, ("source-a", "item-1", 1, "source-a", "source-a"))

    def test_http_resolve_passes_the_principal_source_scope(self) -> None:
        handler = object.__new__(Handler)
        handler.path = "/v1/receipts/resolve?receipt=recall%3A%2F%2Fsource-a%2Fitem-1%3Frev%3D1"
        handler.require = mock.MagicMock(return_value={"source_id": "source-a"})
        handler.store = mock.MagicMock()
        handler.store.resolve.return_value = None
        handler.send_json = mock.MagicMock()

        Handler.do_GET(handler)

        handler.store.resolve.assert_called_once_with(
            "recall://source-a/item-1?rev=1", authorized_source="source-a",
        )
        handler.send_json.assert_called_once_with(404, {"error": "not found"})


class HttpBoundaryContractTest(unittest.TestCase):
    def test_malformed_content_length_is_a_closed_client_error(self) -> None:
        handler = object.__new__(Handler)
        handler.headers = {"Content-Length": "not-an-integer"}
        handler.send_json = mock.MagicMock()
        self.assertIsNone(handler.body_length(1024))
        handler.send_json.assert_called_once_with(400, {"error": "invalid body size"})

    def test_unauthenticated_tcp_cannot_bind_beyond_loopback(self) -> None:
        with mock.patch.dict(os.environ, {"RECALL_AUTH_REQUIRED": "0"}):
            with self.assertRaisesRegex(RuntimeError, "authentication"):
                serve("postgresql://synthetic.invalid/recall", "0.0.0.0", 8788)

    def test_unix_server_refuses_to_replace_a_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "recall.sock"
            path.write_text("do not delete")
            with self.assertRaisesRegex(RuntimeError, "non-socket"):
                serve_unix("postgresql://synthetic.invalid/recall", str(path))
            self.assertEqual(path.read_text(), "do not delete")


class AdminCliSafetyTest(unittest.TestCase):
    def test_token_creation_requires_a_private_output_file(self) -> None:
        errors = io.StringIO()
        with mock.patch.object(sys, "argv", [
            "recall-server", "--dsn", "postgresql://synthetic.invalid/db",
            "token-create", "synthetic-collector",
        ]), contextlib.redirect_stderr(errors), self.assertRaises(SystemExit) as raised:
            server_cli.main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--output", errors.getvalue())
        self.assertNotIn("rcl_", errors.getvalue())


class EnvelopeContractTest(unittest.TestCase):
    def test_default_deadline_fits_below_tailnet_slo_with_client_headroom(self) -> None:
        self.assertEqual(DEFAULT_SEARCH_DEADLINE_MS, 300)
        self.assertLess(DEFAULT_SEARCH_DEADLINE_MS, 500)

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

    def test_validation_rejects_ambiguous_identifiers_nonfinite_json_and_bad_tombstones(self) -> None:
        for updates in (
            {"source_id": "bad/source"},
            {"native_id": "ambiguous?rev=7"},
            {"native_id": "ambiguous#item=2"},
            {"occurred_at": "not-a-timestamp"},
            {"extra": "open-schema"},
        ):
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                validate_envelope(envelope(**updates))
        nonfinite = envelope()
        nonfinite["content"] = {"value": float("nan")}
        with self.assertRaisesRegex(ValueError, "finite JSON"):
            validate_envelope(nonfinite)
        with self.assertRaisesRegex(ValueError, "tombstone target"):
            validate_envelope(envelope(
                kind="tombstone", native_id="memory-one",
                content={"target_native_id": "memory-two"},
            ))

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

    def test_projection_redacts_secret_shaped_native_tool_entity(self) -> None:
        secret = "Z" * 40
        value = envelope(
            kind="transcript_record",
            provenance={"harness": "claude"},
            content={
                "type": "assistant", "timestamp": "2026-07-12T20:00:00Z",
                "message": {"content": [{
                    "type": "tool_use", "name": "api_key=" + secret, "input": {"path": "safe.txt"},
                }]},
            },
        )
        items, _ = project(value, 1)
        rendered = json.dumps(items)
        self.assertNotIn(secret, rendered)
        self.assertIn("REDACTED", rendered)

    def test_redaction_does_not_use_semantic_matching(self) -> None:
        self.assertEqual(redact_text("a harmless discussion about password rotation"), "a harmless discussion about password rotation")

    def test_redaction_removes_private_key_blocks_and_generic_key_assignments(self) -> None:
        private_value = "Z" * 50
        private_block = (
            "-----BEGIN " + "PRIVATE KEY-----\n" + ("Q" * 256)
            + "\n-----END " + "PRIVATE KEY-----"
        )
        redacted = redact_text("safe\nkey=" + private_value + "\n" + private_block + "\nend")
        self.assertNotIn(private_value, redacted)
        self.assertNotIn("Q" * 64, redacted)
        self.assertIn("safe", redacted)
        self.assertIn("end", redacted)

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

        self.assertEqual(
            preferred_phrase_probe([
                "the sqlalchemy async greenlet_spawn has not been called error",
                "greenlet_spawn has not been called",
                "sqlalchemy async greenlet_spawn",
                "async greenlet_spawn has",
            ]),
            "greenlet_spawn has not been called",
        )
        self.assertEqual(
            preferred_phrase_probe([
                "where we handled the httpx ConnectTimeout transient dispatch error",
                "ConnectTimeout transient dispatch error",
                "the httpx ConnectTimeout",
                "transient dispatch error",
            ]),
            "ConnectTimeout transient dispatch error",
        )
        self.assertEqual(
            preferred_phrase_probes([
                "where we handled the httpx ConnectTimeout transient dispatch error",
                "ConnectTimeout transient dispatch error",
                "the httpx ConnectTimeout",
                "transient dispatch error",
            ]),
            ["ConnectTimeout transient dispatch error", "transient dispatch error"],
        )
        self.assertEqual(
            preferred_phrase_probe([
                "the foreign-key violation on check_result for agent_instance_id",
                "violation on check_result for agent_instance_id",
                "the foreign-key violation",
            ]),
            "violation on check_result for agent_instance_id",
        )

    def test_identifier_plan_runs_exact_legs_before_any_phrase(self) -> None:
        self.assertEqual(retrieval_leg_order(["api-prod-6fcdc84dd4-mmjpj"]), ("entity", "identifier"))
        self.assertEqual(retrieval_leg_order([]), ("phrase", "entity", "partial", "all"))

    def test_sparse_phrase_candidates_do_not_suppress_structural_fallback(self) -> None:
        self.assertTrue(should_run_partial(candidate_count=1, result_limit=10))
        self.assertFalse(should_run_partial(candidate_count=29, result_limit=10))

    def test_phrase_fallbacks_share_one_bounded_database_leg(self) -> None:
        self.assertEqual(
            phrase_query_spec(["ConnectTimeout transient dispatch error", "transient dispatch error"]),
            ('"ConnectTimeout transient dispatch error" OR "transient dispatch error"', "websearch_to_tsquery"),
        )
        self.assertEqual(phrase_query_spec(["504 gateway timeout"]), ("504 gateway timeout", "phraseto_tsquery"))

    def test_rank_components_keep_identifier_phrase_and_error_evidence_distinct(self) -> None:
        identifier = evidence_rank_components(
            legs={"entity"}, surface="tool_output", lexical_rank=1.0,
            matched_count=1, informative_count=8, has_identifier=True,
            recency_factor=0.5,
        )
        phrase_command = evidence_rank_components(
            legs={"phrase"}, surface="tool_input", lexical_rank=0.2,
            matched_count=4, informative_count=6, has_identifier=False,
            recency_factor=0.5,
        )
        phrase_echo = evidence_rank_components(
            legs={"phrase"}, surface="tool_output", lexical_rank=0.8,
            matched_count=4, informative_count=6, has_identifier=False,
            recency_factor=0.5,
        )
        error_entity = evidence_rank_components(
            legs={"entity"}, surface="tool_input", lexical_rank=1.0,
            matched_count=1, informative_count=6, has_identifier=False,
            recency_factor=1.0,
        )
        self.assertEqual(identifier["evidence_class"], "identifier")
        self.assertGreater(tuple(identifier["rank_key"]), tuple(phrase_command["rank_key"]))
        self.assertGreater(tuple(phrase_command["rank_key"]), tuple(phrase_echo["rank_key"]))
        self.assertGreater(tuple(phrase_echo["rank_key"]), tuple(error_entity["rank_key"]))


if __name__ == "__main__":
    unittest.main()
