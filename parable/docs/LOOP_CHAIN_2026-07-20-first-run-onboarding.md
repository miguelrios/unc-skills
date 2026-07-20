# The Loop Chain — Parable first-run subscription onboarding (2026-07-20)

> Successor to the completed 30/30 subscription-subagent proof. Turn the
> source-pinned but manual OSS recipe into a safe first-run CLI without
> weakening per-user OAuth ownership, exact-model fail-closed behavior, or
> loopback-only transport. Raw OAuth URLs, callbacks, tokens, account ids, and
> provider responses never enter Git.
>
> **Pacing:** autonomous through implementation and merge. npm publication is
> a final human-authorized release gate and is outside this chain.

## Loop anatomy

| Field | Meaning |
|---|---|
| `goal` | One sentence describing the loop's state change. |
| `prompt` | Self-contained instructions sufficient for a fresh session. |
| `accept` | Safe, checkable artifacts and explicit fake-success guards. |
| `bound` | Maximum evidence failures and review/fix rounds before `AT_BOUND`. |
| `exit →` | The successor triggered by a complete EXIT. |

## The ribbon

```text
RE-PLAN → BUILD → PIN → PROVE → MEASURE → REVIEW → MERGE → EXIT
```

All product edits use fresh isolated worktrees. Hermetic proofs use temporary
homes, fake provider binaries, and loopback fake catalogs. A later live smoke
may read the already authorized local CLIProxyAPI catalog but must not start an
OAuth flow, rewrite an auth record, expose a credential, or spend a model turn.

**Bound + escalation:** at most two correctly wired PROVE failures and three
REVIEW→fix rounds per loop. Instrument failures are disclosed separately. At
the bound, write an `AT_BOUND` EXIT naming unmet criteria and stop.

## The chain

**Order rationale:** pin safety and UX first; implement reversible local files
before auth subprocesses; prove catalog-driven exact selection before a live
read-only smoke; publish only after the complete hermetic and package gates.

### O0 — ONBOARDING CONTRACT AND BASELINE

- **goal:** Pin the first-run command surface, security boundaries, and current
  missing behavior before product code changes.
- **prompt:**
  > Record the current source/npm version split, current CLI help, manual setup
  > steps, and the intended `setup|auth|proxy` commands. Define file ownership,
  > permissions, overwrite behavior, vendor/model mapping, headless Claude
  > callback guidance, and explicit non-goals. Add a sanitized receipt and EXIT;
  > spend no provider turn and run no OAuth flow.
- **accept:**
  1. The contract names every command, generated file, mode, and failure mode.
  2. Fake-success guards cover missing proxy, existing files, missing ChatGPT,
     absent exact catalog ids, and credential-safe output.
  3. The npm/source version split is measured without publishing.
  4. Focused PR is merged and verified at `main`.
- **bound:** 2 documentation proofs / 3 review rounds.
- **exit →:** O1.

### O1 — SECURE LOCAL BOOTSTRAP

- **goal:** Implement deterministic first-run configuration and proxy discovery
  without touching provider OAuth.
- **prompt:**
  > Add `parable setup` with interactive vendor selection and explicit
  > non-interactive flags. Generate a random localhost client token, loopback
  > CLIProxy YAML, shell environment file, and exact Parable TOML for only the
  > selected vendors. Resolve the proxy from an explicit flag, environment, or
  > PATH. Add `parable proxy build` for an explicit source-pinned managed build;
  > `setup --build-proxy` may call it, while interactive setup must ask before
  > network/build work. Refuse existing files unless their complete setup is
  > valid and reusable; never print the token. Pin modes and symlink safety.
- **accept:**
  1. ChatGPT is mandatory for the Sol parent; Claude and xAI are independently
     optional; Kimi is absent.
  2. Files are atomic, mode 0600 under mode-0700 directories, loopback-only,
     token-free on stdout/stderr, and never silently overwritten.
  3. Exact selected executor ids/models and routing validate through Parable.
  4. The managed builder verifies source commit and patch checksum before
     patch/test/build, never mutates an arbitrary existing checkout, and is
     covered by fake `git`/`go` integration tests.
  5. Unit/integration tests pin success plus every declared fake-success guard.
  6. Focused PR is merged and verified at `main`.
- **bound:** 2 correctly wired PROVE runs / 3 review rounds.
- **exit →:** O2.

### O2 — VENDOR AUTH AND PROXY LIFECYCLE

- **goal:** Make additive vendor authorization and foreground proxy startup
  discoverable and safe from the Parable CLI.
- **prompt:**
  > Add `parable auth add chatgpt|claude|xai`, `parable auth status`, and
  > `parable proxy start`. Delegate only to CLIProxyAPI's native login flags;
  > never parse, copy, log, or transform OAuth material. Status emits provider
  > presence booleans from user-only records. Claude remote guidance names its
  > callback port and requires the same live process. Start remains foreground
  > and exec-style so signals and logs belong to CLIProxyAPI.
- **accept:**
  1. Exact vendor-to-native-flag mapping is pinned with fake binaries.
  2. Auth is additive; output redaction tests prove no record content leaks.
  3. Missing binary/config and unsupported vendor fail before subprocess work.
  4. Proxy start preserves foreground lifecycle and configured binary/path.
  5. Focused PR is merged and verified at `main`.
- **bound:** 2 correctly wired PROVE runs / 3 review rounds.
- **exit →:** O3.

### O3 — CATALOG FINALIZE AND FIRST LAUNCH

- **goal:** Close onboarding by reconciling selected vendors with exact
  authenticated catalog ids before stock Claude Code starts.
- **prompt:**
  > Add a finalize/status path that queries only loopback `/v1/models`, maps
  > selected vendors to the exact proved ids, refuses absent ids without alias
  > substitution, writes or confirms the exact cast, and prints the next launch
  > command. Prove a complete setup→auth-wrapper→catalog→generated-agents→fake
  > Claude launch against a hermetic fake proxy and fake provider binary.
- **accept:**
  1. All selected exact ids produce the intended six-or-subset named agents.
  2. Missing ids fail closed before Claude; no display-name or regex fallback.
  3. The first-launch E2E proves local token handoff without exposing it.
  4. Existing unrelated project agents and config remain untouched.
  5. Focused PR is merged and verified at `main`.
- **bound:** 2 correctly wired PROVE runs / 3 review rounds.
- **exit →:** O4.

### O4 — OSS DELIVERY AND RELEASE HANDOFF

- **goal:** Publish one accurate first-run story and hand a verified package to
  the explicit npm release gate.
- **prompt:**
  > Run a non-destructive live smoke against the existing loopback catalog with
  > zero model turns and zero OAuth flows. Update README, setup guide, provider
  > reference, help output, and companion HTML from merged behavior only. Run
  > the full suite and package dry-run; record the source/npm version split and
  > exact publish command without executing it.
- **accept:**
  1. Hermetic and live read-only receipts agree on exact selected models.
  2. One canonical first-run command path is documented end to end.
  3. Complete tests, privacy scan, and package dry-run pass.
  4. Focused PR is merged and verified at `main`.
  5. npm publication is presented as an explicit human gate, never inferred.
- **bound:** 2 documentation/live proofs / 3 review rounds; release wait is
  unbounded.
- **exit →:** human release verdict or successor chain.

## Chain invariants

1. No loop advances without an EXIT mapping every criterion to evidence.
2. No provider turn or new OAuth flow is required for implementation proofs.
3. Existing configuration and auth records are never overwritten or copied.
4. All generated network listeners bind loopback; remote hosts fail validation.
5. Exact catalog ids are the only entitlement signal; aliases never substitute.
6. Instrument failures and evidence failures are disclosed separately.
7. Public artifacts contain no secrets, raw callbacks, account identifiers,
   provider payloads, private paths, or transcripts.
8. Kimi remains paused until the operator explicitly resumes it.
9. npm publication is a human-authorized external state change.
10. ZEN: declarative vendor/model data, ordinary subprocess delegation, and
    agent judgment; no broker, shared deployment, credential adapter, or model
    name regex router.
