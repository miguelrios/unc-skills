"""Closed private configuration and object wiring for the connector supervisor."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from client.mac import BrainClient, load_file_token, load_keychain_token
from connectors.export_inbox import ExportInboxConnector
from connectors.grep_ai import GrepAIConnector, load_private_api_key, validate_api_key
from connectors.registry import ConnectorRegistryError, validate_policy
from connectors.sdk import SOURCE_ID, ConnectorRunner
from connectors.supervisor import (
    ConnectorSupervisor,
    ScheduleDefinition,
    ScheduledJob,
    SupervisorStore,
)
from privacy.policy import PrivacyPolicy


HOST_SCHEMA_VERSION = 1
MAX_CONFIG_BYTES = 1_000_000
REFERENCE_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{1,255}\Z")
CONFIG_FIELDS = {"schema_version", "jobs"}
JOB_FIELDS = {
    "schedule", "source_id", "endpoint", "brain_authority", "privacy_mode", "connector",
}


class ConnectorHostError(ValueError):
    pass


def _absolute_path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise ConnectorHostError(f"invalid_{label}")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or "~" in path.parts:
        raise ConnectorHostError(f"invalid_{label}")
    return path


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ConnectorHostError(f"invalid_{label}")
    return value


@dataclass(frozen=True)
class AuthorityReference:
    kind: str
    path: Path | None = None
    service: str | None = None
    account: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AuthorityReference":
        if not isinstance(value, Mapping):
            raise ConnectorHostError("invalid_authority_reference")
        kind = value.get("kind")
        if kind == "file" and set(value) == {"kind", "path"}:
            return cls(kind="file", path=_absolute_path(value["path"], "authority_path"))
        if kind == "keychain" and set(value) == {"kind", "service", "account"}:
            service, account = value["service"], value["account"]
            if not isinstance(service, str) or not REFERENCE_VALUE.fullmatch(service):
                raise ConnectorHostError("invalid_keychain_service")
            if not isinstance(account, str) or not REFERENCE_VALUE.fullmatch(account):
                raise ConnectorHostError("invalid_keychain_account")
            return cls(kind="keychain", service=service, account=account)
        raise ConnectorHostError("invalid_authority_reference")

    def to_mapping(self) -> dict[str, Any]:
        if self.kind == "file":
            return {"kind": "file", "path": str(self.path)}
        return {"kind": "keychain", "service": self.service, "account": self.account}

    def fingerprint(self) -> str:
        encoded = json.dumps(self.to_mapping(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def load_brain(self) -> str:
        if self.kind == "file":
            return load_file_token(self.path)
        return load_keychain_token(self.service, self.account)

    def load_source(self) -> str:
        if self.kind == "file":
            return load_private_api_key(self.path)
        return validate_api_key(load_keychain_token(self.service, self.account))


@dataclass(frozen=True)
class ExportOptions:
    inbox: Path
    catalog: Path
    spool: Path
    page_size: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ExportOptions":
        if not isinstance(value, Mapping) or set(value) != {"inbox", "catalog", "spool", "page_size"}:
            raise ConnectorHostError("invalid_export_options")
        result = cls(
            inbox=_absolute_path(value["inbox"], "inbox"),
            catalog=_absolute_path(value["catalog"], "catalog"),
            spool=_absolute_path(value["spool"], "spool"),
            page_size=_integer(value["page_size"], "page_size", 1, 500),
        )
        if len({result.inbox, result.catalog, result.spool}) != 3:
            raise ConnectorHostError("duplicate_job_paths")
        return result

    def to_mapping(self) -> dict[str, Any]:
        return {
            "inbox": str(self.inbox), "catalog": str(self.catalog),
            "spool": str(self.spool), "page_size": self.page_size,
        }


@dataclass(frozen=True)
class GrepOptions:
    source_authority: AuthorityReference
    spool: Path
    max_pages: int
    page_size: int
    timeout_seconds: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "GrepOptions":
        fields = {"source_authority", "spool", "max_pages", "page_size", "timeout_seconds"}
        if not isinstance(value, Mapping) or set(value) != fields:
            raise ConnectorHostError("invalid_grep_options")
        return cls(
            source_authority=AuthorityReference.from_mapping(value["source_authority"]),
            spool=_absolute_path(value["spool"], "spool"),
            max_pages=_integer(value["max_pages"], "max_pages", 1, 1000),
            page_size=_integer(value["page_size"], "page_size", 1, 100),
            timeout_seconds=_integer(value["timeout_seconds"], "timeout_seconds", 1, 60),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "source_authority": self.source_authority.to_mapping(),
            "spool": str(self.spool), "max_pages": self.max_pages,
            "page_size": self.page_size, "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True)
class HostedJobDefinition:
    schedule: ScheduleDefinition
    source_id: str
    endpoint: str
    brain_authority: AuthorityReference
    privacy_mode: str
    connector: ExportOptions | GrepOptions

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "HostedJobDefinition":
        if not isinstance(value, Mapping) or set(value) != JOB_FIELDS:
            raise ConnectorHostError("invalid_job_fields")
        try:
            schedule = ScheduleDefinition.from_mapping(value["schedule"])
        except ValueError as error:
            raise ConnectorHostError("invalid_schedule") from error
        source_id = value["source_id"]
        if not isinstance(source_id, str) or not SOURCE_ID.fullmatch(source_id):
            raise ConnectorHostError("invalid_source_id")
        endpoint = _endpoint(value["endpoint"])
        brain = AuthorityReference.from_mapping(value["brain_authority"])
        privacy_mode = value["privacy_mode"]
        if schedule.connector_id == "openai.export-inbox":
            connector = ExportOptions.from_mapping(value["connector"])
            authorities = {"brain"}
        elif schedule.connector_id == "grep.ai":
            connector = GrepOptions.from_mapping(value["connector"])
            authorities = {"brain", "source"}
            if brain.fingerprint() == connector.source_authority.fingerprint():
                raise ConnectorHostError("authority_alias")
        else:
            raise ConnectorHostError("connector_not_hosted")
        try:
            validate_policy(
                schedule.connector_id, visibility="private",
                privacy_mode=privacy_mode, authorities=authorities,
            )
        except ConnectorRegistryError as error:
            raise ConnectorHostError("invalid_registry_policy") from error
        return cls(schedule, source_id, endpoint, brain, privacy_mode, connector)

    @property
    def source_authority(self) -> AuthorityReference | None:
        return self.connector.source_authority if isinstance(self.connector, GrepOptions) else None

    @property
    def durable_paths(self) -> tuple[Path, ...]:
        if isinstance(self.connector, ExportOptions):
            return (self.connector.catalog, self.connector.spool)
        return (self.connector.spool,)

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schedule": self.schedule.to_public(), "source_id": self.source_id,
            "endpoint": self.endpoint, "brain_authority": self.brain_authority.to_mapping(),
            "privacy_mode": self.privacy_mode, "connector": self.connector.to_mapping(),
        }


def _endpoint(value: Any) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ConnectorHostError("invalid_endpoint")
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError as error:
        raise ConnectorHostError("invalid_endpoint") from error
    valid_port = port is None or 1 <= port <= 65535
    production = parsed.scheme == "https" and bool(parsed.hostname) and valid_port
    loopback = (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost"}
        and port is not None
        and valid_port
    )
    if not (production or loopback) or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConnectorHostError("invalid_endpoint")
    if parsed.path not in {"", "/"}:
        raise ConnectorHostError("invalid_endpoint")
    return value.rstrip("/")


@dataclass(frozen=True)
class ConnectorHostConfig:
    schema_version: int
    jobs: tuple[HostedJobDefinition, ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ConnectorHostConfig":
        if not isinstance(value, Mapping) or set(value) != CONFIG_FIELDS:
            raise ConnectorHostError("invalid_config_fields")
        if value["schema_version"] != HOST_SCHEMA_VERSION or isinstance(value["schema_version"], bool):
            raise ConnectorHostError("invalid_schema_version")
        raw_jobs = value["jobs"]
        if not isinstance(raw_jobs, list) or not 1 <= len(raw_jobs) <= 64:
            raise ConnectorHostError("invalid_jobs")
        jobs = tuple(HostedJobDefinition.from_mapping(item) for item in raw_jobs)
        for values, label in (
            ([job.schedule.job_key for job in jobs], "job_key"),
            ([job.source_id for job in jobs], "source_id"),
            ([str(path) for job in jobs for path in job.durable_paths], "durable_path"),
        ):
            if len(values) != len(set(values)):
                raise ConnectorHostError(f"duplicate_{label}")
        return cls(schema_version=1, jobs=jobs)

    def to_mapping(self) -> dict[str, Any]:
        return {"schema_version": 1, "jobs": [job.to_mapping() for job in self.jobs]}


def load_host_config(path: Path) -> ConnectorHostConfig:
    path = Path(path)
    try:
        parent = path.parent.lstat()
        metadata = path.lstat()
    except OSError:
        raise ConnectorHostError("config_unavailable") from None
    if not stat.S_ISDIR(parent.st_mode) or stat.S_IMODE(parent.st_mode) & 0o077:
        raise ConnectorHostError("config_parent_not_private")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ConnectorHostError("config_not_regular")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ConnectorHostError("config_not_private")
    if metadata.st_size > MAX_CONFIG_BYTES:
        raise ConnectorHostError("config_too_large")
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_dev != metadata.st_dev
                or opened.st_ino != metadata.st_ino or opened.st_size > MAX_CONFIG_BYTES):
            raise ConnectorHostError("config_changed")
        raw = os.read(descriptor, MAX_CONFIG_BYTES + 1)
    except ConnectorHostError:
        raise
    except OSError:
        raise ConnectorHostError("config_unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ConnectorHostError("config_invalid_json") from None
    return ConnectorHostConfig.from_mapping(value)


def preview_host_config(config: ConnectorHostConfig) -> dict[str, Any]:
    if not isinstance(config, ConnectorHostConfig):
        raise ConnectorHostError("invalid_config")
    connector_counts: dict[str, int] = {}
    authority_counts = {"file": 0, "keychain": 0}
    privacy_counts: dict[str, int] = {}
    for job in config.jobs:
        connector_counts[job.schedule.connector_id] = connector_counts.get(job.schedule.connector_id, 0) + 1
        privacy_counts[job.privacy_mode] = privacy_counts.get(job.privacy_mode, 0) + 1
        references = [job.brain_authority]
        if job.source_authority is not None:
            references.append(job.source_authority)
        for reference in references:
            authority_counts[reference.kind] += 1
    return {
        "schema_version": 1, "mode": "connector-supervisor-config-preview",
        "jobs": len(config.jobs), "enabled": sum(job.schedule.enabled for job in config.jobs),
        "disabled": sum(not job.schedule.enabled for job in config.jobs),
        "connectors": dict(sorted(connector_counts.items())),
        "authority_references": authority_counts,
        "privacy_modes": dict(sorted(privacy_counts.items())),
        "credential_reads": 0, "source_reads": 0, "network_requests": 0, "writes": 0,
    }


def validate_reserved_export_inbox(config: ConnectorHostConfig, inbox: Path) -> None:
    """Fail closed when another installed owner already controls an export inbox."""

    if not isinstance(config, ConnectorHostConfig):
        raise ConnectorHostError("invalid_config")
    reserved = Path(os.path.normpath(str(Path(inbox))))
    for job in config.jobs:
        if isinstance(job.connector, ExportOptions) and job.connector.inbox == reserved:
            raise ConnectorHostError("duplicate_export_inbox_owner")


class ConnectorHost:
    def __init__(self, *, config: ConnectorHostConfig, store: SupervisorStore,
                 runners: list[ConnectorRunner], connectors: list[Any], jobs: tuple[ScheduledJob, ...]):
        self.config = config
        self.store = store
        self.runners = runners
        self.connectors = connectors
        self.jobs = jobs
        self.supervisor = ConnectorSupervisor(store)

    def close(self) -> None:
        for runner in reversed(self.runners):
            runner.close()
        for connector in reversed(self.connectors):
            close = getattr(connector, "close", None)
            if callable(close):
                close()
        self.store.close()


def build_host(config: ConnectorHostConfig, *, state_path: Path, grep_transport: Any = None) -> ConnectorHost:
    if not isinstance(config, ConnectorHostConfig):
        raise ConnectorHostError("invalid_config")
    store = SupervisorStore(state_path)
    runners: list[ConnectorRunner] = []
    connectors: list[Any] = []
    jobs: list[ScheduledJob] = []
    try:
        for item in config.jobs:
            privacy = PrivacyPolicy(mode=item.privacy_mode)
            brain = BrainClient(
                endpoint=item.endpoint, token=item.brain_authority.load_brain(),
                source_id=item.source_id, principal_id="owner", visibility="private",
            )
            if isinstance(item.connector, ExportOptions):
                connector = ExportInboxConnector(
                    inbox=item.connector.inbox, catalog_path=item.connector.catalog,
                    source_id=item.source_id, page_size=item.connector.page_size,
                    privacy_mode=item.privacy_mode,
                )
                spool = item.connector.spool
            else:
                connector = GrepAIConnector(
                    api_key=item.connector.source_authority.load_source(),
                    source_id=item.source_id, max_pages=item.connector.max_pages,
                    page_size=item.connector.page_size, timeout=item.connector.timeout_seconds,
                    transport=grep_transport,
                )
                spool = item.connector.spool
            runner = ConnectorRunner(
                connector=connector, brain=brain, spool_path=spool, privacy=privacy,
            )
            connectors.append(connector)
            runners.append(runner)
            jobs.append(ScheduledJob(item.schedule, runner.run_once))
    except Exception:
        for runner in reversed(runners):
            runner.close()
        for connector in reversed(connectors):
            close = getattr(connector, "close", None)
            if callable(close):
                close()
        store.close()
        raise
    return ConnectorHost(
        config=config, store=store, runners=runners,
        connectors=connectors, jobs=tuple(jobs),
    )


def run_host_once(config_path: Path, state_path: Path, *,
                  clock=time.time) -> dict[str, Any]:
    try:
        host = build_host(load_host_config(config_path), state_path=state_path)
    except ConnectorHostError:
        raise
    except PermissionError:
        raise ConnectorHostError("authority_unavailable") from None
    except (OSError, RuntimeError, ValueError):
        raise ConnectorHostError("host_unavailable") from None
    try:
        now = clock()
        return host.supervisor.tick(host.jobs, now=now, clock=clock)
    finally:
        host.close()


def run_host_daemon(config_path: Path, state_path: Path, *, max_wait_seconds: int = 30,
                    max_cycles: int | None = None, clock=time.time) -> dict[str, Any]:
    """Reload the private config between bounded C8F loop cycles."""
    if not isinstance(max_wait_seconds, int) or isinstance(max_wait_seconds, bool) or not 1 <= max_wait_seconds <= 300:
        raise ConnectorHostError("invalid_max_wait")
    if max_cycles is not None and (
        not isinstance(max_cycles, int) or isinstance(max_cycles, bool) or max_cycles < 1
    ):
        raise ConnectorHostError("invalid_max_cycles")
    wake_event = threading.Event()
    stop_event = threading.Event()
    previous: dict[int, Any] = {}

    def stop(_number, _frame) -> None:
        stop_event.set(); wake_event.set()

    def wake(_number, _frame) -> None:
        wake_event.set()

    handlers = {signal.SIGTERM: stop, signal.SIGINT: stop}
    if hasattr(signal, "SIGHUP"):
        handlers[signal.SIGHUP] = wake
    for number, handler in handlers.items():
        previous[number] = signal.getsignal(number)
        signal.signal(number, handler)
    cycles = 0
    try:
        while not stop_event.is_set() and (max_cycles is None or cycles < max_cycles):
            try:
                host = build_host(load_host_config(config_path), state_path=state_path)
            except ConnectorHostError:
                raise
            except PermissionError:
                raise ConnectorHostError("authority_unavailable") from None
            except (OSError, RuntimeError, ValueError):
                raise ConnectorHostError("host_unavailable") from None
            try:
                cycles += host.supervisor.run_loop(
                    host.jobs, clock=clock, wake_event=wake_event, stop_event=stop_event,
                    max_wait_seconds=max_wait_seconds, max_cycles=1,
                )
            finally:
                host.close()
        return {"schema_version": 1, "status": "stopped", "cycles": cycles}
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
