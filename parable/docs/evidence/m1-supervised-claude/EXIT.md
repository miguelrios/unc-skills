# M1 — SELF-BOOTSTRAP AND SUPERVISED CLAUDE · EXIT (2026-07-21)

## Status: COMPLETE

## The headline evidence

The hermetic lifecycle matrix passes seven cases plus all three forwarded
signals. `parable claude` starts an absent configured proxy, waits for its
authenticated catalog, runs the existing exact-id reconciliation, launches
stock Claude, preserves meaningful child exits, and leaves zero child
processes. A healthy existing endpoint is reused and never stopped. The
complete suite moves from 99/99 to 106/106.

## What shipped

| Piece | Where |
|---|---|
| Managed-or-reused proxy supervisor | `lib/onboarding.js` |
| Supervised Claude CLI entry and exit propagation | `bin/parable.js` |
| Hermetic lifecycle, collision, timeout, failure, signal, and cleanup proofs | `tests/test_integration.py` |
| Sanitized mechanism receipt | `docs/evidence/m1-supervised-claude/receipt.json` |

## Bound accounting (honest)

- Correctly wired PROVE runs: 2; targeted lifecycle matrix and complete suite
  both passed.
- Evidence failures: 0.
- Instrument failures: 1. The first positive interactive-build test inherited
  this host's installed proxy and therefore skipped the build prompt. Isolating
  `PATH` made the test exercise the intended missing-proxy branch; it passed.
- Review rounds: 2/3. The second review added an after-probe child-exit check so
  an early exit wins over a simultaneous connection error.
- Provider turns / OAuth flows / external listener mutations: 0 / 0 / 0.

ZEN check: the launcher is one ordinary owner-or-borrower supervisor. It adds
no daemon, pidfile, broker, routing engine, shared key, or background service.
Exact model judgment remains in the existing Python reconcile path.

## Accept criteria → evidence

1. Clean interactive setup without `--build-proxy` — ✅ isolated fake
   `git`/`go` proof accepts the explicit build prompt and creates the pinned
   managed binary.
2. One-terminal lifecycle — ✅ owned-proxy proof reaches authenticated
   readiness, exact reconciliation, fake Claude, exit 23, and owned cleanup.
3. Safe reuse/collision behavior — ✅ authenticated existing endpoint is reused
   with zero proxy subprocess calls; HTTP 401 listener fails before proxy or
   Claude and is not stopped.
4. Failure, signal, and cleanup behavior — ✅ early exit 17, timeout, mid-session
   exit 19, SIGINT/SIGTERM/SIGHUP, and zero-orphan checks all pass.
5. Exact-id and credential guards — ✅ prior exact-catalog failures remain green;
   the generated token appears in no stdout, stderr, or subprocess receipt.
6. Focused PR and merged `main` — ✅ recorded below.

## The running delta table (M0→Mn)

| Loop | Shipped | Headline |
|---|---|---|
| M0 | Contract + baseline | 4 commands / 2 terminals; 99/99 baseline |
| M1 | Self-bootstrap + supervisor | 2 commands / 1 terminal; 106/106 complete |

## Merge verification

- Targeted lifecycle matrix: PASS, 7/7 plus 3/3 signals.
- Complete Parable suite: PASS, 106/106.
- npm pack dry-run: PASS, 33 files.
- Privacy and diff checks: PASS.
- Focused PR: [#204](https://github.com/miguelrios/unc-skills/pull/204),
  merged 2026-07-20T23:58:36Z.
- Verified merged `main`: `47f7d4fb665302c39068f47afbf08c84b72ff7d8`.

## exit → M2

Align every public onboarding entry point with `parable setup` then
`parable claude`, rerun the full hermetic delivery gate, and stop at the
explicit npm release verdict.
