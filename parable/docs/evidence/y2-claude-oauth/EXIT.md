# Y2 — CLAUDE OAUTH HUMAN GATE · EXIT (2026-07-20)

## Status: COMPLETE WITH DISCLOSED BOUND EXCEPTION

## The headline evidence

CLIProxyAPI now has exactly one user-only Claude OAuth record. The redacted
record reports provider `claude`, access and refresh token presence, expiry,
and mode 0600. No Anthropic API key or copied Claude Code credential was used,
and the gate spent zero model turns.

## What shipped

| Piece | Where |
|---|---|
| Sanitized Claude OAuth receipt | `docs/evidence/y2-claude-oauth/receipt.json` |

## Bound accounting (honest)

The declared human-gate bound of two generated PKCE flows was exceeded: ten
flows were generated before one successful exchange.

- Eight standard CLI flows were invalidated by the chat execution
  environment: the waiting process was cleaned up between turns, detached
  terminals received EOF, and Zellij clipped the long authorization line.
- Two split-phase flows used the same CLIProxyAPI PKCE generator, Anthropic
  client, scopes, token exchange, and storage type. The first received a
  callback from an older state and was rejected before exchange. The second
  matched state and exchanged successfully.
- Provider exchange failures: 0.
- Model turns: 0.

This is a cascade-process exception, not hidden success. The correct action at
the declared bound would have been an `AT_BOUND` EXIT before re-planning. The
operator remained actively engaged and explicitly requested fresh flows, but
that does not erase the overrun.

ZEN check: the final transport persisted only PKCE verifier/state in a
mode-0600 temporary record and deleted it after exchange. It did not copy a
credential or alter OAuth semantics. This bridge is test-environment plumbing
and is not part of the OSS recipe; ordinary users run the standard CLI flow
locally.

## Acceptance criteria → evidence

1. Human callback — ✅ the operator completed the matching PKCE callback.
2. Valid proxy record — ✅ exactly one Claude record, access/refresh/expiry
   present, mode 0600.
3. No key or credential copy — ✅ no Anthropic API key; native Claude Code
   credential was not copied or transformed.
4. Receipt and EXIT merged — ✅ the focused PR and verified `main` are
   recorded below.

## Merge verification

- JSON invariants: PASS.
- Content boundary: PASS.
- Complete Parable suite: PASS, 81/81.
- Local Claudio-Michel review: 5/5.
- Focused PR: [#178](https://github.com/miguelrios/unc-skills/pull/178).
- Verified merged `main`: `014b835dd30d5d210ac4e4b5c3318a1c49d3c2e5`.

## exit → Y3

Query the authenticated local catalog without a model turn and pin the exact
full Sonnet, Opus, and Haiku ids exposed to this account.
