# The Loop Chain — OSS subscription routing: Sol brain, Kimi cast (2026-07-18)

> This chain turns two user-visible claims into live, falsifiable proofs:
> (1) stock Claude Code launches with GPT-5.6 Sol as its session model, using the
> user's ChatGPT subscription; and (2) that Sol session launches Kimi K3 as a
> named Claude Code subagent, using the user's Kimi Code subscription.
> Each loop has a self-contained prompt, evidence-based exit criteria, and a
> hard bound. Raw prompts, transcripts, OAuth artifacts, and credentials never
> enter the repository. Nothing advances on vibes.
>
> **Pacing:** autonomous until a declared human credential/consent gate or
> `AT_BOUND`.

## Outcome contract

The release claim is intentionally narrow:

```text
Claude Code
  └── localhost-only CLIProxyAPI
      ├── ChatGPT subscription OAuth → gpt-5.6-sol (session/brain)
      └── Kimi Code subscription OAuth → kimi-k3 (named subagent)
```

### Required live proofs

| Proof | User-visible action | Non-spoofable evidence |
|---|---|---|
| P1 — Sol session | Launch a Claude Code session whose selected main model is `gpt-5.6-sol` and complete one benign tool-using turn. | Claude Code exits successfully plus a sanitized proxy receipt naming the OpenAI OAuth channel and requested model `gpt-5.6-sol`. |
| P2 — heterogeneous subagent | From a fresh Sol session, have the orchestrator invoke the named Kimi agent for one bounded task. | An isolated canary proxy instance yields separate sanitized receipts for parent `gpt-5.6-sol` and child `kimi-k3`, plus a synthetic artifact produced by the child and verified by the parent. |

Model self-identification in prose is not evidence. An `Agent` tool call without
an upstream `kimi-k3` request is not evidence. A direct API-key request is not a
subscription proof.

### Product surface targeted by this chain

Parable remains a thin OSS layer over two independently installed CLIs:

```toml
[claude]
base_url = "http://127.0.0.1:8317"
auth_token_env = "CLIPROXY_API_KEY"
brain_model = "gpt-5.6-sol"

[executors.kimi]
provider = "claude"
model = "kimi-k3"
tags = ["implementer", "agentic"]
use_for = "Subscription-backed coding subagent for bounded implementation work."
```

The exact field names may be improved during L2 if tests expose a simpler,
more general shape, but the semantics are fixed:

- no provider credential or OAuth token appears in TOML;
- the launcher sets the Claude-compatible localhost endpoint and main model;
- arbitrary subagent model IDs are materialized as named Claude agent files;
- `CLAUDE_CODE_SUBAGENT_MODEL` is absent for heterogeneous routing because it
  overrides every named agent's model;
- OAuth login and token refresh remain CLIProxyAPI's responsibility.

The intended commands are:

```text
npx @parcha/parable doctor
npx @parcha/parable agents sync
npx @parcha/parable claude [-- <ordinary Claude Code arguments>]
```

L2 may collapse `agents sync` into install/launch if that is simpler and remains
inspectable and idempotent.

## Explicitly out of scope

- LiteLLM, Parcha's `llm-broker`, any new broker, master keys, virtual keys, or
  shared hosted routing.
- Sharing one user's subscription, OAuth session, or quota with another user.
- Server-side account pools, multi-tenant auth, billing reconciliation, or a
  Parable cloud service.
- Direct provider API keys as a substitute for either acceptance proof.
- Reimplementing, vendoring, or scraping provider OAuth flows in Parable.
- Fable quota detection/fallback policy, Grok, Cursor, and other providers.
  Those are candidates for a successor chain only after P1 and P2 are proven.

Each OSS user runs a localhost proxy and connects their own subscriptions.

## Loop anatomy

| Field | Meaning |
|---|---|
| `goal` | One sentence describing the state change the loop produces. |
| `prompt` | A self-contained instruction block that a fresh session can execute using only this document and its named inputs. |
| `accept` | Evidence-based exit criteria naming safe, checkable artifacts. |
| `bound` | Maximum inner attempts before honest escalation. |
| `exit →` | The loop triggered after a verified exit. |

## The ribbon

Every loop executes the same inner sequence:

```text
RE-PLAN  Read this chain, the active loop, current HEAD, and relevant upstream pins.
BUILD    Make one bounded state change; keep unrelated work untouched.
PIN      Test the mechanism and its fake-success modes.
PROVE    Run the live or hermetic canary and publish only sanitized evidence.
MEASURE  Record the cumulative P1/P2 scoreboard; skip unrelated metrics explicitly.
REVIEW   Open one focused PR and resolve every finding.
MERGE    Merge only after the loop's criteria are green.
EXIT     Map every criterion to evidence in docs/evidence/<loop>/EXIT.md.
```

Runtime protocol:

- Pin the exact CLIProxyAPI release/commit and verify its published checksum
  before execution. The research pin when this chain was cut was
  `93d74a890a44802f656d7f39a573916b2611896e`; execution may select a newer
  released pin only with the reason recorded.
- Bind CLIProxyAPI to `127.0.0.1`; do not expose its management or inference
  ports remotely.
- Keep OAuth state in CLIProxyAPI's user auth directory with user-only
  permissions. Never copy it into Parable artifacts.
- Keep raw Claude Code and proxy logs in a gitignored local run directory.
  Public receipts may contain only versions, timestamps, provider/channel,
  requested model, HTTP outcome, tool-call counts, and artifact hashes.
- Give each live canary a dedicated loopback proxy process/port and admit only
  that canary's Claude Code process during the evidence window. This associates
  parent and child receipts without logging prompts or inventing a broker.
- Run the public unit/integration suite on the repository's pinned runtime.
- The live proof uses the installed public Claude Code binary. Source inspection
  is design evidence, not a substitute for runtime proof.

**Bound + escalation (every loop):** maximum 3 REVIEW→fix rounds and maximum
2 correctly wired failed PROVE runs. Harness/setup failures are diagnosed and
recorded separately; they do not consume a proof attempt. At the bound, write
`EXIT.md` with `status: AT_BOUND`, name the unmet criteria and cause, and stop.

## Task graph

```text
L0 CONTRACT
  → L1 SOL LIVE
    → L2 OSS SURFACE
      → L3 KIMI LIVE [human OAuth gate]
        → L4 CLEAN-ROOM
          → L5 RE-PLAN [human sign-off]
```

## The chain

### L0 — CONTRACT AND BASELINE

- **goal:** Pin the runtime, security boundary, current behavior, and evidence
  format before installing or changing anything.
- **prompt:**
  > Work in the Parable repository from a clean branch based on current `main`.
  > Record a sanitized baseline containing Parable HEAD, Claude Code version,
  > OS/architecture, and whether CLIProxyAPI is installed/running—never auth
  > paths or contents. Pin an upstream CLIProxyAPI release/commit and checksum.
  > Confirm its model catalog contains `gpt-5.6-sol` and `kimi-k3`, its OpenAI
  > and Kimi device/browser OAuth entry points exist, and its Claude-compatible
  > request path supports tools. Pin the Claude Code routing hazards with tests
  > or a sanitized source-inspection receipt: arbitrary custom-agent frontmatter
  > model IDs pass through, while `CLAUDE_CODE_SUBAGENT_MODEL` globally overrides
  > them. Define the content-free receipt schema used by P1 and P2. Run the
  > existing Parable suite without changing product behavior.
- **accept:**
  1. `docs/evidence/l0-contract/manifest.json` records only safe version/pin facts
     and contains no credential material or identifying local paths.
  2. `docs/evidence/l0-contract/contract.md` maps the four routing prerequisites
     (Sol catalog, Kimi catalog, OAuth channels, arbitrary named-agent model) to
     pinned source or executable checks.
  3. The receipt schema can distinguish parent and child model requests from one
     isolated canary instance without storing prompts or responses.
  4. Existing tests pass at the recorded Parable HEAD.
  5. The focused PR is merged and all criteria are verified at merged HEAD.
- **bound:** 2 PROVE runs / 3 review rounds.
- **exit →** L1.

### L1 — SOL SUBSCRIPTION LIVE CANARY (P1)

- **goal:** Prove stock Claude Code can complete a tool-using session on
  `gpt-5.6-sol` through a local ChatGPT-subscription OAuth connection.
- **prompt:**
  > Install the pinned upstream CLIProxyAPI binary using its published release
  > artifact and verified checksum; do not fork or patch it. Configure a
  > localhost-only listener and a local client token supplied through an
  > environment variable, not committed config. Connect the operator's ChatGPT
  > subscription using CLIProxyAPI's OpenAI/Codex OAuth flow; if browser consent
  > is needed, pause only for the operator to complete it—never request or handle
  > their password. Verify `gpt-5.6-sol` appears in the authenticated local model
  > catalog. Launch the installed Claude Code binary against the proxy with
  > `--model gpt-5.6-sol`, complete one bounded turn that uses a harmless local
  > tool and leaves a synthetic marker in a temporary directory, then verify the
  > marker independently. Preserve raw logs only in a gitignored local directory.
  > Emit the L0-defined sanitized receipt.
- **accept:**
  1. CLIProxyAPI is pinned, checksum-verified, bound only to loopback, and healthy.
  2. The authenticated local catalog contains exactly `gpt-5.6-sol` for the
     ChatGPT subscription channel.
  3. Claude Code exits successfully and the synthetic tool artifact passes an
     independent deterministic check.
  4. A sanitized receipt records a successful OpenAI OAuth-channel request for
     `gpt-5.6-sol`; no direct OpenAI API key was used.
  5. `docs/evidence/l1-sol-live/EXIT.md` marks P1 `PASS`, includes bound
     accounting, and maps every criterion to evidence at merged HEAD.
- **bound:** 2 correctly wired live canaries / 3 review rounds. OAuth consent wait
  is unbounded and does not consume an attempt.
- **exit →** L2.

### L2 — PORTABLE PARABLE LAUNCHER AND NAMED-AGENT SYNC

- **goal:** Make the proven manual wiring a small, portable, declarative Parable
  workflow that any OSS user can reproduce with their own local proxy.
- **prompt:**
  > Extend Parable's versioned TOML schema with the minimal Claude-session fields
  > needed to select a localhost Claude-compatible endpoint, client-token
  > environment variable, and brain model. Add a `parable claude` command that
  > validates config and credential presence, checks proxy health and selected
  > model availability, sets only the per-process Claude Code environment, and
  > forwards ordinary Claude arguments without mutating the user's global Claude
  > configuration. Add idempotent named-agent synchronization for configured
  > `provider = "claude"` executors whose model is not a built-in Claude alias;
  > materialize Kimi as a clearly namespaced agent with `model: kimi-k3`.
  > Explicitly remove or reject inherited `CLAUDE_CODE_SUBAGENT_MODEL` whenever
  > heterogeneous agents are enabled. Do not install, supervise, fork, or
  > reimplement CLIProxyAPI in this loop. Add fake-binary and fake-local-proxy
  > integration tests covering argv/env construction, forwarding, model health,
  > permissions, idempotence, stale-agent cleanup, and secret-free artifacts.
- **accept:**
  1. A minimal TOML config selects Sol as the brain and Kimi as an executor
     without containing OAuth/provider credentials.
  2. Hermetic integration tests prove `parable claude` selects
     `gpt-5.6-sol`, forwards user arguments, and does not alter global Claude
     configuration.
  3. Agent-sync tests prove the generated named Kimi agent carries
     `model: kimi-k3`, is namespaced/idempotent, and preserves unrelated user
     agents.
  4. A fake-success test starts with
     `CLAUDE_CODE_SUBAGENT_MODEL=gpt-5.6-sol` and proves the launched
     heterogeneous session cannot inherit the global override.
  5. Logs, generated receipts, and committed fixtures contain no tokens; local
     secret-bearing files are mode `0600` where applicable.
  6. Unit and integration suites pass; the focused PR is merged and verified at
     merged HEAD.
- **bound:** 2 PROVE runs / 3 review rounds.
- **exit →** L3.

### L3 — SOL ORCHESTRATES KIMI SUBAGENT (P2, HUMAN OAUTH GATE)

- **goal:** Prove a Parable-launched Sol session invokes a real Kimi K3 named
  subagent through the operator's Kimi Code subscription.
- **prompt:**
  > Pause before authentication and ask the operator to complete CLIProxyAPI's
  > Kimi device/browser OAuth flow. Do not ask for, receive, display, or persist
  > their password, refresh token, access token, or auth files. Once the operator
  > confirms completion, verify `kimi-k3` appears in the authenticated local
  > catalog. From a clean temporary git repository, run the L2 Parable workflow:
  > start a dedicated loopback proxy process/port for the evidence window,
  > synchronize agents, launch only one Claude Code process with Sol as the
  > brain, and give Sol a bounded task that explicitly requires the namespaced
  > Kimi agent to create a deterministic synthetic artifact, after which Sol
  > verifies it. Keep
  > `CLAUDE_CODE_SUBAGENT_MODEL` unset. Prove routing using sanitized proxy
  > receipts, not model prose or merely the presence of an Agent tool call.
- **accept:**
  1. The local authenticated catalog exposes `kimi-k3` through the Kimi OAuth
     channel; no Kimi API key was used.
  2. The parent session receipt records `gpt-5.6-sol`, and a distinct child
     receipt from the same canary records `kimi-k3`.
  3. Claude Code's trace records invocation of the namespaced Kimi agent, while
     the proxy receipt proves that invocation reached Kimi rather than falling
     back to the parent model.
  4. The Kimi child creates the synthetic artifact and the Sol parent verifies
     its deterministic content/hash; the process exits successfully.
  5. No credential, prompt, response, transcript, identifying local path, or raw
     proxy log enters git.
  6. `docs/evidence/l3-kimi-live/EXIT.md` marks P2 `PASS`, maps every criterion to
     evidence, and the focused evidence/docs PR is merged at verified HEAD.
- **bound:** 2 correctly wired live canaries / 3 review rounds. The Kimi OAuth
  wait is an explicit unbounded human gate and consumes no attempt.
- **exit →** L4.

### L4 — CLEAN-ROOM OSS REPRODUCIBILITY

- **goal:** Demonstrate that P1/P2 depend only on public artifacts, portable
  configuration, and each user's own subscriptions.
- **prompt:**
  > Test installation and setup from a temporary clean HOME with no Parcha
  > services, LiteLLM, broker configuration, internal endpoints, or pre-existing
  > Claude agent files. Use fake OAuth/model fixtures for automated tests; never
  > copy live OAuth state. Document the public path: install Claude Code, install
  > pinned CLIProxyAPI, perform upstream OpenAI and Kimi OAuth login, create the
  > small Parable TOML config, run doctor/agent sync, and launch. Add a
  > troubleshooting table for missing entitlement, stale model catalog,
  > localhost health, inherited global subagent override, and OAuth refresh.
  > State clearly that subscriptions and quotas are user-bound and never shared.
  > Re-run the full suite plus a sanitized live smoke using the already connected
  > accounts, without publishing raw logs.
- **accept:**
  1. A clean-HOME integration test passes using only public repository artifacts
     and synthetic local fixtures.
  2. OSS documentation reaches `parable claude` from zero setup without any
     broker, LiteLLM, internal repository, direct provider API key, or hidden
     manual file edit.
  3. Doctor failures are actionable for missing binary, unhealthy proxy, missing
     env token, and absent `gpt-5.6-sol`/`kimi-k3` entitlement.
  4. The cumulative scoreboard remains P1 `PASS`, P2 `PASS`; any fresh live smoke
     is labeled `n=1` and is not presented as a reliability benchmark.
  5. Full tests pass; the documentation/install PR is merged and verified at
     merged HEAD.
- **bound:** 2 PROVE runs / 3 review rounds.
- **exit →** L5.

### L5 — RE-PLAN GATE

- **goal:** Produce an evidence-backed release verdict and let the user choose
  whether the next chain tackles Fable→Sol quota fallback, Grok, packaging, or
  hardening.
- **prompt:**
  > Read every prior `EXIT.md`, verify P1 and P2 again at current merged HEAD,
  > audit the public setup for hidden infrastructure assumptions and secret
  > leakage, and write a concise verdict. Include the cumulative scoreboard,
  > known limitations, upstream pins, and exact operator steps. Draft—not
  > execute—a successor chain from the remaining user priority: automatic
  > Fable-quota fallback to Sol, Grok subscription agents, binary lifecycle
  > packaging, or broader platform coverage. Pause for user sign-off.
- **accept:**
  1. The verdict maps both release claims to merged code and sanitized live
     evidence.
  2. Security audit finds no committed credential material or shared-subscription
     behavior.
  3. The next-chain draft is bounded by the evidence rather than assumed scope.
  4. User sign-off is received.
- **bound:** 2 verdict drafts; the user sign-off wait is unbounded by design.
- **exit →** a successor chain; this document remains append-forward.

## Running scoreboard

Every `EXIT.md` repeats this table and updates only from checkable evidence:

| Loop | P1: Sol session | P2: Sol → Kimi subagent | OSS clean-room |
|---|---:|---:|---:|
| L0 | NOT RUN | NOT RUN | NOT RUN |

## Chain invariants

1. No implementation begins before this chain and its task graph exist.
2. No loop advances without an `EXIT.md` mapping every acceptance criterion to
   evidence verified at current merged HEAD.
3. The scoreboard is cumulative; a regression reopens the affected proof.
4. `AT_BOUND` is an honest stop, never a synonym for done.
5. Harness/setup failures are separated from correctly wired evidence failures.
6. Human OAuth/consent gates wait for the operator and never time out into fake
   approval.
7. Apply the ZEN check on every BUILD: simple, general, prompt/agentic-oriented,
   beautiful, and dope.
8. Prefer model judgment for routing descriptions and structured code for
   security, configuration, process, and artifact invariants.
9. The chain is append-forward; the next body of work gets a successor document.
10. Public evidence is content-free or synthetic. Raw transcripts, conversations,
    prompts, responses, tool traces, OAuth files, credentials, private exports,
    identifying local paths, and infrastructure secrets never enter the
    repository.
