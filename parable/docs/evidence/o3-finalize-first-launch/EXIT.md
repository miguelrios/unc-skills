# O3 — CATALOG FINALIZE AND FIRST LAUNCH · EXIT (2026-07-20)

## Status: COMPLETE

## The headline evidence

`parable setup finalize` now authenticates to the loopback catalog with the
private generated client token, requires every selected exact model id, writes
or confirms the exact project-local cast, and prints the next launch command.
Finalize and `parable claude` share the same reconcile function. The complete
hermetic setup→auth-wrapper→catalog→finalize→Claude proof passes.

## What shipped

| Piece | Where |
|---|---|
| Setup token handoff and finalize dispatch | `bin/parable.js`, `lib/onboarding.js` |
| Shared exact catalog/cast reconcile | `skills/parable/scripts/parable.py` |
| All-vendor, subset, and missing-id E2E | `tests/test_integration.py` |
| Sanitized receipt | `docs/evidence/o3-finalize-first-launch/receipt.json` |

Users no longer need to source `cliproxy.env` before finalization or launch.
The Node entrypoint reads the private generated token, passes it only to the
Python child, and the Claude launcher converts it to `ANTHROPIC_AUTH_TOKEN`
while removing the source environment name before stock Claude starts.

## Bound accounting (honest)

- Correctly wired PROVE runs: 1/2 maximum; passed 2/2 multi-stage E2Es.
- Evidence failures: 0.
- Instrument failures: 0.
- Review rounds: 1/3. Shared reconcile ordering, exact membership, token
  lifetime, idempotency, subset cast, misleading aliases, user-owned files,
  and direct-launch bypass were checked.
- Provider turns: 0. OAuth flows: 0. Live provider calls: 0.

ZEN check: one exact-id set check drives both finalize and launch; existing
declarative executor config drives cast rendering. No display alias, regex,
fallback, second router, broker, or token-copy artifact was added.

## Accept criteria → evidence

1. Selected ids produce the intended cast — ✅ the all-vendor E2E produces
   exact Sol plus six exact named agents; ChatGPT-only produces exact Sol plus
   Terra and Luna. A second finalize is 0 changed / 6 unchanged / 0 removed.
2. Missing ids fail closed — ✅ a catalog containing `grok-4.5-latest` and
   uppercase `GROK-4.5`, but not exact `grok-4.5`, fails before any generated
   agent or Claude invocation.
3. First-launch local-token handoff — ✅ the generated token authorizes both
   catalog checks without appearing in output, agent files, fake-Claude
   capture, or Claude's source environment; fake stock Claude receives exact
   `--model gpt-5.6-sol`.
4. Unrelated project state remains untouched — ✅ an ordinary handwritten
   agent and an unowned `parable-*` agent survive both finalize and launch;
   no project configuration file is written or changed.
5. Focused PR and merged `main` — ✅ recorded below.

## The running delta table (O0→On)

| Loop | Shipped | Headline |
|---|---|---|
| O0 | Contract + baseline | No setup/auth/proxy surface; 81/81 baseline |
| O1 | Secure setup + pinned builder | 9/9 focused; 91/91 complete |
| O2 | Native auth + safe status + foreground proxy | 5/5 focused; 96/96 complete |
| O3 | Exact finalize + first launch | 2/2 E2E; 98/98 complete |

## Merge verification

- JSON invariants and privacy boundary: PASS.
- Complete Parable suite: PASS, 98/98.
- npm pack dry-run: PASS, 33 files.
- Local Claudio-Michel review: 5/5.
- Focused PR: [#197](https://github.com/miguelrios/unc-skills/pull/197),
  merged 2026-07-20T23:01:01Z.
- Verified merged `main`: `6504f6e347f6190f60a8037e7c14c8f782d3758c`.

## exit → O4

Run the zero-turn live read-only catalog smoke, replace the manual first-run
story with the shipped CLI path, and prepare the explicit npm release handoff.
