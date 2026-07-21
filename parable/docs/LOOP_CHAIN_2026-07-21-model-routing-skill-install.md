# The Loop Chain — Parable model routing and skill-first install (2026-07-21)

> Turn the current generic subscription cast into an evidence-informed troupe,
> then make an installed Claude Code skill capable of completing Parable setup
> and handing the user one exact command for a fresh terminal. Public evidence
> remains synthetic or content-free.
>
> **Pacing:** autonomous through implementation and merge. npm publication and
> the final product-signoff verdict remain human gates.

## Loop anatomy

| Field | Meaning |
|---|---|
| `goal` | One sentence. The state change the loop exists to produce. |
| `prompt` | Self-contained instructions sufficient for a fresh session. |
| `accept` | Safe, checkable evidence that pins success and fake-success paths. |
| `bound` | Maximum evidence failures and review/fix rounds before `AT_BOUND`. |
| `exit ->` | The successor triggered by a complete EXIT. |

## The ribbon

```text
RE-PLAN -> BUILD -> PIN -> PROVE -> MEASURE -> REVIEW -> MERGE -> EXIT
```

All product edits use isolated worktrees. Proofs use temporary homes, synthetic
catalogs, and fake provider/Claude processes. They neither start OAuth nor spend
a provider turn. Raw credentials, provider payloads, and transcripts never
enter repository evidence.

**Bound + escalation:** at most two correctly wired PROVE failures and three
REVIEW-to-fix rounds per loop. Instrument failures are disclosed separately. At
the bound, write an `AT_BOUND` EXIT naming unmet criteria and stop.

## The chain

**Order rationale:** first improve declarative routing and the generated agents
without changing provider transport; then improve the installation entrypoint
on top of that stable cast; finally audit the complete user journey. These are
mechanical and prompt/agentic changes, not a deterministic model-name router.

### R0 — EVIDENCE-INFORMED CAST

- **goal:** Give the parent accurate model stage directions, useful task menus,
  symmetric Fable/Sol access, and pinned per-agent effort.
- **prompt:**
  > Update generated subscription configuration and the reference example so
  > Fable and Sol can each be the parent or a named executor, while the current
  > parent is excluded by routing guidance. Specialize Terra for React/frontend
  > implementation, Luna and Haiku for mechanical/data work, Sonnet for
  > brownfield implementation, Opus for high-judgment review, Grok for bounded
  > terminal-heavy independent execution, Sol for long implementation and
  > high-recall review, and Fable for architecture. Add only the smallest
  > general task classes justified by those distinctions. Render configured
  > `effort` into Claude Code custom-agent frontmatter. Reconcile the routing
  > reference with live-headroom menus and author/current-parent exclusion.
- **accept:**
  1. Generated and example casts include exact Fable and Sol executors plus
     evidence-informed `use_for`/`avoid_for` guidance.
  2. Routing menus cover mechanical, data transformation, frontend, bounded
     feature, wide refactor, gnarly, review, smoke, and architecture work.
  3. Generated custom agents pin valid `effort` frontmatter and reject invalid
     or unsafe metadata through existing validation.
  4. The skill and routing reference agree that the brain chooses among capable
     peers using live headroom, excluding its own model and the review author.
  5. Focused and full tests plus package dry-run pass; PR merged and verified at
     `main`.
- **bound:** 2 PROVE runs / 3 review rounds.
- **exit ->** R1.

### R1 — SKILL-FIRST MAGICAL INSTALL

- **goal:** Let a user invoke one script from the installed Claude Code Parable
  skill and finish with one exact fresh-terminal launch command.
- **prompt:**
  > Verify current Claude Code plugin/skill installation conventions, then add
  > a portable `parable.sh` entrypoint inside the skill. It must locate its
  > packaged Parable CLI without assuming a source checkout, install a durable
  > user-facing `parable` command on PATH without downloading mutable code, run
  > the existing interactive setup/auth flow, preserve idempotency and private
  > file modes, and end with exactly worded guidance beginning `In a new
  > terminal` followed by the canonical auto-brain launch command. Make the
  > Parable skill recognize install/onboard intent and invoke the script. Pin
  > success, rerun, missing dependency, noninteractive, PATH, and failure
  > propagation paths with temporary homes and fake binaries. Do not publish
  > npm or start real OAuth in tests.
- **accept:**
  1. A skill-installed user can run one documented `parable.sh` command from
     Claude Code to install the durable CLI and enter existing setup/auth.
  2. Reruns are idempotent and never overwrite unrelated commands, credentials,
     or modified generated configuration.
  3. Successful completion prints one exact `In a new terminal` handoff using
     the shipped auto-brain command; failures never print a false-success handoff.
  4. CLI/plugin/skills.sh/source installation docs all tell one coherent story.
  5. Focused and full tests plus package dry-run pass; PR merged and verified at
     `main`.
- **bound:** 2 PROVE runs / 3 review rounds.
- **exit ->** R2.

### R2 — RE-PLAN GATE

- **goal:** Audit the merged journey and present the release/product verdict.
- **prompt:**
  > At merged main, verify every R0/R1 criterion against tests and package
  > contents, run the clean temporary-home skill-first journey without provider
  > turns, record the test/package counts, and identify only genuine remaining
  > losing cells. Draft a successor chain only if evidence finds one.
- **accept:**
  1. A final EXIT maps every chain criterion to merged evidence.
  2. The package contains the skill entrypoint, routing guidance, generated
     effort support, and all required runtime files.
  3. npm publication state and any remaining release gate are explicit.
  4. User signs off on the product journey; this wait is unbounded.
- **bound:** 2 audit runs; user-signoff wait is unbounded.
- **exit ->** release or a successor chain.

## Chain invariants

1. No loop advances without an EXIT mapping every criterion to evidence.
2. No implementation proof starts OAuth or spends a provider/model turn.
3. Existing auth records and generated private setup are never copied or
   overwritten.
4. Model benchmark conclusions remain stage directions for agent judgment, not
   deterministic filename or task-text matching.
5. Routing candidates are capable-peer menus; live headroom and parent-pool
   preservation choose among them.
6. A reviewer never uses the author's model, and a named executor never repeats
   the active parent model when another capable lane exists.
7. Public artifacts contain no secrets, callbacks, account identifiers, raw
   provider payloads, private paths, or transcripts.
8. Kimi remains paused.
9. npm publication is a human-authorized external state change.
10. ZEN: declarative cast, prose specialization, one skill entrypoint, one
    durable command, and one honest fresh-terminal handoff.

## Execution log

- **R0 — complete (2026-07-21):** exact Fable/Sol peers, model-specific stage
  directions, task-fit routing menus, and supported agent effort landed through
  [PR #211](https://github.com/miguelrios/unc-skills/pull/211). Evidence:
  `docs/evidence/r0-evidence-informed-cast/EXIT.md`. Parable improves by
  keeping selection agentic while giving the parent much better priors. Next:
  R1 skill-first magical install and automatic brain choice.
