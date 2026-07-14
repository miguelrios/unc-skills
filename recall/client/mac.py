from __future__ import annotations

import copy
import hashlib
import json
import os
import platform
import re
import ssl
import stat
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

try:
    from collector.collector import canonical_json, sanitize
except ModuleNotFoundError:  # installed bundle imports sibling package
    from ..collector.collector import canonical_json, sanitize

from privacy.policy import PrivacyPolicy, summarize_receipts


FORBIDDEN_PRIVATE_PATHS = (
    "library/application support/chatgpt",
    "library/containers/com.openai.chat",
    "library/application support/cowork",
    "library/containers/com.anthropic.cowork",
)
PATTERNS = {"claude": "*.jsonl", "codex": "rollout-*.jsonl"}
SAFE_EXPORT_SUFFIXES = {".json", ".jsonl"}
SOURCE_ID = re.compile(r"^[A-Za-z0-9_.:@-]{3,160}$")
MAX_INGEST_BYTES = 8_000_000
MAX_INGEST_EVENTS = 500


class PrivacyError(ValueError):
    pass


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_visibility(value: str) -> str:
    if value not in {"private", "shared"}:
        raise ValueError("visibility must be private or shared")
    return value


def _validate_source_id(value: str) -> str:
    if not SOURCE_ID.fullmatch(value):
        raise ValueError("invalid source id")
    return value


def _forbid_private_app_path(path: Path, home: Path) -> None:
    try:
        relative = path.expanduser().absolute().relative_to(home.expanduser().absolute())
        normalized = relative.as_posix().casefold()
    except ValueError:
        normalized = path.expanduser().absolute().as_posix().casefold()
    if any(fragment in normalized for fragment in FORBIDDEN_PRIVATE_PATHS):
        raise PrivacyError("unsupported private application path; use an explicit supported export")


def approved_files(harness: str, root: Path, *, home: Path) -> list[Path]:
    if harness not in PATTERNS:
        raise ValueError("harness must be claude or codex")
    requested = root.expanduser().absolute()
    _forbid_private_app_path(requested, home)
    if not requested.exists():
        return []
    canonical_root = requested.resolve(strict=True)
    files: list[Path] = []
    for candidate in sorted(requested.rglob(PATTERNS[harness])):
        if candidate.is_symlink():
            raise PrivacyError(f"symlink escape or alias is not allowed: {candidate}")
        try:
            canonical = candidate.resolve(strict=True)
            canonical.relative_to(canonical_root)
        except (OSError, ValueError) as exc:
            raise PrivacyError(f"path escape outside approved root: {candidate}") from exc
        if not canonical.is_file():
            continue
        files.append(canonical)
    return files


def dry_run_manifest(*, selections: list[dict], visibility: str, home: Path | None = None) -> dict:
    visibility = _validate_visibility(visibility)
    home = (home or Path.home()).expanduser().absolute()
    selected = []
    files = []
    for selection in selections:
        harness = str(selection["harness"])
        root = Path(selection["root"]).expanduser().absolute()
        canonical_root = root.resolve(strict=True) if root.exists() else root
        chosen = approved_files(harness, root, home=home)
        selected.append({
            "harness": harness,
            "root": str(canonical_root),
            "eligible_files": len(chosen),
        })
        for path in chosen:
            files.append({
                "harness": harness,
                "root": str(canonical_root),
                "relative_path": path.relative_to(canonical_root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            })
    return {
        "schema_version": 1,
        "mode": "dry-run",
        "network_requests": 0,
        "visibility": visibility,
        "selections": selected,
        "files": files,
        "totals": {"files": len(files), "bytes": sum(item["bytes"] for item in files)},
    }


def load_file_token(path: Path) -> str:
    mode = stat.S_IMODE(path.expanduser().stat().st_mode)
    if mode & 0o077:
        raise PermissionError("token file must not be accessible by group or other")
    value = json.loads(path.expanduser().read_text()).get("token")
    if not isinstance(value, str) or not value:
        raise ValueError("token file has no token")
    return value


def load_keychain_token(service: str, account: str) -> str:
    if not service or not account:
        raise ValueError("Keychain service and account are required")
    if platform.system() != "Darwin":
        raise RuntimeError("Keychain lookup is available only on macOS")
    import ctypes
    import ctypes.util

    framework = ctypes.util.find_library("Security")
    if not framework:
        raise RuntimeError("macOS Security framework is unavailable")
    security = ctypes.CDLL(framework)
    security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
    security.SecKeychainItemFreeContent.restype = ctypes.c_int32
    service_bytes = service.encode()
    account_bytes = account.encode()
    length = ctypes.c_uint32()
    data = ctypes.c_void_p()
    item = ctypes.c_void_p()
    status = security.SecKeychainFindGenericPassword(
        None,
        len(service_bytes), ctypes.c_char_p(service_bytes),
        len(account_bytes), ctypes.c_char_p(account_bytes),
        ctypes.byref(length), ctypes.byref(data), ctypes.byref(item),
    )
    if status != 0:
        raise RuntimeError(f"Keychain lookup failed with OSStatus {status}")
    try:
        value = ctypes.string_at(data, length.value).decode()
        if not value:
            raise ValueError("Keychain item is empty")
        return value
    finally:
        security.SecKeychainItemFreeContent(None, data)
        core = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        core.CFRelease(item)


def store_keychain_token(service: str, account: str, token: str) -> None:
    """Add or update a generic password without placing secret bytes in argv."""
    if platform.system() != "Darwin":
        raise RuntimeError("Keychain storage is available only on macOS")
    if not service or not account or not token:
        raise ValueError("Keychain service, account, and stdin token are required")
    import ctypes
    import ctypes.util

    framework = ctypes.util.find_library("Security")
    if not framework:
        raise RuntimeError("macOS Security framework is unavailable")
    security = ctypes.CDLL(framework)
    security.SecKeychainFindGenericPassword.restype = ctypes.c_int32
    security.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
    security.SecKeychainAddGenericPassword.restype = ctypes.c_int32
    service_bytes = service.encode()
    account_bytes = account.encode()
    token_bytes = token.encode()
    item = ctypes.c_void_p()
    status = security.SecKeychainFindGenericPassword(
        None,
        len(service_bytes), ctypes.c_char_p(service_bytes),
        len(account_bytes), ctypes.c_char_p(account_bytes),
        None, None, ctypes.byref(item),
    )
    if status == 0:
        status = security.SecKeychainItemModifyAttributesAndData(
            item, None, len(token_bytes), ctypes.c_char_p(token_bytes)
        )
        core = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
        core.CFRelease(item)
    elif status == -25300:  # errSecItemNotFound
        status = security.SecKeychainAddGenericPassword(
            None,
            len(service_bytes), ctypes.c_char_p(service_bytes),
            len(account_bytes), ctypes.c_char_p(account_bytes),
            len(token_bytes), ctypes.c_char_p(token_bytes),
            None,
        )
    if status != 0:
        raise RuntimeError(f"Keychain write failed with OSStatus {status}")


def _envelope(*, source_id: str, native_id: str, kind: str, content: Any,
              principal_id: str, visibility: str, provenance: dict,
              occurred_at: str | None = None, parent: str | None = None) -> dict:
    clean = sanitize(content)
    return {
        "schema_version": 1,
        "source_id": _validate_source_id(source_id),
        "native_id": native_id,
        "native_parent_id": parent or native_id,
        "kind": kind,
        "occurred_at": occurred_at or iso_now(),
        "observed_at": occurred_at or iso_now(),
        "principal_id": principal_id,
        "visibility": _validate_visibility(visibility),
        "content_type": "application/json",
        "content": clean,
        "provenance": sanitize(provenance),
        "content_sha256": hashlib.sha256(canonical_json(clean)).hexdigest(),
    }


class BrainClient:
    def __init__(self, *, endpoint: str, token: str, source_id: str,
                 principal_id: str = "owner", visibility: str = "private",
                 privacy: PrivacyPolicy | None = None):
        if not endpoint.startswith(("https://", "http://127.0.0.1:", "http://localhost:")):
            raise ValueError("endpoint must use HTTPS except for loopback tests")
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.source_id = _validate_source_id(source_id)
        self.principal_id = principal_id
        self.visibility = _validate_visibility(visibility)
        self.privacy = privacy or PrivacyPolicy(mode="off")

    def _request(self, path: str, *, body: dict | None = None,
                 idempotency_key: str | None = None, method: str | None = None) -> dict:
        data = canonical_json(body) if body is not None else None
        headers = {"Authorization": "Bearer " + self.token}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(
            self.endpoint + path,
            data=data,
            method=method or ("POST" if data is not None else "GET"),
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=60, context=ssl.create_default_context()) as response:
            return json.loads(response.read())

    def ingest(self, events: list[dict]) -> dict:
        if not events:
            raise ValueError("cannot ingest an empty event list")
        prepared = []
        receipts = []
        for event in events:
            decision = (
                PrivacyPolicy(mode="off").apply(event["content"])
                if event.get("kind") == "tombstone"
                else self.privacy.apply(event["content"])
            )
            receipts.append(decision.receipt())
            if decision.action == "drop":
                continue
            candidate = copy.deepcopy(event)
            candidate["content"] = decision.value
            candidate["content_sha256"] = hashlib.sha256(canonical_json(decision.value)).hexdigest()
            prepared.append(candidate)
        privacy = summarize_receipts(receipts, self.privacy.mode)
        if not prepared:
            return {"status": "privacy_filtered", "inserted": 0, "duplicate_events": 0, "receipts": [], "replay": False, "privacy": privacy}
        return self._ingest_prepared(prepared, privacy)

    def _ingest_prepared(self, prepared: list[dict], privacy: dict[str, Any] | None = None) -> dict:
        if len(canonical_json({"events": prepared})) > MAX_INGEST_BYTES:
            raise ValueError("ingest batch exceeds client size limit")
        key = "client-v1-" + hashlib.sha256(canonical_json(prepared)).hexdigest()
        acknowledgement = self._request("/v1/ingest/batches", body={"events": prepared}, idempotency_key=key)
        if privacy is not None and self.privacy.mode != "off":
            acknowledgement["privacy"] = privacy
        return acknowledgement

    def search(self, query: str, *, limit: int = 10) -> dict:
        return self._request("/v1/search", body={"query": query, "limit": limit, "filters": {}})

    def resolve(self, receipt: str) -> dict:
        return self._request("/v1/receipts/resolve?" + urllib.parse.urlencode({"receipt": receipt}))

    def doctor(self) -> dict:
        return self._request("/v1/doctor")


class MemoryClient(BrainClient):
    def put(self, text: str, *, provenance: dict | None = None) -> dict:
        if not text.strip():
            raise ValueError("memory text must not be empty")
        native_id = "memory-" + uuid.uuid4().hex
        decision = self.privacy.apply({"text": text})
        privacy = summarize_receipts([decision.receipt()], self.privacy.mode)
        if decision.action == "drop":
            acknowledgement = {
                "status": "privacy_filtered", "inserted": 0, "duplicate_events": 0,
                "receipts": [], "replay": False, "privacy": privacy,
            }
            return {
                "kind": "memory", "native_id": native_id,
                "privacy": {**privacy, "action": "drop"},
                "acknowledgement": acknowledgement,
            }
        event = _envelope(
            source_id=self.source_id,
            native_id=native_id,
            kind="memory",
            content=decision.value,
            principal_id=self.principal_id,
            visibility=self.visibility,
            provenance=provenance or {"uri": "manual://recall_put"},
        )
        acknowledgement = self._ingest_prepared(
            [event], privacy if self.privacy.mode != "off" else None
        )
        result = {"kind": "memory", "native_id": native_id, "acknowledgement": acknowledgement}
        if acknowledgement["receipts"]:
            result["receipt"] = acknowledgement["receipts"][0]
        if "privacy" in acknowledgement:
            actions = acknowledgement["privacy"]["actions"]
            action = next(iter(actions)) if len(actions) == 1 else "mixed"
            result["privacy"] = {**acknowledgement["privacy"], "action": action}
        return result

    def delete(self, receipt: str) -> dict:
        event_part = receipt.split("#", 1)[0]
        try:
            base, revision = event_part.rsplit("?rev=", 1)
            if int(revision) < 1 or not base.startswith("recall://"):
                raise ValueError
            base = base.removeprefix("recall://")
            source_id, native_id = base.split("/", 1)
            if not native_id:
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid receipt") from exc
        if source_id != self.source_id:
            raise PrivacyError("receipt source does not match client source")
        event = _envelope(
            source_id=self.source_id,
            native_id=native_id,
            kind="tombstone",
            content={"target_native_id": native_id, "deleted_receipt": event_part},
            principal_id=self.principal_id,
            visibility=self.visibility,
            provenance={"uri": "manual://recall_delete"},
        )
        acknowledgement = self.ingest([event])
        return {"kind": "tombstone", "native_id": native_id, "receipt": acknowledgement["receipts"][0], "acknowledgement": acknowledgement}


def _safe_member(info: zipfile.ZipInfo) -> PurePosixPath:
    member = PurePosixPath(info.filename)
    if member.is_absolute() or ".." in member.parts or not member.parts:
        raise PrivacyError(f"unsafe archive member: {info.filename}")
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        raise PrivacyError(f"symlink archive member is not allowed: {info.filename}")
    return member


def _records_from_bytes(data: bytes, suffix: str) -> list[Any]:
    text = data.decode("utf-8")
    if suffix == ".jsonl":
        records = []
        for line in text.splitlines():
            if line.strip():
                records.append(json.loads(line))
        return records
    value = json.loads(text)
    return value if isinstance(value, list) else [value]


class ExportImporter:
    def __init__(self, *, source_id: str, principal_id: str, visibility: str,
                 privacy: PrivacyPolicy | None = None):
        self.source_id = _validate_source_id(source_id)
        self.principal_id = principal_id
        self.visibility = _validate_visibility(visibility)
        self.privacy = privacy or PrivacyPolicy(mode="off")

    def inventory(self, inputs: Iterable[Path]) -> dict:
        records = []
        files = []
        privacy_receipts = []
        for supplied in inputs:
            requested = Path(supplied).expanduser().absolute()
            if requested.is_symlink():
                raise PrivacyError(f"supported export input must not be a symlink: {requested}")
            path = requested.resolve(strict=True)
            if not path.is_file():
                raise PrivacyError(f"supported export input must be a regular file: {path}")
            file_sha = sha256_file(path)
            files.append({"name": path.name, "bytes": path.stat().st_size, "sha256": file_sha})
            if path.suffix.casefold() == ".zip":
                with zipfile.ZipFile(path) as archive:
                    for info in sorted(archive.infolist(), key=lambda item: item.filename):
                        member = _safe_member(info)
                        suffix = member.suffix.casefold()
                        if info.is_dir() or suffix not in SAFE_EXPORT_SUFFIXES:
                            continue
                        for index, content in enumerate(_records_from_bytes(archive.read(info), suffix)):
                            decision = self.privacy.apply(content)
                            privacy_receipts.append(decision.receipt())
                            if decision.action != "drop":
                                records.append(self._export_envelope(path, file_sha, f"{member.as_posix()}#record={index}", index, decision.value))
            elif path.suffix.casefold() in SAFE_EXPORT_SUFFIXES:
                for index, content in enumerate(_records_from_bytes(path.read_bytes(), path.suffix.casefold())):
                    decision = self.privacy.apply(content)
                    privacy_receipts.append(decision.receipt())
                    if decision.action != "drop":
                        records.append(self._export_envelope(path, file_sha, f"{path.name}#record={index}", index, decision.value))
            else:
                raise PrivacyError(f"unsupported export type: {path.suffix}")
        return {"schema_version": 1, "mode": "export-inventory", "network_requests": 0, "files": files, "records": records,
                "privacy": summarize_receipts(privacy_receipts, self.privacy.mode)}

    def _export_envelope(self, path: Path, file_sha: str, member: str, index: int, content: Any) -> dict:
        native_key = f"{file_sha}\x1f{member}\x1f{index}"
        native_id = "export-" + hashlib.sha256(native_key.encode()).hexdigest()
        occurred = iso_mtime(path)
        uri = "export://" + file_sha
        return _envelope(
            source_id=self.source_id,
            native_id=native_id,
            parent="export-session-" + file_sha[:24],
            kind="chat_export",
            content=content,
            principal_id=self.principal_id,
            visibility=self.visibility,
            occurred_at=occurred,
            provenance={
                "uri": uri,
                "original_path": f"{uri}/{member}",
                "archive": path.name,
                "member": member,
            },
        )

    def import_with(self, client: BrainClient, inputs: Iterable[Path]) -> dict:
        inventory = self.inventory(inputs)
        batches: list[list[dict]] = []
        current: list[dict] = []
        for event in inventory["records"]:
            candidate = [*current, event]
            if current and (len(candidate) > MAX_INGEST_EVENTS or len(canonical_json({"events": candidate})) > MAX_INGEST_BYTES):
                batches.append(current)
                current = [event]
            else:
                current = candidate
            if len(canonical_json({"events": current})) > MAX_INGEST_BYTES:
                raise ValueError("one export record exceeds client size limit")
        if current:
            batches.append(current)
        if not batches:
            if inventory["privacy"]["actions"].get("drop", 0):
                return {
                    "inventory": {key: value for key, value in inventory.items() if key != "records"},
                    "records": 0,
                    "acknowledgement": {
                        "status": "privacy_filtered", "inserted": 0,
                        "duplicate_events": 0, "receipts": [], "replay": False,
                        "batches": 0, "privacy": inventory["privacy"],
                    },
                }
            raise ValueError("supported export contains no JSON records")
        if self.privacy.mode == "off":
            acknowledgements = [client.ingest(batch) for batch in batches]
        else:
            acknowledgements = [client._ingest_prepared(batch, inventory["privacy"]) for batch in batches]
        acknowledgement = {
            "status": "committed",
            "inserted": sum(item.get("inserted", 0) for item in acknowledgements),
            "duplicate_events": sum(item.get("duplicate_events", 0) for item in acknowledgements),
            "receipts": [receipt for item in acknowledgements for receipt in item.get("receipts", [])],
            "replay": all(bool(item.get("replay")) for item in acknowledgements),
            "batches": len(acknowledgements),
        }
        return {
            "inventory": {key: value for key, value in inventory.items() if key != "records"},
            "records": len(inventory["records"]),
            "acknowledgement": acknowledgement,
        }
