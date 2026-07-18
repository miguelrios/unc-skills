# Recall Integration Fabric RDD

**Date:** 2026-07-18  
**Status:** execution-ready design  
**Scope:** source acquisition, normalization, supervision, packaging, and live acceptance  
**Out of scope:** changing Recall retrieval, reopening the Render-to-PlanetScale network decision,
write/send/action integrations, or publishing private source evidence

## 1. Objective

Turn Recall's merged connector-v2 substrate into an extensible ingestion fabric that works in two
ordinary deployment shapes:

1. **Mac local:** Recall Bridge reads explicitly selected device-local surfaces, applies privacy
   policy before durable spool or network, and sends only typed canonical records to the Brain.
2. **Remote worker:** an always-on worker uses owner-authorized read APIs or imports, applies the
   same contract, and writes through a source-scoped Brain authority.

The same connector contract, fixtures, conformance runner, lifecycle, receipts, and deletion rules
must serve both shapes. A connector may be `source_local`, `remote_worker`, or `either`; placement
must never change its record identity or evidence semantics.

## 2. Recalled starting point

Merged PRs #52 and #53 established:

- typed connector-v2 records;
- a closed connector registry;
- ACK-gated cursor commits and revision/tombstone replay;
- privacy processing before the durable connector spool;
- a resumable supervisor and private connector host;
- ChatGPT/Cowork export-inbox and Grep AI connectors; and
- a pinned, closed, read-only Google Workspace CLI rail.

The Google, private-Mac, and social/work source loops never started. The later public-MCP chain
proved the application contract below Render's network boundary and stopped `AT_BOUND` at
Render-to-PlanetScale connectivity. Source implementation can advance without claiming that hosted
network proof or changing the public MCP listener.

## 3. Research verdict

### 3.1 What to adopt

- **OpenClaw:** explicit per-account configuration, independently composable setup/security/status/
  lifecycle/capability facets, lazy loading, capability-aware discovery, and content-free doctor
  surfaces.
- **Hermes:** one registry entry owns factory, configuration validation, authorization metadata,
  health, setup, and runtime hints; adapters share a lifecycle instead of scattering wiring through
  core.
- **GBrain:** a versioned public ingestion contract, a stable import surface, dumb source emitters,
  daemon-owned supervision, a source test harness, and separate trickle versus migration modes.
- **Airbyte/Singer/Nango:** schema/record/state separation, resumable incremental state, explicit
  added/updated/deleted results, signed wakeups, and provider-managed OAuth as an optional rail rather
  than the canonical data store.

### 3.2 What not to adopt

- Do not load arbitrary in-process plugins into Recall Core. OpenClaw and Hermes explicitly treat
  installed plugins as fully trusted local code. Recall's first public SDK is a build-time connector
  kit plus source-scoped wire contract; third-party workers remain separate processes/containers.
- Do not mistake messaging gateways for history ingestion. Live inbound chat adapters usually see
  only events delivered after connection and often expose write/send behavior.
- Do not make Nango, Composio, Airbyte, or another integration platform the source of truth. An
  optional managed-auth/sync rail may be added later, but Recall owns canonical receipts, revisions,
  tombstones, authorization, and deletion lineage.
- Do not add a generic HTTP recipe runner. Provider operations remain code-defined, read-only, and
  allowlisted.
- Do not infer deletion from list absence.

## 4. Architecture

```text
Mac-only source                         Remote/API source
Messages DB / export / browser          Google / Slack / GitHub / ...
             |                                      |
     bundled source adapter                    bundled source adapter
             |                                      |
             +------ Connector Kit v3 --------------+
                    validate → normalize → policy
                         → private ACK spool
                         → source-scoped writer
                         → Recall Core / Brain
```

### 4.1 Connector Kit v3

The current `PullConnector.pull(cursor) -> ConnectorPage` stays as the runner boundary. Version 3
adds a manifest and conformance surface around it; it does not replace the proven runner.

Each bundled connector declares:

- stable connector ID and source family;
- execution placement: `source_local`, `remote_worker`, or `either`;
- acquisition shape: `poll`, `watch`, `import`, or `webhook`;
- auth kind: `none`, `oauth2`, `api_token`, `os_permission`, or `selected_export`;
- exact minimum read scopes or OS permissions;
- supported typed record kinds;
- backfill and incremental modes;
- selection/filter capabilities;
- edit, explicit-delete, attachment, and retention semantics;
- default privacy mode; and
- content-free setup, health, and remediation descriptors.

The manifest contains no executable path, import string, credential, selector, cursor, endpoint
override, or arbitrary request recipe.

### 4.2 Reusable connector facets

Provider code is narrow and composed from shared facets:

- `Transport`: bounded request/subprocess/local-snapshot boundary and stable error classes.
- `AuthReference`: a pointer to Keychain, secret manager, or private file; never the credential.
- `Selection`: owner-approved accounts, labels, calendars, channels, chats, roots, or streams.
- `PageState`: opaque upstream cursor plus bounded reconciliation state.
- `Normalizer`: upstream object to connector-v2 typed record.
- `DeletionPolicy`: explicit tombstone only, with source-specific authority.
- `ConnectorSpec`: immutable metadata used by setup, preview, host, package, and doctor.

Remote and Mac hosts consume these facets through the same static registry. A source adapter cannot
receive another source's authority or emit another source's ID.

### 4.3 Third-party extensibility

The first supported extension boundary is out of process:

- publishers use the public connector kit and conformance harness;
- a connector worker emits the versioned page protocol to a source-scoped writer;
- Core never imports publisher code or mounts publisher credentials;
- installation is explicit and pinned by the operator; and
- a future signed catalog can distribute workers without changing the contract.

Bundling a connector in Recall remains the shortest path for User #1. The out-of-process contract is
proven before enabling runtime discovery.

## 5. Source phases

### 5.1 Remote/API pack

| Source | Placement | Acquisition | First bounded surface |
|---|---|---|---|
| Gmail | remote worker | poll + history cursor | Inbox/Sent message bodies; attachment metadata |
| Google Calendar | remote worker | poll + sync token | selected calendars/events |
| Google Contacts | remote worker | poll + sync token | selected account contacts |
| Google Drive/Docs | remote worker | poll + change token | selected roots; text exports only |
| GitHub | remote worker | poll | selected repos: issues, PRs, comments, commits |
| Linear | remote worker | poll | selected teams/projects/issues/comments |
| Slack | remote worker | poll | selected channels/DMs allowed by exact scopes |
| Notion | remote worker | poll | pages/databases shared with the integration |
| X | remote worker | poll | own posts, mentions, bookmarks; home timeline off |

Google uses the pinned Workspace rail. Other providers use narrow official read APIs. Synthetic
contract completion precedes all owner account grants. X cost and retention remain a live-test gate.

### 5.2 Mac-local pack

| Source | Acquisition | Boundary |
|---|---|---|
| iMessage | read-only SQLite snapshot/reconciliation | Full Disk Access; no send/private API/SIP change |
| WhatsApp | watched explicit export | no linked-device session |
| Codex / Claude / Cowork / Hermes | explicit local session surfaces | reuse existing parsers and lifecycle |
| ChatGPT | consented export inbox | no unstable application-database scraping |
| Safari / Chrome | selected read-only history/bookmark snapshot | per-browser opt-in |
| Apple Notes | selected read-only snapshot when schema probe passes | unsupported schema fails closed |
| selected files / Obsidian | explicit roots only | no home-directory discovery or symlink escape |

All local data crosses privacy policy before the connector spool or network. Diagnostics contain
counts and stable error classes only.

### 5.3 Portable import and web pack

Portable fallbacks reduce OAuth and platform fragility:

- MBOX/EML, ICS, VCF;
- Slack export, Notion export, X archive, WhatsApp export;
- RSS/Atom;
- Markdown/text/closed JSONL selected folders; and
- authenticated deliberate-capture webhook using the existing memory authority.

Imports use stable archive/native identities and explicit removal. Re-import is idempotent; absence
from a later archive never implies deletion.

## 6. Configuration and filter contract

Every source begins disabled and supports a content-free preview. Enabling requires an explicit
source instance plus:

- account/device placement;
- privacy mode (`scrub` or `drop` by default);
- source-specific selectors;
- backfill window and live-sync cadence;
- attachment mode (metadata/off unless separately enabled);
- retention mode;
- searchable/default participation state; and
- a source-scoped authority reference.

Selectors are typed per source. No connector accepts a free-form executable, import path, query
language, URL template, or arbitrary HTTP parameters from configuration.

## 7. Test and evidence contract

Every connector must pass one reusable matrix:

1. manifest and static-preview validation;
2. first page, pagination, empty page, and terminal cursor;
3. replay, lost ACK, and crash after fetch/stage/Brain commit/cursor commit;
4. unchanged replay, changed revision, explicit tombstone, and out-of-order overlap;
5. invalid cursor and bounded reconciliation;
6. rate limit, transient failure, auth revoke, malformed response, and output bounds;
7. selectors, privacy all-drop, credential-shaped exception, and content-free doctor;
8. ingest → project → retrieve → show → tombstone Brain E2E; and
9. placement-specific package E2E.

Safety floors remain zero: unauthorized retrieval, secret/PII canary leakage, duplicate acknowledged
versions, deletion resurrection, cursor commit before Brain ACK, source-authority escape, arbitrary
code loading, and public exposure.

Private live runs store mode-0600 criterion maps outside git. The repository and PRs contain only
synthetic fixtures and content-free aggregate results. No Cascade diary, transcript, personal
message/event/contact/post/document, private query/answer, credential, selector, path, or live
infrastructure detail is committed.

## 8. Primary references

Research was pinned on 2026-07-18 to:

- [OpenClaw channel plugin contract](https://github.com/openclaw/openclaw/blob/cb5070101e94dd30c3bb2e2fe21d7019c0036ca4/src/channels/plugins/types.plugin.ts)
  and [plugin trust model](https://github.com/openclaw/openclaw/blob/cb5070101e94dd30c3bb2e2fe21d7019c0036ca4/SECURITY.md).
- [Hermes platform registry](https://github.com/NousResearch/hermes-agent/blob/3d9be2789552a495c7adf30148e867e7614a4bdc/gateway/platform_registry.py)
  and [adapter guide](https://github.com/NousResearch/hermes-agent/blob/3d9be2789552a495c7adf30148e867e7614a4bdc/website/docs/developer-guide/adding-platform-adapters.md).
- [GBrain ingestion source contract](https://github.com/garrytan/gbrain/blob/f72de97943eb9dc1292a80f85d19db7e311855dc/src/core/ingestion/types.ts)
  and [infrastructure architecture](https://github.com/garrytan/gbrain/blob/f72de97943eb9dc1292a80f85d19db7e311855dc/docs/architecture/infra-layer.md).
- [Singer record/schema/state contract](https://hub.meltano.com/singer/spec/),
  [Airbyte resumable cursor API](https://airbytehq.github.io/airbyte-python-cdk/airbyte_cdk.html),
  and [Nango signed sync webhooks and record cursors](https://nango.dev/docs/guides/platform/webhooks-from-nango).
- Official Google
  [Gmail](https://developers.google.com/workspace/gmail/api/guides/sync),
  [Calendar](https://developers.google.com/workspace/calendar/api/guides/sync),
  and [People](https://developers.google.com/people/v1/contacts) incremental-sync guidance.

