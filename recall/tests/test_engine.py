"""Synthetic, fixture-scoped tests for the local recall engine."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "skills/recall/scripts/recall.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
spec = importlib.util.spec_from_file_location("recall_engine", SCRIPT)
engine = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(engine)


class RecallEngineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.claude = self.root / "claude"
        self.codex = self.root / "codex"
        self.claude.mkdir(); self.codex.mkdir()
        self.db = self.root / "state/index.db"
        self.old_env = {key: os.environ.get(key) for key in ("RECALL_CLAUDE_ROOT", "RECALL_CODEX_ROOT", "RECALL_DB")}
        os.environ.update(RECALL_CLAUDE_ROOT=str(self.claude), RECALL_CODEX_ROOT=str(self.codex), RECALL_DB=str(self.db))

    def tearDown(self):
        for key, value in self.old_env.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value
        self.tmp.cleanup()

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = engine.main(list(argv))
        self.assertEqual(code, 0, err.getvalue())
        return out.getvalue()

    def write_claude(self, name="session.jsonl", extra=""):
        target = self.claude / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / "claude_sample.jsonl", target)
        if extra:
            with target.open("a") as fh: fh.write(extra)
        return target

    def copy_codex(self):
        target = self.codex / "2026/02/03/rollout-fixture.jsonl"
        target.parent.mkdir(parents=True)
        shutil.copy(FIXTURES / "codex_rollout.jsonl", target)
        return target

    def rows(self, sql):
        conn = engine.connect(self.db)
        result = conn.execute(sql).fetchall(); conn.close()
        return result

    def test_append_resume_grows_without_duplicates(self):
        p = self.write_claude()
        self.cli("index")
        before = self.rows("select count(*) from chunks")[0][0]
        line = json.dumps({"type":"user", "timestamp":"2026-01-02T03:05:00Z", "message":{"content":"appended uniquely"}}) + "\n"
        with p.open("a") as fh: fh.write(line)
        self.assertIn("appended=1", self.cli("index"))
        self.assertEqual(self.rows("select count(*) from chunks")[0][0], before + 1)
        self.cli("index")
        self.assertEqual(self.rows("select count(*) from chunks")[0][0], before + 1)

    def test_truncation_reparses_and_rename_tombstones(self):
        p = self.write_claude(); self.cli("index")
        p.write_text(json.dumps({"type":"user", "timestamp":"2026-01-03T00:00:00Z", "message":{"content":"short replacement"}}) + "\n")
        self.cli("index")
        self.assertEqual(self.rows("select count(*) from chunks")[0][0], 1)
        newer = self.claude / "renamed.jsonl"; p.rename(newer)
        self.cli("index")
        statuses = {Path(r[0]).name: r[1] for r in self.rows("select path,status from files")}
        self.assertEqual(statuses["session.jsonl"], "tombstone")
        self.assertEqual(statuses["renamed.jsonl"], "ok")

    def test_final_partial_line_is_safe_and_resumed(self):
        p = self.write_claude()
        partial = '{"type":"user","timestamp":"2026-01-04T00:00:00Z","message":{"content":"later"}}'
        with p.open("a") as fh: fh.write(partial)
        self.cli("index")
        self.assertEqual(self.rows("select status from files")[0][0], "partial")
        self.assertNotIn("later", self.cli("search", "later"))
        with p.open("a") as fh: fh.write("\n")
        self.cli("index")
        self.assertIn("later", self.cli("search", "later"))

    def test_secret_redaction_tool_cap_and_fts_injection(self):
        secret = "sk-ABCDEFGHIJKLMNOPQRSTUVWX"
        huge = "z" * 5000
        p = self.claude / "secret.jsonl"
        p.write_text("\n".join([
            json.dumps({"type":"user","timestamp":"2026-01-01T00:00:00Z","message":{"content":"token=" + secret + "\nnormal safe words"}}),
            json.dumps({"type":"assistant","timestamp":"2026-01-01T00:00:01Z","message":{"content":[{"type":"tool_result","content":huge}]}}),
        ]) + "\n")
        self.cli("index")
        self.assertNotIn(secret, self.cli("search", secret))
        self.assertEqual(self.rows("select length(text) from chunks where surface='tool_output'")[0][0], 4096)
        self.cli("search", '"foo OR bar)(')

    def test_quoted_secret_and_codex_metadata_are_redacted(self):
        secret = "AbCdEfGhIjKlMnOpQrStUvWxYz123456"
        rollout = self.codex / "2026/01/01/rollout-secret.jsonl"
        rollout.parent.mkdir(parents=True)
        rollout.write_text("\n".join([
            json.dumps({"timestamp":"2026-01-01T00:00:00Z", "type":"session_meta", "payload": {
                "title": '"api_key": "' + secret, "first_user_prompt": 'authorization: "' + secret,
                "cwd":"/safe"}}),
            json.dumps({"timestamp":"2026-01-01T00:00:01Z", "type":"response_item", "payload": {
                "role":"user", "content":[{"type":"input_text", "text":'"token": "' + secret}]}}),
        ]) + "\n")
        self.cli("index")
        self.assertNotIn(secret, self.cli("search", secret))
        stored = self.rows("select title,first_user_prompt from sessions")[0]
        self.assertNotIn(secret, stored[0]); self.assertNotIn(secret, stored[1])
        self.assertEqual(stored[0], "[redacted-secret-line]")

    def test_partial_fts_or_fallback_returns_natural_partial_matches(self):
        (self.claude / "alpha.jsonl").write_text(json.dumps({"type":"user", "timestamp":"2026-01-01T00:00:00Z", "message":{"content":"alpha bravo only"}}) + "\n")
        (self.claude / "beta.jsonl").write_text(json.dumps({"type":"user", "timestamp":"2026-01-01T00:00:01Z", "message":{"content":"alpha charlie only"}}) + "\n")
        self.cli("index")
        paths = self.cli("search", "alpha bravo charlie", "--paths")
        self.assertIn("alpha.jsonl", paths)
        self.assertIn("beta.jsonl", paths)

    def test_phrase_leg_finds_exact_error_among_common_decoys(self):
        error = "TypeError expected str got None"
        for number in range(8):
            (self.claude / f"decoy-{number}.jsonl").write_text(json.dumps({
                "type":"assistant", "timestamp":"2026-01-01T00:00:00Z",
                "message":{"content":"TypeError expected another value"}}) + "\n")
        target = self.claude / "error-target.jsonl"
        target.write_text(json.dumps({"type":"assistant", "timestamp":"2026-01-01T00:00:01Z",
                                      "message":{"content":"prefix " + error + " suffix"}}) + "\n")
        self.cli("index")
        self.assertEqual(Path(self.cli("search", error, "--paths").splitlines()[0]), target)

    def test_entity_direct_survives_fts_leg_cutoff(self):
        uuid = "deadbeef-1234-1234-1234-123456789abc"
        target = self.claude / "entity-target.jsonl"
        target.write_text(json.dumps({"type":"assistant", "timestamp":"2026-01-01T00:00:00Z",
                                      "message":{"content":[{"type":"tool_result", "content":uuid}]}}) + "\n")
        decoy = self.claude / "entity-decoys.jsonl"
        decoy.write_text(json.dumps({"type":"user", "timestamp":"2026-01-01T00:00:00Z",
                                     "message":{"content":"seed"}}) + "\n")
        self.cli("index")
        conn = engine.connect(self.db)
        decoy_session = conn.execute("select s.id from sessions s join files f on f.id=s.file_id where f.path like '%entity-decoys.jsonl'").fetchone()[0]
        for _ in range(4):
            chunk_id = conn.execute("insert into chunks(session_id,ts,surface,text) values (?,?,?,?)", (decoy_session, 1, "assistant", "common " + uuid + " " + uuid)).lastrowid
            conn.execute("insert into chunks_fts(rowid,text) values (?,?)", (chunk_id, "common " + uuid + " " + uuid))
        conn.commit(); conn.close()
        old_limit = engine.FTS_LEG_LIMIT
        engine.FTS_LEG_LIMIT = 2
        try:
            self.assertEqual(Path(self.cli("search", "common " + uuid, "--paths").splitlines()[0]), target)
        finally:
            engine.FTS_LEG_LIMIT = old_limit

    def test_identifier_token_leg_finds_raw_tool_output_without_entity(self):
        token = "api-prod-6fcdc84dd4-mmjpj"
        target = self.claude / "identifier-tool.jsonl"
        target.write_text(json.dumps({"type":"user", "timestamp":"2026-01-01T00:00:00Z",
                                      "message":{"content":"seed"}}) + "\n")
        self.cli("index")
        conn = engine.connect(self.db)
        session_id = conn.execute("select s.id from sessions s join files f on f.id=s.file_id where f.path like '%identifier-tool.jsonl'").fetchone()[0]
        chunk_id = conn.execute("insert into chunks(session_id,ts,surface,text) values (?,?,?,?)", (session_id, 1, "tool_output", token)).lastrowid
        conn.execute("insert into chunks_fts(rowid,text) values (?,?)", (chunk_id, token))
        conn.commit(); conn.close()
        self.assertEqual(Path(self.cli("search", "which pod was failing " + token, "--paths").splitlines()[0]), target)

    def test_gate_keeps_fuzzy_best_with_two_long_terms(self):
        target = self.claude / "fuzzy-best.jsonl"
        target.write_text(json.dumps({"type":"user", "timestamp":"2026-01-01T00:00:00Z",
                                      "message":{"content":"aurora beacon remediation"}}) + "\n")
        self.cli("index")
        paths = self.cli("search", "aurora unrelated beacon", "--paths")
        self.assertIn("fuzzy-best.jsonl", paths)

    def test_vocab_pruning_omits_noisy_or_term(self):
        target = self.claude / "signal.jsonl"
        target.write_text(json.dumps({"type":"user", "timestamp":"2026-01-01T00:00:00Z",
                                      "message":{"content":"signalterm anotherterm"}}) + "\n")
        self.cli("index")
        original = engine.vocab_doc_counts
        engine.vocab_doc_counts = lambda conn, terms: {"noisyterm": 100001, "signalterm": 1, "anotherterm": 1}
        try:
            self.assertEqual(Path(self.cli("search", "noisyterm signalterm anotherterm", "--paths").splitlines()[0]), target)
        finally:
            engine.vocab_doc_counts = original

    def test_negative_and_stopword_queries_are_empty(self):
        self.write_claude(); self.copy_codex(); self.cli("index")
        self.assertEqual(self.cli("search", "kafka consumer rebalancing tuning", "--paths"), "")
        self.assertEqual(self.cli("search", "the and of to", "--paths"), "")

    def test_fts_limit_keeps_bm25_best_candidate(self):
        filler = self.claude / "filler.jsonl"
        best = self.claude / "best.jsonl"
        filler.write_text(json.dumps({"type":"user", "timestamp":"2026-01-01T00:00:00Z", "message":{"content":"seed"}}) + "\n")
        best.write_text(json.dumps({"type":"user", "timestamp":"2026-01-01T00:00:00Z", "message":{"content":"seed"}}) + "\n")
        self.cli("index")
        conn = engine.connect(self.db)
        ids = {Path(r[1]).name: r[0] for r in conn.execute("select s.id,f.path from sessions s join files f on f.id=s.file_id")}
        for _ in range(1001):
            chunk_id = conn.execute("insert into chunks(session_id,ts,surface,text) values (?,?,?,?)", (ids["filler.jsonl"], 1, "user", "rankingword")).lastrowid
            conn.execute("insert into chunks_fts(rowid,text) values (?,?)", (chunk_id, "rankingword"))
        chunk_id = conn.execute("insert into chunks(session_id,ts,surface,text) values (?,?,?,?)", (ids["best.jsonl"], 1, "user", " ".join(["rankingword"] * 40))).lastrowid
        conn.execute("insert into chunks_fts(rowid,text) values (?,?)", (chunk_id, " ".join(["rankingword"] * 40)))
        conn.commit(); conn.close()
        self.assertIn("best.jsonl", self.cli("search", "rankingword", "--paths"))

    def test_append_fingerprint_detects_tail_edit_with_preserved_stat(self):
        p = self.claude / "large.jsonl"
        old = "a" * 12000
        p.write_text(json.dumps({"type":"user", "timestamp":"2026-01-01T00:00:00Z", "message":{"content":old}}) + "\n")
        self.cli("index")
        stat = p.stat()
        changed = p.read_text()
        # Change a region within the final 4 KiB while retaining byte length and mtime.
        changed = changed[:-100] + changed[-100:].replace("a", "b", 1)
        p.write_text(changed); os.utime(p, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.cli("index")
        self.assertIn("b", self.rows("select text from chunks")[0][0][-200:])

    def test_tombstoned_file_resurrects_without_reparse(self):
        p = self.write_claude(); parked = self.root / "parked.jsonl"
        self.cli("index")
        p.rename(parked); self.cli("index")
        self.assertEqual(self.rows("select status from files")[0][0], "tombstone")
        parked.rename(p); self.cli("index")
        self.assertEqual(self.rows("select status from files")[0][0], "ok")
        self.assertIn("session.jsonl", self.cli("search", "PAR-123"))

    def test_required_indexes_exist(self):
        self.write_claude(); self.cli("index")
        conn = engine.connect_ro(self.db)
        names = {row[1] for table in ("chunks", "entities", "sessions") for row in conn.execute(f"pragma index_list({table})")}
        conn.close()
        self.assertTrue({"chunks_session_idx", "entities_chunk_idx", "sessions_file_idx"} <= names)

    def test_entity_boost_filters_and_codex_reader(self):
        c = self.write_claude("old.jsonl")
        # A generic recent session supplies a less-specific ordinary match.
        uuid = "12345678-1234-1234-1234-123456789abc"
        (self.claude / "generic.jsonl").write_text(json.dumps({"type":"user","timestamp":"2026-07-01T00:00:00Z","cwd":"/other","message":{"content":"mention an unrelated identifier"}}) + "\n")
        codex = self.copy_codex(); self.cli("index")
        # Seed a matching legacy-style chunk with no entities: the exact UUID
        # prefix entity in the Codex transcript must outrank this newer match.
        conn = engine.connect(self.db)
        session_id = conn.execute("select s.id from sessions s join files f on f.id=s.file_id where f.path like '%generic.jsonl'").fetchone()[0]
        chunk_id = conn.execute("insert into chunks(session_id,ts,surface,text) values (?,?,?,?)", (session_id, 1782864000, "assistant", "legacy mention 12345678")).lastrowid
        conn.execute("insert into chunks_fts(rowid,text) values (?,?)", (chunk_id, "legacy mention 12345678"))
        conn.commit(); conn.close()
        result = self.cli("search", "12345678", "--paths")
        self.assertEqual(Path(result.splitlines()[0]), codex)
        self.assertIn("rollout-fixture", self.cli("search", "found", "--harness", "codex"))
        self.assertNotIn("rollout-fixture", self.cli("search", "found", "--since", "2026-03-01T00:00:00Z"))
        self.assertIn("old.jsonl", self.cli("search", "PAR-123", "--harness", "claude"))
        self.assertTrue(c.exists())

    def test_related_show_and_doctor(self):
        self.write_claude("one.jsonl")
        self.write_claude("two.jsonl")
        self.cli("index")
        related = self.cli("related", "--cwd", "/work/grep123/project")
        self.assertIn("one.jsonl", related)
        shown = self.cli("show", str(self.claude / "one.jsonl"), "--prompts")
        self.assertIn("Please inspect", shown)
        doctor = self.cli("doctor")
        self.assertIn("OK FTS5 available", doctor)

    def test_doctor_missing_db_is_read_only(self):
        self.assertFalse(self.db.exists())
        doctor = self.cli("doctor")
        self.assertIn("WARN db exists=False", doctor)
        self.assertFalse(self.db.exists())


if __name__ == "__main__":
    unittest.main()
