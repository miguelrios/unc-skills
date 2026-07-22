# Portable collectors

The collector discovers only Claude Code `*.jsonl` or Codex `rollout-*.jsonl` files under one
explicit root. Complete JSONL records enter a mode-0600 SQLite outbox before network I/O. The
scan offset may move once the spool transaction is durable; the separate committed offset moves
only after BrainStore returns a commit acknowledgement. A lost acknowledgement retries the same
idempotency key.

One process and source-scoped credential serve each harness on Linux or macOS:

```bash
python -m collector.cli run \
  --harness claude --root ~/.claude/projects \
  --source-id claude:linux:host \
  --spool ~/.local/state/recall-brain/collector-claude.db \
  --endpoint https://host.example.ts.net:9443 \
  --token-file ~/.config/recall-brain/collector-claude-token.json \
  --visibility private
```

Each scan invocation is resumably bounded to 1,000 complete JSONL records or 20
seconds by default. Override these closed deployment bounds with
`--max-scan-records` and `--max-scan-seconds`. A bounded slice retains its exact
byte checkpoint and scan membership, never infers deletion from unvisited files,
and resumes before it advances to later work. An unchanged rerun reads no record
bodies and queues no versions.

`doctor` reports file coverage, parse/dead-letter rate, pending/acked records,
committed files, acknowledgement latency p95, last successful Brain ACK, bounded
scan completion, and stable runtime error state. `locate --receipt ...` maps a central receipt back to its
original local file and exact byte window without retaining acknowledged envelope bodies.
Transport batches are capped at 8 MB under the server's authenticated 12 MB request ceiling.
An oversized row retains its source coordinates and is losslessly recoverable with `recover` after
a limit upgrade; it is never counted as acknowledged while dead.

Large backfills may run non-overlapping `flush --shard-count N --shard-index I` workers. Sharding
is a stable hash of the source path modulo `N`, so one transcript/session is owned by exactly one
worker and cannot deadlock its projection rows; the steady-state watcher remains a single worker.

Structured values whose keys name credentials—including `LITELLM_MASTER_KEY`—are replaced before
spooling. Non-JSONL files and paths outside the configured root are never discovered.

Use `--privacy-mode scrub` to retain safe context with category-labelled
redactions or `--privacy-mode drop` to omit a classified record before it enters
the outbox. The default is explicit `off`. Scan and doctor output show the mode and
content-free action/category counts. A one-time secure compaction protects pending
rows when privacy is enabled on an older spool. See `client/README.md` for preview,
agentic-judge consent and routing, deletion limits, and rollback.

The reproducible macOS bundle in `client/` uses this exact collector class and
spool. Its LaunchAgents resolve source-scoped credentials from Keychain at run
time; the Linux token-file path remains mode-0600 and is never packaged.
