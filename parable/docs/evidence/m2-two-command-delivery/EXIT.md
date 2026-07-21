# M2 — TWO-COMMAND DELIVERY AND RELEASE HANDOFF · EXIT (2026-07-21)

## Status: COMPLETE — AWAITING HUMAN RELEASE VERDICT

## The headline evidence

Seven public entry points now agree on the ordinary path: `parable setup`, then
`parable claude` in one terminal. The complete package passes 107/107 tests;
the inherited lifecycle matrix passes seven cases plus three signals with zero
orphans; and the 33-file package dry-run contains the complete runtime.

## What shipped

| Piece | Where |
|---|---|
| Canonical two-command source onboarding | `README.md` |
| Full ordinary, headless, and diagnostic operations guide | `docs/CLIPROXYAPI_GPT_SUBSCRIPTION.md` |
| In-session and provider guidance | `skills/parable/SKILL.md`, `skills/parable/references/providers.md` |
| CLI and legacy-installer next-step language | `bin/parable.js`, `lib/onboarding.js`, `install.sh` |
| Cross-surface regression | `tests/test_integration.py` |
| Responsive standalone field manual | operator docs directory |
| Sanitized delivery receipt | `docs/evidence/m2-two-command-delivery/receipt.json` |

The `frontend-design` skill kept the standalone manual's editorial orange-crab
language while rebuilding the runbook around two elevated primary command
cards. Desktop and mobile reduced-motion renders were inspected so every
section was visible without animation timing.

## Bound accounting (honest)

- Correctly wired PROVE runs: 2; documentation/HTML gate and complete package
  gate passed after one finding was fixed.
- Evidence failures: 1/2. The first cross-surface check found that the provider
  reference described “diagnostic finalize” without naming the copyable
  `parable setup finalize` command. The reference was corrected and the same
  gate passed.
- Instrument failures: 0.
- Review rounds: 2/3; cross-surface consistency, then desktop/mobile visual and
  package review.
- Provider turns / OAuth flows / external listener mutations: 0 / 0 / 0.

ZEN check: the public story is the actual product story—one setup command and
one supervised launch. Headless auth and foreground diagnostics remain visible
without competing with the default. No daemon, broker, alias, fallback, or
credential adapter was introduced.

## Accept criteria → evidence

1. One canonical path — ✅ README, full guide, skill, provider reference, CLI
   help, installer output, and standalone HTML say `parable setup` then
   `parable claude`; diagnostics are explicitly exceptional.
2. Complete lifecycle proof — ✅ seven clean/reuse/collision/timeout/failure
   cases, SIGINT/SIGTERM/SIGHUP, and zero-orphan assertions remain green.
3. Delivery gate — ✅ 107/107 tests, privacy check, relative-link check,
   33-file package dry-run, HTML parse, and desktop/mobile renders pass.
4. Focused PR and merged `main` — ✅ recorded below.
5. Explicit release gate — ✅ source is `0.1.9`, registry is `0.1.7`,
   `publishAuthorized=false`, and no publish command was executed.

## The running delta table (M0→Mn)

| Loop | Shipped | Headline |
|---|---|---|
| M0 | Contract + baseline | 4 commands / 2 terminals; 99/99 baseline |
| M1 | Self-bootstrap + supervisor | 2 commands / 1 terminal; 106/106 complete |
| M2 | Unified delivery | 7 surfaces aligned; 107/107 complete |

## Merge verification

- Documentation/HTML two-command gate: PASS.
- Lifecycle matrix: PASS, 7/7 cases plus 3/3 signals, zero orphans.
- Complete Parable suite: PASS, 107/107.
- npm pack dry-run: PASS, 33 files.
- Desktop/mobile companion renders: PASS.
- Focused PR: [#206](https://github.com/miguelrios/unc-skills/pull/206),
  merged 2026-07-21T00:08:26Z.
- Verified merged `main`: `c4151dc273cbc081c9e4a2d9137d945e85fd8a0e`.

## exit → human release verdict

No npm publication has been inferred. After explicit approval, rerun
`npm test`, `npm run pack:check`, then `npm publish --access public` from the
`parable` package directory.
