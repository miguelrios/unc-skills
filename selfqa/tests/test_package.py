import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "selfqa" / "SKILL.md"


class SelfqaPackageTest(unittest.TestCase):
    def test_skill_has_required_frontmatter(self):
        text = SKILL.read_text()
        self.assertTrue(text.startswith("---\n"))
        self.assertRegex(text, r"(?m)^name: selfqa$")
        self.assertRegex(text, r"(?m)^description: .+")

    def test_referenced_files_ship(self):
        text = SKILL.read_text()
        refs = set(re.findall(r"(?:scripts|references|assets|agents)/[A-Za-z0-9_./-]+", text))
        self.assertTrue(refs)
        for relative in refs:
            self.assertTrue((SKILL.parent / relative).exists(), relative)

    def test_no_duplicate_skill_payload(self):
        self.assertEqual(list(ROOT.glob("**/SKILL.md")), [SKILL])

    def test_phases_are_ordered_and_gated(self):
        text = SKILL.read_text()
        phases = re.findall(r"(?m)^## Phase (\d)", text)
        self.assertEqual(phases, ["0", "1", "2", "3", "4"])
        self.assertEqual(text.count("Done when:"), 5)


if __name__ == "__main__":
    unittest.main()
