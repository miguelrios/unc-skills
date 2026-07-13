#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[3]
SERVER = ROOT / "recall/server"
sys.path.insert(0, str(SERVER))

from recall_server.db import BrainStore, SearchDeadlineExceeded
from recall_server.projectors import canonical_json, project


def make_envelope(native_id: str, content, *, source="codex:linux", parent="session-1", kind="message", occurred="2026-07-12T20:00:00Z", harness="codex"):
    value = {
        "schema_version": 1,
        "source_id": source,
        "native_id": native_id,
        "native_parent_id": parent,
        "kind": kind,
        "occurred_at": occurred,
        "observed_at": "2026-07-12T20:00:02Z",
        "principal_id": "owner",
        "visibility": "private",
        "content_type": "application/json",
        "content": content,
        "provenance": {
            "harness": harness,
            "original_path": f"/evidence/{source.replace(':', '-')}/{parent}.jsonl",
            "cwd": "/workspace/recall-e2e",
            "branch": "test/remote-recall",
        },
    }
    value["content_sha256"] = hashlib.sha256(canonical_json(content)).hexdigest()
    return value


def request(base: str, method: str, path: str, body=None, key=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if key is not None:
        headers["Idempotency-Key"] = key
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def load_fixture_records(path: Path, harness: str):
    records = []
    for line_number, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        try:
            content = json.loads(line)
        except json.JSONDecodeError:
            continue
        env = make_envelope(f"{path.stem}:{line_number}", content, source=f"{harness}:fixture", parent=f"{harness}-fixture", kind="transcript_record", harness=harness)
        expected, _ = project(env, 1)
        records.append((env, expected))
    if not records or not any(expected for _, expected in records):
        raise AssertionError(f"no projectable record: {path}")
    return records


def main() -> None:
    dsn = os.environ["RECALL_DATABASE_URL"]
    port = int(os.environ.get("RECALL_E2E_PORT", "18788"))
    base = f"http://127.0.0.1:{port}"
    store = BrainStore(dsn)
    store.migrate()
    with store.connect() as conn:
        conn.execute("TRUNCATE chunks,items,sessions,projection_watermarks,source_events,ingest_batches,source_grants,sources,dead_letters,audit_events RESTART IDENTITY CASCADE")

    with tempfile.TemporaryDirectory(prefix="recall-c1-e2e-") as tmp:
        log_path = Path(tmp) / "server.log"
        log = log_path.open("w")
        env = os.environ | {"PYTHONPATH": str(SERVER), "RECALL_DATABASE_URL": dsn, "RECALL_PORT": str(port)}
        process = subprocess.Popen([sys.executable, "-m", "recall_server.app"], env=env, stdout=log, stderr=log)
        try:
            for _ in range(50):
                try:
                    if request(base, "GET", "/healthz")[0] == 200:
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                raise AssertionError("server did not become healthy")

            first = make_envelope("session-1:turn-1", {"role": "user", "text": "quartz decision"})
            status, ack = request(base, "POST", "/v1/ingest/batches", {"events": [first]}, "batch-first")
            assert status == 201 and ack["inserted"] == 1 and not ack["replay"]

            # Remote retrieval preserves exact provenance, WHY legs, and resolvable receipts.
            status, searched = request(base, "POST", "/v1/search", {
                "query": "quartz decision",
                "filters": {"cwd": "recall-e2e", "branch": "remote-recall", "harness": "codex"},
                "limit": 5,
            })
            assert status == 200 and searched["results"], (status, searched)
            first_hit = searched["results"][0]
            assert first_hit["path"] == "/evidence/codex-linux/session-1.jsonl"
            assert first_hit["receipt"].startswith("recall://codex:linux/session-1:turn-1?rev=1#item=")
            assert first_hit["tier"] >= 1 and first_hit["legs"]
            diagnostics = searched["diagnostics"]
            assert diagnostics["deadline_ms"] < 500 and diagnostics["elapsed_ms"] >= 0
            assert diagnostics["legs"] and all(set(leg) == {"leg", "elapsed_ms", "n_results", "timed_out"} for leg in diagnostics["legs"])
            assert "quartz" not in json.dumps(diagnostics).lower()
            assert request(base, "GET", "/v1/receipts/resolve?" + urllib.parse.urlencode({"receipt": first_hit["receipt"]}))[0] == 200

            bounded_started = time.monotonic()
            with store.connect() as deadline_conn:
                try:
                    with deadline_conn.transaction():
                        store._execute_bounded(deadline_conn, "SELECT pg_sleep(1)", [], time.monotonic() + 0.03)
                except SearchDeadlineExceeded:
                    pass
                else:
                    raise AssertionError("adversarial query escaped the server deadline")
            assert time.monotonic() - bounded_started < 0.25

            entity_marker = "deadbeef-1234-1234-1234-123456789abc"
            entity_env = make_envelope("session-entity:turn-1", {"role": "tool", "text": entity_marker}, parent="session-entity")
            entity_status, entity_ack = request(base, "POST", "/v1/ingest/batches", {"events": [entity_env]}, "batch-entity")
            assert entity_status == 201, (entity_status, entity_ack)
            entity_search = store.search("which trace used " + entity_marker, {}, 5)
            assert entity_search["results"][0]["session_native_id"] == "session-entity"
            assert "entity" in entity_search["results"][0]["legs"]
            with store.connect() as conn:
                projected = conn.execute(
                    "SELECT kind,value,normalized FROM entities ORDER BY kind,value"
                ).fetchall()
                assert {("uuid", entity_marker, entity_marker), ("uuid", "deadbeef", "deadbeef")} <= {
                    (row["kind"], row["value"], row["normalized"]) for row in projected
                }
                conn.execute("DELETE FROM entities")
                conn.execute("DELETE FROM projection_backfills WHERE name='entities-v2'")
            first_backfill = store.backfill_entities(batch_size=1, max_batches=1)
            assert first_backfill["items_scanned"] == 1 and not first_backfill["completed"]
            final_backfill = store.backfill_entities(batch_size=3)
            assert final_backfill["completed"] and final_backfill["last_item_id"] == final_backfill["target_item_id"]
            assert store.backfill_entities(batch_size=3)["items_scanned"] == 0
            entity_search = store.search("which trace used " + entity_marker, {}, 5)
            assert entity_search["results"][0]["session_native_id"] == "session-entity"

            status, shown = request(base, "POST", "/v1/show", {
                "target": first_hit["path"], "tail": 5, "prompts": False, "around": None,
            })
            assert status == 200 and any("quartz decision" in chunk["text"] for chunk in shown["chunks"])

            status, related = request(base, "POST", "/v1/related", {
                "cwd": "/workspace/recall-e2e", "branch": "test/remote-recall", "limit": 5,
                "mains_only": True, "fast": False,
            })
            assert status == 200 and related["results"][0]["path"] == first_hit["path"]

            status, remote_doctor = request(base, "GET", "/v1/doctor")
            assert status == 200 and remote_doctor["status"] == "ok" and remote_doctor["projection_lag"] == 0
            status, replay = request(base, "POST", "/v1/ingest/batches", {"events": [first]}, "batch-first")
            assert status == 200 and replay["replay"] and replay["batch_id"] == ack["batch_id"]

            changed = make_envelope("session-1:turn-1", {"role": "user", "text": "quartz decision revised"})
            status, conflict = request(base, "POST", "/v1/ingest/batches", {"events": [changed]}, "batch-first")
            assert status == 409 and "different request" in conflict["error"]
            status, revision_ack = request(base, "POST", "/v1/ingest/batches", {"events": [changed]}, "batch-revision")
            assert status == 201 and revision_ack["receipts"][0].endswith("?rev=2")

            # Same native ID in different sources remains two independently resolvable records.
            collision_a = make_envelope("shared-native:1", {"text": "alpha evidence"}, source="source:alpha", parent="shared")
            collision_b = make_envelope("shared-native:1", {"text": "beta evidence"}, source="source:beta", parent="shared")
            _, alpha_ack = request(base, "POST", "/v1/ingest/batches", {"events": [collision_a]}, "collision-alpha")
            _, beta_ack = request(base, "POST", "/v1/ingest/batches", {"events": [collision_b]}, "collision-beta")
            _, alpha_resolved = request(base, "GET", "/v1/receipts/resolve?" + urllib.parse.urlencode({"receipt": alpha_ack["receipts"][0]}))
            _, beta_resolved = request(base, "GET", "/v1/receipts/resolve?" + urllib.parse.urlencode({"receipt": beta_ack["receipts"][0]}))
            assert alpha_resolved["event"]["source_id"] == "source:alpha" and "alpha evidence" in json.dumps(alpha_resolved)
            assert beta_resolved["event"]["source_id"] == "source:beta" and "beta evidence" in json.dumps(beta_resolved)
            scoped_alpha = store.search("alpha evidence", {}, 5, authorized_source="source:alpha")
            assert scoped_alpha["results"] and {hit["source_id"] for hit in scoped_alpha["results"]} == {"source:alpha"}
            assert store.search("beta evidence", {}, 5, authorized_source="source:alpha")["results"] == []
            assert store.show(beta_ack["receipts"][0], authorized_source="source:alpha") is None
            assert store.doctor("source:alpha")["source_events"] == 1

            # Concurrent different batches carrying the same event converge on one event row.
            raced = make_envelope("session-race:turn-1", {"role": "tool", "text": "race-safe marker"}, parent="session-race")
            def send_race(i):
                return request(base, "POST", "/v1/ingest/batches", {"events": [raced]}, f"race-{i}")
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                race_results = list(pool.map(send_race, range(16)))
            assert all(status == 201 for status, _ in race_results), race_results

            # The same idempotency key is serialized: one commit, all other callers replay it.
            same_key = make_envelope("session-same-key:turn-1", {"text": "one durable batch"}, parent="session-same-key")
            def send_same_key(_i):
                return request(base, "POST", "/v1/ingest/batches", {"events": [same_key]}, "same-key-race")
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                same_key_results = list(pool.map(send_same_key, range(16)))
            assert sorted(status for status, _ in same_key_results).count(201) == 1, same_key_results
            assert sorted(status for status, _ in same_key_results).count(200) == 15, same_key_results
            assert len({body["batch_id"] for _, body in same_key_results}) == 1

            # A source ID is never silently transferred to a different principal.
            takeover = make_envelope("takeover:1", {"text": "should fail"})
            takeover["principal_id"] = "intruder"
            assert request(base, "POST", "/v1/ingest/batches", {"events": [takeover]}, "source-takeover")[0] == 400

            # Ordered session projection is based on source time, not delivery order.
            later = make_envelope("session-order:turn-2", {"text": "later"}, parent="session-order", occurred="2026-07-12T20:00:02Z")
            earlier = make_envelope("session-order:turn-1", {"text": "earlier"}, parent="session-order", occurred="2026-07-12T20:00:01Z")
            assert request(base, "POST", "/v1/ingest/batches", {"events": [later, earlier]}, "batch-order")[0] == 201

            # Commit survives a client that disconnects before reading the acknowledgement.
            disconnect = make_envelope("session-disconnect:turn-1", {"text": "durable after disconnect"}, parent="session-disconnect")
            payload = json.dumps({"events": [disconnect]}).encode()
            raw = (f"POST /v1/ingest/batches HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nIdempotency-Key: batch-disconnect\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n").encode() + payload
            sock = socket.create_connection(("127.0.0.1", port)); sock.sendall(raw); sock.close()
            for _ in range(30):
                status, disconnected_ack = request(base, "POST", "/v1/ingest/batches", {"events": [disconnect]}, "batch-disconnect")
                if status == 200 and disconnected_ack["replay"]:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("disconnected commit was not replayable")

            # Legacy Claude/Codex readers produce byte-equivalent sanitized item text.
            fixtures = ROOT / "recall/tests/fixtures"
            fixture_receipts = []
            for harness, filename in (("claude", "claude_sample.jsonl"), ("codex", "codex_rollout.jsonl")):
                fixture_records = load_fixture_records(fixtures / filename, harness)
                status, fixture_ack = request(base, "POST", "/v1/ingest/batches", {"events": [env for env, _ in fixture_records]}, f"fixture-{harness}")
                assert status == 201
                for receipt, (_fixture_env, expected) in zip(fixture_ack["receipts"], fixture_records, strict=True):
                    fixture_receipts.append((receipt, expected))
                    status, resolved = request(base, "GET", "/v1/receipts/resolve?" + urllib.parse.urlencode({"receipt": receipt}))
                    assert status == 200
                    assert [item["text_redacted"] for item in resolved["items"]] == [item["text_redacted"] for item in expected]

            # Secrets remain canonical in raw evidence but never enter projections/API/audit/logs.
            secret = "supersecretvalue123456"
            secret_env = make_envelope("session-secret:turn-1", {"text": f"safe\nAuthorization={secret}\nend"}, parent="session-secret")
            status, secret_ack = request(base, "POST", "/v1/ingest/batches", {"events": [secret_env]}, "batch-secret")
            assert status == 201
            status, sanitized = request(base, "GET", "/v1/receipts/resolve?" + urllib.parse.urlencode({"receipt": secret_ack["receipts"][0]}))
            assert status == 200 and secret not in json.dumps(sanitized) and "[REDACTED]" in json.dumps(sanitized)
            assert request(base, "GET", "/v1/raw/events")[0] == 404
            with store.connect() as conn:
                assert secret in json.dumps(conn.execute("SELECT envelope FROM source_events WHERE native_id='session-secret:turn-1'").fetchone()["envelope"])
                assert secret not in json.dumps(conn.execute("SELECT metadata FROM audit_events").fetchall())
                assert secret not in "\n".join(row["text_redacted"] for row in conn.execute("SELECT text_redacted FROM items").fetchall())
                assert conn.execute("SELECT count(*) AS n FROM source_events WHERE native_id='session-race:turn-1'").fetchone()["n"] == 1
                ordered = [row["event_native_id"] for row in conn.execute("SELECT event_native_id FROM items WHERE session_native_id='session-order' ORDER BY occurred_at")]
                assert ordered == ["session-order:turn-1", "session-order:turn-2"]

            # Invalid hash is rejected into metadata-only dead letters.
            bad = {**first, "native_id": "bad-hash", "content_sha256": "0" * 64}
            assert request(base, "POST", "/v1/ingest/batches", {"events": [bad]}, "batch-bad")[0] == 400
            with store.connect() as conn:
                dead = conn.execute("SELECT error_code,error_summary FROM dead_letters ORDER BY id DESC LIMIT 1").fetchone()
                assert dead and secret not in json.dumps(dead)

            # PostgreSQL JSONB rejects NUL, but the HTTP boundary must not let the driver's
            # payload excerpt escape through socketserver traceback logging.
            nul_marker = "nul-private-marker"
            nul = make_envelope("nul-record", {"text": nul_marker + "\x00tail"}, parent="nul-session")
            status, rejected = request(base, "POST", "/v1/ingest/batches", {"events": [nul]}, "bad-nul")
            assert status == 500 and rejected == {"error": "ingest failed"}

            # Tombstone removes live projection; rebuild is receipt-equivalent and reapplies it.
            tombstone = make_envelope("session-1:turn-1", {"target_native_id": "session-1:turn-1"}, kind="tombstone")
            status, tomb_ack = request(base, "POST", "/v1/ingest/batches", {"events": [tombstone]}, "batch-tombstone")
            assert status == 201 and tomb_ack["receipts"][0].endswith("?rev=3")
            status, old_after_delete = request(base, "GET", "/v1/receipts/resolve?" + urllib.parse.urlencode({"receipt": ack["receipts"][0]}))
            assert status == 200 and old_after_delete["items"] == []
            before = {}
            for receipt, _ in fixture_receipts:
                before[receipt] = request(base, "GET", "/v1/receipts/resolve?" + urllib.parse.urlencode({"receipt": receipt}))[1]
            rebuild = store.rebuild()
            assert rebuild["items_before"] == rebuild["items_after"]
            assert rebuild["entities_before"] == rebuild["entities_after"]
            for receipt, _ in fixture_receipts:
                after = request(base, "GET", "/v1/receipts/resolve?" + urllib.parse.urlencode({"receipt": receipt}))[1]
                assert after == before[receipt]

            log.flush()
            assert secret not in log_path.read_text()
            assert nul_marker not in log_path.read_text()
            with store.connect() as conn:
                tombstone_lookup_index = conn.execute(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname='public' AND indexname='items_source_event_idx'"
                ).fetchone()
                assert tombstone_lookup_index and "(source_id, event_native_id)" in tombstone_lookup_index["indexdef"]
                lexical_index = conn.execute(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname='public' AND indexname='items_search_vector_idx'"
                ).fetchone()
                assert lexical_index and "to_tsvector" in lexical_index["indexdef"]
                entity_index = conn.execute(
                    "SELECT indexdef FROM pg_indexes WHERE schemaname='public' AND indexname='entities_normalized_source_idx'"
                ).fetchone()
                assert entity_index and "source_id" in entity_index["indexdef"] and "normalized" in entity_index["indexdef"]
                summary = {
                    "source_events": conn.execute("SELECT count(*) AS n FROM source_events").fetchone()["n"],
                    "live_items": conn.execute("SELECT count(*) AS n FROM items WHERE deleted_at IS NULL").fetchone()["n"],
                    "revisions_for_session_1_turn_1": conn.execute("SELECT count(*) AS n FROM source_events WHERE source_id='codex:linux' AND native_id='session-1:turn-1'").fetchone()["n"],
                    "race_duplicates": 0,
                    "same_key_race_single_commit": True,
                    "source_takeover_rejected": True,
                    "source_identity_conflations": 0,
                    "disconnected_ack_replayed": True,
                    "fixture_receipt_equivalence": True,
                    "rebuild_equivalence": True,
                    "secret_projection_leaks": 0,
                    "dead_letters": conn.execute("SELECT count(*) AS n FROM dead_letters").fetchone()["n"],
                    "projection_lag": store.service_metrics()["projection_lag"],
                    "tombstone_lookup_index": True,
                    "lexical_index": True,
                    "entity_projection": True,
                    "entity_source_index": True,
                    "entity_rebuild_equivalence": True,
                    "bounded_adversarial_query": True,
                    "content_free_search_diagnostics": True,
                    "remote_search_receipt_resolved": True,
                    "remote_show_window": True,
                    "remote_related_context": True,
                    "remote_doctor_content_free": True,
                    "source_scoped_reads": True,
                }
            assert summary["projection_lag"] == 0
            result = {"status": "pass", "runtime": {"python": sys.version.split()[0], "postgres": "17-alpine", "psycopg": psycopg.__version__}, "summary": summary}
            rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
            if os.environ.get("RECALL_E2E_OUT"):
                Path(os.environ["RECALL_E2E_OUT"]).write_text(rendered)
            print(json.dumps(result, sort_keys=True))
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
            log.close()


if __name__ == "__main__":
    main()
