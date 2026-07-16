# Tailnet-private pilot deployment

The application listens on a Unix socket, not TCP. Tailscale Serve is the only network proxy.
On Linux, the server verifies `SO_PEERCRED` and trusts Tailscale identity headers only when the
Unix-socket peer UID is explicitly allowlisted. Ubuntu's sandboxed `tailscaled` uses UID 65534;
root is UID 0. Neither identity is assumable by the interactive user, so a same-user process that
connects directly and forges `Tailscale-User-Login` is rejected. Narrow
`RECALL_TRUSTED_PROXY_UIDS` to the UIDs observed for the local `tailscaled` service.

Install from an immutable, reviewed checkout at `~/services/recall-brain`. The service unit never
points at a contributor's active checkout.

```bash
git worktree add --detach ~/services/recall-brain <reviewed-merged-sha>
python3 -m venv ~/.config/recall-brain/venv
~/.config/recall-brain/venv/bin/pip install -r ~/services/recall-brain/recall/server/requirements.txt
docker pull ghcr.io/huggingface/text-embeddings-inference@sha256:ad950d30878eceb72aaf32024d26fa2b1d04a75304fa0b4776b49aa1941fea07
install -m 0600 ~/services/recall-brain/recall/server/deploy/service.env.example ~/.config/recall-brain/service.env
install -m 0644 ~/services/recall-brain/recall/server/deploy/recall-brain.service ~/.config/systemd/user/
install -m 0644 ~/services/recall-brain/recall/server/deploy/recall-brain-backup.service ~/.config/systemd/user/
install -m 0644 ~/services/recall-brain/recall/server/deploy/recall-brain-backup.timer ~/.config/systemd/user/
install -m 0644 ~/services/recall-brain/recall/server/deploy/recall-embedding.service ~/.config/systemd/user/
install -m 0644 ~/services/recall-brain/recall/server/deploy/recall-embedding-backfill.service ~/.config/systemd/user/
install -m 0644 ~/services/recall-brain/recall/server/deploy/recall-embedding-backfill.timer ~/.config/systemd/user/
# Fill in service.env, then apply every schema before starting services or timers.
set -a; source ~/.config/recall-brain/service.env; set +a
cd ~/services/recall-brain/recall/server
~/.config/recall-brain/venv/bin/python -m recall_server.cli migrate
systemctl --user daemon-reload
systemctl --user enable --now recall-embedding
systemctl --user enable --now recall-brain
systemctl --user enable --now recall-brain-backup.timer
systemctl --user enable --now recall-embedding-backfill.timer
tailscale serve --bg --https=9443 unix:/run/user/$(id -u)/recall-brain.sock
```

Do not use Funnel. Preserve unrelated Serve listeners by configuring only the dedicated 9443
listener. Collectors receive revocable tokens from `recall_server.cli token-create`; plaintext
is emitted once and only its SHA-256 is stored. Use `--output /secure/mode-0600-file.json` so the
plaintext never enters terminal or session logs; the command refuses to overwrite an existing file.

The pilot uses a five-minute logical-backup timer for a bounded RPO. Before multi-user scale or
C10 production cutover, replace it with continuous WAL archival plus daily base backups; the
same blank-database restore/fingerprint contract remains the gate.

Searches have a 300ms database-work budget by default. Override it only within the validated
10–2000ms range with `RECALL_SEARCH_DEADLINE_MS`; the response and service log expose only
content-free per-leg timings, result counts, and the deadline outcome.

Semantic retrieval requires PostgreSQL with pgvector and a loopback-only embedding sidecar. The
packaged unit pins TEI 1.9 and Qwen3-Embedding-0.6B, binds only `127.0.0.1:8089`, and never exposes
an embedding route through Tailscale Serve. Keep `RECALL_EMBEDDING_BATCH_SIZE=1`: the derivation
fingerprint and backfill deliberately trade background throughput for reproducible Qwen document
vectors. The runtime verifies the exact model commit and float32 dtype against TEI `/info` before
sending text. Search ignores stale fingerprints, dimensions, projector versions, and content hashes.

Query planning is optional but, when enabled, must use the staging LiteLLM HTTPS router plus a
short-lived model-scoped virtual key in a non-symlink owner-only file. A separate secret-manager
timer must atomically replace that file before expiry. Never place a LiteLLM master key in
`service.env`, pass it to Recall, or call a model provider directly. Recall rereads the key on every
uncached plan and keeps only bounded hash-keyed in-memory caches; it does not persist query text or
planner output. Set `RECALL_LITELLM_APPROVED_URL` to the exact same approved staging-router base URL;
startup fails if the planner points anywhere else.

After schema 011, converge the derived embedding projection online. The timer holds a dedicated
advisory lock, processes bounded batches, and is safe to replay. It never rewrites canonical events,
items, or receipts:

```bash
RECALL_DATABASE_URL=... RECALL_EMBEDDING_URL=http://127.0.0.1:8089 \
  python -m recall_server.cli backfill-embeddings --batch-size 128
```

`recall_embedding_lag` must reach zero before semantic retrieval is considered ready. The service
continues exact and lexical retrieval if the local sidecar or scoped planner is unavailable; stale
vectors are never searched.

Federated ranking uses explicit host-owned source profiles. Ingest envelopes and model tools
cannot set family, quality, or freshness policy. After a source has ingested at least one event,
an operator may configure it through the database-local admin CLI:

```bash
RECALL_DATABASE_URL=... python -m recall_server.cli source-profile-set SOURCE_ID \
  --family coding_history --quality trusted --freshness-half-life-days 180
RECALL_DATABASE_URL=... python -m recall_server.cli federation-scoreboard
```

Families and quality levels are closed enums. Search results add a content-free source profile
receipt plus bounded lexical, freshness, quality, and cross-family corroboration components.
The scoreboard reports aggregates only: it never returns source IDs, query text, or item text.
Unprofiled sources remain explicitly `unclassified`/`unrated`; the server never guesses a profile
from source-name patterns.

After applying schema 005 to an existing brain, backfill the rebuildable entity projection
online. The command commits bounded batches, holds a dedicated advisory lock, resumes from its
watermark after interruption, and does not rewrite canonical events or items:

```bash
RECALL_DATABASE_URL=... python -m recall_server.cli backfill-entities --batch-size 5000
```

Do not substitute the full `rebuild` command for this live migration; rebuild intentionally
truncates all derived projections inside one transaction and is reserved for offline recovery.

After upgrading to projector version 3, converge existing derived text on the current privacy
contract with the resumable redaction backfill. It snapshots the current item high-water mark,
rewrites only derived items/chunks/entities whose redacted form changes, and never mutates canonical
source events or receipts:

```bash
RECALL_DATABASE_URL=... python -m recall_server.cli backfill-redaction --batch-size 5000
```

The default is single-process. On a dedicated multi-core maintenance host, `--workers N` (maximum
32) parallelizes only the pure redaction computation; database reads, writes, watermarks, and the
advisory lock remain single-owner and ordered.

After schema 009, repair legacy Cowork messages that were projected as one session per message.
This migration only moves derived item/session relationships; canonical events, revisions, content
digests, and item receipts remain unchanged. It is high-water bounded, resumable, and idempotent:

```bash
RECALL_DATABASE_URL=... python -m recall_server.cli backfill-cowork-sessions --batch-size 5000
```

Configure owner-controlled aliases only after their exact source exists. Search routing by source
ID, source family, or alias always intersects with a source-scoped credential:

```bash
RECALL_DATABASE_URL=... python -m recall_server.cli source-alias-set cowork cowork:mac:owner
```

Linux history collectors use `recall-collector@.service` with separate `claude` and `codex`
environment/token files. Issue one source-scoped credential per unit, install the two example
environment files with mode 0600 after replacing every value, then enable the instances:

```bash
install -m 0644 ~/services/recall-brain/recall/server/deploy/recall-collector@.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now recall-collector@claude recall-collector@codex
```

Back up and run a blank-database restore proof:

```bash
RECALL_DATABASE_URL=... recall/server/scripts/backup_restore.sh backup /secure/backup/dir
RECALL_RESTORE_DATABASE_URL=... recall/server/scripts/backup_restore.sh restore-test /secure/backup/dir
```
