"""Read-only, transaction-consistent access to one explicitly selected SQLite file."""

from __future__ import annotations

import sqlite3
import stat
from pathlib import Path
from types import TracebackType

from connectors.sdk import ConnectorContractError


MAX_LOCAL_DATABASE_BYTES = 64 * 1024 * 1024 * 1024


class ReadOnlySQLiteSnapshot:
    """Hold one SQLite read transaction without creating source-side files."""

    def __init__(self, path: Path):
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            raise ConnectorContractError("local_database_path_not_absolute")
        try:
            details = candidate.lstat()
        except OSError:
            raise ConnectorContractError("local_database_unavailable") from None
        if stat.S_ISLNK(details.st_mode):
            raise ConnectorContractError("local_database_symlink")
        if not stat.S_ISREG(details.st_mode):
            raise ConnectorContractError("local_database_not_regular")
        if details.st_size > MAX_LOCAL_DATABASE_BYTES:
            raise ConnectorContractError("local_database_too_large")
        self.path = candidate
        self._identity = (details.st_dev, details.st_ino)
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(
                self.path.resolve(strict=True).as_uri() + "?mode=ro",
                uri=True,
                timeout=5,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("BEGIN")
            after = self.path.lstat()
            if stat.S_ISLNK(after.st_mode) or (
                after.st_dev,
                after.st_ino,
            ) != self._identity:
                raise ConnectorContractError("local_database_replaced")
            if connection.execute("PRAGMA query_only").fetchone()[0] != 1:
                raise ConnectorContractError("local_database_not_read_only")
        except ConnectorContractError:
            if "connection" in locals():
                connection.close()
            raise
        except (OSError, sqlite3.Error, ValueError):
            if "connection" in locals():
                connection.close()
            raise ConnectorContractError("local_database_open_failed") from None
        self.connection = connection
        return connection

    def __exit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        if self.connection is not None:
            try:
                self.connection.rollback()
            finally:
                self.connection.close()
                self.connection = None


def table_columns(connection: sqlite3.Connection, table: str) -> frozenset[str]:
    if table not in {"chat", "chat_message_join", "handle", "message"}:
        raise ConnectorContractError("local_database_table_not_allowed")
    try:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        raise ConnectorContractError("local_database_schema_unavailable") from None
    return frozenset(row["name"] for row in rows)


__all__ = ["ReadOnlySQLiteSnapshot", "table_columns"]
