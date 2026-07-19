from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any


OBJECT_KEY_RE = re.compile(r"objects/[0-9a-f]{2}/[0-9a-f]{64}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
ARTIFACT_ID_RE = re.compile(r"art_[0-9a-f]{32}\Z")
METADATA_KEYS = {
    "artifact_id",
    "tenant_scope_sha256",
    "source_scope_sha256",
    "content_sha256",
    "size_bytes",
    "media_type_sha256",
}


class ArchiveSnapshotError(RuntimeError):
    """Content-free archive snapshot failure."""


def _directory(path: Path, *, label: str) -> None:
    try:
        details = path.lstat()
    except OSError as error:
        raise ArchiveSnapshotError(f"{label} is unavailable") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISDIR(details.st_mode)
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise ArchiveSnapshotError(f"{label} is unsafe")


def _open_regular(path: Path) -> tuple[int, int]:
    try:
        details = path.lstat()
    except OSError as error:
        raise ArchiveSnapshotError("archive object is unavailable") from error
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or stat.S_IMODE(details.st_mode) != 0o600
    ):
        raise ArchiveSnapshotError("archive object is unsafe")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (details.st_dev, details.st_ino):
            os.close(descriptor)
            raise ArchiveSnapshotError("archive object changed during verification")
        return descriptor, opened.st_size
    except OSError as error:
        raise ArchiveSnapshotError("archive object is unavailable") from error


def _read_regular(path: Path, *, maximum_bytes: int) -> bytes:
    descriptor, size = _open_regular(path)
    try:
        if size > maximum_bytes:
            raise ArchiveSnapshotError("archive metadata integrity failed")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, min(65_536, maximum_bytes + 1))
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def _hash_regular(path: Path, *, expected_bytes: int) -> str:
    descriptor, size = _open_regular(path)
    digest = hashlib.sha256()
    try:
        if size != expected_bytes:
            raise ArchiveSnapshotError("archive object integrity failed")
        while True:
            chunk = os.read(descriptor, 1_048_576)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)
    finally:
        os.close(descriptor)


def _copy_regular(source: Path, destination: Path) -> None:
    source_descriptor, _size = _open_regular(source)
    destination_descriptor = -1
    try:
        destination_descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        while True:
            chunk = os.read(source_descriptor, 1_048_576)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(destination_descriptor, view)
                if written <= 0:
                    raise ArchiveSnapshotError("archive copy failed")
                view = view[written:]
        os.fsync(destination_descriptor)
    except ArchiveSnapshotError:
        raise
    except OSError as error:
        raise ArchiveSnapshotError("archive copy failed") from error
    finally:
        os.close(source_descriptor)
        if destination_descriptor >= 0:
            os.close(destination_descriptor)


def _metadata(value: bytes) -> dict[str, str]:
    try:
        parsed = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArchiveSnapshotError("archive metadata integrity failed") from error
    if not isinstance(parsed, dict) or set(parsed) != METADATA_KEYS:
        raise ArchiveSnapshotError("archive metadata integrity failed")
    strings = {
        "artifact_id": ARTIFACT_ID_RE,
        "tenant_scope_sha256": SHA256_RE,
        "source_scope_sha256": SHA256_RE,
        "content_sha256": SHA256_RE,
        "media_type_sha256": SHA256_RE,
    }
    if any(
        not isinstance(parsed[key], str) or not pattern.fullmatch(parsed[key])
        for key, pattern in strings.items()
    ):
        raise ArchiveSnapshotError("archive metadata integrity failed")
    size = parsed["size_bytes"]
    if not isinstance(size, str) or not size.isdigit() or int(size) > 5 * 1024**3:
        raise ArchiveSnapshotError("archive metadata integrity failed")
    return parsed


def _object_directories(root: Path) -> list[Path]:
    _directory(root, label="archive root")
    if {entry.name for entry in root.iterdir()} - {"objects", "manifest.json"}:
        raise ArchiveSnapshotError("archive object layout is unsafe")
    objects = root / "objects"
    if not objects.exists():
        return []
    _directory(objects, label="archive object root")
    directories: list[Path] = []
    for prefix in sorted(objects.iterdir()):
        _directory(prefix, label="archive prefix")
        if not re.fullmatch(r"[0-9a-f]{2}", prefix.name):
            raise ArchiveSnapshotError("archive object layout is unsafe")
        for object_dir in sorted(prefix.iterdir()):
            _directory(object_dir, label="archive object directory")
            relative = object_dir.relative_to(root).as_posix()
            if not OBJECT_KEY_RE.fullmatch(relative) or object_dir.name[:2] != prefix.name:
                raise ArchiveSnapshotError("archive object layout is unsafe")
            directories.append(object_dir)
    return directories


def verify_filesystem_archive(root: Path) -> dict[str, int | str]:
    root = Path(root)
    digest = hashlib.sha256()
    object_count = 0
    byte_count = 0
    for object_dir in _object_directories(root):
        entries = {entry.name for entry in object_dir.iterdir()}
        if entries != {"data", "metadata.json"}:
            raise ArchiveSnapshotError("archive object layout is unsafe")
        metadata_bytes = _read_regular(
            object_dir / "metadata.json",
            maximum_bytes=16_384,
        )
        metadata = _metadata(metadata_bytes)
        size_bytes = int(metadata["size_bytes"])
        content_sha256 = _hash_regular(
            object_dir / "data",
            expected_bytes=size_bytes,
        )
        if metadata["content_sha256"] != content_sha256:
            raise ArchiveSnapshotError("archive object integrity failed")
        object_key = object_dir.relative_to(root).as_posix()
        digest.update(object_key.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(metadata_bytes).digest())
        digest.update(bytes.fromhex(content_sha256))
        object_count += 1
        byte_count += size_bytes
    return {
        "object_count": object_count,
        "byte_count": byte_count,
        "archive_fingerprint": digest.hexdigest(),
    }


def _copy_archive(source: Path, destination: Path) -> None:
    destination.mkdir(mode=0o700)
    objects = destination / "objects"
    objects.mkdir(mode=0o700)
    for object_dir in _object_directories(source):
        relative = object_dir.relative_to(source)
        target = destination / relative
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.parent.chmod(0o700)
        target.mkdir(mode=0o700)
        for name in ("data", "metadata.json"):
            _copy_regular(object_dir / name, target / name)


def _empty_destination(path: Path) -> None:
    if not path.exists():
        return
    _directory(path, label="archive destination")
    if any(path.iterdir()):
        raise ArchiveSnapshotError("archive destination must be empty")


def backup_filesystem_archive(source: Path, destination: Path) -> dict[str, Any]:
    source = Path(source)
    source = source if source.is_absolute() else Path.cwd() / source
    destination = Path(destination)
    if not destination.is_absolute():
        raise ArchiveSnapshotError("archive destination is unsafe")
    if source == destination or source in destination.parents:
        raise ArchiveSnapshotError("archive destination is unsafe")
    destination_parent = destination.parent
    _directory(destination_parent, label="archive destination parent")
    _empty_destination(destination)
    before = verify_filesystem_archive(source)
    stage = Path(tempfile.mkdtemp(prefix=".archive-backup-", dir=destination_parent))
    stage.chmod(0o700)
    try:
        stage.rmdir()
        _copy_archive(source, stage)
        after = verify_filesystem_archive(stage)
        if before != after:
            raise ArchiveSnapshotError("archive backup integrity failed")
        manifest = {
            "schema_version": 1,
            **before,
        }
        manifest_path = stage / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
        )
        manifest_path.chmod(0o600)
        if destination.exists():
            destination.rmdir()
        os.replace(stage, destination)
        return {"status": "pass", **before}
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def _load_manifest(snapshot: Path) -> dict[str, Any]:
    raw = _read_regular(snapshot / "manifest.json", maximum_bytes=16_384)
    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ArchiveSnapshotError("archive snapshot integrity failed") from error
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {
            "schema_version", "object_count", "byte_count", "archive_fingerprint",
        }
        or manifest["schema_version"] != 1
        or type(manifest["object_count"]) is not int
        or type(manifest["byte_count"]) is not int
        or not isinstance(manifest["archive_fingerprint"], str)
        or not SHA256_RE.fullmatch(manifest["archive_fingerprint"])
    ):
        raise ArchiveSnapshotError("archive snapshot integrity failed")
    return manifest


def restore_filesystem_archive(snapshot: Path, destination: Path) -> dict[str, Any]:
    snapshot = Path(snapshot)
    destination = Path(destination)
    _directory(snapshot, label="archive snapshot")
    if not destination.is_absolute():
        raise ArchiveSnapshotError("archive destination is unsafe")
    _directory(destination.parent, label="archive destination parent")
    _empty_destination(destination)
    manifest = _load_manifest(snapshot)
    current = verify_filesystem_archive(snapshot)
    expected = {
        key: manifest[key]
        for key in ("object_count", "byte_count", "archive_fingerprint")
    }
    if current != expected:
        raise ArchiveSnapshotError("archive snapshot integrity failed")
    stage = Path(tempfile.mkdtemp(prefix=".archive-restore-", dir=destination.parent))
    stage.rmdir()
    try:
        _copy_archive(snapshot, stage)
        restored = verify_filesystem_archive(stage)
        if restored != expected:
            raise ArchiveSnapshotError("archive restore integrity failed")
        if destination.exists():
            destination.rmdir()
        os.replace(stage, destination)
        return {"status": "pass", **restored}
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(prog="recall-archive-snapshot")
    operations = parser.add_subparsers(dest="operation", required=True)
    backup = operations.add_parser("backup")
    backup.add_argument("source", type=Path)
    backup.add_argument("destination", type=Path)
    restore = operations.add_parser("restore-test")
    restore.add_argument("snapshot", type=Path)
    restore.add_argument("destination", type=Path)
    verify = operations.add_parser("verify")
    verify.add_argument("archive", type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.operation == "backup":
            result = backup_filesystem_archive(
                arguments.source,
                arguments.destination,
            )
        elif arguments.operation == "restore-test":
            result = restore_filesystem_archive(
                arguments.snapshot,
                arguments.destination,
            )
        else:
            result = {"status": "pass", **verify_filesystem_archive(arguments.archive)}
    except ArchiveSnapshotError:
        print(json.dumps({
            "status": "failed",
            "error_code": "archive_snapshot_failed",
        }, sort_keys=True))
        raise SystemExit(2) from None
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
