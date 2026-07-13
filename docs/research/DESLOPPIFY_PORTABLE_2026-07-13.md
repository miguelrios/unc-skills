# Desloppify, portability, and why code turns into slop

Date: 2026-07-13

## Research question

How can `unc-skills` offer a genuinely portable Desloppify skill for Codex,
Claude Code, pi, and other Agent Skills clients without forking the engine,
misrepresenting upstream work, leaking project data, or turning a health score
into a vanity metric?

## Upstream facts pinned for this work

- The upstream project is [Peter O'Malley's Desloppify](https://github.com/peteromallet/desloppify),
  an agent harness that combines mechanical detectors, subjective review, a
  persistent plan, and a `next`-driven repair loop.
- Research was pinned to upstream commit
  `3a7735d531a96b6a226bfbdc9fd662b14195f857` and PyPI version `1.0`, uploaded
  2026-05-13. Desloppify requires Python 3.11 or newer.
- The engine is licensed under Peter O'Malley's
  [Open Source Native License 0.2](https://github.com/peteromallet/desloppify/blob/main/LICENSE).
  Internal use is broadly permitted. Redistribution and derivatives carry
  conditions. The portable skill must therefore be original MIT-licensed
  orchestration that installs/calls the official package; it must not vendor
  the engine or copy the upstream skill wholesale.
- Upstream currently supports project skill overlays for AMP, Claude Code,
  Codex, Cursor, Copilot, Droid, Gemini, Hermes, OpenCode, Qwen, Rovo Dev, and
  Windsurf. Global setup is narrower. Some targets are dedicated skill files;
  others modify shared `AGENTS.md` or Copilot instruction files.
- Native automated subjective-review runners exist for Codex, OpenCode, and
  Rovo Dev. Claude Code and Hermes use their own subagent orchestration. Gemini
  currently uses sequential experimental subagents. A portable workflow must
  select a supported path honestly rather than pretending every harness has
  identical primitives.
- Desloppify stores local analysis and review packets under `.desloppify/`.
  That directory can contain code-derived context and must remain local and
  ignored by git.

## What “code slop” actually is

Code slop is code whose apparent delivery value exceeds its demonstrated
lifecycle value. It may compile and pass the happy-path test while making the
next safe change harder: unclear ownership, copied policy, misleading
contracts, accidental complexity, brittle initialization, weak failure
behavior, or tests that certify implementation trivia instead of behavior.

This is broader than “AI-generated code.” Generative tools increase production
capacity, which can enlarge an existing feedback-bandwidth mismatch, but human
teams create the same debt under the same incentives.

### The causal stack

| Root cause | Observable residue | Why a scanner alone is insufficient | Required intervention |
|---|---|---|---|
| Output pressure outruns review and design feedback | shortcuts, copied code, deferred cleanup | a finding inventory does not change incentives or execution order | bounded repair capacity, a prioritized queue, and proof before closure |
| Work is optimized locally without a current system model | boundary drift, hub modules, inconsistent protocols | local lint rules cannot judge ownership or architecture fitness | blind cross-file review plus root-cause clustering |
| Correctness is reduced to “the happy path passes” | missing failure tests, security gaps, misleading contracts | functional tests do not cover maintainability, authorization, or resilience | existing tests plus targeted regression, static analysis, and subjective review |
| Context and ownership decay across sessions and people | dead compatibility paths, mystery abstractions, stale docs | a point-in-time scan cannot recover intent by itself | preserve evidence, record deliberate tradeoffs, and re-review changed areas |
| Cleanup is managed as an undifferentiated backlog | easy nits get fixed while high-interest debt survives | raw issue counts reward volume and convenience | cluster by cause and order by risk, blast radius, and recurring interest |
| Metrics become targets | suppression, exclusions, cosmetic refactors | Goodhart pressure can improve a number while degrading the product | strict score, explicit exclusions, anti-gaming review, and behavior gates |
| Generated output is cheap but verification remains expensive | boilerplate, needless wrappers, defensive sprawl, plausible bugs | models can reproduce local patterns without understanding global intent | constrain generation, demand evidence, minimize net complexity, and review seams |

### Evidence behind the model

- Tufano et al. mined 200 open-source histories and found that code smells are
  commonly introduced during feature work and bug fixing, often when developers
  are under pressure; most persist rather than being promptly removed.
  [Paper](https://www.cs.wm.edu/~mtufano/publications/J3.pdf)
- A family of industry surveys found deadline/time pressure was the most cited
  cause of technical debt. This supports treating slop as a feedback and
  prioritization failure, not merely a developer-discipline failure.
  [Study](https://arxiv.org/abs/2109.13771)
- Palomba et al. studied 17,350 manually validated smell instances across 395
  releases and found smelly classes more change- and fault-prone than smell-free
  classes. Smells are signals worth investigating, but not proof in isolation.
  [Study](https://link.springer.com/article/10.1007/s10664-017-9535-z)
- Self-admitted technical debt is widespread, accumulates faster than it is
  removed, and can survive more than 1,000 commits even when eventually fixed.
  Persistent state and an execution queue matter more than a one-time report.
  [MSR study](https://doi.org/10.1145/2901739.2901742)
- Research on architecture erosion in OpenStack found review discussions can
  expose erosion symptoms and help prevent further decay. Structural review is
  complementary to mechanical detection.
  [Study](https://arxiv.org/abs/2201.01184)
- AI-assistance evidence is mixed rather than uniformly negative. A GitHub
  randomized study reported modest quality improvements on a bounded task,
  while independent security work found vulnerable Copilot suggestions in a
  substantial fraction of tested scenarios. The right conclusion is not “AI
  code is bad”; it is “generation speed does not remove the need for independent
  quality gates.” [GitHub study](https://github.blog/news-insights/research/does-github-copilot-improve-code-quality-heres-what-the-data-says/),
  [security study](https://arxiv.org/abs/2108.09293)
- A benchmark focused on code smells notes that mainstream code-generation
  evaluation overweights functional accuracy and undermeasures maintainability.
  [CodeSmellEval](https://arxiv.org/abs/2412.18989)

## Portability findings

The portable unit should be the Agent Skills directory, not an `AGENTS.md`
patch and not a harness-specific fork. The open Agent Skills format makes the
frontmatter the discovery layer and loads detailed instructions only when the
skill is activated. That matches the existing `unc-skills` packaging model:

```text
desloppify/
  skills/desloppify/SKILL.md       canonical instructions
  skills/desloppify/scripts/       deterministic bootstrap/doctor adapter
  skills/desloppify/references/    upstream, safety, and harness details
  .claude-plugin/plugin.json       native Claude package
  .codex-plugin/plugin.json        native Codex package
  package.json                     pi package
```

The same payload must be byte-identical in all three native installs. Harness
differences belong in a small routing table in a reference file. The canonical
skill should ask the current agent to use its own native concurrency when
available and fall back to Desloppify's prepared-packet/manual-import path when
it is not.

## Product principles derived from the research

1. **Engine upstream, workflow here.** Install and execute the official
   Desloppify package. Never vendor or silently mutate it.
2. **One native skill.** Ship one trigger-rich `SKILL.md`; package it natively
   for Claude Code, Codex, and pi and make it compatible with generic Agent
   Skills installers.
3. **Transparent versions.** `doctor` reports wrapper version, upstream CLI
   version, Python, runner availability, ignored state, and update commands.
   Normal scans never mutate the installed tool.
4. **No secret or source exfiltration.** Mechanical scans remain local.
   Subjective review uses the active harness or an explicitly selected external
   path. Review packets and state remain gitignored. No provider credential is
   read by the skill.
5. **Behavior before score.** Establish the project test/lint/typecheck gate
   before repair. A higher strict score with a regression is failure.
6. **Causes before counts.** Use findings to locate repeated causes, cluster
   related work, and fix the system that emits symptoms.
7. **Minimize net complexity.** A new abstraction must remove more cognitive
   load than it creates. Cosmetic churn is not progress.
8. **Honest stopping.** Work in bounded batches. Report remaining debt and
   uncertainty instead of suppressing or excluding it to manufacture closure.

## Risks to pin in tests and evaluation

- The skill triggers on generic “review this code” and hijacks ordinary work.
- A helper shells through a string and permits argument injection.
- Setup downloads or overwrites an upstream harness overlay unexpectedly.
- `doctor` prints credentials or the complete environment.
- `.desloppify/` is accidentally committed or included in npm artifacts.
- A monorepo root scan mixes unrelated programs and produces misleading state.
- The agent chooses the Codex runner from Claude Code, or claims native pi
  subagents that do not exist.
- The agent raises a score by exclusions, suppressions, or low-value churn while
  tests regress.
- Upstream changes make the wrapper stale but the user receives no actionable
  update signal.
- Attribution makes it appear that `unc-skills` authored or bundles Desloppify.

## Decision

Build an original skill named `desloppify` as a portable companion to the
official engine. Credit Peter O'Malley prominently, link the upstream project
and OSNL license, keep the wrapper MIT-licensed, and state that upstream is not
bundled. Provide a tiny Python adapter for deterministic discovery, doctoring,
and passthrough execution; keep all code-quality judgment in the agent workflow
and Desloppify itself.
