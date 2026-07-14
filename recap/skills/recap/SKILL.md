---
name: recap
description: Reconstruct what a Claude Code or Codex agent actually did during one exact session, including goals, decisions, actions, file and git changes, tests, failures, recoveries, final state, and open work. Use when the user says "/recap", "recap this session", "what did this agent do", "give me the full handoff", or asks for an evidence-backed account of a long-running coding-agent session. Use Recall for prior-session discovery; use Recap after a session is identified. Never use it to expose hidden reasoning or to infer unobserved actions.
---

# /recap — evidence-backed session comprehension

Recap explains one coding-agent session from end to end. It treats the transcript as an event
source, repository state as corroborating evidence, and the active agent as the semantic
synthesizer. The wrapper collects and validates evidence; it never calls a model.

## Pick the boundary

- With no target, recap the exact current native session. `CODEX_THREAD_ID` is authoritative for
  Codex. An exact Claude session ID is authoritative when the harness exposes one.
- For a prior session, first use `$recall` to find it, then pass its exact path or stable receipt.
- Keep resumed sessions, subagents, and continuations separate by default. Include them only when
  the user asks, and label every boundary independently.
- If identity is ambiguous, stop with the candidates. Never choose the nearest transcript by time.

## Collect a private manifest

Run from this skill directory:

```bash
python3 scripts/recap.py collect --current --output ~/.recap/current.json
python3 scripts/recap.py collect --session <exact-session-path> --output ~/.recap/prior.json
```

The output file is owner-only and may contain redacted session text. Do not put it in a repository,
test artifact, Slack message, or public evidence directory. The command prints only a content-free
receipt. If Recall is installed somewhere unusual, pass `--recall-script` or set `RECALL_SCRIPT`.

Validate before synthesis:

```bash
python3 scripts/recap.py validate ~/.recap/current.json
```

Read `references/truth-contract.md` when handling child sessions, live/partial sessions, multiple
repositories, ambiguous git attribution, or exhaustive-ledger requests.

## Reconstruct, do not critique

Use the manifest to understand the work in two complementary views:

1. **Story** — what the user wanted, what approach emerged, why visible decisions changed, and
   where the work landed.
2. **Timeline** — the evidence-backed sequence of significant actions, failures, recoveries,
   validations, and external side effects.

The transcript can establish that a command was attempted and what output was observed. Current git
state can establish what exists now. Neither alone proves that the session caused a change. Say
"the session edited" only when event evidence supports it; otherwise say "the current worktree
contains" or "git now shows".

Tests count as run only when an observed command and result support the claim. Distinguish passed,
failed, retried, discussed-only, and unverifiable-now. Never convert a proposed test into a run.

## Account for everything

The bootstrap manifest proves structural capture only; it deliberately reports semantic accounting
as `not_performed`. During synthesis, every observed event ordinal must appear exactly once in either:

- a significant claim's evidence list; or
- an explicit low-signal aggregate such as repetitive progress polls or unchanged status checks.

Do not dump every tool call into the prose. Group mechanically repetitive evidence while preserving
counts and ordinal ranges. If the manifest is partial, changed during collection, redacted in a way
that blocks a claim, or fails validation, label the recap incomplete. Never hide that limitation.

## Answer shape

Default to a readable recap under 2,500 words:

1. Scope and coverage
2. Headline outcome
3. Goals and visible decisions
4. Timeline
5. Files and git state
6. Tests and verification
7. Failures and recoveries
8. Final state and open work

For very long sessions, keep the prose concise and offer or create a separate owner-private ledger.
Evidence references should use stable event ordinals/IDs from the manifest, not copied private text.

## Safety

- Never claim access to hidden chain-of-thought. Recap only observable user, assistant, tool, and
  repository evidence.
- Preserve Recall redactions and redact additional credential-shaped material. Report redaction
  counts without reconstructing values.
- Do not call a provider or model API. Semantic synthesis is the current harness agent's job.
- Do not include raw private transcripts in commits, PRs, logs, evidence bundles, or Slack.
- A content-free receipt may include hashes, counts, timestamps, completeness, and duration.
