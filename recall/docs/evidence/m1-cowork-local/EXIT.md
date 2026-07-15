# M1 — LOCAL SOURCE ADAPTERS · EXIT (2026-07-14)

## Status: COMPLETE

## The headline evidence

The bounded Cowork adapter passes eight focused synthetic tests and all 189 repository tests.
Unchanged replay acknowledges zero new records, append acknowledges exactly one, and local absence
emits no tombstone. Five planted privacy classes are absent from both network requests and spool
bytes under `scrub` and `drop`. No private source was read to produce repository evidence.

## What shipped

| Piece | Where |
|---|---|
| Bounded Cowork local-project adapter | `connectors/cowork_local.py` |
| Pre-spool privacy, replay, append, and lifecycle pins | `tests/test_cowork_local.py` |
| Frozen source-selection contract | `tests/cowork_local_v1/` |
| Content-free aggregate evidence | `docs/evidence/m1-cowork-local/attestation.json` |

## Bound accounting (honest)

- PROVE runs used: 1 evidence-bearing full run, passed.
- Expected RED run: adapter class absent, then GREEN after implementation.
- Instrument failures: 0.
- Review rounds used: 0 before PR review.

## Accept criteria → evidence

1. Synthetic user/assistant messages normalize to stable canonical events — ✅ frozen corpus plus
   `test_exact_project_tree_is_paginated_path_free_and_excludes_ambient_files`.
2. Planted PAT, API key, email, phone, and address never enter spool bytes under `scrub`/`drop` —
   ✅ `test_runner_scrubs_before_spool_and_network_and_drop_omits_the_record` and the zero-match
   counters in `attestation.json`.
3. Audit, system, reasoning, tool, and attachment content produce zero events — ✅ projection
   contract tests plus ambient-file exclusion in
   `test_exact_project_tree_is_paginated_path_free_and_excludes_ambient_files`.
4. Replay commits zero duplicates and one append commits exactly one — ✅
   `test_replay_append_change_and_missing_file_never_duplicate_or_delete`.
5. Malformed, oversized, replaced, and symlinked inputs fail closed without checkpoint advance — ✅
   `test_partial_line_is_deferred_and_malformed_oversized_or_unsafe_files_fail_closed` directly
   pins the committed cursor and empty pending-page state across malformed input.
6. Existing Codex, Claude Code, privacy, connector, server, and package behavior remains green — ✅
   `npm test`: 189 passed, zero failed.
7. Every criterion maps to evidence at HEAD — ✅ this document and referenced synthetic tests.

## The running delta table (M0→Mn)

| Loop | Shipped | Headline |
|---|---|---|
| M0 | Closed Cowork selection/identity contract | 15 synthetic cases; 4 focused + 185 full tests green |
| M1 | Bounded Cowork adapter through privacy/ACK runner | 8 focused + 189 full tests; replay 0, append 1, tombstones 0 |

## ZEN check

- **Simple:** one narrow root pattern and the existing connector runner.
- **General:** stable native identity, ACK-ledger replay suppression, and privacy-before-spool apply
  without source-specific deletion heuristics.
- **Prompt/agentic-oriented:** content selection stays a small explicit contract; semantic privacy
  judgment remains in the shared policy layer.
- **Beautiful:** cursors and evidence are opaque/content-free; source paths never become records.
- **Dope:** a live-changing local log can now feed the central brain without copying ambient app state.

## exit → M2

Expose Codex, Claude Code, Cowork, and export-inbox ingestion through one reproducible Mac utility
with content-free lifecycle and health surfaces.
