# Y1 — SOL TO TERRA AND LUNA · EXIT (2026-07-20)

## Status: COMPLETE

## The headline evidence

Ten of ten fresh Sol sessions invoked exact generated agents
`parable-terra` or `parable-luna`, one child at every parent effort. Every
parent and child preserved `low|medium|high|xhigh|max` exactly to the ChatGPT
upstream. All ten children used Bash and produced exact artifacts; all ten
parents used Agent and Bash and returned exact consumption markers.

## What shipped

| Piece | Where |
|---|---|
| Sanitized Terra/Luna nested matrix | `docs/evidence/y1-sol-terra-luna/receipt.json` |

## Bound accounting (honest)

- Correctly wired evidence runs: 10/20 maximum; all passed first attempt.
- Evidence failures: 0.
- Instrument failures: 0.
- Review rounds: 1. Generated agent ids, exact models, parent/child effort,
  tool ownership, hashes, status distributions, OAuth routing, fallback
  absence, API-key environment, and content boundaries were checked.
- One Terra/xhigh session encountered one upstream 502 followed by seven
  local 503 retry statuses. The same bounded session recovered without
  provider fallback and passed every deterministic assertion; the status
  distribution is retained in the receipt.

ZEN check: Terra and Luna use the same declarative generated-agent mechanism
as Grok. No global child override, model alias shim, provider fallback,
broker, or credential adapter was added.

## Acceptance criteria → evidence

1. Exact parent and child — ✅ 10/10 use only exact Sol parent plus exact
   `gpt-5.6-terra` or `gpt-5.6-luna` child.
2. Tool artifact and consumption — ✅ 10/10 show parent Agent+Bash, child
   Bash, artifact hash, and final-marker hash.
3. Child effort behavior — ✅ Claude Code inherits all five efforts and both
   GPT children preserve all five upstream: 10/10 exact.
4. Subscription route — ✅ every successful turn uses ChatGPT OAuth, no
   provider fallback is observed, and direct xAI/OpenAI/Anthropic API-key
   variables are unset.
5. Receipt and EXIT merged — ✅ the focused PR and verified `main` are
   recorded below.

## The running matrix

| Child | `low` | `medium` | `high` | `xhigh` | `max` | Total |
|---|---:|---:|---:|---:|---:|---:|
| Terra | ✅ | ✅ | ✅ | ✅ | ✅ | 5/5 |
| Luna | ✅ | ✅ | ✅ | ✅ | ✅ | 5/5 |
| Grok | ✅ | ✅ | ✅ | ✅→`high` | ✅→`high` | 5/5 |
| Sonnet | pending OAuth | pending | pending | pending | pending | 0/5 |
| Opus | pending OAuth | pending | pending | pending | pending | 0/5 |
| Haiku | pending OAuth | pending | pending | pending | pending | 0/5 |

MEASURE is cumulative matrix coverage: 15/30 cells are now live-proved.

## Merge verification

- JSON invariants: PASS.
- Complete Parable suite: PASS, 81/81.
- Local Claudio-Michel review: 5/5.
- Focused PR: [#165](https://github.com/miguelrios/unc-skills/pull/165).
- Verified merged `main`: `a73fc5e34545439102b0c41ad08a733ca13427b3`.

## exit → Y2

Wait for the operator, then run CLIProxyAPI's standard Claude PKCE login.
Do not copy or transform the already logged-in Claude Code credential.
