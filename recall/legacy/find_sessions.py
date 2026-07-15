#!/usr/bin/env python3
"""Find Claude Code session transcripts that match keyword + optional time/cwd filters.

Walks ~/.claude/projects/**/*.jsonl, opens each file, and reads line-by-line as JSON
(grep is unreliable when user prompts contain escaped newlines or quotes). Returns the
paths ranked by count of matching user prompts, plus a short preview of the first match.

Usage:
    find_sessions.py --keyword "proj-grep"
    find_sessions.py --keyword "honor-report-path" --since 2026-04-16 --until 2026-04-18
    find_sessions.py --keyword "slack" --cwd-filter grep5
    find_sessions.py --keyword "skill" --only-user  # default: search user prompts only
    find_sessions.py --keyword "skill" --include-assistant  # also match assistant text
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECTS_ROOT = Path.home() / ".claude" / "projects"


def parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        # Accept "2026-04-18" or "2026-04-18T07:00:00" etc.
        if "T" not in s:
            s = s + "T00:00:00"
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def scan_file(path: Path, needle: str, since: datetime | None, until: datetime | None,
              include_assistant: bool) -> tuple[int, str | None, str | None]:
    """Return (match_count, first_match_timestamp, first_match_snippet)."""
    needle_lc = needle.lower()
    count = 0
    first_ts: str | None = None
    first_snippet: str | None = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                typ = d.get("type")
                if typ not in ("user", "assistant"):
                    continue
                if typ == "assistant" and not include_assistant:
                    continue
                ts_str = d.get("timestamp")
                if since or until:
                    if not ts_str:
                        continue
                    try:
                        ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).astimezone(timezone.utc)
                    except ValueError:
                        continue
                    if since and ts_dt < since:
                        continue
                    if until and ts_dt >= until:
                        continue
                msg = d.get("message", {})
                content = msg.get("content")
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    parts = []
                    for b in content:
                        if isinstance(b, dict):
                            if b.get("type") == "text" and isinstance(b.get("text"), str):
                                parts.append(b["text"])
                    text = " ".join(parts)
                if not text:
                    continue
                if needle_lc in text.lower():
                    count += 1
                    if first_ts is None:
                        first_ts = ts_str
                        first_snippet = text[:200].replace("\n", " ⏎ ")
    except OSError:
        pass
    return count, first_ts, first_snippet


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--keyword", required=True, help="Substring to search (case-insensitive)")
    ap.add_argument("--since", default=None, help="ISO date/datetime lower bound (UTC)")
    ap.add_argument("--until", default=None, help="ISO date/datetime upper bound exclusive (UTC)")
    ap.add_argument("--cwd-filter", default=None,
                    help="Substring that must appear in the cwd-mangled directory name (e.g. 'grep5')")
    ap.add_argument("--include-assistant", action="store_true",
                    help="Also match assistant messages (default: user prompts only)")
    ap.add_argument("--top", type=int, default=15, help="Show top N ranked sessions (default 15)")
    args = ap.parse_args()

    since = parse_iso(args.since) if args.since else None
    until = parse_iso(args.until) if args.until else None

    if not PROJECTS_ROOT.exists():
        print(f"error: {PROJECTS_ROOT} does not exist", file=sys.stderr)
        return 1

    candidates: list[Path] = []
    for project_dir in PROJECTS_ROOT.iterdir():
        if not project_dir.is_dir():
            continue
        if args.cwd_filter and args.cwd_filter not in project_dir.name:
            continue
        # main session transcripts
        for p in project_dir.glob("*.jsonl"):
            candidates.append(p)
        # subagent transcripts
        for p in project_dir.glob("*/subagents/*.jsonl"):
            candidates.append(p)

    results: list[tuple[int, Path, str | None, str | None]] = []
    for p in candidates:
        count, ts, snippet = scan_file(p, args.keyword, since, until, args.include_assistant)
        if count > 0:
            results.append((count, p, ts, snippet))

    results.sort(key=lambda r: (-r[0], r[2] or ""))

    if not results:
        print("(no matches)", file=sys.stderr)
        return 2

    for _i, (count, p, ts, snippet) in enumerate(results[: args.top]):
        rel = p.relative_to(Path.home())
        print(f"[{count:3d} matches] {ts or '?':<28} ~/{rel}")
        if snippet:
            print(f"              {snippet}")
    if len(results) > args.top:
        print(f"... ({len(results) - args.top} more)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
