# L1 — SOL SUBSCRIPTION LIVE CANARY · EXIT (2026-07-19)

## Status: COMPLETE

## The headline evidence

P1 passed in one correctly wired live run. Claude Code 2.1.215 selected
`gpt-5.6-sol`, invoked Bash once, produced the expected synthetic artifact, and
finished successfully in two turns. The isolated CLIProxyAPI process recorded
two successful requests to the ChatGPT Codex subscription endpoint for
`gpt-5.6-sol`.

This is `n=1` proof of route viability, not a reliability benchmark.

## What shipped

| Piece | Where |
|---|---|
| Content-free P1 receipt | `docs/evidence/l1-sol-live/receipt.json` |
| Installation, network, and auth attestation | `docs/evidence/l1-sol-live/attestation.json` |
| Raw proof | Private ephemeral canary directory; not committed |

## Bound accounting (honest)

- Correctly wired PROVE runs: 1, passed.
- Evidence failures: 0.
- Review rounds: 1. The receipt-schema validation, JSON checks, content-safety
  scan, staged diff, and GitHub mergeability check had no findings; no automated
  reviewer was configured on the repository.
- Human-gate expiration: the first OpenAI device code was not authorized within
  its 15-minute lifetime. No OAuth exchange and no canary occurred, so it did
  not consume a proof attempt. The second code succeeded.
- Instrument failure 1: the binary prints its version after rejecting the
  unsupported `--version` flag. The version was pinned from the binary's own
  startup banner instead.
- Instrument failure 2: Claude Code's variadic `--allowedTools` option consumed
  the trailing prompt in the first invocation. Claude exited locally with no
  input before making an upstream request. Using the `--allowedTools=Bash`
  assignment form fixed the argv; no proof attempt was consumed.

## Accept criteria → evidence

1. Pinned, checksum-verified, loopback-only healthy proxy — ✅
   `attestation.json` records release, commit, verified published SHA-256,
   `127.0.0.1`, disabled remote management, and isolated process status.
2. Authenticated catalog contains exactly `gpt-5.6-sol` — ✅
   `attestation.json` records one Sol entry among ten subscription-visible
   models.
3. Claude Code exits successfully and the synthetic tool artifact passes — ✅
   `receipt.json` records a completed two-request stream, one tool call, and the
   independently verified artifact SHA-256.
4. OpenAI OAuth channel handled Sol with no direct API key — ✅
   `receipt.json` records `provider_channel: openai-codex` and
   `auth_kind: oauth`; `attestation.json` records a Codex OAuth record with
   access/refresh tokens and no provider API key.
5. Focused PR merged and verified at merged HEAD — ✅
   [PR #116](https://github.com/miguelrios/unc-skills/pull/116); its merge state
   and resulting `main` HEAD are checked immediately before L2 advances.

## The running delta table (L0→L1)

| Loop | P1: Sol session | P2: Sol → Kimi subagent | OSS clean-room |
|---|---:|---:|---:|
| L0 | NOT RUN | NOT RUN | NOT RUN |
| L1 | PASS (`n=1`) | NOT RUN | NOT RUN |

MEASURE for L1 is the binary P1 route gate; no latency, cost, or reliability
claim is made from one canary.

## exit → L2

After the L1 evidence PR is merged and verified at HEAD, turn the proven manual
wiring into portable Parable TOML, a `parable claude` launcher, and idempotent
named-agent synchronization.
