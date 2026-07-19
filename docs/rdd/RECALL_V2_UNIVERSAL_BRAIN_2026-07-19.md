# Recall v2 Universal Brain RDD

**Date:** 2026-07-19

**Status:** L0 contract freeze; implementation proceeds through the private Cascade

**Supersedes:** storage and topology sections of the 2026-07-16 universal-ingestion RDD

**Keeps:** ADR-0001's recall-native evidence plane and the 2026-07-18 connector-fabric contract

## 1. Objective

Recall v2 is an owner-controlled, evidence-first brain for local and remote work. It preserves exact
private source material, normalizes searchable evidence, and answers natural-language questions
across sources with stable citations, current-state semantics, and honest gaps.

The product has two supported deployment profiles:

1. **Laptop OSS:** local collectors, filesystem archive, local Postgres or SQLite search profile,
   and a loopback MCP server.
2. **Managed:** Mac source bridge plus remote API workers, private S3-compatible raw archive,
   PlanetScale Postgres, and an authenticated Render MCP/control plane.

The profiles share contracts, identities, conformance suites, deletion semantics, and receipts.
Provider placement cannot change evidence identity.

## 2. Starting point

Recall already has:

- content-addressed source events, exact receipts, revisions, tombstones, grants, and audit events;
- Postgres lexical/entity/vector projections with frozen synthetic retrieval evaluations;
- ACK-gated connector spools, privacy before durable spool or network, and a closed connector kit;
- Mac packaging and local readers;
- bundled Google, work-API, social, portable-import, export-inbox, and webhook adapters;
- authenticated HTTP/MCP contracts; and
- managed-provider planning and deployment adapters.

The missing boundary is not another memory framework. It is a coherent v2 contract joining exact raw
archive, tenant-aware canonical storage, deletion, retrieval, connector activation, and public agent
access. Existing live data remains fenced until v2 proves restore and deletion end to end.

## 3. Architecture

```text
owner-selected local sources                  owner-authorized remote APIs
Mac databases / files / exports               Google / GitHub / Slack / ...
                |                                         |
      source-local connector                         remote worker
                +------------------+----------------------+
                                   |
                     connector v3 typed page contract
                                   |
             exact raw archive -> privacy -> private ACK spool
                                   |
                         source-scoped Brain write
                                   |
                 canonical events + normalized documents
                                   |
                  FTS + vector + temporal + entity indexes
                                   |
                    scoped HTTP/MCP retrieval and capture
```

### 3.1 Canonical truth

The source event and its exact raw artifact are canonical. Documents, chunks, embeddings, entity
links, summaries, contradictions, profiles, and answers are rebuildable projections. A model cannot
create or mutate source identity, revision, deletion authority, grants, receipts, or archive keys.

Each canonical object is bound to:

```text
tenant_id + source_id + native_id + content_sha256 + revision
```

The first deployment has one owner tenant, but every v2 boundary carries `tenant_id`. No global
default or missing-tenant fallback is permitted.

### 3.2 Raw archive

Exact selected records/pages and explicitly enabled attachments are written before normalized Brain
commit:

- `ArchiveStore` has filesystem and S3-compatible implementations.
- Keys are opaque, bounded, and never public URLs.
- Buckets are private; transport verifies TLS; managed storage uses provider encryption and a
  separately scoped archive authority.
- Object metadata contains identity hashes and versions, not source text.
- Raw objects never enter model or judge payloads.
- Authoritative source deletion and explicit owner forget delete raw versions and every derived
  projection, leaving only bounded content-free tombstone/audit proof.

Archive commit alone does not advance a source cursor. The order is:

```text
archive -> privacy -> private spool -> Brain ACK -> cursor commit
```

### 3.3 Evidence and retrieval plane

PlanetScale Postgres stores tenant/principal/source grants, ingest batches, canonical events and
revisions, normalized documents/chunks, artifact references, jobs, receipt redirects, tombstones,
projection watermarks, and content-free audit records.

Retrieval uses independently measurable legs:

- exact receipt and identifier;
- lexical FTS;
- vector similarity;
- temporal/current-state;
- entity and relationship; and
- adjacent/thread context.

Bounded candidates are fused and reranked. Every material result retains source, revision, validity,
supersession, and receipt metadata. Answers cite evidence, surface contradictions, and abstain when
evidence is missing or outside the caller's scope.

Retrieved text is untrusted evidence, not an instruction channel. Only privacy-processed canonical
text may reach embeddings or judges, and model traffic uses the approved staging LiteLLM router with
a short-lived scoped key.

### 3.4 Control plane and MCP

The public Render service exposes only intended authenticated health and MCP/control routes. Bearer
credentials bind audience, tenant, principal, scopes, sources, and expiry. The contract is
OIDC/OAuth-ready but does not require an identity provider for the first owner.

Scopes are closed:

```text
recall:search
recall:show
recall:related
recall:answer
recall:capture
recall:forget
recall:status
```

Collectors never receive database or archive credentials. Archive operations are behind a separate
private authority. Database ingress remains allowlisted; dedicated static egress is a deployment
choice, not a product dependency.

## 4. Contracts

`recall/contracts/recall_v2_boundary_v1.json` is the public schema catalog.
`recall/contracts/v2.py` is the dependency-free runtime validator. They define:

- tenant/principal/source authority;
- raw artifact reference;
- canonical document and chunk identities;
- ingest job state;
- authoritative delete and explicit forget;
- old-to-new receipt redirect;
- retrieval request and cited result;
- MCP principal and scopes;
- model payload boundary; and
- aggregate-only public evidence receipt.

All instances are closed, finite, bounded, and explicitly versioned. Unknown keys fail closed.
Cross-object checks reject tenant/source mismatch, unconstrained write authority, archive URLs or
traversal, deletion without exact target lineage, results outside requested authority, raw fields in
model payloads, and content-bearing public evidence.

## 5. Ingestion portfolio

The first-owner activation catalog covers:

- coding/research: Codex, Claude, Cowork, Hermes, Grep AI;
- communications: Gmail, Slack, iMessage, WhatsApp, consented ChatGPT/Cowork exports;
- schedule/identity: Calendar and Contacts;
- documents/work: Drive/Docs, GitHub, Linear, Notion, selected files/Obsidian;
- social/local activity: X, browser history/bookmarks, Apple Notes; and
- portable/custom: MBOX/EML, ICS, VCF, service exports, feeds, closed JSONL, and authenticated
  typed webhooks.

Every source starts disabled. Activation requires explicit placement, selectors, privacy policy,
backfill window, cadence, attachment policy, retention policy, and source-scoped authority.
Absence never implies deletion.

## 6. Migration

V2 starts from a fresh canonical deployment:

1. freeze and back up the legacy manifest;
2. provision isolated v2 storage and authorities;
3. migrate only irreplaceable captures, tombstones, and receipt redirects;
4. reingest reproducible sources through v2;
5. compare canonical, projection, deletion, archive, and retrieval parity;
6. fence legacy writers and switch all clients;
7. soak for 24 hours and restore independently; then
8. retire legacy only after a separate owner authorization.

Old and new writers never run unfenced for the same source.

## 7. Safety and evaluation floors

Public tests use synthetic fixtures. Real-source and owner-question evaluations remain mode `0600`
outside git and publish aggregate receipts only.

Safety floors are zero:

- cross-tenant, cross-principal, or cross-source retrieval;
- duplicate acknowledged versions;
- cursor commit before Brain ACK;
- deletion resurrection;
- raw bytes sent to a model;
- secret/PII canary leakage;
- arbitrary connector code or HTTP recipes;
- passwordless database access or public archive objects; and
- credentials, transcripts, private queries/answers, paths, selectors, or live traces in git.

The quality target for the private owner holdout is Recall@5 at least `0.85` and judged usefulness at
least `0.80`, with no source-family regression greater than five points. Planned-search p95 must be
at most eight seconds and fast-search p95 at most two seconds on the frozen workload.

## 8. ZEN

- **Simple:** one canonical evidence contract and one connector contract.
- **General:** provider-neutral Postgres, S3-compatible or filesystem archive, and out-of-process
  connectors.
- **Agentic:** agents judge relevance, synthesis, contradictions, and gaps; code owns identity,
  authorization, pagination, deletion, and receipts.
- **Beautiful:** every answer can move backward to evidence and every projection can rebuild
  forward from evidence.
- **Dope:** the owner can ask one natural-language question from any agent and recover trustworthy
  work from any selected source without surrendering control of the raw data.
