from __future__ import annotations

import json
import hashlib
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "server/scripts/backup_restore.sh"


class BackupPublicationTest(unittest.TestCase):
    def test_backup_keeps_previous_dump_visible_until_replacement_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup = root / "latest"
            backup.mkdir()
            (backup / "brain.dump").write_bytes(b"previous-complete-dump")
            (backup / "manifest.json").write_text(json.dumps({"dump_sha256": "previous"}))
            control = root / "control"
            control.mkdir()
            binaries = root / "bin"
            binaries.mkdir()
            docker = binaries / "docker"
            docker.write_text(
                """#!/bin/sh
set -eu
mount=''
for argument in "$@"; do
  case "$argument" in *:/backup) mount=${argument%:/backup};; esac
done
test -n "$mount"
case "$*" in *--snapshot=00000001-00000001-1*) :;; *) exit 3;; esac
printf partial > "$mount/brain.dump"
touch "$BACKUP_CONTROL_DIR/partial"
while test ! -e "$BACKUP_CONTROL_DIR/continue"; do sleep 0.02; done
printf complete-new-dump > "$mount/brain.dump"
"""
            )
            docker.chmod(0o755)
            psql = binaries / "psql"
            psql.write_text(
                r"""#!/bin/sh
set -eu
input=$(cat)
case "$input" in
  *pg_export_snapshot*)
    echo '00000001-00000001-1'
    fifo=$(printf '%s\n' "$input" | sed -n 's/.*< "\(.*\)"/\1/p')
    read ignored < "$fifo"
    ;;
  *'SET TRANSACTION SNAPSHOT'*'max(created_at)'*) echo 0;;
  *'SET TRANSACTION SNAPSHOT'*) echo '1:synthetic-fingerprint';;
  *) exit 4;;
esac
"""
            )
            psql.chmod(0o755)
            environment = os.environ | {
                "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
                "RECALL_DATABASE_URL": "postgresql://synthetic.invalid/db",
                "BACKUP_CONTROL_DIR": str(control),
            }
            process = subprocess.Popen(
                [str(SCRIPT), "backup", str(backup)], env=environment,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                deadline = time.monotonic() + 5
                while not (control / "partial").exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue((control / "partial").exists(), "fake dump did not start")
                self.assertEqual((backup / "brain.dump").read_bytes(), b"previous-complete-dump")
                (control / "continue").touch()
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertEqual((backup / "brain.dump").read_bytes(), b"complete-new-dump")
                manifest = json.loads((backup / "manifest.json").read_text())
                self.assertTrue(manifest["dump_sha256"])
                self.assertEqual(manifest["database_snapshot"], "00000001-00000001-1")
                self.assertIn('"schema_version": 1', stdout)
                self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
                self.assertEqual((backup / "brain.dump").stat().st_mode & 0o777, 0o600)
                self.assertEqual((backup / "manifest.json").stat().st_mode & 0o777, 0o600)
            finally:
                (control / "continue").touch()
                if process.poll() is None:
                    try:
                        process.communicate(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
                else:
                    process.communicate()

    def test_restore_uses_immutable_copy_when_hardlinks_are_forbidden(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backup = root / "latest"
            backup.mkdir()
            original = b"stable-root-owned-dump"
            (backup / "brain.dump").write_bytes(original)
            (backup / "manifest.json").write_text(json.dumps({
                "dump_sha256": hashlib.sha256(original).hexdigest(),
                "source_fingerprint": "1:synthetic-fingerprint",
            }))
            control = root / "control"
            control.mkdir()
            binaries = root / "bin"
            binaries.mkdir()
            docker = binaries / "docker"
            docker.write_text(
                """#!/bin/sh
set -eu
mount=''
for argument in "$@"; do
  case "$argument" in *:/backup:ro) mount=${argument%:/backup:ro};; esac
done
test -n "$mount"
touch "$BACKUP_CONTROL_DIR/snapshot-opened"
while test ! -e "$BACKUP_CONTROL_DIR/continue"; do sleep 0.02; done
test "$(cat "$mount/brain.dump")" = 'stable-root-owned-dump'
"""
            )
            docker.chmod(0o755)
            psql = binaries / "psql"
            psql.write_text("#!/bin/sh\necho '1:synthetic-fingerprint'\n")
            psql.chmod(0o755)
            forbidden_link = binaries / "ln"
            forbidden_link.write_text("#!/bin/sh\necho 'hardlink forbidden' >&2\nexit 1\n")
            forbidden_link.chmod(0o755)
            environment = os.environ | {
                "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
                "RECALL_RESTORE_DATABASE_URL": "postgresql://synthetic.invalid/restore",
                "BACKUP_CONTROL_DIR": str(control),
            }
            process = subprocess.Popen(
                [str(SCRIPT), "restore-test", str(backup)], env=environment,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                deadline = time.monotonic() + 2
                while (
                    not (control / "snapshot-opened").exists()
                    and process.poll() is None
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.02)
                self.assertTrue(
                    (control / "snapshot-opened").exists(),
                    "restore depended on the forbidden hardlink",
                )
                (backup / "brain.dump").write_bytes(b"concurrently-published-replacement")
                (backup / "manifest.json").write_text("{}")
                (control / "continue").touch()
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertIn('"status": "pass"', stdout)
            finally:
                (control / "continue").touch()
                if process.poll() is None:
                    try:
                        process.communicate(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
                else:
                    process.communicate()


if __name__ == "__main__":
    unittest.main()
