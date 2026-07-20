# The Loop Chain — Sol subscription-subagent permutations (2026-07-20)

> Successor to the completed Grok 4.5 chain. Prove one stock Claude Code
> harness with exact `gpt-5.6-sol` as parent across all five effort settings,
> calling every currently intended subscription child lane: Terra, Luna,
> Grok, Sonnet, Opus, and Haiku. Kimi remains paused.
>
> **Pacing:** autonomous through ChatGPT-backed rows, a bounded Anthropic OAuth
> human gate, then autonomous through the Claude-backed rows and OSS delivery.

## Matrix contract

| Axis | Values |
|---|---|
| Sol parent effort | `low`, `medium`, `high`, `xhigh`, `max` |
| Exact/custom child | `gpt-5.6-terra`, `gpt-5.6-luna`, `grok-4.5` |
| Claude child lane | Sonnet, Opus, Haiku; exact full ids pinned from the authenticated catalog before turns |
| Cell proof | exact parent + effort, exact child + observed effort, child Bash artifact, parent Agent/Bash consumption, subscription OAuth route |

The complete matrix has 30 cells. The five Grok cells are already merged in
X3 and are retained rather than spent again. This successor proves the
remaining 25: 10 ChatGPT-backed Terra/Luna cells and 15 Anthropic-backed
Sonnet/Opus/Haiku cells.

Public evidence contains only model ids, effort mappings, status codes,
counts, tool-name booleans, deterministic hashes, entitlement outcomes, and
runtime pins. Raw prompts, responses, proxy logs, OAuth material, account
identifiers, callback URLs, and private paths never enter Git.

**Bound + escalation:** at most two correctly wired attempts per cell and
three review/fix rounds per loop. A human OAuth wait and a pre-request
instrumentation correction do not consume a model-evidence attempt. At the
bound, write an `AT_BOUND` EXIT naming the unmet criteria.

## The ribbon

```text
RE-PLAN → BUILD → PIN → PROVE → MEASURE → REVIEW → MERGE → EXIT
```

## The chain

### Y0 — MATRIX AND AUTH INVENTORY

- **goal:** Pin the complete matrix and prove which rows can run without
  another human action.
- **prompt:**
  > Record exact matrix axes and merged Grok coverage. Inspect only redacted
  > provider/auth counts plus native Claude login status. Do not copy tokens or
  > spend a model turn. Mark ChatGPT/xAI proxy OAuth available, native Claude
  > subscription available, and proxy Claude OAuth absent if that is the
  > observed state.
- **accept:**
  1. Matrix is exactly 30 cells with five merged Grok cells and 25 pending.
  2. Current auth inventory contains no credential values or account ids.
  3. The next autonomous row and later human gate are explicit.
  4. Y0 receipt and EXIT are merged.
- **bound:** two inventory probes / three review rounds.
- **exit →** Y1.

### Y1 — SOL TO TERRA AND LUNA

- **goal:** Prove exact named Terra and Luna children at every Sol parent
  effort through ChatGPT subscription OAuth.
- **prompt:**
  > In one hermetic project, generate exact `parable-terra` and
  > `parable-luna`. Launch ten fresh non-persistent Sol sessions: each child at
  > every parent effort. Explicitly invoke the named child for a bounded Bash
  > artifact, then have Sol verify and consume it. Attribute parent/child
  > model and effort on the wire; never infer inheritance from the CLI flag.
- **accept:**
  1. Ten of ten successful cells use exact Sol parent and exact named child.
  2. Every child creates an exact artifact and every parent consumes it.
  3. Child effort behavior is measured for all ten cells.
  4. Every request uses ChatGPT OAuth with direct provider keys unset.
  5. Y1 receipt and EXIT are merged.
- **bound:** two correctly wired attempts per cell / three review rounds.
- **exit →** Y2.

### Y2 — CLAUDE OAUTH HUMAN GATE

- **goal:** Add the user's Claude subscription OAuth to CLIProxyAPI without an
  API key or credential copy.
- **prompt:**
  > Confirm native Claude Code is subscription-authenticated, then run
  > CLIProxyAPI's standard `--claude-login` PKCE flow against the same
  > user-only auth directory. Show the operator only the authorization
  > instruction needed at that moment. After callback, verify the saved record
  > through a redacting filter that emits provider, auth kind, token-presence
  > booleans, expiry, and mode.
- **accept:**
  1. Operator completes the standard Claude OAuth callback.
  2. Exactly one proxy record reports `type=claude`, `auth_kind=oauth`, token
     presence, expiry, and mode 0600.
  3. No Anthropic API key or copied native credential is used.
  4. Y2 receipt and EXIT are merged.
- **bound:** two generated PKCE flows; human wait is unbounded.
- **exit →** Y3.

### Y3 — AUTHENTICATED CLAUDE CATALOG

- **goal:** Resolve the exact full Sonnet, Opus, and Haiku ids available to the
  user's Claude subscription.
- **prompt:**
  > Query the authenticated local `/v1/models` catalog without a model turn.
  > Record only exact Claude entries and ownership. Resolve one full id for
  > each logical lane Sonnet, Opus, and Haiku; do not rely on display aliases.
- **accept:**
  1. One entitled exact full id is pinned for each of Sonnet, Opus, and Haiku,
     or an unavailable lane is explicitly rejected before turns.
  2. Existing Sol/Terra/Luna/Grok entries remain present.
  3. Direct provider API-key variables are unset.
  4. Y3 receipt and EXIT are merged.
- **bound:** two catalog probes / three review rounds.
- **exit →** Y4.

### Y4 — SOL TO SONNET, OPUS, AND HAIKU

- **goal:** Complete the remaining 15 Sol-parent matrix cells through Claude
  subscription OAuth.
- **prompt:**
  > Launch five fresh Sol sessions per pinned Claude child id. In each, use
  > the Agent tool with that exact child model for one bounded Bash artifact,
  > then have Sol verify and consume it. Attribute model, effort, tool names,
  > status, and provider route from private wire evidence.
- **accept:**
  1. Every entitled lane passes all five parent effort cells with exact model
     attribution; unavailable catalog lanes remain explicit, not substituted.
  2. Every successful child creates the exact artifact and the parent consumes
     it.
  3. Child effort behavior is measured, including any provider normalization.
  4. Sol uses ChatGPT OAuth and Claude children use Anthropic OAuth; direct
     provider keys are unset.
  5. Y4 receipt and EXIT are merged.
- **bound:** two correctly wired attempts per cell / three review rounds.
- **exit →** Y5.

### Y5 — UNIFIED OSS VERDICT

- **goal:** Publish the complete subscription-subagent matrix and any honest
  entitlement gaps.
- **prompt:**
  > Update the guide, provider reference, and example only with merged live
  > behavior. Summarize all 30 cells, effort inheritance/normalization, tool
  > completion, billing route, retries, and unavailable lanes. Run the full
  > suite and package dry-run.
- **accept:**
  1. Every matrix claim maps to a merged receipt.
  2. Exact child selection and billing routes are unambiguous.
  3. No credential, OAuth state, raw trace, or private path is committed.
  4. Complete tests and package dry-run pass.
  5. Focused PR is merged and verified at `main`.
- **bound:** two documentation proofs / three review rounds.
- **exit →** final human verdict.

## Chain invariants

1. No Claude proxy turn precedes Y2 authorization and Y3 entitlement.
2. No loop advances without an EXIT mapping every criterion to evidence.
3. Merged Grok cells remain cumulative evidence; regressions reopen them.
4. `AT_BOUND` is an honest stop, never completion.
5. Instrument failures and evidence failures are separately disclosed.
6. Human OAuth waits unbounded; model evidence runs do not.
7. ZEN applies at every BUILD: exact declarative model selection and agent
   judgment, no broker, credential adapter, model-name regex router, or
   provider fallback.
8. Kimi remains paused until the operator explicitly resumes it.
