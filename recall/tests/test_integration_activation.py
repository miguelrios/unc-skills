from __future__ import annotations

import argparse
import json
import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from client import cli as client_cli
from connectors.activation import (
    ActivationError,
    ActivationStore,
    activation_catalog,
    load_activation_config,
    preview_activation_config,
)
from connectors.registry import REGISTRY


def _intent(connector_id: str) -> dict:
    cases = {
        "apple.imessage": {
            "authority_references": {
                "brain": {"kind": "file", "path": "/private/brain-token.json"},
            },
            "selectors": {"chat_ids": ["synthetic-chat"], "date_min": None},
        },
        "google.gmail": {
            "authority_references": {
                "brain": {"kind": "file", "path": "/private/brain-token.json"},
                "source": {"kind": "keychain", "service": "recall.source", "account": "gmail"},
            },
            "selectors": {
                "include_spam_trash": False,
                "label_ids": ["INBOX"],
                "own_addresses": ["synthetic@example.invalid"],
            },
        },
        "portable.jsonl": {
            "authority_references": {
                "brain": {"kind": "file", "path": "/private/brain-token.json"},
            },
            "selectors": {"max_depth": 4, "root": "/private/selected-export"},
        },
        "custom.webhook": {
            "authority_references": {},
            "selectors": {},
        },
    }
    return {
        "schema_version": 1,
        "connector_id": connector_id,
        "source_id": f"synthetic:{connector_id}",
        "principal_id": "synthetic-owner",
        "privacy_mode": "scrub",
        **cases[connector_id],
    }


class ActivationCatalogTest(unittest.TestCase):
    def test_every_registry_entry_has_one_concrete_activation_surface(self) -> None:
        value = activation_catalog()
        self.assertEqual(value["schema_version"], 1)
        self.assertEqual(value["mode"], "integration-activation-catalog")
        self.assertEqual(
            {
                "credential_reads": 0,
                "source_reads": 0,
                "network_requests": 0,
                "writes": 0,
            },
            {key: value[key] for key in (
                "credential_reads", "source_reads", "network_requests", "writes",
            )},
        )
        entries = value["connectors"]
        self.assertEqual(len(entries), len(REGISTRY))
        self.assertEqual(
            {entry["connector_id"] for entry in entries},
            {item.connector_id for item in REGISTRY},
        )
        self.assertEqual(len(entries), len({entry["connector_id"] for entry in entries}))
        for entry in entries:
            self.assertEqual(
                set(entry),
                {
                    "config_contract",
                    "connector_id",
                    "entrypoint",
                    "implementation",
                    "lifecycle",
                    "package",
                    "runtime",
                },
            )
            self.assertEqual(entry["implementation"], "available")
            self.assertTrue(all(
                isinstance(entry[field], str) and entry[field]
                for field in (
                    "config_contract", "entrypoint", "lifecycle", "package", "runtime",
                )
            ))

        by_id = {entry["connector_id"]: entry for entry in entries}
        self.assertEqual(by_id["apple.imessage"]["runtime"], "mac_utility")
        self.assertEqual(by_id["google.gmail"]["runtime"], "remote_worker")
        self.assertEqual(by_id["portable.jsonl"]["runtime"], "portable_runner")
        self.assertEqual(by_id["custom.webhook"]["runtime"], "public_edge")
        self.assertEqual(by_id["recall.capture"]["runtime"], "mcp_host")

        cli_commands = next(
            action.choices
            for action in client_cli.parser()._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        for entry in entries:
            if entry["package"] == "recall-core":
                self.assertEqual(entry["entrypoint"], "serve")
            else:
                self.assertIn(entry["entrypoint"], cli_commands)


class ActivationIntentTest(unittest.TestCase):
    def _write(self, root: Path, value: dict, name: str = "intent.json") -> Path:
        path = root / name
        path.write_text(json.dumps(value))
        path.chmod(0o600)
        return path

    def test_private_intent_preview_is_closed_and_content_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            for connector_id in (
                "apple.imessage", "google.gmail", "portable.jsonl", "custom.webhook",
            ):
                path = self._write(root, _intent(connector_id), f"{connector_id}.json")
                config = load_activation_config(path)
                value = preview_activation_config(config)
                rendered = json.dumps(value, sort_keys=True)
                self.assertEqual(value["connector_id"], connector_id)
                self.assertEqual(value["credential_reads"], 0)
                self.assertEqual(value["source_reads"], 0)
                self.assertEqual(value["network_requests"], 0)
                self.assertEqual(value["writes"], 0)
                for private in (
                    "synthetic-owner",
                    f"synthetic:{connector_id}",
                    "/private/",
                    "synthetic@example.invalid",
                    "synthetic-chat",
                    "INBOX",
                ):
                    self.assertNotIn(private, rendered)
                self.assertFalse(
                    set(value)
                    & {
                        "source_id", "principal_id", "selectors", "paths", "cursor",
                        "credential", "authority_references",
                    }
                )

            webhook = preview_activation_config(
                load_activation_config(root / "custom.webhook.json")
            )
            self.assertEqual(webhook["required_capability"], "webhook")
            self.assertTrue(webhook["source_bound"])
            self.assertTrue(webhook["principal_bound"])
            self.assertFalse(webhook["credential_issued"])

    def test_intent_loader_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            valid = _intent("apple.imessage")

            permissive = self._write(root, valid, "permissive.json")
            permissive.chmod(0o644)
            with self.assertRaisesRegex(ActivationError, "config_not_private"):
                load_activation_config(permissive)

            target = self._write(root, valid, "target.json")
            symlink = root / "symlink.json"
            symlink.symlink_to(target)
            with self.assertRaisesRegex(ActivationError, "config_not_regular"):
                load_activation_config(symlink)

            for field in ("command", "endpoint", "plugin", "import_string"):
                extra = self._write(root, {**valid, field: "forbidden"}, f"{field}.json")
                with self.assertRaisesRegex(ActivationError, "invalid_config_fields"):
                    load_activation_config(extra)

            unknown = _intent("apple.imessage")
            unknown["selectors"]["secret_selector"] = "nope"
            with self.assertRaisesRegex(ActivationError, "invalid_selectors"):
                load_activation_config(self._write(root, unknown, "unknown.json"))

            missing_authority = _intent("google.gmail")
            del missing_authority["authority_references"]["source"]
            with self.assertRaisesRegex(ActivationError, "authority_reference_mismatch"):
                load_activation_config(self._write(root, missing_authority, "authority.json"))


class ActivationLifecycleTest(unittest.TestCase):
    def test_four_profiles_complete_idempotent_lifecycle_without_private_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            state = root / "activation.db"
            store = ActivationStore(state)
            try:
                for connector_id in (
                    "apple.imessage", "google.gmail", "portable.jsonl", "custom.webhook",
                ):
                    path = root / f"{connector_id}.json"
                    path.write_text(json.dumps(_intent(connector_id)))
                    path.chmod(0o600)
                    config = load_activation_config(path)

                    configured = store.configure(config)
                    self.assertEqual(configured["state"], "configured")
                    self.assertFalse(configured["replay"])
                    replay = store.configure(config)
                    self.assertEqual(replay["revision"], configured["revision"])
                    self.assertTrue(replay["replay"])

                    expected = (
                        ("enable", "enabled"),
                        ("pause", "paused"),
                        ("resume", "enabled"),
                        ("revoke", "revoked"),
                        ("uninstall", "uninstalled"),
                    )
                    for action, final_state in expected:
                        result = store.transition(connector_id, action)
                        self.assertEqual(result["state"], final_state)
                        self.assertFalse(result["replay"])
                        replay = store.transition(connector_id, action)
                        self.assertEqual(replay["revision"], result["revision"])
                        self.assertTrue(replay["replay"])

                    status = store.status(connector_id)
                    rendered = json.dumps(status, sort_keys=True)
                    self.assertEqual(status["state"], "uninstalled")
                    self.assertFalse(
                        set(status)
                        & {
                            "config_digest", "source_id", "principal_id", "selectors",
                            "paths", "cursor", "credential", "authority_references",
                        }
                    )
                    self.assertNotIn("/private/", rendered)
                    self.assertNotIn("synthetic-owner", rendered)
            finally:
                store.close()

            self.assertEqual(os.stat(state).st_mode & 0o777, 0o600)

    def test_invalid_transition_fails_without_state_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            path = root / "intent.json"
            path.write_text(json.dumps(_intent("custom.webhook")))
            path.chmod(0o600)
            store = ActivationStore(root / "activation.db")
            try:
                store.configure(load_activation_config(path))
                before = store.status("custom.webhook")
                with self.assertRaisesRegex(ActivationError, "invalid_transition"):
                    store.transition("custom.webhook", "resume")
                self.assertEqual(store.status("custom.webhook"), before)
            finally:
                store.close()

    def test_status_of_absent_activation_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            state = root / "missing.db"
            store = ActivationStore(state, create=False, read_only=True)
            try:
                self.assertEqual(
                    store.status("custom.webhook")["state"],
                    "absent",
                )
            finally:
                store.close()
            self.assertFalse(state.exists())

    def test_state_loader_rejects_permissive_symlink_and_malformed_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            permissive = root / "permissive.db"
            permissive.write_bytes(b"")
            permissive.chmod(0o644)
            with self.assertRaisesRegex(ActivationError, "state_not_private"):
                ActivationStore(permissive, create=False, read_only=True)

            target = root / "target.db"
            target.write_bytes(b"")
            target.chmod(0o600)
            symlink = root / "state.db"
            symlink.symlink_to(target)
            with self.assertRaisesRegex(ActivationError, "state_not_regular"):
                ActivationStore(symlink, create=False, read_only=True)

            malformed = root / "malformed.db"
            malformed.write_bytes(b"not sqlite")
            malformed.chmod(0o600)
            store = ActivationStore(malformed, create=False, read_only=True)
            try:
                with self.assertRaisesRegex(ActivationError, "state_invalid"):
                    store.status("custom.webhook")
            finally:
                store.close()

    def test_status_rejects_injected_private_state_without_rendering_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            config_path = root / "intent.json"
            config_path.write_text(json.dumps(_intent("custom.webhook")))
            config_path.chmod(0o600)
            state = root / "activation.db"
            store = ActivationStore(state)
            try:
                store.configure(load_activation_config(config_path))
            finally:
                store.close()
            connection = sqlite3.connect(state)
            connection.execute(
                "UPDATE activations SET state=? WHERE connector_id=?",
                ("private-content-must-not-render", "custom.webhook"),
            )
            connection.commit()
            connection.close()

            status_store = ActivationStore(state, create=False, read_only=True)
            try:
                with self.assertRaisesRegex(ActivationError, "^state_invalid$"):
                    status_store.status("custom.webhook")
            finally:
                status_store.close()


class ActivationCliTest(unittest.TestCase):
    def _run(self, arguments: list[str]) -> dict:
        output = io.StringIO()
        with mock.patch("sys.argv", ["recall-brain", *arguments]), \
             mock.patch("sys.stdout", output):
            client_cli.main()
        return json.loads(output.getvalue())

    def test_cli_catalog_preview_and_full_webhook_lifecycle(self) -> None:
        catalog = self._run(["integration-activation-catalog"])
        self.assertEqual(len(catalog["connectors"]), len(REGISTRY))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            config = root / "webhook.json"
            config.write_text(json.dumps(_intent("custom.webhook")))
            config.chmod(0o600)
            state = root / "activation.db"

            preview = self._run([
                "integration-activation-preview", "--config", str(config),
            ])
            self.assertEqual(preview["runtime"], "public_edge")
            self.assertFalse(preview["credential_issued"])

            configured = self._run([
                "integration-activation-configure",
                "--config", str(config),
                "--state", str(state),
            ])
            self.assertEqual(configured["state"], "configured")
            for action, expected in (
                ("enable", "enabled"),
                ("pause", "paused"),
                ("resume", "enabled"),
                ("revoke", "revoked"),
                ("uninstall", "uninstalled"),
            ):
                value = self._run([
                    "integration-activation-transition",
                    "--state", str(state),
                    "--connector-id", "custom.webhook",
                    "--action", action,
                ])
                self.assertEqual(value["state"], expected)
            status = self._run([
                "integration-activation-status",
                "--state", str(state),
                "--connector-id", "custom.webhook",
            ])
            self.assertEqual(status["state"], "uninstalled")

    def test_cli_status_and_failed_transition_do_not_create_missing_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            state = root / "missing.db"
            status = self._run([
                "integration-activation-status",
                "--state", str(state),
                "--connector-id", "custom.webhook",
            ])
            self.assertEqual(status["state"], "absent")
            self.assertFalse(state.exists())
            with mock.patch("sys.argv", [
                "recall-brain",
                "integration-activation-transition",
                "--state", str(state),
                "--connector-id", "custom.webhook",
                "--action", "enable",
            ]), self.assertRaisesRegex(SystemExit, "activation_not_configured"):
                client_cli.main()
            self.assertFalse(state.exists())


if __name__ == "__main__":
    unittest.main()
