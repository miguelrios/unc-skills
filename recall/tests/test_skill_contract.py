"""Static tests for Recall's agent-facing interaction contract."""
from pathlib import Path
import unittest


SKILL = Path(__file__).resolve().parents[1] / "skills/recall/SKILL.md"


class RecallSkillContractTest(unittest.TestCase):
    def test_first_run_requires_native_mode_question_and_verified_exit(self):
        instructions = SKILL.read_text(encoding="utf-8")

        for required in (
            "`AskUserQuestion`",
            "`request_user_input`",
            "Hosted brain (Recommended)",
            "Local-only",
            "Where should Recall search?",
            "Do not auto-resolve or silently default to local.",
            "python3 scripts/recall.py doctor",
            "OK remote",
            "never falls back to SQLite",
        ):
            with self.subTest(required=required):
                self.assertIn(required, instructions)


if __name__ == "__main__":
    unittest.main()
