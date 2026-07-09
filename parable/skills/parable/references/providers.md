# Provider recipes

## Why "responses" only

codex drives custom providers exclusively over the OpenAI **Responses API**
(`wire_api = "responses"`); chat-completions support was removed from codex. A provider works
with parable's codex executors if and only if it serves an OpenAI-compatible `/responses`
endpoint. parable injects providers per-invocation with `-c model_providers.*` overrides —
it never reads or writes `~/.codex/config.toml` provider entries, so your personal codex
setup is untouched.

Check credentials and reachability any time with `parable-config.sh` (shows per-executor
key status).

## Fireworks

```toml
[providers.fireworks]
type = "codex"
base_url = "https://api.fireworks.ai/inference/v1"
env_key = "FIREWORKS_API_KEY"
wire_api = "responses"
```

Model ids are the full account path, e.g. `accounts/fireworks/models/kimi-k2p7-code`.
The Responses endpoint is beta: most coding models run codex's multi-turn tool loop cleanly,
but some model chat-templates fail on multi-turn tool replay (symptom in the run log:
`jinja template rendering failed. Message has tool role, but there was no previous assistant
message with a tool call`) and some return persistent server errors. When a model misbehaves
on one provider, route it through another (OpenRouter, or a LiteLLM bridge) rather than
retrying — the failure is server-side and deterministic. A completed working tree with a
failed final stream is still verifiable: run `parable-verify.sh` and judge the evidence.

## OpenRouter

```toml
[providers.openrouter]
type = "codex"
base_url = "https://openrouter.ai/api/v1"
env_key = "OPENROUTER_API_KEY"
wire_api = "responses"
```

Model ids are OpenRouter's `vendor/model` form, e.g. `minimax/minimax-m3`,
`moonshotai/kimi-k2.7`. The Responses endpoint is beta and stateless; usage responses
include a `cost` field, which run summaries surface when present.

## pi (chat-completions without a bridge)

The [pi coding agent](https://github.com/earendil-works/pi) is a second executor harness.
Where codex only drives Responses-API providers, pi speaks **plain chat-completions** (plus
`anthropic-messages` and `openai-responses`) to any base URL — so chat-only providers work
directly, no proxy in the middle:

```toml
[providers.fireworks-pi]
type = "pi"
base_url = "https://api.fireworks.ai/inference/v1"
env_key = "FIREWORKS_API_KEY"
api = "openai-completions"

[executors.minimax-fw]
provider = "fireworks-pi"
model = "accounts/fireworks/models/minimax-m3"
effort = "medium"
cost = { in = 0.30, out = 1.20, cache_in = 0.06 }
context_ktok = 512
```

This is also the recovery path when a provider's Responses endpoint misbehaves for a model:
the same model over chat-completions often runs the full multi-turn tool loop cleanly.

Mechanics and requirements:

- **node ≥ 22.** pi currently requires node 22+ (older node hits HTTP-client
  incompatibilities); `npx @parcha/parable doctor` checks your version. Put a node 22+ bin dir
  first on PATH (e.g. `nvm use 22`), then `npm i -g @earendil-works/pi-coding-agent`.
  `doctor` checks both.
- **Hermetic per-run config.** parable generates `<run_dir>/pi-agent/models.json` and points
  pi at it via `PI_CODING_AGENT_DIR`; your `~/.pi` is never written to (the hermetic dir's
  `bin/` symlinks `~/.pi/agent/bin` read-only when present, for fd/rg reuse), the API key is
  referenced as `$ENV_VAR` (resolved at request time, never on disk or in argv), and runs get
  `PI_OFFLINE=1` plus `--no-extensions --no-skills --no-prompt-templates --no-approve` so
  nothing personal leaks in.
- **Sessions live in the run dir** (`sessions/`), so `parable-resume.sh` continues the exact
  session by id, and the session file doubles as a transcript backup.
- **Thinking:** `effort` maps to pi's `--thinking` (`off` is legal for pi executors). Models
  with baked-in reasoning may emit thinking regardless; that's the model, not a bug.

## LiteLLM proxy (bridge for chat-only providers)

Any provider that only speaks chat-completions can still back a codex executor through a
LiteLLM proxy, which serves `/v1/responses` in front of any configured model:

```toml
[providers.litellm]
type = "codex"
base_url = "http://localhost:4000/v1"   # your proxy
env_key = "LITELLM_MASTER_KEY"
wire_api = "responses"
```

Model ids are whatever aliases your proxy's `model_list` defines. If the only reason you'd
run the proxy is chat-completions translation, a `type = "pi"` provider does it without the
extra service.

## OpenAI (codex-native)

```toml
[providers.openai]
type = "codex-native"
```

Uses codex's own auth (ChatGPT login or `OPENAI_API_KEY`) and model catalog — no overrides
injected beyond `model` and effort. This is the zero-risk path: codex's home models run its
harness exactly as designed.

## Claude subagents

```toml
[providers.claude]
type = "subagent"
```

Executors on this provider are dispatched with the Agent tool inside the orchestrating
session — no API keys, no codex. `parable.py run` refuses them by design; the brain owns
subagent dispatch directly.

## Driving codex directly (beyond parable.py)

The dispatcher covers the common path; codex itself offers more when the brain needs it:

- `codex fork <session>` — branch an existing session to try alternative fixes in parallel
  without losing the original context.
- `codex apply` — apply the latest diff a codex session produced onto the working tree.
- `codex mcp-server` — run codex as an MCP server and call it as tools with session
  continuity; an alternative integration to subprocess dispatch for harnesses that prefer it.
- Direct `codex exec` calls: stdin is always read and concatenated with the prompt — if stdin
  is open but silent the process hangs forever with zero output. Close it (`</dev/null`) when
  no plan is piped. stderr carries the thinking stream; filter or capture it deliberately.
- Timeouts scale with effort: reasoning at `low` typically finishes in a couple of minutes;
  `xhigh` can legitimately run 20+. Set `max_minutes` per executor accordingly rather than
  one global number.

## Operational notes

- **Effort:** the mandatory-set rule and the per-dispatch `--effort` override live in
  `references/config.md`'s executor table.
- **Expect harness overhead.** A codex run carries its system prompt, skills, and hook
  context — on the order of 15–20K input tokens before the plan. Cache discounts absorb
  most of it on resumed sessions; budget for it on one-shot runs of cheap models.
- **Model metadata warnings** (`Model metadata for '…' not found`) are benign for custom
  providers; set `extra_config` overrides (e.g. `model_context_window`) if a model needs them.
- **`codex exec review`** runs in a read-only sandbox that requires working user namespaces
  (bubblewrap). parable's `review` subcommand instead pipes the diff into a normal executor
  run — identical coverage, no sandbox dependency.
