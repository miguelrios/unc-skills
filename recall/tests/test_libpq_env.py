from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "server" / "scripts" / "libpq_env.py"
SPEC = importlib.util.spec_from_file_location("libpq_env", HELPER)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LibpqEnvironmentTest(unittest.TestCase):
    def test_verified_url_becomes_private_environment_file(self) -> None:
        url = (
            "postgresql://synthetic-user:synthetic-password@db.invalid:5433/recall"
            "?sslmode=verify-full&sslrootcert=%2Fsynthetic%2Fca.pem"
        )
        values = MODULE.libpq_environment(url)
        self.assertEqual(values["PGHOST"], "db.invalid")
        self.assertEqual(values["PGPORT"], "5433")
        self.assertEqual(values["PGPASSWORD"], "synthetic-password")
        self.assertEqual(values["PGSSLMODE"], "verify-full")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "libpq.env"
            MODULE.write_environment(output, values)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            self.assertIn("PGPASSWORD=synthetic-password", output.read_text())

    def test_exec_removes_url_and_secret_from_process_arguments(self) -> None:
        url = (
            "postgresql://synthetic-user:synthetic-password@db.invalid/recall"
            "?sslmode=verify-full"
        )
        code = (
            "import json,os,sys;"
            "print(json.dumps({'argv':sys.argv,'url':os.getenv('DATABASE_URL'),"
            "'password':os.getenv('PGPASSWORD'),'tls':os.getenv('PGSSLMODE')}))"
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "exec",
                "--url-env",
                "DATABASE_URL",
                "--",
                sys.executable,
                "-c",
                code,
            ],
            env=os.environ | {"DATABASE_URL": url},
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertNotIn("synthetic-password", " ".join(completed.args))
        self.assertNotIn(url, completed.stdout)
        self.assertIn('"url": null', completed.stdout)
        self.assertIn('"password": "synthetic-password"', completed.stdout)
        self.assertIn('"tls": "verify-full"', completed.stdout)

    def test_rejects_weaker_tls_and_duplicate_query_keys(self) -> None:
        for url in (
            "postgresql://user:password@db.invalid/recall?sslmode=require",
            "postgresql://user:password@db.invalid/recall"
            "?sslmode=verify-full&sslmode=verify-full",
        ):
            with self.subTest(url=url):
                with self.assertRaises(MODULE.ConnectionPolicyError):
                    MODULE.libpq_environment(url)

    def test_local_fixture_is_explicit_disabled_tls_and_loopback_only(self) -> None:
        values = MODULE.libpq_environment(
            "postgresql://user:password@127.0.0.1:55439/recall?sslmode=disable",
            profile="local-fixture",
        )
        self.assertEqual(values["PGSSLMODE"], "disable")
        with self.assertRaises(MODULE.ConnectionPolicyError):
            MODULE.libpq_environment(
                "postgresql://user:password@db.invalid/recall?sslmode=disable",
                profile="local-fixture",
            )
        with self.assertRaises(MODULE.ConnectionPolicyError):
            MODULE.libpq_environment(
                "postgresql://user:password@127.0.0.1/recall?sslmode=disable",
            )


if __name__ == "__main__":
    unittest.main()
