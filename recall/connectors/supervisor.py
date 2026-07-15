"""Clock-driven, content-free scheduling for explicitly configured connectors."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sqlite3
import stat
import time  # noqa: F401 - retained as a test seam proving tick() never reads wall time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote

from connectors.registry import REGISTRY, ConnectorRegistryError, definition as registry_definition
from connectors.sdk import ConnectorContractError, ConnectorRunError


SUPERVISOR_SCHEMA_VERSION = 1
JOB_KEY = re.compile(r"[0-9a-f]{64}\Z")
FIELDS = {
    "schema_version", "job_key", "connector_id", "generation", "enabled",
    "interval_seconds", "jitter_seconds", "transient_base_seconds",
    "max_backoff_seconds", "lease_seconds", "max_rate_limit_seconds",
}
OUTCOMES = {"never", "success", "rate_limited", "transient", "authority", "contract", "disabled"}
STATES = {"ready", "leased", "parked", "disabled"}
AUTHORITY_ERROR_CODES = {
    "brain_unauthorized", "grep_ai_forbidden", "grep_ai_insufficient_credits",
    "grep_ai_unauthenticated",
}
CONTRACT_ERROR_CODES = {
    "brain_invalid_acknowledgement", "connector_disabled", "connector_invalid_page",
    "connector_spool_error", "grep_ai_invalid_request", "grep_ai_validation_error",
}


class SupervisorContractError(ValueError):
    pass


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise SupervisorContractError(f"invalid_{label}")
    return value


@dataclass(frozen=True)
class ScheduleDefinition:
    schema_version: int
    job_key: str
    connector_id: str
    generation: int
    enabled: bool
    interval_seconds: int
    jitter_seconds: int
    transient_base_seconds: int
    max_backoff_seconds: int
    lease_seconds: int
    max_rate_limit_seconds: int

    def __post_init__(self) -> None:
        if self.schema_version != SUPERVISOR_SCHEMA_VERSION or isinstance(self.schema_version, bool):
            raise SupervisorContractError("invalid_schema_version")
        if not isinstance(self.job_key, str) or not JOB_KEY.fullmatch(self.job_key):
            raise SupervisorContractError("invalid_job_key")
        try:
            connector = registry_definition(self.connector_id)
        except ConnectorRegistryError as error:
            raise SupervisorContractError("invalid_connector_id") from error
        if connector.mode != "pull":
            raise SupervisorContractError("connector_not_schedulable")
        _integer(self.generation, "generation", 1, 2_147_483_647)
        if not isinstance(self.enabled, bool):
            raise SupervisorContractError("invalid_enabled")
        _integer(self.interval_seconds, "interval_seconds", 1, 86_400)
        _integer(self.jitter_seconds, "jitter_seconds", 0, 86_400)
        _integer(self.transient_base_seconds, "transient_base_seconds", 1, 3_600)
        _integer(self.max_backoff_seconds, "max_backoff_seconds", 1, 86_400)
        _integer(self.lease_seconds, "lease_seconds", 1, 3_600)
        _integer(self.max_rate_limit_seconds, "max_rate_limit_seconds", 1, 86_400)
        if self.jitter_seconds > self.interval_seconds:
            raise SupervisorContractError("jitter_exceeds_interval")
        if self.transient_base_seconds > self.max_backoff_seconds:
            raise SupervisorContractError("base_exceeds_backoff")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ScheduleDefinition":
        if not isinstance(value, Mapping):
            raise SupervisorContractError("definition_not_object")
        if set(value) != FIELDS:
            raise SupervisorContractError("definition_fields_invalid")
        return cls(**dict(value))

    def to_public(self) -> dict[str, Any]:
        return {field: getattr(self, field) for field in (
            "schema_version", "job_key", "connector_id", "generation", "enabled",
            "interval_seconds", "jitter_seconds", "transient_base_seconds",
            "max_backoff_seconds", "lease_seconds", "max_rate_limit_seconds",
        )}

    def policy_digest(self) -> str:
        value = self.to_public()
        value.pop("enabled")
        value.pop("generation")
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ScheduledJob:
    definition: ScheduleDefinition
    run: Callable[[], Mapping[str, Any]]

    def __post_init__(self) -> None:
        if not isinstance(self.definition, ScheduleDefinition) or not callable(self.run):
            raise SupervisorContractError("invalid_scheduled_job")


def _safe_parent(path: Path, *, create: bool) -> None:
    parent = path.parent
    if parent.exists():
        info = parent.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise SupervisorContractError("unsafe_state_parent")
        if info.st_mode & 0o077:
            raise SupervisorContractError("unsafe_state_parent_permissions")
    elif create:
        parent.mkdir(parents=True, mode=0o700)
    else:
        raise SupervisorContractError("state_unavailable")
    if create and parent.stat().st_mode & 0o777 != 0o700:
        os.chmod(parent, 0o700)


def _safe_state_file(path: Path, *, must_exist: bool) -> None:
    _safe_parent(path, create=not must_exist)
    if path.is_symlink():
        raise SupervisorContractError("unsafe_state_file")
    if not path.exists():
        if must_exist:
            raise SupervisorContractError("state_unavailable")
        return
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SupervisorContractError("unsafe_state_file")
    if info.st_mode & 0o077:
        raise SupervisorContractError("unsafe_state_permissions")


def _validate_state_schema(path: Path) -> None:
    """Validate an existing file without creating tables or changing bytes."""
    uri = f"file:{quote(str(path.resolve()))}?mode=ro&immutable=1"
    db = None
    try:
        db = sqlite3.connect(uri, uri=True)
        version = db.execute(
            "SELECT value FROM supervisor_meta WHERE key='schema_version'"
        ).fetchone()
        tables = {
            row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        columns = tuple(row[1] for row in db.execute("PRAGMA table_info(supervisor_jobs)"))
    except sqlite3.Error as error:
        raise SupervisorContractError("state_invalid") from error
    finally:
        if db is not None:
            db.close()
    expected_columns = (
        "job_key", "connector_id", "generation", "policy_digest", "enabled", "state",
        "due_at", "lease_until", "lease_token", "failures", "last_outcome", "updated_at",
    )
    if version != (str(SUPERVISOR_SCHEMA_VERSION),) or tables != {
        "supervisor_meta", "supervisor_jobs",
    } or columns != expected_columns:
        raise SupervisorContractError("state_invalid")


class SupervisorStore:
    """Durable leases and timing facts. No source/config/content values are stored."""

    def __init__(self, path: Path):
        self.path = Path(path)
        _safe_state_file(self.path, must_exist=False)
        if self.path.exists():
            _validate_state_schema(self.path)
        self.db = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        os.chmod(self.path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.execute("PRAGMA trusted_schema=OFF")
        self.db.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE IF NOT EXISTS supervisor_meta(
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            ) WITHOUT ROWID;
            INSERT OR IGNORE INTO supervisor_meta(key,value) VALUES ('schema_version','1');
            CREATE TABLE IF NOT EXISTS supervisor_jobs(
              job_key TEXT PRIMARY KEY,
              connector_id TEXT NOT NULL,
              generation INTEGER NOT NULL CHECK(generation BETWEEN 1 AND 2147483647),
              policy_digest TEXT NOT NULL,
              enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
              state TEXT NOT NULL CHECK(state IN ('ready','leased','parked','disabled')),
              due_at REAL,
              lease_until REAL,
              lease_token TEXT,
              failures INTEGER NOT NULL CHECK(failures >= 0),
              last_outcome TEXT NOT NULL CHECK(last_outcome IN
                ('never','success','rate_limited','transient','authority','contract','disabled')),
              updated_at REAL NOT NULL
            ) WITHOUT ROWID;
            COMMIT;
        """)
        version = self.db.execute(
            "SELECT value FROM supervisor_meta WHERE key='schema_version'"
        ).fetchone()
        if version is None or version[0] != str(SUPERVISOR_SCHEMA_VERSION):
            self.db.close()
            raise SupervisorContractError("state_invalid")

    def close(self) -> None:
        self.db.close()

    def _begin(self) -> None:
        self.db.execute("BEGIN IMMEDIATE")

    def reconcile(self, item: ScheduleDefinition, *, now: int | float) -> None:
        now = _finite_time(now)
        digest = item.policy_digest()
        self._begin()
        try:
            row = self.db.execute(
                "SELECT * FROM supervisor_jobs WHERE job_key=?", (item.job_key,)
            ).fetchone()
            changed = bool(row and (
                item.generation != row["generation"]
                or digest != row["policy_digest"]
                or int(item.enabled) != row["enabled"]
            ))
            active = bool(row and row["state"] == "leased" and
                          row["lease_until"] is not None and row["lease_until"] > now)
            if changed and active:
                raise SupervisorContractError("job_active")
            if row is None:
                state = "ready" if item.enabled else "disabled"
                due = now if item.enabled else None
                outcome = "never" if item.enabled else "disabled"
                self.db.execute("""
                    INSERT INTO supervisor_jobs(
                      job_key,connector_id,generation,policy_digest,enabled,state,due_at,
                      lease_until,lease_token,failures,last_outcome,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, (item.job_key, item.connector_id, item.generation, digest,
                      int(item.enabled), state, due, None, None, 0, outcome, now))
            elif item.generation < row["generation"]:
                raise SupervisorContractError("stale_generation")
            elif item.generation == row["generation"]:
                if digest != row["policy_digest"]:
                    raise SupervisorContractError("generation_required")
                if int(item.enabled) != row["enabled"]:
                    state = "ready" if item.enabled else "disabled"
                    due = now if item.enabled else None
                    outcome = "never" if item.enabled else "disabled"
                    self.db.execute("""
                        UPDATE supervisor_jobs SET enabled=?,state=?,due_at=?,lease_until=NULL,
                          lease_token=NULL,failures=0,last_outcome=?,updated_at=? WHERE job_key=?
                    """, (int(item.enabled), state, due, outcome, now, item.job_key))
            else:
                state = "ready" if item.enabled else "disabled"
                due = now if item.enabled else None
                outcome = "never" if item.enabled else "disabled"
                self.db.execute("""
                    UPDATE supervisor_jobs SET connector_id=?,generation=?,policy_digest=?,
                      enabled=?,state=?,due_at=?,lease_until=NULL,lease_token=NULL,failures=0,
                      last_outcome=?,updated_at=? WHERE job_key=?
                """, (item.connector_id, item.generation, digest, int(item.enabled), state,
                      due, outcome, now, item.job_key))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def snapshot(self, job_key: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM supervisor_jobs WHERE job_key=?", (job_key,)
        ).fetchone()
        if row is None:
            raise SupervisorContractError("job_not_found")
        return dict(row)

    def acquire(self, item: ScheduleDefinition, *, now: int | float,
                lease_token: str | None = None) -> str | None:
        now = _finite_time(now)
        token = lease_token or secrets.token_hex(32)
        if not isinstance(token, str) or not JOB_KEY.fullmatch(token):
            raise SupervisorContractError("invalid_lease_token")
        self._begin()
        try:
            row = self.db.execute(
                "SELECT * FROM supervisor_jobs WHERE job_key=?", (item.job_key,)
            ).fetchone()
            if row and (row["generation"] != item.generation or
                        row["policy_digest"] != item.policy_digest()):
                raise SupervisorContractError("schedule_mismatch")
            eligible = bool(row and row["enabled"] and (
                (row["state"] == "ready" and row["due_at"] is not None and row["due_at"] <= now)
                or (row["state"] == "leased" and row["lease_until"] is not None and row["lease_until"] <= now)
            ))
            if not eligible:
                self.db.commit()
                return None
            self.db.execute("""
                UPDATE supervisor_jobs SET state='leased',due_at=NULL,lease_until=?,
                  lease_token=?,updated_at=? WHERE job_key=?
            """, (now + item.lease_seconds, token, now, item.job_key))
            self.db.commit()
            return token
        except Exception:
            self.db.rollback()
            raise

    def complete(self, item: ScheduleDefinition, lease_token: str, *, now: int | float,
                 outcome: str, retry_after: int | None, jitter: int) -> None:
        now = _finite_time(now)
        if outcome not in OUTCOMES - {"never", "disabled"}:
            raise SupervisorContractError("invalid_outcome")
        _integer(jitter, "jitter", 0, item.jitter_seconds)
        self._begin()
        try:
            row = self.db.execute(
                "SELECT * FROM supervisor_jobs WHERE job_key=?", (item.job_key,)
            ).fetchone()
            if (row is None or row["state"] != "leased" or row["lease_token"] != lease_token
                    or row["lease_until"] is None or row["lease_until"] <= now
                    or row["generation"] != item.generation
                    or row["policy_digest"] != item.policy_digest()):
                raise SupervisorContractError("lease_lost")
            failures = int(row["failures"])
            if outcome == "success":
                state, due, failures = "ready", now + item.interval_seconds + jitter, 0
            elif outcome == "rate_limited":
                if not isinstance(retry_after, int) or isinstance(retry_after, bool) or retry_after < 1:
                    raise SupervisorContractError("invalid_retry_after")
                failures += 1
                delay = min(item.max_rate_limit_seconds, retry_after + jitter)
                state, due = "ready", now + delay
            elif outcome == "transient":
                failures += 1
                exponent = min(failures - 1, 30)
                delay = min(item.max_backoff_seconds,
                            item.transient_base_seconds * (2 ** exponent) + jitter)
                state, due = "ready", now + delay
            else:
                failures += 1
                state, due = "parked", None
            self.db.execute("""
                UPDATE supervisor_jobs SET state=?,due_at=?,lease_until=NULL,lease_token=NULL,
                  failures=?,last_outcome=?,updated_at=? WHERE job_key=?
            """, (state, due, failures, outcome, now, item.job_key))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def wake(self, item: ScheduleDefinition, *, now: int | float) -> None:
        now = _finite_time(now)
        self._begin()
        try:
            row = self.db.execute(
                "SELECT enabled,state,lease_until FROM supervisor_jobs WHERE job_key=?", (item.job_key,)
            ).fetchone()
            active = bool(row and row["state"] == "leased" and
                          row["lease_until"] is not None and row["lease_until"] > now)
            if row and row["enabled"] and not active:
                self.db.execute("""
                    UPDATE supervisor_jobs SET state='ready',due_at=?,lease_until=NULL,
                      lease_token=NULL,updated_at=? WHERE job_key=?
                """, (now, now, item.job_key))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def retire_absent(self, job_keys: set[str], *, now: int | float) -> None:
        """Disable configured-set removals without cancelling a live lease."""
        now = _finite_time(now)
        if not isinstance(job_keys, set) or any(
            not isinstance(key, str) or not JOB_KEY.fullmatch(key) for key in job_keys
        ):
            raise SupervisorContractError("invalid_job_keys")
        self._begin()
        try:
            rows = self.db.execute(
                "SELECT job_key,state,lease_until FROM supervisor_jobs WHERE enabled=1"
            ).fetchall()
            for row in rows:
                if row["job_key"] in job_keys:
                    continue
                active = (row["state"] == "leased" and row["lease_until"] is not None
                          and row["lease_until"] > now)
                if active:
                    continue
                self.db.execute("""
                    UPDATE supervisor_jobs SET enabled=0,state='disabled',due_at=NULL,
                      lease_until=NULL,lease_token=NULL,failures=0,last_outcome='disabled',
                      updated_at=? WHERE job_key=?
                """, (now, row["job_key"]))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def next_wait(self, *, now: int | float, maximum: int | float) -> float:
        now = _finite_time(now)
        maximum = _finite_positive(maximum, "max_wait_seconds")
        rows = self.db.execute("""
            SELECT state,due_at,lease_until FROM supervisor_jobs WHERE enabled=1
        """).fetchall()
        moments = [
            row["lease_until"] if row["state"] == "leased" else row["due_at"]
            for row in rows if row["state"] in {"ready", "leased"}
        ]
        moments = [value for value in moments if value is not None]
        if not moments:
            return float(maximum)
        return max(0.0, min(float(maximum), min(moments) - now))


def _finite_time(value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SupervisorContractError("invalid_time")
    result = float(value)
    if result != result or result in {float("inf"), float("-inf")} or result < 0:
        raise SupervisorContractError("invalid_time")
    return result


def _finite_positive(value: Any, label: str) -> float:
    result = _finite_time(value)
    if result <= 0:
        raise SupervisorContractError(f"invalid_{label}")
    return result


def _classify_result(value: Any) -> tuple[str, int | None]:
    if not isinstance(value, Mapping):
        return "contract", None
    status = value.get("status")
    if status == "committed":
        return "success", None
    if status == "backoff" and value.get("error_code") == "connector_rate_limited":
        retry = value.get("retry_after_seconds")
        if isinstance(retry, (int, float)) and not isinstance(retry, bool) and retry >= 1:
            return "rate_limited", max(1, int(retry))
    return "contract", None


def _classify_error(error: Exception) -> str:
    if isinstance(error, PermissionError):
        return "authority"
    if isinstance(error, ConnectorContractError):
        return "contract"
    if isinstance(error, ConnectorRunError):
        if error.error_code in AUTHORITY_ERROR_CODES:
            return "authority"
        if error.error_code in CONTRACT_ERROR_CODES:
            return "contract"
    return "transient"


class ConnectorSupervisor:
    def __init__(self, store: SupervisorStore,
                 *, jitter: Callable[[str, int, int], int] | None = None):
        if not isinstance(store, SupervisorStore):
            raise SupervisorContractError("invalid_store")
        self.store = store
        self.jitter = jitter or self._secure_jitter

    @staticmethod
    def _secure_jitter(_job_key: str, _failures: int, maximum: int) -> int:
        return secrets.randbelow(maximum + 1) if maximum else 0

    @staticmethod
    def _jobs(jobs: tuple[ScheduledJob, ...] | list[ScheduledJob]) -> tuple[ScheduledJob, ...]:
        values = tuple(jobs)
        if not all(isinstance(job, ScheduledJob) for job in values):
            raise SupervisorContractError("invalid_jobs")
        keys = [job.definition.job_key for job in values]
        if len(keys) != len(set(keys)):
            raise SupervisorContractError("duplicate_job_key")
        return values

    def _jitter(self, item: ScheduleDefinition, failures: int) -> int:
        value = self.jitter(item.job_key, failures, item.jitter_seconds)
        return _integer(value, "jitter", 0, item.jitter_seconds)

    def tick(self, jobs: tuple[ScheduledJob, ...] | list[ScheduledJob], *, now: int | float,
             clock: Callable[[], int | float] | None = None) -> dict[str, Any]:
        now = _finite_time(now)
        values = self._jobs(jobs)
        runnable: list[ScheduledJob] = []
        deferred_active = 0
        for job in values:
            try:
                self.store.reconcile(job.definition, now=now)
            except SupervisorContractError as error:
                if str(error) != "job_active":
                    raise
                deferred_active += 1
                continue
            runnable.append(job)
        self.store.retire_absent({job.definition.job_key for job in values}, now=now)
        outcomes: dict[str, int] = {}
        ran = 0
        lost_leases = 0
        for job in runnable:
            item = job.definition
            acquired_at = _finite_time(clock()) if clock is not None else now
            token = self.store.acquire(item, now=acquired_at)
            if token is None:
                continue
            ran += 1
            try:
                outcome, retry_after = _classify_result(job.run())
            except Exception as error:
                outcome, retry_after = _classify_error(error), None
            state = self.store.snapshot(item.job_key)
            jitter = self._jitter(item, int(state["failures"]) + int(outcome != "success"))
            completed_at = _finite_time(clock()) if clock is not None else now
            try:
                self.store.complete(item, token, now=completed_at, outcome=outcome,
                                    retry_after=retry_after, jitter=jitter)
            except SupervisorContractError as error:
                if str(error) != "lease_lost":
                    raise
                lost_leases += 1
                continue
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
        result = {"schema_version": 1, "configured": len(values), "ran": ran,
                  "outcomes": dict(sorted(outcomes.items()))}
        if lost_leases:
            result["lost_leases"] = lost_leases
        if deferred_active:
            result["deferred_active"] = deferred_active
        return result

    def wake(self, jobs: tuple[ScheduledJob, ...] | list[ScheduledJob], *, now: int | float) -> None:
        now = _finite_time(now)
        for job in self._jobs(jobs):
            self.store.reconcile(job.definition, now=now)
            self.store.wake(job.definition, now=now)

    def run_loop(self, jobs: tuple[ScheduledJob, ...] | list[ScheduledJob], *,
                 clock: Callable[[], int | float], wake_event: Any, stop_event: Any,
                 max_wait_seconds: int | float = 30, max_cycles: int | None = None) -> int:
        values = self._jobs(jobs)
        maximum = _finite_positive(max_wait_seconds, "max_wait_seconds")
        if max_cycles is not None:
            _integer(max_cycles, "max_cycles", 1, 2_147_483_647)
        cycles = 0
        while not stop_event.is_set() and (max_cycles is None or cycles < max_cycles):
            now = _finite_time(clock())
            self.tick(values, now=now, clock=clock)
            after = _finite_time(clock())
            timeout = self.store.next_wait(now=after, maximum=maximum)
            awakened = bool(wake_event.wait(timeout))
            cycles += 1
            if awakened:
                wake_event.clear()
                if stop_event.is_set():
                    break
                self.wake(values, now=_finite_time(clock()))
        return cycles


def preview_supervisor_policy() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "connector-supervisor-preview",
        "registered_pull_connectors": sum(item.mode == "pull" for item in REGISTRY),
        "clock": "injected",
        "retry_outcomes": ["authority", "contract", "rate_limited", "success", "transient"],
        "limits_seconds": {
            "interval": [1, 86_400], "jitter": [0, 86_400],
            "lease": [1, 3_600], "max_backoff": [1, 86_400],
            "max_rate_limit": [1, 86_400], "transient_base": [1, 3_600],
        },
        "credential_reads": 0, "source_reads": 0, "network_requests": 0, "writes": 0,
    }


def aggregate_supervisor_status(path: Path, *, now: int | float) -> dict[str, Any]:
    now = _finite_time(now)
    path = Path(path)
    _safe_state_file(path, must_exist=True)
    uri = f"file:{quote(str(path.resolve()))}?mode=ro&immutable=1"
    db = None
    try:
        db = sqlite3.connect(uri, uri=True)
        db.row_factory = sqlite3.Row
        schema = db.execute(
            "SELECT value FROM supervisor_meta WHERE key='schema_version'"
        ).fetchone()
        rows = db.execute("""
            SELECT enabled,state,due_at,lease_until,lease_token,last_outcome FROM supervisor_jobs
        """).fetchall()
    except sqlite3.Error as error:
        raise SupervisorContractError("state_invalid") from error
    finally:
        if db is not None:
            db.close()
    if schema is None or schema["value"] != "1":
        raise SupervisorContractError("state_invalid")
    if any(not _valid_status_row(row) for row in rows):
        raise SupervisorContractError("state_invalid")
    enabled = sum(int(row["enabled"]) for row in rows)
    due = sum(int(row["enabled"] and row["state"] == "ready" and
                  row["due_at"] is not None and row["due_at"] <= now) for row in rows)
    outcomes = {name: 0 for name in sorted(OUTCOMES)}
    for row in rows:
        outcomes[row["last_outcome"]] += 1
    return {
        "schema_version": 1,
        "jobs": len(rows),
        "enabled": enabled,
        "disabled": len(rows) - enabled,
        "ready": sum(row["state"] == "ready" for row in rows),
        "due": due,
        "waiting": sum(row["state"] == "ready" for row in rows) - due,
        "leased": sum(row["state"] == "leased" for row in rows),
        "parked": sum(row["state"] == "parked" for row in rows),
        "outcomes": outcomes,
    }


def _valid_status_row(row: sqlite3.Row) -> bool:
    if row["enabled"] not in {0, 1} or row["state"] not in STATES or row["last_outcome"] not in OUTCOMES:
        return False
    due = row["due_at"]
    lease = row["lease_until"]
    if due is not None:
        try:
            _finite_time(due)
        except SupervisorContractError:
            return False
    if lease is not None:
        try:
            _finite_time(lease)
        except SupervisorContractError:
            return False
    token = row["lease_token"]
    if token is not None and (not isinstance(token, str) or not JOB_KEY.fullmatch(token)):
        return False
    if row["state"] == "ready":
        return row["enabled"] == 1 and due is not None and lease is None and token is None
    if row["state"] == "leased":
        return row["enabled"] == 1 and due is None and lease is not None and token is not None
    if row["state"] == "parked":
        return row["enabled"] == 1 and due is None and lease is None and token is None
    return row["enabled"] == 0 and due is None and lease is None and token is None
