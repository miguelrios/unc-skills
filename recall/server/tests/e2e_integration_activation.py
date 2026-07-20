#!/usr/bin/env python3
"""Process-boundary lifecycle proof for four activation placements."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from connectors.registry import REGISTRY


ROOT = Path(__file__).resolve().parents[2]
PROFILES = {
    "apple.imessage": {
        "authorities": {
            "brain": {"kind": "file", "path": "/private/brain-token.json"},
        },
        "selectors": {"chat_ids": ["synthetic-chat"], "date_min": None},
    },
    "google.gmail": {
        "authorities": {
            "brain": {"kind": "file", "path": "/private/brain-token.json"},
            "source": {
                "kind": "keychain",
                "service": "recall.source",
                "account": "gmail",
            },
        },
        "selectors": {
            "include_spam_trash": False,
            "label_ids": ["INBOX"],
            "own_addresses": ["synthetic@example.invalid"],
        },
    },
    "portable.jsonl": {
        "authorities": {
            "brain": {"kind": "file", "path": "/private/brain-token.json"},
        },
        "selectors": {"max_depth": 4, "root": "/private/selected-export"},
    },
    "custom.webhook": {"authorities": {}, "selectors": {}},
}


def run(*arguments: str) -> dict:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(ROOT)
    completed = subprocess.run(
        [sys.executable, "-m", "client.cli", *arguments],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stderr == ""
    return json.loads(completed.stdout)


def main() -> None:
    catalog = run("integration-activation-catalog")
    assert len(catalog["connectors"]) == len(REGISTRY)
    assert {
        "local.claude-code",
        "local.codex",
        "local.cowork",
        "local.chatgpt-export",
    }.issubset({item["connector_id"] for item in catalog["connectors"]})
    assert catalog["credential_reads"] == 0
    transitions = (
        ("enable", "enabled"),
        ("pause", "paused"),
        ("resume", "enabled"),
        ("revoke", "revoked"),
        ("uninstall", "uninstalled"),
    )
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        root.chmod(0o700)
        for connector_id, profile in PROFILES.items():
            config = root / f"{connector_id}.json"
            state = root / f"{connector_id}.db"
            config.write_text(json.dumps({
                "schema_version": 1,
                "connector_id": connector_id,
                "source_id": f"synthetic:{connector_id}",
                "principal_id": "synthetic-owner",
                "privacy_mode": "scrub",
                "authority_references": profile["authorities"],
                "selectors": profile["selectors"],
            }))
            config.chmod(0o600)

            preview = run(
                "integration-activation-preview", "--config", str(config),
            )
            rendered = json.dumps(preview, sort_keys=True)
            assert preview["credential_reads"] == 0
            assert preview["source_reads"] == 0
            assert preview["network_requests"] == 0
            assert preview["writes"] == 0
            assert "/private/" not in rendered
            assert "synthetic-owner" not in rendered
            assert f"synthetic:{connector_id}" not in rendered

            configured = run(
                "integration-activation-configure",
                "--config", str(config),
                "--state", str(state),
            )
            assert configured["state"] == "configured"
            assert not configured["replay"]
            replay = run(
                "integration-activation-configure",
                "--config", str(config),
                "--state", str(state),
            )
            assert replay["replay"] and replay["revision"] == configured["revision"]

            for action, expected in transitions:
                changed = run(
                    "integration-activation-transition",
                    "--state", str(state),
                    "--connector-id", connector_id,
                    "--action", action,
                )
                assert changed["state"] == expected and not changed["replay"]
                replay = run(
                    "integration-activation-transition",
                    "--state", str(state),
                    "--connector-id", connector_id,
                    "--action", action,
                )
                assert replay["state"] == expected and replay["replay"]
                assert replay["revision"] == changed["revision"]

            status = run(
                "integration-activation-status",
                "--state", str(state),
                "--connector-id", connector_id,
            )
            assert status["state"] == "uninstalled"
            assert state.stat().st_mode & 0o777 == 0o600

    print(json.dumps({
        "status": "pass",
        "activation_catalog_entries": len(catalog["connectors"]),
        "profiles_completed": len(PROFILES),
        "lifecycle_transitions": len(PROFILES) * len(transitions),
        "idempotent_replays": len(PROFILES) * (len(transitions) + 1),
        "credential_reads": 0,
        "source_reads": 0,
        "network_requests": 0,
        "private_values_rendered": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
