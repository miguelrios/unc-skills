# GPT subscription models in stock Claude Code

This is the reproducible OSS path for running stock Claude Code with
`gpt-5.6-sol`, `gpt-5.6-terra`, or `gpt-5.6-luna` through a local
CLIProxyAPI process and the user's own ChatGPT subscription OAuth.

No broker or shared deployment is involved:

```text
Claude Code → loopback CLIProxyAPI → ChatGPT subscription OAuth
```

## Support state

Released CLIProxyAPI `v7.2.88` can transport all three models, but it maps
every Claude Code effort setting to upstream `medium`. The patch shipped in
this repository fixes that protocol translation and has been live-proved for
all 15 combinations of:

- model: `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`
- effort: `low`, `medium`, `high`, `xhigh`, `max`

The result was 15/15 exact effort pass-through and 3/3 tool canaries. The
patch is **not** an upstream CLIProxyAPI release. It is pinned to:

| Item | Pin |
|---|---|
| CLIProxyAPI base | `v7.2.88` / `93d74a890a44802f656d7f39a573916b2611896e` |
| Official source archive SHA-256 | `47c91832a4f09501ed0638191b18d74ffc8eef2ec9de53fd53423ad09d695129` |
| Vendored patch SHA-256 | `d35b422da321265150fe393da80a686862ef642ee45c65a3e2fb908d689d5d1f` |
| Verified toolchain | Go `1.26.5` |
| Verified harness | Claude Code `2.1.215` |

## Five-minute path

Prerequisites: Git, Go `1.26.5`, Claude Code `2.1.215`, `openssl`, `curl`,
`jq`, and a ChatGPT account with access to the target models. Install Go from
the [official downloads](https://go.dev/dl/?mode=html).

### 1. Pin, patch, test, and build CLIProxyAPI

Clone this repository and CLIProxyAPI as siblings:

```bash
git clone https://github.com/miguelrios/unc-skills.git
git clone https://github.com/router-for-me/CLIProxyAPI.git
cd CLIProxyAPI

git checkout --detach 93d74a890a44802f656d7f39a573916b2611896e
test "$(git rev-parse HEAD)" = "93d74a890a44802f656d7f39a573916b2611896e"

PATCH=../unc-skills/parable/patches/cliproxyapi-v7.2.88-claude-effort.patch
printf '%s  %s\n' \
  d35b422da321265150fe393da80a686862ef642ee45c65a3e2fb908d689d5d1f \
  "$PATCH" | sha256sum --check

git -c user.name="Local patch build" \
  -c user.email="local-build@invalid.example" am "$PATCH"

go test -count=1 ./internal/thinking ./internal/translator/codex/claude
go test -count=1 ./test -run '^TestThinkingE2EClaudeAdaptive_Body$'
go build -o cli-proxy-api ./cmd/server
```

`git am` must report two applied commits. If the base pin or patch checksum
does not match, stop; do not force-apply it to another release.

### 2. Create a loopback-only proxy configuration

The local proxy client key is not an OpenAI credential. It prevents other
local processes from using the proxy and must not be committed:

```bash
umask 077
mkdir -p "$HOME/.config/parable"
export CLIPROXY_API_KEY="$(openssl rand -hex 32)"
printf 'export CLIPROXY_API_KEY=%q\n' "$CLIPROXY_API_KEY" \
  > "$HOME/.config/parable/cliproxy.env"

cat > "$HOME/.config/parable/cliproxy.yaml" <<EOF
host: "127.0.0.1"
port: 8317
auth-dir: "~/.cli-proxy-api"
api-keys:
  - "$CLIPROXY_API_KEY"
debug: false
EOF
```

### 3. Connect the user's ChatGPT subscription

Run the browser OAuth flow:

```bash
./cli-proxy-api \
  --config "$HOME/.config/parable/cliproxy.yaml" \
  --codex-login
```

On a headless machine, use `--codex-device-login` instead. Enter the newly
printed device code once; stale or previously submitted codes produce
“We couldn’t authorize this device.”

This route uses ChatGPT subscription OAuth. Do not set `OPENAI_API_KEY` for
this setup.

### 4. Start and verify the local proxy

Start the server in one terminal:

```bash
source "$HOME/.config/parable/cliproxy.env"
./cli-proxy-api \
  --config "$HOME/.config/parable/cliproxy.yaml" \
  --local-model
```

Verify the authenticated catalog from another terminal:

```bash
source "$HOME/.config/parable/cliproxy.env"
curl -fsS \
  -H "Authorization: Bearer $CLIPROXY_API_KEY" \
  http://127.0.0.1:8317/v1/models |
  jq -e '
    [.data[].id] as $ids |
    all(
      ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"][];
      . as $model | $ids | index($model)
    )
  '
```

### 5. Launch stock Claude Code on Sol

Install Parable, then create the minimal personal configuration:

```bash
npx @parcha/parable install

cat > "$HOME/.config/parable/parable.toml" <<'EOF'
[parable]
version = 1

[claude]
base_url = "http://127.0.0.1:8317"
auth_token_env = "CLIPROXY_API_KEY"
brain_model = "gpt-5.6-sol"
EOF

source "$HOME/.config/parable/cliproxy.env"
npx @parcha/parable doctor
npx @parcha/parable claude -- --effort high
```

That launches the installed stock `claude` binary with Sol as the session
model. Change the final effort to any of the five verified values.

## Optional GPT named subagents

Terra and Luna can be materialized as exact project-local Claude agents by
adding:

```toml
[providers.claude]
type = "subagent"

[executors.terra]
provider = "claude"
model = "gpt-5.6-terra"
use_for = "Independent GPT implementation or debugging."

[executors.luna]
provider = "claude"
model = "gpt-5.6-luna"
use_for = "Independent GPT review or a second implementation."
```

Then run:

```bash
npx @parcha/parable agents sync
```

Parable writes `parable-terra` and `parable-luna` agent definitions with exact
`model:` fields. Live main-model transport and effort are proved for both
models; a Sol-parent → named-GPT-subagent tool proof is the next gate and is
not claimed by the current receipt.

Kimi remains paused and is intentionally absent from this setup.

## Evidence and boundaries

- [Released-binary diagnosis](https://github.com/miguelrios/unc-skills/blob/main/parable/docs/evidence/g1-gpt-model-effort-live/EXIT.md)
- [Patched source proof](https://github.com/miguelrios/unc-skills/blob/main/parable/docs/evidence/e1-cliproxy-effort-fix/EXIT.md)
- [Patched live matrix](https://github.com/miguelrios/unc-skills/blob/main/parable/docs/evidence/e2-cliproxy-effort-live/EXIT.md)

This is a per-user localhost setup. CLIProxyAPI owns OAuth storage and token
refresh; Parable stores neither provider credentials nor OAuth state. ChatGPT
plan limits, model entitlements, and provider terms still apply. Do not expose
the proxy port outside loopback.
