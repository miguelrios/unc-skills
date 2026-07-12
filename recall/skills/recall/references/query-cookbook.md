# Query cookbook

Worked examples per query shape. All commands relative to the skill directory;
`R="python3 scripts/recall.py"`.

## Exact identifier (strongest evidence — always include it)

```bash
$R search "api-prod-6fcdc84dd4-mmjpj OOM"          # pod name
$R search "d7d212df heartbeat job"                  # 8-hex uuid prefix works
$R search "#6644"                                   # PR number
$R search "grep-expert-base-v5"                     # snapshot/artifact name
```

Identifiers are matched exactly via the entity index and a dedicated token
leg — they surface even when they only ever appeared inside tool output.

## Error strings (quote the distinctive run of words)

```bash
$R search "canceling statement due to lock timeout"
$R search "driver failed programming external connectivity"
```

The full string is tried as a phrase before term matching; paste it verbatim.

## Time windows (mechanical, never narrated)

```bash
$R search "greptile review" --since 2026-05-01 --until 2026-06-01
```

Timestamps are UTC — convert the user's local "yesterday"/"last week" before
setting the window, and prefer a window one day wider on each side.

## Cross-worktree / cross-harness

```bash
$R search "flue harness origin" --cwd grep5        # work done in another slot
$R search "hermes agent cron telegram" --harness codex
$R related --cwd ~/worktrees/pool/grep5/parcha     # no query at all
```

## Paraphrase (you remember the shape, not the words)

```bash
$R search "sandboxes kept timing out on the first command after a cold start"
```

Works when enough content words overlap. If it returns nothing or junk: add
any identifier you can recover (a filename, a branch word, an error fragment)
— one identifier converts a fuzzy query into an exact one. The WHY line shows
which terms carried each hit; if the WHY terms are all generic words, treat
the result as weak.

## Scripting

```bash
$R search "…" --paths --limit 20     # bare paths, one per line, exit 0
```

Empty stdout = confident no-answer. Non-zero exit = the engine itself failed.
