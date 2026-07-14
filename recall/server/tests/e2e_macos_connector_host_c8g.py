#!/usr/bin/env python3
"""Exact-package Darwin/arm64 LaunchAgent proof for the private connector host.

The approved read-only Grep credential arrives on stdin and is never rendered.
All Brain data stays in a bounded in-memory loopback stub and is deleted before
the temporary workspace is removed.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import platform
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path


EXPORT_SOURCE = "chatgpt:synthetic:mac-host"
GREP_SOURCE = "grep-ai:synthetic:mac-host"
EXPORT_MARKER = "mac launch agent export cobalt marker"
LABEL = "ai.parcha.recall.connector-supervisor"


def private_file(path: Path, value: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(value)
    assert path.stat().st_mode & 0o777 == 0o600


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class BrainState:
    def __init__(self):
        self.lock = threading.Lock()
        self.events = {}
        self.revisions = {}

    def ingest(self, events):
        receipts = []
        inserted = duplicates = 0
        with self.lock:
            for event in events:
                key = (event["source_id"], event["native_id"])
                revision = self.revisions.get(key, 0) + 1
                if event.get("kind") == "tombstone":
                    self.events.pop(key, None)
                    self.revisions[key] = revision
                    inserted += 1
                elif key in self.events:
                    revision = self.revisions[key]
                    duplicates += 1
                else:
                    self.events[key] = event
                    self.revisions[key] = revision
                    inserted += 1
                receipts.append(f"recall://{key[0]}/{key[1]}?rev={revision}")
        return {"inserted": inserted, "duplicate_events": duplicates, "receipts": receipts, "replay": False}

    def live(self, source=None):
        with self.lock:
            return [value for (source_id, _), value in self.events.items()
                    if source is None or source_id == source]

    def receipt(self, event):
        key = (event["source_id"], event["native_id"])
        with self.lock:
            return f"recall://{key[0]}/{key[1]}?rev={self.revisions[key]}"


class BrainHandler(BaseHTTPRequestHandler):
    state: BrainState

    def log_message(self, _format, *_args):
        return

    def reply(self, status, value):
        body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/healthz":
            self.reply(200, {"status": "ok"}); return
        if parsed.path == "/v1/doctor":
            self.reply(200, {"live_items": len(self.state.live())}); return
        if parsed.path == "/v1/receipts/resolve":
            receipt = urllib.parse.parse_qs(parsed.query).get("receipt", [""])[0]
            event_part = receipt.split("#", 1)[0]
            try:
                base = event_part.split("?rev=", 1)[0].removeprefix("recall://")
                source, native = base.split("/", 1)
            except ValueError:
                self.reply(400, {"error": "invalid_receipt"}); return
            found = next((event for event in self.state.live(source) if event["native_id"] == native), None)
            if found is None:
                self.reply(404, {"error": "not_found"}); return
            self.reply(200, {"event": found, "items": [{"receipt": self.state.receipt(found)}]}); return
        self.reply(404, {"error": "not_found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        value = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/v1/ingest/batches":
            self.reply(201, self.state.ingest(value["events"])); return
        if self.path == "/v1/search":
            query = str(value.get("query", "")).casefold()
            results = []
            for event in self.state.live():
                if query and query in json.dumps(event.get("content", {}), ensure_ascii=False).casefold():
                    results.append({"native_id": event["native_id"], "receipt": self.state.receipt(event)})
            self.reply(200, {"results": results, "diagnostics": {}}); return
        self.reply(404, {"error": "not_found"})


def schedule(key: str, connector_id: str) -> dict:
    return {
        "schema_version": 1, "job_key": key, "connector_id": connector_id,
        "generation": 1, "enabled": True, "interval_seconds": 2,
        "jitter_seconds": 0, "transient_base_seconds": 2,
        "max_backoff_seconds": 20, "lease_seconds": 30,
        "max_rate_limit_seconds": 60,
    }


def launch_target() -> str:
    return f"gui/{os.getuid()}/{LABEL}"


def launch_pid() -> int | None:
    completed = subprocess.run(["launchctl", "print", launch_target()], text=True, capture_output=True)
    if completed.returncode:
        return None
    matched = re.search(r"(?m)^\s*pid = (\d+)$", completed.stdout)
    return int(matched.group(1)) if matched else None


def wait_until(operation, *, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = operation()
        if value:
            return value
        time.sleep(0.25)
    raise AssertionError("bounded Mac condition did not become true")


def private_content_strings(event) -> list[str]:
    content = event.get("content", {})
    if not isinstance(content, dict):
        raise AssertionError("live Grep event content is not an object")

    def leaves(value):
        if isinstance(value, str) and value:
            yield value
        elif isinstance(value, dict):
            for child in value.values():
                yield from leaves(child)
        elif isinstance(value, list):
            for child in value:
                yield from leaves(child)

    values = []
    for key in ("question", "report_markdown", "structured_output", "expert_id"):
        values.extend(leaves(content.get(key)))
    if not values:
        raise AssertionError("live Grep event has no private source content")
    return values


def private_query(event) -> str:
    text = "\n".join(private_content_strings(event))
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{5,}", text)
    if not words:
        raise AssertionError("live Grep event has no bounded private query probe")
    return words[0]


def http_json(method: str, url: str, value=None):
    body = None if value is None else json.dumps(value).encode()
    request = urllib.request.Request(url, data=body, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def content_free_diagnostic(wrapper: Path, prefix: Path) -> dict:
    def command(arguments):
        completed = subprocess.run([str(wrapper), *arguments], text=True, capture_output=True)
        if completed.returncode:
            return {"available": False}
        return json.loads(completed.stdout)

    return {
        "launch_pid_present": launch_pid() is not None,
        "supervisor": command([
            "connector-supervisor-status", "--state", str(prefix / "state/connector-supervisor.db"),
        ]),
        "grep": command([
            "connector-registry-status", "--connector-id", "grep.ai", "--enabled",
            "--privacy-mode", "scrub", "--authority", "brain", "--authority", "source",
            "--spool", str(prefix / "state/grep.db"),
        ]),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--bundle-root", type=Path, required=True)
    args = parser.parse_args()
    grep_key = sys.stdin.read().strip()
    if not grep_key:
        raise ValueError("stdin is missing approved Grep read authority")

    root = args.workspace
    private = root / "private"; private.mkdir(parents=True, mode=0o700)
    inbox = root / "inbox"; inbox.mkdir(mode=0o700)
    prefix = root / "installed"
    launch_agents = root / "launch-agents"
    config_path = private / "host.json"
    export_brain = private / "export-brain.json"
    grep_brain = private / "grep-brain.json"
    grep_file = private / "grep.key"
    private_file(export_brain, json.dumps({"token": "synthetic-export-brain"}))
    private_file(grep_brain, json.dumps({"token": "synthetic-grep-brain"}))
    private_file(grep_file, grep_key + "\n")
    private_file(inbox / "first.jsonl", json.dumps({
        "conversation_id": "mac-host-one", "message_id": "mac-host-message-one",
        "parent_message_id": None, "create_time": "2026-07-14T09:00:00Z",
        "role": "assistant", "content": {"content_type": "text", "parts": [EXPORT_MARKER]},
    }) + "\n")

    port = free_port(); base = f"http://127.0.0.1:{port}"
    state = BrainState(); BrainHandler.state = state
    server = ThreadingHTTPServer(("127.0.0.1", port), BrainHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    config = {
        "schema_version": 1,
        "jobs": [
            {
                "schedule": schedule("1" * 64, "openai.export-inbox"),
                "source_id": EXPORT_SOURCE, "endpoint": base,
                "brain_authority": {"kind": "file", "path": str(export_brain)},
                "privacy_mode": "scrub",
                "connector": {
                    "inbox": str(inbox), "catalog": str(prefix / "state/export-catalog.db"),
                    "spool": str(prefix / "state/export.db"), "page_size": 100,
                },
            },
            {
                "schedule": schedule("2" * 64, "grep.ai"),
                "source_id": GREP_SOURCE, "endpoint": base,
                "brain_authority": {"kind": "file", "path": str(grep_brain)},
                "privacy_mode": "scrub",
                "connector": {
                    "source_authority": {"kind": "file", "path": str(grep_file)},
                    "spool": str(prefix / "state/grep.db"), "max_pages": 20,
                    "page_size": 5, "timeout_seconds": 20,
                },
            },
        ],
    }
    private_file(config_path, json.dumps(config, sort_keys=True, separators=(",", ":")))
    installed = False
    result = None
    try:
        install = subprocess.run([
            str(args.bundle_root / "install.sh"), "--prefix", str(prefix),
            "--launch-agents", str(launch_agents),
            "--connector-supervisor-config", str(config_path),
        ], check=True, text=True, capture_output=True)
        installed = True
        assert install.stderr == "" and grep_key not in install.stdout
        plist_path = launch_agents / f"{LABEL}.plist"
        plist = plistlib.loads(plist_path.read_bytes())
        arguments = plist["ProgramArguments"]
        assert arguments[1:4] == ["-m", "client.cli", "connector-supervisor-run"]
        assert grep_key not in json.dumps(plist)
        first_pid = wait_until(launch_pid, timeout=30)
        wait_until(lambda: len(state.live(EXPORT_SOURCE)) == 1, timeout=30)
        wrapper = prefix / "bin/recall-brain"
        try:
            grep_event = wait_until(lambda: next(iter(state.live(GREP_SOURCE)), None), timeout=90)
        except AssertionError:
            raise AssertionError(
                "bounded Grep progress failed: "
                + json.dumps(content_free_diagnostic(wrapper, prefix), sort_keys=True)
            ) from None

        export_receipt = state.receipt(state.live(EXPORT_SOURCE)[0])
        grep_receipt = state.receipt(grep_event)
        export_search = http_json("POST", base + "/v1/search", {"query": EXPORT_MARKER, "limit": 5, "filters": {}})
        grep_probe = private_query(grep_event)
        grep_search = http_json("POST", base + "/v1/search", {"query": grep_probe, "limit": 5, "filters": {}})
        assert any(item["receipt"] == export_receipt for item in export_search["results"])
        assert any(item["receipt"] == grep_receipt for item in grep_search["results"])
        assert http_json("GET", base + "/v1/receipts/resolve?" + urllib.parse.urlencode({"receipt": export_receipt}))
        assert http_json("GET", base + "/v1/receipts/resolve?" + urllib.parse.urlencode({"receipt": grep_receipt}))

        private_file(inbox / "second.jsonl", json.dumps({
            "conversation_id": "mac-host-two", "message_id": "mac-host-message-two",
            "parent_message_id": None, "create_time": "2026-07-14T09:00:01Z",
            "role": "assistant", "content": {"content_type": "text", "parts": ["mac hup wake amber marker"]},
        }) + "\n")
        subprocess.run(["launchctl", "kill", "SIGHUP", launch_target()], check=True)
        wait_until(lambda: len(state.live(EXPORT_SOURCE)) == 2, timeout=30)
        subprocess.run(["launchctl", "kill", "SIGKILL", launch_target()], check=True)
        second_pid = wait_until(lambda: (value if (value := launch_pid()) and value != first_pid else None), timeout=30)
        assert second_pid != first_pid

        status = json.loads(subprocess.run([
            str(wrapper), "connector-supervisor-status",
            "--state", str(prefix / "state/connector-supervisor.db"),
        ], check=True, text=True, capture_output=True).stdout)
        assert status["jobs"] == 2
        rendered_status = json.dumps(status)
        assert EXPORT_SOURCE not in rendered_status and GREP_SOURCE not in rendered_status

        private_strings = [EXPORT_MARKER, grep_key, *private_content_strings(grep_event)]
        for path in (prefix / "state").iterdir():
            if path.is_file():
                raw = path.read_bytes()
                assert all(value.encode() not in raw for value in private_strings)

        disable = subprocess.run([
            str(args.bundle_root / "install.sh"), "--prefix", str(prefix),
            "--launch-agents", str(launch_agents), "--disable-connector-supervisor",
        ], check=True, text=True, capture_output=True)
        assert disable.stderr == "" and launch_pid() is None and not plist_path.exists()
        assert (prefix / "state/connector-supervisor.db").is_file()

        forgotten = 0
        for event in list(state.live()):
            source_id = event["source_id"]
            receipt = state.receipt(event)
            token_file = export_brain if source_id == EXPORT_SOURCE else grep_brain
            completed = subprocess.run([
                str(wrapper), "delete", "--endpoint", base,
                "--source-id", source_id, "--principal-id", "owner",
                "--visibility", "private", "--token-file", str(token_file), receipt,
            ], check=True, text=True, capture_output=True)
            assert grep_key not in completed.stdout + completed.stderr
            forgotten += 1
        assert state.live() == []
        result = {
            "status": "pass",
            "summary": {
                "architecture": f"{platform.system()}-{platform.machine()}",
                "launch_agent_loaded": True, "scheduled_sources": 2,
                "searchable_sources": 2, "resolved_sources": 2,
                "hup_wake_progress": 1, "keepalive_restart": 1,
                "aggregate_status_only": True, "disable_preserved_state": True,
                "forgotten_events": forgotten, "live_after_forget": 0,
                "credential_bytes_rendered": False,
                "private_content_in_state_or_logs": False,
            },
        }
    finally:
        if installed:
            subprocess.run([
                str(args.bundle_root / "uninstall.sh"), "--prefix", str(prefix),
                "--launch-agents", str(launch_agents),
            ], check=True, text=True, capture_output=True)
        server.shutdown(); server.server_close(); thread.join(timeout=2)
        grep_key = ""
        shutil.rmtree(root, ignore_errors=True)
    assert result is not None and not root.exists()
    result["summary"]["launch_agent_residue"] = 0
    result["summary"]["install_state_config_residue"] = 0
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
