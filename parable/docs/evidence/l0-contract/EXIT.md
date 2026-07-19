# L0 — CONTRACT AND BASELINE · EXIT (2026-07-19)

## Status: COMPLETE

## The headline evidence

The pinned source contract contains both target models, both subscription OAuth
channels, and both Claude-compatible executor paths. Parable's existing suite
passes 70/70 under an isolated temporary HOME.

## What shipped

| Piece | Where |
|---|---|
| End-to-end cascade | `docs/LOOP_CHAIN_2026-07-18-oss-subscription-routing.md` |
| Sanitized runtime and upstream pin | `docs/evidence/l0-contract/manifest.json` |
| Pinned routing contract | `docs/evidence/l0-contract/contract.md` |
| Content-free live receipt schema | `docs/evidence/l0-contract/receipt.schema.json` |

## Bound accounting (honest)

- Correctly wired PROVE runs: 1, passed.
- Evidence failures: 0.
- Review rounds: 0 before PR open.
- Instrument failure 1: a process check using a broad command-line search
  matched its own invocation and falsely reported CLIProxyAPI as running. It was
  replaced by an exact process-name check; the true baseline is not installed,
  not running.
- Instrument failure 2: the first `npm test` inherited a personal Parable config
  and failed three fixture-driven integration tests during unrelated config
  validation. The standalone 0.1.7 suite passed 47/47 under an isolated
  temporary HOME. No proof
  attempt was consumed because the first run never exercised the intended
  fixtures.
- Instrument failure 3: the first repository target was the archived standalone
  `Parcha-ai/parable` repository, which GitHub correctly rejected as read-only.
  The canonical writable source is the `parable/` package in
  `miguelrios/unc-skills`; work moved to a clean worktree at its current `main`,
  and its 0.1.8 suite passed 70/70. No commit reached the archived remote.

## Accept criteria → evidence

1. Safe manifest with no credentials or identifying local paths — ✅
   `manifest.json`; its values are versions, hashes, booleans, and a sanitized
   command only.
2. Four routing prerequisites mapped to pinned evidence — ✅ `contract.md`
   covers both catalogs, both OAuth channels, Claude-compatible execution, and
   named-agent routing.
3. Content-free receipt distinguishes parent and child — ✅
   `receipt.schema.json` requires role, provider channel, requested model, and
   an isolated-proxy runtime assertion while disallowing undeclared fields.
4. Existing tests pass at the recorded baseline HEAD — ✅ 70/70 with
   `HOME=<isolated-temporary-home> npm test`, baseline
   `4005ad281ce5d2e9bd301f82c66a70c8d1ac445f`.
5. Focused PR merged and verified at merged HEAD — ⏳ filled during MERGE before
   L1 advances.

## The running delta table (L0→Ln)

| Loop | P1: Sol session | P2: Sol → Kimi subagent | OSS clean-room |
|---|---:|---:|---:|
| L0 | NOT RUN | NOT RUN | NOT RUN |

MEASURE is skipped for L0: it establishes the baseline and moves no product
metric.

## exit → L1

After the L0 PR is merged and verified at HEAD, install the pinned CLIProxyAPI
release and run the Sol subscription canary. Browser consent may require the
operator, but no credential material is requested.
