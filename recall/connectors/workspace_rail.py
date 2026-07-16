"""Closed, read-only subprocess boundary for the pinned Google Workspace CLI."""

from __future__ import annotations

import json
import os
import selectors
import signal
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


GWS_EXECUTABLE = "/opt/recall/vendor/gws/0.22.5/gws"
MAX_CREDENTIAL_BYTES = 1_000_000
DEFAULT_MAX_OUTPUT_BYTES = 8_000_000
MAX_EXPORT_BYTES = 10_000_000


@dataclass(frozen=True)
class GwsRelease:
    version: str
    tag_commit: str
    sha256: Mapping[str, str]
    bytes: Mapping[str, int]


GWS_RELEASE = GwsRelease(
    version="0.22.5",
    tag_commit="705fb0ecac6f4249679958f6325b809b63fdde17",
    sha256={
        "aarch64-apple-darwin": "1d2a9ffd5bc9b2c2c4b48630daf082fad13d9e57d741988a2c248eed562f7dac",
        "aarch64-unknown-linux-gnu": "94490295d9580e1e88574e715a0a162991747d12d62f8c7b8dcc8268b6c1cea0",
        "x86_64-unknown-linux-gnu": "de78ecdbd2f1a84cca0063a7ecbc440240fc14b6ebccbb17f4646b792a8c5c1f",
    },
    bytes={
        "aarch64-apple-darwin": 6_119_841,
        "aarch64-unknown-linux-gnu": 6_615_778,
        "x86_64-unknown-linux-gnu": 6_765_622,
    },
)


@dataclass(frozen=True)
class Operation:
    argv: tuple[str, ...]
    params: frozenset[str]
    response_fields: frozenset[str]


OPERATIONS = {
    "gmail.messages.list": Operation(
        ("gmail", "users", "messages", "list"),
        frozenset({"userId", "maxResults", "pageToken", "q", "labelIds", "includeSpamTrash"}),
        frozenset({"messages", "nextPageToken", "resultSizeEstimate"}),
    ),
    "gmail.messages.get": Operation(
        ("gmail", "users", "messages", "get"),
        frozenset({"userId", "id", "format", "metadataHeaders"}),
        frozenset({"id", "threadId", "labelIds", "snippet", "historyId", "internalDate", "payload", "sizeEstimate", "raw"}),
    ),
    "gmail.history.list": Operation(
        ("gmail", "users", "history", "list"),
        frozenset({"userId", "startHistoryId", "maxResults", "pageToken", "historyTypes", "labelId"}),
        frozenset({"history", "nextPageToken", "historyId"}),
    ),
    "calendar.calendarList.list": Operation(
        ("calendar", "calendarList", "list"),
        frozenset({"maxResults", "minAccessRole", "pageToken", "showDeleted", "showHidden", "syncToken"}),
        frozenset({"kind", "etag", "nextPageToken", "nextSyncToken", "items"}),
    ),
    "calendar.events.list": Operation(
        ("calendar", "events", "list"),
        frozenset({
            "calendarId", "maxResults", "pageToken", "showDeleted", "singleEvents",
            "syncToken", "timeMin", "timeMax", "timeZone", "updatedMin",
        }),
        frozenset({
            "kind", "etag", "summary", "description", "updated", "timeZone", "accessRole",
            "defaultReminders", "nextPageToken", "nextSyncToken", "items",
        }),
    ),
    "calendar.events.get": Operation(
        ("calendar", "events", "get"), frozenset({"calendarId", "eventId", "timeZone"}),
        frozenset({
            "kind", "etag", "id", "status", "htmlLink", "created", "updated", "summary",
            "description", "location", "creator", "organizer", "start", "end", "endTimeUnspecified",
            "recurrence", "recurringEventId", "originalStartTime", "transparency", "visibility",
            "iCalUID", "sequence", "attendees", "attendeesOmitted", "extendedProperties",
            "hangoutLink", "conferenceData", "gadget", "anyoneCanAddSelf", "guestsCanInviteOthers",
            "guestsCanModify", "guestsCanSeeOtherGuests", "privateCopy", "locked", "reminders",
            "source", "workingLocationProperties", "outOfOfficeProperties", "focusTimeProperties",
            "attachments", "eventType", "birthdayProperties",
        }),
    ),
    "people.people.connections.list": Operation(
        ("people", "people", "connections", "list"),
        frozenset({
            "resourceName", "pageSize", "pageToken", "personFields", "requestSyncToken",
            "sortOrder", "syncToken",
        }),
        frozenset({"connections", "nextPageToken", "nextSyncToken", "totalPeople", "totalItems"}),
    ),
    "drive.changes.getStartPageToken": Operation(
        ("drive", "changes", "getStartPageToken"),
        frozenset({"driveId", "supportsAllDrives"}),
        frozenset({"kind", "startPageToken"}),
    ),
    "drive.changes.list": Operation(
        ("drive", "changes", "list"),
        frozenset({
            "pageToken", "driveId", "fields", "includeItemsFromAllDrives", "pageSize",
            "restrictToMyDrive", "spaces", "supportsAllDrives",
        }),
        frozenset({"kind", "nextPageToken", "newStartPageToken", "changes"}),
    ),
    "drive.files.list": Operation(
        ("drive", "files", "list"),
        frozenset({
            "corpora", "driveId", "fields", "includeItemsFromAllDrives", "orderBy",
            "pageSize", "pageToken", "q", "spaces", "supportsAllDrives",
        }),
        frozenset({"kind", "nextPageToken", "incompleteSearch", "files"}),
    ),
    "drive.files.get": Operation(
        ("drive", "files", "get"),
        frozenset({"fileId", "acknowledgeAbuse", "fields", "supportsAllDrives"}),
        frozenset({
            "kind", "driveId", "fileExtension", "copyRequiresWriterPermission", "md5Checksum",
            "contentHints", "writersCanShare", "viewedByMe", "mimeType", "exportLinks", "parents",
            "thumbnailLink", "iconLink", "shared", "lastModifyingUser", "owners", "headRevisionId",
            "sharingUser", "webViewLink", "webContentLink", "size", "spaces", "id", "name",
            "description", "starred", "trashed", "explicitlyTrashed", "createdTime", "modifiedTime",
            "modifiedByMeTime", "viewedByMeTime", "sharedWithMeTime", "quotaBytesUsed", "version",
            "originalFilename", "ownedByMe", "fullFileExtension", "properties", "appProperties",
            "capabilities", "hasAugmentedPermissions", "isAppAuthorized", "linkShareMetadata",
            "sha1Checksum", "sha256Checksum", "inheritedPermissionsDisabled",
        }),
    ),
}


class WorkspaceRailError(RuntimeError):
    """A stable content-free rail failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def _run_bounded(argv: tuple[str, ...], *, environment: Mapping[str, str],
                 timeout_seconds: int, max_output_bytes: int) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        env=dict(environment),
        close_fds=True,
        start_new_session=True,
    )
    if process.stdout is None or process.stderr is None:
        _stop_process_group(process)
        raise WorkspaceRailError("transport_unavailable")
    streams = selectors.DefaultSelector()
    streams.register(process.stdout, selectors.EVENT_READ, "stdout")
    streams.register(process.stderr, selectors.EVENT_READ, "stderr")
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
    deadline = time.monotonic() + timeout_seconds
    try:
        while streams.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop_process_group(process)
                raise WorkspaceRailError("upstream_timeout")
            events = streams.select(min(remaining, 0.25))
            for key, _mask in events:
                chunk = os.read(key.fileobj.fileno(), 65_536)
                if not chunk:
                    streams.unregister(key.fileobj)
                    continue
                total += len(chunk)
                if total > max_output_bytes:
                    _stop_process_group(process)
                    raise WorkspaceRailError("output_too_large")
                buffers[key.data].extend(chunk)
        returncode = process.wait(timeout=max(0.01, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        _stop_process_group(process)
        raise WorkspaceRailError("upstream_timeout") from None
    finally:
        streams.close()
        process.stdout.close()
        process.stderr.close()
    return subprocess.CompletedProcess(
        argv, returncode, stdout=bytes(buffers["stdout"]), stderr=bytes(buffers["stderr"]),
    )


def _finite_json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise WorkspaceRailError("invalid_params") from None


def build_argv(operation: str, params: Mapping[str, Any]) -> tuple[str, ...]:
    spec = OPERATIONS.get(operation)
    if spec is None:
        raise WorkspaceRailError("operation_not_allowed")
    if not isinstance(params, Mapping) or not params or not all(isinstance(key, str) for key in params):
        raise WorkspaceRailError("invalid_params")
    if set(params) - spec.params:
        raise WorkspaceRailError("parameter_not_allowed")
    encoded = _finite_json(dict(params))
    return (GWS_EXECUTABLE, *spec.argv, "--params", encoded, "--format", "json")


def _validate_credential_path(path: Path) -> Path:
    path = Path(path)
    if not path.is_absolute():
        raise WorkspaceRailError("credential_reference_invalid")
    try:
        parent = path.parent.lstat()
        metadata = path.lstat()
    except OSError:
        raise WorkspaceRailError("credential_reference_unavailable") from None
    if (
        stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode)
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        raise WorkspaceRailError("credential_reference_invalid")
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise WorkspaceRailError("credential_reference_invalid")
    if stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_size > MAX_CREDENTIAL_BYTES:
        raise WorkspaceRailError("credential_reference_invalid")
    return path


class WorkspaceRail:
    def __init__(self, *, credential_path: Path, timeout_seconds: int = 60,
                 max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES):
        self.credential_path = _validate_credential_path(credential_path)
        if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60:
            raise WorkspaceRailError("invalid_timeout")
        if type(max_output_bytes) is not int or not 128 <= max_output_bytes <= DEFAULT_MAX_OUTPUT_BYTES:
            raise WorkspaceRailError("invalid_output_bound")
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

    def run(self, operation: str, params: Mapping[str, Any]) -> Any:
        argv = build_argv(operation, params)
        credential_path = _validate_credential_path(self.credential_path)
        environment = {
            "GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE": str(credential_path),
            "NO_COLOR": "1",
        }
        try:
            completed = _run_bounded(
                argv, environment=environment, timeout_seconds=self.timeout_seconds,
                max_output_bytes=self.max_output_bytes,
            )
        except WorkspaceRailError:
            raise
        except OSError:
            raise WorkspaceRailError("transport_unavailable") from None
        stdout = completed.stdout
        if not isinstance(stdout, bytes) or not stdout:
            raise WorkspaceRailError("empty_success" if completed.returncode == 0 else "upstream_error")
        if len(stdout) > self.max_output_bytes:
            raise WorkspaceRailError("output_too_large")
        if completed.returncode != 0:
            raise WorkspaceRailError("upstream_error")
        try:
            value = json.loads(stdout, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise WorkspaceRailError("invalid_json") from None
        if not isinstance(value, dict):
            raise WorkspaceRailError("invalid_response_shape")
        spec = OPERATIONS[operation]
        if not value:
            raise WorkspaceRailError("empty_success")
        if set(value) - spec.response_fields:
            raise WorkspaceRailError("response_schema_drift")
        return value

    def export_document(self, *, file_id: str, mime_type: str = "text/plain") -> bytes:
        if not isinstance(file_id, str) or not 1 <= len(file_id) <= 512:
            raise WorkspaceRailError("invalid_file_id")
        if mime_type != "text/plain":
            raise WorkspaceRailError("export_type_not_allowed")
        credential_path = _validate_credential_path(self.credential_path)
        environment = {
            "GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE": str(credential_path),
            "NO_COLOR": "1",
        }
        with tempfile.TemporaryDirectory(prefix="recall-gws-export-") as directory:
            os.chmod(directory, 0o700)
            output = Path(directory) / "document.txt"
            descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            params = _finite_json({"fileId": file_id, "mimeType": mime_type})
            argv = (
                GWS_EXECUTABLE, "drive", "files", "export",
                "--params", params, "--output", str(output),
            )
            try:
                completed = _run_bounded(
                    argv, environment=environment, timeout_seconds=self.timeout_seconds,
                    max_output_bytes=self.max_output_bytes,
                )
            except WorkspaceRailError:
                raise
            except OSError:
                raise WorkspaceRailError("transport_unavailable") from None
            if completed.returncode != 0:
                raise WorkspaceRailError("upstream_error")
            try:
                metadata = output.lstat()
            except OSError:
                raise WorkspaceRailError("export_missing") from None
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise WorkspaceRailError("export_invalid")
            if metadata.st_size <= 0:
                raise WorkspaceRailError("empty_success")
            if metadata.st_size > MAX_EXPORT_BYTES:
                raise WorkspaceRailError("output_too_large")
            os.chmod(output, 0o600)
            return output.read_bytes()
