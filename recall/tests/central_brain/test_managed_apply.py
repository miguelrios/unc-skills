from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


RECALL = Path(__file__).resolve().parents[2]
SERVER = RECALL / "server"
sys.path.insert(0, str(SERVER))

from recall_server.deployment import load_manifest, preview  # noqa: E402
from recall_server.managed_apply import (  # noqa: E402
    ApprovalError,
    approval_status,
    load_approvals,
    reconcile_infrastructure,
)


class FakeAdapter:
    def __init__(self) -> None:
        self.resources: dict[str, str] = {}
        self.calls = 0

    def ensure(self, logical_id: str, desired: dict) -> dict[str, str]:
        self.calls += 1
        digest = __import__("hashlib").sha256(json.dumps(
            desired, sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        action = "unchanged" if self.resources.get(logical_id) == digest else "created"
        self.resources[logical_id] = digest
        return {"action": action, "receipt_sha256": digest}


class ManagedApplyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = load_manifest(SERVER / "deploy" / "recall-core.plan.example.json")
        self.plan_sha256 = preview(self.manifest)["plan_sha256"]

    def approvals(self, **updates) -> dict:
        value = {
            "schema_version": 1,
            "plan_sha256": self.plan_sha256,
            "infrastructure": {
                "provider-billing": {"approved": True, "selection": "starter"},
                "provider-region": {"approved": True, "selection": "oregon"},
                "provider-authorization": {"approved": True},
                "tailnet-route": {"approved": True},
            },
            "writer-cutover": {"approved": False},
        }
        value.update(updates)
        return value

    def write_approvals(self, value: dict, mode: int = 0o600) -> Path:
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "approvals.json"
        path.write_text(json.dumps(value)); os.chmod(path, mode)
        return path

    def test_approval_file_is_private_closed_and_bound_to_exact_plan(self) -> None:
        loaded = load_approvals(self.write_approvals(self.approvals()), self.plan_sha256)
        self.assertFalse(loaded["writer-cutover"]["approved"])
        status = approval_status(loaded)
        self.assertEqual(status["pending_gates"], ["writer-cutover"])
        self.assertEqual(status["mutation_count"], 0)
        for mutate in (
            lambda value: value.update({"plan_sha256": "0" * 64}),
            lambda value: value.update({"token": "not-allowed"}),
            lambda value: value["infrastructure"]["provider-region"].update({"selection": ""}),
        ):
            value = self.approvals(); mutate(value)
            with self.assertRaises(ApprovalError):
                load_approvals(self.write_approvals(value), self.plan_sha256)
        with self.assertRaises(ApprovalError):
            load_approvals(self.write_approvals(self.approvals(), 0o644), self.plan_sha256)

    def test_missing_infrastructure_approval_makes_zero_adapter_calls(self) -> None:
        value = self.approvals()
        value["infrastructure"]["provider-billing"]["approved"] = False
        approvals = load_approvals(self.write_approvals(value), self.plan_sha256)
        adapters = {key: FakeAdapter() for key in ("database", "service", "network")}
        with self.assertRaises(ApprovalError) as raised:
            reconcile_infrastructure(self.manifest, approvals, adapters)
        self.assertEqual(raised.exception.code, "infrastructure_approval_required")
        self.assertEqual(sum(adapter.calls for adapter in adapters.values()), 0)

    def test_two_applies_converge_without_duplicates_or_rendered_values(self) -> None:
        approvals = load_approvals(
            self.write_approvals(self.approvals()), self.plan_sha256,
        )
        adapters = {key: FakeAdapter() for key in ("database", "service", "network")}
        first = reconcile_infrastructure(self.manifest, approvals, adapters)
        second = reconcile_infrastructure(self.manifest, approvals, adapters)
        self.assertEqual(first["actions"], {"created": 3, "unchanged": 0})
        self.assertEqual(second["actions"], {"created": 0, "unchanged": 3})
        self.assertEqual(first["resource_count"], second["resource_count"])
        self.assertEqual(sum(len(adapter.resources) for adapter in adapters.values()), 3)
        self.assertEqual(second["status"], "writer_cutover_approval_required")
        rendered = json.dumps([first, second], sort_keys=True)
        for forbidden in ("starter", "oregon", "secret://", "approval://"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
