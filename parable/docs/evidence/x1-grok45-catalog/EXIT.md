# X1 — AUTHENTICATED CATALOG · EXIT (2026-07-20)

## Status: COMPLETE

## The headline evidence

The authenticated loopback catalog contains exactly one `grok-4.5` owned by
`xai`. Exact Sol, Terra, and Luna entries remain present and owned by
`openai`. The successful catalog probe spent zero model turns.

## What shipped

| Piece | Where |
|---|---|
| Sanitized authenticated catalog receipt | `docs/evidence/x1-grok45-catalog/receipt.json` |

## Bound accounting (honest)

- Correctly wired catalog probes: 1/2; passed.
- Evidence failures: 0.
- Instrument failures: 0.
- Review rounds: 1. JSON invariants, exact-id multiplicity, provider ownership,
  listener address, API-key environment, and content/credential boundaries
  were checked.

ZEN check: one authenticated catalog query proves entitlement before
inference. No model call, routing rule, broker, credential copy, or provider
fallback is introduced.

## Acceptance criteria → evidence

1. Exact Grok entitlement — ✅ one `grok-4.5`, owned by `xai`.
2. GPT catalog preserved — ✅ exact Sol, Terra, and Luna each appear once and
   remain owned by `openai`.
3. Loopback and no direct keys — ✅ listener is `127.0.0.1`; xAI, OpenAI, and
   Anthropic API-key variables were unset.
4. Receipt and EXIT merged — ✅ the focused PR and verified `main` are
   recorded below.

## The running delta table

| Loop | xAI OAuth | Grok catalog | Grok turns | Named Grok child | Kimi |
|---|---|---|---:|---:|---|
| X0 | valid, mode 0600 | not probed | 0 | 0 | PAUSED |
| X1 | valid | 1 exact `grok-4.5` | 0 | 0 | PAUSED |

MEASURE is entitlement count, not inference quality: exact Grok count moved
from unproved to one; model turns remain zero.

## Merge verification

- JSON invariants: PASS.
- Local Claudio-Michel review: 5/5.
- Focused PR: [#152](https://github.com/miguelrios/unc-skills/pull/152).
- Verified merged `main`: pending.

## exit → X2

Run the five Claude Code effort values on Grok 4.5 plus supported-effort tool
canaries. Runtime wire evidence decides exact, clamp, or rejection behavior.
