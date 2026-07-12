---
name: recall
description: Find prior AI-coding sessions — Claude Code and Codex — with an indexed local search engine, then continue the work, repeat it with fresh inputs, or distill it into a skill. Use when the user says /recall, "find that conversation where…", "what did we do last time about…", "continue what we did yesterday on X", "what did codex do on this branch", "turn what we did about Y into a skill", or "remember when you…". Not for searching code (use grep on the repo) or for facts already in MEMORY.md.
---

# /recall — session memory engine

Every Claude Code and Codex session on this machine is indexed into a local
SQLite engine. You do not grep transcripts; you query the index and read only
the winning session. All commands go through one CLI:

```bash
python3 scripts/recall.py <command>       # relative to this skill directory
```

## First: pick the outcome

1. **Find / verify** — answer "did we…", "which session…", "how did we…".
   Search, read the best hit's relevant window, answer with the session path
   as the receipt.
2. **Continue** — resume in-progress work. Needs the session's tail plus its
   branch and worktree.
3. **Repeat** — redo the same kind of task with fresh inputs. Needs the
   original driving prompts, verbatim.
4. **Skill-ify** — turn the recipe into a reusable skill. Needs the steps that
   worked, minus one-off data. Chain into `skill-creator` at the end.

Ask only if the outcome is genuinely ambiguous.

## Search

```bash
python3 scripts/recall.py search "<what the user said>" [filters]
```

- Pass the user's phrasing plus any identifier you have — identifiers (job
  UUIDs, PR numbers, pod names, error strings, filenames) are the strongest
  evidence and are matched exactly, including inside tool output.
- Filters are mechanical — apply them, never approximate them in the query
  text:
  | ask | flag |
  |---|---|
  | "last 48h", "back in May" | `--since 2026-05-01 --until 2026-06-01` (UTC, both bounds inclusive of the instant — to cover a full local day, use the NEXT day's date as `--until`; convert the user's local day first) |
  | "in the other worktree", "on slot 5" | `--cwd grep5` (any cwd substring) |
  | "what did codex do" | `--harness codex` |
  | branch-scoped | `--branch <substring>` |
- Output is ranked sessions with date, cwd, slot, branch, a matched snippet,
  and WHY it matched. Empty output means nothing cleared the evidence gate —
  NOT proof the work never happened: retry once with a distinctive identifier
  or a wider window; if still empty, tell the user what you searched and that
  the trail is cold.
- `--paths` prints bare file paths (for scripting); `--limit N` widens.

## Read the best-supported hit

Check the WHY line on the top few results first — if the matched terms are all
generic words, the evidence is weak; open the result whose WHY carries an
identifier, phrase, or exact-entity match, not blindly rank 1.

```bash
python3 scripts/recall.py show <path> --prompts                    # user prompts only
python3 scripts/recall.py show <path> --around 2026-07-03T14:20    # ±3-turn window; use the date printed in the search result
python3 scripts/recall.py show <path> --tail 30                    # the session's final turns (for Continue)
```

Pass the session file path from the search output. Never cat a transcript —
sessions reach 80 MB; `show` parses and prints only what you asked for.

## Related work (no query needed)

```bash
python3 scripts/recall.py related --cwd "$(pwd)" --branch "$(git branch --show-current)"
```

Sessions sharing this project, branch, or touched files — ranked by overlap
and recency. Use at session start when the user references prior work without
naming it.

## Outcome playbooks

**Find / verify** — search → `show --around` the matched timestamp → answer
with evidence. Two commands, usually.

**Continue** — search → `show --tail 30` for the final state (last actions,
tool results, open errors) → check the session's branch/slot still exists
(`git -C <cwd> branch --show-current`) → summarize: "Found `<session>` in
`<cwd>` on `<date>`, last action `<x>`, branch `<b>` — resume there or here?"

**Repeat** — search → `show --prompts` → present the driving prompts verbatim
and confirm fresh inputs (dates, scope) before re-running.

**Skill-ify** — search → `show` the working window → separate the durable
recipe (commands, endpoints, auth patterns) from one-off data (specific IDs,
dates) → invoke `skill-creator` with the recipe and a proposed name.

## Index health

```bash
python3 scripts/recall.py index      # incremental; run if results look stale
python3 scripts/recall.py doctor     # coverage, index age, retention watchdog
```

`doctor` warning about `cleanupPeriodDays` means transcript retention got
re-enabled — surface that to the user immediately; history is being deleted.

## Gotchas

- The engine indexes user text, assistant text, and tool input/output — but
  reasoning/thinking blocks are never stored, and secret-shaped lines are
  redacted at ingest. If the only trace of something was a thinking block, it
  is not findable.
- Codex sessions are one file per rollout under a date tree; `show` handles
  both schemas transparently.
- A query about work that never happened can still return lexically-adjacent
  sessions. The ranked WHY line tells you what actually matched — read it
  before asserting the session answers the question.
- Subagent and workflow transcripts are indexed as their own sessions and
  live under the parent session's directory (`<session-uuid>/subagents/…`) —
  the path itself tells you which main session spawned them.

## References

- [references/query-cookbook.md](references/query-cookbook.md) — worked
  examples per stratum: identifiers, error strings, time windows,
  cross-worktree, cross-harness, paraphrase.
- [references/eval.md](references/eval.md) — the frozen retrieval eval: how to
  run it, how to add queries without contaminating the holdout.
