from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from client import cli as client_cli
from connectors.grep_ai import (
    GrepAIConnector,
    GrepAIResponse,
    GrepAIUpstreamError,
    KNOWN_STATUSES,
    MAX_RESPONSE_BYTES,
    NONTERMINAL,
    TERMINAL_MEMORY,
    TERMINAL_NO_MEMORY,
    decode_cursor,
    load_private_api_key,
)
from connectors.sdk import ConnectorContractError, ConnectorRateLimited, ConnectorRunner
from privacy.policy import PrivacyPolicy


ROOT = Path(__file__).parent
CORPUS = ROOT / "grep_ai_v2/corpus.jsonl"
MANIFEST = ROOT / "grep_ai_v2/manifest.json"
CANARY = "grep-ai-private-canary-77"
KEY = "parcha-synthetic-" + "a" * 32


def encoded(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class FakeTransport:
    def __init__(self, pages=None, details=None, failures=None):
        self.pages = pages or {}
        self.details = details or {}
        self.failures = list(failures or [])
        self.requests = []

    def request(self, *, path, query, headers, timeout, max_bytes):
        self.requests.append({"path": path, "query": query, "headers": headers,
                              "timeout": timeout, "max_bytes": max_bytes})
        if self.failures:
            return self.failures.pop(0)
        if path == "/api/v2/research":
            value = self.pages[query.get("cursor")]
        else:
            value = self.details[path.rsplit("/", 1)[-1]]
        return GrepAIResponse(
            status=200, headers={"content-type": "application/json"}, body=encoded(value),
            final_url="https://api.grep.ai" + path,
        )


class FakeBrain:
    def __init__(self):
        self.events = {}
        self.calls = 0
        self.fail_after_commit = False

    def ingest(self, events):
        self.calls += 1
        receipts = []
        inserted = duplicates = 0
        for event in events:
            key = (event["source_id"], event["native_id"], event["content_sha256"])
            if key in self.events:
                duplicates += 1
            else:
                self.events[key] = event
                inserted += 1
            receipts.append(f"recall://{event['source_id']}/{event['native_id']}?rev=1")
        if self.fail_after_commit:
            self.fail_after_commit = False
            raise OSError("synthetic lost acknowledgement")
        return {
            "status": "committed", "inserted": inserted,
            "duplicate_events": duplicates, "receipts": receipts, "replay": False,
        }


def connector(transport, **kwargs):
    return GrepAIConnector(
        api_key=KEY, source_id="grep-ai:synthetic:c8d", transport=transport,
        max_pages=10, **kwargs,
    )


class GrepAIFrozenEvalTest(unittest.TestCase):
    def test_manifest_and_projection_thresholds(self):
        manifest = json.loads(MANIFEST.read_text())
        rows = [json.loads(line) for line in CORPUS.read_text().splitlines()]
        self.assertEqual(hashlib.sha256(CORPUS.read_bytes()).hexdigest(), manifest["corpus_sha256"])
        self.assertEqual(len(rows), manifest["cases"])
        completed = projected = nonterminal = nonterminal_projected = invalid_accepted = survival = duplicates = 0
        for row in rows:
            transport = FakeTransport(pages={None: row["list"]}, details=row["details"])
            try:
                page = connector(transport).pull(None)
            except ConnectorContractError:
                self.assertFalse(row["valid"], row["case"])
                continue
            invalid_accepted += int(not row["valid"])
            self.assertEqual(len(page.records), row["expected_records"], row["case"])
            is_completed = row["case"] in {
                "complete", "completed-structured", "sensitive-complete",
                "live-null-widgets", "control-normalized-complete", "empty-report-complete",
                "official-minimal-completed",
            }
            completed += int(is_completed)
            projected += len(page.records) if is_completed else 0
            is_nonterminal = row["case"] in {
                "failed-not-memory", "nonterminal-not-memory", "all-nonmemory-statuses",
                "official-planning-statuses", "observed-paused-status",
                "observed-in-progress-statuses",
            }
            nonterminal += int(is_nonterminal)
            nonterminal_projected += len(page.records) if is_nonterminal else 0
            duplicates += len(page.records) - len({record.native_id for record in page.records})
            for record in page.records:
                decision = PrivacyPolicy(mode="drop").apply(record.content)
                if row["case"] == "sensitive-complete":
                    self.assertEqual(decision.action, "drop")
                if decision.value is not None:
                    survival += int(CANARY in json.dumps(decision.value))
        thresholds = manifest["thresholds"]
        self.assertEqual(projected / completed, thresholds["completed_projection"])
        self.assertEqual(nonterminal_projected / nonterminal, thresholds["nonterminal_projection"])
        self.assertEqual(invalid_accepted, thresholds["invalid_acceptance"])
        self.assertEqual(survival, thresholds["sensitive_survival"])
        self.assertEqual(duplicates, thresholds["duplicate_live_items"])

    def test_projection_discards_private_surfaces_and_normalizes_links(self):
        row = next(json.loads(line) for line in CORPUS.read_text().splitlines() if json.loads(line)["case"] == "complete")
        page = connector(FakeTransport(pages={None: row["list"]}, details=row["details"])).pull(None)
        record = page.records[0]
        rendered = json.dumps({"content": record.content, "provenance": record.provenance})
        for excluded in ("attachments", "context", "revisions", "widgets", "revision_sha", "job_id", "slug"):
            self.assertNotIn(excluded, rendered)
        self.assertIn("https://example.invalid/doc", record.content["report_markdown"])
        self.assertNotIn("signed=", rendered)
        self.assertNotIn("fragment", rendered)
        self.assertTrue(record.native_id.startswith("grep-ai-"))

    def test_live_json_control_characters_are_normalized_before_projection(self):
        row = next(
            json.loads(line) for line in CORPUS.read_text().splitlines()
            if json.loads(line)["case"] == "control-normalized-complete"
        )
        record = connector(FakeTransport(pages={None: row["list"]}, details=row["details"])).pull(None).records[0]
        rendered = json.dumps(record.content, ensure_ascii=False)
        self.assertNotIn("\x00", rendered)
        self.assertIn("\ufffdcontrol", rendered)

    def test_official_minimal_completed_shape_is_deterministic(self):
        row = next(
            json.loads(line) for line in CORPUS.read_text().splitlines()
            if json.loads(line)["case"] == "official-minimal-completed"
        )
        record = connector(FakeTransport(pages={None: row["list"]}, details=row["details"])).pull(None).records[0]
        self.assertEqual(record.occurred_at, "1970-01-01T00:00:00Z")
        self.assertEqual(record.content["report_markdown"], "")
        self.assertEqual(record.content["question"], "Synthetic minimal completed research")

    def test_status_partition_is_closed_and_behavioral(self):
        self.assertEqual(TERMINAL_MEMORY, {"complete", "completed"})
        self.assertEqual(
            NONTERMINAL,
            {
                "queued", "moderation", "planning", "running", "paused",
                "in progress", "in_progress",
            },
        )
        self.assertEqual(
            TERMINAL_NO_MEMORY,
            {"failed", "blocked", "cancelled", "canceled"},
        )
        partitions = (TERMINAL_MEMORY, NONTERMINAL, TERMINAL_NO_MEMORY)
        self.assertTrue(all(left.isdisjoint(right) for index, left in enumerate(partitions)
                            for right in partitions[index + 1:]))
        self.assertEqual(KNOWN_STATUSES, set().union(*partitions))


class GrepAICursorTest(unittest.TestCase):
    def test_head_reset_finds_concurrent_insert_without_reimporting_watermark(self):
        rows = {row["case"]: row for row in map(json.loads, CORPUS.read_text().splitlines())}
        j2 = rows["complete"]; j1 = rows["completed-structured"]
        old = json.loads(json.dumps(j1["list"]["items"][0])); old["job_id"] = "10000000-0000-4000-8000-000000000000"
        old["status"] = "failed"
        initial = {"items": [j2["list"]["items"][0], j1["list"]["items"][0]], "next_cursor": "upstream-two", "has_more": True}
        second = {"items": [old], "next_cursor": None, "has_more": False}
        transport = FakeTransport(pages={None: initial, "upstream-two": second}, details={**j2["details"], **j1["details"]})
        adapter = connector(transport)
        first = adapter.pull(None)
        self.assertEqual(len(first.records), 2); self.assertTrue(first.has_more)
        second_page = adapter.pull(first.next_cursor)
        self.assertEqual(len(second_page.records), 0); self.assertFalse(second_page.has_more)
        state = decode_cursor(second_page.next_cursor)
        self.assertEqual(state["phase"], "head")

        new_row = json.loads(json.dumps(j2["list"]["items"][0])); new_row["job_id"] = "10000000-0000-4000-8000-000000000009"
        new_detail = json.loads(json.dumps(next(iter(j2["details"].values())))); new_detail["job_id"] = new_row["job_id"]
        transport.pages[None] = {"items": [new_row, j2["list"]["items"][0]], "next_cursor": "must-not-follow", "has_more": True}
        transport.details[new_row["job_id"]] = new_detail
        head = adapter.pull(second_page.next_cursor)
        self.assertEqual(len(head.records), 1)
        self.assertFalse(head.has_more)
        self.assertEqual(decode_cursor(head.next_cursor)["phase"], "head")

    def test_nonterminal_job_remains_ahead_of_checkpoint_until_completion(self):
        rows = {row["case"]: row for row in map(json.loads, CORPUS.read_text().splitlines())}
        complete = rows["complete"]
        pending = json.loads(json.dumps(rows["nonterminal-not-memory"]["list"]["items"][0]))
        boundary = json.loads(json.dumps(rows["failed-not-memory"]["list"]["items"][0]))
        first_transport = FakeTransport(pages={None: {
            "items": [complete["list"]["items"][0], pending, boundary],
            "next_cursor": None, "has_more": False,
        }}, details=complete["details"])
        adapter = connector(first_transport)
        first = adapter.pull(None)
        self.assertEqual(len(first.records), 1)
        self.assertEqual(
            decode_cursor(first.next_cursor)["watermark"],
            hashlib.sha256(boundary["job_id"].encode()).hexdigest(),
        )

        pending["status"] = "complete"
        detail = json.loads(json.dumps(next(iter(complete["details"].values()))))
        detail["job_id"] = pending["job_id"]
        detail["question"] = pending["question"]
        second_transport = FakeTransport(
            pages={None: {"items": [complete["list"]["items"][0], pending, boundary],
                          "next_cursor": None, "has_more": False}},
            details={**complete["details"], pending["job_id"]: detail},
        )
        adapter.transport = second_transport
        second = adapter.pull(first.next_cursor)
        self.assertIn(
            "grep-ai-" + hashlib.sha256(pending["job_id"].encode()).hexdigest()[:48],
            {record.native_id for record in second.records},
        )

    def test_paused_job_stays_ahead_of_checkpoint_without_detail_fetch(self):
        rows = {row["case"]: row for row in map(json.loads, CORPUS.read_text().splitlines())}
        paused = rows["observed-paused-status"]["list"]["items"][0]
        settled = json.loads(json.dumps(rows["failed-not-memory"]["list"]["items"][0]))
        transport = FakeTransport(pages={None: {
            "items": [paused, settled], "next_cursor": None, "has_more": False,
        }})
        page = connector(transport).pull(None)
        self.assertEqual(page.records, ())
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(
            decode_cursor(page.next_cursor)["watermark"],
            hashlib.sha256(settled["job_id"].encode()).hexdigest(),
        )

    def test_lost_ack_replays_without_api_refetch_or_raw_spool(self):
        row = next(json.loads(line) for line in CORPUS.read_text().splitlines() if json.loads(line)["case"] == "complete")
        next(iter(row["details"].values()))["context"] = {"raw": "raw-grep-ai-response-canary-99"}
        transport = FakeTransport(pages={None: row["list"]}, details=row["details"])
        brain = FakeBrain(); brain.fail_after_commit = True
        with tempfile.TemporaryDirectory() as directory:
            spool = Path(directory) / "grep-ai.db"
            runner = ConnectorRunner(connector=connector(transport), brain=brain, spool_path=spool,
                                     privacy=PrivacyPolicy(mode="drop"))
            with self.assertRaisesRegex(Exception, "brain_unavailable"):
                runner.run_once()
            self.assertNotIn(b"raw-grep-ai-response-canary-99", spool.read_bytes())
            requests = len(transport.requests)
            runner.close()
            recovered = ConnectorRunner(connector=connector(transport), brain=brain, spool_path=spool,
                                        privacy=PrivacyPolicy(mode="drop"))
            self.assertEqual(recovered.run_once()["replayed"], 1)
            self.assertEqual(len(transport.requests), requests)
            self.assertEqual(len(brain.events), 1)
            recovered.close()

    def test_sensitive_completed_job_is_dropped_before_spool_and_brain(self):
        row = next(json.loads(line) for line in CORPUS.read_text().splitlines() if json.loads(line)["case"] == "sensitive-complete")
        transport = FakeTransport(pages={None: row["list"]}, details=row["details"])
        brain = FakeBrain()
        with tempfile.TemporaryDirectory() as directory:
            spool = Path(directory) / "grep-ai.db"
            runner = ConnectorRunner(connector=connector(transport), brain=brain, spool_path=spool,
                                     privacy=PrivacyPolicy(mode="drop"))
            result = runner.run_once()
            self.assertEqual(result["dropped"], 1)
            self.assertEqual(brain.calls, 0)
            self.assertNotIn(CANARY.encode(), spool.read_bytes())
            runner.close()


class GrepAITransportAndConfigTest(unittest.TestCase):
    def test_private_key_loader_rejects_mode_symlink_shape_and_path_echo(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key"
            path.write_text(KEY); path.chmod(0o600)
            self.assertEqual(load_private_api_key(path), KEY)
            path.chmod(0o644)
            with self.assertRaises(PermissionError) as raised:
                load_private_api_key(path)
            self.assertNotIn(str(path), str(raised.exception))
            path.unlink(); path.symlink_to(Path(directory) / "missing")
            with self.assertRaises(PermissionError):
                load_private_api_key(path)

    def test_auth_headers_are_separate_exact_and_never_in_cursor_or_record(self):
        row = next(json.loads(line) for line in CORPUS.read_text().splitlines() if json.loads(line)["case"] == "complete")
        transport = FakeTransport(pages={None: row["list"]}, details=row["details"])
        page = connector(transport).pull(None)
        self.assertTrue(all(request["headers"]["Authorization"] == "Bearer " + KEY for request in transport.requests))
        rendered = json.dumps({"cursor": page.next_cursor, "records": [record.content for record in page.records]})
        self.assertNotIn(KEY, rendered)
        self.assertNotIn("Authorization", rendered)

    def test_error_registry_is_content_free_and_rate_limit_is_bounded(self):
        def failure(status, code, retry=None):
            headers = {"content-type": "application/json"}
            if retry is not None: headers["retry-after"] = retry
            return GrepAIResponse(status=status, headers=headers, body=encoded({"error": {
                "code": code, "message": "private upstream message", "details": {"received": "private"},
                "request_id": "synthetic-request",
            }}), final_url="https://api.grep.ai/api/v2/research")
        for status, code, expected in (
            (401, "unauthenticated", "grep_ai_unauthenticated"),
            (402, "insufficient_credits", "grep_ai_insufficient_credits"),
            (403, "forbidden", "grep_ai_forbidden"),
            (404, "job_not_found", "grep_ai_job_not_found"),
            (409, "conflict", "grep_ai_conflict"),
            (422, "validation_error", "grep_ai_validation_error"),
            (500, "internal_error", "grep_ai_internal_error"),
            (503, "upstream_unavailable", "grep_ai_upstream_unavailable"),
        ):
            with self.subTest(code=code):
                adapter = connector(FakeTransport(failures=[failure(status, code)]))
                with self.assertRaises(GrepAIUpstreamError) as raised:
                    adapter.pull(None)
                self.assertEqual(str(raised.exception), expected)
                self.assertNotIn("private", str(raised.exception))
        adapter = connector(FakeTransport(failures=[failure(429, "rate_limited", "120")]))
        with self.assertRaises(ConnectorRateLimited) as raised:
            adapter.pull(None)
        self.assertEqual(raised.exception.retry_after_seconds, 120)
        for retry, expected in ((None, 60), ("later", 60), ("99999", 3600), ("0", 1)):
            with self.subTest(retry=retry):
                adapter = connector(FakeTransport(failures=[failure(429, "rate_limited", retry)]))
                with self.assertRaises(ConnectorRateLimited) as raised:
                    adapter.pull(None)
                self.assertEqual(raised.exception.retry_after_seconds, expected)

    def test_body_redirect_host_and_content_type_fail_closed(self):
        valid = encoded({"items": [], "next_cursor": None, "has_more": False})
        cases = (
            GrepAIResponse(200, {"content-type": "application/json"}, b"{", "https://api.grep.ai/api/v2/research"),
            GrepAIResponse(200, {"content-type": "application/json"}, b"\xff", "https://api.grep.ai/api/v2/research"),
            GrepAIResponse(200, {"content-type": "application/json"}, b"x" * (MAX_RESPONSE_BYTES + 1), "https://api.grep.ai/api/v2/research"),
            GrepAIResponse(200, {"content-type": "text/html"}, valid, "https://api.grep.ai/api/v2/research"),
            GrepAIResponse(200, {"content-type": "application/json"}, valid, "https://redirect.invalid/api/v2/research"),
            GrepAIResponse(200, {"content-type": "application/json"}, valid, "https://api.grep.ai:443/api/v2/research"),
        )
        for index, response in enumerate(cases):
            with self.subTest(case=index):
                with self.assertRaises((ConnectorContractError, GrepAIUpstreamError)):
                    connector(FakeTransport(failures=[response])).pull(None)

    def test_cursor_stall_and_page_cap_fail_before_checkpoint(self):
        page = {"items": [], "next_cursor": "repeat", "has_more": True}
        transport = FakeTransport(pages={None: page, "repeat": page})
        adapter = connector(transport)
        first = adapter.pull(None)
        with self.assertRaisesRegex(ConnectorContractError, "cursor_did_not_advance"):
            adapter.pull(first.next_cursor)
        with self.assertRaisesRegex(ConnectorContractError, "page_cap"):
            GrepAIConnector(
                api_key=KEY, source_id="grep-ai:synthetic:c8d",
                transport=FakeTransport(pages={None: page}), max_pages=1,
            ).pull(None)

    def test_config_preview_does_not_read_credentials_or_call_network_and_is_private(self):
        arguments = [
            "recall-brain", "grep-ai-config-preview",
            "--endpoint", "https://brain.example.ts.net", "--source-id", "grep-ai:synthetic:c8d",
            "--token-file", "/synthetic/brain-token-reference",
            "--grep-api-key-file", "/synthetic/grep-key-reference",
            "--spool", "/synthetic/spool-reference",
        ]
        output = io.StringIO()
        with mock.patch("sys.argv", arguments), mock.patch("sys.stdout", output), \
             mock.patch("client.cli.load_private_api_key") as grep_key, \
             mock.patch("client.cli.load_file_token") as brain_key, \
             mock.patch("urllib.request.urlopen") as network:
            client_cli.main()
        grep_key.assert_not_called(); brain_key.assert_not_called(); network.assert_not_called()
        preview = json.loads(output.getvalue())
        self.assertEqual(preview["network_requests"], 0)
        self.assertEqual(preview["writes"], 0)
        self.assertEqual(preview["visibility"], "private")
        self.assertIn("grep-ai-sync", json.dumps(preview))

    def test_config_preview_supports_separate_grep_keychain_reference(self):
        arguments = [
            "recall-brain", "grep-ai-config-preview",
            "--endpoint", "https://brain.example.ts.net", "--source-id", "grep-ai:synthetic:c8d",
            "--keychain-service", "brain.synthetic", "--keychain-account", "grep-ai:synthetic:c8d",
            "--grep-keychain-service", "grep.synthetic", "--grep-keychain-account", "research-read",
            "--spool", "/synthetic/spool-reference",
        ]
        output = io.StringIO()
        with mock.patch("sys.argv", arguments), mock.patch("sys.stdout", output), \
             mock.patch("client.cli.load_keychain_token") as keychain:
            client_cli.main()
        keychain.assert_not_called()
        preview = json.loads(output.getvalue())
        rendered = json.dumps(preview)
        self.assertIn("brain.synthetic", rendered)
        self.assertIn("grep.synthetic", rendered)
        self.assertNotIn(KEY, rendered)


if __name__ == "__main__":
    unittest.main()
