import contextlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
CALL_USER_PATH = ROOT / "scripts" / "call_user.py"
BUNDLED_CALL_USER_PATH = ROOT / "skills" / "hands-free" / "scripts" / "call_user.py"
INSTALL_PATH = ROOT / "install.sh"


def load_call_user(hands_free_home):
    previous = os.environ.get("HANDS_FREE_HOME")
    os.environ["HANDS_FREE_HOME"] = str(hands_free_home)
    try:
        spec = importlib.util.spec_from_file_location("call_user_under_test", CALL_USER_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            os.environ.pop("HANDS_FREE_HOME", None)
        else:
            os.environ["HANDS_FREE_HOME"] = previous


class RepoShapeTest(unittest.TestCase):
    def test_bundled_script_matches_runtime_script(self):
        self.assertEqual(CALL_USER_PATH.read_text(), BUNDLED_CALL_USER_PATH.read_text())

    def test_no_hook_machinery_anywhere(self):
        self.assertFalse((ROOT / "hooks").exists(), "no hooks directory")
        self.assertFalse((ROOT / "scripts" / "hands_free_hook.py").exists(), "no hook script")
        install_source = INSTALL_PATH.read_text()
        for event in ("UserPromptSubmit", "PreToolUse", "PermissionRequest", '"Stop"'):
            self.assertNotIn(event, install_source, f"installer must not wire {event}")
        codex_manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text())
        self.assertNotIn("hooks", codex_manifest)


class CallUserCliTest(unittest.TestCase):
    def test_ask_prints_answer_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = load_call_user(tmp)
            cli.place_call = lambda message, purpose: {
                "artifact": {"messages": [{"role": "user", "message": "Use Descope, not Auth0."}]}
            }
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = cli.main(["call_user.py", "ask", "Which auth provider?"])
            self.assertEqual(code, 0)
            self.assertEqual(output.getvalue().strip(), "Use Descope, not Auth0.")

    def test_ask_voicemail_exits_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = load_call_user(tmp)
            cli.place_call = lambda message, purpose: {
                "endedReason": "voicemail",
                "artifact": {"messages": [{"role": "user", "message": "When you have finished recording, you may hang up."}]},
            }
            code = cli.main(["call_user.py", "ask", "Which auth provider?"])
            self.assertEqual(code, 3, "voicemail transcript must never pass as the user's answer")

    def test_ask_with_no_answer_exits_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = load_call_user(tmp)
            cli.place_call = lambda message, purpose: {"artifact": {"messages": [], "transcript": ""}}
            self.assertEqual(cli.main(["call_user.py", "ask", "Which auth provider?"]), 3)

    def test_approve_maps_decision_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = load_call_user(tmp)
            for spoken, expected in (("approve it", "approve"), ("no, deny that", "deny")):
                cli.place_call = lambda message, purpose, spoken=spoken: {
                    "artifact": {"messages": [{"role": "user", "message": spoken}]}
                }
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = cli.main(["call_user.py", "approve", "Deploy snapshot v6"])
                self.assertEqual(code, 0)
                self.assertEqual(output.getvalue().strip(), expected)

    def test_approve_ambiguous_exits_three(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = load_call_user(tmp)
            cli.place_call = lambda message, purpose: {
                "artifact": {"messages": [{"role": "user", "message": "hmm let me think"}]}
            }
            self.assertEqual(cli.main(["call_user.py", "approve", "Deploy snapshot v6"]), 3)

    def test_approve_requires_user_attributed_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = load_call_user(tmp)
            cli.place_call = lambda message, purpose: {
                "artifact": {
                    "messages": [{"role": "assistant", "message": "Say approve or deny."}],
                    "transcript": "Unc: Say approve or deny.",
                }
            }
            self.assertEqual(cli.main(["call_user.py", "approve", "Deploy snapshot v6"]), 3)

    def test_missing_config_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = load_call_user(tmp)

            def boom(message, purpose):
                raise RuntimeError("Missing VAPI_API_KEY, VAPI_PHONE_NUMBER_ID, or HANDS_FREE_PHONE_NUMBER")

            cli.place_call = boom
            self.assertEqual(cli.main(["call_user.py", "ask", "anything"]), 2)

    def test_usage_error_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = load_call_user(tmp)
            self.assertEqual(cli.main(["call_user.py", "shout", "hey"]), 2)
            self.assertEqual(cli.main(["call_user.py", "ask"]), 2)

    def test_unc_persona_and_greeting_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = load_call_user(tmp)
            assistant = cli.build_assistant("Deploy snapshot v6 to prod", "approval")
            self.assertEqual(assistant["name"], "Unc")
            self.assertIn("Yo, it's Unc.", assistant["firstMessage"])
            self.assertIn("bless it", assistant["firstMessage"])
            self.assertIn("voicemailDetection", assistant)
            os.environ["HANDS_FREE_GREETING"] = "Oye, soy Unc."
            try:
                assistant = cli.build_assistant("Which db?", "input")
                self.assertTrue(assistant["firstMessage"].startswith("Oye, soy Unc."))
            finally:
                os.environ.pop("HANDS_FREE_GREETING", None)

    def test_env_loads_from_hands_free_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = pathlib.Path(tmp)
            (home / ".env").write_text("VAPI_VOICE_ID=Paige\n")
            cli = load_call_user(home)
            os.environ["HANDS_FREE_HOME"] = str(home)
            try:
                self.assertEqual(cli.load_env().get("VAPI_VOICE_ID"), "Paige")
            finally:
                os.environ.pop("HANDS_FREE_HOME", None)


class InstallerTest(unittest.TestCase):
    def run_installer(self, harness, home):
        env_key = "CLAUDE_HOME" if harness == "claude-code" else "CODEX_HOME"
        subprocess.run(
            [str(INSTALL_PATH), f"--harness={harness}"],
            check=True,
            env={**os.environ, env_key: str(home)},
            stdout=subprocess.PIPE,
            text=True,
        )

    def test_installer_drops_files_and_writes_no_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_home = pathlib.Path(tmp) / "claude"
            claude_home.mkdir()
            self.run_installer("claude-code", claude_home)
            self.assertTrue((claude_home / "hands-free" / "scripts" / "call_user.py").exists())
            self.assertTrue((claude_home / "hands-free" / ".env").exists())
            self.assertTrue((claude_home / "skills" / "hands-free" / "SKILL.md").exists())
            self.assertTrue((claude_home / "skills" / "hands-free" / "references" / "setup.md").exists())
            self.assertFalse((claude_home / "settings.json").exists(), "installer must not create settings.json")

    def test_installer_removes_legacy_hooks_and_preserves_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_home = pathlib.Path(tmp) / "claude"
            claude_home.mkdir()
            settings_path = claude_home / "settings.json"
            legacy = "HANDS_FREE_HARNESS=claude-code python3 /old/hands-free/scripts/hands_free_hook.py"
            settings_path.write_text(json.dumps({
                "hooks": {
                    "Stop": [{"hooks": [{"type": "command", "command": legacy}]}],
                    "PreToolUse": [
                        {"matcher": ".*", "hooks": [{"type": "command", "command": legacy}]},
                        {"hooks": [{"type": "command", "command": "/bin/true"}]},
                    ],
                    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": legacy}]}],
                },
                "model": "claude-opus-4-7",
            }))
            # simulate a legacy install's files
            legacy_scripts = claude_home / "hands-free" / "scripts"
            legacy_scripts.mkdir(parents=True)
            (legacy_scripts / "hands_free_hook.py").write_text("# legacy\n")
            (claude_home / "hands-free" / "state.json").write_text('{"active": true}')

            self.run_installer("claude-code", claude_home)

            settings = json.loads(settings_path.read_text())
            self.assertEqual(settings["model"], "claude-opus-4-7", "unrelated settings preserved")
            hooks = settings.get("hooks", {})
            self.assertNotIn("Stop", hooks)
            self.assertNotIn("UserPromptSubmit", hooks)
            pre_tool_cmds = [hook["command"] for entry in hooks.get("PreToolUse", []) for hook in entry["hooks"]]
            self.assertEqual(pre_tool_cmds, ["/bin/true"], "legacy hands-free hooks removed, unrelated hook kept")
            self.assertFalse((legacy_scripts / "hands_free_hook.py").exists(), "legacy hook script deleted")
            self.assertFalse((claude_home / "hands-free" / "state.json").exists(), "legacy state deleted")

    def test_codex_installer_drops_files_without_touching_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = pathlib.Path(tmp) / "codex"
            codex_home.mkdir()
            (codex_home / "config.toml").write_text("[features]\nother_feature = true\n")
            self.run_installer("codex", codex_home)
            self.assertTrue((codex_home / "hands-free" / "scripts" / "call_user.py").exists())
            self.assertTrue((codex_home / "skills" / "hands-free" / "SKILL.md").exists())
            self.assertNotIn("codex_hooks", (codex_home / "config.toml").read_text(), "config.toml untouched")
            self.assertFalse((codex_home / "hooks.json").exists(), "no hooks.json created")


if __name__ == "__main__":
    unittest.main()
