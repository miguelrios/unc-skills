# The Loop Chain — Recall Brain Mac utility (2026-07-14)

> Package the consented Codex, Claude Code, Claude Cowork, and ChatGPT/Cowork export
> collectors behind one reproducible macOS utility. Every loop exits only on synthetic or
> content-free evidence; raw/private transcripts and identifying host data never enter Git.
>
> **Pacing:** autonomous

## Loop anatomy

| Field | Meaning |
|---|---|
| `goal` | One sentence describing the state change. |
| `prompt` | A self-contained execution brief for a fresh session. |
| `accept` | Evidence-based hard exit criteria and their safe artifacts. |
| `bound` | Maximum failed proof and review rounds before `AT_BOUND`. |
| `exit ->` | The loop triggered after a complete exit. |

## The ribbon

Every loop executes `RE-PLAN -> BUILD -> PIN -> PROVE -> MEASURE -> REVIEW -> MERGE -> EXIT`.
The pinned runtime is the package's immutable arm64 CPython 3.12 runtime. Private Mac canaries
may emit only aggregate counts, timing, schema-version presence, and opaque receipts. Public
fixtures are synthetic. A loop has at most two failed evidence-bearing PROVE runs and three
review/fix rounds. Instrument failures are diagnosed separately and do not burn the PROVE bound.

## The chain

**Order rationale:** freeze the privacy/source contract first, add the missing local parser next,
then unify installation and status surfaces, and only then deploy to a real Mac. This keeps
mechanical parsing ahead of packaging and live-state risk.

### M0 — SOURCE CONTRACT AND BASELINE

- **goal:** Freeze which Mac-local records are eligible for collection and the baseline package behavior.
- **prompt:**
  > Read the current collector, privacy policy, export inbox, Mac installer, and package tests.
  > Add a synthetic Cowork corpus matching every observed additive schema shape without copying
  > private values. Pin an allowlist contract: user/assistant natural-language records only by
  > default; system, reasoning, audit, tool input/output, attachment bodies, account identity,
  > titles, prompts, selected paths, and MCP configuration are excluded. Pin lifecycle semantics:
  > archive is not deletion and absence never implies a tombstone. Record the current test/runtime
  > baseline using content-free counts.
- **accept:**
  1. Synthetic Cowork fixtures contain user, assistant, system/tool, attachment, archive, replay,
     malformed, and additive-schema cases without real/private values.
  2. Tests fail unless excluded record classes produce zero connector records.
  3. Tests fail unless native identity is stable across replay and append.
  4. Baseline test count and package hash protocol are recorded in synthetic evidence.
  5. `EXIT.md` maps every criterion to evidence at HEAD.
- **bound:** 2 failed PROVE runs / 3 review rounds.
- **exit ->** M1.

### M1 — LOCAL SOURCE ADAPTERS

- **goal:** Add a privacy-first Claude Cowork adapter while preserving the existing Codex and Claude Code collectors.
- **prompt:**
  > Implement a tolerant Cowork JSONL parser over explicitly configured
  > `local-agent-mode-sessions/**/.claude/projects/**/*.jsonl` roots. Parse required record fields
  > and ignore additive metadata. Never read audit logs, browser stores, attachment bodies, or
  > session metadata prose. Route eligible records through the existing pre-spool privacy policy,
  > connector runner, checkpoint, ACK, and receipt protocols. Keep Codex and Claude Code behavior
  > byte-compatible. Add bounded file, record, and field-size handling plus symlink/race defenses.
- **accept:**
  1. Synthetic Cowork user/assistant messages normalize to stable canonical events.
  2. Planted PAT, API key, email, phone, and address never enter spool bytes under `scrub`/`drop`.
  3. Audit, system, reasoning, tool, and attachment content produce zero events by default.
  4. Replaying unchanged input commits zero duplicate events; one appended record commits exactly one.
  5. Malformed, oversized, replaced, and symlinked inputs fail closed without checkpoint advance.
  6. Existing Codex, Claude Code, privacy, connector, and server suites remain green.
  7. `EXIT.md` maps every criterion to evidence at HEAD.
- **bound:** 2 failed PROVE runs / 3 review rounds.
- **exit ->** M2.

### M2 — ONE MAC UTILITY

- **goal:** Expose every consented collector through one reproducible Mac package and lifecycle CLI.
- **prompt:**
  > Extend the packaged `recall-brain` utility and installer with explicit source choices for
  > Codex, Claude Code, Claude Cowork, and the selected export inbox. Generate private LaunchAgents
  > from the bundled runtime only. Add content-free preview/status output showing enabled source
  > classes, health, lag, checkpoint presence, and privacy mode without source paths, credentials,
  > record content, or exception text. Upgrade must preserve private spool/catalog state; disable
  > must unload the selected agent without deleting recoverable state; uninstall must remove code
  > and agents while leaving an explicit, tested state-retention choice.
- **accept:**
  1. One signed-format/reproducible tarball contains all collector code and the pinned runtime.
  2. Install, upgrade, per-source disable, and uninstall lifecycle pass on synthetic macOS paths.
  3. Generated LaunchAgents use absolute bundled-runtime paths and source-scoped Keychain accounts.
  4. Preview/status are content-free under adversarial synthetic paths, errors, and credentials.
  5. Existing two-source installs upgrade without duplicate agents or lost checkpoints.
  6. Two consecutive builds are byte-identical and the package secret/transcript scan is empty.
  7. `EXIT.md` maps every criterion to evidence at HEAD.
- **bound:** 2 failed PROVE runs / 3 review rounds.
- **exit ->** M3.

### M3 — REAL-MAC END-TO-END

- **goal:** Deploy the unified utility to the authorized Mac and prove safe cross-device Recall.
- **prompt:**
  > Build from the merged M2 HEAD, verify artifact identity, and install on the authorized arm64 Mac
  > using source-scoped Keychain authority and privacy mode `scrub`. Run a synthetic canary through
  > Codex, Cowork, and export-inbox inputs. Prove incremental collection, central search from another
  > device, receipt resolution, replay idempotency, append behavior, archive semantics, and explicit
  > receipt deletion. Emit only opaque receipts and aggregate counters to private evidence; publish
  > only a content-free attestation.
- **accept:**
  1. Installed package hash equals the twice-reproduced artifact hash from M2.
  2. All selected LaunchAgents are loaded, healthy, and use the bundled runtime.
  3. Each synthetic source becomes centrally searchable within 30 seconds from another device.
  4. Replay adds zero events; append adds exactly one; archive adds no tombstone.
  5. Planted secret-shaped content is absent from local spool and central response surfaces.
  6. Exact-receipt deletion emits one tombstone and removes the canary from active recall.
  7. No private transcript, credential, host path, or raw trace is present in Git/evidence.
  8. `EXIT.md` maps every criterion to evidence at deployed and repository HEADs.
- **bound:** 2 failed PROVE runs / 3 review rounds.
- **exit ->** M4.

### M4 — RE-PLAN GATE

- **goal:** Produce the measured verdict and cut the successor chain from remaining coverage gaps.
- **prompt:**
  > Read M0-M3 exits and report coverage, latency, privacy drops/scrubs, replay behavior, package
  > lifecycle results, and unresolved source gaps. Draft a successor chain only for losing cells,
  > including consumer ChatGPT cloud history if local/export coverage remains incomplete.
- **accept:**
  1. Verdict references only safe evidence and includes exact pass/fail cells.
  2. Successor chain is drafted from unresolved cells rather than editing this chain.
  3. User signs off on the next scope.
- **bound:** 2 drafts; user sign-off wait is unbounded.
- **exit ->** successor chain after the human gate.

## Chain invariants

1. No loop advances without an `EXIT.md` mapping every acceptance criterion to evidence.
2. Raw transcripts, prompts, tool traces, exports, credentials, private paths, and host identifiers
   never enter this public repository or public evidence.
3. Privacy classification happens before spool or network writes.
4. Local file absence and archive state never infer deletion.
5. Every source is explicit opt-in, private by default, and bound to its own source identity.
6. Semantic selection remains a small allowlist contract; parser code does not accumulate app-domain heuristics.
7. Bounds are enforced honestly; unmet criteria produce `AT_BOUND`, not a partial success claim.
