# unc-skills

Miguel's collection of portable Agent Skills for Claude Code, Codex, and pi.

| Skill | What it does | Cross-harness note |
|---|---|---|
| [`hands-free`](hands-free/) | Calls your phone when the coding agent needs an answer or approval. | Same Python/Vapi contract in all three harnesses. |
| [`parable`](parable/) | Plans implementation batches, routes work to cheaper executors, verifies, and reviews. | Claude/native subagents are used only when available; stock pi needs a configured CLI-backed executor. |
| [`cascade`](cascade/) | Runs large projects as bounded, evidence-gated development loops. | Falls back to a file-backed task graph when the harness has no task or wake primitives. |
| [`recall`](recall/) | Indexed local search over prior Claude Code and Codex sessions. | Runs from pi, but does not index pi's own transcripts yet. |

The skill payloads are canonical `skills/<name>/SKILL.md` directories. Harness-specific
manifests package those same files; there are no Claude/Codex/pi forks to drift apart.

## Install for Claude Code

```bash
claude plugin marketplace add miguelrios/unc-skills
claude plugin install hands-free@unc-skills
claude plugin install parable@unc-skills
claude plugin install cascade@unc-skills
claude plugin install recall@unc-skills
```

Install only the skills you want. Start a new session after installation.

## Install for Codex

```bash
codex plugin marketplace add miguelrios/unc-skills
codex plugin add hands-free@unc-skills
codex plugin add parable@unc-skills
codex plugin add cascade@unc-skills
codex plugin add recall@unc-skills
```

Codex uses the native `.agents/plugins/marketplace.json` and package
`.codex-plugin/plugin.json` manifests. Start a new session after installation.

## Install for pi

```bash
pi install git:github.com/miguelrios/unc-skills
```

The repository is one pi package that exposes all four skills. In pi, invoke one explicitly
with `/skill:hands-free`, `/skill:parable`, `/skill:cascade`, or `/skill:recall`.

## Compatibility evidence

The complete 4 skills x 3 harness clean-home matrix passes native installation, discovery, and
credential-free smoke checks. See the [matrix](docs/evidence/L4-clean-home-matrix/matrix.md),
[research](docs/evidence/L0-portability-baseline/research.md), and
[final verdict](docs/evidence/L5-portability-verdict/VERDICT.md).

Run the local gate with:

```bash
npm test
for package in hands-free parable cascade recall; do (cd "$package" && npm test); done
python3 scripts/prove_portability.py --output /tmp/unc-skills-portability
```
