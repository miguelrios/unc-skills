#!/usr/bin/env python3
"""Local, rebuildable search index for Claude Code and Codex JSONL sessions."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "3"
PARSER_VERSION = 1
MAX_TOOL_INPUT = 2048
MAX_TOOL_OUTPUT = 4096
FTS_LEG_LIMIT = 400
SECRET_RE = re.compile(
    r"[\"']?(?:api[_-]?key|token|secret|password|bearer|authorization)[\"']?\s*[=:]\s*[\"']?(?:Bearer\s+)?\S{12,}|"
    r"sk-[A-Za-z0-9]{20,}|xox[bp]-|ghs_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}", re.I)
PATH_RE = re.compile(r"(?<!\w)(?:/[A-Za-z0-9_@.+~#%=-]+(?:/[A-Za-z0-9_@.+~#%=-]+)+|(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_@.+~#%=-]+\.[A-Za-z0-9]+)")
URL_RE = re.compile(r"https?://[^\s<>\]\[\"']+")
UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b|\b[0-9a-fA-F]{8}\b")
STOPWORDS = frozenset("""the a an of in on we i was which that did do how what when where why to for with
and or not it its this those these from by at as is are were be been about into over after before
one ones our my me you us your their them they he she his her can could would should will just
than then if else""".split())


def paths() -> tuple[Path, Path, Path]:
    home = Path.home()
    return (
        Path(os.environ.get("RECALL_CLAUDE_ROOT", home / ".claude/projects")).expanduser(),
        Path(os.environ.get("RECALL_CODEX_ROOT", home / ".codex/sessions")).expanduser(),
        Path(os.environ.get("RECALL_DB", home / ".recall/index.db")).expanduser(),
    )


def epoch(value) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def iso(value: str | None) -> float | None:
    if not value:
        return None
    result = epoch(value)
    if result is None:
        raise ValueError("expected an ISO-8601 timestamp")
    return result


def clean_text(value) -> str:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return "\n".join("[redacted-secret-line]" if SECRET_RE.search(line) else line
                     for line in value.splitlines())


def clipped(value, limit: int) -> str:
    text = clean_text(value)
    return text[:limit]


def fingerprint(path: Path, size: int | None = None) -> str:
    if size is None:
        size = path.stat().st_size
    with path.open("rb") as fh:
        first = fh.read(min(4096, size))
        fh.seek(max(0, size - 4096))
        last = fh.read(min(4096, size))
    return hashlib.sha256(first + last + str(size).encode()).hexdigest()


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(db_path.parent, 0o700)
    conn = sqlite3.connect(db_path)
    os.chmod(db_path, 0o600)
    conn.row_factory = sqlite3.Row
    return conn


def connect_ro(db_path: Path) -> sqlite3.Connection:
    """Open an existing database without creating files or changing permissions."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS files(
      id INTEGER PRIMARY KEY, path TEXT UNIQUE, harness TEXT, size INTEGER,
      mtime_ns INTEGER, fingerprint TEXT, parsed_offset INTEGER,
      parser_version INTEGER, status TEXT);
    CREATE TABLE IF NOT EXISTS sessions(
      id INTEGER PRIMARY KEY, file_id INTEGER, harness TEXT, cwd TEXT, slot TEXT,
      git_branch TEXT, started_at REAL, ended_at REAL, n_turns INTEGER, model TEXT,
      title TEXT, first_user_prompt TEXT);
    CREATE TABLE IF NOT EXISTS chunks(
      id INTEGER PRIMARY KEY, session_id INTEGER, ts REAL, surface TEXT, text TEXT);
    CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
      text, content='chunks', content_rowid='id',
      tokenize="unicode61 tokenchars '-_./#'");
    CREATE TABLE IF NOT EXISTS entities(chunk_id INTEGER, kind TEXT, value TEXT);
    CREATE INDEX IF NOT EXISTS chunks_session_idx ON chunks(session_id);
    CREATE INDEX IF NOT EXISTS entities_chunk_idx ON entities(chunk_id);
    CREATE INDEX IF NOT EXISTS entities_value_idx ON entities(value);
    CREATE INDEX IF NOT EXISTS entities_value_lower_idx ON entities(lower(value));
    CREATE INDEX IF NOT EXISTS sessions_file_idx ON sessions(file_id);
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
    """)
    conn.executemany("INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)", [
        ("schema_version", SCHEMA_VERSION), ("parser_version", str(PARSER_VERSION))])


def reset_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    DROP TABLE IF EXISTS chunks_fts; DROP TABLE IF EXISTS entities; DROP TABLE IF EXISTS chunks;
    DROP TABLE IF EXISTS sessions; DROP TABLE IF EXISTS files; DROP TABLE IF EXISTS meta;
    """)
    create_schema(conn)


def discover(root: Path, harness: str):
    if not root.exists():
        return []
    if harness == "codex":
        return sorted(p for p in root.rglob("rollout-*.jsonl") if p.is_file())
    return sorted(p for p in root.rglob("*.jsonl") if p.is_file())


def add_entities(conn: sqlite3.Connection, chunk_id: int, text: str, extra: list[tuple[str, str]]) -> None:
    found = set(extra)
    found.update(("file_path", x) for x in PATH_RE.findall(text))
    found.update(("pr", x) for x in re.findall(r"#\d{3,5}\b", text))
    found.update(("ticket", x) for x in re.findall(r"\bPAR-\d+\b", text))
    found.update(("url", x.rstrip(".,;)")) for x in URL_RE.findall(text))
    uuid_values = UUID_RE.findall(text)
    found.update(("uuid", x.lower()) for x in uuid_values)
    # Full UUIDs are also indexed by their conventional eight-hex short form.
    found.update(("uuid", x[:8].lower()) for x in uuid_values if "-" in x)
    found.update(("skill", x) for x in re.findall(r"Launching skill:\s*(\w[\w-]*)", text))
    found.update(("error", x) for x in re.findall(r"\b\w+(?:Error|Exception)\b", text))
    conn.executemany("INSERT INTO entities(chunk_id,kind,value) VALUES (?,?,?)",
                     [(chunk_id, kind, value) for kind, value in found])


def content_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(content_text(x.get("text", x.get("content", ""))) if isinstance(x, dict)
                         else str(x) for x in value)
    return "" if value is None else str(value)


def claude_record(data: dict) -> tuple[list[tuple[float | None, str, str, list[tuple[str, str]]]], dict]:
    out, meta = [], {}
    ts = epoch(data.get("timestamp"))
    for key in ("cwd", "gitBranch", "model"):
        if data.get(key) is not None:
            value = data[key]
            meta[{"gitBranch": "branch"}.get(key, key)] = clean_text(value) if isinstance(value, str) else value
    typ = data.get("type")
    message = data.get("message") or {}
    content = message.get("content", data.get("content", "")) if isinstance(message, dict) else data.get("content", "")
    if typ in ("user", "assistant") and isinstance(content, str):
        out.append((ts, typ, clean_text(content), []))
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "thinking":
                continue
            if kind == "text":
                surface = "user" if typ == "user" else "assistant"
                out.append((ts, surface, clean_text(block.get("text", "")), []))
            elif kind == "tool_use":
                name = str(block.get("name", ""))
                out.append((ts, "tool_input", clipped(block.get("input", {}), MAX_TOOL_INPUT), [("tool", name)] if name else []))
            elif kind == "tool_result":
                out.append((ts, "tool_output", clipped(content_text(block.get("content", "")), MAX_TOOL_OUTPUT), []))
    return [(a, b, c, d) for a, b, c, d in out if c], meta


def codex_record(data: dict) -> tuple[list[tuple[float | None, str, str, list[tuple[str, str]]]], dict]:
    out, meta = [], {}
    ts = epoch(data.get("timestamp"))
    typ, payload = data.get("type"), data.get("payload") or {}
    if typ == "session_meta":
        for source, target in (("cwd", "cwd"), ("model", "model"), ("title", "title"),
                               ("first_user_prompt", "first_user_prompt"), ("git_branch", "branch")):
            if payload.get(source) is not None or data.get(source) is not None:
                value = payload.get(source, data.get(source))
                meta[target] = clean_text(value) if isinstance(value, str) else value
        return out, meta
    if typ != "response_item" or not isinstance(payload, dict):
        return out, meta
    role = payload.get("role")
    ptype = payload.get("type")
    if role in ("user", "assistant"):
        texts = []
        for block in payload.get("content", []) if isinstance(payload.get("content"), list) else []:
            if isinstance(block, dict) and block.get("type") in ("input_text", "output_text", "text"):
                texts.append(str(block.get("text", "")))
        if not texts and isinstance(payload.get("content"), str):
            texts = [payload["content"]]
        if texts:
            out.append((ts, role, clean_text("\n".join(texts)), []))
    elif ptype == "function_call":
        name = str(payload.get("name", ""))
        out.append((ts, "tool_input", clipped(payload.get("arguments", ""), MAX_TOOL_INPUT), [("tool", name)] if name else []))
    elif ptype == "function_call_output":
        out.append((ts, "tool_output", clipped(payload.get("output", ""), MAX_TOOL_OUTPUT), []))
    return [(a, b, c, d) for a, b, c, d in out if c], meta


def parse_file(path: Path, harness: str, offset: int = 0):
    chunks, meta = [], {}
    total = bad = complete_end = 0
    parser = claude_record if harness == "claude" else codex_record
    with path.open("rb") as fh:
        fh.seek(offset)
        complete_end = offset
        while True:
            line = fh.readline()
            if not line:
                break
            if not line.endswith(b"\n"):
                break
            complete_end = fh.tell()
            total += 1
            try:
                data = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                bad += 1
                continue
            try:
                records, update = parser(data)
            except (TypeError, ValueError):
                bad += 1
                continue
            chunks.extend(records)
            meta.update({k: v for k, v in update.items() if v not in (None, "")})
    return chunks, meta, complete_end, total, bad


def insert_chunks(conn, session_id: int, chunks) -> int:
    for ts, surface, text, extras in chunks:
        cur = conn.execute("INSERT INTO chunks(session_id,ts,surface,text) VALUES (?,?,?,?)",
                           (session_id, ts, surface, text))
        conn.execute("INSERT INTO chunks_fts(rowid,text) VALUES (?,?)", (cur.lastrowid, text))
        add_entities(conn, cur.lastrowid, text, extras)
    return len(chunks)


def delete_session_chunks(conn, session_id: int) -> None:
    conn.execute("DELETE FROM chunks_fts WHERE rowid IN (SELECT id FROM chunks WHERE session_id=?)", (session_id,))
    conn.execute("DELETE FROM entities WHERE chunk_id IN (SELECT id FROM chunks WHERE session_id=?)", (session_id,))
    conn.execute("DELETE FROM chunks WHERE session_id=?", (session_id,))


def ingest(args) -> int:
    claude_root, codex_root, db_path = paths()
    conn = connect(db_path)
    if args.rebuild:
        reset_schema(conn)
    else:
        try:
            existing = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        except sqlite3.OperationalError:
            existing = None
        if existing is not None and existing[0] != SCHEMA_VERSION:
            reset_schema(conn)
        else:
            create_schema(conn)
    summary = dict(seen=0, parsed=0, appended=0, tombstoned=0, errored=0, sessions=0, chunks=0)
    found: set[str] = set()
    started = time.monotonic()
    for harness, root in (("claude", claude_root), ("codex", codex_root)):
        for path in discover(root, harness):
            summary["seen"] += 1
            key = str(path.resolve())
            found.add(key)
            stat = path.stat()
            row = conn.execute("SELECT * FROM files WHERE path=?", (key,)).fetchone()
            current_fp = fingerprint(path, stat.st_size)
            mode = "new"
            if row and row["size"] == stat.st_size and row["mtime_ns"] == stat.st_mtime_ns and row["fingerprint"] == current_fp:
                if row["status"] == "tombstone":
                    conn.execute("UPDATE files SET status='ok' WHERE id=?", (row["id"],))
                continue
            if row and stat.st_size > row["size"]:
                # Verify the previously indexed prefix using its old size in the digest.
                mode = "append" if fingerprint(path, row["size"]) == row["fingerprint"] else "full"
            elif row:
                mode = "full"
            offset = int(row["parsed_offset"] or 0) if mode == "append" else 0
            parsed, meta, parsed_offset, total, bad = parse_file(path, harness, offset)
            status = "error" if total and bad / total > .5 else ("partial" if parsed_offset < stat.st_size else "ok")
            if status == "error": summary["errored"] += 1
            if row is None:
                cur = conn.execute("INSERT INTO files(path,harness,size,mtime_ns,fingerprint,parsed_offset,parser_version,status) VALUES (?,?,?,?,?,?,?,?)",
                    (key, harness, stat.st_size, stat.st_mtime_ns, current_fp, parsed_offset, PARSER_VERSION, status))
                file_id = cur.lastrowid
                cur = conn.execute("INSERT INTO sessions(file_id,harness,cwd,slot,git_branch,started_at,ended_at,n_turns,model,title,first_user_prompt) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (file_id, harness, None, None, None, None, None, 0, None, None, None))
                session_id = cur.lastrowid
            else:
                file_id = row["id"]
                session = conn.execute("SELECT id FROM sessions WHERE file_id=?", (file_id,)).fetchone()
                if session is None:
                    session_id = conn.execute("INSERT INTO sessions(file_id,harness,n_turns) VALUES (?,?,0)", (file_id, harness)).lastrowid
                else: session_id = session["id"]
                if mode == "full": delete_session_chunks(conn, session_id)
                conn.execute("UPDATE files SET harness=?,size=?,mtime_ns=?,fingerprint=?,parsed_offset=?,parser_version=?,status=? WHERE id=?",
                    (harness, stat.st_size, stat.st_mtime_ns, current_fp, parsed_offset, PARSER_VERSION, status, file_id))
            summary["chunks"] += insert_chunks(conn, session_id, parsed)
            old = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            timestamps = [x[0] for x in parsed if x[0] is not None]
            all_n = conn.execute("SELECT count(*) FROM chunks WHERE session_id=?", (session_id,)).fetchone()[0]
            if mode == "full":
                first_user = next((x[2] for x in parsed if x[1] == "user"), None)
                cwd, branch = meta.get("cwd"), meta.get("branch")
                started_at = min(timestamps) if timestamps else None
                ended_at = max(timestamps) if timestamps else None
                model, title = meta.get("model"), meta.get("title")
                meta_first, old_slot = meta.get("first_user_prompt", first_user), None
            else:
                first_user = next((x[2] for x in parsed if x[1] == "user"), old["first_user_prompt"])
                cwd, branch = meta.get("cwd", old["cwd"]), meta.get("branch", old["git_branch"])
                started_at = old["started_at"] if old["started_at"] is not None else (min(timestamps) if timestamps else None)
                ended_at = max([x for x in [old["ended_at"], *timestamps] if x is not None], default=None)
                model, title = meta.get("model", old["model"]), meta.get("title", old["title"])
                meta_first, old_slot = meta.get("first_user_prompt", first_user), old["slot"]
            slot_match = re.search(r"grep\d+", cwd or "")
            conn.execute("UPDATE sessions SET harness=?,cwd=?,slot=?,git_branch=?,started_at=?,ended_at=?,n_turns=?,model=?,title=?,first_user_prompt=? WHERE id=?",
                (harness, cwd, slot_match.group(0) if slot_match else old_slot, branch,
                 started_at, ended_at, all_n, model, title, meta_first, session_id))
            summary["sessions"] += 1
            summary["appended" if mode == "append" else "parsed"] += 1
    for row in conn.execute("SELECT id,path,status FROM files WHERE status != 'tombstone'"):
        if row["path"] not in found:
            conn.execute("UPDATE files SET status='tombstone' WHERE id=?", (row["id"],))
            summary["tombstoned"] += 1
    conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('last_ingest_at',?)", (str(time.time()),))
    conn.commit(); conn.close()
    print("files seen={seen} parsed={parsed} appended={appended} tombstoned={tombstoned} errored={errored}; sessions={sessions} chunks={chunks}; elapsed={elapsed:.3f}s".format(**summary, elapsed=time.monotonic()-started))
    return 0


def filters_sql(args):
    sql, params = ["f.status != 'tombstone'"], []
    if args.since: sql.append("c.ts >= ?"); params.append(iso(args.since))
    if args.until: sql.append("c.ts <= ?"); params.append(iso(args.until))
    if args.cwd: sql.append("s.cwd LIKE ?"); params.append("%" + args.cwd + "%")
    if args.branch: sql.append("s.git_branch LIKE ?"); params.append("%" + args.branch + "%")
    if args.harness: sql.append("s.harness = ?"); params.append(args.harness)
    return " AND ".join(sql), params


def query_terms(query: str) -> list[str]:
    return [x for x in re.findall(r"[A-Za-z0-9_./#-]+", query) if x]


def informative_terms(query: str) -> list[str]:
    """Terms suitable for broad retrieval; phrases retain the original wording."""
    result = []
    for term in query_terms(query):
        lower = term.lower()
        if lower in STOPWORDS or (len(lower) <= 2 and not any(c.isdigit() for c in lower)):
            continue
        if lower not in result:
            result.append(lower)
    return result


def phrase_queries(query: str) -> list[str]:
    """Safe FTS phrases for the whole query, quotes, and error-like word runs."""
    phrases = []

    def add(value: str) -> None:
        normalized = " ".join(query_terms(value))
        if normalized and normalized not in phrases:
            phrases.append(normalized)

    add(query)
    for quoted in re.findall(r"[\"']([^\"']+)[\"']", query):
        add(quoted)
    words = re.findall(r"\S+", query)
    # Error strings often have a short natural-language frame plus punctuation
    # (for example, "TypeError: expected str got None").  Preserve those runs.
    for width in range(3, min(8, len(words)) + 1):
        for start in range(0, len(words) - width + 1):
            run = words[start:start + width]
            if any(re.search(r"[^A-Za-z0-9]", word) for word in run):
                add(" ".join(run))
    return phrases[:12]


def identifier_terms(terms: list[str]) -> list[str]:
    return [term for term in terms if re.search(r"\d|[-_./#]", term)
            or re.fullmatch(r"[0-9a-f]{8,}", term)]


def search_rows(conn, args):
    terms = query_terms(args.query)
    informative = informative_terms(args.query)
    if not informative:
        return []
    where, params = filters_sql(args)
    match_count_sql = " + ".join(
        "CASE WHEN instr(lower(c.text), ?) > 0 THEN 1 ELSE 0 END" for _ in informative)
    common = """c.id,c.session_id,c.ts,c.surface,c.text,s.cwd,s.slot,s.git_branch,s.started_at,
                       f.path,{bm} AS bm,{matched} AS matched_count
                FROM chunks c JOIN sessions s ON s.id=c.session_id JOIN files f ON f.id=s.file_id"""
    fts_base = "SELECT " + common.format(bm="bm25(chunks_fts)", matched=match_count_sql) + \
        " JOIN chunks_fts ON c.id=chunks_fts.rowid"
    direct_base = "SELECT DISTINCT " + common.format(bm="-25.0", matched=match_count_sql) + \
        " JOIN entities e ON e.chunk_id=c.id"
    candidates: dict[int, tuple[sqlite3.Row, set[str]]] = {}

    def merge(rows, leg: str) -> None:
        for row in rows:
            existing = candidates.get(row["id"])
            if existing is None:
                candidates[row["id"]] = (row, {leg})
            else:
                best, legs = existing
                legs.add(leg)
                if float(row["bm"]) < float(best["bm"]):
                    candidates[row["id"]] = (row, legs)

    def fts(match: str, leg: str) -> None:
        sql = fts_base + " WHERE chunks_fts MATCH ? AND " + where + f" ORDER BY bm25(chunks_fts) LIMIT {FTS_LEG_LIMIT}"
        merge(conn.execute(sql, [*informative, match, *params]).fetchall(), leg)

    try:
        # Leg A: exact titles, quoted text, and error strings as complete phrases.
        for phrase in phrase_queries(args.query):
            fts('"' + phrase.replace('"', '""') + '"', "A")

        # Leg B: entity retrieval does not depend on the FTS rank cutoff.
        identifiers = identifier_terms(informative)
        if identifiers:
            entity_parts, entity_params = [], []
            for term in identifiers:
                entity_parts.append("lower(e.value)=?")
                entity_params.append(term)
                if re.fullmatch(r"[0-9a-f]{8,}", term):
                    entity_parts.append("lower(e.value) LIKE ?")
                    entity_params.append(term + "%")
            merge(conn.execute(direct_base + " WHERE (" + " OR ".join(entity_parts) + ") AND " + where +
                               f" LIMIT {FTS_LEG_LIMIT}", [*informative, *entity_params, *params]).fetchall(), "B")

        # Leg C: all informative words; this is the strongest broad retrieval leg.
        fts(" AND ".join('"' + term.replace('"', '""') + '"' for term in informative), "C")
        # Leg D: only broaden if the precise legs did not produce enough chunks.
        if len(candidates) < 3 * args.limit:
            fts(" OR ".join('"' + term.replace('"', '""') + '"' for term in informative), "D")
    except sqlite3.OperationalError:
        likes = " OR ".join("lower(c.text) LIKE ?" for _ in informative)
        fallback = "SELECT " + common.format(bm="0.0", matched=match_count_sql) + \
            " WHERE (" + likes + ") AND " + where + f" ORDER BY c.ts DESC LIMIT {FTS_LEG_LIMIT}"
        merge(conn.execute(fallback, [*informative, *("%" + term + "%" for term in informative), *params]).fetchall(), "D")
    now = time.time()
    result = []
    for row, legs in candidates.values():
        raw = max(0.01, -float(row["bm"]) + 1.0)
        raw *= {"user": 4.0, "assistant": 2.0, "tool_input": 1.5, "tool_output": 1.0}[row["surface"]]
        score = raw + (10.0 if "B" in legs else 0.0)
        score *= 1 / (1 + max(0, now - (row["ts"] or row["started_at"] or now)) / 86400 / 180)
        matched = [] if args.paths else [x for x in terms if x.lower() in row["text"].lower()]
        result.append((row, score, matched, legs, len(informative)))
    return result


def search(args) -> int:
    _, _, db = paths()
    if not db.exists():
        if not args.paths: print("Recall index does not exist; run `recall index` first.", file=sys.stderr)
        return 0
    conn = connect_ro(db)
    grouped = {}
    for row, score, matched, legs, informative_count in search_rows(conn, args):
        item = grouped.setdefault(row["session_id"], {"row": row, "best": (score, matched, legs, informative_count), "count": 0})
        item["count"] += 1
        if score > item["best"][0]: item["row"], item["best"] = row, (score, matched, legs, informative_count)
    ranked = []
    for item in grouped.values():
        best_row = item["row"]
        _, _, legs, informative_count = item["best"]
        enough_terms = best_row["matched_count"] >= math.ceil(.6 * informative_count)
        if not ({"A", "B"} & legs) and not enough_terms:
            continue
        score = item["best"][0] + .2 * math.log(1 + item["count"])
        ranked.append((score, item))
    ranked.sort(key=lambda x: x[0], reverse=True)
    for rank, (_, item) in enumerate(ranked[:args.limit], 1):
        row = item["row"]
        if args.paths:
            print(row["path"]); continue
        date = datetime.fromtimestamp(row["ts"] or row["started_at"] or 0, timezone.utc).isoformat()
        snippet = re.sub(r"\s+", " ", row["text"])[:200]
        why = "terms=" + ",".join(item["best"][1]) + "; legs=" + ",".join(sorted(item["best"][2]))
        print(f"{rank}. {row['path']}\n   {date} cwd={row['cwd'] or '-'} slot={row['slot'] or '-'} branch={row['git_branch'] or '-'}\n   [{row['surface']}] {snippet}\n   WHY: {why}")
    conn.close(); return 0


def direct_chunks(path: Path):
    harness = "codex" if path.name.startswith("rollout-") else "claude"
    return parse_file(path, harness)[0]


def show(args) -> int:
    _, _, db = paths(); target = Path(args.target).expanduser()
    if not target.exists() and args.target.isdigit() and db.exists():
        conn = connect_ro(db); row = conn.execute("SELECT f.path FROM sessions s JOIN files f ON f.id=s.file_id WHERE s.id=?", (int(args.target),)).fetchone(); conn.close()
        target = Path(row[0]) if row else target
    if not target.exists():
        print("session not found", file=sys.stderr); return 1
    chunks = direct_chunks(target)
    if args.around:
        point = iso(args.around); closest = min(range(len(chunks)), key=lambda i: abs((chunks[i][0] or 0)-point), default=0)
        chunks = chunks[max(0, closest-3):closest+4]
    for ts, surface, text, _ in chunks:
        if args.prompts and surface != "user": continue
        stamp = datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else "-"
        print(f"[{stamp}] {surface}: {text}")
    return 0


def related(args) -> int:
    _, _, db = paths()
    if not db.exists(): return 0
    conn = connect_ro(db)
    cwd = args.cwd or str(Path.cwd())
    branch = args.branch
    where, par = [], []
    if cwd: where.append("s.cwd LIKE ?"); par.append("%"+cwd+"%")
    if branch: where.append("s.git_branch LIKE ?"); par.append("%"+branch+"%")
    contexts = conn.execute("SELECT id FROM sessions s WHERE " + (" OR ".join(where) if where else "0") + " ORDER BY s.ended_at DESC LIMIT 50", par).fetchall()
    context_ids = [r[0] for r in contexts]
    values = set()
    if context_ids:
        marks = ",".join("?" * len(context_ids))
        values = {r[0] for r in conn.execute("SELECT DISTINCT e.value FROM entities e JOIN chunks c ON c.id=e.chunk_id WHERE c.session_id IN ("+marks+") AND e.kind='file_path' LIMIT 1000", context_ids)}
    candidates = conn.execute("SELECT s.id,f.path,s.cwd,s.git_branch,s.ended_at FROM sessions s JOIN files f ON f.id=s.file_id WHERE f.status != 'tombstone' ORDER BY s.ended_at DESC LIMIT 2000").fetchall()
    candidate_ids = [r["id"] for r in candidates]
    entity_map: dict[int, set[str]] = {}
    if values and candidate_ids:
        for start in range(0, len(candidate_ids), 500):
            batch = candidate_ids[start:start + 500]
            marks = ",".join("?" * len(batch))
            for entity_row in conn.execute("SELECT c.session_id,e.value FROM entities e JOIN chunks c ON c.id=e.chunk_id WHERE c.session_id IN ("+marks+") AND e.kind='file_path'", batch):
                entity_map.setdefault(entity_row[0], set()).add(entity_row[1])
    ranked = []
    for row in candidates:
        overlap = int(bool(cwd and cwd in (row["cwd"] or ""))) + int(bool(branch and branch == row["git_branch"]))
        if values:
            overlap += len(values & entity_map.get(row["id"], set()))
        if overlap:
            recency = 1 / (1 + max(0, time.time()-(row["ended_at"] or 0))/86400/180)
            ranked.append((overlap + recency, row, overlap))
    for _, row, overlap in sorted(ranked, reverse=True, key=lambda x: x[0])[:args.limit]:
        print(f"{row['path']}\toverlap={overlap}\tcwd={row['cwd'] or '-'}\tbranch={row['git_branch'] or '-'}")
    conn.close(); return 0


def doctor(args) -> int:
    claude, codex, db = paths()
    conn = None
    try:
        probe = sqlite3.connect(":memory:")
        probe.execute("CREATE VIRTUAL TABLE _fts_test USING fts5(x)")
        probe.close()
        print("OK FTS5 available")
    except sqlite3.DatabaseError as exc:
        print(f"HARD FAIL FTS5/database: {exc}"); return 1
    if db.exists():
        try:
            conn = connect_ro(db)
            schema = (conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone() or ["missing"])[0]
            print(f"OK db exists=True size={db.stat().st_size} schema_version={schema}")
            last = (conn.execute("SELECT value FROM meta WHERE key='last_ingest_at'").fetchone() or [None])[0]
            print("OK index age=" + (f"{(time.time()-float(last))/3600:.1f}h" if last else "unknown"))
            counts = dict(conn.execute("SELECT status,count(*) FROM files GROUP BY status").fetchall())
            print(f"OK files errored={counts.get('error',0)} partial={counts.get('partial',0)}")
            ledger = {r[0] for r in conn.execute("SELECT path FROM files WHERE status!='tombstone'")}
        except sqlite3.OperationalError:
            print("WARN index empty/not built metadata unavailable")
            print("WARN index empty/not built file ledger unavailable")
            ledger = set()
        except sqlite3.DatabaseError as exc:
            print(f"HARD FAIL db corrupt: {exc}")
            return 1
    else:
        print("WARN db exists=False size=0 schema_version=missing")
        print("WARN index empty/not built metadata unavailable")
        print("WARN index empty/not built file ledger unavailable")
        ledger = set()
    for name, root, harness in (("claude", claude, "claude"), ("codex", codex, "codex")):
        disk = {str(x.resolve()) for x in discover(root, harness)} if root.exists() else set()
        coverage = 100 * len(disk & ledger) / len(disk) if disk else 100
        print(f"{'OK' if root.exists() else 'WARN'} {name} root={root} files={len(disk)} coverage={coverage:.1f}%")
    disk_parent = db.parent
    while not disk_parent.exists() and disk_parent != disk_parent.parent:
        disk_parent = disk_parent.parent
    stat = os.statvfs(disk_parent); free = stat.f_bavail * stat.f_frsize
    print(f"{'WARN' if free < 20*1024**3 else 'OK'} free_disk_gb={free/1024**3:.1f}")
    settings = Path.home()/".claude/settings.json"; days = None
    try: days = json.loads(settings.read_text()).get("cleanupPeriodDays")
    except (OSError, json.JSONDecodeError): pass
    print(f"{'OK' if isinstance(days,(int,float)) and days >= 3650 else 'WARN'} cleanupPeriodDays={days}")
    manifests_dir = Path.home()/"archives/manifests"
    manifests = list(manifests_dir.glob("*.json")) if manifests_dir.exists() else []
    age = (time.time()-max(p.stat().st_mtime for p in manifests))/3600 if manifests else None
    print(f"{'WARN' if age is None or age > 48 else 'OK'} archives_manifest_age_hours={age:.1f}" if age is not None else "WARN archives_manifest_age_hours=missing")
    if conn is not None: conn.close()
    return 0


def run_eval(args) -> int:
    script = Path(__file__).resolve(); eval_script = script.parents[3] / "tests/eval/run_eval.py"
    command = f"{sys.executable} {script} search --paths {{query}} --since {{since}} --until {{until}} --cwd {{cwd}} --harness {{harness}}"
    return subprocess.call([sys.executable, str(eval_script), "--queries", str(eval_script.parent/"queries.jsonl"), "--split", args.split, "--searcher-cmd", command, "--out", args.out])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="recall")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("index"); p.add_argument("--rebuild", action="store_true"); p.set_defaults(func=ingest)
    p = sub.add_parser("search"); p.add_argument("query"); p.add_argument("--since"); p.add_argument("--until"); p.add_argument("--cwd"); p.add_argument("--branch"); p.add_argument("--harness", choices=("claude","codex")); p.add_argument("--limit", type=int, default=10); p.add_argument("--paths", action="store_true"); p.set_defaults(func=search)
    p = sub.add_parser("show"); p.add_argument("target"); p.add_argument("--around"); p.add_argument("--prompts", action="store_true"); p.set_defaults(func=show)
    p = sub.add_parser("related"); p.add_argument("--cwd"); p.add_argument("--branch"); p.add_argument("--limit", type=int, default=10); p.set_defaults(func=related)
    p = sub.add_parser("doctor"); p.set_defaults(func=doctor)
    p = sub.add_parser("eval"); p.add_argument("--split", default="dev", choices=("dev","holdout")); p.add_argument("--out", default="recall-eval.json"); p.set_defaults(func=run_eval)
    args = ap.parse_args(argv)
    try: return args.func(args)
    except ValueError as exc: ap.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
