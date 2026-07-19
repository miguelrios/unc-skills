from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from server.recall_server.archive import ArchiveRequest, FilesystemArchiveStore
from server.recall_server.archive_snapshot import (
    ArchiveSnapshotError,
    backup_filesystem_archive,
    restore_filesystem_archive,
    verify_filesystem_archive,
)


class ArchiveSnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.archive = FilesystemArchiveStore(
            self.root / "archive",
            namespace_key=b"a" * 32,
        )
        self.references = []
        for index in range(3):
            self.references.append(self.archive.put(ArchiveRequest(
                tenant_id="tenant:snapshot",
                source_id="source:snapshot",
                native_id=f"native:{index}",
                media_type="application/json",
                payload=json.dumps({"index": index, "text": "private"}).encode(),
            )))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_backup_restore_is_independent_exact_private_and_content_free(self) -> None:
        backup = self.root / "backup"
        before = verify_filesystem_archive(self.archive.root)
        published = backup_filesystem_archive(self.archive.root, backup)
        restored = self.root / "restored"
        result = restore_filesystem_archive(backup, restored)
        after = verify_filesystem_archive(restored)

        self.assertEqual(before, after)
        self.assertEqual(published["archive_fingerprint"], before["archive_fingerprint"])
        self.assertEqual(result["archive_fingerprint"], before["archive_fingerprint"])
        self.assertEqual(result["status"], "pass")
        restored_store = FilesystemArchiveStore(
            restored,
            namespace_key=b"a" * 32,
        )
        for reference in self.references:
            self.assertIn(
                b'"text": "private"',
                restored_store.read(
                    reference,
                    tenant_id="tenant:snapshot",
                    source_id="source:snapshot",
                ),
            )
        for directory in (
            self.archive.root / "objects",
            *list((self.archive.root / "objects").glob("*")),
            *list((self.archive.root / "objects").glob("*/*")),
        ):
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        self.assertEqual(restored.stat().st_mode & 0o777, 0o700)
        rendered = json.dumps(result)
        self.assertNotIn("private", rendered)
        self.assertNotIn(str(self.root), rendered)

    def test_tamper_symlink_and_nonempty_restore_fail_closed(self) -> None:
        backup = self.root / "backup"
        backup_filesystem_archive(self.archive.root, backup)
        data = next((backup / "objects").glob("*/*/data"))
        data.write_bytes(b"tampered")
        with self.assertRaisesRegex(ArchiveSnapshotError, "integrity"):
            restore_filesystem_archive(backup, self.root / "restore-tampered")

        object_dir = next((self.archive.root / "objects").glob("*/*"))
        (object_dir / "data").unlink()
        (object_dir / "data").symlink_to(self.root / "outside")
        with self.assertRaisesRegex(ArchiveSnapshotError, "unsafe"):
            verify_filesystem_archive(self.archive.root)

        occupied = self.root / "occupied"
        occupied.mkdir(mode=0o700)
        (occupied / "marker").write_text("do not replace")
        with self.assertRaisesRegex(ArchiveSnapshotError, "empty"):
            restore_filesystem_archive(backup, occupied)
