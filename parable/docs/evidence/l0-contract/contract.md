# L0 routing contract

This contract pins the minimum facts needed before any live subscription
canary. It is source evidence, not a claim that the end-to-end route has
already passed.

## Upstream pin

CLIProxyAPI is pinned to release `v7.2.88`, commit
`93d74a890a44802f656d7f39a573916b2611896e`.

| Prerequisite | Pinned evidence |
|---|---|
| Sol exists | The pinned model registry contains `gpt-5.6-sol`: [`models.json`](https://github.com/router-for-me/CLIProxyAPI/blob/93d74a890a44802f656d7f39a573916b2611896e/internal/registry/models/models.json#L1581). |
| Kimi K3 exists | The same registry contains `kimi-k3`: [`models.json`](https://github.com/router-for-me/CLIProxyAPI/blob/93d74a890a44802f656d7f39a573916b2611896e/internal/registry/models/models.json#L2175). |
| ChatGPT subscription OAuth exists | `DoCodexLogin` delegates provider `codex` to the shared OAuth manager and persists its returned auth record: [`openai_login.go`](https://github.com/router-for-me/CLIProxyAPI/blob/93d74a890a44802f656d7f39a573916b2611896e/internal/cmd/openai_login.go#L29). The server exposes it as `--codex-login`. |
| Kimi subscription OAuth exists | `DoKimiLogin` delegates provider `kimi` to the shared OAuth manager: [`kimi_login.go`](https://github.com/router-for-me/CLIProxyAPI/blob/93d74a890a44802f656d7f39a573916b2611896e/internal/cmd/kimi_login.go#L12). The server exposes it as `--kimi-login`. |
| Claude requests can reach Codex | The Codex executor translates its source format into the Codex/Responses format; the pinned upstream test exercises `SourceFormat: "claude"`: [`codex_executor_stream_output_test.go`](https://github.com/router-for-me/CLIProxyAPI/blob/93d74a890a44802f656d7f39a573916b2611896e/internal/runtime/executor/codex_executor_stream_output_test.go#L102). |
| Claude requests can reach Kimi | `KimiExecutor` embeds `ClaudeExecutor` and explicitly dispatches Claude-format streaming and non-streaming requests through it: [`kimi_executor.go`](https://github.com/router-for-me/CLIProxyAPI/blob/93d74a890a44802f656d7f39a573916b2611896e/internal/runtime/executor/kimi_executor.go#L30). |

## Claude Code named-agent routing

A source snapshot inspected alongside Claude Code 2.1.215 establishes the
mechanism that L2 must pin with a black-box integration test:

| Logical module | SHA-256 | Observed contract |
|---|---|---|
| `src/utils/model/agent.ts` | `f5f1551da0cdcc261b28b3df8fecc81af05c1213e4bd99f2a5d7fb7541bad89b` | `CLAUDE_CODE_SUBAGENT_MODEL` is checked first and therefore overrides all per-agent selection. |
| `src/tools/AgentTool/AgentTool.tsx` | `322e8d097352d61d42bc2aa30b7e2509c3a9782d8d77434f960f7f650143401a` | The direct Agent-tool `model` field is restricted to `sonnet`, `opus`, and `haiku`; arbitrary third-party selection must use a named agent. |
| `src/tools/AgentTool/loadAgentsDir.ts` | `06cb0d5a3e05b5b59d0e48aef0e0c3af24f9677e642f3243a619b66459ab4eab` | A named agent's non-empty `model:` frontmatter is preserved as a string. |
| `src/utils/model/model.ts` | `e12212768bd8d00e7ba3f89a45382c17bec2be27047077d32f120c348e45f00c` | Unknown model strings pass through after built-in alias resolution. |

The installed Claude Code 2.1.215 binary has SHA-256
`c1efffaaf370aa187cb6a09dd93d4e511c646899b0078476f83791b664bde7fe`
and contains the `CLAUDE_CODE_SUBAGENT_MODEL` configuration symbol. L2 must
still prove the behavior against the installed binary; source inspection alone
does not satisfy P2.

## Receipt boundary

`receipt.schema.json` deliberately permits only:

- a random canary identifier and timestamps;
- pinned runtime versions;
- parent/child role, provider channel, OAuth auth kind, requested model,
  HTTP outcome, completion state, and tool-call count;
- a synthetic artifact hash and deterministic verdict;
- explicit negative attestations for direct provider API keys, raw content, and
  committed credentials.

Each live proof gets its own loopback-only CLIProxyAPI process and admits one
Claude Code canary during the evidence window. This associates its model
requests without storing prompts, responses, transcripts, OAuth files, or
identifying local paths.
