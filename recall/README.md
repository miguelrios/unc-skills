# recall

It is Thursday. You ask your agent about the staging bug you both fixed last
month, and it starts grepping five gigabytes of session transcripts like a
detective who burned his own case files. Six rounds later: "could you tell me
which week that was?" You were there. It was there. Nobody remembers.

Your agent already wrote everything down — every session, every command, every
tool result, in transcripts it never reads again. **recall** is the hippocampus
bolted onto that pile: a local SQLite index over your Claude Code *and* Codex
session history, a search CLI tuned for how you actually ask ("the pod that
kept OOMing", "what did codex do on this branch"), and a session-start hook
that surfaces prior art before you ask at all.

```
you           ──ask──▶  /recall "the greptile review work from back in May"
recall        ──1 cmd──▶ ranked sessions + WHY each matched (0.2s warm)
your agent    ──reads──▶ just the winning window, never the 80MB file
```

## Unscientific stats, one dev box, receipts in-repo

- 5,959 transcripts / 2.5M chunks indexed in 400s; incremental after that.
- Retrieval eval (57 frozen known-answer queries, labels grep-verified):
  recall\@5 **0.63** / MRR\@10 **0.55** at **0.16s p95** — vs 0.57/0.53 at
  2.9s for raw grep + mtime sorting, 0.23/0.21 at 15.7s for the prompt-only
  predecessor. Same inputs, same scorer.
- Five real "the agent thrashed for 5-7 rounds" incidents replayed: rank-1
  hit from ONE command each.
- The honest number too: no-answer discipline is lexically bounded — queries
  about work that never happened can still surface adjacent sessions. The WHY
  line tells you what matched; embeddings are the phase-3 answer.

## What's in the box

- `skills/recall/SKILL.md` — the operator manual your agent reads.
- `skills/recall/scripts/recall.py` — the engine: stdlib-only Python, one
  SQLite file, FTS5 + entity index + evidence-tiered ranking. Transcripts stay
  the source of truth; the index is disposable and rebuildable.
- `skills/recall/scripts/recall-hook.sh` — SessionStart hook: prior-art block
  in ≤6 lines, fail-open (a broken index can never break a session), and a
  throttled background delta-index so freshness needs no daemon and no cron.
- Secret-shaped lines are redacted at ingest; thinking blocks are never
  stored; the index directory is 0700.
- `tests/` — unit tests plus a frozen retrieval eval with a held-out split,
  so ranking changes are measured, not vibed.

## Install

```bash
claude plugin marketplace add miguelrios/unc-skills
claude plugin install recall@unc-skills

# index your history (one-time backfill; minutes, not hours)
python3 ~/.claude/skills/recall/scripts/recall.py index

# optional: the session-start prior-art hook
./install.sh --hook     # prints the settings.json snippet to add
```

## Requirements

- Python 3.10+ with SQLite FTS5 (stock on Debian/Ubuntu/macOS).
- Claude Code and/or Codex CLI session history on disk.
- Linux/macOS. `recall doctor` checks all of it and tells you what's missing.

## When to use it

- "Did we already fix this?" — before re-fixing it.
- Continuing yesterday's half-finished branch from a fresh session.
- Cross-tool archaeology: Claude asking what Codex did, and vice versa.
- Turning "that thing that worked" into a skill instead of a legend.

*Moral: an agent that reads its own diary stops introducing itself to its own
work.*

Credits: retrieval architecture informed by [garrytan/gbrain](https://github.com/garrytan/gbrain)
(deterministic substrate, hybrid ranking, doctor/eval as first-class); the
session-catalog pattern borrowed from Codex's own `state_5.sqlite`.

MIT © Miguel Rios
