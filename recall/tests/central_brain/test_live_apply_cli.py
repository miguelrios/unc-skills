from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RECALL = Path(__file__).resolve().parents[2]
SERVER = RECALL / "server"
sys.path.insert(0, str(SERVER))

from recall_server.cli import main  # noqa: E402
from recall_server.deployment import load_manifest, preview  # noqa: E402
from recall_server.live_providers import (  # noqa: E402
    LiveProviderError,
    build_live_adapters,
)


class FakeAdapter:
    def ensure(self, logical_id: str, desired: dict) -> dict[str, str]:
        return {
            "action": "unchanged",
            "receipt_sha256": __import__("hashlib")
            .sha256(logical_id.encode())
            .hexdigest(),
        }


class LiveApplyCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest_path = SERVER / "deploy" / "recall-core.plan.example.json"
        manifest = load_manifest(self.manifest_path)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.approvals_path = Path(self.directory.name) / "approvals.json"
        self.approvals_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "plan_sha256": preview(manifest)["plan_sha256"],
                    "infrastructure": {
                        "provider-billing": {
                            "approved": True,
                            "selection": "balanced-ha",
                        },
                        "provider-region": {
                            "approved": True,
                            "selection": "virginia",
                        },
                        "provider-authorization": {"approved": True},
                        "tailnet-route": {"approved": True},
                    },
                    "writer-cutover": {"approved": False},
                }
            )
        )
        os.chmod(self.approvals_path, 0o600)

    def argv(self) -> list[str]:
        return [
            "recall-server",
            "deployment-apply",
            "--manifest",
            str(self.manifest_path),
            "--approvals",
            str(self.approvals_path),
            "--planetscale-organization",
            "synthetic-org",
            "--database-name",
            "synthetic-recall",
            "--render-owner-id",
            "owner-synthetic",
            "--core-name",
            "synthetic-recall-core",
            "--gateway-name",
            "synthetic-recall-gateway",
            "--tailnet-hostname",
            "synthetic-recall",
            "--tailnet-tag",
            "tag:synthetic-recall",
        ]

    def test_cli_reconciles_only_after_approval_and_emits_content_free_json(self):
        adapters = {key: FakeAdapter() for key in ("database", "service", "network")}
        output = io.StringIO()
        with (
            mock.patch.object(sys, "argv", self.argv()),
            mock.patch(
                "recall_server.cli.build_live_adapters",
                return_value=adapters,
            ) as build,
            contextlib.redirect_stdout(output),
        ):
            main()
        result = json.loads(output.getvalue())
        self.assertEqual(result["resource_count"], 3)
        self.assertEqual(result["actions"], {"created": 0, "unchanged": 3})
        self.assertEqual(result["status"], "writer_cutover_approval_required")
        build.assert_called_once()
        rendered = output.getvalue()
        for forbidden in (
            "synthetic-org",
            "owner-synthetic",
            "synthetic-recall",
            "secret://",
            "approval://",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_adapter_builder_requires_every_injected_secret(self):
        common = {
            "PLANETSCALE_SERVICE_TOKEN_ID": "synthetic-ps-id",
            "PLANETSCALE_SERVICE_TOKEN": "synthetic-ps-token",
            "RENDER_API_KEY": "synthetic-render-token",
            "RECALL_DATABASE_URL": (
                "postgresql://synthetic:synthetic@db.invalid/recall"
                "?sslmode=verify-full&sslrootcert=system"
            ),
            "RECALL_EMBEDDING_API_KEY": "synthetic-embedding-key",
            "TAILSCALE_OAUTH_CLIENT_ID": "synthetic-ts-id",
            "TAILSCALE_OAUTH_CLIENT_SECRET": "synthetic-ts-secret",
        }
        kwargs = {
            "planetscale_organization": "synthetic-org",
            "database_name": "synthetic-recall",
            "render_owner_id": "owner-synthetic",
            "core_name": "synthetic-recall-core",
            "gateway_name": "synthetic-recall-gateway",
            "tailnet_hostname": "synthetic-recall",
            "tailnet_tag": "tag:synthetic-recall",
        }
        with mock.patch.dict(os.environ, common, clear=True):
            adapters = build_live_adapters(**kwargs)
        self.assertEqual(set(adapters), {"database", "service", "network"})
        for missing in common:
            values = {key: value for key, value in common.items() if key != missing}
            with (
                self.subTest(missing=missing),
                mock.patch.dict(os.environ, values, clear=True),
                self.assertRaisesRegex(
                    LiveProviderError, "provider_credentials_unavailable"
                ),
            ):
                build_live_adapters(**kwargs)


if __name__ == "__main__":
    unittest.main()
