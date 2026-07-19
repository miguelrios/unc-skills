"""Closed runtime validators for Recall v2 boundary contracts.

The JSON Schema catalog is the portable description. These validators own cross-field rules that
JSON Schema cannot express clearly and keep the core runtime dependency-free.
"""

from __future__ import annotations

import json
import hashlib
import math
import re
from datetime import datetime
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit


class ContractError(ValueError):
    """A boundary value is unsafe or ambiguous."""


CONTRACT_VERSION = 1
MAX_CONTRACT_BYTES = 4_000_000
MAX_AGGREGATE_NODES = 2048
MAX_AGGREGATE_DEPTH = 8
IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{1,159}\Z")
OPAQUE_ID_RE = re.compile(r"[a-z][a-z0-9_]{2,31}_[A-Za-z0-9_-]{16,128}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
GIT_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
OBJECT_KEY_RE = re.compile(r"objects/[0-9a-f]{2}/[A-Za-z0-9_-]{32,128}\Z")
MCP_SCOPES = frozenset({
    "recall:search",
    "recall:show",
    "recall:related",
    "recall:answer",
    "recall:capture",
    "recall:forget",
    "recall:status",
})
AUTHORITY_SCOPES = MCP_SCOPES | {
    "recall:ingest",
    "recall:archive:read",
    "recall:archive:write",
    "recall:admin",
}
FORBIDDEN_PUBLIC_KEYS = frozenset({
    "answer",
    "body",
    "content",
    "conversation",
    "credential",
    "path",
    "payload",
    "prompt",
    "query",
    "raw",
    "selector",
    "text",
    "token",
    "trace",
    "transcript",
    "url",
})


def _copy(value: Any) -> Any:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError) as error:
        raise ContractError("contract must be finite JSON") from error
    if len(encoded) > MAX_CONTRACT_BYTES:
        raise ContractError("contract exceeds byte bound")
    return json.loads(encoded)


def _closed(value: Any, *, required: set[str], optional: set[str] = set()) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("contract must be an object")
    missing = required - set(value)
    unknown = set(value) - required - optional
    if missing or unknown:
        raise ContractError("contract fields are incomplete or unknown")
    return value


def _string(value: Any, *, maximum: int = 4096, minimum: int = 1) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise ContractError("string field is invalid")
    return value


def _identity(value: Any) -> str:
    text = _string(value, maximum=160, minimum=2)
    if not IDENTITY_RE.fullmatch(text):
        raise ContractError("identity field is invalid")
    return text


def _opaque_id(value: Any, prefix: str) -> str:
    text = _string(value, maximum=160)
    if not OPAQUE_ID_RE.fullmatch(text) or not text.startswith(prefix + "_"):
        raise ContractError("opaque identity field is invalid")
    return text


def _sha256(value: Any) -> str:
    text = _string(value, maximum=64)
    if not SHA256_RE.fullmatch(text):
        raise ContractError("digest field is invalid")
    return text


def _timestamp(value: Any) -> str:
    text = _string(value, maximum=64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ContractError("timestamp field is invalid") from error
    if parsed.tzinfo is None:
        raise ContractError("timestamp field is invalid")
    return text


def _integer(value: Any, *, minimum: int = 0, maximum: int = 2**63 - 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ContractError("integer field is invalid")
    return value


def _number(value: Any, *, minimum: float = 0, maximum: float = 1) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise ContractError("number field is invalid")
    return float(value)


def _enum(value: Any, allowed: set[str] | frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ContractError("enum field is invalid")
    return value


def _unique_strings(
    value: Any,
    *,
    validator: Callable[[Any], str] = _identity,
    maximum: int = 256,
    empty: bool = True,
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum or (not empty and not value):
        raise ContractError("string list field is invalid")
    result = [validator(item) for item in value]
    if len(result) != len(set(result)):
        raise ContractError("string list field contains duplicates")
    return result


def _receipt(value: Any) -> str:
    text = _string(value, maximum=2048)
    parsed = urlsplit(text)
    if parsed.scheme != "recall" or not parsed.netloc or not parsed.path.strip("/"):
        raise ContractError("receipt field is invalid")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if (
        parsed.username
        or parsed.password
        or (query and (set(query) != {"rev"} or len(query["rev"]) != 1
                       or not re.fullmatch(r"[1-9][0-9]*", query["rev"][0])))
        or (parsed.fragment and not re.fullmatch(r"item=[0-9]+", parsed.fragment))
    ):
        raise ContractError("receipt field is invalid")
    return text


def _receipt_source(value: str) -> str:
    return urlsplit(_receipt(value)).netloc


def _common(value: Any, expected: str | None) -> tuple[str, dict[str, Any]]:
    copied = _copy(value)
    if not isinstance(copied, dict):
        raise ContractError("contract must be an object")
    contract = copied.get("contract")
    if not isinstance(contract, str) or (expected is not None and contract != expected):
        raise ContractError("contract discriminator is invalid")
    if copied.get("schema_version") != CONTRACT_VERSION:
        raise ContractError("contract schema version is invalid")
    return contract, copied


def _authority(value: dict[str, Any]) -> None:
    _closed(
        value,
        required={
            "contract", "schema_version", "tenant_id", "principal_id", "subject",
            "audience", "scopes", "source_ids", "expires_at",
        },
    )
    _identity(value["tenant_id"])
    _identity(value["principal_id"])
    _string(value["subject"], maximum=320)
    _string(value["audience"], maximum=320)
    scopes = _unique_strings(
        value["scopes"],
        validator=lambda item: _enum(item, AUTHORITY_SCOPES),
        maximum=len(AUTHORITY_SCOPES),
        empty=False,
    )
    sources = _unique_strings(value["source_ids"], maximum=256)
    _timestamp(value["expires_at"])
    if any(scope in scopes for scope in {
        "recall:ingest", "recall:archive:read", "recall:archive:write",
        "recall:capture", "recall:forget",
    }) and not sources:
        raise ContractError("source-scoped authority has no sources")
    archive_scopes = {"recall:archive:read", "recall:archive:write"} & set(scopes)
    application_scopes = set(scopes) - {"recall:archive:read", "recall:archive:write"}
    if archive_scopes and application_scopes:
        raise ContractError("archive and application authorities must be separate")


def _artifact(value: dict[str, Any]) -> None:
    _closed(
        value,
        required={
            "contract", "schema_version", "tenant_id", "source_id", "artifact_id",
            "storage_backend", "object_key", "content_sha256", "size_bytes", "media_type",
            "encryption", "version_id", "created_at",
        },
        optional={"deleted_at"},
    )
    _identity(value["tenant_id"])
    _identity(value["source_id"])
    _opaque_id(value["artifact_id"], "art")
    _enum(value["storage_backend"], {"filesystem", "s3"})
    object_key = _string(value["object_key"], maximum=256)
    if (
        not OBJECT_KEY_RE.fullmatch(object_key)
        or ".." in object_key
        or object_key.startswith("/")
        or "://" in object_key
    ):
        raise ContractError("archive object key is not opaque")
    _sha256(value["content_sha256"])
    _integer(value["size_bytes"], maximum=2**50)
    _string(value["media_type"], maximum=255)
    encryption = _enum(value["encryption"], {
        "filesystem-owner-only", "sse-s3", "sse-kms", "sse-c",
    })
    if value["storage_backend"] == "filesystem" and encryption != "filesystem-owner-only":
        raise ContractError("filesystem artifact encryption is invalid")
    if value["storage_backend"] == "s3" and encryption == "filesystem-owner-only":
        raise ContractError("S3 artifact encryption is invalid")
    _string(value["version_id"], maximum=256)
    _timestamp(value["created_at"])
    if "deleted_at" in value:
        _timestamp(value["deleted_at"])


def _document(value: dict[str, Any]) -> None:
    _closed(
        value,
        required={
            "contract", "schema_version", "tenant_id", "principal_id", "source_id",
            "document_id", "native_id", "revision", "kind", "occurred_at", "observed_at",
            "content_sha256", "text_redacted", "text_sha256", "artifact_id", "visibility",
            "is_current",
        },
        optional={"native_parent_id", "supersedes_receipt", "deleted_at"},
    )
    _identity(value["tenant_id"])
    _identity(value["principal_id"])
    _identity(value["source_id"])
    _opaque_id(value["document_id"], "doc")
    _identity(value["native_id"])
    _integer(value["revision"], minimum=1)
    _string(value["kind"], maximum=128)
    _timestamp(value["occurred_at"])
    _timestamp(value["observed_at"])
    _sha256(value["content_sha256"])
    text = _string(value["text_redacted"], maximum=1_000_000, minimum=0)
    if _sha256(value["text_sha256"]) != hashlib.sha256(text.encode()).hexdigest():
        raise ContractError("redacted text digest does not match")
    _opaque_id(value["artifact_id"], "art")
    _enum(value["visibility"], {"private", "shared"})
    if not isinstance(value["is_current"], bool):
        raise ContractError("current-state flag is invalid")
    if "native_parent_id" in value:
        _identity(value["native_parent_id"])
    if "supersedes_receipt" in value:
        if _receipt_source(value["supersedes_receipt"]) != value["source_id"]:
            raise ContractError("superseded receipt source mismatch")
    if "deleted_at" in value:
        _timestamp(value["deleted_at"])
        if value["is_current"]:
            raise ContractError("deleted document cannot be current")


def _chunk(value: dict[str, Any]) -> None:
    _closed(
        value,
        required={
            "contract", "schema_version", "tenant_id", "source_id", "document_id",
            "chunk_id", "ordinal", "text_redacted", "text_sha256", "receipt",
        },
    )
    _identity(value["tenant_id"])
    _identity(value["source_id"])
    _opaque_id(value["document_id"], "doc")
    _opaque_id(value["chunk_id"], "chk")
    _integer(value["ordinal"], maximum=1_000_000)
    text = _string(value["text_redacted"], maximum=64_000, minimum=0)
    if _sha256(value["text_sha256"]) != hashlib.sha256(text.encode()).hexdigest():
        raise ContractError("redacted text digest does not match")
    if _receipt_source(value["receipt"]) != value["source_id"]:
        raise ContractError("chunk receipt source mismatch")


def _job(value: dict[str, Any]) -> None:
    _closed(
        value,
        required={
            "contract", "schema_version", "tenant_id", "source_id", "job_id",
            "connector_id", "mode", "status", "attempt", "created_at", "updated_at",
        },
        optional={"cursor_sha256", "error_code", "completed_at"},
    )
    _identity(value["tenant_id"])
    _identity(value["source_id"])
    _opaque_id(value["job_id"], "job")
    _identity(value["connector_id"])
    _enum(value["mode"], {"backfill", "incremental", "reconcile", "forget"})
    status = _enum(value["status"], {
        "queued", "leased", "committed", "retryable", "parked", "failed",
    })
    _integer(value["attempt"], maximum=10_000)
    _timestamp(value["created_at"])
    _timestamp(value["updated_at"])
    if "cursor_sha256" in value:
        _sha256(value["cursor_sha256"])
    if "error_code" in value:
        _identity(value["error_code"])
        if status not in {"retryable", "parked", "failed"}:
            raise ContractError("successful job cannot carry an error")
    if "completed_at" in value:
        _timestamp(value["completed_at"])
        if status not in {"committed", "failed"}:
            raise ContractError("incomplete job cannot have completion time")


def _forget(value: dict[str, Any]) -> None:
    _closed(
        value,
        required={
            "contract", "schema_version", "tenant_id", "principal_id", "source_id",
            "target_receipt", "mode", "reason", "requested_at", "idempotency_key",
        },
    )
    _identity(value["tenant_id"])
    _identity(value["principal_id"])
    _identity(value["source_id"])
    if _receipt_source(value["target_receipt"]) != value["source_id"]:
        raise ContractError("forget target source mismatch")
    _enum(value["mode"], {"authoritative_delete", "explicit_forget"})
    _enum(value["reason"], {"upstream_deleted", "owner_requested", "retention_expired"})
    _timestamp(value["requested_at"])
    _string(value["idempotency_key"], maximum=200)


def _redirect(value: dict[str, Any]) -> None:
    _closed(
        value,
        required={
            "contract", "schema_version", "tenant_id", "source_id", "old_receipt",
            "new_receipt", "reason", "created_at",
        },
    )
    _identity(value["tenant_id"])
    source = _identity(value["source_id"])
    old_source = _receipt_source(value["old_receipt"])
    new_source = _receipt_source(value["new_receipt"])
    if old_source != source or new_source != source or value["old_receipt"] == value["new_receipt"]:
        raise ContractError("receipt redirect lineage is invalid")
    _enum(value["reason"], {"v2_migration", "identity_repair"})
    _timestamp(value["created_at"])


def _retrieval_request(value: dict[str, Any]) -> None:
    _closed(
        value,
        required={
            "contract", "schema_version", "tenant_id", "principal_id", "request_id",
            "query", "source_ids", "mode", "limit",
        },
        optional={"since", "until", "source_families", "include_superseded"},
    )
    _identity(value["tenant_id"])
    _identity(value["principal_id"])
    _opaque_id(value["request_id"], "req")
    _string(value["query"], maximum=8192)
    _unique_strings(value["source_ids"], maximum=256, empty=False)
    _enum(value["mode"], {"fast", "planned", "exact"})
    _integer(value["limit"], minimum=1, maximum=20)
    if "source_families" in value:
        _unique_strings(value["source_families"], maximum=32)
    if "since" in value:
        _timestamp(value["since"])
    if "until" in value:
        _timestamp(value["until"])
    if "include_superseded" in value and not isinstance(value["include_superseded"], bool):
        raise ContractError("superseded filter is invalid")


def _retrieval_result(value: dict[str, Any]) -> None:
    _closed(
        value,
        required={
            "contract", "schema_version", "tenant_id", "principal_id", "request_id",
            "results", "gaps", "contradictions", "semantic_complete", "elapsed_ms",
        },
    )
    _identity(value["tenant_id"])
    _identity(value["principal_id"])
    _opaque_id(value["request_id"], "req")
    if not isinstance(value["results"], list) or len(value["results"]) > 20:
        raise ContractError("retrieval results are invalid")
    for item in value["results"]:
        _closed(
            item,
            required={
                "receipt", "source_id", "document_id", "chunk_id", "text_redacted",
                "score", "why", "occurred_at", "is_current",
            },
            optional={"supersedes_receipt"},
        )
        source = _identity(item["source_id"])
        if _receipt_source(item["receipt"]) != source:
            raise ContractError("retrieval result source mismatch")
        _opaque_id(item["document_id"], "doc")
        _opaque_id(item["chunk_id"], "chk")
        _string(item["text_redacted"], maximum=64_000, minimum=0)
        _number(item["score"])
        _unique_strings(
            item["why"], validator=lambda child: _string(child, maximum=64), maximum=16,
            empty=False,
        )
        _timestamp(item["occurred_at"])
        if not isinstance(item["is_current"], bool):
            raise ContractError("retrieval result current-state flag is invalid")
        if "supersedes_receipt" in item and _receipt_source(item["supersedes_receipt"]) != source:
            raise ContractError("retrieval result supersession mismatch")
    _unique_strings(
        value["gaps"], validator=lambda item: _string(item, maximum=512), maximum=32,
    )
    _unique_strings(
        value["contradictions"], validator=lambda item: _string(item, maximum=1024), maximum=32,
    )
    if not isinstance(value["semantic_complete"], bool):
        raise ContractError("semantic completeness flag is invalid")
    _number(value["elapsed_ms"], maximum=120_000)


def _mcp_principal(value: dict[str, Any]) -> None:
    _closed(
        value,
        required={
            "contract", "schema_version", "tenant_id", "principal_id", "subject",
            "audience", "scopes", "source_ids", "expires_at",
        },
    )
    _identity(value["tenant_id"])
    _identity(value["principal_id"])
    _string(value["subject"], maximum=320)
    _string(value["audience"], maximum=320)
    scopes = _unique_strings(
        value["scopes"], validator=lambda item: _enum(item, MCP_SCOPES),
        maximum=len(MCP_SCOPES), empty=False,
    )
    sources = _unique_strings(value["source_ids"], maximum=256, empty=False)
    _timestamp(value["expires_at"])
    if any(scope in scopes for scope in {"recall:capture", "recall:forget"}) and len(sources) != 1:
        raise ContractError("write-capable MCP principal must bind one source")


def _model_payload(value: dict[str, Any]) -> None:
    _closed(
        value,
        required={
            "contract", "schema_version", "tenant_id", "principal_id", "purpose",
            "privacy_policy_version", "texts",
        },
    )
    _identity(value["tenant_id"])
    _identity(value["principal_id"])
    _enum(value["purpose"], {"embedding", "rerank", "answer", "privacy_judge"})
    _string(value["privacy_policy_version"], maximum=128)
    texts = value["texts"]
    if not isinstance(texts, list) or not 1 <= len(texts) <= 128:
        raise ContractError("model text list is invalid")
    total = 0
    for item in texts:
        _closed(item, required={"receipt", "text_redacted", "text_sha256"})
        _receipt(item["receipt"])
        text = _string(item["text_redacted"], maximum=64_000, minimum=0)
        total += len(text.encode())
        if _sha256(item["text_sha256"]) != hashlib.sha256(text.encode()).hexdigest():
            raise ContractError("model text digest does not match")
    if total > 2_000_000:
        raise ContractError("model payload exceeds byte bound")


def _aggregate_tree(value: Any, *, depth: int = 0) -> int:
    if depth > MAX_AGGREGATE_DEPTH:
        raise ContractError("aggregate evidence exceeds depth bound")
    if isinstance(value, dict):
        if len(value) > 128:
            raise ContractError("aggregate evidence object is too large")
        nodes = 1
        for key, child in value.items():
            if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_.@-]{0,63}", key):
                raise ContractError("aggregate evidence key is invalid")
            if key.casefold() in FORBIDDEN_PUBLIC_KEYS:
                raise ContractError("content-bearing public evidence key is forbidden")
            nodes += _aggregate_tree(child, depth=depth + 1)
            if nodes > MAX_AGGREGATE_NODES:
                raise ContractError("aggregate evidence exceeds node bound")
        return nodes
    if isinstance(value, list):
        if len(value) > 64:
            raise ContractError("aggregate evidence list is too large")
        nodes = 1
        for child in value:
            nodes += _aggregate_tree(child, depth=depth + 1)
            if nodes > MAX_AGGREGATE_NODES:
                raise ContractError("aggregate evidence exceeds node bound")
        return nodes
    if isinstance(value, bool):
        return 1
    if isinstance(value, int) and not isinstance(value, bool):
        return 1
    if isinstance(value, float) and math.isfinite(value):
        return 1
    if value is None:
        return 1
    raise ContractError("public evidence must contain aggregates only")


def _public_evidence(value: dict[str, Any]) -> None:
    _closed(
        value,
        required={
            "contract", "schema_version", "loop_id", "status", "git_sha",
            "manifest_sha256", "test_counts", "metrics",
        },
    )
    _identity(value["loop_id"])
    _enum(value["status"], {"complete", "at_bound"})
    git_sha = _string(value["git_sha"], maximum=40)
    if not GIT_SHA_RE.fullmatch(git_sha):
        raise ContractError("git digest is invalid")
    _sha256(value["manifest_sha256"])
    _aggregate_tree(value["test_counts"])
    _aggregate_tree(value["metrics"])


VALIDATORS: dict[str, Callable[[dict[str, Any]], None]] = {
    "recall.principal-authority.v1": _authority,
    "recall.artifact-ref.v1": _artifact,
    "recall.canonical-document.v1": _document,
    "recall.canonical-chunk.v1": _chunk,
    "recall.ingest-job.v1": _job,
    "recall.forget-request.v1": _forget,
    "recall.receipt-redirect.v1": _redirect,
    "recall.retrieval-request.v1": _retrieval_request,
    "recall.retrieval-result.v1": _retrieval_result,
    "recall.mcp-principal.v1": _mcp_principal,
    "recall.model-payload.v1": _model_payload,
    "recall.public-evidence.v1": _public_evidence,
}


def validate_contract(value: Any, *, expected: str | None = None) -> dict[str, Any]:
    """Validate and return a finite detached copy of one v2 boundary value."""
    contract, copied = _common(value, expected)
    validator = VALIDATORS.get(contract)
    if validator is None:
        raise ContractError("contract discriminator is unsupported")
    validator(copied)
    return copied


def validate_retrieval_exchange(
    authority: Any,
    request: Any,
    result: Any,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Prove one result stayed inside the authority and request source boundary."""
    auth = validate_contract(authority, expected="recall.principal-authority.v1")
    query = validate_contract(request, expected="recall.retrieval-request.v1")
    answer = validate_contract(result, expected="recall.retrieval-result.v1")
    identity = (auth["tenant_id"], auth["principal_id"])
    if identity != (query["tenant_id"], query["principal_id"]):
        raise ContractError("retrieval request authority mismatch")
    if identity != (answer["tenant_id"], answer["principal_id"]):
        raise ContractError("retrieval result authority mismatch")
    if query["request_id"] != answer["request_id"]:
        raise ContractError("retrieval request identity mismatch")
    granted = set(auth["source_ids"])
    requested = set(query["source_ids"])
    returned = {item["source_id"] for item in answer["results"]}
    if not requested <= granted or not returned <= requested:
        raise ContractError("retrieval source scope mismatch")
    required_scope = "recall:answer" if query["mode"] == "planned" else "recall:search"
    if required_scope not in auth["scopes"]:
        raise ContractError("retrieval scope is missing")
    return auth, query, answer
