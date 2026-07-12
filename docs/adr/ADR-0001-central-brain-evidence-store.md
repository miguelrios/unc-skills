# ADR-0001 — Use recall-native Postgres for the central evidence plane

**Status:** Accepted for C1, falsifiable at later exit gates  
**Date:** 2026-07-12

## Context

Recall v2 has precise session receipts and measured lexical/entity retrieval, but its SQLite
index is local and single-machine. C0 compared three backend shapes using the same three-record
collision corpus and the same required outcomes: exact receipt at rank 1, zero cross-source
leaks, zero duplicates on retry, full record/export fidelity, and measured setup/retrieval
latency.

## Decision

Implement the canonical evidence plane directly on Postgres behind a `BrainStore` contract.
Keep GBrain available as a later curated-page/synthesis bridge and keep managed-memory systems
as derived/index adapters or benchmark competitors. Neither becomes the raw source of truth.

## Measured evidence

Source: `docs/evidence/c0-backend-decision/scorecard.json` and the three backend traces.

| Backend spike | Receipt@1 | leaks | retry duplicates | round-trip/export | setup | retrieval p95 |
|---|---:|---:|---:|---:|---:|---:|
| recall-native Postgres 17 | 1.0 | 0 | 0 | 1.0 / 1.0 | 0.125s | 35.011ms |
| GBrain 0.42.58 PGLite | 1.0 | 0 | 0 | 1.0 / 1.0 | 14.211s | 2166.063ms |
| managed-memory HTTP contract | 1.0 | 0 | 0 | 1.0 / 1.0 | 0.0053s | 1.068ms |

Latency is directional, not a product benchmark:

- Postgres timing includes a new `psql` process for each query, so server-side queries should
  be faster than the measured number.
- GBrain timing intentionally invokes a fresh Bun/CLI process for each search. It proves the
  adapter and source-scope path, not persistent MCP latency.
- The managed-memory row is a local, contract-faithful HTTP conformance server because no
  vendor credential was available. It proves adapter/reversal mechanics only and makes no
  claim about Mem0/Zep/Supermemory retrieval quality, durability, or latency.

## Why native wins

1. The canonical object is already a source event with an exact native receipt. Postgres can
   persist that envelope without converting it to a page or pre-extracted memory.
2. Source/principal predicates can be enforced in every SQL query and fuzzed against same-ID
   collisions.
3. It preserves the option to rebuild FTS, pgvector, graph, GBrain pages, or managed memories
   from one immutable event log.
4. It has the fewest transformation steps and no vendor-specific canonical record type.
5. It yields the cleanest rollback: export envelopes and point the unchanged local parser/index
   at them, rather than reverse-extracting sessions from pages or memories.

## Rejected as canonical backends

### GBrain

GBrain is real and compelling: the spike initialized its current PGLite engine, registered two
isolated sources, captured idempotently, searched in-source, and exported full JSON embedded in
a page. It already provides many later-loop capabilities. It is not selected as canonical
because transcript turns must be wrapped in its page model, production HTTP uses its Postgres
service and OAuth schema, and exact `show --around/--tail/--prompts` remains recall-specific.
This would create two product contracts at the most sensitive layer.

### Managed memory API

A managed service is attractive for semantic extraction and low operator burden. Without a live
credential C0 cannot honestly grade vendor behavior. More importantly, a managed memory/fact
record is derived from—not equivalent to—the source transcript or artifact. It may be added
later behind `BrainStore` projections after vendor E2E and deletion/export gates pass.

## Rollback boundary

C1 owns only the event/envelope store, source grants, ingest ledger, and deterministic
projections. It does not introduce embeddings, fact extraction, graph, synthesis, OAuth edge,
or default client cutover. Every canonical row exports as envelope v1. Until C10, local recall
remains the default and the central store is shadow-only. Rolling back C1 means stopping writes,
exporting/replaying acknowledged envelopes, and returning collectors to local indexing.

## Consequences

- We implement a small service and migrations instead of adopting an entire brain runtime.
- We must build HTTP/MCP/OAuth/operations that GBrain already has, but only after the evidence
  plane is stable and with recall-specific hard exits.
- GBrain remains a first-class comparison and optional bridge. C8/C9 should reuse its proven
  concepts—RRF, graph, cited synthesis, gap analysis—rather than inventing alternatives.
- The decision is automatically reopened if C1 cannot achieve full receipt equivalence, zero
  isolation leaks, and durable idempotency inside its bound.
