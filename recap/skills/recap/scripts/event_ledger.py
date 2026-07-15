#!/usr/bin/env python3
"""Streaming, deterministic evidence ledger primitives for Recap."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Iterator


LEDGER_SCHEMA = "recap.event-ledger.v1"
EPISODE_SCHEMA = "recap.episode-index.v1"
PACKET_SCHEMA = "recap.packet-index.v1"
MAX_PACKET_EVENTS = 1000
MAX_PACKET_TEXT_BYTES = 128 * 1024
TIME_GAP_SECONDS = 30 * 60
HEARTBEAT_EVERY = 10_000


class LedgerError(RuntimeError):
    pass


def _canonical_value(value: Any) -> Any:
    if isinstance(value, dict):
        keys = sorted(key for key in value if key != "text")
        if "text" in value:
            keys.append("text")
        return {key: _canonical_value(value[key]) for key in keys}
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    return value


def canonical(value: Any) -> bytes:
    return json.dumps(
        _canonical_value(value), ensure_ascii=False, sort_keys=False, separators=(",", ":"),
    ).encode()


def digest_ids(values: list[str]) -> str:
    return hashlib.sha256("\n".join(values).encode()).hexdigest()


def _private_parent(path: Path) -> Path:
    path = path.expanduser()
    if path.is_symlink():
        raise LedgerError("refusing to write a ledger through a symlink")
    path = path.resolve(strict=False)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.stat().st_mode & 0o077:
        raise LedgerError("ledger directory must have mode 0700")
    return path


class AtomicJsonl:
    def __init__(self, path: Path):
        self.path = _private_parent(path)
        self.temporary = self.path.with_name("." + self.path.name + ".tmp-" + str(os.getpid()))
        if self.temporary.exists() or self.temporary.is_symlink():
            raise LedgerError("ledger temporary path already exists")
        descriptor = os.open(self.temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.fchmod(descriptor, 0o600)
        self.output = os.fdopen(descriptor, "wb")
        self.digest = hashlib.sha256()
        self.count = 0
        self.size = 0
        self.closed = False

    def write(self, value: dict[str, Any]) -> None:
        if self.closed:
            raise LedgerError("ledger writer is closed")
        line = canonical(value) + b"\n"
        self.output.write(line)
        self.digest.update(line)
        self.count += 1
        self.size += len(line)

    def commit(self) -> dict[str, Any]:
        if self.closed:
            raise LedgerError("ledger writer is closed")
        self.output.flush()
        os.fsync(self.output.fileno())
        self.output.close()
        os.replace(self.temporary, self.path)
        os.chmod(self.path, 0o600)
        self.closed = True
        return {
            "path": str(self.path),
            "sha256": self.digest.hexdigest(),
            "records": self.count,
            "bytes": self.size,
            "mode": "0o600",
        }

    def abort(self) -> None:
        if not self.closed:
            self.output.close()
            self.closed = True
        if self.temporary.exists():
            self.temporary.unlink()


def ledger_paths(manifest_path: Path) -> dict[str, Path]:
    manifest = manifest_path.expanduser().resolve(strict=False)
    return {
        "events": manifest.with_name(manifest.name + ".events.jsonl"),
        "episodes": manifest.with_name(manifest.name + ".episodes.jsonl"),
        "packets": manifest.with_name(manifest.name + ".packets.jsonl"),
        "repeats": manifest.with_name(manifest.name + ".repeats.jsonl"),
    }


def _event_type(surface: str | None) -> str:
    return {
        "user": "user_message",
        "assistant": "assistant_message",
        "tool_input": "tool_call",
        "tool_output": "tool_result",
    }.get(surface or "", "observation")


def _timestamp(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


class LedgerBuilder:
    def __init__(self, manifest_path: Path, *, heartbeat_every: int = HEARTBEAT_EVERY):
        paths = {name: _private_parent(path) for name, path in ledger_paths(manifest_path).items()}
        self.writers: dict[str, AtomicJsonl] = {}
        try:
            for name, path in paths.items():
                self.writers[name] = AtomicJsonl(path)
        except Exception:
            for writer in self.writers.values():
                writer.abort()
            raise
        self.heartbeat_every = heartbeat_every
        self.started = time.monotonic()
        self.event_count = 0
        self.first_event_id = None
        self.last_event_id = None
        self.last_timestamp = None
        self.episode_number = -1
        self.episode: dict[str, Any] | None = None
        self.episode_digest: Any = None
        self.packet_number = 0
        self.packet: dict[str, Any] | None = None
        self.pending_tool: tuple[str, int] | None = None
        self.repeat: dict[str, Any] | None = None
        self.redacted_lines = 0

    def _start_episode(self, event: dict[str, Any]) -> None:
        self.episode_number += 1
        self.episode = {
            "schema_version": EPISODE_SCHEMA,
            "episode_id": f"episode-{self.episode_number:08d}",
            "first_ordinal": event["ordinal"],
            "last_ordinal": event["ordinal"],
            "first_timestamp": event.get("timestamp"),
            "last_timestamp": event.get("timestamp"),
            "event_count": 0,
            "type_counts": {},
        }
        self.episode_digest = hashlib.sha256()

    def _close_episode(self) -> None:
        if not self.episode:
            return
        assert self.episode_digest is not None
        self.episode["event_ids_sha256"] = self.episode_digest.hexdigest()
        self.writers["episodes"].write(self.episode)
        self.episode = None
        self.episode_digest = None

    def _start_packet(self, event: dict[str, Any]) -> None:
        self.packet = {
            "schema_version": PACKET_SCHEMA,
            "packet_id": f"packet-{self.packet_number:08d}",
            "first_ordinal": event["ordinal"],
            "last_ordinal": event["ordinal"],
            "event_count": 0,
            "text_bytes": 0,
            "first_episode_id": event["episode_id"],
            "last_episode_id": event["episode_id"],
            "event_ids": [],
            "type_counts": {},
        }

    def _close_packet(self) -> None:
        if not self.packet:
            return
        event_ids = self.packet.pop("event_ids")
        self.packet["event_ids_sha256"] = digest_ids(event_ids)
        packet_key = {
            key: self.packet[key]
            for key in (
                "first_ordinal", "last_ordinal", "event_count", "text_bytes",
                "first_episode_id", "last_episode_id", "event_ids_sha256", "type_counts",
            )
        }
        self.packet["content_receipt"] = "rpk_" + hashlib.sha256(canonical(packet_key)).hexdigest()
        self.writers["packets"].write(self.packet)
        self.packet_number += 1
        self.packet = None

    def _close_repeat(self) -> None:
        if self.repeat and self.repeat["count"] >= 3:
            repeat = {key: value for key, value in self.repeat.items() if key != "key"}
            self.writers["repeats"].write({
                "schema_version": "recap.repeat-group.v1",
                **repeat,
            })
        self.repeat = None

    def add(self, event: dict[str, Any]) -> dict[str, Any]:
        if event.get("ordinal") != self.event_count:
            raise LedgerError("event ordinals are not contiguous")
        event = dict(event)
        event["schema_version"] = LEDGER_SCHEMA
        event["event_type"] = _event_type(event.get("surface"))
        entities = event.get("entities") if isinstance(event.get("entities"), list) else []
        tool_names = sorted({
            str(entity.get("value")) for entity in entities
            if isinstance(entity, dict) and entity.get("kind") == "tool" and entity.get("value")
        })
        facets = set()
        if any(isinstance(entity, dict) and entity.get("kind") == "file_path" for entity in entities):
            facets.add("file_reference")
        if any(isinstance(entity, dict) and entity.get("kind") == "error" for entity in entities):
            facets.add("error_reference")
        if any(name.casefold() in {"spawn_agent", "followup_task", "send_message"} for name in tool_names):
            facets.add("agent_coordination")
        if event.get("possibly_truncated"):
            facets.add("truncated_source")
        if tool_names:
            event["tool_names"] = tool_names
        if facets:
            event["facets"] = sorted(facets)
        timestamp = _timestamp(event.get("timestamp"))
        starts_episode = self.episode is None or event["event_type"] == "user_message"
        if timestamp is not None and self.last_timestamp is not None:
            starts_episode = starts_episode or timestamp - self.last_timestamp > TIME_GAP_SECONDS
        if starts_episode:
            self._close_episode()
            self._start_episode(event)
        assert self.episode is not None
        event["episode_id"] = self.episode["episode_id"]

        if event["event_type"] == "tool_call":
            self.pending_tool = (event["event_id"], event["ordinal"])
        elif event["event_type"] == "tool_result":
            if self.pending_tool and event["ordinal"] - self.pending_tool[1] <= 3:
                event["paired_call_event_id"] = self.pending_tool[0]
                event["pairing"] = "order_inferred"
            else:
                event["pairing"] = "unpaired"
            self.pending_tool = None
        elif event["event_type"] == "user_message":
            self.pending_tool = None

        self.redacted_lines += int(event.pop("_redactions", 0))
        self.writers["events"].write(event)
        event_id = event["event_id"]
        self.first_event_id = self.first_event_id or event_id
        self.last_event_id = event_id
        self.episode["last_ordinal"] = event["ordinal"]
        self.episode["last_timestamp"] = event.get("timestamp")
        assert self.episode_digest is not None
        if self.episode["event_count"]:
            self.episode_digest.update(b"\n")
        self.episode_digest.update(event_id.encode())
        self.episode["event_count"] += 1
        event_type = event["event_type"]
        self.episode["type_counts"][event_type] = self.episode["type_counts"].get(event_type, 0) + 1

        text_bytes = len(str(event.get("text", "")).encode())
        if self.packet and (
            self.packet["event_count"] >= MAX_PACKET_EVENTS
            or self.packet["text_bytes"] + text_bytes > MAX_PACKET_TEXT_BYTES
        ):
            self._close_packet()
        if self.packet is None:
            self._start_packet(event)
        assert self.packet is not None
        self.packet["last_ordinal"] = event["ordinal"]
        self.packet["last_episode_id"] = event["episode_id"]
        self.packet["event_count"] += 1
        self.packet["text_bytes"] += text_bytes
        self.packet["event_ids"].append(event_id)
        self.packet["type_counts"][event_type] = self.packet["type_counts"].get(event_type, 0) + 1

        repeat_key = (event.get("surface"), event.get("text_sha256"))
        if self.repeat and self.repeat["key"] != repeat_key:
            self._close_repeat()
        if self.repeat is None:
            self.repeat = {
                "key": repeat_key,
                "surface": event.get("surface"),
                "text_sha256": event.get("text_sha256"),
                "first_ordinal": event["ordinal"],
                "last_ordinal": event["ordinal"],
                "count": 0,
            }
        self.repeat["last_ordinal"] = event["ordinal"]
        self.repeat["count"] += 1

        self.event_count += 1
        if timestamp is not None:
            self.last_timestamp = timestamp
        if self.heartbeat_every and self.event_count % self.heartbeat_every == 0:
            elapsed = round(time.monotonic() - self.started, 3)
            print(json.dumps({
                "recap_heartbeat": {"events": self.event_count, "elapsed_seconds": elapsed},
            }, sort_keys=True), file=sys.stderr)
        return event

    def finish(self) -> dict[str, Any]:
        self._close_episode()
        self._close_packet()
        self._close_repeat()
        receipts = {}
        try:
            for name, writer in self.writers.items():
                receipts[name] = writer.commit()
        except Exception:
            for writer in self.writers.values():
                if not writer.closed:
                    writer.abort()
            raise
        return {
            "schema_version": "recap.ledger-bundle.v1",
            "event_count": self.event_count,
            "first_event_id": self.first_event_id,
            "last_event_id": self.last_event_id,
            "redacted_lines": self.redacted_lines,
            "events": {"schema_version": LEDGER_SCHEMA, **receipts["events"]},
            "episodes": {"schema_version": EPISODE_SCHEMA, **receipts["episodes"]},
            "packets": {"schema_version": PACKET_SCHEMA, **receipts["packets"]},
            "repeat_groups": {"schema_version": "recap.repeat-group.v1", **receipts["repeats"]},
        }

    def abort(self) -> None:
        for writer in self.writers.values():
            writer.abort()


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("rb") as source:
        for line_number, line in enumerate(source, 1):
            try:
                value = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise LedgerError(f"invalid JSONL record {line_number}") from exc
            if not isinstance(value, dict):
                raise LedgerError(f"JSONL record {line_number} is not an object")
            yield value


def chunked_events(path: Path, size: int = 5000, overlap: int = 3) -> Iterator[dict[str, Any]]:
    chunk: list[dict[str, Any]] = []
    owned_first = 0
    for event in iter_jsonl(path):
        chunk.append(event)
        if len(chunk) >= size + overlap:
            yield {
                "events": chunk,
                "owned_first": owned_first,
                "owned_last": owned_first + size - 1,
            }
            chunk = chunk[size:]
            owned_first += size
    if chunk:
        yield {
            "events": chunk,
            "owned_first": owned_first,
            "owned_last": int(chunk[-1]["ordinal"]),
        }


def _verify_file(receipt: dict[str, Any]) -> list[str]:
    errors = []
    path = Path(str(receipt.get("path", "")))
    if not path.is_file() or path.is_symlink():
        return ["ledger file is missing or unsafe"]
    if path.parent.stat().st_mode & 0o077:
        errors.append("ledger parent directory is not mode 0700")
    if path.stat().st_mode & 0o077:
        errors.append("ledger file is not mode 0600")
    digest = hashlib.sha256()
    count = 0
    size = 0
    with path.open("rb") as source:
        for line in source:
            digest.update(line)
            count += 1
            size += len(line)
    if digest.hexdigest() != receipt.get("sha256"):
        errors.append("ledger digest mismatch")
    if count != receipt.get("records"):
        errors.append("ledger record count mismatch")
    if size != receipt.get("bytes"):
        errors.append("ledger byte count mismatch")
    return errors


def validate_bundle(
    bundle: Any,
    *,
    source_id: str | None = None,
    native_session_id: str | None = None,
) -> dict[str, Any]:
    errors = []
    if not isinstance(bundle, dict) or bundle.get("schema_version") != "recap.ledger-bundle.v1":
        return {"valid": False, "errors": ["ledger bundle schema is unsupported"]}
    for name in ("events", "episodes", "packets", "repeat_groups"):
        receipt = bundle.get(name)
        if not isinstance(receipt, dict):
            errors.append(f"{name} ledger receipt is missing")
        else:
            errors.extend(f"{name}: {error}" for error in _verify_file(receipt))
    expected = 0
    event_path = Path(bundle.get("events", {}).get("path", ""))
    event_is_private = (
        event_path.is_file()
        and not event_path.is_symlink()
        and not event_path.parent.stat().st_mode & 0o077
        and not event_path.stat().st_mode & 0o077
    )
    if event_is_private:
        descriptor, duplicate_db = tempfile.mkstemp(prefix=".recap-validate-", dir=event_path.parent)
        os.close(descriptor)
        os.chmod(duplicate_db, 0o600)
        connection = sqlite3.connect(duplicate_db)
        connection.execute("CREATE TABLE ids(value TEXT PRIMARY KEY)")
        try:
            for event in iter_jsonl(event_path):
                if event.get("schema_version") != LEDGER_SCHEMA or event.get("ordinal") != expected:
                    errors.append(f"event ledger is discontinuous at ordinal {expected}")
                    break
                event_id = event.get("event_id")
                text = event.get("text")
                text_sha = hashlib.sha256(str(text).encode()).hexdigest() if isinstance(text, str) else None
                if not isinstance(event_id, str) or text_sha != event.get("text_sha256"):
                    errors.append(f"event ledger has invalid evidence at ordinal {expected}")
                    break
                if source_id and native_session_id:
                    expected_id = "rse_" + hashlib.sha256(
                        f"{source_id}\0{native_session_id}\0{event.get('event_native_id')}\0"
                        f"{event.get('item_ordinal')}\0{text_sha}".encode()
                    ).hexdigest()
                    if event_id != expected_id:
                        errors.append(f"event ledger ID mismatch at ordinal {expected}")
                        break
                try:
                    connection.execute("INSERT INTO ids(value) VALUES (?)", (event_id,))
                except sqlite3.IntegrityError:
                    errors.append(f"event ledger has duplicate ID at ordinal {expected}")
                    break
                expected += 1
            connection.commit()
        finally:
            connection.close()
            Path(duplicate_db).unlink(missing_ok=True)
    if expected != bundle.get("event_count"):
        errors.append("event ledger count does not match bundle")
    packet_expected = 0
    packet_path = Path(bundle.get("packets", {}).get("path", ""))
    if packet_path.is_file():
        for packet in iter_jsonl(packet_path):
            if packet.get("first_ordinal") != packet_expected:
                errors.append(f"packet coverage gap/overlap at ordinal {packet_expected}")
                break
            packet_expected = int(packet.get("last_ordinal", -1)) + 1
    if packet_expected != expected:
        errors.append("packet coverage does not account for every event")
    episode_expected = 0
    episode_path = Path(bundle.get("episodes", {}).get("path", ""))
    if episode_path.is_file():
        for episode in iter_jsonl(episode_path):
            if episode.get("first_ordinal") != episode_expected:
                errors.append(f"episode coverage gap/overlap at ordinal {episode_expected}")
                break
            episode_expected = int(episode.get("last_ordinal", -1)) + 1
    if episode_expected != expected:
        errors.append("episode coverage does not account for every event")
    return {
        "valid": not errors,
        "errors": errors,
        "event_count": expected,
        "packet_count": bundle.get("packets", {}).get("records"),
        "episode_count": bundle.get("episodes", {}).get("records"),
    }


def packet_events(bundle: dict[str, Any], packet_id: str) -> Iterable[dict[str, Any]]:
    packet = next((
        item for item in iter_jsonl(Path(bundle["packets"]["path"]))
        if item.get("packet_id") == packet_id
    ), None)
    if packet is None:
        raise LedgerError("packet not found")
    first, last = packet["first_ordinal"], packet["last_ordinal"]
    for event in iter_jsonl(Path(bundle["events"]["path"])):
        ordinal = event["ordinal"]
        if ordinal < first:
            continue
        if ordinal > last:
            break
        yield event
