# M0 — TWO-COMMAND CONTRACT AND BASELINE · EXIT (2026-07-20)

## Status: COMPLETE

## The headline evidence

Merged `main` needs four runtime commands across two terminals after install.
The target is two commands in one terminal: `parable setup`, then
`parable claude`. Baseline proof passes 99/99 tests and a 33-file package
dry-run without an OAuth flow, credential read, or provider turn.

## What shipped

| Piece | Where |
|---|---|
| Successor chain and bounded lifecycle contract | `docs/LOOP_CHAIN_2026-07-20-magical-two-command.md` |
| Sanitized baseline and target receipt | `docs/evidence/m0-two-command-contract/receipt.json` |

## Bound accounting (honest)

- Correctly wired proof runs: 1/2; passed.
- Evidence failures: 0.
- Instrument failures: 0.
- Review rounds: 1/3 local review; focused PR recorded below.
- Provider turns / OAuth flows / credential reads: 0 / 0 / 0.

ZEN check: keep native OAuth and exact catalog judgment where they already
live. Add one ordinary parent/child lifecycle, not a daemon, lock service,
broker, background installer, or second routing engine.

## Accept criteria → evidence

1. Sanitized current lifecycle and baseline — ✅ `receipt.json` records four
   runtime commands, two terminals, 99 passing tests, and 33 package files.
2. Child ownership and exit contract — ✅ the chain requires reuse without
   ownership, private spawn with ownership, readiness before launch, signal
   forwarding, meaningful exit preservation, and owned-child cleanup.
3. Fake-success guards — ✅ the M1 accept contract names timeout, collision,
   early proxy exit, signals, cleanup, missing exact ids, and token leakage.
4. Focused PR and merged `main` — ✅ recorded below.

## The running delta table (M0→Mn)

| Loop | Shipped | Headline |
|---|---|---|
| M0 | Contract + baseline | 4 commands / 2 terminals; 99/99 baseline |

## Merge verification

- JSON parse and privacy boundary: PASS.
- Complete Parable baseline: PASS, 99/99.
- npm pack dry-run: PASS, 33 files.
- Focused PR: [#202](https://github.com/miguelrios/unc-skills/pull/202),
  merged 2026-07-20T23:46:16Z.
- Verified merged `main`: `15023494ef55cea41865d6b41ba4cf628a0dc4ca`.

## exit → M1

Implement the owned-or-reused proxy supervisor and preserve the existing exact
catalog reconciliation before stock Claude Code starts.
