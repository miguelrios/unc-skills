# unc-skills

Miguel's personal collection of Claude Code skills and plugins.

| Plugin | What it does |
|---|---|
| [`hands-free`](hands-free/) | Route AI coding-assistant approval and input prompts to a phone call through Vapi. Works with Claude Code and Codex. |
| [`parable`](parable/) | Multi-model coding orchestration: plan, route to the cheapest capable executor model, verify with code, review with fresh eyes. |
| [`cascade`](cascade/) | Cascading development loops: a chain of bounded, evidence-gated loops — each loop one development cycle with checkable exit evidence; a loop's exit triggers the next. |

## Install

```bash
claude plugin marketplace add miguelrios/unc-skills
claude plugin install hands-free@unc-skills   # or parable, cascade
```
