# autoqa

Point it at a repo; it QAs the running app like an engineer would.

1. **RESOLVE** — target repo, target instance, and an optional repo-specific overlay
   (`autoqa-<project>` skill or `SELFQA.md`) that pre-answers discovery.
2. **DISCOVER** — read what the repo already says about itself (CLAUDE.md/AGENTS.md,
   feature-spec docs, compose files, env samples, CI) to learn run, auth, and the feature
   inventory.
3. **PLAN** — a feature×modality matrix (API / UI / CLI) with concrete pass criteria.
4. **EXECUTE** — run every row against the live instance, capturing a witness artifact per
   check.
5. **REPORT** — verdict table + failure triage + a ship/don't-ship bottom line.

The invariant: **no witness, no verdict.** Repo-specific overlays make repeat runs instant —
write one per product and the vanilla skill skips discovery entirely.
