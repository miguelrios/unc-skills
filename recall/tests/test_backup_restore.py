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
    def test_database_fingerprint_covers_v2_truth_projections_and_forget_fence(self) -> None:
        rendered = SCRIPT.read_text()
        for table in (
            "brain_tenants",
            "brain_principals",
            "canonical_sources",
            "raw_artifacts",
            "canonical_ingest_jobs",
            "canonical_events",
            "canonical_documents",
            "canonical_chunks",
            "receipt_redirects",
            "forget_tombstones",
            "canonical_audit_events",
            "admin_credentials",
            "admin_sessions",
            "provider_connections",
            "connector_installations",
            "oauth_sessions",
            "control_audit_events",
        ):
            self.assertIn(f"('{table}',", rendered)
        self.assertIn('"database_fingerprint"', rendered)
        self.assertNotIn('"source_fingerprint"', rendered)
        self.assertIn('pg_restore --dbname="$PGDATABASE"', rendered)
        self.assertIn("to_regclass('public.' || name) IS NOT NULL", rendered)
        self.assertIn("database fingerprint failed", rendered)

    def test_deployment_timer_is_non_overlapping_and_makes_no_false_rpo_claim(self) -> None:
        timer = (ROOT / "server/deploy/recall-brain-backup.timer").read_text()
        service = (ROOT / "server/deploy/recall-brain-backup.service").read_text()
        self.assertIn("OnActiveSec=15min", timer)
        self.assertIn("OnUnitInactiveSec=6h", timer)
        self.assertNotIn("OnUnitActiveSec", timer)
        self.assertNotIn("five-minute", (timer + service).lower())

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
case "$*" in *synthetic-secret*) exit 9;; esac
previous=''
environment=''
for argument in "$@"; do
  test "$previous" != '--env-file' || environment=$argument
  previous=$argument
done
test -n "$environment"
grep -qx 'PGPASSWORD=synthetic-secret' "$environment"
case "$*" in *':/backup'*) exit 5;; esac
case "$*" in *--snapshot=00000001-00000001-1*) :;; *) exit 3;; esac
printf partial
touch "$BACKUP_CONTROL_DIR/partial"
while test ! -e "$BACKUP_CONTROL_DIR/continue"; do sleep 0.02; done
printf complete-new-dump
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
  *'SET TRANSACTION SNAPSHOT'*) printf 'schema_migrations\t1\td41d8cd98f00b204e9800998ecf8427e\n';;
  *) exit 4;;
esac
"""
            )
            psql.chmod(0o755)
            environment = os.environ | {
                "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
                "RECALL_DATABASE_URL": (
                    "postgresql://synthetic:synthetic-secret@synthetic.invalid/db"
                    "?sslmode=verify-full"
                ),
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
                self.assertEqual((backup / "brain.dump").read_bytes(), b"partialcomplete-new-dump")
                manifest = json.loads((backup / "manifest.json").read_text())
                self.assertTrue(manifest["dump_sha256"])
                self.assertEqual(manifest["database_snapshot"], "00000001-00000001-1")
                self.assertIn('"schema_version": 2', stdout)
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
            digest = hashlib.md5(
                b"schema_migrations:1:d41d8cd98f00b204e9800998ecf8427e",
                usedforsecurity=False,
            ).hexdigest()
            (backup / "brain.dump").write_bytes(original)
            (backup / "manifest.json").write_text(json.dumps({
                "dump_sha256": hashlib.sha256(original).hexdigest(),
                "database_fingerprint": f"1:{digest}",
            }))
            control = root / "control"
            control.mkdir()
            binaries = root / "bin"
            binaries.mkdir()
            docker = binaries / "docker"
            docker.write_text(
                """#!/bin/sh
set -eu
case "$*" in *synthetic-secret*) exit 9;; esac
mount=''
previous=''
environment=''
for argument in "$@"; do
  test "$previous" != '--env-file' || environment=$argument
  case "$argument" in *:/backup:ro) mount=${argument%:/backup:ro};; esac
  previous=$argument
done
test -n "$mount"
test -n "$environment"
grep -qx 'PGPASSWORD=synthetic-secret' "$environment"
touch "$BACKUP_CONTROL_DIR/snapshot-opened"
while test ! -e "$BACKUP_CONTROL_DIR/continue"; do sleep 0.02; done
test "$(cat "$mount/brain.dump")" = 'stable-root-owned-dump'
"""
            )
            docker.chmod(0o755)
            psql = binaries / "psql"
            psql.write_text(
                "#!/bin/sh\n"
                "printf 'schema_migrations\\t1\\td41d8cd98f00b204e9800998ecf8427e\\n'\n"
            )
            psql.chmod(0o755)
            forbidden_link = binaries / "ln"
            forbidden_link.write_text("#!/bin/sh\necho 'hardlink forbidden' >&2\nexit 1\n")
            forbidden_link.chmod(0o755)
            environment = os.environ | {
                "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
                "RECALL_RESTORE_DATABASE_URL": (
                    "postgresql://synthetic:synthetic-secret@synthetic.invalid/restore"
                    "?sslmode=verify-full"
                ),
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
