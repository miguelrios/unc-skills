# O4 — OSS DELIVERY AND RELEASE HANDOFF · EXIT (2026-07-20)

## Status: COMPLETE — AWAITING HUMAN RELEASE VERDICT

## The headline evidence

The shipped source now has one truthful first-run path from install through
exact Sol launch. A controlled live read-only smoke returned HTTP 200 with 37
catalog entries and exactly one of each seven required ids. The proxy was
stopped and its listener closed afterward. The complete package suite passes
99/99 and the 33-file npm dry-run includes every runtime dependency.

## What shipped

| Piece | Where |
|---|---|
| Canonical OSS path and install surface | `README.md`, `install.sh` |
| Complete onboarding/operations guide | `docs/CLIPROXYAPI_GPT_SUBSCRIPTION.md` |
| Provider and in-session guidance | `skills/parable/references/providers.md`, `skills/parable/SKILL.md` |
| Final legacy-installer regression test | `tests/test_integration.py` |
| Sanitized live/package receipt | `docs/evidence/o4-oss-delivery/receipt.json` |
| Standalone visual field manual | `parable-oss-subscription-onboarding-2026-07-20.html` |

The companion HTML lives in the operator's docs directory rather than the npm
tarball. It was parsed and rendered at desktop and mobile widths. The
`frontend-design` skill shaped it as a high-contrast editorial field manual
with an orange-crab motif, responsive runbook, and copyable commands.

## Bound accounting (honest)

- Correctly wired documentation/live proofs: 1/2 maximum; passed.
- Evidence failures: 0.
- Instrument failures: 1. The first broad process-name probe matched its own
  shell command; the simultaneous listener probe correctly showed that no
  proxy was running. The controlled start/stop used an exact process match.
- Review rounds: 2/3. Round two caught and fixed the legacy `install.sh`
  partial-config trap, added its regression, and rendered both HTML layouts.
- Live catalog HTTP requests: 1. Provider turns: 0. OAuth flows: 0. Provider
  record mutations: 0.

ZEN check: one source CLI path, one loopback proxy, one exact catalog gate, and
one declarative cast. The docs delete the old hand-built configuration ritual
from the canonical path and introduce no broker, alias, fallback, daemon, or
credential adapter.

## Accept criteria → evidence

1. Hermetic/live exact-model agreement — ✅ both require exact Sol, Terra,
   Luna, Sonnet, Opus, Haiku, and Grok; the live catalog contains each exactly
   once and the hermetic all-vendor E2E generates the six intended agents.
2. One canonical first-run path — ✅ README, setup guide, provider reference,
   skill, CLI help, and standalone HTML agree on install→setup/auth→foreground
   proxy→finalize→Sol launch. Headless device auth is the documented branch.
3. Complete tests, privacy, and package — ✅ 99/99 tests, JSON/privacy checks,
   `git diff --check`, HTML desktop/mobile renders, and 33-file npm dry-run.
4. Focused PR and merged `main` — ✅ recorded below.
5. npm remains an explicit human gate — ✅ source is `0.1.9`, registry remains
   `0.1.7`, `publish_authorized=false`, and no publish command was executed.

## The completed delta table

| Loop | Shipped | Headline |
|---|---|---|
| O0 | Contract + baseline | No setup/auth/proxy surface; 81/81 baseline |
| O1 | Secure setup + pinned builder | 9/9 focused; 91/91 complete |
| O2 | Native auth + safe status + foreground proxy | 5/5 focused; 96/96 complete |
| O3 | Exact finalize + first launch | 2/2 E2E; 98/98 complete |
| O4 | Live smoke + OSS delivery | 7/7 exact live ids; 99/99 complete |

## Merge verification

- Live read-only catalog smoke: PASS, 7/7 expected exact ids.
- JSON and privacy boundary: PASS.
- Complete Parable suite: PASS, 99/99.
- npm pack dry-run: PASS, 33 files.
- Desktop/mobile companion renders: PASS.
- Local Claudio-Michel review: 5/5.
- Focused PR: [#199](https://github.com/miguelrios/unc-skills/pull/199),
  merged 2026-07-20T23:13:41Z.
- Verified merged `main`: `2596278bcde21141ddcb9e74cff8adb114c49ae6`.

## exit → human release verdict

No npm publication has been inferred. After explicit approval, rerun
`npm test`, `npm run pack:check`, then `npm publish --access public` from the
`parable` package directory.
