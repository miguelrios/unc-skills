# Discovery sources — priority order

Read until the three questions (run / auth / features) are answered. Each source below names
what it typically answers. Trace every learned fact to its source file — the report cites them.

| Priority | Source | Answers |
|---|---|---|
| 1 | `CLAUDE.md`, `AGENTS.md`, `.cursorrules` (repo root + parent dirs) | run commands, port maps, auth conventions, env gotchas — agent-facing docs are written exactly for this job |
| 2 | Feature catalog / spec docs (`docs/feature-spec/`, `docs/features/`, `FEATURES.md`, story files with acceptance criteria) | the feature inventory, ready-made pass criteria |
| 3 | `README.md`, `CONTRIBUTING.md`, `docs/` index | run + auth basics, project shape |
| 4 | `docker-compose*.yml`, `Dockerfile*`, `Procfile`, `Makefile`, `justfile` | services, ports, health checks, boot commands |
| 5 | Env samples (`.env.example`, `.env.template`, worktree/env config files) | required vars, auth tokens, per-instance ports |
| 6 | CI workflows (`.github/workflows/`, `.gitlab-ci.yml`) | the project's own definition of "tested", smoke commands worth reusing |
| 7 | Route/entrypoint code (router files, `App.tsx`/page dirs, CLI `main`s, OpenAPI specs) | feature inventory when no catalog exists; auth middleware behavior |
| 8 | Test suites (`test/`, `e2e/`, `cypress/`, `playwright/`) | executable examples of auth + API usage; reuse their fixtures |

Notes:
- Prefer the most instance-specific config: a per-worktree env file beats a global README.
- A repo's own e2e/smoke scripts are the highest-value shortcut — running them counts as
  matrix rows, witnessed by their output.
- When docs conflict with code, code wins; note the drift in the report.
