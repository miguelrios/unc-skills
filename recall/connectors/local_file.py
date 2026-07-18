"""Bounded race-checked reads for one explicit local file tree."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from connectors.sdk import ConnectorContractError


def explicit_root(path: Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ConnectorContractError("local_root_not_absolute")
    try:
        details = candidate.lstat()
    except OSError:
        raise ConnectorContractError("local_root_unavailable") from None
    if stat.S_ISLNK(details.st_mode):
        raise ConnectorContractError("local_root_symlink")
    if not stat.S_ISDIR(details.st_mode):
        raise ConnectorContractError("local_root_not_directory")
    return candidate.resolve(strict=True)


def read_stable_file(
    path: Path,
    *,
    root: Path | None = None,
    maximum_bytes: int,
) -> bytes:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        raise ConnectorContractError("local_file_not_absolute")
    if root is not None:
        try:
            candidate.relative_to(root)
        except ValueError:
            raise ConnectorContractError("local_file_path_escape") from None
    try:
        before = candidate.lstat()
    except OSError:
        raise ConnectorContractError("local_file_unavailable") from None
    if stat.S_ISLNK(before.st_mode):
        raise ConnectorContractError("local_file_symlink")
    if not stat.S_ISREG(before.st_mode):
        raise ConnectorContractError("local_file_not_regular")
    if before.st_nlink != 1:
        raise ConnectorContractError("local_file_hard_link")
    if before.st_size > maximum_bytes:
        raise ConnectorContractError("local_file_too_large")
    descriptor = None
    try:
        descriptor = os.open(
            candidate,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ConnectorContractError("local_file_replaced")
        chunks = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(1_048_576, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = candidate.lstat()
        if (
            (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
        ):
            raise ConnectorContractError("local_file_changed_during_read")
        return b"".join(chunks)
    except ConnectorContractError:
        raise
    except OSError:
        raise ConnectorContractError("local_file_read_failed") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


__all__ = ["explicit_root", "read_stable_file"]
