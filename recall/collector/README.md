# Linux collectors

The collector discovers only Claude Code `*.jsonl` or Codex `rollout-*.jsonl` files under one
explicit root. Complete JSONL records enter a mode-0600 SQLite outbox before network I/O. The
scan offset may move once the spool transaction is durable; the separate committed offset moves
only after BrainStore returns a commit acknowledgement. A lost acknowledgement retries the same
idempotency key.

One process and source-scoped credential serve each harness:

```bash
python -m collector.cli run \
  --harness claude --root ~/.claude/projects \
  --source-id claude:linux:host \
  --spool ~/.local/state/recall-brain/collector-claude.db \
  --endpoint https://host.example.ts.net:9443 \
  --token-file ~/.config/recall-brain/collector-claude-token.json
```

`doctor` reports file coverage, parse/dead-letter rate, pending/acked records, committed files,
and acknowledgement latency p95. `locate --receipt ...` maps a central receipt back to its
original local file and exact byte window without retaining acknowledged envelope bodies.

Structured values whose keys name credentials—including `LITELLM_MASTER_KEY`—are replaced before
spooling. Non-JSONL files and paths outside the configured root are never discovered.
