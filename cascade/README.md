# Cascade

Cascade is an Agent Skill for breaking large coding tasks into a series of smaller, verifiable loops. It works in Claude Code, Codex, and pi.

[![skills.sh](https://skills.sh/b/miguelrios/unc-skills)](https://skills.sh/miguelrios/unc-skills/cascade)

Long agent runs tend to lose context in the middle: the original goal gets diluted, partial work is mistaken for completion, and it becomes difficult to tell what was actually tested. Cascade keeps the plan and progress in the repository. Before changing code, it writes down the sequence. Each loop has a specific goal, a way to prove the goal was met, and a limit on how long the agent can keep trying.

The basic shape is:

```text
large task
    │
    ▼
plan the chain
    │
    ├── L0: establish the baseline ── prove it ──┐
    ├── L1: make one change       ── prove it ──┤
    ├── L2: integrate the change  ── prove it ──┤
    └── L3: evaluate the result   ── prove it ──┘
                                                 │
                                                 ▼
                                          human decision
```

The useful part is the handoff between loops. The next loop does not start because the agent feels finished. It starts when the current loop can point to its exit evidence.

## Install

skills.sh:

```bash
npx skills add miguelrios/unc-skills --skill cascade
```

Claude Code:

```bash
claude plugin marketplace add miguelrios/unc-skills
claude plugin install cascade@unc-skills
```

Codex:

```bash
codex plugin marketplace add miguelrios/unc-skills
codex plugin add cascade@unc-skills
```

pi (installs the complete unc-skills collection):

```bash
pi install git:github.com/miguelrios/unc-skills
```

## Use it

```text
/cascade Migrate our job runner from Redis queues to Postgres.
```

You can also invoke it without the command:

```text
Work through this as a cascade. Keep going autonomously unless you hit a real decision.
```

If you want to approve each stage:

```text
Cascade this in checkpointed mode. Stop for my go/no-go between loops.
```

Cascade will inspect the repository, write a chain document, and show you the proposed loops before it starts building. For the migration above, the chain might separate baseline behavior, the storage implementation, worker integration, migration tooling, and a final live test. The exact chain comes from the repository; it is not a fixed checklist.

Invocation syntax differs by harness: `/cascade` in Claude Code, `$cascade` or a direct request
in Codex, and `/skill:cascade` in pi. Cascade uses a native task list, background runner, or
recurring wake only when the harness exposes one. The chain document remains the portable source
of truth, so pi can use a file-backed task graph and tmux/sequential work without losing the loop.

## Examples

### Large refactor

```text
/cascade Refactor the billing module so providers use a shared interface. Preserve existing behavior and keep the branch passing at every stage.
```

The work can be split by dependency boundary instead of rewriting the module in one pass:

```text
L0  capture current behavior          exit: existing tests pass
L1  introduce the provider interface  exit: unit + integration tests pass
L2  migrate the Stripe provider       exit: all tests pass
L3  migrate the remaining providers   exit: all tests pass
L4  remove the old path               exit: all tests pass; no old imports remain
```

Each stage leaves the codebase in a working state. A loop cannot exit because its isolated test passes while the full suite is broken; the stage-level verification is that all tests pass.

### Large agent or prompt change

```text
/cascade Rewrite the research planner prompt to reduce unsupported claims without regressing answer quality. Use the existing eval suite as the gate.
```

Prompt work needs measured behavior rather than code tests alone:

```text
L0  run and save the baseline evals    exit: baseline artifact recorded
L1  change planning instructions       exit: targeted evals meet the threshold
L2  update evidence handling           exit: targeted + regression evals pass
L3  run the complete evaluation set    exit: quality improves; no protected metric regresses
```

The eval command, dataset, model, sampling settings, and acceptance thresholds are pinned in the chain. Each stage compares against the same baseline, and a prompt change only advances when its eval gate passes.

### Large frontend change

```text
/cascade Rebuild onboarding as a multi-step flow. Preserve account creation, validation, back navigation, and mobile behavior. Verify it with computer use.
```

The frontend can be divided into user-visible slices:

```text
L0  record the current user journeys   exit: baseline screenshots and flows saved
L1  build the new shell and navigation exit: computer-use navigation flow passes
L2  migrate account creation           exit: happy path and validation flows pass
L3  add responsive behavior            exit: desktop and mobile flows pass
L4  remove the previous onboarding     exit: complete computer-use suite passes
```

The proof is an agent using the interface as a user would: loading the app, clicking through the flow, entering valid and invalid data, checking back/forward behavior, and capturing screenshots at the required viewports. Component tests can support the change, but the stage does not exit until the computer-use flow passes.

## Inside a loop

```text
RE-PLAN
   │    Read the chain, the loop prompt, and the current code.
   ▼
BUILD
   │    Make the smallest change that satisfies this loop.
   ▼
PIN
   │    Add tests for the mechanism and the likely false-positive cases.
   ▼
PROVE
   │    Run the code and save the relevant output.
   ▼
MEASURE
   │    Compare with the baseline when the work has a metric.
   ▼
REVIEW → MERGE
   │    Resolve findings and land the change when authorized.
   ▼
EXIT
        Check every acceptance criterion against the resulting HEAD.
```

Not every job needs every step. `MEASURE`, for example, is skipped when there is no meaningful number to compare. The exit still records that it was skipped and why.

Each loop prompt is self-contained. This is intentional: another session should be able to resume the chain by reading the repository artifacts instead of relying on the original conversation.

## What it writes

```text
.cascade/
├── LOOP_CHAIN_<date>.md
└── evidence/
    ├── L0-baseline/
    │   └── EXIT.md
    ├── L1-core-change/
    │   ├── live-trace.json
    │   └── EXIT.md
    └── L2-integration/
        └── EXIT.md
```

The chain document is the plan and current position. Each `EXIT.md` maps the loop's acceptance
criteria to concrete evidence such as a test result, trace, benchmark output, or merged commit.
Keep `.cascade/` ignored: it may contain transcripts, machine paths, identifiers, or operational
details. If a project needs a public result, write a separate redacted summary rather than
committing the raw working evidence.

## Autonomous and checkpointed modes

**Autonomous** is the default. A completed loop starts the next one automatically. The chain still stops for decisions that require a person and when a loop reaches its retry bound.

**Checkpointed** pauses after every loop. It shows the exit evidence and waits for you to continue, stop, or change the remaining plan.

Autonomous mode works well when the target and tests are understood. Checkpointed mode is a better fit for migrations, architecture changes, and experiments where the result of one stage may change what should happen next.

## What happens when a loop cannot prove its claim

A loop gets a limited number of failed proof runs and review/fix rounds. When it uses them up, it exits as `AT_BOUND` and stops.

```text
                       yes
acceptance met? ─────────────► complete the loop ──► next loop
      │
      │ no
      ▼
bound remaining? ── yes ─────► diagnose and retry
      │
      │ no
      ▼
AT_BOUND
  - list the unmet criteria
  - record what was attempted
  - ask for the decision needed
  - do not advance
```

This distinction matters for unattended runs. A loop that ran out of time is not treated as a loop that passed.

## When to use it

- A feature crosses several parts of a codebase.
- A migration has ordering, compatibility, or rollback concerns.
- Performance work needs a baseline and repeatable measurements.
- An agent should continue unattended without receiving one unbounded prompt.
- A long job needs to survive across sessions without depending on chat history.

It is not intended for small, well-scoped changes. Cascade adds planning and evidence files on purpose, so the overhead only makes sense when the task is large enough to benefit from them.

The full behavior is defined in [`skills/cascade/SKILL.md`](skills/cascade/SKILL.md). Templates for chain documents, loop exits, and autonomous heartbeats are in [`skills/cascade/references/templates.md`](skills/cascade/references/templates.md).
