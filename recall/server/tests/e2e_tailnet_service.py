#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SERVER = ROOT / "recall/server"
sys.path.insert(0, str(SERVER))

from recall_server.db import BrainStore
from recall_server.projectors import canonical_json


def http(base: str, method: str, path: str, *, body=None, token=None, key=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    if key:
        headers["Idempotency-Key"] = key
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10, context=ssl.create_default_context()) as response:
            raw = response.read()
            return response.status, response.headers.get("Content-Type", ""), raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), exc.read()


def unix_status(path: str, target: str, headers: dict[str, str]) -> int:
    request = [f"GET {target} HTTP/1.1", "Host: localhost", "Connection: close"]
    request.extend(f"{key}: {value}" for key, value in headers.items())
    payload = ("\r\n".join(request) + "\r\n\r\n").encode()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(path); sock.sendall(payload)
    data = b""
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        data += chunk
    sock.close()
    return int(data.split(b" ", 2)[1])


def envelope(marker: str, source_id: str, run_id: str) -> dict:
    content = {"role": "tool", "text": marker}
    return {
        "schema_version": 1,
        "source_id": source_id,
        "native_id": f"tailnet-e2e:{run_id}:turn-1",
        "native_parent_id": f"tailnet-e2e:{run_id}",
        "kind": "tool_result",
        "occurred_at": "2026-07-12T21:20:00Z",
        "observed_at": "2026-07-12T21:20:01Z",
        "principal_id": "owner",
        "visibility": "private",
        "content_type": "application/json",
        "content": content,
        "content_sha256": hashlib.sha256(canonical_json(content)).hexdigest(),
        "provenance": {"harness": "tailnet-e2e"},
    }


def main() -> None:
    base = os.environ["RECALL_TAILNET_URL"].rstrip("/")
    socket_path = os.environ["RECALL_UNIX_SOCKET"]
    dsn = os.environ["RECALL_DATABASE_URL"]
    collector_data = json.loads(Path(os.environ["RECALL_COLLECTOR_TOKEN_FILE"]).read_text())
    metrics_data = json.loads(Path(os.environ["RECALL_METRICS_TOKEN_FILE"]).read_text())
    collector_token = collector_data["token"]
    metrics_token = metrics_data["token"]
    run_id = os.environ.get("RECALL_E2E_RUN_ID", "pilot")
    source_id = os.environ.get("RECALL_E2E_SOURCE", "pilot:collector")
    marker = f"tailnet-private-marker-{run_id}"

    health_status, _, _ = http(base, "GET", "/healthz")
    assert health_status == 200
    status, _, raw = http(base, "POST", "/v1/ingest/batches", body={"events": [envelope(marker, source_id, run_id)]}, token=collector_token, key=f"tailnet-e2e-batch-{run_id}")
    assert status == 201, raw
    ack = json.loads(raw); receipt = ack["receipts"][0]

    # Tagged/workload-style bearer path.
    encoded = urllib.parse.urlencode({"receipt": receipt})
    collector_read, _, collector_raw = http(base, "GET", "/v1/receipts/resolve?" + encoded, token=collector_token)
    assert collector_read == 200 and marker in collector_raw.decode()

    # Interactive user path: no bearer; Serve strips/injects the verified identity header.
    user_read, _, user_raw = http(base, "GET", "/v1/receipts/resolve?" + encoded)
    assert user_read == 200 and marker in user_raw.decode()

    # Bad bearer fails closed; it must not downgrade to the simultaneously verified user.
    bad_bearer, _, _ = http(base, "GET", "/v1/receipts/resolve?" + encoded, token="rcl_invalid")
    assert bad_bearer == 401

    mismatched = envelope("scope-mismatch", source_id, run_id + "-mismatch")
    mismatched["source_id"] = "pilot:other"
    mismatch_status, _, _ = http(base, "POST", "/v1/ingest/batches", body={"events": [mismatched]}, token=collector_token, key="scope-mismatch")
    assert mismatch_status == 403

    metrics_status, content_type, metrics_raw = http(base, "GET", "/metrics", token=metrics_token)
    metrics_text = metrics_raw.decode()
    assert metrics_status == 200 and "text/plain" in content_type
    assert marker not in metrics_text and collector_token not in metrics_text and metrics_token not in metrics_text
    for required in ("recall_http_requests_total", "recall_auth_denied_total", "recall_source_events", "recall_dead_letters", "recall_projection_lag"):
        assert required in metrics_text

    # Same-user direct Unix access cannot forge the root tailscaled peer identity.
    forged_status = unix_status(socket_path, "/v1/receipts/resolve?" + encoded, {"Tailscale-User-Login": "miguel@parcha.ai"})
    assert forged_status == 401

    # There is no application TCP listener to bypass Serve on loopback or the tailnet IP.
    no_tcp = {}
    for host in ("127.0.0.1", "100.106.234.74"):
        sock = socket.socket(); sock.settimeout(1)
        try:
            sock.connect((host, 18788)); no_tcp[host] = False
        except OSError:
            no_tcp[host] = True
        finally:
            sock.close()
    assert all(no_tcp.values())

    # Revocation is immediate on the next request.
    assert BrainStore(dsn).revoke_collector_token("pilot-collector")
    revoked_status, _, _ = http(base, "GET", "/v1/receipts/resolve?" + encoded, token=collector_token)
    assert revoked_status == 401

    result = {
        "status": "pass",
        "tailnet_url": base,
        "allowed_user_status": user_read,
        "allowed_collector_status": collector_read,
        "ingest_status": status,
        "bad_bearer_status": bad_bearer,
        "source_scope_mismatch_status": mismatch_status,
        "forged_direct_unix_identity_status": forged_status,
        "revoked_collector_status": revoked_status,
        "no_tcp_listener": no_tcp,
        "metrics_content_leaks": 0,
        "receipt_resolved": True,
    }
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if os.environ.get("RECALL_E2E_OUT"):
        Path(os.environ["RECALL_E2E_OUT"]).write_text(rendered)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
