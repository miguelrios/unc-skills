# autoqa report template

```markdown
# autoqa report — <repo> @ <branch/commit> on <instance>

**Date:** <iso date>  **Instance:** <url>  **Evidence:** <evidence dir>

## Bottom line

<One paragraph: ship / don't-ship / ship-with-caveats, and why. Written for the release
owner, no jargon invented during the run.>

## Verdict table

| # | Feature | Modality | Check | Result | Witness |
|---|---------|----------|-------|--------|---------|
| 1 | Health/boot | API | GET /health → 200 | PASS | evidence/01-health.txt |
| 2 | <feature> | UI | <check> | FAIL | evidence/02-<slug>.png |
| 3 | <feature> | — | <not run> | UNTESTED | needs <fixture> |
| … | | | | | |

Results are exactly PASS / FAIL / UNTESTED / SKIPPED. Every row has a witness path that
resolves. State the tier plainly: full-catalog coverage, or a smoke pass over headline
features.

## Failures — triage

| # | Failure | Severity | Cause |
|---|---------|----------|-------|
| 2 | <what broke> | release blocker / env quirk / test bug / dead code | <one line, incl. the user entry point that reaches it> |

## Coverage notes

- Features in inventory but UNTESTED, and why (fixture missing, env can't reach, …).
- Modalities skipped and why (no browser tooling in session, …).

## Instance health after run

<health check output; anything restarted and re-verified>
```
