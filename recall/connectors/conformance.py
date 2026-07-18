"""Aggregate-only conformance runner for connector fixture factories."""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from connectors.kit import decode_page_wire, encode_page_wire
from connectors.registry import ConnectorDefinitionV3
from connectors.sdk import (
    ConnectorContractError,
    ConnectorPage,
    ConnectorRunError,
    ConnectorRunner,
    PullConnector,
)
from privacy.policy import PrivacyPolicy


CONNECTOR_CONFORMANCE_VERSION = "recall.connector-conformance.v1"
CONNECTOR_CONFORMANCE_CELLS = (
    "acknowledged_replay",
    "connector_identity",
    "content_revision",
    "empty_terminal",
    "explicit_tombstone",
    "first_page_ack",
    "invalid_page",
    "lost_ack_replay",
    "manifest_round_trip",
    "pagination",
    "privacy_before_spool",
    "rate_limit",
    "wire_round_trip",
)
CONNECTOR_ID = re.compile(r"[a-z][a-z0-9_.-]{2,63}\Z")


class ConnectorFixtureFactory(Protocol):
    manifest: ConnectorDefinitionV3
    source_id: str

    def build(self, scenario: str) -> PullConnector: ...


@dataclass(frozen=True)
class ConformanceReport:
    connector_id: str
    passed: int
    total: int
    failures: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.connector_id, str) or not CONNECTOR_ID.fullmatch(self.connector_id):
            raise ValueError("invalid connector_id")
        if (
            type(self.total) is not int
            or type(self.passed) is not int
            or self.total != len(CONNECTOR_CONFORMANCE_CELLS)
            or not 0 <= self.passed <= self.total
        ):
            raise ValueError("invalid conformance counts")
        if (
            not isinstance(self.failures, tuple)
            or len(self.failures) != len(set(self.failures))
            or any(cell not in CONNECTOR_CONFORMANCE_CELLS for cell in self.failures)
            or len(self.failures) != self.total - self.passed
            or self.failures != tuple(sorted(self.failures))
        ):
            raise ValueError("invalid conformance failures")

    def to_public(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "conformance_version": CONNECTOR_CONFORMANCE_VERSION,
            "connector_id": self.connector_id,
            "status": "pass" if not self.failures else "fail",
            "passed": self.passed,
            "total": self.total,
            "failures": list(self.failures),
        }


class _Brain:
    def __init__(self, source_id: str, *, fail_after_commit: bool = False):
        self.source_id = source_id
        self.fail_after_commit = fail_after_commit
        self.calls = 0
        self.events: dict[tuple[str, str, str], dict] = {}

    def ingest(self, events: list[dict]) -> dict:
        self.calls += 1
        inserted = 0
        duplicates = 0
        receipts = []
        for event in events:
            if event.get("source_id") != self.source_id:
                raise PermissionError("source authority mismatch")
            key = (event["source_id"], event["native_id"], event["content_sha256"])
            if key in self.events:
                duplicates += 1
            else:
                self.events[key] = event
                inserted += 1
            receipts.append(
                f"recall://{event['source_id']}/{event['native_id']}?rev=1"
            )
        if self.fail_after_commit:
            self.fail_after_commit = False
            raise OSError("synthetic lost acknowledgement")
        return {
            "status": "committed",
            "inserted": inserted,
            "duplicate_events": duplicates,
            "receipts": receipts,
            "replay": bool(duplicates),
        }


def _runner(
    factory: ConnectorFixtureFactory,
    scenario: str,
    spool: Path,
    brain: _Brain,
    *,
    privacy: PrivacyPolicy | None = None,
) -> ConnectorRunner:
    connector = factory.build(scenario)
    if (
        getattr(connector, "connector_id", None) != factory.manifest.connector_id
        or getattr(connector, "source_id", None) != factory.source_id
    ):
        raise AssertionError("connector identity mismatch")
    return ConnectorRunner(
        connector=connector,
        brain=brain,
        spool_path=spool,
        privacy=privacy,
    )


def _manifest_round_trip(factory: ConnectorFixtureFactory, _root: Path) -> None:
    manifest = factory.manifest
    assert isinstance(manifest, ConnectorDefinitionV3)
    assert ConnectorDefinitionV3.from_mapping(manifest.to_public()) == manifest


def _connector_identity(factory: ConnectorFixtureFactory, root: Path) -> None:
    brain = _Brain(factory.source_id)
    runner = _runner(factory, "first_page", root / "identity.db", brain)
    try:
        assert runner.connector_id == factory.manifest.connector_id
        assert runner.source_id == factory.source_id
    finally:
        runner.close()


def _first_page_ack(factory: ConnectorFixtureFactory, root: Path) -> None:
    brain = _Brain(factory.source_id)
    runner = _runner(factory, "first_page", root / "first.db", brain)
    try:
        result = runner.run_once()
        assert result["acked"] == 1
        assert len(brain.events) == 1
        assert runner.doctor()["checkpointed"]
    finally:
        runner.close()


def _pagination(factory: ConnectorFixtureFactory, root: Path) -> None:
    brain = _Brain(factory.source_id)
    runner = _runner(factory, "pagination", root / "pagination.db", brain)
    try:
        first = runner.run_once()
        second = runner.run_once()
        assert first["acked"] == 1 and second["acked"] == 1
        assert len(brain.events) == 2
        assert runner.doctor()["checkpointed"]
    finally:
        runner.close()


def _empty_terminal(factory: ConnectorFixtureFactory, root: Path) -> None:
    brain = _Brain(factory.source_id)
    runner = _runner(factory, "empty_page", root / "empty.db", brain)
    try:
        result = runner.run_once()
        assert result["acked"] == 0
        assert brain.calls == 0
        assert runner.doctor()["checkpointed"]
    finally:
        runner.close()


def _lost_ack_replay(factory: ConnectorFixtureFactory, root: Path) -> None:
    spool = root / "lost-ack.db"
    brain = _Brain(factory.source_id, fail_after_commit=True)
    connector = factory.build("lost_ack")
    if (
        connector.connector_id != factory.manifest.connector_id
        or connector.source_id != factory.source_id
    ):
        raise AssertionError("connector identity mismatch")
    first = ConnectorRunner(connector=connector, brain=brain, spool_path=spool)
    try:
        try:
            first.run_once()
        except ConnectorRunError as error:
            assert error.error_code == "brain_unavailable"
        else:
            raise AssertionError("lost acknowledgement was treated as success")
        assert not first.doctor()["checkpointed"]
        assert first.doctor()["pending"] == 1
    finally:
        first.close()
    recovered = ConnectorRunner(connector=connector, brain=brain, spool_path=spool)
    try:
        result = recovered.run_once()
        assert result["replayed"] == 1
        assert len(brain.events) == 1
        assert brain.calls == 2
        assert recovered.doctor()["checkpointed"]
    finally:
        recovered.close()


def _content_revision(factory: ConnectorFixtureFactory, root: Path) -> None:
    brain = _Brain(factory.source_id)
    runner = _runner(factory, "revision", root / "revision.db", brain)
    try:
        runner.run_once()
        runner.run_once()
        events = tuple(brain.events.values())
        assert len(events) == 2
        assert len({event["native_id"] for event in events}) == 1
        assert len({event["content_sha256"] for event in events}) == 2
    finally:
        runner.close()


def _explicit_tombstone(factory: ConnectorFixtureFactory, root: Path) -> None:
    brain = _Brain(factory.source_id)
    runner = _runner(factory, "tombstone", root / "tombstone.db", brain)
    try:
        runner.run_once()
        runner.run_once()
        events = tuple(brain.events.values())
        assert len(events) == 2
        assert {event["kind"] for event in events} == {"connector_record", "tombstone"}
    finally:
        runner.close()


def _acknowledged_replay(factory: ConnectorFixtureFactory, root: Path) -> None:
    brain = _Brain(factory.source_id)
    runner = _runner(factory, "replay", root / "replay.db", brain)
    try:
        runner.run_once()
        result = runner.run_once()
        assert result["acked"] == 0
        assert result["deduplicated"] == 1
        assert brain.calls == 1
        assert len(brain.events) == 1
    finally:
        runner.close()


def _privacy_before_spool(factory: ConnectorFixtureFactory, root: Path) -> None:
    brain = _Brain(factory.source_id)
    spool = root / "privacy.db"
    runner = _runner(
        factory,
        "privacy_drop",
        spool,
        brain,
        privacy=PrivacyPolicy(mode="drop"),
    )
    try:
        result = runner.run_once()
        assert result["dropped"] == 1
        assert brain.calls == 0
        assert runner.doctor()["checkpointed"]
        private_bytes = b"".join(
            path.read_bytes()
            for path in spool.parent.glob(spool.name + "*")
            if path.is_file()
        )
        assert b"conformance-private-canary-77" not in private_bytes
    finally:
        runner.close()


def _rate_limit(factory: ConnectorFixtureFactory, root: Path) -> None:
    brain = _Brain(factory.source_id)
    runner = _runner(factory, "rate_limit", root / "rate.db", brain)
    try:
        result = runner.run_once()
        assert result["status"] == "backoff"
        assert result["error_code"] == "connector_rate_limited"
        assert 1 <= result["retry_after_seconds"] <= 3600
        assert brain.calls == 0
        assert not runner.doctor()["checkpointed"]
    finally:
        runner.close()


def _invalid_page(factory: ConnectorFixtureFactory, root: Path) -> None:
    brain = _Brain(factory.source_id)
    runner = _runner(factory, "invalid_page", root / "invalid.db", brain)
    try:
        try:
            runner.run_once()
        except ConnectorContractError:
            pass
        else:
            raise AssertionError("invalid page was treated as success")
        assert runner.doctor()["last_error_code"] == "connector_invalid_page"
        assert not runner.doctor()["checkpointed"]
    finally:
        runner.close()


def _wire_round_trip(factory: ConnectorFixtureFactory, _root: Path) -> None:
    connector = factory.build("wire")
    if (
        connector.connector_id != factory.manifest.connector_id
        or connector.source_id != factory.source_id
    ):
        raise AssertionError("connector identity mismatch")
    page = connector.pull(None)
    assert isinstance(page, ConnectorPage)
    assert decode_page_wire(encode_page_wire(page)) == page


_CELL_RUNNERS = {
    "acknowledged_replay": _acknowledged_replay,
    "connector_identity": _connector_identity,
    "content_revision": _content_revision,
    "empty_terminal": _empty_terminal,
    "explicit_tombstone": _explicit_tombstone,
    "first_page_ack": _first_page_ack,
    "invalid_page": _invalid_page,
    "lost_ack_replay": _lost_ack_replay,
    "manifest_round_trip": _manifest_round_trip,
    "pagination": _pagination,
    "privacy_before_spool": _privacy_before_spool,
    "rate_limit": _rate_limit,
    "wire_round_trip": _wire_round_trip,
}


def run_connector_conformance(factory: ConnectorFixtureFactory) -> ConformanceReport:
    """Run the fixed matrix and return only aggregate, content-free cell names."""
    failures = []
    with tempfile.TemporaryDirectory(prefix="recall-connector-conformance-") as directory:
        root = Path(directory)
        for cell in CONNECTOR_CONFORMANCE_CELLS:
            try:
                _CELL_RUNNERS[cell](factory, root)
            except Exception:
                failures.append(cell)
    connector_id = getattr(getattr(factory, "manifest", None), "connector_id", "")
    failure_tuple = tuple(sorted(failures))
    return ConformanceReport(
        connector_id=connector_id,
        passed=len(CONNECTOR_CONFORMANCE_CELLS) - len(failure_tuple),
        total=len(CONNECTOR_CONFORMANCE_CELLS),
        failures=failure_tuple,
    )


def render_conformance_report(report: ConformanceReport) -> str:
    return json.dumps(report.to_public(), sort_keys=True, separators=(",", ":"))


__all__ = [
    "CONNECTOR_CONFORMANCE_CELLS",
    "CONNECTOR_CONFORMANCE_VERSION",
    "ConformanceReport",
    "ConnectorFixtureFactory",
    "render_conformance_report",
    "run_connector_conformance",
]
