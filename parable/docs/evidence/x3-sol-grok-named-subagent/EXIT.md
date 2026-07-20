# X3 — SOL PARENT TO NAMED GROK SUBAGENT · EXIT (2026-07-20)

## Status: COMPLETE

## The headline evidence

Five of five fresh Sol parent sessions explicitly invoked exact generated
agent `parable-grok`, whose model field is exact `grok-4.5`. Private wire
evidence attributes every parent request to Sol over ChatGPT OAuth and every
child request to Grok over xAI OAuth. Each successful child used Bash to
produce an exact artifact; each parent used Agent and Bash and returned its
synthetic consumption marker.

Claude Code inherited the parent effort into the named child in all five
cells. Grok then preserved `low|medium|high` and clamped inherited
`xhigh|max` to `high`, matching the direct X2 behavior.

## What shipped

| Piece | Where |
|---|---|
| Sanitized nested-agent matrix receipt | `docs/evidence/x3-sol-grok-named-subagent/receipt.json` |

## Bound accounting (honest)

- Correctly wired evidence runs: 6/10 maximum.
- Evidence failures: 1. The first medium child routed correctly and invoked
  Bash but copied the canary path incorrectly, so neither the child artifact
  nor parent consumption passed. Its permitted second attempt passed.
- Instrument failures: 1. An invalid empty MCP configuration stopped before
  any model request; fixing it did not consume an evidence attempt.
- Review rounds: 1. Agent generation, exact models, effort inheritance,
  upstream clamp behavior, tool ownership, deterministic hashes, OAuth-route
  booleans, API-key environment, and content boundaries were checked.

ZEN check: a standard declarative project agent is the entire heterogeneous
routing mechanism. Claude Code owns orchestration, provider routing is visible
on the wire, and the one model-level failure remains evidence instead of
being erased.

## Acceptance criteria → evidence

1. Exact Sol parent — ✅ 5/5 successful cells have only
   `gpt-5.6-sol`; all five inbound and upstream efforts are exact.
2. Exact named Grok child — ✅ 5/5 invoke generated `parable-grok` and every
   child request is `grok-4.5`, never Sol or a Claude alias.
3. Tool result and consumption — ✅ 5/5 successful cells show parent
   `Agent+Bash`, child `Bash`, exact artifact hashes, and exact final-marker
   hashes.
4. Child effort behavior — ✅ Claude Code inherits all five parent efforts;
   Grok preserves three supported values and clamps `xhigh|max` to `high`.
5. Subscription routes — ✅ all successful cells show Sol over ChatGPT OAuth
   and Grok over xAI OAuth, with direct xAI, OpenAI, and Anthropic API-key
   variables unset.
6. Receipt and EXIT merged — ✅ the focused PR and verified `main` are
   recorded below.

## The running delta table

| Loop | Grok main text | Grok main tools | Sol → named Grok | Child effort measured | Kimi |
|---|---:|---:|---:|---:|---|
| X1 | 0 | 0 | 0 | 0 | PAUSED |
| X2 | 5/5 | 3/3 | 0 | 0 | PAUSED |
| X3 | 5/5 | 3/3 | 5/5 | 5/5 | PAUSED |

MEASURE is exact heterogeneous attribution plus deterministic completion:
named-child coverage moved from zero to five parent-effort permutations.

## Merge verification

- JSON invariants: PASS.
- Complete Parable suite: PASS, 81/81.
- Local Claudio-Michel review: 5/5.
- Focused PR: [#158](https://github.com/miguelrios/unc-skills/pull/158).
- Verified merged `main`: `63e715f8e6b2baf16d8782d0426970eec3dd7347`.

## exit → X4

Publish the proved xAI OAuth, Grok main-model, effort-clamp, and exact
`parable-grok` recipe in the OSS guide. Preserve the existing ChatGPT route
and keep Cursor-Grok and Kimi outside this subscription path.
