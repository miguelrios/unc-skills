import tempfile
import unittest
from pathlib import Path
import sys


PACKAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE / "scripts"))
import check_package  # noqa: E402


class PackageAuditTest(unittest.TestCase):
    def test_clean_files_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "README.md").write_text("safe")
            self.assertEqual(check_package.audit_files(["README.md"], root), [])

    def test_private_state_and_cache_paths_fail(self):
        violations = check_package.audit_files(
            [".desloppify/state.json", "skills/x/__pycache__/thing.pyc"],
            Path("/nonexistent"),
        )
        self.assertEqual(violations, [
            ".desloppify/state.json",
            "skills/x/__pycache__/thing.pyc",
        ])

    def test_forbidden_paths_are_component_aware(self):
        self.assertFalse(check_package.forbidden_path("src/tokenizer.py"))
        self.assertFalse(check_package.forbidden_path("docs/review_packets_are_private.md"))
        self.assertTrue(check_package.forbidden_path("state/token/value.txt"))
        self.assertTrue(check_package.forbidden_path(".env.local"))
        self.assertTrue(check_package.forbidden_path("review_packet_blind.json"))

    def test_secret_shaped_content_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "oops.txt"
            path.write_text("-----BEGIN PRIVATE KEY-----")
            self.assertEqual(
                check_package.audit_files(["oops.txt"], root),
                ["oops.txt (secret-shaped content)"],
            )


if __name__ == "__main__":
    unittest.main()
