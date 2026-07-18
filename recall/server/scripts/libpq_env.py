#!/usr/bin/env python3
"""Translate a verified PostgreSQL URL into libpq environment variables."""

from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit


ALLOWED_QUERY = {
    "application_name": "PGAPPNAME",
    "channel_binding": "PGCHANNELBINDING",
    "connect_timeout": "PGCONNECT_TIMEOUT",
    "options": "PGOPTIONS",
    "sslcert": "PGSSLCERT",
    "sslkey": "PGSSLKEY",
    "sslrootcert": "PGSSLROOTCERT",
    "target_session_attrs": "PGTARGETSESSIONATTRS",
}


class ConnectionPolicyError(ValueError):
    pass


def libpq_environment(url: str) -> dict[str, str]:
    if not url or any(character in url for character in "\x00\r\n"):
        raise ConnectionPolicyError("database URL unavailable")
    parsed = urlsplit(url)
    try:
        port = parsed.port or 5432
    except ValueError as error:
        raise ConnectionPolicyError("database port invalid") from error
    database = unquote(parsed.path.removeprefix("/"))
    username = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or not parsed.hostname
        or not database
        or not username
        or not password
        or parsed.fragment
    ):
        raise ConnectionPolicyError("database URL invalid")
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    if len({key for key, _value in pairs}) != len(pairs):
        raise ConnectionPolicyError("database query duplicated")
    query = dict(pairs)
    if query.pop("sslmode", None) != "verify-full":
        raise ConnectionPolicyError("database TLS policy invalid")
    unknown = set(query) - set(ALLOWED_QUERY)
    if unknown:
        raise ConnectionPolicyError("database query unsupported")
    values = {
        "PGHOST": parsed.hostname,
        "PGPORT": str(port),
        "PGDATABASE": database,
        "PGUSER": username,
        "PGPASSWORD": password,
        "PGSSLMODE": "verify-full",
        **{ALLOWED_QUERY[key]: value for key, value in query.items()},
    }
    if any(
        not value or any(character in value for character in "\x00\r\n")
        for value in values.values()
    ):
        raise ConnectionPolicyError("database field invalid")
    return values


def write_environment(path: Path, values: dict[str, str]) -> None:
    if path.exists() or path.is_symlink():
        raise ConnectionPolicyError("environment output already exists")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w") as output:
            descriptor = -1
            for key, value in sorted(values.items()):
                output.write(f"{key}={value}\n")
            output.flush()
            os.fsync(output.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        path.unlink(missing_ok=True)
        raise ConnectionPolicyError("environment output mode invalid")


def main() -> None:
    parser = argparse.ArgumentParser()
    operations = parser.add_subparsers(dest="operation", required=True)
    execute = operations.add_parser("exec")
    execute.add_argument("--url-env", required=True)
    execute.add_argument("command", nargs=argparse.REMAINDER)
    write = operations.add_parser("write")
    write.add_argument("--url-env", required=True)
    write.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    values = libpq_environment(os.environ.get(args.url_env, ""))
    if args.operation == "write":
        write_environment(args.output, values)
        return
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        execute.error("a command is required")
    environment = os.environ.copy()
    environment.pop(args.url_env, None)
    environment.update(values)
    os.execvpe(command[0], command, environment)


if __name__ == "__main__":
    main()
