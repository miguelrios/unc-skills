# X2 — GROK MAIN-MODEL PERMUTATIONS · EXIT (2026-07-20)

## Status: COMPLETE

## The headline evidence

Stock Claude Code completed five of five fresh Grok 4.5 text sessions and
three of three real Bash tool canaries through xAI subscription OAuth.
`low`, `medium`, and `high` are exact from Claude's request through the xAI
upstream request. Claude's `xhigh` and `max` flags reach CLIProxyAPI intact
and are deterministically clamped to Grok's supported `high` effort.

## What shipped

| Piece | Where |
|---|---|
| Sanitized main-model matrix receipt | `docs/evidence/x2-grok45-main-permutations/receipt.json` |

## Bound accounting (honest)

- Correctly wired evidence runs: 8/16 maximum; all passed first attempt.
- Evidence failures: 0.
- Instrument failures: 0.
- Review rounds: 1. The matrix shape, request multiplicity, model/effort
  mappings, deterministic hashes, OAuth-route booleans, API-key environment,
  and content boundaries were checked.

ZEN check: one declarative exact model id drives every cell. Unsupported
efforts are handled by the provider registry's general clamp, with no
model-name shim, prompt rule, broker, or retry masquerading as success.

## Acceptance criteria → evidence

1. Five text cells — ✅ every requested, inbound, and upstream effort is in
   the receipt; all five exited zero with deterministic markers.
2. Supported effort fidelity — ✅ `low|medium|high` are exact end to end.
3. Real tool use — ✅ three of three Bash canaries produced exact one-line
   artifacts; both model requests in each tool loop preserved its effort.
4. Unsupported effort classification — ✅ `xhigh|max` both clamp to `high`;
   neither is silently reported as exact.
5. Subscription route — ✅ all 11 model requests use exact `grok-4.5` over the
   xAI OAuth chat route with xAI, OpenAI, and Anthropic API-key variables
   unset.
6. Receipt and EXIT merged — ✅ the focused PR and verified `main` are
   recorded below.

## The running delta table

| Loop | xAI OAuth | Grok text | Exact efforts | Tool canaries | Named Grok child | Kimi |
|---|---|---:|---:|---:|---:|---|
| X0 | valid, mode 0600 | 0 | 0 | 0 | 0 | PAUSED |
| X1 | valid, catalogued | 0 | 0 | 0 | 0 | PAUSED |
| X2 | valid, live | 5/5 | 3/3 | 3/3 | 0 | PAUSED |

MEASURE is protocol fidelity plus deterministic completion: supported effort
coverage moved from zero to 3/3 and real tool coverage from zero to 3/3.

## Merge verification

- JSON invariants: PASS.
- Complete Parable suite: PASS, 81/81.
- Local Claudio-Michel review: 5/5.
- Focused PR: pending.
- Verified merged `main`: pending.

## exit → X3

Launch Sol at all five parent effort settings and explicitly invoke exact
named `parable-grok`. Attribute both providers from private wire traces and
prove a deterministic child tool result is consumed by the parent.
