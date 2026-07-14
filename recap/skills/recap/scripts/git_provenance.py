#!/usr/bin/env python3
"""Read-only git provenance for Recap.

Session observations and present repository state are intentionally separate. This module never
executes a command recovered from a transcript and never treats current git state as proof that the
selected session caused it.
"""

from __future__ import annotations

import ast
import json
import os
import re
import selectors
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any


PROBE_ARGUMENTS = {
    "root": ("rev-parse", "--show-toplevel"),
    "head": ("rev-parse", "--verify", "HEAD"),
    "branch": ("symbolic-ref", "--quiet", "--short", "HEAD"),
    "common_dir": ("rev-parse", "--git-common-dir"),
    "git_dir": ("rev-parse", "--git-dir"),
    "upstream": ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"),
    "merge_base": ("merge-base", "HEAD", "@{upstream}"),
    "status": ("status", "--porcelain=v2", "-z", "--untracked-files=all"),
    "refs": (
        "for-each-ref", "--count=200",
        "--format=%(refname)%1f%(objectname)%1f%(upstream:short)",
        "refs/heads", "refs/remotes",
    ),
    "commits": (
        "log", "-n", "200", "--date=iso-strict",
        "--format=%H%x1f%P%x1f%aI%x1f%cI%x1f%s",
    ),
    "reflog": (
        "reflog", "show", "--all", "-n", "200", "--date=iso-strict",
        "--format=%H%x1f%gD%x1f%gs",
    ),
    "worktrees": ("worktree", "list", "--porcelain"),
}
DEFAULT_TIMEOUT = 8.0
DEFAULT_MAX_BYTES = 2 * 1024 * 1024
SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")
PATCH_PATH_RE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)
DIFF_PATH_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$", re.MULTILINE)
RESULT_SHA_PATTERNS = (
    re.compile(r"^\[[^\]\n]+ ([0-9a-f]{7,40})\]", re.MULTILINE),
    re.compile(r"^HEAD is now at ([0-9a-f]{7,40})\b", re.MULTILINE),
    re.compile(r"^([0-9a-f]{40})$", re.MULTILINE),
)
MUTATION_TOOLS = frozenset({
    "apply_patch", "write", "edit", "multiedit", "notebookedit",
    "functions.apply_patch", "tools.apply_patch",
})
SHELL_TOOLS = frozenset({
    "bash", "shell", "exec_command", "functions.exec_command", "tools.exec_command",
})
ORCHESTRATOR_TOOLS = frozenset({"exec", "functions.exec"})
GIT_MUTATING_VERBS = frozenset({
    "add", "am", "apply", "branch", "checkout", "cherry-pick", "clean", "commit",
    "merge", "mv", "rebase", "reset", "restore", "revert", "rm", "stash", "switch",
    "tag", "worktree",
})
MAX_INDEXED_OBSERVATIONS = 20_000
MAX_REPOSITORY_EVENT_IDS = 1_000
MAX_REPOSITORY_CANDIDATES = 128


class GitProbeError(RuntimeError):
    pass


def _kill(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def run_git_probe(
    repo: Path,
    probe: str,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict[str, Any]:
    """Run one fixed read-only probe with bounded time and output."""
    if probe not in PROBE_ARGUMENTS:
        raise GitProbeError(f"unsupported git probe: {probe}")
    if timeout <= 0 or max_bytes <= 0:
        raise GitProbeError("git probe bounds must be positive")
    command = [
        "git", "-C", str(repo),
        "-c", "core.fsmonitor=false",
        "-c", "core.untrackedCache=false",
        "--no-pager", *PROBE_ARGUMENTS[probe],
    ]
    env = {
        **os.environ,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            shell=False,
        )
    except OSError as exc:
        return {"code": 127, "output": "", "timed_out": False, "truncated": False,
                "error": type(exc).__name__}
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    chunks: list[bytes] = []
    size = 0
    deadline = time.monotonic() + timeout
    timed_out = False
    truncated = False
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _kill(process)
                break
            ready = selector.select(min(remaining, 0.1))
            if not ready:
                if process.poll() is not None:
                    break
                continue
            block = os.read(process.stdout.fileno(), min(65536, max_bytes - size + 1))
            if not block:
                break
            if size + len(block) > max_bytes:
                keep = max(0, max_bytes - size)
                if keep:
                    chunks.append(block[:keep])
                truncated = True
                _kill(process)
                break
            chunks.append(block)
            size += len(block)
        if process.poll() is None:
            try:
                process.wait(timeout=max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill(process)
    finally:
        selector.close()
        process.stdout.close()
    code = process.returncode if process.returncode is not None else 124
    if timed_out:
        code = 124
    elif truncated:
        code = 125
    return {
        "code": code,
        "output": b"".join(chunks).decode("utf-8", "replace"),
        "timed_out": timed_out,
        "truncated": truncated,
        "error": None,
    }


def _text(result: dict[str, Any]) -> str | None:
    if result["code"] != 0 or result["timed_out"] or result["truncated"]:
        return None
    return result["output"].rstrip("\n\0")


def _absolute(value: str, base: Path | None) -> Path | None:
    if not value or "\x00" in value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        if base is None:
            return None
        path = base / path
    try:
        return path.resolve(strict=False)
    except OSError:
        return None


def _existing_ancestor(path: Path) -> Path | None:
    candidate = path
    if candidate.exists() and candidate.is_file():
        candidate = candidate.parent
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.exists() else None


def resolve_repo(path: Path) -> tuple[Path | None, str | None]:
    ancestor = _existing_ancestor(path)
    if ancestor is None:
        return None, "no accessible ancestor"
    result = run_git_probe(ancestor, "root")
    root = _text(result)
    if not root:
        return None, "not an accessible git worktree"
    return Path(root).resolve(strict=False), None


def _parse_payload(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
        if isinstance(value, str):
            value = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _tool_names(event: dict[str, Any]) -> set[str]:
    names = set()
    for entity in event.get("entities") or []:
        if isinstance(entity, dict) and entity.get("kind") == "tool" and entity.get("value"):
            names.add(str(entity["value"]).casefold())
    return names


def _workdir(payload: dict[str, Any] | None, default: Path | None) -> Path | None:
    if payload:
        for key in ("workdir", "cwd", "working_directory"):
            if isinstance(payload.get(key), str):
                return _absolute(payload[key], default)
    return default


def _has_explicit_workdir(payload: dict[str, Any] | None) -> bool:
    return bool(payload and any(isinstance(payload.get(key), str) for key in (
        "workdir", "cwd", "working_directory",
    )))


def _path_values(payload: dict[str, Any] | None) -> list[str]:
    if not payload:
        return []
    values = []
    for key in ("file_path", "path", "target_file", "output", "output_path", "notebook_path"):
        value = payload.get(key)
        if isinstance(value, str):
            values.append(value)
    return values


def _patch_paths(text: str) -> list[str]:
    values = PATCH_PATH_RE.findall(text)
    values.extend(match[1] for match in DIFF_PATH_RE.findall(text))
    return values


def _command(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    for key in ("cmd", "command"):
        if isinstance(payload.get(key), str):
            return payload[key]
    return None


def _balanced_object(source: str, start: int) -> str | None:
    depth = 0
    quote = None
    escaped = False
    for index in range(start, len(source)):
        character = source[index]
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {"\"", "'", "`"}:
            quote = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    return None


def _js_string_property(source: str, key: str) -> str | None:
    pattern = re.compile(
        rf"\b{re.escape(key)}\s*:\s*(?P<literal>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')",
        re.DOTALL,
    )
    match = pattern.search(source)
    if not match:
        return None
    try:
        value = ast.literal_eval(match.group("literal"))
    except (SyntaxError, ValueError):
        return None
    return value if isinstance(value, str) else None


def _orchestrator_exec_payloads(source: str) -> list[dict[str, str]]:
    payloads = []
    cursor = 0
    marker = "tools.exec_command("
    while True:
        call = source.find(marker, cursor)
        if call < 0:
            break
        opening = source.find("{", call + len(marker))
        if opening < 0:
            break
        body = _balanced_object(source, opening)
        if body is None:
            break
        command = _js_string_property(body, "cmd") or _js_string_property(body, "command")
        if command:
            payload = {"cmd": command}
            workdir = _js_string_property(body, "workdir") or _js_string_property(body, "cwd")
            if workdir:
                payload["workdir"] = workdir
            payloads.append(payload)
        cursor = opening + len(body)
    return payloads


def _orchestrator_patch_texts(source: str) -> list[str]:
    assignments = {}
    assignment_pattern = re.compile(
        r"\b(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*"
        r"(?P<literal>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*')",
        re.DOTALL,
    )
    for match in assignment_pattern.finditer(source):
        try:
            value = ast.literal_eval(match.group("literal"))
        except (SyntaxError, ValueError):
            continue
        if isinstance(value, str):
            assignments[match.group("name")] = value
    patches = []
    for match in re.finditer(r"tools\.apply_patch\(\s*([^,)]+)", source):
        argument = match.group(1).strip()
        if argument in assignments:
            patches.append(assignments[argument])
            continue
        try:
            value = ast.literal_eval(argument)
        except (SyntaxError, ValueError):
            continue
        if isinstance(value, str):
            patches.append(value)
    return patches


def _command_segments(command: str) -> list[list[str]]:
    segments = []
    for line in command.splitlines():
        try:
            lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|")
            lexer.whitespace_split = True
            lexer.commenters = ""
            tokens = list(lexer)
        except ValueError:
            continue
        current = []
        for token in tokens:
            if token and set(token) <= {";", "&", "|"}:
                if current:
                    segments.append(current)
                    current = []
            else:
                current.append(token)
        if current:
            segments.append(current)
    return segments


def _git_argv(argv: list[str]) -> tuple[list[str], str | None] | None:
    try:
        index = argv.index("git")
    except ValueError:
        return None
    args = argv[index + 1:]
    git_cwd = None
    if len(args) >= 2 and args[0] == "-C":
        git_cwd = args[1]
        args = args[2:]
    return (args, git_cwd) if args else None


def _git_verb(args: list[str]) -> str | None:
    for value in args:
        if not value.startswith("-"):
            return value
    return None


def _verification_kind(argv: list[str]) -> str | None:
    executable = Path(argv[0]).name.casefold()
    if executable in {"pytest", "jest", "vitest", "tox", "nox"}:
        return "test"
    if executable in {"ruff", "mypy"}:
        return "check"
    if executable == "go" and len(argv) > 1 and argv[1] == "test":
        return "test"
    if executable == "cargo" and len(argv) > 1:
        return "test" if argv[1] == "test" else "check" if argv[1] in {"check", "clippy"} else None
    if executable in {"npm", "pnpm", "yarn"} and len(argv) > 1:
        if argv[1] == "test":
            return "test"
        if argv[1] == "run" and len(argv) > 2:
            return "test" if "test" in argv[2].casefold() else "check" if any(
                word in argv[2].casefold() for word in ("check", "lint", "type")
            ) else None
    if executable in {"make", "gradle", "mvn"} and any(
        word in argument.casefold() for argument in argv[1:]
        for word in ("test", "check", "lint", "verify")
    ):
        return "test" if any("test" in argument.casefold() for argument in argv[1:]) else "check"
    if executable.startswith("python") and len(argv) > 2 and argv[1:3] == ["-m", "pytest"]:
        return "test"
    return None


def _result_for(events: list[dict[str, Any]], index: int) -> dict[str, Any] | None:
    for candidate in events[index + 1:index + 4]:
        if candidate.get("surface") == "tool_input":
            return None
        if candidate.get("surface") == "tool_output":
            payload = _parse_payload(str(candidate.get("text", "")))
            exit_code = payload.get("exit_code") if payload else None
            return {
                "event_id": candidate.get("event_id"),
                "ordinal": candidate.get("ordinal"),
                "status": (
                    "passed" if exit_code == 0 else "failed" if isinstance(exit_code, int) else "unknown"
                ),
                "pairing": "order_inferred",
            }
    return None


def observed_git_provenance(
    events: list[dict[str, Any]],
    metadata: dict[str, Any],
    *,
    owned_first: int | None = None,
    owned_last: int | None = None,
) -> dict[str, Any]:
    default_cwd = _absolute(str(metadata.get("cwd", "")), None)
    repositories = []
    mutations = []
    git_commands = []
    commits = []
    branch_switches = []
    tests = []
    limitations = []
    candidate_paths: list[dict[str, Any]] = []
    if default_cwd:
        candidate_paths.append({"path": str(default_cwd), "source": "session_metadata", "event_id": None})

    for index, event in enumerate(events):
        if event.get("surface") != "tool_input":
            continue
        ordinal = event.get("ordinal")
        if owned_first is not None and (not isinstance(ordinal, int) or ordinal < owned_first):
            continue
        if owned_last is not None and (not isinstance(ordinal, int) or ordinal > owned_last):
            continue
        text = str(event.get("text", ""))
        payload = _parse_payload(text)
        names = _tool_names(event)
        result = _result_for(events, index)
        if event.get("possibly_truncated"):
            limitations.append({
                "event_id": event.get("event_id"), "ordinal": event.get("ordinal"),
                "reason": "tool_input_possibly_truncated",
            })

        payloads = [payload] if payload else []
        patch_texts = []
        if names & MUTATION_TOOLS:
            patch_texts.append(
                payload.get("patch") if payload and isinstance(payload.get("patch"), str) else text
            )
        if names & ORCHESTRATOR_TOOLS:
            payloads.extend(_orchestrator_exec_payloads(text))
            patch_texts.extend(_orchestrator_patch_texts(text))

        primary_cwd = _workdir(payload, default_cwd)
        if primary_cwd and _has_explicit_workdir(payload):
            candidate_paths.append({
                "path": str(primary_cwd), "source": "tool_workdir",
                "event_id": event.get("event_id"),
            })
        for patch_text in patch_texts:
            paths = _patch_paths(patch_text)
            paths.extend(_path_values(payload))
            for value in paths:
                path = _absolute(value, primary_cwd)
                if path is None:
                    continue
                operation = next((
                    kind.casefold() for kind in ("Add", "Update", "Delete")
                    if f"*** {kind} File: {value}" in patch_text
                ), "mutation")
                mutations.append({
                    "path": str(path),
                    "operation": operation,
                    "event_id": event.get("event_id"),
                    "ordinal": event.get("ordinal"),
                    "source": "session_tool_input",
                    "confidence": "direct",
                    "result": result,
                })
                candidate_paths.append({
                    "path": str(path.parent), "source": "observed_mutation",
                    "event_id": event.get("event_id"),
                })

        if names and not (names & SHELL_TOOLS or names & ORCHESTRATOR_TOOLS):
            payloads = []
        for command_payload in payloads:
            command = _command(command_payload)
            if not command:
                continue
            cwd = _workdir(command_payload, primary_cwd)
            if cwd and _has_explicit_workdir(command_payload):
                candidate_paths.append({
                    "path": str(cwd), "source": "tool_workdir",
                    "event_id": event.get("event_id"),
                })
            for argv in _command_segments(command):
                parsed_git = _git_argv(argv)
                if parsed_git:
                    args, git_cwd = parsed_git
                    verb = _git_verb(args)
                    command_cwd = _absolute(git_cwd, cwd) if git_cwd else cwd
                    record = {
                        "argv": ["git", *args],
                        "workdir": str(command_cwd) if command_cwd else None,
                        "verb": verb,
                        "mutating": verb in GIT_MUTATING_VERBS,
                        "event_id": event.get("event_id"),
                        "ordinal": event.get("ordinal"),
                        "source": "session_tool_input",
                        "confidence": "direct",
                        "result": result,
                    }
                    git_commands.append(record)
                    if command_cwd:
                        candidate_paths.append({
                            "path": str(command_cwd), "source": "git_command",
                            "event_id": event.get("event_id"),
                        })
                    if verb in {"switch", "checkout"}:
                        try:
                            verb_index = args.index(verb)
                        except ValueError:
                            verb_index = -1
                        target = next((
                            value for value in reversed(args[verb_index + 1:])
                            if not value.startswith("-")
                        ), None)
                        branch_switches.append({**record, "target": target})
                    if verb in {"commit", "rev-parse", "log", "show"} and result:
                        output_event = next(
                            (candidate for candidate in events if candidate.get("event_id") == result["event_id"]),
                            None,
                        )
                        output = str(output_event.get("text", "")) if output_event else ""
                        output_payload = _parse_payload(output)
                        if output_payload and isinstance(output_payload.get("output"), str):
                            output = output_payload["output"]
                        for pattern in RESULT_SHA_PATTERNS:
                            for sha in pattern.findall(output):
                                commits.append({
                                    "sha": sha,
                                    "event_id": result["event_id"],
                                    "ordinal": result["ordinal"],
                                    "source": "session_tool_output",
                                    "confidence": "reported_by_tool_output",
                                    "command_event_id": event.get("event_id"),
                                })
                verification_kind = _verification_kind(argv)
                if verification_kind:
                    tests.append({
                        "kind": verification_kind,
                        "argv": argv,
                        "workdir": str(cwd) if cwd else None,
                        "event_id": event.get("event_id"),
                        "ordinal": event.get("ordinal"),
                        "source": "session_tool_input",
                        "confidence": "direct",
                        "result": result,
                    })

    repositories_by_root: dict[str, dict[str, Any]] = {}
    unavailable_by_path: dict[tuple[str, str | None], dict[str, Any]] = {}
    candidates_by_path: dict[str, dict[str, Any]] = {}
    for candidate in candidate_paths:
        grouped = candidates_by_path.setdefault(candidate["path"], {
            "path": candidate["path"], "sources": [], "event_ids": [],
        })
        if candidate["source"] not in grouped["sources"]:
            grouped["sources"].append(candidate["source"])
        if candidate["event_id"] and candidate["event_id"] not in grouped["event_ids"]:
            grouped["event_ids"].append(candidate["event_id"])
    for candidate in candidates_by_path.values():
        root, reason = resolve_repo(Path(candidate["path"]))
        if root is None:
            key = (candidate["path"], reason)
            if key not in unavailable_by_path:
                unavailable_by_path[key] = {
                    "path": candidate["path"], "reason": reason,
                    "sources": [], "event_ids": [],
                }
            unavailable = unavailable_by_path[key]
            unavailable["sources"].extend(
                value for value in candidate["sources"] if value not in unavailable["sources"]
            )
            unavailable["event_ids"].extend(
                value for value in candidate["event_ids"] if value not in unavailable["event_ids"]
            )
            continue
        value = str(root)
        if value not in repositories_by_root:
            repositories_by_root[value] = {
                "repo_root": value,
                "sources": [],
                "event_ids": [],
                "confidence": "direct" if candidate["event_ids"] else "metadata",
            }
            repositories.append(repositories_by_root[value])
        repository = repositories_by_root[value]
        repository["sources"].extend(
            source for source in candidate["sources"] if source not in repository["sources"]
        )
        repository["event_ids"].extend(
            event_id for event_id in candidate["event_ids"] if event_id not in repository["event_ids"]
        )
        if repository["event_ids"]:
            repository["confidence"] = "direct"
    return {
        "repositories": repositories,
        "unavailable_repository_candidates": list(unavailable_by_path.values()),
        "file_mutations": mutations,
        "git_commands": git_commands,
        "observed_commits": list({(item["sha"], item["event_id"]): item for item in commits}.values()),
        "branch_switches": branch_switches,
        "test_commands": tests,
        "limitations": limitations,
    }


def _parse_status(output: str) -> list[dict[str, Any]]:
    records = output.split("\0")
    entries = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        prefix = record[:1]
        if prefix == "1":
            fields = record.split(" ", 8)
            if len(fields) == 9:
                entries.append({"kind": "ordinary", "xy": fields[1], "path": fields[8]})
        elif prefix == "2":
            fields = record.split(" ", 9)
            original = records[index] if index < len(records) else None
            index += 1 if original is not None else 0
            if len(fields) == 10:
                entries.append({
                    "kind": "rename_or_copy", "xy": fields[1], "score": fields[8],
                    "path": fields[9], "original_path": original,
                })
        elif prefix == "u":
            fields = record.split(" ", 10)
            if len(fields) == 11:
                entries.append({"kind": "unmerged", "xy": fields[1], "path": fields[10]})
        elif prefix in {"?", "!"}:
            entries.append({"kind": "untracked" if prefix == "?" else "ignored", "path": record[2:]})
    return entries


def _separated_lines(output: str, fields: int) -> list[list[str]]:
    values = []
    for line in output.splitlines():
        parts = line.split("\x1f", fields - 1)
        if len(parts) == fields:
            values.append(parts)
    return values


def verified_repository_snapshot(repo: Path) -> dict[str, Any]:
    root_result = run_git_probe(repo, "root")
    root_text = _text(root_result)
    if not root_text:
        return {
            "available": False,
            "candidate": str(repo),
            "reason": "not an accessible git worktree",
            "probe": {key: root_result[key] for key in ("code", "timed_out", "truncated", "error")},
        }
    root = Path(root_text).resolve(strict=False)
    results = {name: run_git_probe(root, name) for name in PROBE_ARGUMENTS if name != "root"}
    head = _text(results["head"])
    branch = _text(results["branch"])
    upstream = _text(results["upstream"])
    merge_base = _text(results["merge_base"]) if upstream else None
    status_text = _text(results["status"])
    entries = _parse_status(status_text or "") if status_text is not None else []
    refs = [
        {"ref": ref, "sha": sha, "upstream": ref_upstream or None}
        for ref, sha, ref_upstream in _separated_lines(_text(results["refs"]) or "", 3)
    ]
    commits = [
        {"sha": sha, "parents": parents.split() if parents else [], "authored_at": authored,
         "committed_at": committed, "subject": subject}
        for sha, parents, authored, committed, subject
        in _separated_lines(_text(results["commits"]) or "", 5)
    ]
    reflog = []
    for sha, selector, subject in _separated_lines(_text(results["reflog"]) or "", 3):
        date_match = re.search(r"@\{(.+)\}$", selector)
        reflog.append({
            "sha": sha, "selector": selector,
            "at": date_match.group(1) if date_match else None,
            "subject": subject,
        })
    worktrees = []
    current: dict[str, Any] = {}
    for line in (_text(results["worktrees"]) or "").splitlines() + [""]:
        if not line:
            if current:
                worktrees.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value if value else True
    limitations = [
        {"probe": name, "code": result["code"], "timed_out": result["timed_out"],
         "truncated": result["truncated"]}
        for name, result in results.items()
        if result["code"] != 0 and name not in {"branch", "upstream", "merge_base", "reflog"}
    ]
    if results["reflog"]["code"] != 0:
        limitations.append({"probe": "reflog", "reason": "unavailable_or_expired"})
    elif not reflog:
        limitations.append({"probe": "reflog", "reason": "empty_or_expired"})
    return {
        "available": True,
        "repo_root": str(root),
        "git_dir": _text(results["git_dir"]),
        "common_dir": _text(results["common_dir"]),
        "head": head if head and SHA_RE.fullmatch(head) else None,
        "branch": branch,
        "upstream": upstream,
        "merge_base": merge_base if merge_base and SHA_RE.fullmatch(merge_base) else None,
        "status": entries,
        "changed_paths": sorted({
            value for entry in entries for value in (entry.get("path"), entry.get("original_path")) if value
        }),
        "refs": refs,
        "commits": commits,
        "reflog": reflog,
        "worktrees": worktrees,
        "limitations": limitations,
        "attribution": "verified_now_only",
    }


def _merge_observed(parts: Any) -> dict[str, Any]:
    merged = {
        "repositories": [], "unavailable_repository_candidates": [], "file_mutations": [],
        "git_commands": [], "observed_commits": [], "branch_switches": [],
        "test_commands": [], "limitations": [],
    }
    repositories: dict[str, dict[str, Any]] = {}
    unavailable: dict[tuple[Any, Any], dict[str, Any]] = {}
    detail_keys = (
        "file_mutations", "git_commands", "observed_commits", "branch_switches",
        "test_commands", "limitations",
    )
    unique = {key: {} for key in detail_keys}
    observations_seen = {key: 0 for key in detail_keys}
    omitted_repositories = 0
    omitted_unavailable = 0
    indexed = 0
    for part in parts:
        for item in part.get("repositories", []):
            root = item["repo_root"]
            if root not in repositories and len(repositories) >= MAX_REPOSITORY_CANDIDATES:
                omitted_repositories += 1
                continue
            target = repositories.setdefault(root, {
                "repo_root": root, "sources": [], "event_ids": [], "confidence": "metadata",
            })
            target["sources"].extend(
                value for value in item.get("sources", []) if value not in target["sources"]
            )
            for value in item.get("event_ids", []):
                if value in target["event_ids"]:
                    continue
                if len(target["event_ids"]) < MAX_REPOSITORY_EVENT_IDS:
                    target["event_ids"].append(value)
                else:
                    target["omitted_event_ids"] = target.get("omitted_event_ids", 0) + 1
            if target["event_ids"]:
                target["confidence"] = "direct"
        for item in part.get("unavailable_repository_candidates", []):
            key = (item.get("path"), item.get("reason"))
            if key not in unavailable and len(unavailable) >= MAX_REPOSITORY_CANDIDATES:
                omitted_unavailable += 1
                continue
            target = unavailable.setdefault(key, {
                "path": item.get("path"), "reason": item.get("reason"),
                "sources": [], "event_ids": [],
            })
            target["sources"].extend(
                value for value in item.get("sources", []) if value not in target["sources"]
            )
            for value in item.get("event_ids", []):
                if value in target["event_ids"]:
                    continue
                if len(target["event_ids"]) < MAX_REPOSITORY_EVENT_IDS:
                    target["event_ids"].append(value)
                else:
                    target["omitted_event_ids"] = target.get("omitted_event_ids", 0) + 1
        for key in detail_keys:
            for item in part.get(key, []):
                observations_seen[key] += 1
                signature = json.dumps(item, sort_keys=True, separators=(",", ":"))
                if signature in unique[key]:
                    continue
                if indexed < MAX_INDEXED_OBSERVATIONS:
                    unique[key][signature] = item
                    indexed += 1
    merged["repositories"] = list(repositories.values())
    merged["unavailable_repository_candidates"] = list(unavailable.values())
    for key in detail_keys:
        merged[key] = list(unique[key].values())
    merged["index_limits"] = {
        "max_indexed_observations": MAX_INDEXED_OBSERVATIONS,
        "max_repository_candidates_per_class": MAX_REPOSITORY_CANDIDATES,
        "indexed_observations": indexed,
        "observations_seen": observations_seen,
        "omitted_observations": {
            key: observations_seen[key] - len(unique[key]) for key in detail_keys
        },
        "omitted_repository_candidates": omitted_repositories,
        "omitted_unavailable_repository_candidates": omitted_unavailable,
        "full_evidence_preserved_in_event_ledger": True,
    }
    return merged


def collect_git_provenance_chunks(
    chunks: Any,
    metadata: dict[str, Any],
    explicit_repositories: list[str] | None = None,
) -> dict[str, Any]:
    def parts():
        for chunk in chunks:
            if isinstance(chunk, dict) and isinstance(chunk.get("events"), list):
                yield observed_git_provenance(
                    chunk["events"], metadata,
                    owned_first=chunk.get("owned_first"), owned_last=chunk.get("owned_last"),
                )
            else:
                yield observed_git_provenance(chunk, metadata)

    observed = _merge_observed(parts())
    candidates = [item["repo_root"] for item in observed["repositories"]]
    candidates.extend(explicit_repositories or [])
    snapshots = []
    seen = set()
    for value in candidates:
        path = Path(value).expanduser().resolve(strict=False)
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        snapshots.append(verified_repository_snapshot(path))
    return {
        "schema_version": "recap.git-provenance.v1",
        "session_observed": observed,
        "session_end": {
            "state": "unknown_unless_explicitly_observed",
            "reason": "current git state is not a historical session-end snapshot",
        },
        "verified_now": {
            "repositories": snapshots,
        },
    }


def collect_git_provenance(
    events: list[dict[str, Any]],
    metadata: dict[str, Any],
    explicit_repositories: list[str] | None = None,
) -> dict[str, Any]:
    return collect_git_provenance_chunks([events], metadata, explicit_repositories)


def git_referenced_event_ids(value: Any) -> set[str]:
    found = set()
    if not isinstance(value, dict) or not isinstance(value.get("session_observed"), dict):
        return found
    observed = value["session_observed"]
    for key in ("file_mutations", "git_commands", "observed_commits", "branch_switches", "test_commands"):
        for item in observed.get(key, []) if isinstance(observed.get(key), list) else []:
            if not isinstance(item, dict):
                continue
            for field in ("event_id", "command_event_id"):
                if isinstance(item.get(field), str):
                    found.add(item[field])
            if isinstance(item.get("result"), dict) and isinstance(item["result"].get("event_id"), str):
                found.add(item["result"]["event_id"])
    for key in ("repositories", "unavailable_repository_candidates"):
        for item in observed.get(key, []) if isinstance(observed.get(key), list) else []:
            if isinstance(item, dict):
                found.update(value for value in item.get("event_ids", []) if isinstance(value, str))
    for item in observed.get("limitations", []) if isinstance(observed.get("limitations"), list) else []:
        if isinstance(item, dict) and isinstance(item.get("event_id"), str):
            found.add(item["event_id"])
    return found


def validate_git_provenance(value: Any, event_ids: set[str] | None = None) -> list[str]:
    errors = []
    if not isinstance(value, dict) or value.get("schema_version") != "recap.git-provenance.v1":
        return ["git provenance schema is unsupported"]
    observed = value.get("session_observed")
    if not isinstance(observed, dict):
        return ["git session_observed is missing"]
    for key in (
        "repositories", "unavailable_repository_candidates", "file_mutations", "git_commands",
        "observed_commits", "branch_switches", "test_commands", "limitations",
    ):
        if not isinstance(observed.get(key), list):
            errors.append(f"git session_observed.{key} must be a list")
    for key in ("file_mutations", "git_commands", "observed_commits", "branch_switches", "test_commands"):
        for item in observed.get(key, []) if isinstance(observed.get(key), list) else []:
            if not isinstance(item, dict) or (
                event_ids is not None and item.get("event_id") not in event_ids
            ):
                errors.append(f"git {key} references unknown event")
                continue
            result = item.get("result")
            if result is not None and (
                not isinstance(result, dict)
                or (event_ids is not None and result.get("event_id") not in event_ids)
                or result.get("pairing") != "order_inferred"
            ):
                errors.append(f"git {key} has invalid result evidence")
            if event_ids is not None and item.get("command_event_id") is not None and item["command_event_id"] not in event_ids:
                errors.append(f"git {key} references unknown command event")
    for key in ("repositories", "unavailable_repository_candidates"):
        for item in observed.get(key, []) if isinstance(observed.get(key), list) else []:
            if not isinstance(item, dict) or (
                event_ids is not None and any(
                    event_id not in event_ids for event_id in item.get("event_ids", [])
                )
            ):
                errors.append(f"git {key} references unknown event")
    for item in observed.get("limitations", []) if isinstance(observed.get("limitations"), list) else []:
        if not isinstance(item, dict) or (
            event_ids is not None and item.get("event_id") not in event_ids
        ):
            errors.append("git limitations reference unknown event")
    session_end = value.get("session_end")
    if not isinstance(session_end, dict) or session_end.get("state") not in {
        "unknown_unless_explicitly_observed", "observed",
    }:
        errors.append("git session_end state is invalid")
    verified = value.get("verified_now")
    if not isinstance(verified, dict) or not isinstance(verified.get("repositories"), list):
        errors.append("git verified_now repositories are missing")
    else:
        for repository in verified["repositories"]:
            if not isinstance(repository, dict) or not isinstance(repository.get("available"), bool):
                errors.append("git verified_now repository is invalid")
            elif repository["available"] and repository.get("attribution") != "verified_now_only":
                errors.append("git verified_now repository lost its attribution boundary")
    return errors
