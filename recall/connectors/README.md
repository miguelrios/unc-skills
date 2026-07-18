# Recall connector SDK

A connector fetches one bounded page. `ConnectorRunner` owns everything after
that boundary: closed-schema validation, pre-ingest privacy, a mode-0600 SQLite
outbox, Brain acknowledgement recovery, cursor commits, canonical tombstones, and
content-free doctor state.

## Connector Kit v3

`connectors.kit` is the versioned publisher surface around the same proven page
runner. A v3 definition is composed from closed placement, auth, sync, policy,
record-kind, and selection facets. It declares what a connector can do without
containing executable paths, import strings, credentials, endpoint templates, or
arbitrary request recipes.

Placements are `source_local`, `remote_worker`, or `either`. Acquisition modes
are explicit (`poll`, `watch`, `snapshot`, `import`, or `webhook`). Provider
scopes remain code-owned strings on the bundled definition, which permits
non-Google APIs without turning configuration into an operation language.

Third-party workers do not load into Recall Core. They may exchange a single
closed `recall.connector-page.v1` document with a source-scoped host:

```python
from connectors.kit import decode_page_wire, encode_page_wire

payload = encode_page_wire(page)
same_page = decode_page_wire(payload)
```

The wire contains only typed records, an opaque next cursor, and `has_more`.
Source identity and Brain authority stay outside the payload and are bound by
the host. Unknown versions, fields, record schemas, invalid cursors, non-finite
JSON, and oversized payloads fail before the runner. The schema is published at
`contracts/connector_page_v1.json`.

The first kit deliberately provides no entry-point discovery or arbitrary
worker launcher. Publishers can develop and conformance-test separate workers;
operators must explicitly pin and run them outside Core until a later signed
distribution contract is approved.

### Conformance fixtures

`run_connector_conformance(factory)` runs the same aggregate-only matrix for
remote polling, local snapshots, imports, and future acquisition shapes. A
fixture factory exposes a v3 manifest, one synthetic source ID, and a
`build(scenario)` method. The fixed scenarios pin first-page ACK, pagination,
empty terminal pages, lost-ACK recovery, revisions, explicit tombstones,
acknowledged replay, privacy-before-spool, rate limits, malformed pages, source
identity, and wire parity.

The report contains only the connector ID, fixed cell names, and counts. It
never includes source IDs, cursors, paths, exception text, records, or provider
payloads. A publisher should make this matrix part of its own test suite before
asking for a connector to be bundled.

Connectors are explicit Python objects. Recall does not discover or execute
plugins, recipes, entry points, or code from the current directory.

```python
from connectors.sdk import ConnectorPage, ConnectorRecord, ConnectorRunner

class MyConnector:
    connector_id = "example.pull"
    source_id = "example:account:mine"

    def pull(self, cursor: str | None) -> ConnectorPage:
        # Fetch with connector-owned credentials. Never put those credentials in
        # content, provenance, cursors, exceptions, or the Brain client.
        return ConnectorPage(
            records=(ConnectorRecord.from_mapping({
                "schema_version": 1,
                "native_id": "stable-upstream-id",
                "occurred_at": "2026-07-14T00:00:00Z",
                "content": {"text": "source value"},
                "provenance": {"uri": "https://source.example/item/stable-upstream-id"},
                "deleted": False,
            }),),
            next_cursor="stable-upstream-checkpoint",
            has_more=False,
        )
```

The cursor is opaque private coordination state. It is persisted only in the
private spool and is never returned by `doctor`. A page cursor commits only after
all retained records and tombstones receive a valid Brain acknowledgement. When
privacy drops every record, the runner can commit the cursor without making a
Brain request.

`ConnectorRateLimited(retry_after_seconds=...)` returns a bounded, jittered retry
hint without sleeping. All other upstream and Brain failures surface only a
stable `ConnectorRunError.error_code`; payload-bearing exception text is not
propagated. The caller owns scheduling and may log only the code and doctor
counts.

External source credentials stay inside the connector. The Brain credential is
source-scoped and stays inside its Brain client. Revoking either side leaves the
pending page and cursor recoverable. Deletion records set `deleted: true`; their
tombstones bypass contextual privacy-judge availability.

## Explicit ChatGPT/Cowork export inbox

`ExportInboxConnector` is the bundled, network-free adapter for user-supplied
OpenAI-style `conversations.json`, JSONL, and ZIP exports. It never discovers
Downloads, Desktop, application support, browser state, or private databases.
The selected directory is flat: nested roots, symlinks, hard links, archive
traversal, and malformed records fail closed.

The catalog stores only hashes, stable native IDs, timestamps, and content-free
provenance. Message bodies cross the shared privacy boundary before the durable
runner spool or Brain network. Removing a file from the inbox does not delete
central history. List its opaque export ID and explicitly queue removal instead:

```bash
recall-brain export-inbox-dry-run --inbox "$HOME/Recall Inbox" --catalog "$STATE/catalog.db"
recall-brain export-inbox-list --inbox "$HOME/Recall Inbox" --catalog "$STATE/catalog.db"
recall-brain export-inbox-remove --inbox "$HOME/Recall Inbox" --catalog "$STATE/catalog.db" exp_...
```

The next scheduled `export-inbox-sync` ACKs reference-safe tombstones. If another
active export still owns the same upstream message, that message remains live.

## Read-only Grep AI completed research

`GrepAIConnector` imports completed work from the official Grep API v2. It uses
only authenticated `GET /api/v2/research` list and exact job-detail requests.
It cannot create research, spend credits, mutate sharing, use public-capability
reads, upload attachments, or download signed files.

Grep authority and Brain authority are separate. Supply the Grep
`research:read` key through Keychain or a regular mode-0600 file; its value never
belongs in argv, config previews, cursors, receipts, errors, or the Brain. The
preview reads neither credential and performs no writes or network calls:

```bash
recall-brain grep-ai-config-preview \
  --endpoint https://brain.example.ts.net \
  --source-id grep-ai:tenant:mine \
  --keychain-service ai.parcha.recall.grep-ai \
  --keychain-account grep-ai:tenant:mine \
  --grep-keychain-service ai.parcha.grep.research-read \
  --grep-keychain-account research-read \
  --spool "$HOME/.local/state/recall/grep-ai.db"
```

Each invocation fetches one bounded keyset page. The private cursor resets to
the list head after a historical sweep, so a job inserted above an in-progress
page walk is found on the next pass. Brain acknowledgement gates cursor commit;
a lost acknowledgement replays the staged batch without refetching Grep.
Completed reports cross the shared privacy boundary before SQLite or Brain.
Failed, blocked, and cancelled jobs advance the settled boundary without becoming
memories. Queued, moderation, planning, running, paused, and the exact
`in progress`/`in_progress` variants also remain out of memory, but hold that
boundary behind them so a later status transition is revisited.
List absence, 404, retention, and authorization failure never infer deletion—forget
an imported result explicitly with its Brain receipt.

After a valid Brain acknowledgement, the runner keeps only the lowercase
SHA-256 pair for the source-qualified native ID and canonical content. Exact
versions encountered again on a reordered upstream page advance the cursor
without another Brain request. Changed content and tombstones remain distinct
versions and still ingest. A lost acknowledgement deliberately replays because
the pair is not recorded until the acknowledgement transaction commits.

When upgrading a spool whose records already exist centrally, stop its service
and create a private hash-only seed manifest from canonical Brain events:

```json
{"schema_version":1,"records":[{"native_sha256":"<64 lowercase hex>","content_sha256":"<64 lowercase hex>"}]}
```

The manifest and existing spool must be absolute, regular non-symlink mode-0600
files in mode-0700 non-symlink directories. The spool must have pinned identity
and no pending work. The command validates the entire closed schema before one
atomic, idempotent insert and emits counts only:

```bash
recall-brain connector-spool-seed-acknowledged \
  --spool "$STATE/grep-ai.db" --input "$PRIVATE/grep-ai-ack-seed.json"
```

## Declarative registry and content-free status

`connectors.registry` is the closed inventory for Recall's installed input
surfaces. It declares command, push/pull mode, exact authority slots, allowed
visibility/privacy modes, checkpoint behavior, and explicit deletion semantics
for deliberate capture, the ChatGPT/Cowork export inbox, and Grep AI. It is an
immutable tuple in the package—not an entry-point or working-directory plugin
loader.

Preview is static and performs zero credential/source reads, writes, imports,
or network calls:

```bash
recall-brain connector-registry-preview
```

Status accepts only authority-presence flags and an optional local spool. The
spool is opened read-only and immutable; output is limited to health, counts,
checkpoint presence, privacy policy, and a whitelisted stable error code:

```bash
recall-brain connector-registry-status \
  --connector-id grep.ai --enabled --privacy-mode drop \
  --authority brain --authority source --spool "$STATE/grep-ai.db"
```

Status never syncs, repairs, advances a cursor, opens source material, or emits
the spool path, source identity, cursor, authority reference, or private content.

## Resumable connector supervision

`connectors.supervisor` decides only when an explicitly constructed pull
connector runner may execute. Schedules use an opaque random job key, injected
clock and jitter, bounded cadence/backoff/rate-limit/lease values, atomic leases,
and an explicit repair generation. Connector credentials, source IDs, command
arguments, paths, cursors, and content are not schedule fields and never enter
the supervisor database.

Authority and contract failures park a job until its generation changes or the
running process receives an explicit wake. Transient failures back off; rate
limit hints are capped; success resets failures. A due job still runs when
another job fails. Expired leases are recoverable after process death, while an
active lease cannot be acquired by another supervisor.

Preview is static. Status opens the mode-0600 state database read-only and emits
only aggregate counts and stable outcome classes:

```bash
recall-brain connector-supervisor-preview
recall-brain connector-supervisor-status --state "$STATE/supervisor.db"
```

The scheduler core never reads the wall clock or sleeps: hosts inject time and
wait/wake events. Private schedule configuration and service launch wiring are
separate deployment concerns; the supervisor does not discover plugins or
execute arbitrary commands.

## Private connector host

`connectors.host` is the closed deployment layer for the bundled export-inbox
and Grep AI pull connectors. It reads one explicitly selected regular mode-0600
JSON file from a mode-0700 directory. The file contains source-specific paths
and authority *references*; inline tokens/API keys, command arrays, import
strings, plugins, relative paths, shared visibility, duplicate source/job/state
ownership, and Brain/Grep authority aliasing are rejected.

Preview validates only the config schema and renders aggregate counts. It does
not dereference authority, open an inbox/report, construct a connector, create
state, or call a network:

```bash
recall-brain connector-supervisor-config-preview --config "$PRIVATE/host.json"
recall-brain connector-supervisor-run --config "$PRIVATE/host.json" \
  --state "$STATE/connector-supervisor.db" --once
```

Without `--once`, the host runs bounded supervisor cycles. TERM/INT wake and
stop it; HUP wakes the current cycle and reloads the private config between
cycles. Only the closed factory may construct `ExportInboxConnector`,
`GrepAIConnector`, `BrainClient`, `PrivacyPolicy`, and `ConnectorRunner`.

The macOS installer accepts `--connector-supervisor-config FILE` to install the
fixed `ai.parcha.recall.connector-supervisor` LaunchAgent. The plist passes the
config path, content-free supervisor state path, bundled interpreter, and fixed
module/subcommand—never authority values or source content. Use
`--disable-connector-supervisor` to unload/remove only that agent while keeping
recoverable config, connector spools, and scheduler state.
