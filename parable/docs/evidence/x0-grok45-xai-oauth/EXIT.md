# X0 — xAI OAUTH HUMAN GATE · EXIT (2026-07-20)

## Status: COMPLETE

## The headline evidence

The operator completed xAI's device authorization on the first generated
code. A redacting inspection found exactly one xAI record with OAuth auth kind,
non-empty access, refresh, and ID-token booleans, an expiry, and mode `0600`.
No provider API-key variable was set.

## What shipped

| Piece | Where |
|---|---|
| Grok 4.5 successor chain | `docs/LOOP_CHAIN_2026-07-20-grok45-subscription-routing.md` |
| Sanitized OAuth receipt | `docs/evidence/x0-grok45-xai-oauth/receipt.json` |

## Bound accounting (honest)

- Device codes generated: 1/2.
- Authorization attempts: 1; successful.
- Evidence failures: 0.
- Instrument failures: 0.
- Review rounds: 1. JSON invariants, token-presence booleans, file/config
  permissions, API-key environment, and content/credential boundaries were
  checked.

ZEN check: one provider-owned device flow and one user-only OAuth record.
Parable gains no OAuth implementation, broker, shared credential, or API key.

## Acceptance criteria → evidence

1. Operator authorization — ✅ xAI device polling returned success on the
   first code.
2. Valid user-only OAuth record — ✅ the receipt records one `type=xai`,
   `auth_kind=oauth` record, token-presence booleans, expiry, and mode `0600`.
3. No xAI API key — ✅ `XAI_API_KEY`, `OPENAI_API_KEY`, and
   `ANTHROPIC_API_KEY` were unset; no key or OAuth material is committed.
4. Receipt and EXIT merged — ✅ the focused PR and verified `main` are
   recorded below.

## The running delta table

| Loop | xAI OAuth | Grok catalog | Grok turns | Named Grok child | Kimi |
|---|---|---|---:|---:|---|
| E3 GPT delivery | absent | unproved | 0 | 0 | PAUSED |
| X0 | valid, mode 0600 | not probed | 0 | 0 | PAUSED |

## Merge verification

- JSON invariants: PASS.
- Parable tests: 81/81 PASS in an isolated HOME.
- Local Claudio-Michel review: 5/5.
- Focused PR: [#150](https://github.com/miguelrios/unc-skills/pull/150).
- Verified merged `main`: `c01449bd8c80e1bc95ee49356e003b122ce4047a`.

## exit → X1

Start the patched proxy on a dedicated loopback port and prove this account's
authenticated catalog contains exact `grok-4.5` before spending a model turn.
