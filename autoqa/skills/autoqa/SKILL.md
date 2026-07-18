---
name: autoqa
description: Self-QA a repo's running application end-to-end — discover how it runs, build a feature×modality test plan, execute it against a live instance, and produce a witnessed pass/fail report. Use when the user says "autoqa", "QA this repo/branch", "verify every feature works", "release readiness check", "test this like a QA engineer would", or another skill needs an automated QA pass before sign-off.
---

# autoqa — QA any repo against its own running app

You are the QA engineer for this repo. The output is a **witnessed** report: every verdict
points at an artifact (an HTTP response, a page snapshot, a log line, a DB row). A check
without a witness is not a check — rerun it or mark it UNTESTED, never infer a pass.

## Phase 0 — RESOLVE

Establish three facts before anything else:

1. **Target repo** — path or URL the user pointed at (ask only if truly absent).
2. **Target instance** — a running deployment to test against (URL/port), or the
   instruction to bring one up locally.
3. **Repo config** — look for `AUTOQA.md` at the repo root or under `docs/`. It is the
   repo's pre-answered discovery: how to run, how to auth, where the feature catalog
   lives, known env caveats. **If found, read it now and skip straight to Phase 2.**
   Missing config means full Phase 1 — and a repo you QA repeatedly earns one: write
   `AUTOQA.md` from what Phase 1 taught you and it becomes the config for every later run.

Done when: repo path, instance URL (or "must boot"), and config-or-none are stated.

## Phase 1 — DISCOVER

Read the repo the way a new engineer would, in this order, stopping when the three questions
below are answered. Full source-priority list and what each source answers:
[references/discovery.md](references/discovery.md).

- **How does it run?** Dockerfile / compose / Procfile / Makefile / CI workflows / README.
- **How do I authenticate?** env samples, auth middleware, dev-token conventions, CLAUDE.md / AGENTS.md.
- **What are the features?** a feature catalog or spec doc if the repo ships one (use it —
  it beats inference), else routes/pages/CLI entrypoints enumerated from code.

Done when: you can write down the run command, an auth recipe, and a feature inventory —
each traced to the file that taught you it.

## Phase 2 — PLAN

Write the test matrix to a scratch file before executing anything: one row per feature,
columns = modality, check, pass criterion, witness to capture.

- **Every feature in the inventory gets a row.** When the repo ships a feature catalog,
  the matrix enumerates *all* of it, each row landing on one disposition: DEEP (run the
  end-to-end check), SMOKE (load the surface, assert its key content), UNTESTED (needs a
  fixture you don't have), SKIPPED (the branch predates the feature). A run that covers
  only the headline features is a smoke pass — say so in the report rather than implying
  full coverage.
- **Modalities**: API (endpoint calls), UI (browser tooling — chrome-devtools MCP,
  Playwright, or whatever the session has), CLI (the repo's own binaries). Cover every
  modality the app actually has; a web app QA'd only through its API is half-tested.
- **Prefer the user's path.** Drive each feature the way a user reaches it — through the
  UI, or the API call the client actually issues. An endpoint you found in the router may
  be dead code; a check that only you can trigger proves little.
- **Pass criteria are concrete**: status codes, visible text, row counts, terminal job
  states — never "looks right".
- Order rows: boot/health/auth first (everything else depends on them), then core money
  paths, then edge/regression rows.

Done when: the matrix exists on disk, every inventory feature has a row, and every row
carries all four columns.

## Phase 3 — EXECUTE

Run the matrix top to bottom against the live instance.

- Instance not up? Bring it up exactly as discovery taught — respect the repo's own
  runbook (secret-injection wrappers, port maps) over generic docker commands.
- Capture the witness for every row as you go into an evidence directory (default
  `<scratch>/autoqa-evidence/`): curl output with status codes, page snapshots or
  screenshots, log excerpts. Name files by row.
- A failing row gets one diagnosis pass: is it the app, the env, or your check? Fix
  env/check mistakes and rerun; app failures stay failed and get a one-line cause.
- **Before filing a failure, prove the feature is reachable.** Trace the entry point a
  user would take — the button, link, or route that leads here. A break behind a path
  nothing navigates to is dead code, and the finding is "remove this", not "fix this".
- Async work (jobs, builds) is polled to a terminal state — "still running" at report time
  is UNTESTED with a note, not a pass.
- Leave the instance as healthy as you found it; if you restarted anything, re-verify
  health before reporting.

Done when: every matrix row is PASS, FAIL (with cause), or UNTESTED (with reason), each
with a witness file.

## Phase 4 — REPORT

Write the report from the template in
[references/report-template.md](references/report-template.md): verdict table (feature,
modality, result, witness path), failure triage (release blocker vs env quirk vs test
bug), and the one-paragraph bottom line a release owner can act on.

Done when: the report file exists, every table row's witness path resolves, and the bottom
line states ship / don't-ship / ship-with-caveats.

## Hard rules

- **No witness, no verdict.** Reruns beat inference.
- **The repo's runbook outranks your habits** — a project whose docs wrap startup in a
  secret-injection command never gets a bare `docker compose up`.
- **Report failures as found** — a QA pass that only reports greens is a failed QA pass;
  triage severity honestly instead of softening results.
