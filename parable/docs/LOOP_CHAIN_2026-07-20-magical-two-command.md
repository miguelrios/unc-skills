# The Loop Chain — Parable magical two-command onboarding (2026-07-20)

> Successor to the completed first-run onboarding chain. Collapse the shipped
> multi-terminal runbook into `parable setup` followed by `parable claude`
> without weakening local credential ownership, exact-model validation, or
> process cleanup. Public evidence remains synthetic or content-free.
>
> **Pacing:** autonomous through implementation and merge. npm publication
> remains a final human-authorized release gate.

## Loop anatomy

| Field | Meaning |
|---|---|
| `goal` | One sentence. The state change the loop exists to produce. |
| `prompt` | Self-contained instructions sufficient for a fresh session. |
| `accept` | Safe, checkable evidence that pins success and fake-success paths. |
| `bound` | Maximum evidence failures and review/fix rounds before `AT_BOUND`. |
| `exit →` | The successor triggered by a complete EXIT. |

## The ribbon

```text
RE-PLAN → BUILD → PIN → PROVE → MEASURE → REVIEW → MERGE → EXIT
```

All product edits use fresh isolated worktrees. Hermetic proofs use temporary
homes, fake provider binaries, a loopback catalog, and a fake Claude process.
No proof starts OAuth, spends a provider turn, or records a credential. Live
checks are read-only and optional because the lifecycle claim is pinned by
synthetic subprocess and signal probes.

**Bound + escalation:** at most two correctly wired PROVE failures and three
REVIEW→fix rounds per loop. Instrument failures are disclosed separately. At
the bound, write an `AT_BOUND` EXIT naming unmet criteria and stop.

## The chain

**Order rationale:** measure current friction first; then change the mechanical
process lifecycle; finally prove the clean-machine path and align every public
entry point. No provider or model-routing semantics change.

### M0 — TWO-COMMAND CONTRACT AND BASELINE

- **goal:** Pin what “magical” means and measure the current extra steps before
  changing runtime code.
- **prompt:**
  > Read current `setup`, `proxy start`, `setup finalize`, and `claude`
  > behavior at merged main. Record the command count, current process
  > ownership, baseline tests/package result, and a precise two-command
  > contract. The contract must cover preinstalled versus missing proxy,
  > existing valid proxy reuse, occupied ports, readiness timeout, Claude and
  > proxy exits, signals, cleanup, exact catalog gating, and credential-safe
  > output. Do not call a provider or start OAuth.
- **accept:**
  1. A sanitized receipt records the current manual lifecycle and baseline.
  2. The contract defines ownership and exit behavior for every child process.
  3. Fake-success guards are named for timeout, collision, early exit, signal,
     cleanup, missing exact ids, and credential leakage.
  4. Focused PR is merged and verified at `main`.
- **bound:** 2 proof runs / 3 review rounds.
- **exit →:** M1.

### M1 — SELF-BOOTSTRAP AND SUPERVISED CLAUDE

- **goal:** Make the ordinary first run exactly `parable setup`, then
  `parable claude`.
- **prompt:**
  > Make interactive `parable setup` build the pinned proxy by default when no
  > executable is discoverable, after one explicit network/build consent;
  > preserve deterministic non-interactive flags. Replace the Node launcher
  > handoff with a supervisor: reuse an already healthy authenticated endpoint
  > without owning it, otherwise spawn the configured proxy privately, wait on
  > authenticated `/v1/models`, invoke the existing exact-catalog reconcile and
  > stock Claude launch, forward signals, preserve the meaningful child exit,
  > and stop only the proxy it owns. Never kill an unknown listener or print a
  > token. Keep `proxy start` and `setup finalize` as diagnostic escape hatches.
- **accept:**
  1. Clean interactive setup needs no `--build-proxy` flag after consent.
  2. `parable claude` owns start→readiness→catalog→Claude→cleanup in one terminal.
  3. A valid existing proxy is reused and never stopped; an occupied or wrong
     endpoint fails closed before Claude.
  4. Proxy timeout/early exit, Claude spawn/exit, SIGINT/SIGTERM/SIGHUP, and
     cleanup behavior are pinned without orphan processes.
  5. Exact-model and token-redaction guards remain green.
  6. Focused PR is merged and verified at `main`.
- **bound:** 2 correctly wired PROVE failures / 3 review rounds.
- **exit →:** M2.

### M2 — TWO-COMMAND DELIVERY AND RELEASE HANDOFF

- **goal:** Prove and document one honest two-command OSS path, then stop at
  the explicit package-release gate.
- **prompt:**
  > Run the complete hermetic lifecycle matrix from temporary clean homes,
  > align README, setup guide, CLI help, skill, provider reference, installer,
  > and standalone HTML with the merged behavior, and run the full suite plus
  > npm package dry-run. Record only sanitized counts and lifecycle facts.
  > Do not publish npm.
- **accept:**
  1. Canonical onboarding is `parable setup` then `parable claude`; exceptional
     auth and foreground commands remain documented as recovery tools.
  2. Hermetic clean, reuse, collision, timeout, failure, and signal paths pass.
  3. Full tests, privacy checks, package dry-run, and responsive HTML checks pass.
  4. Focused PR is merged and verified at `main`.
  5. npm publication remains an explicit human gate.
- **bound:** 2 correctly wired PROVE failures / 3 review rounds; release wait
  is unbounded.
- **exit →:** human npm release verdict or a successor chain.

## Chain invariants

1. No loop advances without an EXIT mapping every criterion to evidence.
2. No provider turn or new OAuth flow is required for implementation proofs.
3. Existing configuration and auth records are never overwritten or copied.
4. Only a proxy spawned by the current `parable claude` process may be stopped.
5. An unknown or unhealthy listener is never treated as readiness.
6. Exact catalog ids remain the only entitlement signal; aliases never substitute.
7. Public artifacts contain no secrets, callbacks, account identifiers, raw
   provider payloads, private paths, or transcripts.
8. Kimi remains paused.
9. npm publication is a human-authorized external state change.
10. ZEN: one setup command, one supervised launch command, declarative models,
    ordinary child-process ownership, and no daemon, broker, shared key, or
    deterministic model-name guessing.
