from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from connectors.composio_workspace_rail import ComposioWorkspaceRail
from server.recall_server.control import ControlError
from server.recall_server.managed_worker import ManagedConnectorWorker, _private_root


class ManagedWorkerComposioTests(unittest.TestCase):
    def test_private_root_normalizes_provider_mount_mode_without_following_links(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            provider_mount = root / "worker"
            provider_mount.mkdir(mode=0o755)

            self.assertEqual(_private_root(provider_mount), provider_mount)
            self.assertEqual(stat.S_IMODE(provider_mount.stat().st_mode), 0o700)

            target = root / "target"
            target.mkdir(mode=0o700)
            linked = root / "linked"
            linked.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(
                ValueError,
                "managed worker state root is not private",
            ):
                _private_root(linked)

    def worker(self, root: Path) -> ManagedConnectorWorker:
        worker = object.__new__(ManagedConnectorWorker)
        worker.spool_root = root / "spools"
        worker.spool_root.mkdir(mode=0o700)
        worker.remote_rails = {}
        return worker

    def row(self):
        return {
            "id": "synthetic-installation",
            "connector_id": "google.gmail",
            "provider": "composio",
            "source_id": "synthetic:google:gmail",
            "privacy_mode": "scrub",
            "selectors": {"own_addresses": [], "label_ids": []},
        }

    def credentials(self):
        return {
            "user_id": "principal_synthetic_owner",
            "connected_account_id": "ca_synthetic_account_123",
            "toolkit": "gmail",
        }

    def test_composio_connection_builds_without_materializing_provider_credentials(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "authority"
            private.mkdir(mode=0o700)
            worker = self.worker(root)
            with patch.dict(
                os.environ,
                {"RECALL_COMPOSIO_API_KEY": "synthetic-project-authority"},
                clear=True,
            ):
                connector, spool = worker._build_default(
                    self.row(), self.credentials(), private
                )
            self.assertIsInstance(connector.rail, ComposioWorkspaceRail)
            self.assertEqual(connector.rail.user_id, "principal_synthetic_owner")
            self.assertEqual(
                connector.rail.connected_account_id,
                "ca_synthetic_account_123",
            )
            self.assertEqual(connector.rail.toolkit, "gmail")
            self.assertEqual(connector.page_size, 10)
            self.assertEqual(list(private.iterdir()), [])
            self.assertEqual(spool, worker.spool_root / "synthetic-installation.db")

    def test_toolkit_mismatch_and_missing_project_authority_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private = root / "authority"
            private.mkdir(mode=0o700)
            worker = self.worker(root)
            wrong = {**self.credentials(), "toolkit": "googlecalendar"}
            with (
                patch.dict(
                    os.environ,
                    {"RECALL_COMPOSIO_API_KEY": "synthetic-project-authority"},
                    clear=True,
                ),
                self.assertRaisesRegex(ControlError, "connector_authority_revoked"),
            ):
                worker._build_default(self.row(), wrong, private)
            with (
                patch.dict(os.environ, {}, clear=True),
                self.assertRaisesRegex(ControlError, "connector_authority_revoked"),
            ):
                worker._build_default(self.row(), self.credentials(), private)


if __name__ == "__main__":
    unittest.main()
