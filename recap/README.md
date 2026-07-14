# Recap

Recap answers the deceptively hard question: **what did that coding agent actually do?**

[![skills.sh](https://skills.sh/b/miguelrios/unc-skills)](https://skills.sh/miguelrios/unc-skills/recap)

Point it at the current Claude Code or Codex session and it reconstructs the goals, visible
decisions, significant actions, git state, tests, failures, recoveries, final result, and unfinished
work. Recall supplies one exact, redacted session boundary; Recap turns that evidence into a useful
story and timeline without pretending it can see hidden reasoning.

## Install

```bash
npx skills add miguelrios/unc-skills --skill recap
```

Or install it from the native `unc-skills` marketplace for Claude Code or Codex. The repository is
also a pi package. Recap currently understands Claude Code and Codex evidence; pi can run the skill
against those sessions but its own transcript format is not yet indexed by Recall.

## Use

```text
/recap
```

In Codex, use `$recap` or ask “recap this session.” For an older run, use Recall to identify the
exact session first, then ask Recap to explain it. Long sessions stay readable: the default answer
is concise, while exhaustive evidence lives in owner-private streaming ledgers. The host agent reads
bounded, content-addressed packets and seals every event to either a supported claim or an explicit
low-signal group before calling the recap exhaustive.

For sessions spanning multiple repositories, repeat `--repo` when collecting the private manifest.
Recap keeps event-observed actions, the usually unknown historical session end, and read-only git
state verified now as separate evidence surfaces, so a pre-existing branch diff is never presented
as work performed by the selected agent.

Recap never calls a model provider itself, never dumps private transcripts into a repository, and
never labels a session complete until every exported page has been consumed.
