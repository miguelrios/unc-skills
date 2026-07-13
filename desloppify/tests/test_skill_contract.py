import json
import re
import unittest
import sys
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1]
SKILL = PACKAGE / "skills" / "desloppify" / "SKILL.md"
FIXTURES = PACKAGE / "tests" / "fixtures" / "trigger_cases.json"


class SkillContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SKILL.read_text()
        cls.cases = json.loads(FIXTURES.read_text())

    def test_frontmatter_is_narrow_and_complete(self):
        match = re.match(r"\A---\n(.*?)\n---\n", self.text, re.DOTALL)
        self.assertIsNotNone(match)
        frontmatter = match.group(1)
        self.assertIn("name: desloppify", frontmatter)
        self.assertIn("explicitly asks for Desloppify", frontmatter)
        self.assertIn("Do not trigger for an ordinary diff review", frontmatter)
        self.assertNotIn("TODO", self.text)

    def test_trigger_fixture_has_positive_and_negative_coverage(self):
        positives = [case for case in self.cases if case["trigger"]]
        negatives = [case for case in self.cases if not case["trigger"]]
        self.assertGreaterEqual(len(positives), 6)
        self.assertGreaterEqual(len(negatives), 6)
        ids = {case["id"] for case in self.cases}
        self.assertTrue({"monorepo-edge", "sensitive-edge", "gaming-edge"} <= ids)

    def test_referenced_local_files_exist(self):
        paths = set(re.findall(r"\((references/[A-Za-z0-9_./-]+)\)", self.text))
        self.assertGreaterEqual(len(paths), 3)
        for relative in paths:
            self.assertTrue((SKILL.parent / relative).is_file(), relative)

    def test_safety_and_behavior_contracts_are_explicit(self):
        required = (
            ".desloppify/",
            "never by gaming exclusions or suppressions",
            "A higher Desloppify score never excuses a product regression",
            "Do not overwrite `AGENTS.md`",
            "Never read or print model-provider credentials",
            "one coherent program at a time",
            "Peter O'Malley",
            "OSNL-0.2",
        )
        for phrase in required:
            self.assertIn(phrase, self.text)

    def test_companion_does_not_claim_or_bundle_upstream(self):
        normalized = " ".join(self.text.split())
        self.assertIn("does not bundle it", normalized)
        upstream = (SKILL.parent / "references" / "upstream-and-safety.md").read_text()
        self.assertIn("not an official upstream distribution", upstream)
        self.assertIn("Do not run `desloppify update-skill`", upstream)

    def test_routing_names_harness_differences(self):
        routing = (SKILL.parent / "references" / "review-routing.md").read_text()
        for harness in ("Codex", "Claude Code", "Hermes", "OpenCode", "Rovo Dev", "Gemini", "pi"):
            self.assertIn(harness, routing)
        self.assertIn("Do not fabricate trusted", routing)

    def test_public_versions_stay_in_lockstep(self):
        package = json.loads((PACKAGE / "package.json").read_text())
        claude = json.loads((PACKAGE / ".claude-plugin" / "plugin.json").read_text())
        codex = json.loads((PACKAGE / ".codex-plugin" / "plugin.json").read_text())
        script_dir = PACKAGE / "skills" / "desloppify" / "scripts"
        sys.path.insert(0, str(script_dir))
        import desloppify_portable

        self.assertEqual(
            {package["version"], claude["version"], codex["version"], desloppify_portable.COMPANION_VERSION},
            {"0.1.0"},
        )


if __name__ == "__main__":
    unittest.main()
