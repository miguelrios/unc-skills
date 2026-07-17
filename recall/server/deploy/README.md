# Tailnet-private pilot deployment

## Managed Core preview

`recall-core` is the existing API, projection, and retrieval runtime packaged as one
non-root container. The deployment preview is intentionally offline and non-mutating:

```bash
python -m recall_server.cli deployment-preview \
  --manifest server/deploy/recall-core.plan.example.json
```

It emits only a content-free plan hash, resource kinds, and the five approvals still
required. It does not contact a provider, read a source, render a reference, or apply
infrastructure. The example is synthetic; a live manifest belongs in a private mode-0600
location and contains references, never credential values.

The production database gate requires a standard PostgreSQL URL with
`sslmode=verify-full` and an explicit trust root, schema migrations 1 through 14,
pgvector 0.8.0 or newer, and a runtime role without superuser, database/role creation,
replication, or RLS-bypass privilege:

```bash
python -m recall_server.cli capability-check
```

`--profile local-fixture` is a visibly non-production exception restricted to a
loopback PostgreSQL fixture. It never reports production readiness.

The separate approval document is owner-only, bound to the exact preview hash, and
contains only explicit booleans plus the approved billing and region slugs. Validate it
without applying anything:

```bash
python -m recall_server.cli deployment-approval-check \
  --manifest server/deploy/recall-core.plan.example.json \
  --approvals /private/approvals.json
```

Infrastructure reconciliation remains impossible until billing, region, provider
authorization, and the Tailnet route are all approved. Writer cutover is a separate
approval and is never inferred from infrastructure approval. Provider adapters receive
only the closed desired state and return content-free receipts; repeated reconciliation
must converge to `unchanged` without duplicate resources.

The managed pilot profile is deliberately one stack:

```text
Grep/Codex/Claude/Mac collectors
              |
       Tailnet HTTPS :9443
              |
  Render private Tailscale gateway
              |
      Render private network
              |
      Recall Core :8788 -------- HTTPS --------> managed embeddings
              |
 PlanetScale Postgres (Virginia, HA, bounded autoscaling)
```

There is no Render public web service and no Tailscale Funnel. Core requires a
revocable bearer credential even after the Tailnet boundary. Grep agents use the
stable MCP Streamable HTTP endpoint at `https://<tailnet-host>:9443/mcp`; the
same bearer token and source scope apply to both MCP tools and REST reads.
`recall_search`, `recall_related`, and `recall_show` are read-only. Browser
clients must match `RECALL_MCP_ALLOWED_ORIGINS`; server-side agents omit
`Origin`.

The live adapter profile pins PlanetScale `PS_80` with two replicas, 50 GiB
initial storage, a 1 TiB autoscaling ceiling, PostgreSQL 17, and the current
PlanetScale Virginia slug `us-east`. It creates only two digest-pinned Render
private services: Starter Core and a Starter Tailscale gateway with a 1 GiB
identity disk. The manifest selects a managed `voyage` or OpenAI-compatible
embedding endpoint over exact-match HTTPS; no dedicated embedding service is
created. Existing service environment variables, secret files, image digests,
commands, plans, disks, and regions must match exactly or reconciliation fails
without mutation.

Hosted embeddings receive the redacted text projection selected for semantic
indexing. The example uses `voyage-4` at 512 dimensions; operators who cannot
send that projection to a provider should use the self-hosted TEI profile in
the existing-host section instead. The managed profile uses the validated
2,000ms database-work ceiling because a remote database round trip cannot meet
the 300ms loopback default reliably at multi-million-item scale.

Inject credentials from the approved 1Password Environment at runtime. Never
put their values in arguments, a manifest, an approval file, shell history, or
the repository:

```text
PLANETSCALE_SERVICE_TOKEN_ID
PLANETSCALE_SERVICE_TOKEN
RENDER_API_KEY
RECALL_DATABASE_URL
RECALL_EMBEDDING_API_KEY
TAILSCALE_OAUTH_CLIENT_ID
TAILSCALE_OAUTH_CLIENT_SECRET
```

`RECALL_DATABASE_URL` must be a PlanetScale application role URL with
`sslmode=verify-full` and an explicit trust root. Prefer
`sslrootcert=/etc/ssl/certs/ca-certificates.crt` in the pinned Linux container;
`sslrootcert=system` is also accepted where the runtime's libpq/OpenSSL build
resolves the OS trust store correctly. Bootstrap and migrate the database with
a separate administrative credential, then retain only this least-privilege
runtime URL in the injected environment. The provider token needs only database
read/create permissions; the Tailscale OAuth client must be restricted to the
dedicated gateway tag.

When migration and runtime use separate PostgreSQL roles, refresh runtime grants
after every migration and before deleting a short-lived migration role. Replace
`recall_runtime` with the actual runtime role identifier:

```sql
GRANT USAGE ON SCHEMA public TO recall_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE
  ON ALL TABLES IN SCHEMA public TO recall_runtime;
GRANT USAGE, SELECT
  ON ALL SEQUENCES IN SCHEMA public TO recall_runtime;
```

Apply only the grants the enabled runtime operations need. Reassign objects to the
durable owner before deleting a temporary migration role.

After reviewing the zero-network preview and mode-0600 approval document, run
the exact approved apply under 1Password injection:

```bash
OP_CACHE=false op run --environment "$APPROVED_ENV_ID" -- \
  python -m recall_server.cli deployment-apply \
  --manifest /private/recall-core.plan.json \
  --approvals /private/approvals.json \
  --planetscale-organization ORGANIZATION \
  --database-name DATABASE \
  --render-owner-id WORKSPACE_ID \
  --core-name RECALL_CORE \
  --gateway-name RECALL_GATEWAY \
  --tailnet-hostname RECALL \
  --tailnet-tag tag:recall
```

The command checks infrastructure approvals before reading any credential. Its
stdout is content-free: actions, plan hash, and non-reversible resource
receipts only. Writer cutover remains a separate approval.

## Existing host pilot

The existing host pilot listens on a Unix socket, not TCP. Tailscale Serve is its only network proxy.
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
install -m 0644 ~/services/recall-brain/recall/server/deploy/recall-embedding-backfill-sidecar.service ~/.config/systemd/user/
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
10–5000ms range with `RECALL_SEARCH_DEADLINE_MS`; the response and service log expose only
content-free per-leg timings, result counts, and the deadline outcome.

Semantic retrieval requires PostgreSQL with pgvector and one explicitly selected embedding
profile. Cosine score distributions vary by model, so
`RECALL_SEMANTIC_MINIMUM_SIMILARITY` is an explicit validated deployment setting in the
0–1 range and defaults to `0.35`. Calibrate it with a private retrieval eval when changing
models; the bounded top-K candidate pool prevents a lower floor from creating an unbounded
scan. Recall supports three protocols:

- `voyage` is the recommended hosted profile. `voyage-4` supports 512-dimensional output and
  distinct `document`/`query` retrieval modes.
- `openai` calls the standard `/v1/embeddings` contract and works with OpenAI-compatible hosted or
  local services. Optional document/query prefixes support asymmetric open models such as Nomic.
- `tei` preserves the pinned local Qwen profile below for operators who prioritize private
  inference and accept its compute footprint.

Leaving `RECALL_EMBEDDING_URL` unset is a supported zero-dependency lexical-only profile. This is
degraded retrieval, not failed startup. Hosted profiles send projected memory text to the selected
provider; operators must make that privacy choice deliberately.

Every non-loopback endpoint must use HTTPS, exactly match
`RECALL_EMBEDDING_APPROVED_URL`, and read its bearer from exactly one protected source:
an owner-only, non-symlink `RECALL_EMBEDDING_KEY_FILE`, or the deployment secret variable named
by `RECALL_EMBEDDING_KEY_ENV`. The latter is the normal container pattern; the variable's value
must be injected by the secret manager and never written in the config file. Recall rejects
redirects, rereads the selected key source, validates response
indices, dimensions, and finite values, and fingerprints the protocol, model version, dimensions,
and query/document transformation. A profile change therefore makes old vectors stale until the
online backfill converges.

The optional packaged self-hosted unit pins TEI 1.9 and Qwen3-Embedding-0.6B, binds only
`127.0.0.1:8089`, and never exposes an embedding route through Tailscale Serve. Keep
`RECALL_EMBEDDING_BATCH_SIZE=1`: the derivation
fingerprint and backfill deliberately trade background throughput for reproducible Qwen document
vectors. The sidecar admits only one single-input request at a time because this Qwen/TEI CPU path is
not reproducible across batched requests. An overlapping request is rejected and retrieval safely
falls back to exact and lexical legs; a backfill retry converges later. Oversized
documents use a fingerprinted 4,096-character
head-and-tail projection so a giant tool result cannot stall a complete backfill batch. The runtime
verifies the exact model commit and float32 dtype against TEI `/info` before sending text. Search
ignores stale fingerprints, dimensions, projector versions, and content hashes.

Production backfill uses an identical second sidecar on `127.0.0.1:8090`. Keeping historical CPU
inference separate prevents convergence work from rejecting live query embeddings. Neither port is
served through Tailscale, and both runtimes enforce the same pinned single-input contract. The worker
sets its endpoint at `ExecStart`, after `EnvironmentFile` loading, so the live-query URL cannot override
the dedicated backfill route through systemd environment precedence. Its 120-second transport timeout
allows long CPU inference to finish under contention without relaxing the live-query timeout.

Query planning is separate from embeddings and optional. When enabled, it must use the staging
LiteLLM HTTPS router plus a
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

On a large existing brain, converge high-value sources first without changing global correctness:

```bash
python -m recall_server.cli backfill-embeddings --source-id SOURCE_ID --batch-size 128
python -m recall_server.cli backfill-embeddings --source-id SOURCE_ID --surface user --batch-size 128
```

The optional source and surface selectors change scheduling only. Metrics and search compatibility remain global, and an
unscoped replay finishes every remaining source. The packaged oneshot has no start timeout because a
bounded batch on CPU can legitimately exceed the systemd manager default; batch and timer bounds still
provide resumable checkpoints.

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
