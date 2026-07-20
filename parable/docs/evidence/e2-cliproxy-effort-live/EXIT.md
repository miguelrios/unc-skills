# E2 — PATCHED LIVE MATRIX · EXIT (2026-07-20)

## Status: COMPLETE

## The headline evidence

The patched CLIProxyAPI binary preserved the requested effort exactly from
stock Claude Code's inbound `output_config.effort` to Codex
`reasoning.effort` for every Sol, Terra, and Luna cell. All 15 text cells
exited zero, returned the independently checked synthetic marker, completed
their stream, and used the ChatGPT OAuth route.

Exact live fidelity moved from 3/15 with released CLIProxyAPI `v7.2.88` to
15/15 with the E1 source patch: +12 cells and +80 percentage points. Each
model also completed one real medium-effort Bash tool round-trip.

## What shipped

| Piece | Where |
|---|---|
| Sanitized patched 15-cell and 3-canary scoreboard | `docs/evidence/e2-cliproxy-effort-live/receipt.json` |
| Released-versus-patched OSS caveat | `skills/parable/references/providers.md` |

## Bound accounting (honest)

- Correctly wired text attempts: 15, one per cell; all passed within the
  two-attempt bound.
- Correctly wired tool attempts: 3, one per model; all passed.
- Evidence failures: 0.
- Review rounds: 1. Receipt invariants, released-versus-patched language,
  content/credential scanning, JSON validity, diff hygiene, and the full
  Parable suite were checked. No critical issue or warning remained; local
  Claudio-Michel review verdict 5/5.
- Instrument correction: as in G1, a successful tool canary creates two model
  requests: tool selection and the post-tool continuation. The runner's
  one-log trace field was empty, so the bounded invocation windows were parsed
  through the safe-field filter and grouped without a rerun. All six requests
  carried `medium → medium`, the OAuth-route marker, and two HTTP 200 statuses.

ZEN check: one general protocol fix produces exact control across three model
ids and five effort values. Parable adds no model-name routing rule, credential
path, static effort remap, or hidden replacement for the released proxy.

## Acceptance criteria → evidence

1. All 15 cells have non-null requested, inbound, and upstream effort plus a
   successful deterministic hash — ✅ every cell in `receipt.json` exits zero,
   records one attributable request and two HTTP 200 statuses, and completes
   with its synthetic marker.
2. Requested-to-upstream exact fidelity is 15/15 — ✅ all five values are
   identical at requested, inbound, and translated layers for Sol, Terra, and
   Luna. No provider clamp or rejection occurred.
3. One deterministic tool canary per model — ✅ 3/3 artifact hashes passed;
   both requests in every canary preserved `medium`.
4. ChatGPT OAuth only — ✅ all 21 parsed model requests used the OAuth route
   while direct OpenAI and Anthropic API-key variables were unset.
5. Sanitized receipt and caveat merged — ✅ public artifacts contain only
   runtime pins, model/effort fields, counts, statuses, booleans, and
   deterministic hashes; raw prompts, responses, logs, credentials, and
   private paths are absent. The focused PR and merge verification are
   recorded below.

## The running delta table

| Loop | Sol text | Terra text | Luna text | Exact upstream effort | Tool canaries | Kimi P2 |
|---|---:|---:|---:|---:|---:|---:|
| G1 release | 5/5 | 5/5 | 5/5 | 3/15 (`medium` only) | 3/3 | PAUSED |
| E1 unit proof | not rerun | not rerun | not rerun | semantic tests green | not rerun | PAUSED |
| E2 patched live | 5/5 | 5/5 | 5/5 | 15/15 (all exact) | 3/3 | PAUSED |

MEASURE for E2 is wire fidelity, not comparative model quality, latency,
token-use, or long-session reliability.

## Merge verification

- Parable tests: 81/81 PASS in an isolated HOME.
- Review verdict: 5/5.
- Focused evidence PR: [#146](https://github.com/miguelrios/unc-skills/pull/146).
- Verified merged `main`: pending.

## exit → E3

Package the exact two-commit source patch and prove it applies, tests, and
builds from the official pinned CLIProxyAPI archive. Publish the five-minute
OSS build path while stating plainly that the change is not upstream.
