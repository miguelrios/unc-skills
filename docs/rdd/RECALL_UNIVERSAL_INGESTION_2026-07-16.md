# Recall Universal Ingestion — RDD

**Status:** replanned after L0 storage AT_BOUND; approved for autonomous remediation through human gates
**Date:** 2026-07-16
**Decision owner:** Recall owner
**Execution plan:** `docs/LOOP_CHAIN_RECALL_UNIVERSAL_INGESTION_2026-07-16.md`
**Migration issue:** [#54](https://github.com/miguelrios/unc-skills/issues/54)

## 1. Outcome

Recall becomes an owner-controlled context plane that can continuously ingest, normalize, and
retrieve useful evidence from communications, calendars, social activity, files, local Mac
applications, research systems, and future sources without turning a personal brain into an
unbounded surveillance database.

For User #1, the product succeeds when a natural-language question can gather the right evidence
across devices and sources, preserve surrounding conversation or event context, cite every material
claim back to a canonical source receipt, distinguish current from stale or deleted state, admit only
context appropriate to the task, and honestly report gaps.

The intended shape is:

```text
source-local reader          always-on API reader
(Mac databases/files)        (official remote APIs)
         |                           |
         +---- deterministic pull ---+
                       |
          normalize -> policy -> spool
                       |
              source-scoped write
                       |
        managed Recall Core API/workers
                       |
       managed Postgres + encrypted durability
                       |
       canonical immutable source evidence
                       |
       +---------------+----------------+
       |               |                |
  conversations   identity graph   temporal projections
       |               |                |
       +---------------+----------------+
                       |
       hybrid retrieval + task admission
                       |
      cited answer + contradiction/gap report
```

## 2. Why extend Recall rather than replace it

Recall already owns the correctness boundary that matters most for private, heterogeneous inputs:

- stable source and native identities;
- content-addressed revisions and exact receipts;
- source-scoped authorization;
- ACK-gated cursor commits and recoverable mode-0600 spools;
- replay-safe, content-hash deduplication;
- explicit tombstones;
- privacy policy before spool or network;
- content-free health and supervisor state;
- Tailnet-only service access; and
- synthetic retrieval, privacy, deletion, and connector E2E suites.

Adopting another memory product as the canonical store would weaken receipts and deletion lineage,
force Recall records into a foreign page or fact model, and create a second trust contract. External
projects remain references and eval competitors:

- GBrain contributes the deterministic-collector pattern, compiled truth plus timelines, typed
  graph traversal, hybrid retrieval, cited synthesis, and explicit gap analysis.
- Botmem contributes a broad connector inventory, unified message shape, contact identity
  resolution, and practical local-source acquisition patterns.
- Ground-truth-preserving memory research supports keeping episodes and source evidence intact,
  then improving retrieval and context expansion rather than performing lossy ingest-time rewriting.
- Memory-admission research treats retrieved personal context as a control-channel boundary, not
  automatically trustworthy prompt material.

These are design inputs, not code dependencies. In particular, AGPL implementations must not be
copied into Recall.

## 3. Product principles

1. **Evidence first.** Raw source events are canonical; facts, summaries, links, and profiles are
   rebuildable projections with source receipts.
2. **Code for mechanics, agents for judgment.** Pagination, IDs, timestamps, permalinks, MIME
   decoding, edits, deletions, and cursor recovery are deterministic. Agents may classify,
   synthesize, resolve ambiguity, and report gaps.
3. **Read and act are different products.** Ingestion credentials are read-only. Sending mail,
   messages, posts, or calendar mutations requires separate tools, credentials, and approval.
4. **Collect broadly, admit narrowly.** Search may discover many sources, but only context relevant
   and safe for the current task can enter an answer or tool prompt.
5. **Local when local is authoritative.** Mac application data is read on the Mac and crosses the
   privacy boundary before network writes. Official API readers may run in the managed central plane.
6. **Private ingress first, portable ingress later.** User #1 keeps Tailnet-only Brain ingress.
   Ordinary laptop users may later use authenticated public HTTPS with device-scoped credentials only
   after its separate cross-tenant, revocation, abuse, and exposure gates pass. Source polling remains
   preferred; a webhook requires its own reviewed ingress design.
7. **Explicit scope and deletion.** Every source declares include/exclude rules, backfill horizon,
   retention, edit semantics, deletion semantics, and attachment policy.
8. **Closed runtime.** Recall constructs only bundled, reviewed connector factories. It never loads
   arbitrary entry points, imports, commands, recipes, or current-directory plugins.
9. **Private proof stays private.** Public tests are synthetic. Real-source evals publish aggregate,
   content-free receipts only; no message, query, answer, transcript, credential, private export,
   identifying path, or infrastructure detail enters the repository.
10. **Owner-visible control.** The utility must show what is enabled, where it runs, last success,
    bounded lag, current backfill state, privacy mode, and how to revoke or forget it.

## 4. Scope

### 4.1 First-class source families

| Family | Initial connectors | Default execution |
|---|---|---|
| `communications` | Gmail, iMessage, WhatsApp, Slack, Telegram | API host or source Mac |
| `schedule` | Google Calendar, Apple Calendar later | API host or source Mac |
| `contacts` | Google Contacts, source participant identities | API host plus projection |
| `social` | owner-authored X posts, mentions, bookmarks; optional home feed | API host |
| `documents` | Drive, Notion, selected filesystem roots, attachments | API host or source Mac |
| `work_activity` | GitHub, Linear, existing coding and research sources | API host or existing host |
| `personal_media` | selected Photos metadata/OCR, voice notes later | source Mac |
| `local_activity` | Apple Notes, browser bookmarks/history, selected local files | source Mac |

Finance, health, precise location, and password-manager data are not ordinary connector additions.
They require successor RDDs with stronger classification, retention, and admission policies.

### 4.2 Explicit non-goals for this chain

- No sending, posting, deleting upstream, RSVP, or calendar mutation.
- No arbitrary website scraping or browser automation when an official API/export exists.
- No public Brain URL or generic webhook executor.
- No model training on imported content.
- No silent contact merges based only on similar names.
- No attachment ingestion by default.
- No automatic promotion of source text into durable user preference or instruction memory.
- No claim that missing upstream records prove deletion unless the source emitted an explicit,
  authoritative deletion signal.

## 5. Runtime topology

### 5.1 Central Brain placement

The first managed profile is one immutable `recall-core` container on a Render private service,
PlanetScale Postgres as the canonical database, and a Tailscale gateway for private User #1 ingress.
The container packages the existing Recall HTTP API, projection/retrieval runtime, and background
workers; it is a deployment boundary, not a rewrite or a second memory product.

Application behavior remains standard PostgreSQL:

- one `DATABASE_URL` contract with full TLS certificate verification;
- startup capability probes for PostgreSQL version, required extensions, migration state, and role
  privileges;
- provider-neutral schema, queries, receipts, revisions, and tombstones;
- separate migration, application, source-writer, and read-only operational roles; and
- no PlanetScale, Render, Supabase, or Neon SDK inside canonical ingestion or retrieval code.

PlanetScale is the first live profile because it supplies managed encrypted Postgres, pgvector,
backups/PITR, roles, and agent-accessible provisioning. Supabase, Neon, and conforming pgvector
Postgres remain deployment adapters tested against the same capability contract. Provider branches
are deployment and restore units, never Recall source identity or authorization boundaries.

Collectors communicate only with Recall Core. A laptop or source connector never receives a database
credential. Source-local collectors keep mode-0600 device spools; always-on cloud connectors place
their outbox/checkpoint state in Postgres or a separately provider-attested encrypted durable volume.
The end state is a stateless Core process, but the first cutover may retain a narrow encrypted state
volume when required for safe incremental migration.

Provisioning is agent-runnable but not agent-authorized: automation may validate and preview a plan,
then must pause for billing, region, provider-account grants, Tailnet route approval, and final writer
cutover. Secret values live only in approved provider secret stores and never in manifests, argv,
logs, CI, support bundles, or repository evidence.

### 5.2 Source placement

Each connector declares one of three placements:

- `source_local`: data exists only on a selected device, such as iMessage `chat.db`.
- `always_on_api`: an official API benefits from continuous polling, such as X's bounded timeline.
- `either`: an incremental API can safely catch up after sleep, such as Gmail or Calendar.

The first implementation keeps placement static in private configuration. Dynamic workload movement
is unnecessary and risks copying credentials between hosts.

### 5.3 Credential boundary

- External-source and Brain credentials are distinct.
- Each connector has one exact source-scoped Brain writer.
- Mac credentials are Keychain references; server credentials are approved secret-manager or strict
  mode-0600 references.
- Every model or judge call uses the approved staging LiteLLM router with a short-lived scoped virtual
  key minted from the protected environment; master keys and direct provider calls are prohibited.
- Config files contain references, never secret values.
- Logs, exceptions, status, cursors, provenance, receipts, tests, and evidence never render secret
  values or authority references.
- Revoking source authority stops reads; revoking Brain authority leaves an ACK-recoverable spool.
- Database administration credentials exist only for bounded migrations. Runtime uses a rotatable,
  least-privilege application role; collectors use source-scoped Brain credentials, never SQL roles.

### 5.4 Storage boundary

Tailnet encryption does not protect database disks, connector state, or backups. Before bulk personal
communications ingestion, the deployment must produce content-free provider attestations for
encrypted database storage, encrypted backups, encrypted durable connector state, tested restore,
restricted filesystem modes, TLS server-identity verification, and source-scoped access.

Canonical source envelopes and searchable projections may contain personal content. Searchable
plaintext therefore relies on encrypted volumes and strict host access. A later optional encrypted
raw-payload sidecar may reduce the searchable projection footprint; it is not allowed to compromise
receipt parity or deletion lineage.

Migration freezes a canonical manifest before copying, restores into an isolated target, recreates
extensions explicitly, and verifies canonical events, revisions, tombstones, receipts, grants, source
profiles, projections, and embeddings. The final delta is ACK-gated. Old and new writers never run
unfenced against the same source, and the old deployment remains rollback-only until parity, backup
restore, both-device retrieval, and the owner cutover gate pass.

## 6. Connector contract v2

The current pull contract remains the execution primitive. V2 adds declared capabilities and typed
records without allowing runtime plugin discovery.

### 6.1 Bundled manifest

Each bundled connector factory declares:

```text
connector_id
contract_version
source_family
record_kinds
execution_placement
authority_slots
minimum_external_scopes
backfill_modes
checkpoint_semantics
edit_semantics
deletion_semantics
retention_modes
attachment_capability
default_privacy_mode
```

Registry preview is static and performs no credential, source, network, or filesystem reads. Status
remains content-free and reads only explicitly selected private state.

### 6.2 Typed canonical records

All records retain `source_id`, stable `native_id`, optional `native_parent_id`, `occurred_at`,
content hash, provenance, and deletion state. The content object additionally declares one closed
kind.

#### `communication_message.v1`

- stable conversation/thread identity;
- stable message identity and optional reply identity;
- author and participant source identifiers;
- inbound/outbound/system direction;
- sent, received, edited, and deleted timestamps when authoritative;
- subject and cleaned text;
- deterministic source permalink when available;
- MIME/format metadata; and
- attachment descriptors without attachment bytes by default.

#### `calendar_event.v1`

- calendar and event identities;
- recurring-series and instance identities;
- organizer and attendee source identifiers;
- start/end/timezone/all-day fields;
- title, description, location, conference reference;
- busy/visibility/status fields; and
- deterministic source permalink when available.

#### `social_post.v1`

- platform post, author, thread, and reply identities;
- text and source URL;
- created/edited/deleted state;
- authenticated stream type (`own`, `mention`, `bookmark`, `home`); and
- observed public metrics as a separate revisionable projection.

#### `contact_identity.v1`

- source-scoped identifier and identifier type;
- display name and optional organization/title;
- self/other role; and
- provenance confidence.

#### `document.v1`

- provider document/file identity and parent hierarchy;
- name, MIME type, modified time, owner/participant identities;
- extracted text when explicitly allowed; and
- source permalink.

### 6.3 Identity, revision, and deletion

- `source_id + native_id` is logical identity.
- `source_id + native_id + content_sha256` is an acknowledged version.
- A changed canonical content object creates a revision, never an overwrite.
- A lost Brain acknowledgement replays the exact staged batch.
- An upstream page cursor commits only after every retained event or tombstone is acknowledged.
- Stable already-acknowledged versions advance the source cursor without another Brain write.
- Explicit authoritative deletion creates a tombstone and invalidates every derived projection.
- List absence is not deletion.
- Sources with provider compliance obligations declare a bounded deletion reconciliation job.

### 6.4 Workspace acquisition rail

Recall should not reimplement every Google REST client unless a required correctness property cannot
be obtained from a maintained CLI. It introduces a closed `WorkspaceRail` boundary whose output is
still normalized through the same connector-v2 contract:

```text
pinned CLI or direct API client
  -> exact argv/method allowlist
  -> structured JSON/NDJSON envelope
  -> source-specific deterministic normalizer
  -> privacy policy before spool/network
  -> canonical Recall record + checkpoint
```

The first BUILD loop pins one transport rather than exposing a choice to runtime agents:

| Transport | What it gives Recall | Main risk | Intended role |
|---|---|---|---|
| Google Workspace `gws` v0.22.5, tag commit `705fb0ec` | Discovery-generated access to Gmail History, Calendar sync tokens, People sync tokens, Drive changes, and Docs exports through JSON/NDJSON | dynamic command surface and write methods unless Recall closes them | sole v1 transport, installed from checksum-pinned official assets and hidden behind exact read-only method/schema allowlists |

Every Google source uses this one pinned transport and has exactly one allowed command family in a
release. Swapping transports must replay the same synthetic conformance fixtures and prove canonical-record
parity before live use. A rail is transport, not the Brain's data model and not a generic shell tool.
`gws` Model Armor is a useful comparison point but is not enabled: Recall's model/judge traffic must
use the approved staging LiteLLM route, so imported-content classification stays behind that boundary.

Every CLI execution is closed and fail-safe:

- pin version and release checksum; upgrades are explicit conformance-tested changes;
- construct an argv vector without a shell, executable config, arbitrary subcommand, or user-supplied
  flag passthrough;
- allow only exact read methods required by the enabled source, plus capability-specific read-only and
  no-send ceilings where the CLI supports them;
- require non-interactive JSON/NDJSON, a frozen output schema, bounded pages/bytes/time, and explicit
  exit-code handling; empty success output fails rather than advancing a cursor;
- pass a minimal environment containing credential references, not the ambient agent environment;
- disable verbose/body logging and sanitize stdout, stderr, and errors before any persistent log;
- treat every returned field as untrusted source evidence, never as an instruction; and
- keep CLI/MCP access internal to the collector. Recall never exposes a generic command bridge to an
  answering agent.

OpenClaw's useful pattern is the separation between a thin agent skill and a durable CLI. Recall reuses
that boundary, the exact capability ceilings, and the Gmail delivery-before-checkpoint behavior. It
does not copy OpenClaw's on-demand agent workflow wholesale: continuous ingestion remains a
deterministic collector, and imported mail never becomes a hook prompt before Recall policy and
admission checks.

## 7. Privacy and source policy

Each source has an owner-private policy with closed fields:

```text
enabled
include selectors
exclude selectors
backfill start/end
privacy mode: scrub | drop
retention mode: mirror | archive | explicit-only
attachments: off | metadata | selected-content
noise treatment: searchable | low-priority | excluded
derived-memory eligibility
```

`off` privacy is prohibited for new personal-communication connectors. Selectors are connector-
specific but structurally bounded: Gmail labels/senders, calendar IDs, chat IDs, source folders,
X stream types, or explicit roots. Policy previews show counts and categories, never source content
or selectors that expose private identities.

### 7.1 Default classifier behavior

Drop before spool and network:

- one-time passcodes and authentication codes;
- password-reset or account-recovery secrets;
- private keys, API keys, bearer tokens, session cookies, and credential files;
- payment-card authentication values; and
- records classified as authentication material rather than durable communication.

Scrub secret-shaped spans and high-risk identifiers while preserving safe surrounding context.
Contextual PII classification remains opt-in and, when enabled, uses only the approved staging model
router with a short-lived scoped key.

Marketing, notification, receipt, and automated senders are classified rather than silently dropped.
Retrieval can demote noise without making potentially useful evidence unrecoverable.

### 7.2 Prompt-injection and action isolation

Imported content is evidence, never instruction. Retrieval labels source text as untrusted quoted
material. The admission layer cannot let an email, message, post, or document change system policy,
authorization, tool parameters, or user intent. Tool-enabled agents receive only admitted evidence
plus receipts; all action tools retain their independent approval model.

## 8. Projections and retrieval

### 8.1 Rebuildable projections

1. **Items:** cleaned source evidence with exact receipts.
2. **Conversations:** ordered messages grouped by native parent/thread.
3. **Temporal events:** event revisions with current, cancelled, superseded, or deleted state.
4. **Identity graph:** source identities joined to owner-approved people.
5. **Claims:** extracted assertions with validity, confidence, extractor version, and evidence.
6. **Relationships:** typed, time-bounded edges supported by receipts.
7. **Compiled views:** person/project/topic summaries that separate current truth from timeline.

An extracted claim, edge, or summary cannot be cited unless it resolves to live source receipts.
Deletion or policy exclusion removes its derived embeddings, links, and compiled views.

### 8.2 Contact resolution

Deterministic exact identifiers may merge automatically only when policy permits:

- normalized email address;
- normalized phone number;
- stable provider identity mapped by an authoritative contact record; or
- explicit owner alias.

Name-only, organization-only, or model-suggested matches remain candidates. The Mac utility presents
a diff and supporting source types for owner approval. Splits and corrections are versioned.

### 8.3 Query path

```text
query
  -> intent/domain/time/person/source planning
  -> hard authorization and source-policy filters
  -> lexical + vector + exact/entity candidate legs
  -> RRF and logical-evidence deduplication
  -> task-conditioned memory admission
  -> thread/episode neighbor expansion
  -> optional identity/temporal graph expansion
  -> rerank
  -> cited synthesis with contradiction and gap reporting
```

Memory admission is observable and content-free: selected domains, source families, temporal mode,
and exclusion reason counts. It never exposes excluded content.

## 9. Source-specific acquisition decisions

### 9.1 Google Workspace

- Use the L0-selected, pinned Workspace rail over official Google APIs with least-privilege read scopes
  and bring-your-own OAuth credentials for the first owner deployment.
- Prefer OpenClaw's operational shape where it proves the contract: non-interactive structured output,
  exact command allowlists, read-only/no-send ceilings, untrusted-content wrapping, and Gmail Pub/Sub
  pull so no public inbound endpoint is required.
- Use Gmail `historyId` incremental sync; an expired history window triggers a bounded full
  reconciliation. CLI notifications are wakeups, never the canonical cursor or delivery receipt.
- Use Calendar `syncToken`; HTTP 410 triggers a full reconciliation without logical duplicates.
- Use People API sync tokens for Contacts; ambiguous identity resolution remains a Recall projection,
  not a CLI merge.
- Add Drive and Docs through the same chosen rail when their stable IDs, revisions, exports, and change
  checkpoints pass the connector contract; Sheets remains metadata unless explicitly selected.
- Use message IDs as native IDs and thread IDs as parents. Import Inbox and Sent so response state can
  be reconstructed. Treat attachments as metadata until separately enabled.
- Keep OAuth onboarding explicit about restricted scopes, incremental consent, personal-use status,
  refresh-token expiry, revoke behavior, and any public-distribution verification requirement.

### 9.2 iMessage

- Run only on a selected Mac with explicit Full Disk Access.
- Use a pinned, reviewed, read-only adapter against the local Messages database or an equivalent
  stable JSON reader.
- Do not enable send, AppleScript mutation, private framework injection, or SIP modifications.
- Snapshot safely before querying a live SQLite/WAL source.
- Track stable message and chat IDs, edits, unsends/deletes when represented, participants, and
  attachment metadata.

### 9.3 WhatsApp

Two separately reviewed modes are allowed:

1. `export`: explicit owner-supplied exports; safest, not continuous.
2. `linked-device-experimental`: an isolated, pinned Baileys-compatible reader; continuous but
   unofficial and subject to account/session risk.

The experimental mode requires an unbounded human gate acknowledging the risk. It must expose no
send surface, pace syncs, isolate session keys in strict private storage, prove unlink/revoke, and
never claim that reconnect will recover unavailable history. The official WhatsApp Business Cloud
API is not a substitute for a personal chat-history API.

### 9.4 X

- Use official OAuth user context only; no scraping or browser automation.
- Durable defaults: owner-authored posts, mentions, bookmarks, and explicitly saved items.
- General home-timeline ingestion defaults to a bounded rolling retention or derived digest, not an
  unbounded permanent archive.
- Use current pay-per-use limits and an explicit monthly cost ceiling.
- Stored X content must reconcile edits, deletions, protected/suspended state, and policy requests
  within the required window.
- Off-X identity linking and any durable home-feed mode require an explicit reviewed policy choice.

### 9.5 Official API bundle

Drive, Slack, Notion, GitHub, and Linear use official read APIs or exports, stable upstream IDs,
incremental checkpoints where available, and separate source-scoped writers. A generic HTTP recipe
runner is not introduced. Each connector passes the same conformance suite and adds only its narrow
normalizer and authority implementation.

### 9.6 Local Mac bundle

Apple Notes, selected Photos metadata/OCR, browser bookmarks/history, and selected filesystem roots
are individually opt-in. No connector inventories Desktop, Downloads, application support, browser
state, or the home directory beyond its explicit root/surface. Symlinks, hard-link escapes, archive
traversal, and unsupported database schemas fail closed.

## 10. Mac utility

Extend the packaged utility into a menu-bar-capable `Recall Bridge` while preserving CLI parity.
For each source it provides:

- explanation of data read, external scope, execution host, privacy mode, and retention;
- explicit source/root/account/chat selection;
- OAuth, QR, Full Disk Access, or export onboarding appropriate to that source;
- content-free preview before first read;
- bounded backfill progress, last success, lag, error class, and pending counts;
- pause, resume, retry, reauthorize, unlink, disable, forget-by-receipt, and delete-local-state flows;
- no credential values, message samples, private paths, selectors, or cursors in diagnostics; and
- an exportable content-free support bundle.

Uninstall keeps recoverable state by default. Deleting source state or central memory is a separate,
explicit operation with clear consequences.

## 11. Test-driven development contract

Every BUILD follows red -> green -> refactor. A loop cannot claim TDD merely because tests exist at
the end.

### 11.1 Reusable connector conformance suite

Every pull connector is tested against synthetic fixtures for:

- first page, pagination, empty page, and terminal cursor;
- stable replay and lost acknowledgement;
- crash after fetch, stage, Brain commit, and cursor commit;
- repeated identical record and changed-content revision;
- explicit edit and tombstone;
- out-of-order and overlapping upstream pages;
- expired/invalid cursor requiring full reconciliation;
- rate limit, transient failure, authorization failure, and malformed response;
- all-records-dropped privacy page;
- credential-shaped upstream exception;
- attachment oversize/type rejection;
- source-policy include/exclude behavior; and
- content-free status and logs.

Fake success is pinned: a test must fail if the connector advances a cursor before ACK, treats list
absence as deletion, changes identity on restart, swallows an authorization error, emits an empty
scoreboard, or lets private fixtures enter repository evidence.

### 11.2 Evaluation pyramid

1. Unit: normalizers, identity, cursor codec, policy, and deletion semantics.
2. Contract: connector state machine with fake upstream and fake Brain.
3. Integration: pinned official-client or local-reader adapter against synthetic servers/databases.
4. Brain E2E: ingest -> project -> retrieve -> show -> tombstone.
5. Mac package E2E: install -> authorize/reference -> run -> status -> disable -> uninstall.
6. Private shadow: real User #1 sources and questions; aggregate-only public receipt.

### 11.3 Non-negotiable floors

| Metric | Floor |
|---|---:|
| Unauthorized retrieval | `0` |
| Secret/PII canary leakage | `0` |
| Duplicate logical evidence after replay/restart | `0` |
| Deletion resurrection | `0` |
| Cursor commit before Brain ACK | `0` |
| Deterministic repeat ranking agreement | `1.00` |
| Exact identifier Hit@1 | `1.00` |
| Receipt resolution | `1.00` |

Average quality gains never compensate for a safety-floor regression.

### 11.4 Real-source acceptance

Private real-source runs use questions frozen before opening results. Public evidence contains only a
run ID, source-family counts, corpus/query hashes, aggregate metrics, latency, and sanitized failure
categories. Samples below 30 are labeled directional. No real message, event, query, expected answer,
hit text, receipt, participant, source selector, or path is committed.

## 12. Operational requirements

- `recall-core` builds reproducibly as an immutable, vulnerability-scanned container with a
  content-free health contract; no source data, credential, private selector, or deployment identity
  enters the image or build provenance.
- The deployment manifest is closed and provider-neutral at runtime. Provider adapters may provision
  infrastructure but cannot change canonical schema, authorization, deletion, or retrieval semantics.
- Provisioning preview performs no billable mutation and renders no secret. Apply pauses at explicit
  billing, region, account-grant, network-route, and writer-cutover gates.
- Database startup fails closed on missing extensions, migration drift, excessive role privilege,
  disabled TLS verification, unavailable encrypted durability, or an untested backup policy.
- Supervisor leases, bounded cadence, jitter, backoff, rate-limit caps, and repair generations remain
  deterministic and content-free.
- Every connector publishes enabled/configured/health/checkpoint/pending/lag/error-class state.
- Backfills are pausable, resumable, rate-limited, and cannot starve live incremental sync.
- Source polling is staggered; one failed source cannot block another.
- Schema and projection migrations are restartable and rollbackable.
- Backups include canonical events, grants, source profiles, projections, aliases, and migration
  metadata; restore proves receipt and tombstone parity.
- Model-derived enrichment is asynchronous, versioned, rebuildable, and never blocks canonical
  evidence ingestion.

## 13. Rollout and rollback

The managed deployment rolls out before new source activation. First restore an isolated copy and run
parity reads. Next run synthetic collectors against the new writer. At the human cutover gate, pause
old writers, drain acknowledged work, apply one bounded final delta, rotate collector endpoints, and
keep the previous deployment read-only and rollback-ready. Rollback reverses endpoints and replays
only ACK-safe staged work; it never runs unfenced dual writers or infers deletion from copy absence.

Each source starts in four states:

1. `preview`: configuration and permissions only; zero source reads.
2. `shadow`: ingest into isolated private state and compare counts/receipts; excluded from default
   retrieval.
3. `searchable`: evidence participates in explicit source-routed queries.
4. `default`: evidence participates in ordinary admitted retrieval.

Rollback disables the source, preserves recoverable local cursor/spool unless explicitly deleted,
removes it from default retrieval immediately, and optionally tombstones its central events through
an owner-confirmed forget operation. Rollback never requires rewriting another source.

## 14. Release decision

Universal ingestion is five outcome-level Cascade loops, not a loop per connector. A loop may contain
multiple small, serial PRs, but every PR still has one concern and the loop exits only after its
system-level eval/E2E gate is green. A mode-0600 private exit manifest maps every criterion to safe
evidence verified at merged HEAD; no Cascade diary or live evidence enters git. The final loop runs
cross-device, cross-source questions, produces an aggregate verdict,
drafts the successor chain from losing cells, and pauses for owner sign-off.

The L0 storage failure does not add a sixth loop. Issue #54 is the remediation phase of L0 and must
close at both merged and deployed HEAD before L1 may request Google consent or read personal data.

## 15. Human decisions deliberately deferred to gates

1. Approve the managed provider, billing plan, region, provider-account grants, Tailnet route, and
   final writer cutover after reviewing a content-free deployment preview.
2. Approve the least-privilege Google read-only services/scopes declared by the pinned `gws` rail.
3. Grant access only to a WhatsApp export inbox; linked-device access is outside this chain.
4. Choose X rolling home-feed retention or keep durable ingestion limited to owner/saved streams.
5. Opt into any attachment content, contextual-PII judge, or source-specific durable fact extraction.
6. Accept the final User #1 quality and privacy verdict before broadening distribution.

## 16. External design references

These primary references were rechecked on 2026-07-16. They support acquisition and architecture
choices, but do not override the hard safety and evidence contracts in this RDD.

- [GBrain repository](https://github.com/garrytan/gbrain), including its compiled-truth/timeline,
  hybrid retrieval, typed graph, synthesis, and gap-analysis direction.
- [Botmem product and open-source overview](https://botmem.xyz/) for its local-first connector and
  contact-graph patterns; Recall does not inherit Botmem's trust or merge policy.
- [Gmail incremental synchronization](https://developers.google.com/workspace/gmail/api/guides/sync)
  for `historyId`, partial sync, and full reconciliation after an expired history window.
- [Google Calendar incremental synchronization](https://developers.google.com/workspace/calendar/api/guides/sync)
  for `syncToken`, pagination, authoritative deleted entries, and HTTP 410 recovery.
- [Google People contacts guide](https://developers.google.com/people/v1/contacts) for contact
  pagination and sync-token behavior.
- [Google OAuth guidance](https://developers.google.com/identity/protocols/oauth2) for incremental,
  least-privilege consent and separate API scopes.
- [OpenClaw's `gog` skill](https://github.com/openclaw/openclaw/blob/main/skills/gog/SKILL.md),
  [`gog` CLI](https://github.com/openclaw/gogcli), and
  [Gmail watch design](https://github.com/openclaw/gogcli/blob/main/docs/watch.md) for the thin-skill,
  constrained-CLI, Pub/Sub pull, retry, and delivery-before-checkpoint patterns.
- [Google Workspace CLI](https://github.com/googleworkspace/cli) for Discovery-generated command
  coverage, schema introspection, structured output, agent skills, and response sanitization. The
  repository explicitly labels the CLI pre-v1 and not an officially supported Google product.
- [PlanetScale Postgres](https://planetscale.com/docs/postgres),
  [extensions](https://planetscale.com/docs/postgres/extensions),
  [security](https://planetscale.com/docs/security), and
  [backups/PITR](https://planetscale.com/docs/postgres/backups) for the first managed database profile.
- [Render Blueprints](https://render.com/docs/infrastructure-as-code),
  [private services](https://render.com/docs/private-services), and
  [Tailscale template](https://render.com/templates/tailscale) for the first agent-managed compute and
  private-ingress profile.
- [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve) for private Tailnet ingress;
  Funnel is not enabled by the User #1 profile.
- [Supabase Postgres](https://supabase.com/docs/guides/database/overview) and
  [Neon pgvector](https://neon.com/docs/ai/ai-scale-with-neon) as portability profiles that must obey
  the same standard-Postgres capability and Recall safety contracts.
- [Apple Full Disk Access guidance](https://support.apple.com/guide/mac-help/change-privacy-security-settings-mchl211c911f/mac)
  for explicit access to other applications' local data, including Messages.
- [X timeline API](https://docs.x.com/x-api/posts/timelines/introduction) and
  [X API pricing](https://docs.x.com/x-api/getting-started/pricing) for official authenticated streams,
  bounded history, and pay-per-use cost controls.
- [Baileys repository and disclaimer](https://github.com/WhiskeySockets/Baileys) for the explicitly
  experimental WhatsApp linked-device option and its non-affiliation, breakage, and account-risk gate.

## 17. Public MCP replan addendum — 2026-07-17

The owner selected a public HTTPS MCP because the primary Grep agent runtime executes outside the
Tailnet. This addendum supersedes the Tailnet-only product choice in sections 2, 3.6, 4.2, 5.1, 12,
15.1, and the corresponding L0 acceptance row. It does not weaken source privacy, database security,
or authorization floors.

The first hosted OSS profile is one Render public web service running the immutable Recall image in a
closed `mcp-only` mode, PlanetScale Postgres, and a managed embedding API called directly over HTTPS.
Only `/mcp`, `/healthz`, and `/readyz` are public. Administrative, migration, credential, ingestion,
metrics, debug, and generic REST surfaces are absent from that listener. Existing collectors stay on
the proven writer until the public MCP loop exits; a later source loop may add a separately authorized
collector path without broadening the MCP listener.

Public MCP authentication is capability- and principal-aware:

- a credential is stored only as a digest and is immediately revocable;
- read capability resolves only sources granted to its principal;
- deliberate capture and exact-receipt forget are confined to one named memory source;
- tool discovery exposes only capabilities held by the current principal;
- missing, malformed, revoked, wrong-principal, wrong-source, and wrong-capability requests fail
  before source or store access; and
- the first profile remains one owner per deployment/database. Cross-owner multi-tenancy is outside
  this chain.

Grep must inject the MCP credential through a host-managed secret boundary. A durable credential may
not appear in a sandbox environment, filesystem, prompt, tool result, or log. If the Grep runtime
cannot prove this property, the public-MCP loop stops `AT_BOUND` and the successor design uses a
short-lived token exchange; it does not copy an owner-wide credential into an untrusted sandbox.

PlanetScale remains a public managed endpoint protected by verified TLS, database authentication, and
an all-role IP restriction. The production Render service uses a dedicated outbound-IP set scoped to
its region and environment; shared regional ranges are not the production network boundary. The
temporary proven-writer address remains allowlisted only through cutover. Purchasing the dedicated
set and final cutover are explicit human gates.

For clarity, public MCP ingress does not make imported evidence public. The service returns evidence
only after Recall authorization, retains exact receipts and deletion lineage, keeps raw/private proof
out of the repository, and exposes no anonymous query mode. Private-network deployment remains an
optional operator profile rather than an OSS requirement.

The execution plan is
`docs/LOOP_CHAIN_RECALL_PUBLIC_MCP_AND_UNIVERSAL_INGESTION_2026-07-17.md`.

Primary provider references for this replan:

- [Render web services](https://render.com/docs/web-services) for managed public HTTPS.
- [Render outbound IP addresses](https://render.com/docs/outbound-ip-addresses) for regional shared
  ranges and the dedicated-IP alternative.
- [Render dedicated IPs](https://render.com/docs/dedicated-ips) for fixed, workspace-exclusive
  outbound addresses.
- [PlanetScale IP restrictions](https://planetscale.com/docs/postgres/connecting/ip-restrictions) for
  all-role/schema CIDR enforcement.
