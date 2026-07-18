#!/usr/bin/env python3
"""Aggregate synthetic E2E for the bundled remote API foundation."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "recall"))

from connectors.registry import ConnectorDefinitionV3, definition
from connectors.remote_api import BoundedJsonRail, RemoteOperation


REMOTE_IDS = (
    "github.activity",
    "linear.activity",
    "notion.workspace",
    "slack.messages",
    "x.activity",
)


class Response:
    status = 200
    headers = {
        "Content-Type": "application/json",
        "Content-Length": "28",
    }

    def __init__(self):
        self.body = io.BytesIO(b'{"items":[],"next":null}')

    def read(self, size: int = -1) -> bytes:
        return self.body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def main() -> None:
    if not os.environ.get("RECALL_DATABASE_URL"):
        raise RuntimeError("RECALL_DATABASE_URL is required")
    manifests = [definition(connector_id) for connector_id in REMOTE_IDS]
    assert all(isinstance(item, ConnectorDefinitionV3) for item in manifests)
    assert all(item.placement.execution == "remote_worker" for item in manifests)
    assert all(item.authority_slots == ("brain", "source") for item in manifests)

    calls = []

    def opener(request, *, timeout):
        calls.append((request.full_url, request.get_method(), timeout))
        return Response()

    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        os.chmod(root, 0o700)
        authority = root / "authority"
        authority.write_text("synthetic-authority")
        os.chmod(authority, 0o600)
        rail = BoundedJsonRail(
            origin="https://api.example.invalid",
            authority_path=authority,
            authorization_scheme="Bearer",
            operations={
                "items.list": RemoteOperation(
                    method="GET",
                    path_template="/v1/items",
                    path_fields=(),
                    query_fields=("cursor",),
                ),
            },
            opener=opener,
        )
        value = rail.request("items.list", query={"cursor": "synthetic"})
    assert value == {"items": [], "next": None}
    assert calls == [(
        "https://api.example.invalid/v1/items?cursor=synthetic",
        "GET",
        30,
    )]
    print(json.dumps({
        "status": "pass",
        "remote_manifests": len(manifests),
        "bounded_operation_calls": len(calls),
        "credential_bytes_rendered": False,
        "provider_payloads_rendered": False,
        "live_grants": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
