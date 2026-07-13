from __future__ import annotations

import json
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
printf partial > "$mount/brain.dump"
touch "$BACKUP_CONTROL_DIR/partial"
while test ! -e "$BACKUP_CONTROL_DIR/continue"; do sleep 0.02; done
printf complete-new-dump > "$mount/brain.dump"
"""
            )
            docker.chmod(0o755)
            psql = binaries / "psql"
            psql.write_text(
                """#!/bin/sh
case "$*" in *'max(created_at)'*) echo 0;; *) echo '1:synthetic-fingerprint';; esac
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
                self.assertIn('"schema_version": 1', stdout)
            finally:
                (control / "continue").touch()
                if process.poll() is None:
                    try:
                        process.communicate(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()


if __name__ == "__main__":
    unittest.main()
