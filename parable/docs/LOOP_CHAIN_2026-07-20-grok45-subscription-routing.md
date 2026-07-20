# The Loop Chain — Grok 4.5 subscription routing (2026-07-20)

> Successor to the completed CLIProxyAPI GPT effort-fidelity chain. Prove
> Grok 4.5 through per-user xAI OAuth in the same stock Claude Code harness,
> first as the session model and then as an exact named subagent of Sol. Each
> loop has a self-contained prompt, safe evidence, and a hard bound. Raw
> prompts, responses, proxy logs, OAuth material, and private paths never enter
> the repository.
>
> **Pacing:** autonomous, with an immediate xAI device-authorization human gate
> and a final human re-plan gate.

## Loop anatomy

| Field | Meaning |
|---|---|
| `goal` | One sentence describing the state change. |
| `prompt` | Self-contained execution instructions for a fresh session. |
| `accept` | Evidence-based exit criteria naming safe, checkable artifacts. |
| `bound` | Maximum evidence and review iterations before honest escalation. |
| `exit →` | The next loop triggered by a valid exit. |

## The ribbon

Every loop follows:

```text
RE-PLAN → BUILD → PIN → PROVE → MEASURE → REVIEW → MERGE → EXIT
```

The proof uses the already verified CLIProxyAPI source patch based on
`v7.2.88` / commit `93d74a890a44802f656d7f39a573916b2611896e`,
stock Claude Code `2.1.215`, exact model ids `grok-4.5` and
`gpt-5.6-sol`, and one dedicated loopback-only proxy. Provider API-key
variables remain unset. xAI OAuth is the only new credential and stays in
CLIProxyAPI's user-only auth directory.

Public evidence contains only runtime pins, provider/model/effort fields,
status codes, counts, booleans, deterministic hashes, and entitlement
outcomes.

**Bound + escalation:** at most two correctly wired failed PROVE runs per
cell and three REVIEW→fix rounds per loop. Device-code expiry is a human-gate
wait, not an evidence failure. At the bound, write an `AT_BOUND` EXIT naming
the unmet criteria and stop.

## Order rationale

Authorization and catalog entitlement precede model turns. Direct Grok
transport precedes named-subagent orchestration so provider failures cannot be
misdiagnosed as Claude agent-routing failures. The final matrix explicitly
tests Claude Code's full effort enum even though Grok officially advertises
only `low|medium|high`; unsupported settings must clamp or reject honestly,
never become fake passes.

## The chain

### X0 — xAI OAUTH HUMAN GATE

- **goal:** Store one valid per-user xAI OAuth record without an API key.
- **prompt:**
  > Start CLIProxyAPI's xAI device flow with the patched local binary and the
  > existing mode-0600 loopback configuration. Show the operator only the
  > verification URL, user code, and expiry. Wait for authorization. Inspect
  > the saved record only through a redacting filter that reports provider,
  > auth kind, token-presence booleans, expiry, and file mode.
- **accept:**
  1. Operator completes xAI device authorization.
  2. Exactly one new record reports `type=xai`, `auth_kind=oauth`, non-empty
     access and refresh token booleans, and user-only file permissions.
  3. No xAI API key is set, stored in config, or committed.
  4. Sanitized X0 receipt and EXIT are merged and verified at `main`.
- **bound:** two generated device codes; authorization wait is unbounded.
- **exit →** X1.

### X1 — AUTHENTICATED CATALOG

- **goal:** Prove this xAI account exposes exact `grok-4.5` through the local
  Claude-compatible proxy.
- **prompt:**
  > Start one dedicated patched proxy on loopback. Query `/v1/models` with the
  > local proxy client token. Record only total count and exact entries for
  > `grok-4.5`, `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna`, including
  > `owned_by`. Confirm the xAI OAuth route and no direct provider API keys.
- **accept:**
  1. Catalog contains exactly one `grok-4.5` owned by `xai`.
  2. Existing Sol/Terra/Luna entries remain present.
  3. Proxy is loopback-only and direct provider API keys are unset.
  4. Sanitized X1 receipt and EXIT are merged and verified at `main`.
- **bound:** two correctly wired catalog probes / three review rounds.
- **exit →** X2.

### X2 — GROK MAIN-MODEL PERMUTATIONS

- **goal:** Measure every Claude Code effort setting and real tool use with
  Grok 4.5 as the session model.
- **prompt:**
  > Run five fresh named, non-persistent stock Claude Code text cells on exact
  > model `grok-4.5`: `low|medium|high|xhigh|max`. Independently verify a
  > synthetic marker and parse only inbound effort, translated upstream
  > effort, statuses, completion, model, and OAuth-route booleans. Then run one
  > deterministic Bash tool canary for each officially supported distinct
  > effort `low|medium|high`. Record unsupported xhigh/max clamps or rejections
  > exactly; do not infer fidelity from CLI flags.
- **accept:**
  1. All five text cells have requested, inbound, and upstream effort or an
     explicit provider rejection.
  2. `low|medium|high` are exact end to end.
  3. Three of three supported-effort tool canaries complete deterministically.
  4. `xhigh|max` behavior is classified as exact, clamp, or rejection.
  5. Every request uses xAI subscription OAuth with direct keys unset.
  6. Sanitized X2 receipt and EXIT are merged and verified at `main`.
- **bound:** two correctly wired attempts per cell / three review rounds.
- **exit →** X3.

### X3 — SOL PARENT TO NAMED GROK SUBAGENT

- **goal:** Prove Sol can select exact `grok-4.5` as a native named Claude Code
  subagent and receive a deterministic tool-produced result.
- **prompt:**
  > Add a declarative `grok` Claude executor with exact model `grok-4.5` in a
  > hermetic test configuration and sync `parable-grok`. Launch five fresh Sol
  > parent sessions, one at each parent effort
  > `low|medium|high|xhigh|max`. In each session explicitly invoke the named
  > Grok agent for one bounded deterministic tool task. Attribute parent and
  > child proxy requests by model and invocation window. Record the child
  > effort actually observed; do not assume inheritance.
- **accept:**
  1. All five parent sessions route as exact `gpt-5.6-sol`.
  2. All five child invocations route as exact `grok-4.5`, never the parent
     model or a Claude alias.
  3. Each child completes the deterministic tool artifact and the parent
     consumes it successfully.
  4. Child effort behavior across all five parent efforts is measured.
  5. Sol uses ChatGPT OAuth, Grok uses xAI OAuth, and direct keys are unset.
  6. Sanitized X3 receipt and EXIT are merged and verified at `main`.
- **bound:** two correctly wired attempts per permutation / three review
  rounds.
- **exit →** X4.

### X4 — OSS GROK DELIVERY

- **goal:** Add the proved Grok subscription route to the public localhost
  recipe without weakening the GPT path.
- **prompt:**
  > Update the five-minute guide and provider recipe with `--xai-login`, exact
  > Grok model/effort support, a named-agent TOML example, and the observed
  > unsupported-effort behavior. Keep Cursor's Grok route clearly separate:
  > Cursor subscription is a Parable CLI executor, not a Claude Code-native
  > subagent. Run the complete Parable suite and package dry-run.
- **accept:**
  1. OSS guide contains an exact xAI OAuth and named-Grok path.
  2. Subscription routes and effort limitations match live receipts.
  3. No credential, OAuth state, raw trace, or private path is committed.
  4. Full tests and package dry-run pass.
  5. Focused PR is merged and verified at `main`.
- **bound:** two documentation proofs / three review rounds.
- **exit →** X5.

### X5 — RE-PLAN GATE

- **goal:** Present the exact Grok verdict and choose the next subscription
  route.
- **prompt:**
  > Read X0–X4 EXITs and report authentication, catalog, main-model effort,
  > tool, named-subagent, and billing-route results with exact counts. Keep Kimi
  > paused. Recommend reliability repetitions, Cursor-Grok parity, or another
  > named GPT subagent proof, then wait for the user.
- **accept:**
  1. Every verdict claim maps to merged safe evidence.
  2. xAI and Cursor subscription routes remain clearly distinguished.
  3. Kimi remains `PAUSED`.
  4. User chooses the successor.
- **bound:** two verdict drafts; user wait is unbounded.
- **exit →** a new successor chain.

## Chain invariants

1. No Grok model turn precedes X0 authorization and X1 entitlement.
2. No loop advances without an EXIT mapping every criterion to evidence.
3. Deltas are cumulative; regression reopens the affected cell.
4. `AT_BOUND` is an honest stop, never completion.
5. Instrument failures are separate from evidence failures.
6. Human device authorization and final sign-off wait unbounded.
7. ZEN applies at every BUILD: declarative exact model ids and protocol
   evidence, no model-name shim or broker.
8. This chain is append-forward; its final gate creates a successor document.
9. Public evidence is synthetic or content-free. Raw logs, prompts, responses,
   OAuth material, tokens, account identifiers, and private paths stay out of
   Git.
10. Kimi remains paused until the operator explicitly resumes it.
