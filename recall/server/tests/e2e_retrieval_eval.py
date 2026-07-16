#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SERVER))

from evals.runner import evaluate_store, load_jsonl, load_synthetic_corpus, public_report, safe_pins, write_json  # noqa: E402
from recall_server.db import BrainStore  # noqa: E402


FIXTURES = ROOT / "tests/central_brain/retrieval_eval_v2"


def request(base: str, path: str, body: dict) -> dict:
    encoded = json.dumps(body).encode()
    value = urllib.request.Request(
        base + path,
        data=encoded,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(value, timeout=5) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise AssertionError(f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:200]}") from None


class HttpStore:
    def __init__(self, base: str):
        self.base = base

    def search(self, query, filters, limit, authorized_source):
        if authorized_source is not None:
            raise AssertionError("the unauthenticated E2E transport cannot emulate a source-scoped credential")
        return request(self.base, "/v1/search", {"query": query, "filters": filters, "limit": limit})

    def show(self, target, *, around, authorized_source):
        if authorized_source is not None:
            raise AssertionError("the unauthenticated E2E transport cannot emulate a source-scoped credential")
        return request(self.base, "/v1/show", {"target": target, "around": around})


def main() -> None:
    dsn = os.environ["RECALL_DATABASE_URL"]
    port = int(os.environ.get("RECALL_RETRIEVAL_E2E_PORT", "18791"))
    base = f"http://127.0.0.1:{port}"
    store = BrainStore(dsn)
    store.migrate()
    with store.connect() as connection:
        connection.execute(
            "TRUNCATE session_export_cursors,chunks,entities,items,sessions,projection_watermarks,"
            "source_events,ingest_batches,source_profiles,source_grants,sources,dead_letters,audit_events "
            "RESTART IDENTITY CASCADE"
        )
    ingest = load_synthetic_corpus(store, load_jsonl(FIXTURES / "corpus.jsonl"))
    # Source-scoped auth cases use BrainStore directly because this E2E server deliberately runs
    # without fabricated credentials. Every other case traverses the real HTTP boundary.
    all_cases = load_jsonl(FIXTURES / "queries-dev.jsonl") + load_jsonl(FIXTURES / "queries-holdout.jsonl")
    http_cases = [case for case in all_cases if case["stratum"] != "privacy-auth"]

    with tempfile.TemporaryDirectory(prefix="recall-retrieval-eval-") as temporary:
        log_path = Path(temporary) / "server.log"
        with log_path.open("w") as log:
            environment = os.environ | {
                "PYTHONPATH": str(SERVER),
                "RECALL_DATABASE_URL": dsn,
                "RECALL_PORT": str(port),
            }
            process = subprocess.Popen(
                [sys.executable, "-m", "recall_server.app"],
                env=environment,
                stdout=log,
                stderr=log,
            )
            try:
                for _ in range(50):
                    try:
                        with urllib.request.urlopen(base + "/healthz", timeout=1) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise AssertionError("server did not become healthy")

                first_http = evaluate_store(HttpStore(base), http_cases, source_routing_supported=True)
                second_http = evaluate_store(HttpStore(base), http_cases, source_routing_supported=True)
            finally:
                process.terminate()
                process.wait(timeout=5)

    first = evaluate_store(store, all_cases, source_routing_supported=True)
    second = evaluate_store(store, all_cases, source_routing_supported=True)
    assert first_http["rankings"] == second_http["rankings"], "retrieval rankings changed between runs"
    assert first["rankings"] == second["rankings"], "direct retrieval rankings changed between runs"
    assert first["strata"]["exact-identifier"]["hit@1"] == 1.0
    assert first["behavior"]["session_reconstruction_accuracy"] == 1.0
    assert first["behavior"]["deletion_resurrection_rate"] == 0.0
    assert ingest["deduplication_accuracy"] == 1.0
    assert first["aggregate"]["unauthorized_hit_rate"] == 0.0
    assert first["strata"]["source-routed"]["backend_error_rate"] == 0.0
    assert first["strata"]["source-routed"]["recall@5"] == 1.0
    assert all(
        metrics["backend_error_rate"] == 0.0
        for stratum, metrics in first["strata"].items()
        if stratum != "source-routed"
    )
    numeric = first["numeric_values"]
    assert numeric and all(math.isfinite(value) for value in numeric)

    first["behavior"]["deduplication_accuracy"] = ingest["deduplication_accuracy"]
    first["pins"] = safe_pins(ROOT.parent, FIXTURES / "corpus.jsonl", FIXTURES / "queries-holdout.jsonl")
    output_path = os.environ.get("RECALL_EVAL_OUTPUT")
    if output_path:
        write_json(Path(output_path), public_report(first, aggregate_only=True), private=False)
    print(json.dumps({
        "status": "pass",
        "queries": len(all_cases),
        "exact_hit_at_1": first["strata"]["exact-identifier"]["hit@1"],
        "semantic_recall_at_5": first["strata"]["semantic-paraphrase"]["recall@5"],
        "source_routing_backend_error_rate": first["strata"]["source-routed"]["backend_error_rate"],
        "session_reconstruction_accuracy": first["behavior"]["session_reconstruction_accuracy"],
        "deduplication_accuracy": ingest["deduplication_accuracy"],
        "unauthorized_hit_rate": first["aggregate"]["unauthorized_hit_rate"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
