from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from connectors.host import (
    ConnectorHostConfig,
    ConnectorHostError,
    build_host,
    load_host_config,
    preview_host_config,
)


ROOT = Path(__file__).parent / "connector_host_v1"
CORPUS = ROOT / "corpus.jsonl"
MANIFEST = ROOT / "manifest.json"


class FrozenHostConfigTest(unittest.TestCase):
    def test_manifest_and_closed_config_thresholds(self) -> None:
        manifest = json.loads(MANIFEST.read_text())
        rows = [json.loads(line) for line in CORPUS.read_text().splitlines()]
        self.assertEqual(hashlib.sha256(CORPUS.read_bytes()).hexdigest(), manifest["corpus_sha256"])
        valid = accepted = invalid = invalid_accepted = 0
        for row in rows:
            try:
                value = ConnectorHostConfig.from_mapping(row["config"])
            except ConnectorHostError:
                self.assertFalse(row["valid"], row["case"])
                invalid += 1
                continue
            accepted += 1
            valid += int(row["valid"])
            invalid_accepted += int(not row["valid"])
            self.assertEqual(value.to_mapping(), row["config"])
        self.assertEqual(accepted / valid, manifest["thresholds"]["valid_acceptance"])
        self.assertEqual(invalid_accepted / invalid, manifest["thresholds"]["invalid_acceptance"])

    def test_config_file_is_explicit_private_regular_and_validation_reads_nothing_else(self) -> None:
        value = json.loads(CORPUS.read_text().splitlines()[0])["config"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "private"
            root.mkdir(mode=0o700)
            path = root / "host.json"
            path.write_text(json.dumps(value)); os.chmod(path, 0o600)
            with mock.patch("connectors.host.load_file_token") as file_token, \
                 mock.patch("connectors.host.load_keychain_token") as keychain, \
                 mock.patch("connectors.host.load_private_api_key") as source_key, \
                 mock.patch("pathlib.Path.iterdir") as source_read, \
                 mock.patch("urllib.request.urlopen") as network:
                loaded = load_host_config(path)
            file_token.assert_not_called(); keychain.assert_not_called(); source_key.assert_not_called()
            source_read.assert_not_called(); network.assert_not_called()
            self.assertEqual(len(loaded.jobs), 1)

            os.chmod(path, 0o644)
            with self.assertRaisesRegex(ConnectorHostError, "config_not_private"):
                load_host_config(path)
            os.chmod(path, 0o600)
            link = root / "linked.json"; link.symlink_to(path)
            with self.assertRaisesRegex(ConnectorHostError, "config_not_regular"):
                load_host_config(link)

    def test_preview_is_content_free_and_does_not_read_authority_or_source(self) -> None:
        value = ConnectorHostConfig.from_mapping(json.loads(CORPUS.read_text().splitlines()[2])["config"])
        with mock.patch("connectors.host.load_file_token") as file_token, \
             mock.patch("connectors.host.load_keychain_token") as keychain, \
             mock.patch("connectors.host.load_private_api_key") as source_key, \
             mock.patch("pathlib.Path.iterdir") as source_read, \
             mock.patch("urllib.request.urlopen") as network:
            preview = preview_host_config(value)
        file_token.assert_not_called(); keychain.assert_not_called(); source_key.assert_not_called()
        source_read.assert_not_called(); network.assert_not_called()
        rendered = json.dumps(preview)
        for canary in ("synthetic:export:c8g", "/synthetic/", "synthetic.brain", "research-read"):
            self.assertNotIn(canary, rendered)
        self.assertEqual(preview["jobs"], 2)
        self.assertEqual(preview["credential_reads"], 0)
        self.assertEqual(preview["source_reads"], 0)
        self.assertEqual(preview["network_requests"], 0)
        self.assertEqual(preview["writes"], 0)


class ClosedFactoryTest(unittest.TestCase):
    def test_factory_uses_only_bundled_types_and_separate_authorities(self) -> None:
        value = ConnectorHostConfig.from_mapping(json.loads(CORPUS.read_text().splitlines()[2])["config"])
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch("connectors.host.load_file_token", side_effect=("brain-export", "brain-grep")) as file_token, \
             mock.patch("connectors.host.load_keychain_token", return_value="parcha-synthetic-" + "a" * 32) as keychain, \
             mock.patch("connectors.host.ExportInboxConnector") as export_type, \
             mock.patch("connectors.host.GrepAIConnector") as grep_type, \
             mock.patch("connectors.host.BrainClient") as brain_type, \
             mock.patch("connectors.host.ConnectorRunner") as runner_type:
            export_type.return_value.connector_id = "openai.export-inbox"
            export_type.return_value.source_id = "synthetic:export:c8g"
            grep_type.return_value.connector_id = "grep.ai"
            grep_type.return_value.source_id = "synthetic:grep:c8g"
            runner_type.side_effect = lambda **kwargs: mock.Mock(run_once=mock.Mock(return_value={"status": "committed"}), close=mock.Mock())
            host = build_host(value, state_path=Path(directory) / "supervisor.db")
        self.assertEqual(export_type.call_count, 1)
        self.assertEqual(grep_type.call_count, 1)
        self.assertEqual(brain_type.call_count, 2)
        self.assertEqual(runner_type.call_count, 2)
        self.assertEqual(file_token.call_count, 2)
        self.assertEqual(keychain.call_count, 1)
        self.assertEqual(len(host.jobs), 2)
        self.assertNotEqual(
            value.jobs[1].brain_authority.fingerprint(),
            value.jobs[1].source_authority.fingerprint(),
        )


if __name__ == "__main__":
    unittest.main()
