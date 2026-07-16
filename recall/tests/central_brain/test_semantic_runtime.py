from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SERVER = Path(__file__).resolve().parents[2] / "server"
sys.path.insert(0, str(SERVER))

from recall_server.semantic import (  # noqa: E402
    DEFAULT_EMBEDDING_REVISION,
    DOCUMENT_CLIP_MARKER,
    MAX_DOCUMENT_CHARS,
    QUERY_INSTRUCTION,
    SearchPlan,
    SemanticRuntime,
    _RejectRedirect,
)


class SemanticRuntimeContractTest(unittest.TestCase):
    def runtime(self, key_file: str | None = None) -> SemanticRuntime:
        return SemanticRuntime(
            embedding_url="http://127.0.0.1:8081",
            model="synthetic-embedding",
            revision=DEFAULT_EMBEDDING_REVISION,
            dimensions=512,
            planner_url="https://router.example" if key_file else None,
            planner_approved_url="https://router.example" if key_file else None,
            planner_model="synthetic-planner" if key_file else None,
            planner_key_file=key_file,
            planner_samples=1,
        )

    def test_embedding_endpoint_is_local_only(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            SemanticRuntime(
                embedding_url="https://provider.example", model="unsafe",
                revision=DEFAULT_EMBEDDING_REVISION, dimensions=512,
            )

    def test_planner_must_match_the_approved_router(self) -> None:
        with self.assertRaisesRegex(ValueError, "approved router"):
            SemanticRuntime(
                embedding_url="http://127.0.0.1:8081",
                model="synthetic-embedding",
                revision=DEFAULT_EMBEDDING_REVISION,
                dimensions=512,
                planner_url="https://provider.example",
                planner_approved_url="https://router.example",
                planner_model="synthetic-planner",
                planner_key_file="/tmp/synthetic.key",
            )

    def test_transport_rejects_redirects(self) -> None:
        runtime = self.runtime()
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b'{}'
        opener = mock.MagicMock()
        opener.open.return_value = response
        with mock.patch("recall_server.semantic.urllib.request.build_opener", return_value=opener) as build:
            self.assertEqual(runtime._post("http://127.0.0.1:8081/embed", {}), {})
        self.assertIsInstance(build.call_args.args[0], _RejectRedirect)

    def test_transport_bounds_response_bytes(self) -> None:
        response = mock.MagicMock()
        response.read.return_value = b"x" * (8 * 1024 * 1024 + 1)
        with self.assertRaisesRegex(ValueError, "too large"):
            SemanticRuntime._read_json(response)

    def test_embedding_batch_and_query_instruction_are_validated(self) -> None:
        runtime = self.runtime()
        runtime._embedding_identity_checked = True
        vector = [0.0] * 512
        with mock.patch.object(runtime, "_post", return_value=[vector]) as post:
            self.assertEqual(runtime.embed_query("find the rollout"), vector)
        self.assertTrue(post.call_args.args[1]["inputs"][0].startswith(QUERY_INSTRUCTION))
        with mock.patch.object(runtime, "_post", return_value=[[0.0] * 8]):
            with self.assertRaisesRegex(ValueError, "dimensions"):
                runtime.embed_documents(["bad"])

    def test_document_batches_are_bounded_and_fingerprinted(self) -> None:
        runtime = SemanticRuntime(
            embedding_url="http://127.0.0.1:8081",
            model="synthetic-embedding",
            revision=DEFAULT_EMBEDDING_REVISION,
            dimensions=512,
            embedding_batch_size=2,
        )
        runtime._embedding_identity_checked = True
        vector = [0.0] * 512
        with mock.patch.object(runtime, "_post", side_effect=[
            [vector, vector], [vector, vector], [vector],
        ]) as post:
            self.assertEqual(len(runtime.embed_documents(["a", "b", "c", "d", "e"])), 5)
        self.assertEqual([len(call.args[1]["inputs"]) for call in post.call_args_list], [2, 2, 1])
        self.assertRegex(runtime.fingerprint, r"^[0-9a-f]{64}$")

    def test_oversized_documents_keep_a_bounded_head_and_tail(self) -> None:
        runtime = self.runtime()
        runtime._embedding_identity_checked = True
        value = "H" * 5000 + "T" * 5000
        vector = [0.0] * 512
        with mock.patch.object(runtime, "_post", return_value=[vector]) as post:
            runtime.embed_documents([value])
        projected = post.call_args.args[1]["inputs"][0]
        self.assertEqual(len(projected), MAX_DOCUMENT_CHARS)
        self.assertIn(DOCUMENT_CLIP_MARKER, projected)
        self.assertTrue(projected.startswith("H"))
        self.assertTrue(projected.endswith("T"))

    def test_embedding_revision_is_verified_before_content_leaves(self) -> None:
        runtime = self.runtime()
        vector = [0.0] * 512
        info = {
            "model_id": "synthetic-embedding",
            "model_sha": DEFAULT_EMBEDDING_REVISION,
            "model_dtype": "float32",
        }
        with mock.patch.object(runtime, "_get", return_value=info) as get, \
                mock.patch.object(runtime, "_post", return_value=[vector]):
            self.assertEqual(runtime.embed_documents(["safe"]), [vector])
            self.assertEqual(runtime.embed_documents(["safe again"]), [vector])
        self.assertEqual(get.call_count, 1)
        mismatch = self.runtime()
        with mock.patch.object(mismatch, "_get", return_value={**info, "model_sha": "0" * 40}), \
                mock.patch.object(mismatch, "_post") as post:
            with self.assertRaisesRegex(ValueError, "pinned runtime"):
                mismatch.embed_documents(["must stay local"])
        post.assert_not_called()

    def test_planner_key_is_reloaded_from_owner_only_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            key = Path(temporary) / "planner.key"
            key.write_text("short-lived-synthetic-key")
            os.chmod(key, 0o600)
            runtime = self.runtime(str(key))
            response = {"choices": [{"message": {"content": (
                '```json\n{"searchable":true,"phrases":["retry budget",'
                '"retry budget","event replay"]}\n```'
            )}}]}
            with mock.patch.object(runtime, "_post", return_value=response) as post:
                self.assertEqual(
                    runtime.plan("bounded attempts"),
                    SearchPlan(True, ("retry budget", "event replay")),
                )
            self.assertEqual(post.call_args.args[2]["Authorization"], "Bearer short-lived-synthetic-key")
            os.chmod(key, 0o644)
            with self.assertRaisesRegex(PermissionError, "owner-only"):
                runtime.plan("a different query")

    def test_query_caches_are_bounded_and_do_not_use_raw_query_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            key = Path(temporary) / "planner.key"
            key.write_text("short-lived-synthetic-key")
            os.chmod(key, 0o600)
            runtime = SemanticRuntime(
                embedding_url="http://127.0.0.1:8081",
                model="synthetic-embedding",
                revision=DEFAULT_EMBEDDING_REVISION,
                dimensions=512,
                planner_url="https://router.example",
                planner_approved_url="https://router.example",
                planner_model="synthetic-planner",
                planner_key_file=str(key),
                planner_samples=1,
                cache_size=1,
            )
            response = {"choices": [{"message": {"content": (
                '{"searchable":true,"phrases":["advisory lock"]}'
            )}}]}
            vector = [0.0] * 512
            runtime._embedding_identity_checked = True
            with mock.patch.object(runtime, "_post", side_effect=[response, [vector]]) as post:
                self.assertEqual(runtime.plan("duplicate work"), runtime.plan("duplicate work"))
                self.assertEqual(runtime.embed_query("duplicate work"), runtime.embed_query("duplicate work"))
            self.assertEqual(post.call_count, 2)
            self.assertNotIn("duplicate work", runtime._plan_cache)
            self.assertNotIn("duplicate work", runtime._query_embedding_cache)

    def test_planner_samples_union_valid_results_and_tolerate_one_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            key = Path(temporary) / "planner.key"
            key.write_text("short-lived-synthetic-key")
            os.chmod(key, 0o600)
            runtime = SemanticRuntime(
                embedding_url="http://127.0.0.1:8081",
                model="synthetic-embedding",
                revision=DEFAULT_EMBEDDING_REVISION,
                dimensions=512,
                planner_url="https://router.example",
                planner_approved_url="https://router.example",
                planner_model="synthetic-planner",
                planner_key_file=str(key),
                planner_samples=2,
            )
            response = {"choices": [{"message": {"content": (
                '{"searchable":true,"phrases":["token refresh","credential renewal"]}'
            )}}]}
            with mock.patch.object(runtime, "_post", side_effect=[response, TimeoutError("synthetic")]):
                self.assertEqual(
                    runtime.plan("renew credentials"),
                    SearchPlan(True, ("token refresh", "credential renewal")),
                )


if __name__ == "__main__":
    unittest.main()
