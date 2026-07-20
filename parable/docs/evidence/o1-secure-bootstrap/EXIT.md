# O1 — SECURE LOCAL BOOTSTRAP · EXIT (2026-07-20)

## Status: COMPLETE

## The headline evidence

`parable setup` now creates one private, loopback-only configuration for an
exact Sol parent and the selected GPT, Claude, and xAI child families.
`parable proxy build` verifies the pinned CLIProxyAPI source commit and
vendored patch before patch, tests, or build. The complete package suite passes
91/91; all build proofs are hermetic.

## What shipped

| Piece | Where |
|---|---|
| Setup/build implementation | `lib/onboarding.js` |
| CLI dispatch and first-install guard | `bin/parable.js` |
| Hermetic setup/build integration proof | `tests/test_integration.py` |
| Sanitized receipt | `docs/evidence/o1-secure-bootstrap/receipt.json` |

Global `parable install` no longer creates a lone default `parable.toml` for a
new user. That file would be indistinguishable from unsafe partial onboarding
state. Project and explicit-target installs retain their existing example-file
behavior, while existing global configs are preserved.

## Bound accounting (honest)

- Correctly wired PROVE runs: 2/2 maximum; the successful second run is 9/9.
- Evidence failures: 1. The first focused proof found that Node's readline
  `question()` could consume piped input and then exit zero with no setup after
  the first answer. A queued async line iterator now consumes every answer and
  makes premature EOF an explicit error.
- Instrument failures: 0.
- Review rounds: 2/3. The second review closed the legacy global-install
  partial-state trap and added the onboarding module to the package syntax
  gate.
- Provider turns: 0. OAuth flows: 0. Real build-network calls: 0.

ZEN check: one declarative vendor/model table renders exact subsets; one
ordinary subprocess path builds the pinned proxy; file-state judgment is
structural and fail-closed. No broker, provider credential adapter, callback
parser, model regex, fallback router, or background service was added.

## Accept criteria → evidence

1. ChatGPT mandatory; Claude/xAI optional; Kimi absent — ✅ interactive and
   non-interactive tests cover ChatGPT-only, ChatGPT+Claude, all three active
   vendors, missing ChatGPT, unsupported Kimi, and exact generated models.
2. Atomic private files, loopback, no token output, no overwrite — ✅ setup
   creates two mode-0700 directories and four mode-0600 regular files using
   exclusive atomic links; token checks cover stdout, stderr, manifest, and
   Parable TOML; partial, symlinked, over-permissive, changed, and differently
   selected state all fail without overwrite.
3. Exact selected routing validates through Parable — ✅ every successful setup
   invokes `parable.py config --validate`; subset tests pin Sol, Terra, Luna,
   exact optional Claude ids, and exact Grok 4.5.
4. Managed builder verifies and isolates — ✅ fake `git`/`go` tests prove patch
   checksum before subprocesses, exact source HEAD before `git am`, both pinned
   Go test slices before build, private executable output, rollback on mismatch,
   and refusal to touch an existing destination.
5. Unit/integration fake-success guards — ✅ focused onboarding proof 9/9;
   complete package proof 91/91; npm package dry-run includes the new module.
6. Focused PR and merged `main` — ✅ recorded below.

## The running delta table (O0→On)

| Loop | Shipped | Headline |
|---|---|---|
| O0 | Contract + baseline | No setup/auth/proxy surface; 81/81 baseline |
| O1 | Secure setup + pinned builder | 9/9 focused; 91/91 complete; zero OAuth/model work |

## Merge verification

- JSON invariants and privacy boundary: PASS.
- Complete Parable suite: PASS, 91/91.
- npm pack dry-run: PASS, 33 files including `lib/onboarding.js`.
- Local Claudio-Michel review: 5/5.
- Focused PR: [#193](https://github.com/miguelrios/unc-skills/pull/193),
  merged 2026-07-20T22:42:31Z.
- Verified merged `main`: `b57c8852c83820e4cc8cfb9fca338c3c6876418b`.

## exit → O2

Add native vendor-auth delegation, credential-safe status, and foreground
proxy lifecycle without parsing or copying any OAuth material.
