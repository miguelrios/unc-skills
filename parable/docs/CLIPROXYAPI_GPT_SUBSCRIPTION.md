# GPT, Claude, and Grok subscription models in stock Claude Code

This is the reproducible OSS path for running stock Claude Code with
`gpt-5.6-sol` as the parent and exact GPT, Claude, and Grok named subagents
through a local CLIProxyAPI process and the user's own subscription OAuth.

No broker or shared deployment is involved:

```text
                                      ┌→ ChatGPT subscription OAuth
Claude Code → loopback CLIProxyAPI ───┤
                                      ├→ Claude subscription OAuth
                                      └→ xAI subscription OAuth
```

## Support state

Released CLIProxyAPI `v7.2.88` can transport all three GPT models, but it
maps every Claude Code effort setting to upstream `medium`. The patch shipped
in this repository fixes that protocol translation and has been live-proved
for all 15 combinations of:

- model: `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`
- effort: `low`, `medium`, `high`, `xhigh`, `max`

The result was 15/15 exact GPT effort pass-through and 3/3 tool canaries.
It also completed the full 30-cell named-child matrix: five Sol parent effort
settings times exact Terra, Luna, Grok, Sonnet, Opus, and Haiku children.
Every cell used a real child Bash artifact followed by parent Agent/Bash
consumption.

Grok 4.5 ran through xAI OAuth:

- Grok `low`, `medium`, and `high` are exact end to end.
- Claude's `xhigh` and `max` reach CLIProxyAPI intact, then clamp to Grok's
  supported `high`.
- A Sol parent invoked exact named agent `parable-grok` at all five parent
  efforts. Claude Code inherited each parent effort into the child; Grok
  applied the same `xhigh|max → high` clamp.

Claude children ran through Claude subscription OAuth:

- `claude-sonnet-5` and `claude-opus-4-8` preserve
  `low|medium|high|xhigh|max` exactly as Anthropic adaptive-thinking effort.
- `claude-haiku-4-5-20251001` completes all five cells but normalizes each
  inherited setting to `thinking.type=enabled`, `budget_tokens=31999`, and no
  effort label.

The patch is **not** an upstream CLIProxyAPI release. It is pinned to:

| Item | Pin |
|---|---|
| CLIProxyAPI base | `v7.2.88` / `93d74a890a44802f656d7f39a573916b2611896e` |
| Official source archive SHA-256 | `47c91832a4f09501ed0638191b18d74ffc8eef2ec9de53fd53423ad09d695129` |
| Vendored patch SHA-256 | `d35b422da321265150fe393da80a686862ef642ee45c65a3e2fb908d689d5d1f` |
| Verified toolchain | Go `1.26.5` |
| Verified harness | Claude Code `2.1.215` |

## Five-minute path

Prerequisites: Git, Go `1.26.5`, Claude Code `2.1.215`, `openssl`, `curl`,
`jq`, and the subscription accounts whose models you intend to use. Install
Go from the [official downloads](https://go.dev/dl/?mode=html).

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

### 3. Connect the user's subscriptions

Connect ChatGPT:

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

Connect the user's Claude subscription:

```bash
./cli-proxy-api \
  --config "$HOME/.config/parable/cliproxy.yaml" \
  --claude-login \
  --no-browser
```

Keep this command running while you open its newly printed authorization URL
and complete the callback. A stale URL or a callback delivered to a prior
process cannot complete the new PKCE flow. CLIProxyAPI stores its own OAuth
record; do not copy Claude Code's credential and do not set
`ANTHROPIC_API_KEY`.

Connect the user's xAI subscription separately:

```bash
./cli-proxy-api \
  --config "$HOME/.config/parable/cliproxy.yaml" \
  --xai-login \
  --no-browser
```

Complete the printed xAI device flow. CLIProxyAPI stores and refreshes each
provider's OAuth record in its user-only auth directory. Do not set
`XAI_API_KEY`; this recipe proves subscription routes, not metered API
billing.

### 4. Start and verify the local proxy

Start the server in one terminal:

```bash
source "$HOME/.config/parable/cliproxy.env"
./cli-proxy-api \
  --config "$HOME/.config/parable/cliproxy.yaml" \
  --local-model
```

Verify the combined authenticated catalog from another terminal. Remove ids
for subscriptions you intentionally did not connect:

```bash
source "$HOME/.config/parable/cliproxy.env"
curl -fsS \
  -H "Authorization: Bearer $CLIPROXY_API_KEY" \
  http://127.0.0.1:8317/v1/models |
  jq -e '
    [.data[].id] as $ids |
    all(
      [
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
        "grok-4.5",
        "claude-sonnet-5",
        "claude-opus-4-8",
        "claude-haiku-4-5-20251001"
      ][];
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

[providers.claude]
type = "subagent"

[executors.grok]
provider = "claude"
model = "grok-4.5"
use_for = "A third-family implementation or adversarial review."
avoid_for = "Reviewing its own diff."

[executors.terra]
provider = "claude"
model = "gpt-5.6-terra"
use_for = "Independent GPT implementation or debugging."

[executors.luna]
provider = "claude"
model = "gpt-5.6-luna"
use_for = "Independent GPT review or a second implementation."

[executors.sonnet_exact]
provider = "claude"
model = "claude-sonnet-5"
use_for = "Implementation through the exact entitled Sonnet model."

[executors.opus_exact]
provider = "claude"
model = "claude-opus-4-8"
use_for = "Deep review through the exact entitled Opus model."

[executors.haiku_exact]
provider = "claude"
model = "claude-haiku-4-5-20251001"
use_for = "Fast mechanical work through the exact entitled Haiku model."
EOF

source "$HOME/.config/parable/cliproxy.env"
npx @parcha/parable doctor
npx @parcha/parable agents sync
npx @parcha/parable claude -- --effort high
```

That launches the installed stock `claude` binary with Sol as the session
model and materializes six exact project agents:

```text
parable-terra          gpt-5.6-terra
parable-luna           gpt-5.6-luna
parable-grok           grok-4.5
parable-sonnet-exact   claude-sonnet-5
parable-opus-exact     claude-opus-4-8
parable-haiku-exact    claude-haiku-4-5-20251001
```

Change the final effort to any verified value. In the session, ask Sol to use
the exact `parable-*` agent intended for the task.

The named-child effort rule is observable, not advisory:

| Child | `low` | `medium` | `high` | `xhigh` | `max` |
|---|---|---|---|---|---|
| Terra | exact | exact | exact | exact | exact |
| Luna | exact | exact | exact | exact | exact |
| Grok 4.5 | exact | exact | exact | → `high` | → `high` |
| Sonnet 5 | adaptive exact | adaptive exact | adaptive exact | adaptive exact | adaptive exact |
| Opus 4.8 | adaptive exact | adaptive exact | adaptive exact | adaptive exact | adaptive exact |
| Haiku 4.5 | → enabled/31,999 | → enabled/31,999 | → enabled/31,999 | → enabled/31,999 | → enabled/31,999 |

## What the 30/30 proof means

The proof covers exact model selection, model-specific effort behavior, real
child Bash use, parent Agent/Bash consumption, and distinct OAuth routes. It
does not promise that every plan tier entitles every model forever. The
authenticated catalog remains the fail-closed entitlement check at launch.

Kimi remains paused and is intentionally absent from this setup.

## Evidence and boundaries

- [Released-binary diagnosis](https://github.com/miguelrios/unc-skills/blob/main/parable/docs/evidence/g1-gpt-model-effort-live/EXIT.md)
- [Patched source proof](https://github.com/miguelrios/unc-skills/blob/main/parable/docs/evidence/e1-cliproxy-effort-fix/EXIT.md)
- [Patched live matrix](https://github.com/miguelrios/unc-skills/blob/main/parable/docs/evidence/e2-cliproxy-effort-live/EXIT.md)
- [xAI OAuth and catalog proof](https://github.com/miguelrios/unc-skills/blob/main/parable/docs/evidence/x1-grok45-catalog/EXIT.md)
- [Grok main-model matrix](https://github.com/miguelrios/unc-skills/blob/main/parable/docs/evidence/x2-grok45-main-permutations/EXIT.md)
- [Sol to named Grok matrix](https://github.com/miguelrios/unc-skills/blob/main/parable/docs/evidence/x3-sol-grok-named-subagent/EXIT.md)
- [Sol to named Terra/Luna matrix](https://github.com/miguelrios/unc-skills/blob/main/parable/docs/evidence/y1-sol-terra-luna/EXIT.md)
- [Claude OAuth gate](https://github.com/miguelrios/unc-skills/blob/main/parable/docs/evidence/y2-claude-oauth/EXIT.md)
- [Authenticated Claude catalog](https://github.com/miguelrios/unc-skills/blob/main/parable/docs/evidence/y3-claude-catalog/EXIT.md)
- [Sol to named Claude-child matrix](https://github.com/miguelrios/unc-skills/blob/main/parable/docs/evidence/y4-sol-claude-children/EXIT.md)

This is a per-user localhost setup. CLIProxyAPI owns OAuth storage and token
refresh; Parable stores neither provider credentials nor OAuth state.
ChatGPT, Claude, and xAI plan limits, model entitlements, and provider terms
still apply. Do not expose the proxy port outside loopback.
