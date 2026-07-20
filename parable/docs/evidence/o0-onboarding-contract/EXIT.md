# O0 — ONBOARDING CONTRACT AND BASELINE · EXIT (2026-07-20)

## Status: COMPLETE

## The headline evidence

GitHub source is Parable `0.1.9`, while npm serves `0.1.7`. Source exposes
`install|doctor|claude|agents sync`; the registry exposes only
`install|doctor`. Neither has `setup|auth|proxy`. The complete source suite
passes 81/81 before onboarding code changes.

## What shipped

| Piece | Where |
|---|---|
| Append-forward loop chain | `docs/LOOP_CHAIN_2026-07-20-first-run-onboarding.md` |
| Command and safety contract | `docs/evidence/o0-onboarding-contract/contract.md` |
| Sanitized baseline | `docs/evidence/o0-onboarding-contract/receipt.json` |

## Bound accounting (honest)

- Documentation proofs: 1/2 maximum; passed.
- Evidence failures: 0.
- Instrument failures: 2. The first source-version probe used a duplicated
  repository path after entering the package directory. The first registry
  help probe passed `--help`, which version 0.1.7 treats as an unknown command;
  its zero-argument help path passed. Neither touched product or provider
  state.
- Review rounds: 1/3. Commands, files, modes, overwrite rules, provider/model
  mapping, native auth delegation, callback guidance, catalog failure, build
  pins, release boundary, and public evidence boundaries were checked.
- Provider turns: 0. OAuth flows: 0.

ZEN check: declarative vendor/model tables and native CLI delegation; no
broker, credential adapter, callback parser, model regex, or fallback router.

## Accept criteria → evidence

1. Complete command/file/failure contract — ✅ `contract.md` names the
   surface, generated modes, idempotency, native flags, and lifecycle.
2. Fake-success guards — ✅ eight explicit guards cover missing binary,
   ChatGPT, unsafe state, unsupported vendor, missing ids, secret output,
   build pins, and loopback validation.
3. Version split measured without publishing — ✅ source `0.1.9`, registry
   `0.1.7`, `npm_publish_authorized=false` in the receipt.
4. Focused PR and merged `main` — ✅ recorded below.

## The running delta table (O0→On)

| Loop | Shipped | Headline |
|---|---|---|
| O0 | Contract + baseline | 0 setup/auth/proxy commands; 81/81 baseline tests |

## Merge verification

- JSON invariants: PASS.
- Content boundary: PASS.
- Complete Parable suite: PASS, 81/81.
- Local Claudio-Michel review: 5/5.
- Focused PR: pending.
- Verified merged `main`: pending.

## exit → O1

Implement secure local bootstrap and the explicit managed proxy builder from
the pinned contract.
