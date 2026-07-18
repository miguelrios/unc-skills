from __future__ import annotations

import hashlib
import contextlib
import inspect
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
from recall_server.app import Handler, serve, serve_unix, validate_http_profile
from recall_server.capture import build_capture_event
from recall_server.db import (
    BrainStore,
    related_candidate_limit,
    semantic_candidate_limit,
)
from recall_server.federation import SOURCE_FAMILIES, SourceProfile
from recall_server.projectors import advisory_lock_key, canonical_json, effective_session_id, partial_lexical_probes, phrase_query_spec, preferred_phrase_probe, preferred_phrase_probes, project, redact_text, validate_envelope
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

    def test_connector_v2_source_families_are_host_owned_and_migrated(self) -> None:
        added = {
            "communications", "schedule", "contacts", "social", "documents",
            "work_activity", "local_activity", "personal_media",
        }
        self.assertTrue(added.issubset(SOURCE_FAMILIES))
        for family in added:
            profile = SourceProfile.from_mapping({
                "source_id": "synthetic:source:v2", "family": family,
                "quality": "standard", "freshness_half_life_days": 30,
            })
            self.assertEqual(profile.family, family)
        migration = SERVER / "schema" / "012_source_profile_families.sql"
        self.assertTrue(migration.is_file())
        rendered = migration.read_text()
        self.assertTrue(all(f"'{family}'" in rendered for family in added))

    def test_source_scoped_backfill_has_a_live_item_id_index(self) -> None:
        migration = SERVER / "schema" / "013_source_backfill_index.sql"
        rendered = " ".join(migration.read_text().split()).casefold()
        self.assertIn("on items(source_id, id)", rendered)
        self.assertIn(
            "where deleted_at is null and btrim(text_redacted) <> ''",
            rendered,
        )

    def test_global_embedding_backfill_has_a_runtime_scoped_watermark(self) -> None:
        migration = SERVER / "schema" / "014_embedding_projection_watermark.sql"
        rendered = " ".join(migration.read_text().split()).casefold()
        self.assertIn(
            "runtime_fingerprint char(64) primary key",
            rendered,
        )
        self.assertIn(
            "last_item_id bigint not null default 0",
            rendered,
        )

        implementation = inspect.getsource(BrainStore.embed_pending)
        self.assertIn("use_watermark = source_id is None and surface is None", implementation)
        self.assertIn("WHERE item.id>%s", implementation)
        self.assertIn("WHERE id>%s", implementation)
        self.assertIn("SELECT min(item_id) AS item_id", implementation)
        self.assertIn("WHERE runtime_fingerprint<>%s", implementation)
        self.assertIn("SET last_item_id=LEAST(last_item_id,%s)", implementation)
        self.assertIn("GREATEST(last_item_id,%s)", implementation)
        self.assertLess(
            implementation.index("cursor.executemany("),
            implementation.rindex("GREATEST(last_item_id,%s)"),
        )

    def test_remote_principal_credentials_are_migrated(self) -> None:
        migration = SERVER / "schema" / "015_principal_credentials.sql"
        rendered = " ".join(migration.read_text().split()).casefold()
        self.assertIn(
            "add column if not exists principal_id text",
            rendered,
        )
        self.assertIn(
            "source_grants(principal_id, permission, source_id)",
            rendered,
        )

    def test_capture_origin_is_host_owned_credential_state(self) -> None:
        migration = SERVER / "schema" / "016_capture_credentials.sql"
        rendered = " ".join(migration.read_text().split()).casefold()
        self.assertIn(
            "add column if not exists capture_origin text",
            rendered,
        )

    def test_managed_upgrade_documents_split_role_grant_refresh(self) -> None:
        guide = " ".join(
            (SERVER / "deploy" / "README.md").read_text().split()
        ).casefold()
        self.assertIn(f"schema migrations 1 through {SCHEMA_VERSION}", guide)
        self.assertIn("refresh runtime grants after every migration", guide)
        self.assertIn("on all tables in schema public", guide)
        self.assertIn("on all sequences in schema public", guide)


class EmbeddingBackfillWatermarkTest(unittest.TestCase):
    def test_global_backfill_advances_cursor_only_after_embedding_write(self) -> None:
        class Runtime:
            model = "voyage-4-lite"
            fingerprint = "a" * 64
            dimensions = 512

            @staticmethod
            def embed_documents(_texts):
                return [[0.0] * 512]

        class Result:
            def __init__(self, *, one=None, all_rows=None):
                self.one = one
                self.all_rows = all_rows

            def fetchone(self):
                return self.one

            def fetchall(self):
                return self.all_rows

        class Cursor:
            def __init__(self, connection):
                self.connection = connection

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def executemany(self, _sql, values):
                self.connection.embedded = True
                self.connection.embedding_values = values

            def execute(self, sql, params):
                self.connection.cursor_updates.append((sql, params))
                if "embedding_projection_watermarks" in sql:
                    self.connection.watermark_advanced_after_write = (
                        self.connection.embedded
                    )

        class Connection:
            def __init__(self):
                self.embedded = False
                self.embedding_values = []
                self.cursor_updates = []
                self.watermark_advanced_after_write = False
                self.selection_params = None

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def transaction(self):
                return contextlib.nullcontext()

            def cursor(self):
                return Cursor(self)

            def execute(self, sql, params=None):
                normalized = " ".join(sql.split())
                if "pg_try_advisory_lock(" in normalized:
                    return Result(one={"value": True})
                if normalized.startswith(
                    "INSERT INTO embedding_projection_watermarks"
                ):
                    return Result()
                if normalized.startswith("SELECT last_item_id"):
                    return Result(one={"last_item_id": 7})
                if normalized.startswith("SELECT min(item_id) AS item_id"):
                    return Result(one={"item_id": None})
                if normalized.startswith("SELECT item.id") and "item.id>%s" in normalized:
                    self.selection_params = params
                    return Result(
                        all_rows=[
                            {
                                "id": 8,
                                "source_id": "synthetic:source",
                                "text_redacted": "synthetic safe text",
                                "projector_version": 1,
                            }
                        ]
                    )
                if "pg_advisory_unlock(" in normalized:
                    return Result(one={"value": True})
                raise AssertionError(f"unexpected SQL: {normalized}")

        connection = Connection()
        store = BrainStore("postgresql://synthetic.invalid/db", semantic_runtime=Runtime())
        with mock.patch.object(store, "connect", return_value=connection):
            result = store.embed_pending(batch_size=1, max_batches=1)

        self.assertEqual(result["processed"], 1)
        self.assertEqual(connection.selection_params[0], 7)
        self.assertEqual(connection.embedding_values[0][0], 8)
        self.assertTrue(connection.watermark_advanced_after_write)
        watermark_update = next(
            params
            for sql, params in connection.cursor_updates
            if "embedding_projection_watermarks" in sql
        )
        self.assertEqual(watermark_update, (8, Runtime.fingerprint))


class TypedConnectorProjectionTest(unittest.TestCase):
    def test_v2_communication_projects_clean_conversation_evidence(self) -> None:
        value = envelope(
            kind="connector_record", native_id="message-1", native_parent_id="thread-1",
            content={
                "kind": "communication_message.v1", "conversation_id": "thread-1",
                "message_id": "message-1", "direction": "inbound",
                "subject": "Synthetic subject", "text": "Synthetic body",
            },
            provenance={"connector_id": "google.gmail", "connector_schema_version": 2},
        )
        validate_envelope(value)
        items, metadata = project(value, 1)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["surface"], "communication_message.v1")
        self.assertEqual(items[0]["role"], "inbound")
        self.assertEqual(items[0]["text_redacted"], "Synthetic subject\nSynthetic body")
        self.assertEqual(metadata["record_kind"], "communication_message.v1")

    def test_v2_connector_record_rejects_unknown_or_mismatched_kind(self) -> None:
        for content in (
            {"kind": "runtime_plugin.v1", "text": "synthetic"},
            {"text": "synthetic"},
        ):
            value = envelope(
                kind="connector_record", content=content,
                provenance={"connector_id": "synthetic.pull", "connector_schema_version": 2},
            )
            with self.subTest(content=content), self.assertRaises(ValueError):
                validate_envelope(value)

    def test_v2_connector_record_rejects_invalid_field_value(self) -> None:
        value = envelope(
            kind="connector_record",
            content={
                "kind": "communication_message.v1", "conversation_id": "thread-1",
                "message_id": "message-1", "direction": "sideways", "text": "synthetic",
            },
            provenance={"connector_id": "synthetic.pull", "connector_schema_version": 2},
        )
        with self.assertRaisesRegex(ValueError, "invalid typed connector record"):
            validate_envelope(value)


class SourceScopedReadContractTest(unittest.TestCase):
    def test_family_and_alias_resolve_once_to_an_intersected_source_set(self) -> None:
        connection = mock.MagicMock()

        def execute(sql, _params):
            result = mock.MagicMock()
            if "source_profiles" in sql:
                result.fetchall.return_value = [
                    {"source_id": "source-a"}, {"source_id": "source-b"},
                ]
            else:
                result.fetchone.return_value = {"source_id": "source-b"}
            return result

        connection.execute.side_effect = execute
        self.assertEqual(
            BrainStore._resolve_routed_source_ids(connection, {
                "source_family": "coding_history", "source_alias": "cowork",
            }),
            ["source-b"],
        )
        self.assertEqual(connection.execute.call_count, 2)

    def test_requested_source_filters_intersect_with_authorized_scope(self) -> None:
        where, params = BrainStore._read_filters(
            {"source_id": "source-b", "source_family": "coding_history", "source_alias": "cowork"},
            authorized_source="source-a",
            routed_source_ids=["source-b", "source-c"],
        )
        self.assertEqual(where.count("i.source_id = %s"), 2)
        self.assertNotIn("sp.family", where)
        self.assertNotIn("SELECT source_id FROM source_aliases", where)
        self.assertIn("i.source_id = ANY(%s)", where)
        self.assertEqual(params, ["source-a", "source-b", ["source-b", "source-c"]])

    def test_authorized_source_set_is_closed_even_when_empty(self) -> None:
        where, params = BrainStore._read_filters(
            {},
            authorized_source=[],
        )
        self.assertIn("i.source_id = ANY(%s)", where)
        self.assertEqual(params, [[]])

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
    def test_readiness_executes_one_constant_query(self) -> None:
        connection = mock.MagicMock()
        connection.execute.return_value.fetchone.return_value = {"ready": 1}
        context = mock.MagicMock()
        context.__enter__.return_value = connection
        store = BrainStore("postgresql://synthetic.invalid/recall")
        store.connect = mock.MagicMock(return_value=context)

        self.assertEqual(store.readiness(), {"status": "ready"})
        connection.execute.assert_called_once_with("SELECT 1 AS ready")

    def test_readyz_uses_constant_time_database_probe_not_full_metrics(self) -> None:
        handler = object.__new__(Handler)
        handler.path = "/readyz"
        handler.store = mock.MagicMock()
        handler.store.readiness.return_value = {"status": "ready"}
        handler.send_json = mock.MagicMock()

        Handler.do_GET(handler)

        handler.store.readiness.assert_called_once_with()
        handler.store.service_metrics.assert_not_called()
        handler.send_json.assert_called_once_with(200, {"status": "ready"})

    def test_operational_health_uses_one_bounded_projection_probe(self) -> None:
        connection = mock.MagicMock()
        connection.execute.return_value.fetchone.return_value = {"projection_lag": 0}
        context = mock.MagicMock()
        context.__enter__.return_value = connection
        store = BrainStore("postgresql://synthetic.invalid/recall")
        store.connect = mock.MagicMock(return_value=context)

        self.assertEqual(
            store.operational_health(),
            {"status": "ok", "projection_lag": 0},
        )
        connection.execute.assert_called_once()
        sql = connection.execute.call_args.args[0]
        self.assertIn("ORDER BY id DESC LIMIT 1", sql)
        self.assertNotIn("count(", sql.casefold())


    def test_remote_doctor_uses_bounded_health_not_full_metrics(self) -> None:
        handler = object.__new__(Handler)
        handler.path = "/v1/doctor"
        handler.require = mock.MagicMock(return_value={"source_id": "source-a"})
        handler.store = mock.MagicMock()
        handler.store.operational_health.return_value = {
            "status": "ok",
            "projection_lag": 0,
        }
        handler.send_json = mock.MagicMock()

        Handler.do_GET(handler)

        handler.store.operational_health.assert_called_once_with()
        handler.store.doctor.assert_not_called()
        handler.store.service_metrics.assert_not_called()
        handler.send_json.assert_called_once_with(
            200,
            {"status": "ok", "projection_lag": 0},
        )

    def test_search_rejects_oversized_query_before_database_or_model_io(self) -> None:
        with self.assertRaisesRegex(ValueError, "too large"):
            BrainStore("postgresql://unused").search("x" * 8193)

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

    def test_public_mcp_profile_requires_bearer_auth_and_forbids_proxy_headers(
        self,
    ) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "RECALL_HTTP_PROFILE": "public-mcp",
                "RECALL_AUTH_REQUIRED": "0",
                "RECALL_TRUST_TAILSCALE_HEADERS": "0",
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "requires authentication"):
                serve("postgresql://synthetic.invalid/recall")
        with mock.patch.dict(
            os.environ,
            {
                "RECALL_HTTP_PROFILE": "public-mcp",
                "RECALL_AUTH_REQUIRED": "1",
                "RECALL_TRUST_TAILSCALE_HEADERS": "1",
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "forbids trusted"):
                serve("postgresql://synthetic.invalid/recall")
        with mock.patch.dict(
            os.environ,
            {"RECALL_HTTP_PROFILE": "public-mpc"},
        ):
            with self.assertRaisesRegex(RuntimeError, "unsupported HTTP profile"):
                validate_http_profile()

    def test_unix_server_refuses_to_replace_a_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "recall.sock"
            path.write_text("do not delete")
            with self.assertRaisesRegex(RuntimeError, "non-socket"):
                serve_unix("postgresql://synthetic.invalid/recall", str(path))
            self.assertEqual(path.read_text(), "do not delete")


class IngestTransactionContractTest(unittest.TestCase):
    def test_batch_projection_advances_the_shared_watermark_once(self) -> None:
        store = BrainStore("postgresql://synthetic.invalid/recall")
        store._project_one = mock.MagicMock()
        store._advance_projector = mock.MagicMock()
        connection = mock.MagicMock()
        events = [
            (41, envelope(native_id="turn-1"), 1),
            (43, envelope(native_id="turn-2"), 1),
            (42, envelope(native_id="turn-3"), 1),
        ]

        store._project_batch(connection, events)

        self.assertEqual(
            store._project_one.call_args_list,
            [
                mock.call(connection, 41, events[0][1], 1),
                mock.call(connection, 43, events[1][1], 1),
                mock.call(connection, 42, events[2][1], 1),
            ],
        )
        store._advance_projector.assert_called_once_with(connection, 43)


class DeliberateCaptureContractTest(unittest.TestCase):
    def test_public_capture_limit_fits_the_mcp_transport_budget(self) -> None:
        principal = {
            "source_id": "synthetic:capture",
            "principal_id": "synthetic-owner",
            "capture_origin": "synthetic-agent",
        }
        base = {
            "schema_version": 1,
            "title": "Synthetic bounded capture",
            "occurred_at": "2026-07-18T02:00:00Z",
            "tags": ["synthetic"],
            "provenance": {"uri": "manual://synthetic"},
        }
        for body in ("x" * 32_000, "😀" * 32_000, "\x00" * 32_000):
            with self.subTest(kind=body[:1].encode().hex()):
                event, _privacy = build_capture_event(
                    {**base, "body": body},
                    principal,
                )
                request_body = json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": "recall_capture",
                            "arguments": {**base, "body": body},
                        },
                    },
                    ensure_ascii=False,
                ).encode()
                self.assertLessEqual(len(body), 32_000)
                self.assertLessEqual(len(body.encode()), 128 * 1024)
                self.assertLess(len(request_body), 256 * 1024)
                self.assertEqual(event["content"]["body"], body)

        for body in ("x" * 32_001, "😀" * 32_001):
            with self.assertRaisesRegex(ValueError, "capture body"):
                build_capture_event({**base, "body": body}, principal)


class SemanticRetrievalConfigurationTest(unittest.TestCase):
    def test_similarity_floor_is_explicit_validated_deployment_config(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"RECALL_SEMANTIC_MINIMUM_SIMILARITY": "0.25"},
        ):
            store = BrainStore("postgresql://synthetic.invalid/recall")
        self.assertEqual(store.semantic_minimum_similarity, 0.25)
        with self.assertRaisesRegex(ValueError, "semantic minimum similarity"):
            BrainStore(
                "postgresql://synthetic.invalid/recall",
                semantic_minimum_similarity=1.01,
            )

    def test_dense_candidate_pool_is_bounded_and_keeps_anchor_headroom(self) -> None:
        self.assertEqual(semantic_candidate_limit(1), 20)
        self.assertEqual(semantic_candidate_limit(5), 20)
        self.assertEqual(semantic_candidate_limit(10), 40)
        self.assertEqual(semantic_candidate_limit(50), 100)


class RelatedRetrievalContractTest(unittest.TestCase):
    def test_fast_candidate_pool_is_bounded_with_filter_headroom(self) -> None:
        self.assertEqual(related_candidate_limit(1), 100)
        self.assertEqual(related_candidate_limit(5), 100)
        self.assertEqual(related_candidate_limit(10), 200)
        self.assertEqual(related_candidate_limit(20), 400)

    def test_fast_query_uses_indexable_evidence_join_and_candidate_cap(self) -> None:
        connection = mock.MagicMock()
        connection.execute.return_value.fetchall.return_value = [{
            "source_id": "synthetic:source",
            "native_id": "session-1",
            "metadata": {"cwd": "/workspace/recall", "branch": "main"},
            "ended_at": None,
            "path": "/workspace/recall/session.jsonl",
            "receipt": "recall://synthetic/session?rev=1#item=0",
            "overlap": 2,
        }]
        store = BrainStore("postgresql://synthetic.invalid/recall")
        store.connect = mock.MagicMock(
            return_value=contextlib.nullcontext(connection)
        )

        result = store.related(
            cwd="/workspace/recall",
            branch="main",
            limit=3,
            mains_only=True,
            fast=True,
            authorized_source=["synthetic:source"],
        )

        sql, params = connection.execute.call_args.args
        normalized = " ".join(sql.split())
        self.assertIn("WITH candidates AS MATERIALIZED", normalized)
        self.assertIn("JOIN source_events event ON event.id=i.event_id", normalized)
        self.assertNotIn("COALESCE(se.native_parent_id,se.native_id)", normalized)
        self.assertIn("LIMIT %s", normalized)
        self.assertEqual(params[-2:], [100, 3])
        self.assertEqual(result["results"][0]["overlap"], 2)


class SemanticRetrievalContractTest(unittest.TestCase):
    def test_similarity_threshold_is_applied_after_bounded_hnsw_retrieval(self) -> None:
        runtime = mock.Mock(
            model="voyage-4",
            fingerprint="voyage:synthetic",
            dimensions=512,
        )
        store = BrainStore(
            "postgresql://synthetic.invalid/recall",
            semantic_runtime=runtime,
        )
        cursor = mock.MagicMock()
        cursor.fetchall.return_value = [
            {"id": 1, "lexical_rank": 0.81},
            {"id": 2, "lexical_rank": 0.22},
        ]
        store._execute_bounded = mock.MagicMock(return_value=cursor)
        connection = mock.MagicMock()

        rows = store._semantic_leg(
            connection,
            [0.1] * 512,
            {},
            limit=5,
            minimum_similarity=0.35,
        )

        _connection, sql, values, _deadline = (
            store._execute_bounded.call_args.args
        )
        self.assertNotIn(
            "AND 1-(embedding.embedding <=> %s::halfvec) >=",
            sql,
        )
        self.assertNotIn(0.35, values)
        self.assertEqual(
            rows,
            [{"id": 1, "lexical_rank": 0.81, "leg": "semantic", "tier": 2}],
        )


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

    def test_write_credentials_must_be_bound_to_one_source(self) -> None:
        store = BrainStore("postgresql://synthetic.invalid/recall")
        store.connect = mock.MagicMock()
        with self.assertRaisesRegex(ValueError, "write credential requires a source"):
            store.create_collector_token(
                "synthetic-unbound-writer",
                None,
                ["read", "write"],
                principal_id="synthetic-owner",
            )
        store.connect.assert_not_called()


class EnvelopeContractTest(unittest.TestCase):
    def test_cowork_session_fallback_repairs_legacy_parentless_envelopes(self) -> None:
        legacy = envelope(
            source_id="cowork:mac:synthetic",
            native_id="session-alpha/message-one",
            native_parent_id=None,
            kind="connector_record",
            content={"session_id": "session-alpha", "role": "user", "text": "safe"},
            provenance={"connector_id": "anthropic.cowork-local"},
        )
        self.assertEqual(effective_session_id(legacy), "session-alpha")
        legacy["native_parent_id"] = legacy["native_id"]
        self.assertEqual(effective_session_id(legacy), "session-alpha")
        self.assertEqual(effective_session_id(envelope(native_parent_id=None)), "session-1:turn-1")

    def test_default_deadline_fits_below_tailnet_slo_with_client_headroom(self) -> None:
        self.assertEqual(DEFAULT_SEARCH_DEADLINE_MS, 300)
        self.assertLess(DEFAULT_SEARCH_DEADLINE_MS, 500)

    def test_large_remote_corpora_may_select_a_bounded_five_second_deadline(self) -> None:
        self.assertEqual(
            BrainStore(
                "postgresql://synthetic.invalid/recall",
                search_deadline_ms=5000,
            ).search_deadline_ms,
            5000,
        )
        with self.assertRaisesRegex(ValueError, "between 10 and 5000"):
            BrainStore(
                "postgresql://synthetic.invalid/recall",
                search_deadline_ms=5001,
            )

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

    def test_redaction_is_idempotent_after_nested_secret_markers(self) -> None:
        secret = "SyntheticSecret" + "9" * 40
        value = (
            "safe prefix\n"
            "access_token:\n"
            f", Authorization={secret}\n"
            "safe suffix"
        )

        once = redact_text(value)

        self.assertNotIn(secret, once)
        self.assertEqual(redact_text(once), once)

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
        self.assertEqual(
            retrieval_leg_order([]),
            ("exact-question", "semantic", "phrase", "entity", "partial", "all"),
        )

    def test_sentence_punctuation_is_not_part_of_an_identifier(self) -> None:
        engine = __import__("recall_server.projectors", fromlist=["legacy_engine"]).legacy_engine()
        terms = engine.informative_terms("Gather synthesis-marker-01.")
        self.assertIn("synthesis-marker-01", terms)
        self.assertNotIn("synthesis-marker-01.", terms)

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
        answer = evidence_rank_components(
            legs={"answer"}, surface="message", lexical_rank=0.2,
            matched_count=0, informative_count=6, has_identifier=False,
            recency_factor=0.5,
        )
        self.assertEqual(identifier["evidence_class"], "identifier")
        self.assertEqual(answer["evidence_class"], "answer")
        self.assertGreater(tuple(identifier["rank_key"]), tuple(answer["rank_key"]))
        self.assertGreater(tuple(answer["rank_key"]), tuple(phrase_command["rank_key"]))
        self.assertGreater(tuple(phrase_command["rank_key"]), tuple(phrase_echo["rank_key"]))
        self.assertGreater(tuple(phrase_echo["rank_key"]), tuple(error_entity["rank_key"]))
        semantic = evidence_rank_components(
            legs={"semantic", "rewrite"}, surface="assistant", lexical_rank=0.5,
            matched_count=0, informative_count=6, has_identifier=False,
            recency_factor=0.5, fusion_score=2 / 61,
        )
        self.assertEqual(semantic["evidence_class"], "semantic")
        self.assertGreater(semantic["fusion_score"], 0)


if __name__ == "__main__":
    unittest.main()
