from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from client.mac import ExportImporter, MemoryClient, PrivacyError, dry_run_manifest


class DryRunPrivacyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.claude = self.home / ".claude" / "projects"
        self.codex = self.home / ".codex" / "sessions"
        self.claude.mkdir(parents=True)
        self.codex.mkdir(parents=True)

    def test_manifest_is_content_free_exact_and_selection_scoped(self) -> None:
        secret = "C5_TOKEN_CANARY_DO_NOT_RENDER"
        claude_file = self.claude / "project" / "session.jsonl"
        claude_file.parent.mkdir()
        claude_file.write_text(json.dumps({"message": secret}) + "\n")
        (self.claude / "project" / "notes.txt").write_text("not eligible")
        codex_file = self.codex / "2026" / "rollout-one.jsonl"
        codex_file.parent.mkdir()
        codex_file.write_text("{}\n")

        manifest = dry_run_manifest(
            selections=[{"harness": "claude", "root": str(self.claude)}],
            visibility="private",
            home=self.home,
        )

        self.assertEqual(manifest["mode"], "dry-run")
        self.assertEqual(manifest["network_requests"], 0)
        self.assertEqual(manifest["visibility"], "private")
        self.assertEqual(len(manifest["files"]), 1)
        item = manifest["files"][0]
        self.assertEqual(item["relative_path"], "project/session.jsonl")
        self.assertEqual(item["bytes"], claude_file.stat().st_size)
        self.assertEqual(item["sha256"], hashlib.sha256(claude_file.read_bytes()).hexdigest())
        rendered = json.dumps(manifest, sort_keys=True)
        self.assertNotIn(secret, rendered)
        self.assertNotIn(str(codex_file), rendered)
        self.assertNotIn("notes.txt", rendered)

    def test_symlink_escape_and_undocumented_app_roots_fail_closed(self) -> None:
        outside = self.home / "outside.jsonl"
        outside.write_text("{}\n")
        (self.claude / "escape.jsonl").symlink_to(outside)
        with self.assertRaisesRegex(PrivacyError, "escape"):
            dry_run_manifest(
                selections=[{"harness": "claude", "root": str(self.claude)}],
                visibility="private",
                home=self.home,
            )

        chatgpt = self.home / "Library" / "Application Support" / "ChatGPT"
        chatgpt.mkdir(parents=True)
        with self.assertRaisesRegex(PrivacyError, "unsupported private application path"):
            dry_run_manifest(
                selections=[{"harness": "claude", "root": str(chatgpt)}],
                visibility="private",
                home=self.home,
            )

    def test_visibility_is_explicit_and_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "visibility"):
            dry_run_manifest(selections=[], visibility="public", home=self.home)


class SupportedExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_jsonl_and_zip_members_retain_provenance_and_are_idempotent(self) -> None:
        source = self.root / "supported-export.jsonl"
        source.write_text('{"text":"first"}\n{"text":"second"}\n')
        archive = self.root / "supported-export.zip"
        with zipfile.ZipFile(archive, "w") as output:
            info = zipfile.ZipInfo("conversations/chat.json")
            info.date_time = (2020, 1, 1, 0, 0, 0)
            output.writestr(info, json.dumps([{"text": "third"}]))

        importer = ExportImporter(source_id="export:mac:test", principal_id="owner", visibility="private")
        first = importer.inventory([source, archive])
        second = importer.inventory([source, archive])

        self.assertEqual(first, second)
        self.assertEqual(len(first["records"]), 3)
        members = {record["provenance"]["member"] for record in first["records"]}
        self.assertIn("supported-export.jsonl#record=1", members)
        self.assertIn("conversations/chat.json#record=0", members)
        for record in first["records"]:
            provenance = record["provenance"]
            self.assertEqual(
                provenance["original_path"],
                f"{provenance['uri']}/{provenance['member']}",
            )
            self.assertNotIn(str(self.root), provenance["original_path"])
        self.assertTrue(all(record["kind"] == "chat_export" for record in first["records"]))

    def test_archive_traversal_and_symlink_members_are_rejected(self) -> None:
        traversal = self.root / "traversal.zip"
        with zipfile.ZipFile(traversal, "w") as output:
            output.writestr("../escape.json", "{}")
        importer = ExportImporter(source_id="export:mac:test", principal_id="owner", visibility="private")
        with self.assertRaisesRegex(PrivacyError, "unsafe archive member"):
            importer.inventory([traversal])

        symlink = self.root / "symlink.zip"
        with zipfile.ZipFile(symlink, "w") as output:
            info = zipfile.ZipInfo("linked.json")
            info.external_attr = (0o120777 << 16)
            output.writestr(info, "target")
        with self.assertRaisesRegex(PrivacyError, "symlink"):
            importer.inventory([symlink])

        source = self.root / "source.json"
        source.write_text("{}")
        alias = self.root / "alias.json"
        alias.symlink_to(source)
        with self.assertRaisesRegex(PrivacyError, "must not be a symlink"):
            importer.inventory([alias])


class FakeResponse:
    def __init__(self, status: int, body: dict):
        self.status = status
        self.body = json.dumps(body).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.body


class ExplicitMemoryTest(unittest.TestCase):
    def test_put_and_delete_use_canonical_envelopes_without_token_in_argv_or_body(self) -> None:
        requests = []

        def open_request(request, **_kwargs):
            requests.append(request)
            event = json.loads(request.data)["events"][0]
            revision = 2 if event["kind"] == "tombstone" else 1
            return FakeResponse(201, {
                "status": "committed",
                "receipts": [f"recall://{event['source_id']}/{event['native_id']}?rev={revision}"],
            })

        client = MemoryClient(
            endpoint="https://brain.example.ts.net",
            token="C5_TOKEN_CANARY_DO_NOT_RENDER",
            source_id="memory:mac:test",
            principal_id="owner",
            visibility="private",
        )
        with mock.patch("urllib.request.urlopen", side_effect=open_request):
            put = client.put("remember this exact marker", provenance={"uri": "manual://test"})
            deleted = client.delete(put["receipt"])

        self.assertEqual(put["kind"], "memory")
        self.assertEqual(deleted["kind"], "tombstone")
        put_event = json.loads(requests[0].data)["events"][0]
        delete_event = json.loads(requests[1].data)["events"][0]
        self.assertEqual(put_event["content"], {"text": "remember this exact marker"})
        self.assertEqual(delete_event["content"]["target_native_id"], put_event["native_id"])
        self.assertEqual(delete_event["native_id"], put_event["native_id"])
        for request in requests:
            self.assertNotIn(b"C5_TOKEN_CANARY_DO_NOT_RENDER", request.data)
            self.assertEqual(request.get_header("Authorization"), "Bearer C5_TOKEN_CANARY_DO_NOT_RENDER")


if __name__ == "__main__":
    unittest.main()
