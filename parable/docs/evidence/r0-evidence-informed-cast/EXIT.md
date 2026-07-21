# R0 EXIT — Evidence-informed cast

Status: complete

## Acceptance map

1. **Exact Fable and Sol peers with model-specific stage directions.**
   `lib/onboarding.js` generates `fable_exact` and `sol_exact` alongside
   Terra, Luna, Sonnet, Opus, Haiku, and Grok. The generated config and
   `examples/parable.claude-subscriptions.toml` carry matching `use_for` and
   `avoid_for` prose. The all-vendor setup integration test asserts the exact
   eight-model catalog.
2. **Task-fit menus.** The generated and reference configurations contain
   `mechanical`, `data_transform`, `frontend`, `feature`, `refactor_wide`,
   `gnarly`, `review`, `smoke_test`, and `architecture`. The setup integration
   test pins the frontend and architecture menus.
3. **Supported effort reaches Claude Code agents.** `parable.py` renders an
   explicit `effort` field and validates Claude subagents against
   `low|medium|high|xhigh|max`. Unit tests reject `minimal` and `ultra` for that
   provider, while the finalize integration test verifies valid effort in all
   eight generated agent files.
4. **One selection policy.** `skills/parable/SKILL.md`,
   `references/routing.md`, and `references/config.md` agree: task arrays are
   capable-peer menus; the brain selects by task fit and live subscription
   headroom, excludes the current parent and review author, and treats only
   `routing.escalation` as ordered.
5. **Verification and delivery.** `cd parable && npm test` passed all 108 tests.
   `npm pack --dry-run --json` reported 33 files and included the changed
   runtime, skill, example, and onboarding files. `git diff --check` passed.
   Delivery is [PR #211](https://github.com/miguelrios/unc-skills/pull/211) to
   `main`; no provider authorization or model turn was used for proof.

## Review and ZEN

- Simple/general: model differences live in declarative prose and task menus,
  not deterministic filename or prompt matching.
- Prompt/agentic: the parent retains judgment and reads live headroom before
  dispatch.
- Beautiful/dope: either Fable or Sol can narrate while the other remains an
  exact named peer; effort follows the agent instead of ambient user settings.

## Next loop

R1 adds the portable skill entrypoint, durable `parable` command, automatic
Fable/Sol brain selection, and the honest fresh-terminal handoff.
