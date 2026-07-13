# Subjective review routing

Use the current harness's strongest supported blind-review path. Never select a
runner merely because its executable happens to be installed.

## Codex

Use the upstream isolated batch runner:

```bash
desloppify review --run-batches --runner codex --parallel --scan-after-import
```

It prepares immutable packets, executes bounded Codex subprocess batches, and
imports the result. Retry only failed batch indexes against the same packet.
Do not ask those subprocess prompts to spawn more children.

## Claude Code

Run `desloppify review --prepare`, read the blind packet and dimension prompts,
and launch small waves of native context-isolated subagents. Each reviewer gets
only its dimensions, packet path, repository root, output schema, and permission
to read—not edit—the target. Write batch results as `batch-N.raw.txt`, merge,
validate, and import before fixing. Do not use the Codex runner from Claude Code.

## Hermes

Generate batch prompts with `desloppify review --run-batches --dry-run`, then
use native `delegate_task` in waves of at most three. Each isolated child reads
one prompt and the blind packet and writes only its `batch-N.raw.txt` result.
Import the completed run directory. Do not install upstream's shared
`AGENTS.md` overlay on top of this skill.

## OpenCode and Rovo Dev

Use their first-class upstream runners when the active harness matches:

```bash
desloppify review --run-batches --runner opencode --parallel --scan-after-import
desloppify review --run-batches --runner rovodev --parallel --scan-after-import
```

## Gemini and pi

Neither is currently a trusted first-class Desloppify batch runner. Prepare the
blind packet, use native isolated agent calls if the client provides them, then
validate and import findings. Gemini's experimental subagents may be sequential.
Stock pi should use the prepared-packet/manual path. Do not fabricate trusted
assessment provenance; findings-only import is honest when needed.

## Generic or unknown client

Use:

```bash
desloppify review --prepare
```

Review requested dimensions from the blind packet in isolated contexts. Return
the exact JSON schema the packet requests, validate the merged file, and import
it. If isolation or attestation cannot be established, import findings without
claiming durable trusted scores.

## Every route

- Keep reviewers blind to previous scores and targets.
- Treat scan signals as navigation, not confirmed defects.
- Inspect source evidence for every finding.
- Prefer zero findings to quota-driven noise.
- Import before fixing.
- Keep `.desloppify/` local and out of packages.
- Follow workspace model-routing and data-handling policy.
