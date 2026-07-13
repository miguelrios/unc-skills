---
name: recall
description: Find prior Claude Code and Codex sessions with an indexed local search engine, then continue the work, repeat it with fresh inputs, or distill it into a skill. Runs from Claude Code, Codex, or pi; the current index covers Claude Code and Codex transcripts. Use when the user names Recall, says "find that conversation where…", "what did we do last time about…", "continue what we did yesterday on X", "what did codex do on this branch", "turn what we did about Y into a skill", or "remember when you…". Not for searching code (use grep on the repo) or for facts already in MEMORY.md.
---

# /recall — session memory engine

Claude Code and Codex sessions on this machine are indexed into a local
SQLite engine. You do not grep transcripts; you query the index and read only
the winning session. All commands go through one CLI:

```bash
python3 scripts/recall.py <command>       # relative to this skill directory
```

## Local, central, and shadow modes

With no central configuration, every command behaves exactly as the local engine described below.
Setting `RECALL_URL` selects the tailnet central service for read commands (`search`, `show`,
`related`, and `doctor`). The same flags and output shapes remain valid, and every displayed remote
hit includes its resolvable receipt on the `WHY` line.

Use `RECALL_MODE=local|remote|shadow` when the mode must be explicit:

- `local` is the config-only rollback switch and never calls the central service.
- `remote` fails closed on transport/auth errors; it never silently returns stale local results.
- `shadow` returns the local result while recording a receipt-level local/central comparison under
  `~/.recall/shadow.jsonl` (override with `RECALL_SHADOW_LOG`).

Interactive tailnet access uses the Tailscale identity boundary. If a scoped bearer is required,
set `RECALL_TOKEN_FILE` to a mode-`0600` JSON file containing `{"token":"..."}`. Never put a token
in `RECALL_URL`, shell history, a repository, or evidence. `index` remains local in every mode, so
switching read modes cannot rewrite either the local SQLite index or central canonical events.

## First: pick the outcome

1. **Find / verify** — answer "did we…", "which session…", "how did we…".
   Search, read the best hit's relevant window, answer with the session path
   as the receipt.
2. **Continue** — resume in-progress work. Needs the session's tail plus its
   branch and worktree.
3. **Repeat** — redo the same kind of task with fresh inputs. Needs the
   original driving prompts, verbatim.
4. **Skill-ify** — turn the recipe into a reusable skill. Needs the steps that
   worked, minus one-off data. Chain into the harness's skill creator when one
   is installed; otherwise write the standard `SKILL.md` package directly.

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
  | "in the other worktree/checkout" | `--cwd <any-cwd-substring>` |
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
dates) → invoke the available skill creator with the recipe and a proposed
name, or create a standard Agent Skills directory when none is installed.

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
- Running Recall from pi is supported, but pi's own session format is not yet
  indexed. Do not claim that a cold result proves no pi session exists.
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
