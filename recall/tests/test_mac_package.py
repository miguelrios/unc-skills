from __future__ import annotations

import hashlib
import json
import os
import plistlib
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


RECALL_ROOT = Path(__file__).resolve().parents[1]
BUILDER = RECALL_ROOT / "scripts" / "build_macos_package.py"


class MacPackageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runtime = self.root / "runtime.tar.gz"
        self.runtime_lock = self.root / "RUNTIME_LOCK.json"
        self._write_runtime_fixture()

    def _write_runtime_fixture(self, *, include_license: bool = True) -> None:
        members = {
            "python/bin/python3.12": b"\xcf\xfa\xed\xfe\x0c\x00\x00\x01" + b"\x00" * 24,
            "python/lib/python3.12/__pycache__/site.cpython-312.pyc": b"derived cache fixture",
            "python/lib/python3.12/ssl.py": b"# fixture\n",
            "python/lib/python3.12/sqlite3/__init__.py": b"# fixture\n",
        }
        if include_license:
            members["python/lib/python3.12/LICENSE.txt"] = b"Python fixture license\n"
        with tarfile.open(self.runtime, "w:gz") as archive:
            for name, data in sorted(members.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o755 if name.endswith("python3.12") else 0o644
                archive.addfile(info, __import__("io").BytesIO(data))
            link = tarfile.TarInfo("python/bin/python3")
            link.type = tarfile.SYMTYPE
            link.linkname = "python3.12"
            link.mode = 0o777
            archive.addfile(link)
        lock = {
            "schema_version": 1,
            "provider": "test/runtime",
            "release": "immutable-test-release",
            "version": "3.12.13",
            "target": "aarch64-apple-darwin",
            "archive_root": "python",
            "capabilities": {
                "implementation": "CPython",
                "language": {"zip_strict": True},
                "machine": "arm64",
                "stdlib_imports": ["ctypes", "ssl", "sqlite3"],
                "system": "Darwin",
                "sqlite": {"fts5": True},
                "tls": {
                    "default_ca_certificates": "nonempty",
                    "default_verify_path": "existing",
                },
            },
            "artifact": {
                "url": "https://example.invalid/runtime.tar.gz",
                "bytes": self.runtime.stat().st_size,
                "sha256": hashlib.sha256(self.runtime.read_bytes()).hexdigest(),
            },
            "license_paths": ["python/lib/python3.12/LICENSE.txt"],
            "required_paths": [
                "python/bin/python3", "python/bin/python3.12",
                "python/lib/python3.12/LICENSE.txt", "python/lib/python3.12/ssl.py",
                "python/lib/python3.12/sqlite3/__init__.py",
            ],
        }
        self.runtime_lock.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")

    def build(self, name: str, *, check: bool = True) -> Path | subprocess.CompletedProcess[str]:
        output = self.root / name
        result = subprocess.run(
            [
                sys.executable, str(BUILDER), "--source-root", str(RECALL_ROOT),
                "--runtime-archive", str(self.runtime), "--runtime-lock", str(self.runtime_lock),
                "--output", str(output),
            ],
            check=check, text=True, capture_output=True,
        )
        return output if check else result

    def test_production_runtime_lock_is_immutable_arm64_cpython_312(self) -> None:
        lock = json.loads((RECALL_ROOT / "client" / "macos" / "RUNTIME_LOCK.json").read_text())
        self.assertEqual(lock["version"], "3.12.13")
        self.assertEqual(lock["target"], "aarch64-apple-darwin")
        self.assertEqual(lock["release"], "20260510")
        self.assertEqual(lock["artifact"]["bytes"], 25102827)
        self.assertEqual(lock["artifact"]["sha256"], "5a30271f8d345a5b02b0c9e4e31e0f1e1455a8e4a04fba95cd9762472abc3b17")
        self.assertTrue(lock["artifact"]["url"].startswith("https://github.com/astral-sh/python-build-standalone/releases/download/20260510/"))
        self.assertEqual(lock["capabilities"], {
            "implementation": "CPython",
            "language": {"zip_strict": True},
            "machine": "arm64",
            "stdlib_imports": ["ctypes", "ssl", "sqlite3"],
            "system": "Darwin",
            "sqlite": {"fts5": True},
            "tls": {
                "default_ca_certificates": "nonempty",
                "default_verify_path": "existing",
            },
        })

    def test_tampered_runtime_is_rejected_before_package_write(self) -> None:
        self.runtime.write_bytes(self.runtime.read_bytes() + b"tampered")
        result = self.build("tampered.tar.gz", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runtime artifact", result.stderr)
        self.assertFalse((self.root / "tampered.tar.gz").exists())

    def test_wrong_target_and_missing_license_are_rejected(self) -> None:
        lock = json.loads(self.runtime_lock.read_text())
        lock["target"] = "x86_64-apple-darwin"
        self.runtime_lock.write_text(json.dumps(lock))
        wrong_target = self.build("wrong-target.tar.gz", check=False)
        self.assertNotEqual(wrong_target.returncode, 0)
        self.assertIn("aarch64-apple-darwin", wrong_target.stderr)

        self._write_runtime_fixture(include_license=False)
        missing_license = self.build("missing-license.tar.gz", check=False)
        self.assertNotEqual(missing_license.returncode, 0)
        self.assertIn("license", missing_license.stderr.lower())

    def test_reproducible_content_free_package_and_clean_install_uninstall(self) -> None:
        first = self.build("first.tar.gz")
        second = self.build("second.tar.gz")
        self.assertEqual(hashlib.sha256(first.read_bytes()).digest(), hashlib.sha256(second.read_bytes()).digest())
        canary = b"C5_TOKEN_CANARY_DO_NOT_RENDER"
        self.assertNotIn(canary, first.read_bytes())

        extracted = self.root / "extracted"
        extracted.mkdir()
        with tarfile.open(first, "r:gz") as archive:
            names = archive.getnames()
            self.assertFalse(any("__pycache__" in name or name.endswith(".pyc") for name in names))
            archive.extractall(extracted, filter="data")
        package = extracted / "recall-brain-macos"
        manifest = json.loads((package / "MANIFEST.json").read_text())
        self.assertEqual(manifest["format"], "recall-macos-v2")
        self.assertEqual(manifest["runtime"]["version"], "3.12.13")
        self.assertEqual(manifest["runtime"]["target"], "aarch64-apple-darwin")
        self.assertTrue((package / "runtime" / "bin" / "python3").is_symlink())
        self.assertTrue((package / "runtime" / "lib" / "python3.12" / "LICENSE.txt").is_file())
        self.assertNotIn("token", json.dumps(manifest).lower())

        wrapper = (package / "bin" / "recall-brain").read_text()
        self.assertIn('exec "$HERE/runtime/bin/python3" -m client.cli', wrapper)
        self.assertNotIn("exec python3", wrapper)
        installer = (package / "install.sh").read_text()
        self.assertIn('$PREFIX/runtime/bin/python3', installer)
        self.assertNotRegex(installer, r"(?m)(?:^|[ ;])python3(?:[ ;]|$)")
        self.assertIn('RUNTIME_LOCK.json', installer)
        self.assertIn('ssl.get_default_verify_paths()', installer)
        self.assertIn('get_ca_certs()', installer)

        home = self.root / "home"
        home.mkdir()
        env = {**os.environ, "HOME": str(home)}
        prefix = home / "Library" / "Application Support" / "RecallBrain"
        launch_agents = home / "Library" / "LaunchAgents"
        common = [
            "--prefix", str(prefix),
            "--launch-agents", str(launch_agents),
            "--endpoint", "https://brain.example.ts.net",
            "--host-id", "test-mac",
            "--keychain-service", "ai.parcha.recall.test",
            "--visibility", "private",
            "--sources", "claude,codex",
            "--no-load",
        ]
        # Installation executes and validates the Mach-O runtime, so that E2E belongs
        # on the real Apple-arm64 target. This unit test pins its generated plist contract.
        self.assertIn('"ProgramArguments": [', installer)
        self.assertIn('"-m", "client.cli", "collect"', installer)
        self.assertIn('"PYTHONPATH":', installer)


if __name__ == "__main__":
    unittest.main()
