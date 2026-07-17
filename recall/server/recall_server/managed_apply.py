from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path
from typing import Any, Protocol

from .deployment import preview, validate_manifest


MAX_APPROVAL_BYTES = 16 * 1024
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
SELECTION_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,62}\Z")
INFRASTRUCTURE_GATES = (
    "provider-billing", "provider-region", "provider-authorization", "tailnet-route",
)


class ApprovalError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ProviderAdapter(Protocol):
    def ensure(self, logical_id: str, desired: dict[str, Any]) -> dict[str, str]: ...


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ApprovalError("approval_invalid")
        result[key] = value
    return result


def _closed(value: object, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ApprovalError("approval_invalid")
    return value


def _read_private(path: Path) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as error:
        raise ApprovalError("approval_unavailable") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o077
            or not 0 < metadata.st_size <= MAX_APPROVAL_BYTES
        ):
            raise ApprovalError("approval_file_unsafe")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = -1
            return source.read(MAX_APPROVAL_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def load_approvals(path: Path, plan_sha256: str) -> dict[str, Any]:
    payload = _read_private(path)
    if len(payload) > MAX_APPROVAL_BYTES:
        raise ApprovalError("approval_file_unsafe")
    try:
        value = json.loads(payload, object_pairs_hook=_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ApprovalError("approval_invalid") from error
    root = _closed(
        value, {"schema_version", "plan_sha256", "infrastructure", "writer-cutover"},
    )
    if root["schema_version"] != 1 or type(root["schema_version"]) is not int:
        raise ApprovalError("approval_invalid")
    if (
        not isinstance(root["plan_sha256"], str)
        or not SHA256_RE.fullmatch(root["plan_sha256"])
        or root["plan_sha256"] != plan_sha256
    ):
        raise ApprovalError("approval_plan_mismatch")
    infrastructure = _closed(root["infrastructure"], set(INFRASTRUCTURE_GATES))
    for gate in ("provider-billing", "provider-region"):
        approval = _closed(infrastructure[gate], {"approved", "selection"})
        if type(approval["approved"]) is not bool or not isinstance(approval["selection"], str):
            raise ApprovalError("approval_invalid")
        if not SELECTION_RE.fullmatch(approval["selection"]):
            raise ApprovalError("approval_invalid")
    for gate in ("provider-authorization", "tailnet-route"):
        approval = _closed(infrastructure[gate], {"approved"})
        if type(approval["approved"]) is not bool:
            raise ApprovalError("approval_invalid")
    cutover = _closed(root["writer-cutover"], {"approved"})
    if type(cutover["approved"]) is not bool:
        raise ApprovalError("approval_invalid")
    return root


def _adapter_result(value: object) -> dict[str, str]:
    result = _closed(value, {"action", "receipt_sha256"})
    if result["action"] not in {"created", "unchanged"}:
        raise ApprovalError("provider_result_invalid")
    if not isinstance(result["receipt_sha256"], str) or not SHA256_RE.fullmatch(
        result["receipt_sha256"],
    ):
        raise ApprovalError("provider_result_invalid")
    return result


def approval_status(approvals: dict[str, Any]) -> dict[str, Any]:
    infrastructure = approvals.get("infrastructure") or {}
    pending = [
        gate for gate in INFRASTRUCTURE_GATES
        if (infrastructure.get(gate) or {}).get("approved") is not True
    ]
    if approvals.get("writer-cutover", {}).get("approved") is not True:
        pending.append("writer-cutover")
    return {
        "schema_version": 1,
        "status": "approval_required" if pending else "approved",
        "plan_sha256": approvals["plan_sha256"],
        "pending_gates": pending,
        "mutation_count": 0,
        "network_calls": 0,
        "credential_values_rendered": 0,
    }


def reconcile_infrastructure(
    manifest: dict[str, Any], approvals: dict[str, Any],
    adapters: dict[str, ProviderAdapter],
) -> dict[str, Any]:
    manifest = validate_manifest(manifest)
    if approvals.get("plan_sha256") != preview(manifest)["plan_sha256"]:
        raise ApprovalError("approval_plan_mismatch")
    infrastructure = approvals.get("infrastructure") or {}
    if any((infrastructure.get(gate) or {}).get("approved") is not True for gate in INFRASTRUCTURE_GATES):
        raise ApprovalError("infrastructure_approval_required")
    if set(adapters) != {"database", "service", "network"}:
        raise ApprovalError("provider_adapter_invalid")
    region = infrastructure["provider-region"]["selection"]
    billing = infrastructure["provider-billing"]["selection"]
    desired = {
        "database": {
            **manifest["database"], "region": region, "billing_plan": billing,
        },
        "service": {
            **manifest["service"], "image": manifest["image"],
            "region": region, "billing_plan": billing,
        },
        "network": {
            **manifest["network"], "provider_authorized": True, "route_approved": True,
        },
    }
    results = [
        _adapter_result(adapters[kind].ensure(f"recall-core-{kind}", desired[kind]))
        for kind in ("database", "service", "network")
    ]
    actions = {
        action: sum(result["action"] == action for result in results)
        for action in ("created", "unchanged")
    }
    return {
        "schema_version": 1,
        "status": (
            "infrastructure_ready_for_cutover"
            if approvals["writer-cutover"]["approved"]
            else "writer_cutover_approval_required"
        ),
        "plan_sha256": approvals["plan_sha256"],
        "resource_count": len(results),
        "actions": actions,
        "resource_receipts": [result["receipt_sha256"] for result in results],
        "credential_values_rendered": 0,
        "source_reads": 0,
    }
