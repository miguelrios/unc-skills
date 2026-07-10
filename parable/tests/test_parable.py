"""Unit tests for parable.py — config merge, validation, argv construction, event parsing."""

import importlib.util
import json
import sys
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "parable" / "scripts" / "parable.py"
spec = importlib.util.spec_from_file_location("parable", SCRIPT)
parable = importlib.util.module_from_spec(spec)
sys.modules["parable"] = parable
spec.loader.exec_module(parable)


class TestMerge(unittest.TestCase):
    def test_executor_merges_per_field(self):
        base = {"executors": {"kimi": {"model": "m1", "effort": "high", "tags": ["a"]}}}
        overlay = {"executors": {"kimi": {"effort": "low"}}}
        out = parable.merge_configs(base, overlay)
        self.assertEqual(out["executors"]["kimi"]["effort"], "low")
        self.assertEqual(out["executors"]["kimi"]["model"], "m1")

    def test_new_executor_added(self):
        out = parable.merge_configs({"executors": {}}, {"executors": {"glm": {"model": "g"}}})
        self.assertIn("glm", out["executors"])

    def test_routing_is_whole_table_overwrite_per_key(self):
        base = {"routing": {"feature": ["sonnet"], "review": ["opus"]}}
        overlay = {"routing": {"feature": ["kimi"]}}
        out = parable.merge_configs(base, overlay)
        self.assertEqual(out["routing"]["feature"], ["kimi"])
        self.assertEqual(out["routing"]["review"], ["opus"])

    def test_builtin_defaults_survive_partial_overlay(self):
        out = parable.merge_configs(parable.BUILTIN_DEFAULTS, {"parable": {"default_executor": "kimi"}})
        self.assertEqual(out["parable"]["default_executor"], "kimi")
        self.assertIn("sonnet", out["executors"])


class TestValidation(unittest.TestCase):
    def cfg(self, **kw):
        cfg = json.loads(json.dumps(parable.BUILTIN_DEFAULTS))
        for key, val in kw.items():
            cfg = parable.merge_configs(cfg, {key: val}) if isinstance(val, dict) else cfg
        return cfg

    def test_builtin_defaults_valid(self):
        self.assertEqual(parable.validate_config(parable.BUILTIN_DEFAULTS), [])

    def test_unknown_provider_type_fails_loud(self):
        cfg = self.cfg(providers={"tau": {"type": "tau"}})
        problems = parable.validate_config(cfg)
        self.assertTrue(any("unknown type 'tau'" in p for p in problems))

    def test_pi_provider_requires_fields(self):
        cfg = self.cfg(providers={"fw": {"type": "pi"}})
        problems = parable.validate_config(cfg)
        self.assertTrue(any("type=pi requires base_url" in p for p in problems))
        self.assertTrue(any("type=pi requires env_key" in p for p in problems))

    def test_pi_provider_api_enum(self):
        cfg = self.cfg(providers={"fw": {"type": "pi", "base_url": "https://x", "env_key": "K", "api": "grpc"}})
        problems = parable.validate_config(cfg)
        self.assertTrue(any("api='grpc'" in p for p in problems))

    def test_effort_off_is_pi_only(self):
        cfg = self.cfg(providers={"fw": {"type": "pi", "base_url": "https://x", "env_key": "K"}})
        cfg["executors"]["m"] = {"provider": "fw", "model": "mm", "effort": "off"}
        self.assertEqual([p for p in parable.validate_config(cfg) if "effort" in p], [])
        cfg2 = self.cfg(providers={"cx": {"type": "codex", "base_url": "https://x", "env_key": "K", "wire_api": "responses"}})
        cfg2["executors"]["m"] = {"provider": "cx", "model": "mm", "effort": "off"}
        problems = parable.validate_config(cfg2)
        self.assertTrue(any("effort='off'" in p for p in problems))

    def test_effort_max_everywhere_ultra_codex_only(self):
        # "max" is legal for both harnesses.
        cfg = self.cfg(providers={"cx": {"type": "codex", "base_url": "https://x", "env_key": "K", "wire_api": "responses"},
                                  "fw": {"type": "pi", "base_url": "https://x", "env_key": "K"}})
        cfg["executors"]["a"] = {"provider": "cx", "model": "mm", "effort": "max"}
        cfg["executors"]["b"] = {"provider": "fw", "model": "mm", "effort": "max"}
        self.assertEqual([p for p in parable.validate_config(cfg) if "effort" in p], [])
        # "ultra" is codex-only (proactive multi-agent delegation); pi rejects it.
        cfg["executors"]["a"]["effort"] = "ultra"
        self.assertEqual([p for p in parable.validate_config(cfg) if "effort" in p], [])
        cfg["executors"]["b"]["effort"] = "ultra"
        problems = parable.validate_config(cfg)
        self.assertTrue(any("effort='ultra'" in p for p in problems))

    def test_codex_provider_requires_responses(self):
        cfg = self.cfg(providers={"fw": {"type": "codex", "base_url": "https://x", "env_key": "K", "wire_api": "chat"}})
        problems = parable.validate_config(cfg)
        self.assertTrue(any("only supports 'responses'" in p for p in problems))

    def test_routing_unknown_executor(self):
        cfg = self.cfg()
        cfg["routing"]["feature"] = ["ghost"]
        problems = parable.validate_config(cfg)
        self.assertTrue(any("routing.feature: unknown executor 'ghost'" in p for p in problems))

    def test_executor_unknown_provider(self):
        cfg = self.cfg()
        cfg["executors"]["x"] = {"provider": "nope", "model": "m"}
        problems = parable.validate_config(cfg)
        self.assertTrue(any("unknown provider 'nope'" in p for p in problems))


class TestArgv(unittest.TestCase):
    def make_cfg(self):
        cfg = json.loads(json.dumps(parable.BUILTIN_DEFAULTS))
        cfg["providers"]["fireworks"] = {
            "type": "codex",
            "base_url": "https://api.fireworks.ai/inference/v1",
            "env_key": "FIREWORKS_API_KEY",
            "wire_api": "responses",
        }
        cfg["executors"]["kimi"] = {
            "provider": "fireworks",
            "model": "accounts/fireworks/models/kimi-k2p7-code",
            "effort": "high",
        }
        return cfg

    def test_run_argv_shape(self):
        argv, overrides = parable.build_run_argv(self.make_cfg(), "kimi", Path("/w"), Path("/l.txt"))
        joined = " ".join(argv)
        self.assertIn("codex exec --yolo --json -C /w", joined)
        self.assertIn('model_providers.parable_fireworks.base_url="https://api.fireworks.ai/inference/v1"', joined)
        self.assertIn('model_providers.parable_fireworks.wire_api="responses"', joined)
        self.assertIn('model_provider="parable_fireworks"', joined)
        self.assertIn('model="accounts/fireworks/models/kimi-k2p7-code"', joined)
        self.assertIn('model_reasoning_effort="high"', joined)
        self.assertEqual(argv[-1], "-")  # plan arrives on stdin
        # overrides are exactly the replayable flags, embedded intact in argv
        self.assertIn(" ".join(overrides), joined)
        self.assertTrue(all(o in argv for o in overrides))

    def test_effort_always_pinned(self):
        cfg = self.make_cfg()
        del cfg["executors"]["kimi"]["effort"]
        argv, _ = parable.build_run_argv(cfg, "kimi", Path("/w"), Path("/l.txt"))
        self.assertIn('model_reasoning_effort="high"', " ".join(argv))

    def test_codex_native_has_no_provider_overrides(self):
        cfg = self.make_cfg()
        cfg["providers"]["openai"] = {"type": "codex-native"}
        cfg["executors"]["gpt55"] = {"provider": "openai", "model": "gpt-5.5"}
        argv, _ = parable.build_run_argv(cfg, "gpt55", Path("/w"), Path("/l.txt"))
        joined = " ".join(argv)
        self.assertNotIn("model_providers", joined)
        self.assertIn('model="gpt-5.5"', joined)


class TestEventParsing(unittest.TestCase):
    def write_events(self, tmp, events):
        p = Path(tmp) / "harness.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in events))
        return p

    def test_parse_stream(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = self.write_events(tmp, [
                {"type": "thread.started", "thread_id": "abc-123"},
                {"type": "turn.started"},
                {"type": "item.completed", "item": {"type": "command_execution", "command": "ls"}},
                {"type": "item.completed", "item": {"type": "agent_message", "text": "done, output is 5"}},
                {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 20}},
            ])
            facts = parable.parse_events(p)
        self.assertEqual(facts["session_id"], "abc-123")
        self.assertEqual(facts["phase"], "complete")
        self.assertEqual(facts["turns"], 1)
        self.assertEqual(facts["tool_calls"], 1)
        self.assertEqual(facts["last_message"], "done, output is 5")
        self.assertEqual(facts["usage"]["input_tokens"], 100)

    def test_non_json_lines_skipped(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "h.jsonl"
            p.write_text("warning: some banner\n" + json.dumps({"type": "thread.started", "thread_id": "x"}))
            facts = parable.parse_events(p)
        self.assertEqual(facts["session_id"], "x")

    def test_error_items_collected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = self.write_events(tmp, [
                {"type": "thread.started", "thread_id": "y"},
                {"type": "item.completed", "item": {"type": "error", "message": "stream disconnected"}},
            ])
            facts = parable.parse_events(p)
        self.assertEqual(facts["errors"], ["stream disconnected"])


PI_PROV = {"type": "pi", "base_url": "https://api.fw.test/v1", "env_key": "FW_KEY"}
PI_EX = {
    "provider": "fw", "model": "accounts/fw/models/mini", "effort": "medium",
    "cost": {"in": 0.30, "out": 1.20, "cache_in": 0.06}, "context_ktok": 512,
}


class TestPiModelsJson(unittest.TestCase):
    def test_mapping(self):
        out = parable.build_pi_models_json("fw", PI_PROV, PI_EX, "minimax")
        prov = out["providers"]["parable_fw"]
        self.assertEqual(prov["baseUrl"], "https://api.fw.test/v1")
        self.assertEqual(prov["apiKey"], "$FW_KEY")
        self.assertEqual(prov["api"], "openai-completions")
        m = prov["models"][0]
        self.assertEqual(m["id"], "accounts/fw/models/mini")
        self.assertEqual(m["name"], "minimax")
        self.assertEqual(m["cost"], {"input": 0.30, "output": 1.20, "cacheRead": 0.06, "cacheWrite": 0})
        self.assertEqual(m["contextWindow"], 512000)
        self.assertTrue(m["reasoning"])

    def test_defaults_and_overrides(self):
        ex = {"provider": "fw", "model": "m", "reasoning": False,
              "model_overrides": {"maxTokens": 9000, "reasoning": True}}
        m = parable.build_pi_models_json("fw", PI_PROV, ex, "x")["providers"]["parable_fw"]["models"][0]
        self.assertEqual(m["cost"]["input"], 0)
        self.assertNotIn("contextWindow", m)
        self.assertEqual(m["maxTokens"], 9000)
        self.assertTrue(m["reasoning"])  # model_overrides merge wins

    def test_no_key_material(self):
        out = json.dumps(parable.build_pi_models_json("fw", PI_PROV, PI_EX, "x"))
        self.assertNotIn("sk-", out)
        self.assertIn("$FW_KEY", out)


class TestPiArgv(unittest.TestCase):
    def make_cfg(self):
        cfg = json.loads(json.dumps(parable.BUILTIN_DEFAULTS))
        cfg["providers"]["fw"] = dict(PI_PROV)
        cfg["executors"]["minimax"] = dict(PI_EX)
        return cfg

    def test_shape(self):
        argv, overrides = parable.build_pi_argv(self.make_cfg(), "minimax", Path("/r"), "sid-1", Path("/r/plan.md"))
        joined = " ".join(argv)
        self.assertTrue(joined.startswith("pi -p --mode json"))
        # provider and model are separate flags: slashed model ids mis-split
        # in the combined provider/id form
        self.assertIn("--provider parable_fw", joined)
        self.assertIn("--model accounts/fw/models/mini", joined)
        self.assertIn("--thinking medium", joined)
        self.assertIn("--session-dir /r/sessions --session-id sid-1", joined)
        self.assertIn("--no-extensions --no-skills --no-prompt-templates --no-approve", joined)
        self.assertEqual(argv[-1], "@/r/plan.md")
        self.assertEqual(argv, ["pi", "-p"] + overrides + ["@/r/plan.md"])

    def test_effort_pinned_when_absent(self):
        cfg = self.make_cfg()
        del cfg["executors"]["minimax"]["effort"]
        argv, _ = parable.build_pi_argv(cfg, "minimax", Path("/r"), "s", Path("/r/plan.md"))
        self.assertIn("--thinking high", " ".join(argv))


class TestPiEventParsing(unittest.TestCase):
    def write(self, tmp, events):
        p = Path(tmp) / "h.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in events))
        return p

    def assistant_end(self, text, cost=0.001, stop="stop", **usage):
        u = {"input": usage.get("inp", 100), "output": usage.get("out", 10),
             "cacheRead": usage.get("cached", 50), "cacheWrite": 0,
             "cost": {"total": cost}}
        return {"type": "message_end", "message": {
            "role": "assistant", "stopReason": stop,
            "content": [{"type": "thinking", "thinking": "..."}, {"type": "text", "text": text}],
            "usage": u}}

    def test_parse_normalizes_to_codex_keys(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = self.write(tmp, [
                {"type": "session", "id": "pid-9"},
                {"type": "turn_start"},
                {"type": "tool_execution_start", "toolName": "bash"},
                self.assistant_end("done, PASS"),
                {"type": "agent_end"},
            ])
            facts = parable.parse_pi_events(p)
        self.assertEqual(facts["session_id"], "pid-9")
        self.assertEqual(facts["phase"], "complete")
        self.assertEqual(facts["turns"], 1)
        self.assertEqual(facts["tool_calls"], 1)
        self.assertEqual(facts["last_message"], "done, PASS")
        self.assertEqual(facts["usage"]["input_tokens"], 100)
        self.assertEqual(facts["usage"]["cached_input_tokens"], 50)
        self.assertEqual(facts["usage"]["output_tokens"], 10)
        self.assertAlmostEqual(facts["usage"]["cost"], 0.001)

    def test_error_stop_reason(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = self.write(tmp, [
                {"type": "session", "id": "x"},
                {"type": "message_end", "message": {"role": "assistant", "stopReason": "error",
                                                    "errorMessage": "boom", "content": [], "usage": {}}},
                {"type": "agent_end"},
            ])
            facts = parable.parse_pi_events(p)
        self.assertEqual(facts["phase"], "error")  # agent_end must not mask the error
        self.assertEqual(facts["errors"], ["boom"])

    def test_merge_with_codex_facts(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = self.write(tmp, [{"type": "session", "id": "s"}, {"type": "turn_start"},
                                 self.assistant_end("fixed", cost=0.002), {"type": "agent_end"}])
            pi_facts = parable.parse_pi_events(p)
        codex_facts = {"session_id": None, "turns": 2, "tool_calls": 3, "last_message": "old",
                       "errors": [], "usage": {"input_tokens": 500}, "phase": "complete"}
        merged = parable.merge_facts([codex_facts, pi_facts])
        self.assertEqual(merged["turns"], 3)
        self.assertEqual(merged["last_message"], "fixed")
        self.assertEqual(merged["usage"]["input_tokens"], 600)

    def test_harness_dispatch(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = self.write(tmp, [{"type": "session", "id": "abc"}])
            self.assertEqual(parable.parse_harness_events("pi", p)["session_id"], "abc")
            self.assertIsNone(parable.parse_harness_events("codex", p)["session_id"])


class TestPiResumeArgv(unittest.TestCase):
    def test_reconstruction(self):
        cfg = json.loads(json.dumps(parable.BUILTIN_DEFAULTS))
        cfg["providers"]["fw"] = dict(PI_PROV)
        cfg["executors"]["minimax"] = dict(PI_EX)
        _, overrides = parable.build_pi_argv(cfg, "minimax", Path("/r"), "sid", Path("/r/plan.md"))
        resume = ["pi", "-p"] + overrides + ["fix it"]
        joined = " ".join(resume)
        self.assertIn("--session-id sid", joined)
        self.assertNotIn("@/r/plan.md", joined)
        self.assertEqual(resume[-1], "fix it")


class TestEnabledFlag(unittest.TestCase):
    def test_disabled_status(self):
        cfg = json.loads(json.dumps(parable.BUILTIN_DEFAULTS))
        cfg["executors"]["sonnet"]["enabled"] = False
        self.assertEqual(parable.env_key_status(cfg, "sonnet"), "disabled")
        self.assertEqual(parable.env_key_status(cfg, "opus"), "subagent")


class TestResearchConfig(unittest.TestCase):
    def test_default_is_grep_ai(self):
        self.assertEqual(parable.BUILTIN_DEFAULTS["research"]["provider"], "grep.ai")
        self.assertEqual(parable.validate_config(parable.BUILTIN_DEFAULTS), [])

    def test_claude_opt_out_valid(self):
        cfg = json.loads(json.dumps(parable.BUILTIN_DEFAULTS))
        cfg["research"]["provider"] = "claude"
        self.assertEqual(parable.validate_config(cfg), [])

    def test_unknown_provider_fails_loud(self):
        cfg = json.loads(json.dumps(parable.BUILTIN_DEFAULTS))
        cfg["research"]["provider"] = "bing"
        problems = parable.validate_config(cfg)
        self.assertTrue(any("[research] provider='bing'" in p for p in problems))

    def test_overlay_overrides_default(self):
        out = parable.merge_configs(parable.BUILTIN_DEFAULTS,
                                    {"research": {"provider": "claude"}})
        self.assertEqual(out["research"]["provider"], "claude")


class TestConfigPrecedence(unittest.TestCase):
    def test_order_lowest_first(self):
        import os as _os
        old = _os.environ.pop("PARABLE_CONFIG", None)
        try:
            paths = parable.config_paths(Path("/repo"))
            self.assertEqual(paths[0], Path.home() / ".config" / "parable" / "parable.toml")
            self.assertEqual(paths[1], Path("/repo/parable.toml"))
            self.assertEqual(paths[2], Path("/repo/.claude/parable.toml"))
            self.assertEqual(len(paths), 3)
            _os.environ["PARABLE_CONFIG"] = "/x/custom.toml"
            paths = parable.config_paths(Path("/repo"))
            self.assertEqual(paths[-1], Path("/x/custom.toml"))  # env wins (loaded last)
        finally:
            _os.environ.pop("PARABLE_CONFIG", None)
            if old is not None:
                _os.environ["PARABLE_CONFIG"] = old

    def test_invalid_toml_fails_loud(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "parable.toml"
            bad.write_text("[parable\nversion = ")
            import os as _os
            old = _os.environ.get("PARABLE_CONFIG")
            _os.environ["PARABLE_CONFIG"] = str(bad)
            try:
                with self.assertRaises(SystemExit) as ctx:
                    parable.load_config(Path(tmp))
                self.assertIn("invalid TOML", str(ctx.exception))
            finally:
                if old is None:
                    _os.environ.pop("PARABLE_CONFIG", None)
                else:
                    _os.environ["PARABLE_CONFIG"] = old


class TestPartitionUntracked(unittest.TestCase):
    def test_no_paths_lists_everything(self):
        include, listed = parable.partition_untracked(["a.py", "b/c.ts"], ".parable", [])
        self.assertEqual(include, [])
        self.assertEqual(listed, ["a.py", "b/c.ts"])

    def test_scoped_includes_only_in_scope(self):
        include, listed = parable.partition_untracked(
            ["src/new.py", "scripts/other.py"], ".parable", ["src"])
        self.assertEqual(include, ["src/new.py"])
        self.assertEqual(listed, ["scripts/other.py"])

    def test_log_dir_fully_hidden(self):
        include, listed = parable.partition_untracked(
            [".parable/runs/x/harness.jsonl", "src/a.py"], ".parable", ["src"])
        self.assertEqual(include, ["src/a.py"])
        self.assertEqual(listed, [])

    def test_secretish_never_included_even_in_scope(self):
        files = ["src/.env.local", "src/service.pem", "src/aws_credentials.json", "src/ok.py"]
        include, listed = parable.partition_untracked(files, ".parable", ["src"])
        self.assertEqual(include, ["src/ok.py"])
        self.assertEqual(sorted(listed), sorted(files[:3]))


class TestMergeFacts(unittest.TestCase):
    def test_aggregates_across_streams(self):
        merged = parable.merge_facts([
            {"session_id": "s1", "turns": 2, "tool_calls": 3, "last_message": "first",
             "errors": ["e1"], "usage": {"input_tokens": 100}, "phase": "complete"},
            {"session_id": None, "turns": 1, "tool_calls": 1, "last_message": "fixed",
             "errors": [], "usage": {"input_tokens": 50}, "phase": "complete"},
        ])
        self.assertEqual(merged["session_id"], "s1")
        self.assertEqual(merged["turns"], 3)
        self.assertEqual(merged["tool_calls"], 4)
        self.assertEqual(merged["last_message"], "fixed")
        self.assertEqual(merged["errors"], ["e1"])
        self.assertEqual(merged["usage"]["input_tokens"], 150)

    def test_empty_resume_keeps_prior_message(self):
        merged = parable.merge_facts([
            {"session_id": "s", "turns": 1, "tool_calls": 0, "last_message": "done",
             "errors": [], "usage": {}, "phase": "complete"},
            {"session_id": None, "turns": 0, "tool_calls": 0, "last_message": "",
             "errors": [], "usage": {}, "phase": "unknown"},
        ])
        self.assertEqual(merged["last_message"], "done")
        self.assertEqual(merged["phase"], "complete")


class TestFailureLines(unittest.TestCase):
    def test_grep_extraction(self):
        check = {"grep": r"error TS\d+"}
        res = {"output": "junk\nsrc/a.ts(3,1): error TS2304: Cannot find name\nmore junk"}
        lines = parable.failure_lines(check, res)
        self.assertEqual(len(lines), 1)
        self.assertIn("TS2304", lines[0])

    def test_tail_fallback(self):
        check = {"tail_lines": 2}
        res = {"output": "a\nb\nc\nd"}
        self.assertEqual(parable.failure_lines(check, res), ["c", "d"])


CURSOR_PROV = {"type": "cursor"}
CURSOR_EX = {"provider": "cur", "model": "composer-2.5", "tags": ["mechanical"]}


class TestCursorValidation(unittest.TestCase):
    def cfg(self):
        c = json.loads(json.dumps(parable.BUILTIN_DEFAULTS))
        c["providers"]["cur"] = dict(CURSOR_PROV)
        c["executors"]["composer"] = dict(CURSOR_EX)
        return c

    def test_cursor_provider_is_valid(self):
        self.assertEqual(parable.validate_config(self.cfg()), [])

    def test_cursor_rejects_base_url(self):
        c = self.cfg()
        c["providers"]["cur"]["base_url"] = "https://x"
        self.assertTrue(any("type=cursor takes no base_url" in p for p in parable.validate_config(c)))

    def test_cursor_effort_is_advisory_not_gated(self):
        # cursor pins effort in the slug, so any effort string is accepted (no enum gate).
        c = self.cfg()
        c["executors"]["composer"]["effort"] = "high"      # a codex enum value
        self.assertEqual([p for p in parable.validate_config(c) if "effort" in p], [])
        c["executors"]["composer"]["effort"] = "banana"    # not in any enum — still fine for cursor
        self.assertEqual([p for p in parable.validate_config(c) if "effort" in p], [])

    def test_cursor_env_status(self):
        import os as _os
        c = self.cfg()
        old = _os.environ.pop("CURSOR_API_KEY", None)
        try:
            self.assertEqual(parable.env_key_status(c, "composer"), "missing CURSOR_API_KEY")
            _os.environ["CURSOR_API_KEY"] = "x"
            self.assertEqual(parable.env_key_status(c, "composer"), "ready")
        finally:
            _os.environ.pop("CURSOR_API_KEY", None)
            if old is not None:
                _os.environ["CURSOR_API_KEY"] = old


class TestCursorArgv(unittest.TestCase):
    def cfg(self):
        c = json.loads(json.dumps(parable.BUILTIN_DEFAULTS))
        c["providers"]["cur"] = dict(CURSOR_PROV)
        c["executors"]["composer"] = dict(CURSOR_EX)
        return c

    def test_run_argv_shape(self):
        argv, overrides = parable.build_cursor_argv(self.cfg(), "composer", Path("/w"))
        self.assertEqual(argv[:2], ["cursor-agent", "-p"])
        joined = " ".join(argv)
        self.assertIn("--output-format stream-json", joined)
        self.assertIn("--force", joined)
        self.assertIn("--model composer-2.5", joined)
        self.assertIn("--workspace /w", joined)
        self.assertNotIn("--resume", joined)  # fresh run has no chat id

    def test_resume_replays_overrides_plus_chat(self):
        _, overrides = parable.build_cursor_argv(self.cfg(), "composer", Path("/w"))
        resume = ["cursor-agent", "-p"] + overrides + ["--resume", "chat-77"]
        joined = " ".join(resume)
        self.assertIn("--model composer-2.5", joined)   # model survives the resume
        self.assertIn("--resume chat-77", joined)


class TestCursorEventParsing(unittest.TestCase):
    def write(self, tmp, events):
        p = Path(tmp) / "h.jsonl"
        p.write_text("\n".join(json.dumps(e) for e in events))
        return p

    def test_parse_normalizes_to_codex_keys(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = self.write(tmp, [
                {"type": "system", "subtype": "init", "session_id": "cur-1", "model": "Composer 2.5"},
                {"type": "tool_call", "subtype": "started", "call_id": "t1"},
                {"type": "tool_call", "subtype": "completed", "call_id": "t1"},
                {"type": "assistant", "message": {"role": "assistant",
                    "content": [{"type": "text", "text": "interim"}]}},
                {"type": "result", "subtype": "success", "is_error": False,
                 "result": "Done. Created file.", "session_id": "cur-1",
                 "usage": {"inputTokens": 8014, "outputTokens": 147,
                           "cacheReadTokens": 24388, "cacheWriteTokens": 0}},
            ])
            facts = parable.parse_cursor_events(p)
        self.assertEqual(facts["session_id"], "cur-1")
        self.assertEqual(facts["phase"], "complete")
        self.assertEqual(facts["turns"], 1)
        self.assertEqual(facts["tool_calls"], 1)   # only 'started' counts, not 'completed'
        self.assertEqual(facts["last_message"], "Done. Created file.")  # result text wins over interim
        self.assertEqual(facts["usage"]["input_tokens"], 8014)
        self.assertEqual(facts["usage"]["cached_input_tokens"], 24388)
        self.assertEqual(facts["usage"]["output_tokens"], 147)

    def test_error_result(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = self.write(tmp, [
                {"type": "system", "subtype": "init", "session_id": "e1"},
                {"type": "result", "subtype": "error", "is_error": True,
                 "result": "model refused", "session_id": "e1"},
            ])
            facts = parable.parse_cursor_events(p)
        self.assertEqual(facts["phase"], "error")
        self.assertEqual(facts["errors"], ["model refused"])

    def test_harness_dispatch(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            p = self.write(tmp, [{"type": "system", "subtype": "init", "session_id": "z"}])
            self.assertEqual(parable.parse_harness_events("cursor", p)["session_id"], "z")


class TestPoolsInConfig(unittest.TestCase):
    def test_maps_provider_types_to_pools(self):
        c = json.loads(json.dumps(parable.BUILTIN_DEFAULTS))  # subagent-only -> claude
        self.assertEqual(parable.pools_in_config(c), ["claude"])
        c["providers"]["oai"] = {"type": "codex-native"}
        c["executors"]["terra"] = {"provider": "oai", "model": "gpt-5.6-terra"}
        c["providers"]["cur"] = dict(CURSOR_PROV)
        c["executors"]["composer"] = dict(CURSOR_EX)
        self.assertEqual(parable.pools_in_config(c), ["claude", "codex", "cursor"])

    def test_metered_providers_have_no_pool(self):
        c = json.loads(json.dumps(parable.BUILTIN_DEFAULTS))
        c["providers"]["fw"] = dict(PI_PROV)
        c["executors"]["minimax"] = dict(PI_EX)
        # pi/codex (API-key) providers are not subscription pools -> only claude remains
        self.assertEqual(parable.pools_in_config(c), ["claude"])


class TestUsageProbe(unittest.TestCase):
    """parable_usage fails soft — a missing credential is 'unknown', never an exception."""
    def setUp(self):
        import importlib, sys as _sys
        _sys.path.insert(0, str(Path(parable.__file__).resolve().parent))
        self.pu = importlib.import_module("parable_usage")

    def test_missing_credentials_are_unknown_not_error(self):
        import os as _os
        saved = {k: _os.environ.pop(k, None) for k in ("CURSOR_API_KEY", "CLAUDE_CONFIG_DIR", "CODEX_HOME")}
        _os.environ["CLAUDE_CONFIG_DIR"] = "/nonexistent-xyz"
        _os.environ["CODEX_HOME"] = "/nonexistent-xyz"
        try:
            r = {x["pool"]: x for x in self.pu.probe_all()}
            self.assertEqual(r["claude"]["status"], "unknown")
            self.assertEqual(r["codex"]["status"], "unknown")
            self.assertEqual(r["cursor"]["status"], "unknown")
            # a soft failure still produces a formattable report
            self.assertIn("unknown", self.pu.format_report(list(r.values())))
        finally:
            _os.environ.pop("CLAUDE_CONFIG_DIR", None)
            _os.environ.pop("CODEX_HOME", None)
            for k, v in saved.items():
                if v is not None:
                    _os.environ[k] = v

    def test_worst_used_pct_picks_tightest_window(self):
        report = {"pool": "codex", "status": "ok",
                  "windows": [{"window": "5h", "used_pct": 12.0},
                              {"window": "7d", "used_pct": 88.0}]}
        self.assertEqual(self.pu.worst_used_pct(report), 88.0)
        self.assertIsNone(self.pu.worst_used_pct({"pool": "x", "windows": []}))


if __name__ == "__main__":
    unittest.main()
