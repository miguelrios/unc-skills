# Y3 — AUTHENTICATED CLAUDE CATALOG · EXIT (2026-07-20)

## Status: COMPLETE

## The headline evidence

The authenticated loopback catalog now contains 14 Anthropic entries.
The successor pins one exact full id for each planned child lane:

| Lane | Exact id | Owner |
|---|---|---|
| Sonnet | `claude-sonnet-5` | `anthropic` |
| Opus | `claude-opus-4-8` | `anthropic` |
| Haiku | `claude-haiku-4-5-20251001` | `anthropic` |

Exact Sol, Terra, Luna, and Grok remain present. The catalog probe spent zero
model turns.

## What shipped

| Piece | Where |
|---|---|
| Sanitized authenticated catalog receipt | `docs/evidence/y3-claude-catalog/receipt.json` |

## Bound accounting (honest)

- Correctly wired catalog probes: 2/2; passed.
- Evidence failures: 0.
- Instrument failures: 1. An aggregate-only `jq` expression failed to compile;
  the authenticated response was unchanged and the corrected redacting
  expression passed.
- Review rounds: 1. Exact-id multiplicity, ownership, retained providers,
  listener, API-key environment, zero-turn accounting, and content boundaries
  were checked.

ZEN check: the authenticated catalog selects full ids before inference.
No display alias, name regex, fallback model, provider call, or entitlement
guess is used.

## Acceptance criteria → evidence

1. Exact Claude children — ✅ one exact Anthropic-owned id is pinned for each
   Sonnet, Opus, and Haiku lane.
2. Existing routes preserved — ✅ exact Sol/Terra/Luna/Grok each remain
   present once under their original owner.
3. No direct keys — ✅ Anthropic, OpenAI, and xAI API-key variables are unset.
4. Receipt and EXIT merged — ✅ the focused PR and verified `main` are
   recorded below.

## Merge verification

- JSON invariants: PASS.
- Complete Parable suite: PASS, 81/81.
- Local Claudio-Michel review: 5/5.
- Focused PR: pending.
- Verified merged `main`: pending.

## exit → Y4

Run all 15 Sol-parent permutations against the three pinned exact Claude child
ids. Require real child Bash, parent consumption, exact model attribution,
and separate ChatGPT/Anthropic OAuth routes.
