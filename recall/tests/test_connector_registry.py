from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from client import cli as client_cli
from connectors.registry import (
    REGISTRY,
    ConnectorDefinition,
    ConnectorRegistryError,
    aggregate_status,
    definition,
    validate_policy,
)


ROOT = Path(__file__).parent / "connector_registry_v1"
CORPUS = ROOT / "corpus.jsonl"
MANIFEST = ROOT / "manifest.json"


class FrozenRegistryTest(unittest.TestCase):
    def test_manifest_and_closed_definition_thresholds(self):
        manifest = json.loads(MANIFEST.read_text())
        rows = [json.loads(line) for line in CORPUS.read_text().splitlines()]
        self.assertEqual(hashlib.sha256(CORPUS.read_bytes()).hexdigest(), manifest["corpus_sha256"])
        valid = accepted = invalid = invalid_accepted = 0
        for row in rows:
            try:
                value = ConnectorDefinition.from_mapping(row["definition"])
            except ConnectorRegistryError:
                self.assertFalse(row["valid"], row["case"])
                invalid += 1
                continue
            accepted += 1
            valid += int(row["valid"])
            invalid_accepted += int(not row["valid"])
            self.assertEqual(value.to_public(), row["definition"])
        self.assertEqual(accepted / valid, manifest["thresholds"]["valid_acceptance"])
        self.assertEqual(invalid_accepted / invalid, manifest["thresholds"]["invalid_acceptance"])

    def test_builtin_registry_is_exact_immutable_and_no_discovery(self):
        self.assertEqual(tuple(item.connector_id for item in REGISTRY), (
            "recall.capture", "chatgpt.export_inbox", "grep.ai",
        ))
        self.assertEqual(definition("grep.ai").authority_slots, ("brain", "source"))
        with self.assertRaises(ConnectorRegistryError):
            definition("entrypoint.from.cwd")
        with self.assertRaises((AttributeError, TypeError)):
            REGISTRY[0].mode = "write"

    def test_policy_is_registry_driven_and_deletion_is_always_explicit(self):
        validate_policy("recall.capture", visibility="shared", privacy_mode="off", authorities={"brain"})
        validate_policy("chatgpt.export_inbox", visibility="private", privacy_mode="scrub", authorities={"brain"})
        validate_policy("grep.ai", visibility="private", privacy_mode="drop", authorities={"brain", "source"})
        for connector_id, visibility, privacy, authorities in (
            ("grep.ai", "shared", "drop", {"brain", "source"}),
            ("grep.ai", "private", "off", {"brain", "source"}),
            ("grep.ai", "private", "drop", {"brain"}),
        ):
            with self.assertRaises(ConnectorRegistryError):
                validate_policy(connector_id, visibility=visibility, privacy_mode=privacy, authorities=authorities)
        self.assertTrue(all(item.deletion.startswith("explicit_") for item in REGISTRY))


class RegistryPreviewAndStatusTest(unittest.TestCase):
    def test_preview_is_static_content_free_and_zero_io(self):
        output = io.StringIO()
        with mock.patch("sys.argv", ["recall-brain", "connector-registry-preview"]), \
             mock.patch("sys.stdout", output), \
             mock.patch("client.cli.load_file_token") as token, \
             mock.patch("client.cli.load_private_api_key") as source_key, \
             mock.patch("sqlite3.connect") as sqlite_connect, \
             mock.patch("urllib.request.urlopen") as network:
            client_cli.main()
        token.assert_not_called(); source_key.assert_not_called()
        sqlite_connect.assert_not_called(); network.assert_not_called()
        value = json.loads(output.getvalue())
        self.assertEqual(value["credential_reads"], 0)
        self.assertEqual(value["source_reads"], 0)
        self.assertEqual(value["network_requests"], 0)
        self.assertEqual(value["writes"], 0)
        self.assertEqual(len(value["connectors"]), 3)

    def test_status_health_is_bounded_read_only_and_content_free(self):
        with tempfile.TemporaryDirectory() as directory:
            spool = Path(directory) / "state.db"
            connection = sqlite3.connect(spool)
            connection.executescript("""
                CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE pages(id INTEGER PRIMARY KEY);
                CREATE TABLE outbox(id INTEGER PRIMARY KEY);
                INSERT INTO meta VALUES ('committed_cursor','private-cursor-never-render');
                INSERT INTO meta VALUES ('last_error_code','connector_rate_limited');
                INSERT INTO pages VALUES (1);
                INSERT INTO outbox VALUES (1);
            """)
            connection.commit(); connection.close()
            before = hashlib.sha256(spool.read_bytes()).hexdigest()
            value = aggregate_status(
                connector_id="grep.ai", enabled=True, privacy_mode="drop",
                authorities={"brain", "source"}, spool_path=spool,
            )
            after = hashlib.sha256(spool.read_bytes()).hexdigest()
            self.assertEqual(before, after)
            self.assertEqual(value["health"], "degraded")
            self.assertEqual(value["error_code"], "connector_rate_limited")
            self.assertTrue(value["checkpointed"])
            self.assertEqual((value["pending_pages"], value["pending"]), (1, 1))
            rendered = json.dumps(value)
            self.assertNotIn("private-cursor", rendered)
            self.assertNotIn(str(spool), rendered)
            self.assertFalse(set(value) & {"cursor", "source_id", "path", "credential", "report", "content"})

    def test_status_distinguishes_disabled_missing_state_authority_and_ready(self):
        disabled = aggregate_status("grep.ai", False, "drop", set(), None)
        missing_authority = aggregate_status("grep.ai", True, "drop", {"brain"}, None)
        missing_state = aggregate_status("grep.ai", True, "drop", {"brain", "source"}, Path("/missing"))
        capture = aggregate_status("recall.capture", True, "scrub", {"brain"}, None)
        self.assertEqual(disabled["health"], "disabled")
        self.assertEqual(missing_authority["health"], "reference_missing")
        self.assertEqual(missing_state["health"], "local_state_unavailable")
        self.assertEqual(capture["health"], "ready")


if __name__ == "__main__":
    unittest.main()
