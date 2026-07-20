# E3 — OSS DELIVERY AND UPSTREAM STATUS · EXIT (2026-07-20)

## Status: COMPLETE

## The headline evidence

The exact E1 two-commit change now ships as a checksum-pinned patch in the
Parable repository. Starting from a fresh copy of CLIProxyAPI's official
`v7.2.88` commit archive, that artifact applied both commits without
intervention, passed the focused tests and vet, passed the complete Go suite,
and built the server.

The five-minute guide carries an OSS user from the official source pin through
patch verification, local build, loopback configuration, ChatGPT OAuth, model
catalog verification, and a stock Claude Code session on Sol. It does not
require or mention an LLM broker.

## What shipped

| Piece | Where |
|---|---|
| Checksum-pinned source patch | `patches/cliproxyapi-v7.2.88-claude-effort.patch` |
| End-to-end OSS setup | `docs/CLIPROXYAPI_GPT_SUBSCRIPTION.md` |
| Machine-readable packaging/build receipt | `docs/evidence/e3-cliproxy-effort-oss/receipt.json` |
| Provider reference link | `skills/parable/references/providers.md` |
| README entry point | `README.md` |

## Bound accounting (honest)

- Documentation proofs: 1. The guide's catalog `jq` expression was executed
  against a synthetic three-model response and returned true.
- Patch applications: 1 clean application to the official archive.
- Evidence failures: 0.
- Instrument corrections: the first targeted cross-protocol command selected
  the parent test but used the unnormalized case name, so Go reported no child
  tests. The corrected parent-level command listed and passed the intended
  `CaseC14-top-level-effort-without-thinking` alongside the matrix. This was a
  diagnostic selector correction, not a patch or evidence failure.
- Review rounds: 1. Checks covered source/archive/patch hashes, changed-file
  scope, applyability, released-versus-patched wording, executable commands,
  link resolution, credential boundaries, JSON validity, diff hygiene, and
  full test suites. No critical issue or warning remained; local
  Claudio-Michel review verdict 5/5. `git diff --check` passes for the product
  and evidence files; the vendored mail patch is excluded from that container
  check because its required inner unified-diff context markers look like
  whitespace to the outer diff. The applied source diff itself passes
  `git diff --check`.

ZEN check: one portable patch, one local process, one declarative Parable
configuration. No model-name shim, broker, hosted control plane, credential
vendor, or magic installer is added. The guide exposes every pin and boundary.

## Acceptance criteria → evidence

1. Exact known-good source state — ✅ the guide and receipt pin CLIProxyAPI
   `v7.2.88` / `93d74a…`, the official archive SHA-256, the vendored patch
   SHA-256, Go `1.26.5`, and Claude Code `2.1.215`.
2. No unmerged patch represented as a release — ✅ every user-facing surface
   says released `v7.2.88` remains medium-only and labels the exact-control
   route as patched source.
3. Upstream state recorded — ✅ the receipt links the public upstream
   repository and records the precise blocker: the required Claudio Michel
   GitHub App has no push, triage, maintain, or admin permission there. No
   issue, PR, merge, or release is fabricated.
4. Full verification and merge — ✅ the fresh-archive patch application,
   focused tests, focused vet, `go test -count=1 ./...`, and server build all
   exited zero; the npm dry-run includes both guide and patch, and the Parable
   suite and focused delivery PR are recorded below.

## Proof scoreboard

| Proof | Result |
|---|---|
| Patch SHA-256 | `d35b422da321265150fe393da80a686862ef642ee45c65a3e2fb908d689d5d1f` |
| Patch commits applied | 2/2 |
| Focused thinking + translator tests | PASS |
| Cross-protocol matrix | PASS, including top-level effort case |
| Focused Go vet | PASS |
| Complete CLIProxyAPI Go suite | PASS |
| CLIProxyAPI server build | PASS |
| Patched live GPT effort | 15/15 exact |
| Patched live tool canaries | 3/3 |
| Kimi | PAUSED |

## Merge verification

- Parable tests: 81/81 PASS in an isolated HOME.
- Local Claudio-Michel review: 5/5.
- Focused delivery PR: [#148](https://github.com/miguelrios/unc-skills/pull/148).
- Verified merged `main`: `c126a988673e82b65e06f55f7c96d8eaa3657768`.

## exit → E4

Present the released-versus-patched verdict and pause at the human re-plan
gate. The recommended successor is a Sol parent invoking one exact named GPT
subagent; Kimi stays paused.
