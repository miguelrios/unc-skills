# parable

<p align="center">
  <img src="assets/hero.jpeg" alt="parable" width="400">
</p>


[![skills.sh](https://skills.sh/b/miguelrios/unc-skills)](https://skills.sh/miguelrios/unc-skills/parable)

**Multi-model coding orchestration for Claude Code, Codex, and pi.**

## Fable directs. The cast ships.

```mermaid
flowchart LR
    You(["You<br/><i>name the outcome</i>"]) --> F["Fable<br/><b>the brain</b><br/>plans · routes · judges"]
    F --> R{"cast each scene<br/>by fit + headroom"}

    R -->|"Agent tool"| A["Claude subagents<br/>Sonnet implements<br/>Opus reviews"]
    R -->|"codex exec"| C["Codex CLI<br/>GPT builds · debugs · reviews"]
    R -->|"cursor-agent"| X["Cursor CLI<br/>Composer implements<br/>Grok 4.5 challenges"]
    R -->|"pi / API"| P["Open model bench<br/>Kimi · MiniMax · DeepSeek"]

    A & C & X & P --> W["shared worktree<br/><b>one diff, many hands</b>"]
    W --> T["deterministic gate<br/>typecheck · tests"]
    T -->|"PASS"| Q["cross-family review<br/>reviewer ≠ author"]
    T -.->|"FAIL + compact evidence"| E["resume the author<br/>same session · warm context"]
    E --> W
    Q --> F
    F -->|"commit with receipts"| You

    classDef brain fill:#6d28d9,color:#fff,stroke:#c4b5fd,stroke-width:3px;
    classDef cursor fill:#111827,color:#fff,stroke:#22d3ee,stroke-width:3px;
    classDef gate fill:#052e16,color:#fff,stroke:#4ade80,stroke-width:2px;
    class F brain;
    class X cursor;
    class T,Q,E gate;
```

Fable never implements the scene. It spends its expensive tokens on decomposition, routing,
verification, and judgment; the harnesses do the typing.

### Cursor is the third lane

Parable drives `cursor-agent` headlessly alongside Claude subagents, Codex, and pi. That adds
two particularly useful cast members:

- **Composer 2.5** — Cursor's exclusive agentic model, excellent for fast implementation bursts
  from a precise `plan.md`.
- **Grok 4.5** — a genuinely different model family for features and adversarial review. Choose
  the reasoning tier in the model slug: `grok-4.5-low`, `grok-4.5-high`, or
  `grok-4.5-xhigh`, with fast variants when available.

Cursor runs are real executor sessions, not pasted prompts: Parable streams the plan on stdin,
captures the chat ID, resumes the same session after failed checks, and uses Cursor's read-only
plan mode for reviews. The included [three-subscription cast](examples/parable.cursor.toml)
load-balances Claude, ChatGPT, and Cursor plan headroom while keeping Grok as a boutique
third-family reviewer rather than burning the smaller Cursor budget on bulk work.

It is Tuesday. You are pair-programming with Fable on a small task: extract a helper
function and add a test. Three hundred lines later, the helper has its own module and two new
dependencies. The model is pleased with itself.

You open the billing console.

## Unscientific stats

One real feature (a new research tier in a production monorepo), same spec and same base commit,
run twice headlessly — a plain Fable session vs parable orchestrating, Fable as the brain. Spend straight from the
LLM proxy's logs. One run per arm: an anecdote with receipts, not a benchmark.

| | Plain Fable session | With parable |
|---|---|---|
| Fable (the expensive one) | **$44.12** | **$16.08** (−64%) |
| Sonnet subagent — scouting | — | $2.08 |
| Opus subagent — review | — | $2.54 |
| Kimi K2.7-code — implementation (373 requests) | — | $6.99 |
| MiniMax M3 — mechanical edits | — | $0.05 |
| **Total** | **$44.12** | **$27.74** |

The subagent lines ride a Claude plan and the Fable line is your rate-limit budget if your
session does too — so the cash you actually send anywhere is the $7 of metered Fireworks/OpenRouter
tokens. A blind Opus judge scored the two diffs 94 and 88; parable's was smaller and carried more
tests.

## The fable

In a parable, the storyteller does not act out the scenes. Your session model, the most expensive
one you run, becomes the **brain**: it writes a fully-specified `plan.md` for each task (the
story), casts the cheapest capable **executor** model to perform it (the cast), checks the result
with your own typecheck and tests before any model spends tokens on opinions, and hands the diff
to a reviewer that is never the author.

The brain never implements — it knows what its own tokens cost.

## The cast

You configure the cast in one TOML file, with real prices and plain-prose stage directions
(`use_for`, `avoid_for`) that the brain reads verbatim when deciding who plays which scene.
A reference troupe, at their actual rates:

**The Sparrow** (Sonnet, already lives in your house)
Default implementer with zero configuration. Follows a well-written story to the letter, which
means it builds exactly what you wrote. Write the story properly.

**The Owl** (Opus, also on payroll)
Reviews and smoke-tests. Has opinions and expresses them in complete sentences. You will not
always like them. That is the point.

**The Mule** (Kimi K2.7-code, $0.95/M in, $4.00/M out)
Carries features and bugfixes without complaint. Writes code like someone who has been writing
code longer than you have been debugging it.
*Moral: strong legs cost less than strong opinions.*

**The Fox** (MiniMax M3, $0.30/M in, $1.20/M out)
Alarmingly cheap; handles boilerplate and first-pass review. Do not ask it about your
architectural decisions, because it will agree with all of them.

**The Elephant** (DeepSeek V4 Pro, $1.74/M in, $3.48/M out, 1M context)
Holds your entire repository in its head at once, for the refactors that touch everything.

**The Magpie** (GPT-5.5 via codex)
Collects shiny things from a different training run. Useful for gnarly debugging and adversarial
review, because it reads your codebase as an outsider — and it rides your ChatGPT plan.

**The Swift** (Composer 2.5 via Cursor)
Moves quickly when the story is precise. Cursor-exclusive, lean in the harness, and ideal for a
mechanical burst that should be finished before the brain is tempted to type it itself.

**The Jackal** (Grok 4.5 via Cursor)
Circles the diff from a third model family and notices different prey. Use it when Claude and
OpenAI agree suspiciously fast, or when a high-stakes review deserves one more worldview.

Swap any of them or add your own; the cast list is yours.

## Four acts

**Act I: no keys, works now.** Install and go. The Sparrow implements, the Owl reviews, your
session model narrates, and everything runs as Claude subagents with nothing to configure.

**Act II: enter the Magpie.** Install the [codex CLI](https://github.com/openai/codex), log in,
and add a `codex-native` provider. GPT-5.5 joins for the hard scenes.

**Act III: open the Cursor lane.** Install the [Cursor CLI](https://cursor.com/docs/cli), add a
`type = "cursor"` provider, and Composer plus Grok 4.5 can implement or review through your Cursor
plan. Runs are headless, resumable, and normalized into the same compact evidence as Codex/pi.

**Act IV: the full troupe.** Add any OpenAI-compatible provider with one `[providers.*]` block
plus an `[executors.*]` block per model. codex drives Responses-API providers (Fireworks,
OpenRouter, your own LiteLLM proxy); a `type = "pi"` provider runs the
[pi coding agent](https://github.com/earendil-works/pi) as a second harness and speaks plain
chat-completions to any base URL, so chat-only providers need no bridge at all. See
`skills/parable/references/providers.md` and `examples/`.

For the non-coding half: with `[research] provider = "grep.ai"` (the default), in-depth research
and research-backed slides, sheets, and docs route through the free
[grep-research-skills](https://github.com/Parcha-ai/grep-research-skills) package — the research
runs on grep.ai's hosted service, quick lookups stay in-session, and setting `"claude"` keeps
everything local to your session instead.

## The script

```toml
[providers.fireworks]
type = "codex"
base_url = "https://api.fireworks.ai/inference/v1"
env_key = "FIREWORKS_API_KEY"
wire_api = "responses"

[executors.kimi]
provider = "fireworks"
model = "accounts/fireworks/models/kimi-k2p7-code"
effort = "high"
cost = { in = 0.95, out = 4.00, cache_in = 0.19 }
tags = ["implementer", "agentic"]
use_for = "Default implementer: fast, strong tool loop."

[routing]
feature = ["kimi", "sonnet"]
review  = ["minimax", "opus"]
```

The brain routes by reading your prose; there is no scoring function underneath. If the cast
keeps producing the wrong scene, look at the stage directions first.

## The part where it checks its own work

Your typecheck and tests run before any model spends tokens forming an opinion about the diff —
code is the cheapest witness. The result is a `PASS` or a `FAIL`, not a vibe.

Failures go back to the same executor session (context intact, cache warm) as a compact
evidence report. Models can usually fix what they broke; they just need to be told, concretely,
that they broke it. A one-line string change gets a check and a glance; a billing change gets a
frontier adversarial reviewer. And the reviewer is never the author — parable refuses to run
that configuration.

*Moral: never let the author hold the pen during the final read.*

## Install

skills.sh:

```bash
npx skills add miguelrios/unc-skills --skill parable
```

```bash
# Claude Code plugin marketplace
claude plugin marketplace add miguelrios/unc-skills
claude plugin install parable@unc-skills

# Codex plugin marketplace
codex plugin marketplace add miguelrios/unc-skills
codex plugin add parable@unc-skills

# pi package (installs the complete unc-skills collection)
pi install git:github.com/miguelrios/unc-skills

# standalone Claude/manual installer (adds the skill + a starter config)
npx @parcha/parable install
npx @parcha/parable doctor

# manual
git clone https://github.com/miguelrios/unc-skills && cd unc-skills/parable && ./install.sh
```

Requirements: Claude Code, Codex, or pi as the orchestrating harness; Python 3.11+; codex CLI
for codex-backed executors; Cursor CLI plus `CURSOR_API_KEY` for Cursor-backed executors; pi CLI
(node 22+) for pi-backed executors.

Claude Code and Codex builds with native agent spawning can use Parable's zero-config subagent
cast. Stock pi has no built-in subagents, so configure at least one Codex, pi, or Cursor executor
in a harness-neutral `parable.toml` before dispatching. Installation parity does not erase that
runtime difference.

## What's in the box

- `skills/parable/SKILL.md`: what the brain reads — the strategy, the house rules, and the
  environment facts it can't derive on its own. Deliberately small; the method is the model's.
- `skills/parable/scripts/parable.py`: the dispatcher, stdlib only, with `config`, `list`,
  `run`, `resume`, `status`, `verify`, and `review` subcommands. It runs codex and pi headlessly
  with per-invocation provider injection, drives Cursor through `cursor-agent`, and reports
  compact run summaries the brain can read for pennies. Your `~/.codex/config.toml` and `~/.pi`
  are never touched.
- `skills/parable/references/`: config schema, provider recipes, routing playbook, reviewer
  rubric, and a commented example config. `examples/` holds minimal Fireworks, OpenRouter,
  LiteLLM, and pi-Fireworks casts.

## Credits

parable grew out of [dctanner's `cook` skill](https://gist.github.com/dctanner/54c57da4a94a24e71df6281f487f51e1),
the original plan-then-codex-then-review loop, generalized into a configurable cast with
cost-aware routing and a verification-first review ladder.

MIT © Parcha Labs

*Moral: the expensive model should tell the story, not type it.*
