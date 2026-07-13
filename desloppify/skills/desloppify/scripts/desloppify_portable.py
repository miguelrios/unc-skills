#!/usr/bin/env python3
"""Credential-blind doctor and argv-safe launcher for official Desloppify."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Literal, Sequence, TypedDict, cast


COMPANION_VERSION = "0.1.0"
MINIMUM_UPSTREAM_VERSION = "1.0"
SUPPORTED_UPSTREAM_MAJOR = 1
SUPPORTED_UPSTREAM_SPEC = ">=1,<2"

HarnessName = Literal[
    "codex",
    "claude-code",
    "pi",
    "hermes",
    "gemini",
    "opencode",
    "rovodev",
    "generic",
]
ProbeFailure = Literal["execution_error", "timeout"]


class HarnessSpec(TypedDict):
    runner: str | None
    review_route: str
    signals: tuple[str, ...]
    fallback_signals: tuple[str, ...]


class UpstreamStatus(TypedDict):
    installed: bool
    executable: str | None
    version: str | None
    version_source: str
    probe_status: str
    supported_spec: str
    compatible: bool | None


class ProjectStatus(TypedDict):
    requested_path: str
    git_root: str | None
    git_root_status: str
    ignore_probe_status: str
    tracked_probe_status: str
    desloppify_ignored: bool | None
    desloppify_tracked_files: list[str] | None
    scope_check: str


class DoctorReport(TypedDict):
    schema_version: int
    ready: bool
    companion: dict[str, str]
    python: dict[str, str | bool]
    upstream: dict[str, str | bool | None]
    harness: dict[str, str]
    runners: dict[str, bool]
    project: ProjectStatus
    notes: list[str]


HARNESS_SPECS: dict[HarnessName, HarnessSpec] = {
    "codex": {
        "runner": "codex",
        "review_route": "native-batch:codex",
        "signals": ("CODEX_THREAD_ID",),
        # CODEX_HOME is often persistent config while another harness is active.
        "fallback_signals": ("CODEX_HOME",),
    },
    "claude-code": {
        "runner": "claude",
        "review_route": "native-subagents:prepared-packet",
        "signals": ("CLAUDE_CODE_SESSION_ID", "CLAUDECODE"),
        "fallback_signals": (),
    },
    "pi": {
        "runner": "pi",
        "review_route": "prepared-packet:manual-import",
        "signals": ("PI_CODING_AGENT_DIR",),
        "fallback_signals": (),
    },
    "hermes": {
        "runner": "hermes",
        "review_route": "native-delegate-task:prepared-batches",
        "signals": ("HERMES_HOME",),
        "fallback_signals": (),
    },
    "gemini": {
        "runner": "gemini",
        "review_route": "sequential-subagents:prepared-packet",
        "signals": ("GEMINI_CLI_HOME",),
        "fallback_signals": (),
    },
    "opencode": {
        "runner": "opencode",
        "review_route": "native-batch:opencode",
        "signals": ("OPENCODE_CONFIG",),
        "fallback_signals": (),
    },
    "rovodev": {
        "runner": "acli",
        "review_route": "native-batch:rovodev",
        "signals": ("ROVODEV_HOME",),
        "fallback_signals": (),
    },
    "generic": {
        "runner": None,
        "review_route": "prepared-packet:manual-import",
        "signals": (),
        "fallback_signals": (),
    },
}
HARNESSES = ("auto", *HARNESS_SPECS.keys())
_VERSION_RE = re.compile(r"\b(\d+\.\d+(?:\.\d+)?(?:[A-Za-z0-9.+-]*)?)\b")
_COMMAND_VERSION_RE = re.compile(
    r"^desloppify\s+(\d+\.\d+(?:\.\d+)?(?:[A-Za-z0-9.+-]*)?)$", re.IGNORECASE
)
_STABLE_RELEASE_RE = re.compile(r"^(?:desloppify\s+)?(\d+\.\d+(?:\.\d+)?)$", re.IGNORECASE)


def parse_version(value: str | None) -> tuple[int, ...] | None:
    """Return the numeric release prefix used for compatibility comparison."""
    if not value:
        return None
    match = _VERSION_RE.search(value)
    if not match:
        return None
    numeric = re.match(r"\d+(?:\.\d+)*", match.group(1))
    if numeric is None:
        return None
    return tuple(int(part) for part in numeric.group(0).split("."))


def version_at_least(value: str | None, floor: str) -> bool | None:
    actual = parse_version(value)
    required = parse_version(floor)
    if actual is None or required is None:
        return None
    width = max(len(actual), len(required))
    return actual + (0,) * (width - len(actual)) >= required + (0,) * (width - len(required))


def version_supported(value: str | None) -> bool | None:
    if value is None:
        return None
    match = _STABLE_RELEASE_RE.fullmatch(value.strip())
    if match is None:
        return None
    actual = tuple(int(part) for part in match.group(1).split("."))
    minimum_ok = version_at_least(match.group(1), MINIMUM_UPSTREAM_VERSION)
    if minimum_ok is None:
        return None
    return minimum_ok and actual[0] == SUPPORTED_UPSTREAM_MAJOR


def detect_harness(environ: dict[str, str], explicit: str = "auto") -> HarnessName:
    """Detect only from allowlisted presence signals; never inspect values."""
    if explicit != "auto":
        if explicit not in HARNESS_SPECS:
            accepted = ", ".join(HARNESSES)
            raise ValueError(f"unsupported harness {explicit!r}; expected one of: {accepted}")
        return cast(HarnessName, explicit)
    for signal_kind in ("signals", "fallback_signals"):
        for harness, spec in HARNESS_SPECS.items():
            if any(variable in environ for variable in spec[signal_kind]):
                return harness
    return "generic"


def _run_capture(
    argv: Sequence[str], *, cwd: Path | None = None
) -> tuple[subprocess.CompletedProcess[str] | None, ProbeFailure | None]:
    try:
        result = subprocess.run(
            list(argv),
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=8,
            check=False,
        )
        return result, None
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError:
        return None, "execution_error"


def _package_version() -> str | None:
    try:
        return importlib.metadata.version("desloppify")
    except importlib.metadata.PackageNotFoundError:
        return None


def _probe_executable_version(executable: str) -> tuple[str | None, str, str]:
    result, failure = _run_capture([executable, "--version"])
    if failure:
        return None, "unknown", failure
    if result is None or result.returncode != 0:
        return None, "unknown", "nonzero_exit"
    first_line = (result.stdout or result.stderr).strip().splitlines()[0:1]
    match = _COMMAND_VERSION_RE.fullmatch(first_line[0] if first_line else "")
    if match is None:
        return None, "unknown", "unparseable"
    return match.group(1), "command", "ok"


def upstream_status() -> UpstreamStatus:
    executable = shutil.which("desloppify")
    if executable:
        version, version_source, probe_status = _probe_executable_version(executable)
    else:
        version = _package_version()
        version_source = "package-metadata-only" if version else "unknown"
        probe_status = "not_installed"

    return {
        "installed": executable is not None,
        "executable": executable,
        "version": version,
        "version_source": version_source,
        "probe_status": probe_status,
        "supported_spec": SUPPORTED_UPSTREAM_SPEC,
        "compatible": version_supported(version) if executable else False,
    }


def _git_root(project: Path) -> tuple[Path | None, str]:
    git = shutil.which("git")
    if git is None:
        return None, "git_missing"
    result, failure = _run_capture([git, "-C", str(project), "rev-parse", "--show-toplevel"])
    if failure:
        return None, f"git_root_{failure}"
    if result is None or result.returncode != 0:
        return None, "not_git_repository"
    return Path(result.stdout.strip()).resolve(), "ok"


def project_status(project: Path) -> ProjectStatus:
    project = project.resolve()
    root, root_status = _git_root(project)
    ignore_status = "not_run"
    tracked_status = "not_run"
    ignored: bool | None = None
    tracked: list[str] | None = None
    if root is not None:
        git = shutil.which("git")
        if git is None:
            return {
                "requested_path": str(project),
                "git_root": str(root),
                "git_root_status": root_status,
                "ignore_probe_status": "git_missing",
                "tracked_probe_status": "git_missing",
                "desloppify_ignored": None,
                "desloppify_tracked_files": None,
                "scope_check": "agent-review-required",
            }
        try:
            state_target = str((project / ".desloppify").relative_to(root)) + "/"
        except ValueError:
            state_target = str(project / ".desloppify") + "/"

        ignored_result, ignored_failure = _run_capture(
            [git, "-C", str(root), "check-ignore", "-q", "--", state_target]
        )
        if ignored_failure:
            ignore_status = ignored_failure
        elif ignored_result is not None and ignored_result.returncode in (0, 1):
            ignored = ignored_result.returncode == 0
            ignore_status = "ok"
        else:
            ignore_status = "nonzero_exit"

        tracked_result, tracked_failure = _run_capture(
            [git, "-C", str(root), "ls-files", "--", state_target]
        )
        if tracked_failure:
            tracked_status = tracked_failure
        elif tracked_result is not None and tracked_result.returncode == 0:
            tracked = [line for line in tracked_result.stdout.splitlines() if line]
            tracked_status = "ok"
        else:
            tracked_status = "nonzero_exit"

    return {
        "requested_path": str(project),
        "git_root": str(root) if root else None,
        "git_root_status": root_status,
        "ignore_probe_status": ignore_status,
        "tracked_probe_status": tracked_status,
        "desloppify_ignored": ignored,
        "desloppify_tracked_files": tracked,
        "scope_check": "agent-review-required",
    }


def runner_status() -> dict[str, bool]:
    return {
        name: shutil.which(spec["runner"]) is not None
        for name, spec in HARNESS_SPECS.items()
        if spec["runner"] is not None
    }


def build_report(
    project: Path, harness: str, environ: dict[str, str] | None = None
) -> DoctorReport:
    environ = dict(os.environ) if environ is None else environ
    selected = detect_harness(environ, harness)
    upstream = upstream_status()
    repository = project_status(project)
    python_ok = sys.version_info >= (3, 11)
    ready = bool(
        python_ok
        and upstream["installed"]
        and upstream["compatible"] is True
        and repository["desloppify_ignored"] is True
        and repository["desloppify_tracked_files"] == []
    )
    return {
        "schema_version": 1,
        "ready": ready,
        "companion": {
            "version": COMPANION_VERSION,
            "update": "Update through the same unc-skills, plugin, or skills.sh installer.",
        },
        "python": {
            "version": ".".join(str(part) for part in sys.version_info[:3]),
            "minimum": "3.11",
            "compatible": python_ok,
        },
        "upstream": {
            **upstream,
            "author": "Peter O'Malley",
            "license": "OSNL-0.2",
            "update": "Run `uv tool upgrade 'desloppify<2'` explicitly; scans never auto-update.",
        },
        "harness": {
            "detected": selected,
            "review_route": HARNESS_SPECS[selected]["review_route"],
        },
        "runners": runner_status(),
        "project": repository,
        "notes": [
            "No network request, install, model invocation, or shared instruction-file write was performed.",
            "Review the project scope manually before scanning a monorepo.",
        ],
    }


def _state_check(report: DoctorReport) -> tuple[bool, str]:
    ignored = report["project"]["desloppify_ignored"]
    if ignored is True:
        return True, ".desloppify/ is ignored"
    if ignored is False:
        return False, ".desloppify/ is not ignored"
    return False, ".desloppify/ ignore state could not be verified"


def _tracked_check(report: DoctorReport) -> tuple[bool, str]:
    tracked = report["project"]["desloppify_tracked_files"]
    if tracked == []:
        return True, "no tracked .desloppify files"
    if tracked is None:
        return False, "tracked .desloppify files could not be verified"
    return False, f"{len(tracked)} tracked .desloppify file(s)"


def render_report(report: DoctorReport) -> str:
    upstream = report["upstream"]
    if not upstream["installed"]:
        engine_check = (False, "Desloppify is not installed")
    elif upstream["compatible"] is True:
        engine_check = (True, f"Desloppify {upstream['version']}")
    else:
        version = upstream["version"] or "version unknown"
        engine_check = (False, f"Desloppify {version} is unverified ({upstream['probe_status']})")

    checks = (
        (bool(report["python"]["compatible"]), f"Python {report['python']['version']}"),
        engine_check,
        _state_check(report),
        _tracked_check(report),
    )
    lines = [f"Desloppify portable doctor — {'ready' if report['ready'] else 'action needed'}"]
    for ok, detail in checks:
        lines.append(f"{'ok' if ok else '!!'}  {detail}")
    lines.extend(
        (
            f"harness  {report['harness']['detected']}",
            f"review   {report['harness']['review_route']}",
            "updates  companion and official engine are explicit and independent",
        )
    )
    if not upstream["installed"]:
        lines.append("next     uv tool install --upgrade 'desloppify[full]>=1,<2'")
    elif upstream["compatible"] is not True:
        lines.append("next     verify an official Desloppify >=1,<2 installation")
    ignored = report["project"]["desloppify_ignored"]
    if ignored is False:
        lines.append("next     add .desloppify/ to the applicable .gitignore")
    elif ignored is None:
        lines.append("next     verify the project is a Git worktree and rerun doctor")
    return "\n".join(lines)


def _doctor(args: argparse.Namespace) -> int:
    report = build_report(args.project, args.harness)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_report(report))
    return 0 if report["ready"] else 1


def _exec_upstream(args: argparse.Namespace) -> int:
    executable = shutil.which("desloppify")
    if executable is None:
        print(
            "desloppify is not installed; run: uv tool install --upgrade 'desloppify[full]>=1,<2'",
            file=sys.stderr,
        )
        return 127
    upstream_args = list(args.upstream_args)
    if upstream_args and upstream_args[0] == "--":
        upstream_args.pop(0)
    if not upstream_args:
        print("run requires arguments after `--`", file=sys.stderr)
        return 2
    try:
        os.execvpe(executable, [executable, *upstream_args], dict(os.environ))
    except OSError:
        print("could not launch the installed desloppify executable", file=sys.stderr)
        return 126


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {COMPANION_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check local readiness without mutation or network access")
    doctor.add_argument("--project", type=Path, default=Path.cwd())
    doctor.add_argument("--harness", choices=HARNESSES, default="auto")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=_doctor)

    run = subparsers.add_parser("run", help="Exec the installed official CLI with argv fidelity")
    run.add_argument("upstream_args", nargs=argparse.REMAINDER)
    run.set_defaults(func=_exec_upstream)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
