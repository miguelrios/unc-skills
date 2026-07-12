from __future__ import annotations

import json
import logging
import os
import socket
import socketserver
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .db import BrainStore, IdempotencyConflict

LOG = logging.getLogger("recall.brainstore")
MAX_BODY_BYTES = 2 * 1024 * 1024
COUNTERS = {"http_requests": 0, "auth_denied": 0, "ingest_commits": 0, "ingest_replays": 0}
COUNTER_LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    store: BrainStore

    def log_message(self, fmt: str, *args) -> None:
        LOG.info("http method=%s path=%s status=%s", self.command, self.path.split("?", 1)[0], args[1] if len(args) > 1 else "unknown")

    def send_json(self, status: int, body: object) -> None:
        data = json.dumps(body, default=str, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def authenticate(self, scope: str) -> dict | None:
        if os.environ.get("RECALL_AUTH_REQUIRED", "0") != "1":
            return {"kind": "development", "name": "unauthenticated"}
        authorization = self.headers.get("Authorization")
        if authorization is not None:
            if not authorization.startswith("Bearer "):
                return None
            credential = self.store.authenticate_bearer(authorization.removeprefix("Bearer ").strip(), scope)
            if not credential:
                return None
            return {"kind": "collector", "name": credential["name"], "source_id": credential["source_id"]}
        if os.environ.get("RECALL_TRUST_TAILSCALE_HEADERS", "0") == "1" and self.trusted_proxy_peer():
            login = self.headers.get("Tailscale-User-Login")
            allowed = {value.strip().casefold() for value in os.environ.get("RECALL_ALLOWED_TAILSCALE_USERS", "").split(",") if value.strip()}
            if login and login.casefold() in allowed:
                return {"kind": "tailscale-user", "name": login}
        return None

    def trusted_proxy_peer(self) -> bool:
        """Trust identity headers only from root/tailscaled over a Unix socket."""
        if not getattr(self.server, "is_unix_socket", False):
            return False
        try:
            _pid, uid, _gid = struct.unpack("3i", self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
            return uid == 0
        except (AttributeError, OSError, struct.error):
            return False

    def require(self, scope: str) -> dict | None:
        principal = self.authenticate(scope)
        if principal:
            return principal
        with COUNTER_LOCK:
            COUNTERS["auth_denied"] += 1
        self.send_json(401, {"error": "unauthorized"})
        return None

    def metrics(self) -> bytes:
        db = self.store.service_metrics()
        with COUNTER_LOCK:
            counters = dict(COUNTERS)
        lines = [
            "# HELP recall_http_requests_total HTTP requests handled.",
            "# TYPE recall_http_requests_total counter",
            f"recall_http_requests_total {counters['http_requests']}",
            "# HELP recall_auth_denied_total Requests rejected by authentication.",
            "# TYPE recall_auth_denied_total counter",
            f"recall_auth_denied_total {counters['auth_denied']}",
            "# HELP recall_ingest_commits_total Newly committed batches.",
            "# TYPE recall_ingest_commits_total counter",
            f"recall_ingest_commits_total {counters['ingest_commits']}",
            "# HELP recall_ingest_replays_total Idempotent batch replays.",
            "# TYPE recall_ingest_replays_total counter",
            f"recall_ingest_replays_total {counters['ingest_replays']}",
            f"recall_source_events {db['source_events']}",
            f"recall_dead_letters {db['dead_letters']}",
            f"recall_projection_lag {db['projection_lag']}",
            "",
        ]
        return "\n".join(lines).encode()

    def handle_one_request(self) -> None:
        with COUNTER_LOCK:
            COUNTERS["http_requests"] += 1
        super().handle_one_request()

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/healthz":
            self.send_json(200, {"status": "ok"})
            return
        if parsed.path == "/readyz":
            try:
                self.store.service_metrics()
                self.send_json(200, {"status": "ready"})
            except Exception:
                self.send_json(503, {"status": "not_ready"})
            return
        if parsed.path == "/metrics":
            if not self.require("metrics"):
                return
            data = self.metrics()
            self.send_response(200); self.send_header("Content-Type", "text/plain; version=0.0.4"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
            return
        if parsed.path == "/v1/receipts/resolve":
            if not self.require("read"):
                return
            receipt = parse_qs(parsed.query).get("receipt", [""])[0]
            try:
                result = self.store.resolve(receipt)
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)}); return
            self.send_json(200 if result else 404, result or {"error": "not found"}); return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/v1/ingest/batches":
            self.send_json(404, {"error": "not found"}); return
        principal = self.require("write")
        if not principal:
            return
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY_BYTES:
            self.send_json(413, {"error": "invalid body size"}); return
        try:
            body = json.loads(self.rfile.read(length))
            if principal.get("kind") == "collector" and principal.get("source_id"):
                if any(event.get("source_id") != principal["source_id"] for event in body["events"]):
                    with COUNTER_LOCK:
                        COUNTERS["auth_denied"] += 1
                    self.send_json(403, {"error": "collector source scope mismatch"})
                    return
            ack, replay = self.store.ingest(self.headers.get("Idempotency-Key", ""), body["events"])
            with COUNTER_LOCK:
                COUNTERS["ingest_replays" if replay else "ingest_commits"] += 1
            self.send_json(200 if replay else 201, {**ack, "replay": replay})
        except IdempotencyConflict as exc:
            self.send_json(409, {"error": str(exc)})
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            LOG.warning("ingest rejected type=%s", type(exc).__name__)
            self.store.record_dead_letter(type(exc).__name__, str(exc))
            self.send_json(400, {"error": str(exc)})
        except BrokenPipeError:
            # Commit may already be durable; replaying the idempotency key returns its ack.
            return


def serve(dsn: str, host: str = "127.0.0.1", port: int = 8788) -> None:
    Handler.store = BrainStore(dsn)
    server = ThreadingHTTPServer((host, port), Handler)
    LOG.info("brainstore listening host=%s port=%s", host, port)
    server.serve_forever()


class ThreadingUnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    is_unix_socket = True


def serve_unix(dsn: str, path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    Handler.store = BrainStore(dsn)
    server = ThreadingUnixHTTPServer(path, Handler)
    os.chmod(path, 0o600)
    LOG.info("brainstore listening unix_socket=%s", path)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(levelname)s %(message)s")
    if os.environ.get("RECALL_UNIX_SOCKET"):
        serve_unix(os.environ["RECALL_DATABASE_URL"], os.environ["RECALL_UNIX_SOCKET"])
    else:
        serve(os.environ["RECALL_DATABASE_URL"], os.environ.get("RECALL_HOST", "127.0.0.1"), int(os.environ.get("RECALL_PORT", "8788")))
