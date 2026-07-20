from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from client import cli as client_cli
from connectors.host import (
    HOSTED_FACTORIES,
    ConnectorHostConfig,
    ConnectorHostError,
    build_host,
    load_host_config,
    preview_host_config,
    run_host_daemon,
    validate_reserved_export_inbox,
)


ROOT = Path(__file__).parent / "connector_host_v1"
CORPUS = ROOT / "corpus.jsonl"
MANIFEST = ROOT / "manifest.json"


class FrozenHostConfigTest(unittest.TestCase):
    def test_https_endpoint_accepts_explicit_tailnet_port_and_rejects_bad_ports(self) -> None:
        value = json.loads(CORPUS.read_text().splitlines()[0])["config"]
        value["jobs"][0]["endpoint"] = "https://brain.example.test:9443"
        configured = ConnectorHostConfig.from_mapping(value)
        self.assertEqual(configured.jobs[0].endpoint, "https://brain.example.test:9443")

        for endpoint in (
            "https://brain.example.test:0",
            "https://brain.example.test:65536",
            "https://brain.example.test:not-a-port",
        ):
            with self.subTest(endpoint=endpoint):
                value["jobs"][0]["endpoint"] = endpoint
                with self.assertRaisesRegex(ConnectorHostError, "invalid_endpoint"):
                    ConnectorHostConfig.from_mapping(value)

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

    def test_reserved_export_inbox_rejects_a_second_owner_without_source_reads(self) -> None:
        value = ConnectorHostConfig.from_mapping(
            json.loads(CORPUS.read_text().splitlines()[0])["config"]
        )
        inbox = value.jobs[0].connector.inbox
        with mock.patch("pathlib.Path.iterdir") as source_read:
            with self.assertRaisesRegex(ConnectorHostError, "duplicate_export_inbox_owner"):
                validate_reserved_export_inbox(value, inbox)
            validate_reserved_export_inbox(value, inbox.parent / "different-inbox")
        source_read.assert_not_called()


class ClosedFactoryTest(unittest.TestCase):
    def test_factory_registry_is_closed_and_has_no_discovery_surface(self) -> None:
        self.assertEqual(
            tuple(HOSTED_FACTORIES),
            (
                "openai.export-inbox",
                "grep.ai",
                "google.gmail",
                "google.calendar",
                "google.contacts",
                "google.drive",
                "github.activity",
                "linear.activity",
                "slack.messages",
                "notion.workspace",
                "x.activity",
            ),
        )
        with self.assertRaises(TypeError):
            HOSTED_FACTORIES["runtime.plugin"] = object()
        self.assertFalse(set(HOSTED_FACTORIES) & {"entrypoint", "module", "command"})

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
        host.close()

    def test_canonical_mode_wires_one_tenant_scoped_writer_and_archive_client(self) -> None:
        value = ConnectorHostConfig.from_mapping(
            json.loads(CORPUS.read_text().splitlines()[0])["config"]
        )
        environment = {
            "RECALL_CANONICAL_V2_ENABLED": "1",
            "RECALL_TENANT_ID": "tenant:personal",
            "RECALL_PRINCIPAL_ID": "principal:owner",
        }
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.dict(os.environ, environment, clear=False), \
             mock.patch("connectors.host.load_file_token", return_value="brain-token"), \
             mock.patch("connectors.host.CanonicalBrainWriter") as writer_type, \
             mock.patch("connectors.host.CanonicalArchiveClient") as archive_type, \
             mock.patch("connectors.host.ExportInboxConnector") as connector_type, \
             mock.patch("connectors.host.ConnectorRunner") as runner_type:
            connector_type.return_value.connector_id = "openai.export-inbox"
            connector_type.return_value.source_id = "synthetic:export:c8g"
            runner_type.return_value = mock.Mock(
                run_once=mock.Mock(return_value={"status": "committed"}),
                close=mock.Mock(),
            )
            host = build_host(
                value,
                state_path=Path(directory) / "supervisor.db",
            )
        writer_type.assert_called_once()
        archive_type.assert_called_once()
        call = runner_type.call_args.kwargs
        self.assertIs(call["brain"], writer_type.return_value)
        self.assertIs(call["archive"], archive_type.return_value)
        self.assertEqual(call["tenant_id"], "tenant:personal")
        self.assertEqual(call["principal_id"], "principal:owner")
        host.close()


class HostCliTest(unittest.TestCase):
    def private_config(self, directory: str) -> Path:
        root = Path(directory) / "private"
        root.mkdir(mode=0o700)
        path = root / "host.json"
        value = json.loads(CORPUS.read_text().splitlines()[0])["config"]
        path.write_text(json.dumps(value)); os.chmod(path, 0o600)
        return path

    def test_preview_cli_reads_only_config_and_renders_no_private_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.private_config(directory)
            output = io.StringIO()
            with mock.patch("sys.argv", [
                "recall-brain", "connector-supervisor-config-preview", "--config", str(path),
            ]), mock.patch("sys.stdout", output), \
                 mock.patch("connectors.host.load_file_token") as file_token, \
                 mock.patch("connectors.host.load_keychain_token") as keychain, \
                 mock.patch("connectors.host.load_private_api_key") as source_key:
                client_cli.main()
            file_token.assert_not_called(); keychain.assert_not_called(); source_key.assert_not_called()
            rendered = output.getvalue()
            self.assertNotIn(str(path), rendered)
            self.assertNotIn("synthetic:export:c8g", rendered)
            self.assertEqual(json.loads(rendered)["jobs"], 1)

    def test_preview_cli_rejects_reserved_export_inbox_overlap_content_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.private_config(directory)
            value = load_host_config(path)
            inbox = value.jobs[0].connector.inbox
            with mock.patch("sys.argv", [
                "recall-brain", "connector-supervisor-config-preview", "--config", str(path),
                "--reserved-export-inbox", str(inbox),
            ]), self.assertRaisesRegex(SystemExit, "duplicate_export_inbox_owner"):
                client_cli.main()

    def test_once_cli_closes_host_and_renders_only_aggregate_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self.private_config(directory)
            result = {
                "schema_version": 1, "configured": 1, "ran": 1,
                "outcomes": {"success": 1},
            }
            output = io.StringIO()
            with mock.patch("sys.argv", [
                "recall-brain", "connector-supervisor-run", "--config", str(path),
                "--state", str(Path(directory) / "state.db"), "--once",
            ]), mock.patch("sys.stdout", output), mock.patch("client.cli.run_host_once", return_value=result) as run:
                client_cli.main()
            run.assert_called_once()
            rendered = json.loads(output.getvalue())
            self.assertEqual(rendered["outcomes"], {"success": 1})
            self.assertNotIn(str(path), output.getvalue())

    def test_daemon_reloads_between_cycles_and_maps_hup_term_to_wake_stop(self) -> None:
        handlers = {}
        old_handlers = {}

        def install(number, handler):
            handlers[number] = handler

        first = mock.Mock(); second = mock.Mock()
        first.jobs = (); second.jobs = ()

        def first_loop(*_args, **kwargs):
            handlers[__import__("signal").SIGHUP](None, None)
            self.assertTrue(kwargs["wake_event"].is_set())
            kwargs["wake_event"].clear()
            return 1

        def second_loop(*_args, **kwargs):
            handlers[__import__("signal").SIGTERM](None, None)
            self.assertTrue(kwargs["stop_event"].is_set())
            self.assertTrue(kwargs["wake_event"].is_set())
            return 1

        first.supervisor.run_loop.side_effect = first_loop
        second.supervisor.run_loop.side_effect = second_loop
        with mock.patch("connectors.host.signal.getsignal", side_effect=lambda number: old_handlers.setdefault(number, object())), \
             mock.patch("connectors.host.signal.signal", side_effect=install), \
             mock.patch("connectors.host.load_host_config", return_value=mock.Mock()) as load, \
             mock.patch("connectors.host.build_host", side_effect=(first, second)) as build:
            result = run_host_daemon(Path("/private/config"), Path("/private/state"), max_cycles=3, clock=lambda: 0)
        self.assertEqual(result, {"schema_version": 1, "status": "stopped", "cycles": 2})
        self.assertEqual(load.call_count, 2); self.assertEqual(build.call_count, 2)
        first.close.assert_called_once(); second.close.assert_called_once()

    def test_mac_live_probe_excludes_structural_connector_metadata(self) -> None:
        script = Path(__file__).resolve().parents[1] / "server/tests/e2e_macos_connector_host_c8g.py"
        spec = importlib.util.spec_from_file_location("c8g_mac_e2e", script)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        event = {"content": {
            "provider": "grep.ai", "status": "complete",
            "question": "Synthetic private question marker",
            "report_markdown": "Synthetic private report marker",
            "structured_output": None, "expert_id": None,
        }}
        private = module.private_content_strings(event)
        self.assertEqual(private, [
            "Synthetic private question marker", "Synthetic private report marker",
        ])
        self.assertEqual(module.private_query(event), "Synthetic")
        self.assertNotIn("grep.ai", private)
        self.assertNotIn("complete", private)


if __name__ == "__main__":
    unittest.main()
