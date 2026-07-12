#!/usr/bin/env python3
"""Verify frozen bytes and aggregate counts without printing holdout semantics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def main() -> None:
    root = Path(__file__).with_name("eval_v1")
    manifest = json.loads((root / "manifest.json").read_text())
    for name, expected in manifest["sha256"].items():
        actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"freeze mismatch: {name}")
    if manifest["counts"]["semantic-paraphrase"] < 20 or manifest["counts"]["negative"] < 15:
        raise SystemExit("stratum floor unmet")
    if manifest["counts"]["source-isolation"] < 10:
        raise SystemExit("isolation matrix floor unmet")
    print(json.dumps({"status": "pass", "counts": manifest["counts"], "holdout_sha256": manifest["sha256"]["queries-holdout.jsonl"]}, sort_keys=True))


if __name__ == "__main__":
    main()
