from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .db import BrainStore, IdempotencyConflict

LOG = logging.getLogger("recall.brainstore")
MAX_BODY_BYTES = 2 * 1024 * 1024


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

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/healthz":
            self.send_json(200, {"status": "ok"})
            return
        if parsed.path == "/v1/receipts/resolve":
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
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY_BYTES:
            self.send_json(413, {"error": "invalid body size"}); return
        try:
            body = json.loads(self.rfile.read(length))
            ack, replay = self.store.ingest(self.headers.get("Idempotency-Key", ""), body["events"])
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


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(levelname)s %(message)s")
    serve(os.environ["RECALL_DATABASE_URL"], os.environ.get("RECALL_HOST", "127.0.0.1"), int(os.environ.get("RECALL_PORT", "8788")))
