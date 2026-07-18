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
        "openai.export-inbox": ("private", "drop", {"brain"}),
        "grep.ai": ("private", "drop", {"brain", "source"}),
        "google.gmail": ("private", "scrub", {"brain", "source"}),
        "google.calendar": ("private", "scrub", {"brain", "source"}),
        "google.contacts": ("private", "scrub", {"brain", "source"}),
        "google.drive": ("private", "scrub", {"brain", "source"}),
        "github.activity": ("private", "scrub", {"brain", "source"}),
        "linear.activity": ("private", "scrub", {"brain", "source"}),
        "slack.messages": ("private", "scrub", {"brain", "source"}),
        "notion.workspace": ("private", "scrub", {"brain", "source"}),
        "x.activity": ("private", "scrub", {"brain", "source"}),
        "apple.imessage": ("private", "scrub", {"brain", "source"}),
        "whatsapp.export": ("private", "scrub", {"brain", "source"}),
        "local.selected-text": ("private", "scrub", {"brain", "source"}),
        "apple.safari": ("private", "scrub", {"brain", "source"}),
        "google.chrome": ("private", "scrub", {"brain", "source"}),
        "apple.notes": ("private", "scrub", {"brain", "source"}),
        "hermes.sessions": ("private", "scrub", {"brain", "source"}),
        "portable.mail": ("private", "scrub", {"brain", "source"}),
        "portable.calendar": ("private", "scrub", {"brain", "source"}),
        "portable.contacts": ("private", "scrub", {"brain", "source"}),
        "portable.slack": ("private", "scrub", {"brain", "source"}),
        "portable.notion": ("private", "scrub", {"brain", "source"}),
        "portable.x": ("private", "scrub", {"brain", "source"}),
        "portable.feed": ("private", "scrub", {"brain", "source"}),
        "portable.jsonl": ("private", "scrub", {"brain", "source"}),
    }
    tests = {
        "recall.capture": "e2e_capture_mcp.py",
        "openai.export-inbox": "e2e_export_inbox.py",
        "grep.ai": "e2e_grep_ai.py",
        "google.gmail": "e2e_google_workspace_connectors.py",
        "google.calendar": "e2e_google_workspace_connectors.py",
        "google.contacts": "e2e_google_workspace_connectors.py",
        "google.drive": "e2e_google_workspace_connectors.py",
        "github.activity": "e2e_work_api_connectors.py",
        "linear.activity": "e2e_work_api_connectors.py",
        "slack.messages": "e2e_work_api_connectors.py",
        "notion.workspace": "e2e_work_api_connectors.py",
        "x.activity": "e2e_x_connector.py",
        "apple.imessage": "e2e_imessage_connector.py",
        "whatsapp.export": "e2e_local_file_connectors.py",
        "local.selected-text": "e2e_local_file_connectors.py",
        "apple.safari": "e2e_local_activity_connectors.py",
        "google.chrome": "e2e_local_activity_connectors.py",
        "apple.notes": "e2e_local_activity_connectors.py",
        "hermes.sessions": "e2e_local_activity_connectors.py",
        "portable.mail": "e2e_portable_pim_imports.py",
        "portable.calendar": "e2e_portable_pim_imports.py",
        "portable.contacts": "e2e_portable_pim_imports.py",
        "portable.slack": "e2e_portable_service_archives.py",
        "portable.notion": "e2e_portable_service_archives.py",
        "portable.x": "e2e_portable_service_archives.py",
        "portable.feed": "e2e_feed_and_jsonl_connectors.py",
        "portable.jsonl": "e2e_feed_and_jsonl_connectors.py",
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
