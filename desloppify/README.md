# Desloppify

> Turn “this repo feels cursed” into a queue, a proof, and a cleaner codebase.

This is a portable Agent Skill for
[Desloppify](https://github.com/peteromallet/desloppify), the codebase-health
engine created by **Peter O'Malley**. Peter built the engine, detectors, scoring,
and upstream workflow. This companion makes that workflow travel cleanly across
Codex, Claude Code, pi, and other Agent Skills clients.

The companion is MIT-licensed. The separately installed official engine remains
[OSNL-0.2 licensed](https://github.com/peteromallet/desloppify/blob/main/LICENSE)
and is not bundled here.

## Level 1: summon the janitor

Install the skill:

```bash
npx skills add miguelrios/unc-skills --skill desloppify
```

Install the official engine in an isolated Python tool environment:

```bash
uv tool install --upgrade 'desloppify[full]>=1,<2'
```

Then tell your agent:

> Use Desloppify to clean up this codebase without gaming the score.

The agent scopes one coherent project, protects the existing behavior gate,
scans, reviews what linters cannot see, clusters common causes, and works the
queue in bounded batches.

## Level 2: see if the floor is lava

The skill ships a local doctor. Your agent resolves `<skill-dir>` from its
installed skill location:

```bash
python3 <skill-dir>/scripts/desloppify_portable.py doctor --project .
```

It checks Python, the official engine version, active harness, available review
runners, and whether `.desloppify/` is safely ignored. It performs no network
request, install, model invocation, or shared instruction-file write.

Every readiness check line should be `ok`; the remaining lines describe the
selected harness, review route, and update policy. Add `--json` for the full
structured report. Then the agent runs the official CLI through an argv-safe
launcher:

```bash
python3 <skill-dir>/scripts/desloppify_portable.py run -- scan --path .
python3 <skill-dir>/scripts/desloppify_portable.py run -- next
```

## Level 3: fight causes, not lint confetti

```text
behavior baseline
       │
       ▼
mechanical scan ──→ blind structural review
       │                    │
       └────────┬───────────┘
                ▼
      root-cause clusters
                │
                ▼
 next → fix → prove → resolve → rescan
```

The strict score is a compass, not a release button. A score increase with a
test regression is failure. So is hiding authored code, mass-suppressing hard
findings, or adding abstractions that cost more than they remove.

The skill asks a more useful question than “how many warnings can we delete?”:

> What system keeps emitting these symptoms, and what proof lets us fix it
> without changing product behavior?

## Level 4: run native, stay honest

- **Codex** uses Desloppify's isolated native batch runner.
- **Claude Code** prepares a blind packet and uses native context-isolated
  subagents in small waves.
- **pi** uses the prepared-packet/manual-import route; stock pi is not presented
  as a trusted upstream batch runner.
- **Hermes**, **Gemini**, **OpenCode**, **Rovo Dev**, and generic clients get
  explicit routes and limitations in the bundled reference.

All native packages load the same canonical `SKILL.md`; there are no drifting
Claude/Codex/pi instruction forks.

Native installs are also available:

```bash
# Claude Code
claude plugin marketplace add miguelrios/unc-skills
claude plugin install desloppify@unc-skills

# Codex
codex plugin marketplace add miguelrios/unc-skills
codex plugin add desloppify@unc-skills

# pi (installs the full unc-skills collection)
pi install git:github.com/miguelrios/unc-skills
```

Browse the skill at
[skills.sh/miguelrios/unc-skills/desloppify](https://skills.sh/miguelrios/unc-skills/desloppify).

## Level 5: updates without mystery meat

The companion and engine update independently:

```text
companion  → update through your unc-skills/plugin/skills.sh installer
engine     → uv tool upgrade 'desloppify<2'
```

The JSON doctor report includes the companion version, official engine version,
and supported upstream range. It never upgrades during a scan. Pin the official
engine version in CI when reproducibility matters, and review upstream's license
before redistributing the engine in a product or service.

Do not run `desloppify update-skill` over this install: that command installs an
upstream harness-specific document, while this package already supplies the
portable skill. Engine bugs belong upstream; companion packaging bugs belong
here.

## Build it, scan it, prove it

```bash
npm test
npm run pack:check
```

The gate covers trigger scope, local-state privacy, missing and stale engines,
nested monorepo paths, harness routing, secret redaction, adversarial argv,
upstream exit-code fidelity, manifests, and package leak detection.
