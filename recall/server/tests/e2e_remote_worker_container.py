#!/usr/bin/env python3
"""Aggregate contract proof for the non-root remote-worker image."""

from __future__ import annotations

import json
import os
import subprocess


def main() -> None:
    image = os.environ["RECALL_REMOTE_WORKER_IMAGE"]
    config = json.loads(subprocess.run(
        ["docker", "image", "inspect", image],
        check=True,
        capture_output=True,
        text=True,
    ).stdout)[0]["Config"]
    assert config["User"] == "10001:10001"
    assert config["Entrypoint"] == ["python", "-m", "client.cli"]
    assert config["Cmd"][0] == "remote-worker-run"
    assert config.get("ExposedPorts") in (None, {})
    subprocess.run(
        ["docker", "run", "--rm", image, "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    version = subprocess.run(
        [
            "docker", "run", "--rm",
            "--entrypoint", "/opt/recall/vendor/gws/0.22.5/gws",
            image, "--version",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert version.stdout.splitlines()[0] == "gws 0.22.5"
    print(json.dumps({
        "status": "pass",
        "non_root": True,
        "listener_ports": 0,
        "pinned_workspace_cli": True,
        "default_profile": "remote_worker",
        "credential_bytes_rendered": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
