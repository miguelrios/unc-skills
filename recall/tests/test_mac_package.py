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

    def build(self, name: str) -> Path:
        output = self.root / name
        subprocess.run(
            [sys.executable, str(BUILDER), "--source-root", str(RECALL_ROOT), "--output", str(output)],
            check=True,
        )
        return output

    def test_reproducible_content_free_package_and_clean_install_uninstall(self) -> None:
        first = self.build("first.tar.gz")
        second = self.build("second.tar.gz")
        self.assertEqual(hashlib.sha256(first.read_bytes()).digest(), hashlib.sha256(second.read_bytes()).digest())
        canary = b"C5_TOKEN_CANARY_DO_NOT_RENDER"
        self.assertNotIn(canary, first.read_bytes())

        extracted = self.root / "extracted"
        extracted.mkdir()
        with tarfile.open(first, "r:gz") as archive:
            archive.extractall(extracted, filter="data")
        package = extracted / "recall-brain-macos"
        manifest = json.loads((package / "MANIFEST.json").read_text())
        self.assertEqual(manifest["format"], "recall-macos-v1")
        self.assertNotIn("token", json.dumps(manifest).lower())

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
            "--no-load",
        ]
        subprocess.run([str(package / "install.sh"), *common], check=True, env=env)
        subprocess.run([str(package / "install.sh"), *common], check=True, env=env)
        plists = sorted(launch_agents.glob("ai.parcha.recall.*.plist"))
        self.assertEqual(len(plists), 2)
        for path in plists:
            value = plistlib.loads(path.read_bytes())
            rendered = json.dumps(value, sort_keys=True)
            self.assertIn("Keychain", rendered)
            self.assertNotIn("C5_TOKEN_CANARY", rendered)
            self.assertNotIn("--token", rendered)
        subprocess.run([
            str(package / "uninstall.sh"),
            "--prefix", str(prefix),
            "--launch-agents", str(launch_agents),
            "--no-load",
        ], check=True, env=env)
        self.assertFalse(prefix.exists())
        self.assertEqual(list(launch_agents.glob("ai.parcha.recall.*.plist")), [])


if __name__ == "__main__":
    unittest.main()
