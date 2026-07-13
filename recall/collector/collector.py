from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COLLECTOR_VERSION = 1
MAX_BATCH_BYTES = 8_000_000
SENSITIVE_KEY = re.compile(r"(?:litellm.*master.*key|api[_-]?key|password|secret|authorization|bearer|token)", re.I)
SENSITIVE_LINE = re.compile(r"(?i)(LITELLM_MASTER_KEY|api[_-]?key|password|secret|authorization|bearer|token)\s*[=:]\s*\S+")


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "[REDACTED]" if SENSITIVE_KEY.search(str(key)) else sanitize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        without_nul = value.replace("\x00", "[NUL]")
        return "\n".join("[REDACTED]" if SENSITIVE_LINE.search(line) else line for line in without_nul.splitlines())
    return value


def fingerprint(path: Path, size: int | None = None) -> str:
    size = path.stat().st_size if size is None else size
    with path.open("rb") as source:
        first = source.read(min(4096, size))
        source.seek(max(0, size - 4096))
        last = source.read(min(4096, size))
    return hashlib.sha256(first + last + str(size).encode()).hexdigest()


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalized_timestamp(value: Any, fallback_epoch: float) -> str:
    try:
        if isinstance(value, (int, float)):
            parsed = datetime.fromtimestamp(float(value), timezone.utc)
        elif isinstance(value, str) and value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            raise ValueError
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, TypeError, OverflowError):
        return datetime.fromtimestamp(fallback_epoch, timezone.utc).isoformat().replace("+00:00", "Z")


class Collector:
    def __init__(self, *, root: Path, harness: str, source_id: str, spool_path: Path,
                 endpoint: str, token: str, principal_id: str = "owner",
                 visibility: str = "private", batch_size: int = 500):
        if harness not in {"claude", "codex"}:
            raise ValueError("harness must be claude or codex")
        if visibility not in {"private", "shared"}:
            raise ValueError("visibility must be private or shared")
        self.root = Path(root).expanduser().resolve()
        self.harness = harness
        self.source_id = source_id
        self.spool_path = Path(spool_path).expanduser()
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.principal_id = principal_id
        self.visibility = visibility
        self.batch_size = batch_size
        self.shard_count = 1
        self.shard_index = 0
        self.spool_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.spool_path.parent, 0o700)
        self.db = sqlite3.connect(self.spool_path)
        self.db.row_factory = sqlite3.Row
        os.chmod(self.spool_path, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=30000")
        self.db.execute("PRAGMA synchronous=FULL")
        self._migrate()

    def _migrate(self) -> None:
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS files(
          path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
          fingerprint TEXT NOT NULL, scanned_offset INTEGER NOT NULL DEFAULT 0,
          committed_offset INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL,
          last_scan_id TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS active_records(
          path TEXT NOT NULL, native_id TEXT NOT NULL, content_sha256 TEXT NOT NULL,
          start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL, receipt TEXT,
          PRIMARY KEY(path,native_id));
        CREATE TABLE IF NOT EXISTS record_generations(
          native_id TEXT PRIMARY KEY, generation INTEGER NOT NULL,
          base_content_sha256 TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS scan_members(
          path TEXT NOT NULL, native_id TEXT NOT NULL,
          PRIMARY KEY(path,native_id));
        CREATE TABLE IF NOT EXISTS outbox(
          id INTEGER PRIMARY KEY, path TEXT NOT NULL, native_id TEXT NOT NULL,
          content_sha256 TEXT NOT NULL, start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL,
          shard_key INTEGER NOT NULL DEFAULT 0,
          envelope_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
          queued_at REAL NOT NULL, acked_at REAL, receipt TEXT,
          UNIQUE(native_id,content_sha256));
        CREATE INDEX IF NOT EXISTS outbox_state_id ON outbox(state,id);
        CREATE TABLE IF NOT EXISTS dead_letters(
          id INTEGER PRIMARY KEY, path TEXT NOT NULL, byte_offset INTEGER NOT NULL,
          error_code TEXT NOT NULL, error_summary TEXT NOT NULL, created_at REAL NOT NULL,
          UNIQUE(path,byte_offset,error_code));
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        """)
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(outbox)")}
        if "start_offset" not in columns:
            self.db.execute("ALTER TABLE outbox ADD COLUMN start_offset INTEGER NOT NULL DEFAULT 0")
        if "shard_key" not in columns:
            self.db.execute("ALTER TABLE outbox ADD COLUMN shard_key INTEGER NOT NULL DEFAULT 0")
            self.db.execute("CREATE INDEX IF NOT EXISTS outbox_path_idx ON outbox(path)")
            for row in self.db.execute("SELECT DISTINCT path FROM outbox"):
                self.db.execute("UPDATE outbox SET shard_key=? WHERE path=?", (self._path_shard(row["path"]), row["path"]))
        self.db.execute("CREATE INDEX IF NOT EXISTS outbox_shard_state_id ON outbox(shard_key,state,id)")
        self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('collector_version',?)", (str(COLLECTOR_VERSION),))
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def discover(self) -> list[Path]:
        if not self.root.exists():
            return []
        pattern = "rollout-*.jsonl" if self.harness == "codex" else "*.jsonl"
        return sorted(path for path in self.root.rglob(pattern) if path.is_file())

    def _file_key(self, path: Path) -> str:
        relative = str(path.relative_to(self.root))
        return hashlib.sha256((self.harness + "\x1f" + relative).encode()).hexdigest()[:24]

    @staticmethod
    def _path_shard(path: str) -> int:
        return int.from_bytes(hashlib.sha256(path.encode()).digest()[:8], "big") & ((1 << 63) - 1)

    def _envelope(self, path: Path, native_id: str, kind: str, content: Any,
                  occurred_at: str, start: int, end: int) -> dict:
        clean = sanitize(content)
        return {
            "schema_version": 1,
            "source_id": self.source_id,
            "native_id": native_id,
            "native_parent_id": f"{self.harness}-session-{self._file_key(path)}",
            "kind": kind,
            "occurred_at": occurred_at,
            "observed_at": iso_now(),
            "principal_id": self.principal_id,
            "visibility": self.visibility,
            "content_type": "application/json",
            "content": clean,
            "content_sha256": hashlib.sha256(canonical_json(clean)).hexdigest(),
            "provenance": {
                "harness": self.harness,
                "collector_version": COLLECTOR_VERSION,
                "original_path": str(path),
                "byte_start": start,
                "byte_end": end,
            },
        }

    def _versioned_record_content(self, native_id: str, content: dict, *, was_active: bool) -> dict:
        clean = sanitize(content)
        base_sha = hashlib.sha256(canonical_json(clean)).hexdigest()
        row = self.db.execute("SELECT generation,base_content_sha256 FROM record_generations WHERE native_id=?", (native_id,)).fetchone()
        generation = 0 if row is None else int(row["generation"]) + int(not was_active or row["base_content_sha256"] != base_sha)
        self.db.execute(
            "INSERT INTO record_generations(native_id,generation,base_content_sha256) VALUES (?,?,?) "
            "ON CONFLICT(native_id) DO UPDATE SET generation=excluded.generation,base_content_sha256=excluded.base_content_sha256",
            (native_id, generation, base_sha),
        )
        return {**clean, "_recall_collector_generation": generation}

    def _queue(self, path: Path, envelope: dict, end_offset: int) -> bool:
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO outbox(path,native_id,content_sha256,start_offset,end_offset,shard_key,envelope_json,queued_at) VALUES (?,?,?,?,?,?,?,?)",
            (str(path), envelope["native_id"], envelope["content_sha256"], envelope["provenance"]["byte_start"], end_offset, self._path_shard(str(path)),
             canonical_json(envelope).decode(), time.time()),
        )
        return cursor.rowcount == 1

    def _save_file_progress(self, path: str, stat, current_fingerprint: str, offset: int,
                            status: str, scan_id: str) -> None:
        self.db.execute(
            """INSERT INTO files(path,size,mtime_ns,fingerprint,scanned_offset,committed_offset,status,last_scan_id)
               VALUES (?,?,?,?,?,0,?,?)
               ON CONFLICT(path) DO UPDATE SET size=excluded.size,mtime_ns=excluded.mtime_ns,
               fingerprint=excluded.fingerprint,scanned_offset=excluded.scanned_offset,
               status=excluded.status,last_scan_id=excluded.last_scan_id""",
            (path, stat.st_size, stat.st_mtime_ns, current_fingerprint, offset, status, scan_id),
        )

    def scan(self) -> dict:
        scan_id = hashlib.sha256(f"{time.time_ns()}:{os.getpid()}".encode()).hexdigest()[:16]
        summary = {"files_seen": 0, "records_queued": 0, "tombstones_queued": 0,
                   "parse_errors": 0, "partial_files": 0}
        for path in self.discover():
            summary["files_seen"] += 1
            stat = path.stat()
            path_text = str(path)
            row = self.db.execute("SELECT * FROM files WHERE path=?", (path_text,)).fetchone()
            current_fingerprint = fingerprint(path, stat.st_size)
            if row and not row["status"].startswith("scanning-") and row["size"] == stat.st_size and row["mtime_ns"] == stat.st_mtime_ns and row["fingerprint"] == current_fingerprint:
                self.db.execute("UPDATE files SET last_scan_id=? WHERE path=?", (scan_id, path_text))
                continue
            resume_mode = row["status"].removeprefix("scanning-") if row and row["status"].startswith("scanning-") else None
            if resume_mode:
                mode = resume_mode
                file_scan_id = row["last_scan_id"]
            else:
                mode = "append" if row and stat.st_size > row["size"] and fingerprint(path, row["size"]) == row["fingerprint"] else ("full" if row else "new")
                file_scan_id = scan_id
                if mode != "append":
                    self.db.execute("DELETE FROM scan_members WHERE path=?", (path_text,))
            append = mode == "append"
            start_offset = int(row["scanned_offset"]) if append or resume_mode else 0
            old_active = {item["native_id"]: item for item in self.db.execute("SELECT * FROM active_records WHERE path=?", (path_text,))}
            seen_native: set[str] = set(old_active) if append else {item["native_id"] for item in self.db.execute("SELECT native_id FROM scan_members WHERE path=?", (path_text,))}
            complete_end = start_offset
            complete_records = 0
            with path.open("rb") as source:
                source.seek(start_offset)
                while True:
                    line_start = source.tell()
                    line = source.readline()
                    if not line:
                        break
                    if not line.endswith(b"\n"):
                        summary["partial_files"] += 1
                        break
                    complete_end = source.tell()
                    complete_records += 1
                    native_id = f"{self._file_key(path)}-{line_start:016x}"
                    try:
                        content = json.loads(line)
                        if not isinstance(content, dict):
                            raise ValueError("record is not an object")
                    except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
                        summary["parse_errors"] += 1
                        self.db.execute(
                            "INSERT OR IGNORE INTO dead_letters(path,byte_offset,error_code,error_summary,created_at) VALUES (?,?,?,?,?)",
                            (path_text, line_start, type(exc).__name__, str(exc)[:300], time.time()),
                        )
                        continue
                    occurred_at = normalized_timestamp(content.get("timestamp"), stat.st_mtime)
                    versioned_content = self._versioned_record_content(native_id, content, was_active=native_id in old_active)
                    envelope = self._envelope(path, native_id, "transcript_record", versioned_content, occurred_at, line_start, complete_end)
                    if self._queue(path, envelope, complete_end):
                        summary["records_queued"] += 1
                    self.db.execute(
                        "INSERT INTO active_records(path,native_id,content_sha256,start_offset,end_offset) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(path,native_id) DO UPDATE SET content_sha256=excluded.content_sha256,start_offset=excluded.start_offset,end_offset=excluded.end_offset",
                        (path_text, native_id, envelope["content_sha256"], line_start, complete_end),
                    )
                    seen_native.add(native_id)
                    if not append:
                        self.db.execute("INSERT OR IGNORE INTO scan_members(path,native_id) VALUES (?,?)", (path_text, native_id))
                    if complete_records % 1000 == 0:
                        self._save_file_progress(path_text, stat, current_fingerprint, complete_end, "scanning-" + mode, file_scan_id)
                        self.db.commit()
            if not append:
                for native_id, old in old_active.items():
                    if native_id in seen_native:
                        continue
                    content = {"target_native_id": native_id, "deletion_id": scan_id}
                    envelope = self._envelope(path, native_id, "tombstone", content, iso_now(), old["start_offset"], old["end_offset"])
                    if self._queue(path, envelope, complete_end):
                        summary["tombstones_queued"] += 1
                    self.db.execute("DELETE FROM active_records WHERE path=? AND native_id=?", (path_text, native_id))
            status = "partial" if complete_end < stat.st_size else "ok"
            self._save_file_progress(path_text, stat, current_fingerprint, complete_end, status, scan_id)
            self.db.execute("DELETE FROM scan_members WHERE path=?", (path_text,))
            # Bound crash recovery to one source file; acknowledged offsets still move only in flush().
            self.db.commit()
        missing = list(self.db.execute("SELECT path FROM files WHERE last_scan_id != ? AND status != 'tombstone'", (scan_id,)))
        for item in missing:
            for old in self.db.execute("SELECT * FROM active_records WHERE path=?", (item["path"],)):
                path = Path(item["path"])
                envelope = self._envelope(path, old["native_id"], "tombstone", {"target_native_id": old["native_id"], "deletion_id": scan_id}, iso_now(), old["start_offset"], old["end_offset"])
                if self._queue(path, envelope, old["end_offset"]):
                    summary["tombstones_queued"] += 1
            self.db.execute("DELETE FROM active_records WHERE path=?", (item["path"],))
            self.db.execute("UPDATE files SET status='tombstone',last_scan_id=? WHERE path=?", (scan_id, item["path"]))
        self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('last_scan_at',?)", (str(time.time()),))
        self.db.commit()
        return summary

    def pending_envelopes(self) -> list[dict]:
        return [json.loads(row["envelope_json"]) for row in self.db.execute("SELECT envelope_json FROM outbox WHERE state='pending' ORDER BY id")]

    def recover_dead_payloads(self) -> dict:
        result = {"recovered": 0, "unrecoverable": 0}
        rows = list(self.db.execute("SELECT * FROM outbox WHERE state='dead' ORDER BY id"))
        for row in rows:
            try:
                path = Path(row["path"])
                with path.open("rb") as source:
                    source.seek(row["start_offset"])
                    raw = source.read(row["end_offset"] - row["start_offset"])
                if not raw.endswith(b"\n"):
                    raise ValueError("source byte window is no longer a complete record")
                content = json.loads(raw)
                if not isinstance(content, dict):
                    raise ValueError("record is not an object")
                versioned = self._versioned_record_content(row["native_id"], content, was_active=True)
                envelope = self._envelope(
                    path, row["native_id"], "transcript_record", versioned,
                    normalized_timestamp(content.get("timestamp"), path.stat().st_mtime),
                    row["start_offset"], row["end_offset"],
                )
                self.db.execute(
                    "UPDATE outbox SET state='pending',content_sha256=?,envelope_json=?,queued_at=?,acked_at=NULL,receipt=NULL WHERE id=?",
                    (envelope["content_sha256"], canonical_json(envelope).decode(), time.time(), row["id"]),
                )
                self.db.execute(
                    "DELETE FROM dead_letters WHERE path=? AND error_code='PayloadTooLarge'",
                    (row["path"],),
                )
                result["recovered"] += 1
            except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self.db.execute(
                    "INSERT OR IGNORE INTO dead_letters(path,byte_offset,error_code,error_summary,created_at) VALUES (?,?,?,?,?)",
                    (row["path"], row["start_offset"], "RecoveryError", str(exc)[:300], time.time()),
                )
                result["unrecoverable"] += 1
        self.db.commit()
        return result

    def _repair_pending_envelope(self, row: sqlite3.Row) -> dict | None:
        envelope = json.loads(row["envelope_json"])
        clean = sanitize(envelope["content"])
        if clean == envelope["content"]:
            return dict(row)
        old_sha = envelope["content_sha256"]
        envelope["content"] = clean
        envelope["content_sha256"] = hashlib.sha256(canonical_json(clean)).hexdigest()
        rendered = canonical_json(envelope).decode()
        duplicate = self.db.execute(
            "SELECT id,state,receipt FROM outbox WHERE native_id=? AND content_sha256=? AND id<>?",
            (row["native_id"], envelope["content_sha256"], row["id"]),
        ).fetchone()
        if duplicate is not None and duplicate["state"] in {"pending", "acked"}:
            receipt = duplicate["receipt"] if duplicate["state"] == "acked" else None
            self.db.execute("DELETE FROM outbox WHERE id=?", (row["id"],))
            self.db.execute(
                "UPDATE active_records SET content_sha256=?,receipt=? WHERE path=? AND native_id=?",
                (envelope["content_sha256"], receipt, row["path"], row["native_id"]),
            )
            if duplicate["state"] == "acked":
                pending = self.db.execute(
                    "SELECT 1 FROM outbox WHERE path=? AND state='pending' LIMIT 1", (row["path"],)
                ).fetchone()
                if not pending:
                    self.db.execute("UPDATE files SET committed_offset=scanned_offset WHERE path=?", (row["path"],))
            return None
        self.db.execute(
            "UPDATE outbox SET content_sha256=?,envelope_json=? WHERE id=?",
            (envelope["content_sha256"], rendered, row["id"]),
        )
        self.db.execute(
            "UPDATE active_records SET content_sha256=? WHERE path=? AND native_id=? AND content_sha256=?",
            (envelope["content_sha256"], row["path"], row["native_id"], old_sha),
        )
        repaired = dict(row)
        repaired["content_sha256"] = envelope["content_sha256"]
        repaired["envelope_json"] = rendered
        return repaired

    def flush(self) -> dict:
        recovery = self.recover_dead_payloads() if self.shard_index == 0 else {"recovered": 0, "unrecoverable": 0}
        result = {"batches": 0, "acked": 0, "replayed_batches": 0, "errors": 0, **recovery}
        while True:
            raw_candidates = list(self.db.execute(
                "SELECT * FROM outbox WHERE state='pending' AND (shard_key % ?) = ? ORDER BY id LIMIT ?",
                (self.shard_count, self.shard_index, self.batch_size),
            ))
            if not raw_candidates:
                break
            candidates = []
            for row in raw_candidates:
                candidate = self._repair_pending_envelope(row)
                if candidate is not None:
                    candidates.append(candidate)
            self.db.commit()
            rows: list[dict] = []
            body_size = len(b'{"events":[]}')
            for candidate in candidates:
                event_size = len(candidate["envelope_json"].encode()) + 1
                if not rows and event_size > MAX_BATCH_BYTES:
                    with self.db:
                        self.db.execute("UPDATE outbox SET state='dead',envelope_json='{}' WHERE id=?", (candidate["id"],))
                        self.db.execute(
                            "INSERT OR IGNORE INTO dead_letters(path,byte_offset,error_code,error_summary,created_at) VALUES (?,?,?,?,?)",
                            (candidate["path"], candidate["start_offset"], "PayloadTooLarge", f"sanitized envelope exceeds {MAX_BATCH_BYTES} bytes", time.time()),
                        )
                    result["errors"] += 1
                    continue
                if rows and body_size + event_size > MAX_BATCH_BYTES:
                    break
                rows.append(candidate)
                body_size += event_size
            if not rows:
                continue
            events = [json.loads(row["envelope_json"]) for row in rows]
            key_material = self.source_id + ":" + ",".join(f"{row['id']}:{row['content_sha256']}" for row in rows)
            key = "collector-v1-" + hashlib.sha256(key_material.encode()).hexdigest()
            body = canonical_json({"events": events})
            request = urllib.request.Request(
                self.endpoint + "/v1/ingest/batches", data=body, method="POST",
                headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json", "Idempotency-Key": key},
            )
            acknowledgement = None
            for attempt in range(5):
                try:
                    with urllib.request.urlopen(request, timeout=60, context=ssl.create_default_context()) as response:
                        acknowledgement = json.loads(response.read())
                        if response.status not in {200, 201} or acknowledgement.get("status") != "committed":
                            raise RuntimeError("server did not return a commit acknowledgement")
                    break
                except urllib.error.HTTPError as exc:
                    result["errors"] += 1
                    if exc.code < 500:
                        return result
                except (OSError, urllib.error.URLError):
                    result["errors"] += 1
                except (json.JSONDecodeError, RuntimeError):
                    result["errors"] += 1
                    return result
                if attempt < 4:
                    time.sleep(min(2 ** attempt, 10))
            if acknowledgement is None:
                return result
            receipts = acknowledgement.get("receipts", [])
            if len(receipts) != len(rows):
                result["errors"] += 1
                break
            acked_at = time.time()
            with self.db:
                acknowledgements = list(zip(rows, receipts, strict=True))
                self.db.executemany(
                    "UPDATE outbox SET state='acked',acked_at=?,receipt=?,envelope_json='{}' WHERE id=?",
                    [(acked_at, receipt, row["id"]) for row, receipt in acknowledgements],
                )
                self.db.executemany(
                    "UPDATE active_records SET receipt=? WHERE path=? AND native_id=?",
                    [(receipt, row["path"], row["native_id"]) for row, receipt in acknowledgements],
                )
                for path in {row["path"] for row in rows}:
                    pending = self.db.execute("SELECT 1 FROM outbox WHERE path=? AND state='pending' LIMIT 1", (path,)).fetchone()
                    if not pending:
                        self.db.execute("UPDATE files SET committed_offset=scanned_offset WHERE path=?", (path,))
            result["batches"] += 1
            result["acked"] += len(rows)
            result["replayed_batches"] += int(bool(acknowledgement.get("replay")))
        return result

    def doctor(self, *, include_dead_letters: bool = True) -> dict:
        disk = {str(path) for path in self.discover()}
        ledger = {row["path"] for row in self.db.execute("SELECT path FROM files WHERE status != 'tombstone'")}
        total_lines = self.db.execute("SELECT count(*) AS n FROM active_records").fetchone()["n"]
        parse_errors = self.db.execute("SELECT count(*) AS n FROM dead_letters").fetchone()["n"]
        latencies = [row["acked_at"] - row["queued_at"] for row in self.db.execute("SELECT queued_at,acked_at FROM outbox WHERE acked_at IS NOT NULL ORDER BY acked_at-queued_at")]
        p95 = latencies[max(0, int(len(latencies) * 0.95 + 0.999999) - 1)] if latencies else None
        result = {
            "harness": self.harness,
            "source_id": self.source_id,
            "disk_files": len(disk),
            "ledger_files": len(ledger),
            "coverage_percent": 100.0 if not disk else 100.0 * len(disk & ledger) / len(disk),
            "records": total_lines,
            "parse_errors": parse_errors,
            "parse_error_percent": 100.0 * parse_errors / max(1, total_lines + parse_errors),
            "pending": self.db.execute("SELECT count(*) AS n FROM outbox WHERE state='pending'").fetchone()["n"],
            "acked": self.db.execute("SELECT count(*) AS n FROM outbox WHERE state='acked'").fetchone()["n"],
            "dead": self.db.execute("SELECT count(*) AS n FROM outbox WHERE state='dead'").fetchone()["n"],
            "committed_files": self.db.execute("SELECT count(*) AS n FROM files WHERE status != 'tombstone' AND committed_offset=scanned_offset").fetchone()["n"],
            "ack_latency_p95_seconds": p95,
            "dead_letter_count": parse_errors,
        }
        if include_dead_letters:
            result["dead_letters"] = [dict(row) for row in self.db.execute("SELECT path,byte_offset,error_code,error_summary FROM dead_letters ORDER BY id")]
        return result

    def locate_receipt(self, receipt: str) -> dict | None:
        row = self.db.execute(
            "SELECT path,start_offset,end_offset,native_id FROM outbox WHERE receipt=? AND state='acked'", (receipt,)
        ).fetchone()
        return dict(row) if row else None
