---
name: cascade
description: Turn a blunt "do X, don't screw it up" task into structured, evidence-gated progress — instead of charging in, plan the work into a cascade of bounded loops, each one development cycle with a self-contained prompt, a checkable evidence-based exit, and a hard bound; a loop's exit triggers the next, and you can run straight through (autonomous) or pause for a go/no-go at every loop boundary (checkpointed). Use when the user says /cascade, "cascading loops", "work in loops", "go ham" on a multi-cycle project, hands a big/risky/"make no mistakes" task worth planning before building, or asks to plan, advance, resume, or take over a loop chain.
---

# Cascade — cascading development loops

**The move: don't charge at a big task, cascade it.** A blunt prompt — "do X, and make no
mistakes" — is the signal to STOP and plan first, not to start typing. You replace one
unstructured push with a **chain** of bounded loops: each loop produces one state change,
proves it with checkable artifacts, and its EXIT triggers the next loop. Between loops you
either flow straight through or pause for the user's go/no-go (see Pacing). Nothing advances
on vibes: an exit criterion that can't be pointed at (a passing test, a live trace file, a
scoreboard delta, a merged PR verified at HEAD) is not a criterion. This is the same
discipline the product ships (`loop_until` with an accept contract, bounded rounds, honest at
the bound) applied to our own development.

## Pick the branch

1. **PLAN** — the user hands a goal, plate, issue, or lever list. Cut it into a chain doc + task graph. Do this before any BUILD.
2. **ADVANCE** — a chain exists and a loop is mid-flight. Find position, run the next ribbon step.
3. **TAKEOVER** — resuming after a gap, a compaction, or inheriting another session's chain. Re-evaluate the priors skeptically before advancing — prior-you's conclusions (especially anything inside a noise band) get a fresh read, not automatic trust.

## Pacing: autonomous vs checkpointed

Set at PLAN time and recorded in the chain doc header; infer from the request when obvious
("go ham" = autonomous; "walk me through it" / high-stakes changes = checkpointed) and ask
when it isn't.

- **autonomous** (default) — a loop's EXIT triggers the next loop directly; the chain pauses
  only at declared human gates and AT_BOUND.
- **checkpointed** — EVERY loop EXIT is a stop: present the exit evidence and ask
  go / no-go / redirect (AskUserQuestion where available) before the next loop starts. A
  redirect re-cuts the remaining chain, appended to the doc. Checkpoints are confirmation
  stops, not design sessions — one question, concrete options.

## Loop anatomy

Every loop in the chain doc is specified with exactly these fields:

| Field | Meaning |
|---|---|
| `goal` | One sentence. The state change the loop exists to produce. |
| `prompt` | Self-contained instruction block — enough for a fresh session to execute the loop with zero prior context beyond the chain doc and its named inputs. |
| `accept` | Evidence-based exit criteria. Every criterion names a **checkable artifact**: a test passing on the pinned runtime, a live trace under `docs/evidence/`, a scoreboard JSON with non-null numbers, a merged PR verified at HEAD. |
| `bound` | Max inner iterations before honest escalation (default: 2 failed PROVE runs, 3 REVIEW→fix rounds). |
| `exit →` | The loop this one's completion triggers. |

Ordering rule: **measure before touching anything** — if later loops must show deltas, the
baseline loop comes first, whatever the standing plan said. Rank levers by risk
(mechanical → prompt → model), not category momentum: cut the full cost/quality anatomy first,
then order loops so the provably semantics-preserving levers run before the risky ones.

## The ribbon (inner structure, shared by every loop)

```
RE-PLAN   read the chain doc + this loop's prompt + current HEAD; cut a mini-plan for THIS loop only
BUILD     implement; one concern per PR; targeted git add
PIN       tests that pin the mechanism AND its fake-success modes explicitly
PROVE     live run; trace evidence copied to docs/evidence/<loop>/
MEASURE   re-run the gate slice (baseline mini-benchmark); record the delta — SKIP when the loop moves no metric (a plain "do X" with no number to track); say so in EXIT.md
REVIEW    open PR; reviewers auto-fire on open (NO ping); resolve every finding
MERGE     merge after findings resolved (per standing authority; else wait)
EXIT      verify each accept criterion against HEAD + the trace — never commit messages;
          write docs/evidence/<loop>/EXIT.md with criterion → evidence pointers; trigger the next loop
```

Launch background dispatches, judges, and long runs rather than waiting on them — interleave a
parallel-track task whenever the chain loop is blocked on review or a live run.

## Chain invariants (no exceptions)

0. **Plan before you build** — a blunt task gets a chain doc + task graph FIRST; no BUILD before the cascade is cut. This is the whole point; skipping it is skipping the skill.
1. **No loop advances without its EXIT.md** — criterion → evidence pointer, verified at HEAD.
2. **Deltas are cumulative** — each EXIT.md carries the running L0→Ln table.
3. **Regression = unmet criteria**, even if the loop's feature "works." The gate slice is the guard.
4. **AT_BOUND is a first-class exit** — write EXIT.md with `status: AT_BOUND`, state exactly which criteria are unmet and why, page the user, and STOP that loop. Hitting the bound ≠ done; faking the exit is the one unforgivable move.
5. **Instrument failures don't count as evidence failures** — a PROVE run that died because the harness/dispatch was miswired gets diagnosed + fixed in its own commit and documented in EXIT.md's bound accounting; only runs that tested the actual claim burn the bound.
6. **Human gates pause the chain** — a loop whose accept includes user sign-off waits unbounded by design; it never self-advances or times out into fake approval.
7. **ZEN check on every BUILD** — semantic judgments go to prompts/agentic graders, structural checks to code; domain shape stays in the SOP/prompt, never the engine.
8. **The chain doc is append-forward** — update it as loops close; when the final re-plan gate fires, the next chain gets a successor doc, not an edit.

## Bookkeeping (set up during PLAN)

- **Chain doc** — `docs/LOOP_CHAIN_<date>.md` (or the project's docs dir): anatomy table, the ribbon, every loop's five fields, invariants. Template: [references/templates.md](references/templates.md).
- **Evidence tree** — `docs/evidence/<loop-id>-<slug>/` per loop; EXIT.md plus the raw artifacts it points at.
- **Task graph** — one TaskCreate per loop, chained with `blockedBy` so the task list mirrors the cascade; parallel tracks as unblocked siblings. Position is always recoverable from TaskList + the chain doc.
- **Final loop = re-plan gate** — the last loop reads the accumulated evidence, writes a verdict with numbers, drafts the next chain from the losing cells, and ends on a human gate.

## Running autonomously

When the user says go ham / keep going, keep the chain moving without them driving each step:

- Arm a heartbeat (the `/loop` skill, a Stop hook, or ScheduleWakeup) whose prompt is: read TaskList + the chain doc for exact position; if a loop is mid-flight, advance its next ribbon step; if bots reviewed an open PR, resolve and merge per etiquette; honor all bounds (AT_BOUND ⇒ report and stop that loop); human gates always wait; if everything is blocked on external work, say so in one line and stop.
- Use Monitor/background Bash on long PROVE runs so exits fire on evidence, not on polling.
- Report at loop EXITs, not ribbon steps: position, what closed, the delta, what's now running. When asked to report externally, post at exits (e.g. Slack mention in the named channel) — one message per loop exit, not per step.

## Templates

Read [references/templates.md](references/templates.md) when writing the chain doc, an EXIT.md,
or the heartbeat prompt — it carries copy-paste skeletons plus a condensed real example of a
closed loop's exit.
