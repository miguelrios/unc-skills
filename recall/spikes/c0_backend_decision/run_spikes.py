#!/usr/bin/env python3
"""Run comparable C0 evidence-store spikes and emit a machine-readable scorecard."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RECORDS = [
    {"source_id": "alpha", "native_id": "session-001:turn-7", "content": "repaired quartz retry budget marker-alpha-001", "receipt": "recall://alpha/session-001:turn-7"},
    {"source_id": "alpha", "native_id": "session-002:turn-3", "content": "unrelated cedar deployment notes", "receipt": "recall://alpha/session-002:turn-3"},
    {"source_id": "beta", "native_id": "session-001:turn-7", "content": "marker-alpha-001 forbidden decoy", "receipt": "recall://beta/session-001:turn-7"},
]
QUERY = "marker-alpha-001"


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int(len(ordered) * p + 0.999999) - 1))]


def run(cmd: list[str], *, env: dict | None = None, cwd: str | None = None, stdin: str | None = None) -> tuple[str, float]:
    started = time.perf_counter()
    proc = subprocess.run(cmd, input=stdin, text=True, capture_output=True, env=env, cwd=cwd, check=True)
    return proc.stdout, time.perf_counter() - started


def result(name: str, latencies: list[float], *, field_fraction: float, receipt_ok: bool, leaks: int, duplicates: int, setup_s: float, export_fraction: float, profiles: dict) -> dict:
    profile = profiles[name]
    return {
        "backend": name,
        "measured": {
            "setup_seconds": round(setup_s, 4),
            "retrieval_p50_ms": round(statistics.median(latencies) * 1000, 3),
            "retrieval_p95_ms": round(percentile(latencies, 0.95) * 1000, 3),
            "roundtrip_field_fraction": field_fraction,
            "receipt_precision_at_1": 1.0 if receipt_ok else 0.0,
            "source_isolation_leaks": leaks,
            "idempotency_duplicates": duplicates,
            "export_field_fraction": export_fraction,
        },
        "structural_proxies": profile,
    }


def native_postgres(dsn: str, profiles: dict) -> dict:
    sql = """
    DROP TABLE IF EXISTS c0_events;
    CREATE TABLE c0_events(source_id text, native_id text, content text, receipt text,
      payload jsonb, PRIMARY KEY(source_id,native_id));
    """
    _, setup = run(["psql", dsn, "-v", "ON_ERROR_STOP=1", "-q", "-c", sql])
    payload = json.dumps(RECORDS[0])
    inserts = []
    for rec in RECORDS + [RECORDS[0]]:
        inserts.append("INSERT INTO c0_events VALUES (%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING;" % tuple("'" + str(x).replace("'", "''") + "'" for x in (rec["source_id"], rec["native_id"], rec["content"], rec["receipt"], json.dumps(rec))))
    run(["psql", dsn, "-v", "ON_ERROR_STOP=1", "-q", "-c", "\n".join(inserts)])
    latencies = []
    output = ""
    for _ in range(7):
        output, elapsed = run(["psql", dsn, "-At", "-c", f"SELECT payload FROM c0_events WHERE source_id='alpha' AND content ILIKE '%{QUERY}%' ORDER BY native_id LIMIT 5"])
        latencies.append(elapsed)
    rows = [json.loads(line) for line in output.splitlines() if line]
    count_out, _ = run(["psql", dsn, "-At", "-c", "SELECT count(*) FROM c0_events"])
    duplicates = max(0, int(count_out.strip()) - len(RECORDS))
    receipt_ok = bool(rows and rows[0]["receipt"] == RECORDS[0]["receipt"])
    leaks = sum(1 for row in rows if row["source_id"] != "alpha")
    fraction = len(set(RECORDS[0]) & set(rows[0])) / len(RECORDS[0]) if rows else 0.0
    return result("recall-native-postgres", latencies, field_fraction=fraction, receipt_ok=receipt_ok, leaks=leaks, duplicates=duplicates, setup_s=setup, export_fraction=fraction, profiles=profiles)


def gbrain(repo: str, profiles: dict) -> dict:
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="c0-gbrain-") as tmp:
        home = Path(tmp) / "home"
        alpha = Path(tmp) / "alpha"; beta = Path(tmp) / "beta"
        alpha.mkdir(); beta.mkdir()
        base_env = os.environ | {"GBRAIN_HOME": str(home)}
        bun = ["npx", "-y", "bun@1.3.10", "src/cli.ts"]
        run(bun + ["init", "--pglite", "--no-embedding", "--force"], env=base_env, cwd=repo)
        run(bun + ["sources", "add", "alpha", "--path", str(alpha)], env=base_env, cwd=repo)
        run(bun + ["sources", "add", "beta", "--path", str(beta)], env=base_env, cwd=repo)
        for rec in RECORDS + [RECORDS[0]]:
            source = rec["source_id"]
            slug = "sessions/" + rec["native_id"].replace(":", "-")
            body = json.dumps(rec, sort_keys=True)
            run(bun + ["capture", body, "--slug", slug, "--source", source, "--json"], env=base_env, cwd=repo)
        setup = time.perf_counter() - started
        env = base_env | {"GBRAIN_SOURCE": "alpha"}
        latencies = []
        output = ""
        for _ in range(7):
            output, elapsed = run(bun + ["search", QUERY, "--limit", "5"], env=env, cwd=repo)
            latencies.append(elapsed)
        receipt_ok = "sessions/session-001-turn-7" in output
        leaks = 1 if "forbidden decoy" in output else 0
        exported, _ = run(bun + ["get", "sessions/session-001-turn-7"], env=env, cwd=repo)
        preserved = sum(1 for key in RECORDS[0] if f'"{key}"' in exported)
        fraction = preserved / len(RECORDS[0])
        listed, _ = run(bun + ["list", "-n", "20"], env=env, cwd=repo)
        duplicates = max(0, listed.count("sessions/session-001-turn-7") - 1)
        return result("gbrain-pglite-adapter", latencies, field_fraction=fraction, receipt_ok=receipt_ok, leaks=leaks, duplicates=duplicates, setup_s=setup, export_fraction=fraction, profiles=profiles)


class MemoryHandler(BaseHTTPRequestHandler):
    rows: dict[tuple[str, str], dict] = {}

    def log_message(self, *_args) -> None:
        return

    def _send(self, code: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        self.rows[(body["source_id"], body["native_id"])] = body
        self._send(200, {"receipt": body["receipt"]})

    def do_GET(self) -> None:
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        source = params.get("source_id", [""])[0]; query = params.get("q", [""])[0].casefold()
        rows = [row for (src, _), row in self.rows.items() if src == source and query in row["content"].casefold()]
        self._send(200, {"results": rows})


def managed_http(profiles: dict) -> dict:
    MemoryHandler.rows = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), MemoryHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f"http://127.0.0.1:{server.server_port}/memories"
    started = time.perf_counter()
    for rec in RECORDS + [RECORDS[0]]:
        req = urllib.request.Request(base, data=json.dumps(rec).encode(), headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req).read()
    setup = time.perf_counter() - started
    latencies = []
    rows = []
    for _ in range(7):
        begin = time.perf_counter()
        body = urllib.request.urlopen(base + "?" + urllib.parse.urlencode({"source_id": "alpha", "q": QUERY})).read()
        latencies.append(time.perf_counter() - begin); rows = json.loads(body)["results"]
    server.shutdown(); server.server_close()
    receipt_ok = bool(rows and rows[0]["receipt"] == RECORDS[0]["receipt"])
    leaks = sum(1 for row in rows if row["source_id"] != "alpha")
    duplicates = max(0, len(MemoryHandler.rows) - len(RECORDS))
    fraction = len(set(RECORDS[0]) & set(rows[0])) / len(RECORDS[0]) if rows else 0.0
    out = result("managed-memory-http-adapter", latencies, field_fraction=fraction, receipt_ok=receipt_ok, leaks=leaks, duplicates=duplicates, setup_s=setup, export_fraction=fraction, profiles=profiles)
    out["limitation"] = "Local contract-faithful HTTP boundary only; no vendor retrieval-quality claim."
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--postgres-dsn", required=True)
    ap.add_argument("--gbrain-repo", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    profiles = json.loads(Path(__file__).with_name("backend_profiles.json").read_text())
    scorecard = {
        "schema_version": 1,
        "corpus_records": len(RECORDS),
        "runtime": {
            "postgres": "17-alpine (real Docker service)",
            "gbrain": "0.42.58.0 / PGLite / bun 1.3.10 (real runtime)",
            "managed_adapter": "stdlib HTTP conformance server (not a live vendor)",
        },
        "runs": [native_postgres(args.postgres_dsn, profiles), gbrain(args.gbrain_repo, profiles), managed_http(profiles)],
    }
    out = Path(args.out)
    out.write_text(json.dumps(scorecard, indent=2, sort_keys=True) + "\n")
    for backend in scorecard["runs"]:
        trace = {
            "schema_version": 1,
            "backend": backend["backend"],
            "runtime": scorecard["runtime"],
            "corpus_records": scorecard["corpus_records"],
            "result": backend,
            "status": "pass" if backend["measured"]["receipt_precision_at_1"] == 1.0 and backend["measured"]["source_isolation_leaks"] == 0 and backend["measured"]["idempotency_duplicates"] == 0 else "fail",
        }
        (out.parent / f"trace-{backend['backend']}.json").write_text(json.dumps(trace, indent=2, sort_keys=True) + "\n")
    print(json.dumps({run["backend"]: run["measured"] for run in scorecard["runs"]}, sort_keys=True))


if __name__ == "__main__":
    main()
