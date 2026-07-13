# Cascade templates

Copy-paste skeletons for the three artifacts the skill produces, plus a condensed real example.

## Contents
- [Chain doc template](#chain-doc-template)
- [EXIT.md template](#exitmd-template)
- [Heartbeat prompt template](#heartbeat-prompt-template)
- [Real example: a closed loop's EXIT.md (condensed)](#real-example)

## Chain doc template

`.cascade/LOOP_CHAIN_<YYYY-MM-DD>.md`

```markdown
# The Loop Chain — <project/goal> (<date>)

> <one-line source: the plate/issue/assessment this chain was cut from>. Each loop has a
> strong self-contained prompt, evidence-based exit criteria (artifacts, traces, deltas —
> never "it should work"), and a fixed inner structure. A loop's EXIT-CHECK triggers the
> next loop. Nothing advances on vibes.
>
> **Pacing:** autonomous | checkpointed <(checkpointed = every loop EXIT stops for
> go / no-go / redirect before the next loop starts)>

## Loop anatomy
<the five-field table from SKILL.md, verbatim>

## The ribbon
<the eight-step ribbon from SKILL.md, plus any project-specific live-run protocol:
runtime pins, required flags, timeouts, output dirs, shared-box etiquette>

**Bound + escalation (every loop):** max 3 REVIEW→fix rounds and max 2 failed PROVE runs
per loop. At the bound: EXIT.md with `status: AT_BOUND`, exactly which criteria are unmet
and why, then stop — never fake the exit.

## The chain

**Order rationale:** <why this order — baseline first if later loops must show deltas;
risk-ranked levers: mechanical → prompt → model>

### L0 — <NAME> (<one-line role>)
- **goal:** <one sentence — the state change>
- **prompt:**
  > <self-contained block: files to read, tasks 1..n, protocol to use. A fresh session
  > with only this doc must be able to execute it.>
- **accept:**
  1. <checkable artifact>
  2. <checkable artifact>
  n. PR merged; criteria verified at HEAD.
- **bound:** 2 PROVE runs / 3 review rounds.
- **exit →** L1.

### L1 — ... (repeat per loop)

### L<final> — RE-PLAN GATE (human gate)
- **goal:** verdict with numbers + the next chain drafted from the losing cells.
- **accept:** verdict doc; user sign-off (the one criterion no trace can satisfy).
- **bound:** 2 drafts; the sign-off wait is unbounded by design — the chain PAUSES.
- **exit →** the next chain (this doc gets a successor, not an edit).

## Parallel track (interleave, don't serialize)
<independent work to run while chain loops wait on review/live runs; same ribbon, own accept>

## Chain invariants
<the eight invariants from SKILL.md>
```

Then mirror the chain in the harness's native task UI when one exists: one task per loop,
each blocked by its predecessor, with parallel tracks as unblocked siblings. When no task UI
exists, add a checked task table with the same dependency links to the chain doc itself.

## EXIT.md template

`.cascade/evidence/<loop-id>-<slug>/EXIT.md`

```markdown
# <Loop id> — <NAME> · EXIT (<date>)

## Status: COMPLETE | AT_BOUND

<if AT_BOUND: exactly which criteria are unmet and why; what was tried; page the user.>

## The headline evidence
<the single strongest artifact: live trace, measurement table, before/after numbers.
n=1 results labeled as directional, never statistical.>

## What shipped
| Piece | Where |
|---|---|
| <mechanism> | <file/PR> |

## Bound accounting (honest)
<PROVE runs used, review rounds used. Instrument failures (harness/dispatch miswiring)
listed separately from evidence failures — each diagnosed + fixed in its own commit;
they don't burn the bound but must be documented.>

## Accept criteria → evidence
1. <criterion> — ✅/❌ <pointer: test file + pass line, trace path, PR # verified at HEAD>
2. ...

## The running delta table (L0→Ln)
| Loop | Shipped | Headline |
|---|---|---|

## exit → <next loop id> (<what it needs to start, incl. anything the user must supply>)
```

## Heartbeat prompt template

For a recurring-wake feature, lifecycle hook, or external scheduler when the harness has one:

```
HEARTBEAT — continue the <project> loop chain autonomously. Read the chain doc (<path>) and
the harness task list, if present, for exact position. Rules: (1) if a chain loop is mid-flight,
advance its next ribbon step (RE-PLAN→BUILD→PIN→PROVE→MEASURE→REVIEW→MERGE→EXIT) — use
background dispatches/judges only when the harness exposes them; (2) if bots reviewed an open PR,
resolve findings and merge per the etiquette; (3) honor all bounds — AT_BOUND means write
the report and STOP that loop (page the user in the reply), never silently continue;
(4) human gates always wait for the user; (5) if genuinely nothing is actionable, say so
in one line and stop. Keep replies short: position, what was advanced, what's now running.
```

## Real example

Condensed from a closed loop (L8 — skip-with-receipt, 2026-07-07) showing the parts that
make an exit honest:

- **Status line carries nuance:** `COMPLETE (the headline fired NATURALLY; the pareto gate
  deferred to L9 pending δ)` — deferrals are declared at the exit, with cause, never dropped
  silently.
- **Headline evidence is one live trace + one table:** the advisor skipped 4 wrong-entity
  decoys with justified receipts; cost $0.4624 → $0.3425 (−26%), explicitly labeled `n=1 —
  directional evidence, not a statistical claim`.
- **Bound accounting separates instrument from evidence:** 5 PROVE runs, runs 1–4 were
  instrument failures (dispatch miswiring), each fixed in its own commit — "these four fixes
  made the custom-SOP dispatch path work end-to-end for the first time — permanent instrument
  value." Run 5, the first correctly-wired attempt, produced the headline.
- **A criterion that would be theater gets deferred, not faked:** "running the gate at
  δ=0.15/n=12 would be theater. The machinery is ready" — deferred to the next loop with the
  human input it needs named.
- **Exit names what the next loop needs from the human:** "L9 needs from Miguel: δ
  (recommend 0.3 at n≈30-40), then the pareto…, and the BUILD/DEFER call."
