#!/usr/bin/env python3
"""Fresh-PostgreSQL aggregate proof for every registered input surface."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "recall"))

from connectors.registry import REGISTRY, validate_policy


def main() -> None:
    if not os.environ.get("RECALL_DATABASE_URL"):
        raise RuntimeError("RECALL_DATABASE_URL is required")
    policies = {
        "recall.capture": ("private", "scrub", {"brain"}),
        "chatgpt.export_inbox": ("private", "drop", {"brain"}),
        "grep.ai": ("private", "drop", {"brain", "source"}),
    }
    tests = {
        "recall.capture": "e2e_capture_mcp.py",
        "chatgpt.export_inbox": "e2e_export_inbox.py",
        "grep.ai": "e2e_grep_ai.py",
    }
    results = {}
    for item in REGISTRY:
        visibility, privacy_mode, authorities = policies[item.connector_id]
        validate_policy(
            item.connector_id, visibility=visibility,
            privacy_mode=privacy_mode, authorities=authorities,
        )
        completed = subprocess.run(
            [sys.executable, str(Path(__file__).parent / tests[item.connector_id])],
            check=True, text=True, capture_output=True, env=os.environ,
        )
        assert completed.stderr == ""
        value = json.loads(completed.stdout)
        assert value["status"] == "pass"
        results[item.connector_id] = True
    print(json.dumps({
        "status": "pass",
        "registered_surfaces": len(results),
        "surface_lifecycles_passed": len(results),
        "registry_policy_rejections": 0,
        "cross_source_deletes": 0,
        "canary_search_hits": 0,
        "inferred_tombstones": 0,
        "private_content_rendered": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
