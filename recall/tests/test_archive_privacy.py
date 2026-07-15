from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ArchivePrivacyTest(unittest.TestCase):
    def test_archive_outputs_are_private_even_with_a_permissive_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            home.chmod(0o755)
            subprocess.run(
                ["bash", str(ROOT / "scripts" / "recall-archive.sh")],
                check=True,
                env={**os.environ, "HOME": str(home)},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            archive = home / "archives"
            manifest = next((archive / "manifests").glob("*.json"))
            self.assertEqual(archive.stat().st_mode & 0o777, 0o700)
            self.assertEqual((archive / "manifests").stat().st_mode & 0o777, 0o700)
            self.assertEqual(manifest.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(manifest.read_text())["sources"], [])


if __name__ == "__main__":
    unittest.main()
