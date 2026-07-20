from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import socket
import socketserver
import stat
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .archive_runtime import build_archive_store
from .canonical import (
    CanonicalArchiveGateway,
    CanonicalLifecycleError,
    CanonicalPlane,
)
from .canonical_retrieval import CanonicalRetrieval
from .db import BrainStore, IdempotencyConflict
from .mcp import (
    SUPPORTED_PROTOCOL_VERSIONS,
    McpProtocolError,
    bound_response as bound_mcp_response,
    dispatch as dispatch_mcp,
    error_response as mcp_error_response,
)
from .semantic import SemanticRuntime
from .webhooks import WEBHOOK_PATH, WebhookError, build_webhook_event

LOG = logging.getLogger("recall.brainstore")
MAX_BODY_BYTES = 12 * 1024 * 1024
COUNTERS = {
    "http_requests": 0,
    "http_errors": 0,
    "http_duration_count": 0,
    "http_duration_sum": 0.0,
    "auth_denied": 0,
    "ingest_commits": 0,
    "ingest_replays": 0,
}
COUNTER_LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    store: BrainStore
    archive_store = None
    canonical_plane: CanonicalPlane | None = None
    canonical_retrieval: CanonicalRetrieval | None = None

    def log_message(self, fmt: str, *args) -> None:
        LOG.info(
            "http method=%s path=%s status=%s",
            self.command,
            self.path.split("?", 1)[0],
            args[1] if len(args) > 1 else "unknown",
        )

    def send_json(self, status: int, body: object) -> None:
        if status >= 400:
            with COUNTER_LOCK:
                COUNTERS["http_errors"] += 1
        data = json.dumps(body, default=str, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        try:
            self.end_headers()
            self.wfile.write(data)
        except BrokenPipeError:
            # The database work may have completed after a bounded client left.
            # Never turn that ordinary transport event into a content-bearing traceback.
            return

    def send_empty(self, status: int, headers: dict[str, str] | None = None) -> None:
        if status >= 400:
            with COUNTER_LOCK:
                COUNTERS["http_errors"] += 1
        self.send_response(status)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def valid_mcp_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        allowed = {
            value.strip()
            for value in os.environ.get("RECALL_MCP_ALLOWED_ORIGINS", "").split(",")
            if value.strip()
        }
        if origin in allowed:
            return True
        self.send_json(
            403,
            mcp_error_response(McpProtocolError(-32000, "invalid origin")),
        )
        return False

    def valid_mcp_accept(self, *, get: bool = False) -> bool:
        accepted = {
            value.split(";", 1)[0].strip().casefold()
            for value in self.headers.get("Accept", "").split(",")
            if value.strip()
        }
        required = (
            {"text/event-stream"} if get else {"application/json", "text/event-stream"}
        )
        if required.issubset(accepted):
            return True
        self.send_json(
            406,
            mcp_error_response(McpProtocolError(-32000, "unsupported accept types")),
        )
        return False

    def valid_mcp_protocol(self) -> bool:
        version = self.headers.get("MCP-Protocol-Version")
        if version is None or version in SUPPORTED_PROTOCOL_VERSIONS:
            return True
        self.send_json(
            400,
            mcp_error_response(
                McpProtocolError(-32600, "unsupported MCP protocol version")
            ),
        )
        return False

    def body_length(self, maximum: int) -> int | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            self.send_json(400, {"error": "invalid body size"})
            return None
        if length <= 0 or length > maximum:
            self.send_json(413, {"error": "invalid body size"})
            return None
        return length

    def authenticate(self, scope: str) -> dict | None:
        if os.environ.get("RECALL_AUTH_REQUIRED", "0") != "1":
            return {"kind": "development", "name": "unauthenticated"}
        authorization = self.headers.get("Authorization")
        if authorization is not None:
            if not authorization.startswith("Bearer "):
                return None
            credential = self.store.authenticate_bearer(
                authorization.removeprefix("Bearer ").strip(), scope
            )
            if not credential:
                return None
            credential_kind = credential.get("credential_kind", "collector")
            if (
                scope == "read"
                and os.environ.get("RECALL_CANONICAL_MCP_ENABLED") == "1"
                and credential_kind != "mcp"
            ):
                return None
            principal = {
                "kind": credential_kind,
                "credential_kind": credential_kind,
                "name": credential["name"],
                "tenant_id": credential.get("tenant_id"),
                "source_id": credential["source_id"],
                "principal_id": credential.get("principal_id"),
                "audience": credential.get("audience"),
                "capture_origin": credential.get("capture_origin"),
                "webhook_privacy_mode": credential.get("webhook_privacy_mode"),
                "scopes": list(credential.get("scopes", [])),
            }
            if credential.get("principal_id") and scope == "read":
                if credential_kind == "mcp":
                    principal["authorized_sources"] = (
                        self.store.authorized_canonical_source_ids(
                            credential.get("tenant_id"),
                            credential["principal_id"],
                        )
                    )
                else:
                    principal["authorized_sources"] = self.store.authorized_source_ids(
                        credential["principal_id"]
                    )
            return principal
        if (
            os.environ.get("RECALL_TRUST_TAILSCALE_HEADERS", "0") == "1"
            and self.trusted_proxy_peer()
        ):
            login = self.headers.get("Tailscale-User-Login")
            allowed = {
                value.strip().casefold()
                for value in os.environ.get("RECALL_ALLOWED_TAILSCALE_USERS", "").split(
                    ","
                )
                if value.strip()
            }
            if login and login.casefold() in allowed:
                return {"kind": "tailscale-user", "name": login}
        return None

    def trusted_proxy_peer(self) -> bool:
        """Trust identity headers only from root/tailscaled over a Unix socket."""
        if not getattr(self.server, "is_unix_socket", False):
            LOG.warning("tailscale identity rejected transport=tcp")
            return False
        try:
            pid, uid, _gid = struct.unpack(
                "3i",
                self.connection.getsockopt(
                    socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
                ),
            )
            trusted_uids = {
                int(value.strip())
                for value in os.environ.get("RECALL_TRUSTED_PROXY_UIDS", "0").split(",")
                if value.strip().isdigit()
            }
            trusted = uid in trusted_uids
            LOG.info(
                "tailscale proxy peer trusted=%s uid=%s pid=%s identity_header_present=%s",
                trusted,
                uid,
                pid,
                bool(self.headers.get("Tailscale-User-Login")),
            )
            return trusted
        except (AttributeError, OSError, struct.error):
            LOG.exception("tailscale identity rejected reason=peer_credential_error")
            return False

    def require(self, scope: str) -> dict | None:
        principal = self.authenticate(scope)
        if principal:
            return principal
        with COUNTER_LOCK:
            COUNTERS["auth_denied"] += 1
        self.send_json(401, {"error": "unauthorized"})
        return None

    def canonical_authority(
        self,
        principal: dict,
        body: dict,
    ) -> tuple[str, str, str] | None:
        if not isinstance(body, dict):
            self.send_json(400, {"error": "canonical request invalid"})
            return None
        tenant_id = body.get("tenant_id")
        principal_id = body.get("principal_id")
        source_id = body.get("source_id")
        if source_id is None:
            events = body.get("events")
            if (
                isinstance(events, list)
                and events
                and all(isinstance(event, dict) for event in events)
            ):
                sources = {event.get("source_id") for event in events}
                if len(sources) == 1:
                    source_id = sources.pop()
        allowed = principal.get("kind") == "development" or (
            principal.get("kind") == "collector"
            and principal.get("tenant_id") == tenant_id
            and principal.get("principal_id") == principal_id
            and principal.get("source_id") == source_id
        )
        if allowed and all(
            isinstance(value, str) and value
            for value in (tenant_id, principal_id, source_id)
        ):
            return tenant_id, principal_id, source_id
        with COUNTER_LOCK:
            COUNTERS["auth_denied"] += 1
        self.send_json(403, {"error": "canonical authority forbidden"})
        return None

    def metrics(self) -> bytes:
        db = self.store.service_metrics()
        with COUNTER_LOCK:
            counters = dict(COUNTERS)
        lines = [
            "# HELP recall_http_requests_total HTTP requests handled.",
            "# TYPE recall_http_requests_total counter",
            f"recall_http_requests_total {counters['http_requests']}",
            "# HELP recall_http_errors_total HTTP responses with status 4xx or 5xx.",
            "# TYPE recall_http_errors_total counter",
            f"recall_http_errors_total {counters['http_errors']}",
            "# HELP recall_http_request_duration_seconds Request handling time without route or content labels.",
            "# TYPE recall_http_request_duration_seconds summary",
            f"recall_http_request_duration_seconds_count {counters['http_duration_count']}",
            f"recall_http_request_duration_seconds_sum {counters['http_duration_sum']:.9f}",
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
            f"recall_source_freshness_seconds {db['source_freshness_seconds']}",
            f"recall_embedded_items {db['embedded_items']}",
            f"recall_embedding_lag {db['embedding_lag']}",
            "",
        ]
        return "\n".join(lines).encode()

    def handle_one_request(self) -> None:
        started = time.monotonic()
        with COUNTER_LOCK:
            COUNTERS["http_requests"] += 1
        try:
            super().handle_one_request()
        finally:
            with COUNTER_LOCK:
                COUNTERS["http_duration_count"] += 1
                COUNTERS["http_duration_sum"] += time.monotonic() - started

    @staticmethod
    def public_mcp_profile() -> bool:
        return os.environ.get("RECALL_HTTP_PROFILE") == "public-mcp"

    @staticmethod
    def public_edge_profile() -> bool:
        return os.environ.get("RECALL_HTTP_PROFILE") == "public-edge"

    def hide_non_public_route(self, method: str, path: str) -> bool:
        if not (self.public_mcp_profile() or self.public_edge_profile()):
            return False
        allowed = (
            method == "POST" and path == "/mcp"
        ) or (
            method == "GET" and path in {"/mcp", "/healthz", "/readyz"}
        )
        if self.public_edge_profile():
            allowed = allowed or (method == "POST" and path == WEBHOOK_PATH)
        if os.environ.get("RECALL_CANONICAL_INGEST_PUBLIC") == "1":
            allowed = allowed or (
                method == "POST"
                and path in {"/v2/archive/objects", "/v2/ingest/canonical"}
            )
        if allowed:
            return False
        self.send_json(404, {"error": "not found"})
        return True

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        if self.hide_non_public_route("GET", parsed.path):
            return
        if parsed.path == "/mcp":
            if not self.valid_mcp_origin():
                return
            if not self.valid_mcp_accept(get=True):
                return
            if not self.require("read"):
                return
            self.send_empty(405, {"Allow": "POST"})
            return
        if parsed.path == "/healthz":
            self.send_json(200, {"status": "ok"})
            return
        if parsed.path == "/readyz":
            try:
                self.send_json(200, self.store.readiness())
            except Exception:
                self.send_json(503, {"status": "not_ready"})
            return
        if parsed.path == "/metrics":
            if not self.require("metrics"):
                return
            data = self.metrics()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if parsed.path == "/v1/receipts/resolve":
            principal = self.require("read")
            if not principal:
                return
            receipt = parse_qs(parsed.query).get("receipt", [""])[0]
            try:
                result = self.store.resolve(
                    receipt,
                    authorized_source=principal.get("source_id"),
                )
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
                return
            self.send_json(200 if result else 404, result or {"error": "not found"})
            return
        if parsed.path == "/v1/doctor":
            if not self.require("read"):
                return
            self.send_json(200, self.store.operational_health())
            return
        self.send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if self.hide_non_public_route("POST", path):
            return
        if path == "/v2/archive/objects":
            principal = self.require("write")
            if not principal:
                return
            length = self.body_length(MAX_BODY_BYTES)
            if length is None:
                return
            try:
                body = json.loads(self.rfile.read(length))
                authority = self.canonical_authority(principal, body)
                if authority is None:
                    return
                tenant_id, principal_id, source_id = authority
                if self.archive_store is None:
                    self.send_json(503, {"error": "canonical archive unavailable"})
                    return
                payload = base64.b64decode(
                    body.get("payload_base64", ""),
                    validate=True,
                )
                gateway = CanonicalArchiveGateway(
                    self.store,
                    self.archive_store,
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                )
                reference = gateway.put_raw(
                    tenant_id=tenant_id,
                    source_id=source_id,
                    native_id=body.get("native_id"),
                    payload=payload,
                    media_type=body.get("media_type"),
                    created_at=body.get("created_at"),
                )
                self.send_json(201, reference)
            except (binascii.Error, json.JSONDecodeError, TypeError, ValueError):
                self.send_json(400, {"error": "canonical archive request invalid"})
            except CanonicalLifecycleError as exc:
                status = (
                    409
                    if exc.error_code == "archive_identity_forgotten"
                    else 403
                    if exc.error_code == "archive_authority_forbidden"
                    else 400
                )
                self.send_json(status, {"error": exc.error_code})
            except Exception as exc:
                LOG.error("canonical archive failed type=%s", type(exc).__name__)
                self.send_json(503, {"error": "canonical archive unavailable"})
            return
        if path == "/v2/ingest/canonical":
            principal = self.require("write")
            if not principal:
                return
            length = self.body_length(MAX_BODY_BYTES)
            if length is None:
                return
            try:
                body = json.loads(self.rfile.read(length))
                authority = self.canonical_authority(principal, body)
                if authority is None:
                    return
                tenant_id, principal_id, source_id = authority
                events = body.get("events")
                if (
                    not isinstance(events, list)
                    or any(
                        not isinstance(event, dict)
                        or event.get("source_id") != source_id
                        for event in events
                    )
                ):
                    self.send_json(403, {"error": "canonical authority forbidden"})
                    return
                if self.canonical_plane is None:
                    self.send_json(503, {"error": "canonical plane unavailable"})
                    return
                acknowledgement = self.canonical_plane.ingest_batch(
                    tenant_id=tenant_id,
                    principal_id=principal_id,
                    events=events,
                )
                if self.canonical_retrieval is not None:
                    try:
                        self.canonical_retrieval.embed_pending(
                            tenant_id=tenant_id,
                            batch_size=min(500, max(1, len(events))),
                            max_batches=1,
                        )
                    except Exception as exc:
                        LOG.warning(
                            "canonical embedding deferred type=%s",
                            type(exc).__name__,
                        )
                with COUNTER_LOCK:
                    COUNTERS[
                        "ingest_replays"
                        if acknowledgement["replay"]
                        else "ingest_commits"
                    ] += 1
                self.send_json(
                    200 if acknowledgement["replay"] else 201,
                    acknowledgement,
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                self.send_json(400, {"error": "canonical ingest request invalid"})
            except CanonicalLifecycleError as exc:
                status = (
                    409
                    if exc.error_code == "canonical_identity_forgotten"
                    else 403
                    if exc.error_code
                    in {"canonical_authority_forbidden", "canonical_lineage_invalid"}
                    else 400
                )
                self.send_json(status, {"error": exc.error_code})
            except Exception as exc:
                LOG.error("canonical ingest failed type=%s", type(exc).__name__)
                self.send_json(500, {"error": "canonical ingest failed"})
            return
        if path == WEBHOOK_PATH:
            principal = self.require("webhook")
            if not principal:
                return
            content_type = (
                self.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
            )
            if content_type != "application/json":
                self.send_json(415, {"error": "unsupported content type"})
                return
            length = self.body_length(256 * 1024)
            if length is None:
                return
            try:
                body = json.loads(self.rfile.read(length))
                prepared = build_webhook_event(body, principal)
                if prepared.event is None:
                    self.send_json(
                        202,
                        {
                            "status": "privacy_filtered",
                            "receipt": None,
                            "replay": False,
                            "privacy": prepared.privacy,
                        },
                    )
                    return
                acknowledgement, replay = self.store.ingest(
                    prepared.idempotency_key,
                    [prepared.event],
                )
                receipts = acknowledgement.get("receipts")
                if not isinstance(receipts, list) or len(receipts) != 1:
                    raise RuntimeError("webhook acknowledgement invalid")
                self.send_json(
                    200 if replay else 201,
                    {
                        "status": acknowledgement.get("status", "committed"),
                        "receipt": receipts[0],
                        "replay": replay,
                        "privacy": prepared.privacy,
                    },
                )
            except (json.JSONDecodeError, WebhookError):
                self.send_json(400, {"error": "invalid webhook"})
            except IdempotencyConflict:
                self.send_json(409, {"error": "webhook conflict"})
            except Exception as exc:
                LOG.error("webhook failed type=%s", type(exc).__name__)
                self.send_json(500, {"error": "webhook failed"})
            return
        if path == "/mcp":
            if not self.valid_mcp_origin():
                return
            if not self.valid_mcp_accept():
                return
            if not self.valid_mcp_protocol():
                return
            principal = self.require("read")
            if not principal:
                return
            content_type = (
                self.headers.get("Content-Type", "").split(";", 1)[0].strip().casefold()
            )
            if content_type != "application/json":
                self.send_json(
                    415,
                    mcp_error_response(
                        McpProtocolError(
                            -32700, "content type must be application/json"
                        )
                    ),
                )
                return
            length = self.body_length(256 * 1024)
            if length is None:
                return
            request_id = None
            try:
                body = json.loads(self.rfile.read(length))
                if isinstance(body, dict):
                    request_id = body.get("id")
                mcp_store = self.store
                if os.environ.get("RECALL_CANONICAL_MCP_ENABLED") == "1":
                    if self.canonical_retrieval is None:
                        raise RuntimeError("canonical retrieval unavailable")
                    mcp_store = self.canonical_retrieval.bind(principal)
                response = dispatch_mcp(mcp_store, principal, body)
            except json.JSONDecodeError:
                self.send_json(
                    400,
                    mcp_error_response(McpProtocolError(-32700, "invalid JSON")),
                )
                return
            except McpProtocolError as exc:
                self.send_json(200, mcp_error_response(exc, request_id))
                return
            except (ValueError, TypeError):
                self.send_json(
                    200,
                    mcp_error_response(
                        McpProtocolError(-32602, "tool arguments rejected"),
                        request_id,
                    ),
                )
                return
            except Exception as exc:
                LOG.error("mcp tool failed type=%s", type(exc).__name__)
                self.send_json(
                    200,
                    mcp_error_response(
                        McpProtocolError(-32603, "tool execution failed"),
                        request_id,
                    ),
                )
                return
            if response is None:
                self.send_empty(202)
            else:
                self.send_json(200, bound_mcp_response(response, request_id))
            return
        if path in {"/v1/search", "/v1/show", "/v1/related", "/v1/session-export"}:
            principal = self.require("read")
            if not principal:
                return
            authorized_source = principal.get("source_id")
            length = self.body_length(256 * 1024)
            if length is None:
                return
            try:
                body = json.loads(self.rfile.read(length))
                if path == "/v1/search":
                    result = self.store.search(
                        body.get("query"),
                        body.get("filters", {}),
                        body.get("limit", 10),
                        authorized_source,
                    )
                    timing = result.get("diagnostics", {})
                    LOG.info(
                        "search timing elapsed_ms=%s deadline_ms=%s deadline_exceeded=%s legs=%s",
                        timing.get("elapsed_ms"),
                        timing.get("deadline_ms"),
                        timing.get("deadline_exceeded"),
                        ",".join(
                            str(item.get("leg")) for item in timing.get("legs", [])
                        ),
                    )
                elif path == "/v1/show":
                    result = self.store.show(
                        body.get("target", ""),
                        around=body.get("around"),
                        tail=body.get("tail", 0),
                        prompts=bool(body.get("prompts", False)),
                        authorized_source=authorized_source,
                    )
                    if result is None:
                        self.send_json(404, {"error": "not found"})
                        return
                elif path == "/v1/related":
                    result = self.store.related(
                        cwd=body.get("cwd"),
                        branch=body.get("branch"),
                        limit=body.get("limit", 10),
                        mains_only=bool(body.get("mains_only", False)),
                        fast=bool(body.get("fast", False)),
                        authorized_source=authorized_source,
                    )
                else:
                    result = self.store.session_export(
                        target=body.get("target"),
                        cursor=body.get("cursor"),
                        limit=body.get("limit", 1000),
                        authorized_source=authorized_source,
                    )
                    if result is None:
                        self.send_json(404, {"error": "not found"})
                        return
                self.send_json(200, result)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self.send_json(400, {"error": str(exc)})
            return
        if path != "/v1/ingest/batches":
            self.send_json(404, {"error": "not found"})
            return
        principal = self.require("write")
        if not principal:
            return
        length = self.body_length(MAX_BODY_BYTES)
        if length is None:
            return
        try:
            body = json.loads(self.rfile.read(length))
            if principal.get("kind") == "collector" and principal.get("source_id"):
                if any(
                    event.get("source_id") != principal["source_id"]
                    for event in body["events"]
                ):
                    with COUNTER_LOCK:
                        COUNTERS["auth_denied"] += 1
                    self.send_json(403, {"error": "collector source scope mismatch"})
                    return
            ack, replay = self.store.ingest(
                self.headers.get("Idempotency-Key", ""), body["events"]
            )
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
        except Exception as exc:
            # Never let driver exceptions render payload excerpts through socketserver tracebacks.
            LOG.error("ingest failed type=%s", type(exc).__name__)
            try:
                self.store.record_dead_letter(
                    type(exc).__name__, "database rejected ingest"
                )
            except Exception:
                LOG.error("dead-letter write failed after ingest error")
            self.send_json(500, {"error": "ingest failed"})

    def do_DELETE(self) -> None:
        self.send_json(404, {"error": "not found"})

    def do_PATCH(self) -> None:
        self.send_json(404, {"error": "not found"})

    def do_PUT(self) -> None:
        self.send_json(404, {"error": "not found"})

    def do_OPTIONS(self) -> None:
        self.send_json(404, {"error": "not found"})

    def do_HEAD(self) -> None:
        self.send_empty(404)


def validate_http_profile() -> None:
    profile = os.environ.get("RECALL_HTTP_PROFILE", "")
    if profile not in {"", "public-edge", "public-mcp"}:
        raise RuntimeError("unsupported HTTP profile")
    if os.environ.get("RECALL_CANONICAL_INGEST_PUBLIC") == "1" and (
        os.environ.get("RECALL_AUTH_REQUIRED", "0") != "1"
        or os.environ.get("RECALL_CANONICAL_V2_ENABLED") != "1"
    ):
        raise RuntimeError(
            "public canonical ingest requires authentication and canonical v2"
        )
    if os.environ.get("RECALL_CANONICAL_MCP_ENABLED") == "1" and (
        os.environ.get("RECALL_AUTH_REQUIRED", "0") != "1"
        or os.environ.get("RECALL_CANONICAL_V2_ENABLED") != "1"
    ):
        raise RuntimeError(
            "canonical MCP requires authentication and canonical v2"
        )
    if profile in {"public-edge", "public-mcp"}:
        if os.environ.get("RECALL_AUTH_REQUIRED", "0") != "1":
            raise RuntimeError("public HTTP profile requires authentication")
        if os.environ.get("RECALL_TRUST_TAILSCALE_HEADERS", "0") == "1":
            raise RuntimeError("public HTTP profile forbids trusted identity headers")


def configure_runtime(dsn: str) -> None:
    Handler.store = BrainStore(dsn, semantic_runtime=SemanticRuntime.from_env())
    if os.environ.get("RECALL_CANONICAL_V2_ENABLED") == "1":
        Handler.archive_store = build_archive_store()
        Handler.canonical_plane = CanonicalPlane(
            Handler.store,
            Handler.archive_store,
        )
        Handler.canonical_retrieval = (
            CanonicalRetrieval(Handler.store, Handler.archive_store)
            if os.environ.get("RECALL_CANONICAL_MCP_ENABLED") == "1"
            else None
        )
    else:
        Handler.archive_store = None
        Handler.canonical_plane = None
        Handler.canonical_retrieval = None


def serve(dsn: str, host: str = "127.0.0.1", port: int = 8788) -> None:
    validate_http_profile()
    if os.environ.get("RECALL_AUTH_REQUIRED", "0") != "1" and host not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise RuntimeError("authentication is required for a non-loopback TCP bind")
    configure_runtime(dsn)
    server = ThreadingHTTPServer((host, port), Handler)
    LOG.info("brainstore listening host=%s port=%s", host, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        Handler.store.close()


class ThreadingUnixHTTPServer(
    socketserver.ThreadingMixIn, socketserver.UnixStreamServer
):
    daemon_threads = True
    is_unix_socket = True


def serve_unix(dsn: str, path: str) -> None:
    validate_http_profile()
    try:
        existing = os.lstat(path)
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISSOCK(existing.st_mode):
            raise RuntimeError("refusing to replace a non-socket Unix path")
        os.unlink(path)
    configure_runtime(dsn)
    server = ThreadingUnixHTTPServer(path, Handler)
    os.chmod(path, 0o600)
    LOG.info("brainstore listening unix_socket=%s", path)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        Handler.store.close()
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"), format="%(levelname)s %(message)s"
    )
    if os.environ.get("RECALL_UNIX_SOCKET"):
        serve_unix(os.environ["RECALL_DATABASE_URL"], os.environ["RECALL_UNIX_SOCKET"])
    else:
        serve(
            os.environ["RECALL_DATABASE_URL"],
            os.environ.get("RECALL_HOST", "127.0.0.1"),
            int(os.environ.get("RECALL_PORT", "8788")),
        )
