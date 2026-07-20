# GPT, Claude, and Grok subscriptions in one Claude Code session

Parable can run stock Claude Code with exact `gpt-5.6-sol` as the parent and
exact GPT, Claude, and Grok named agents through one user-owned, loopback-only
CLIProxyAPI process.

```text
                                      ┌→ this user's ChatGPT subscription
stock Claude Code → localhost proxy ──┼→ this user's Claude subscription
                                      └→ this user's xAI subscription
```

There is no broker, shared deployment, provider API key, or copied Claude Code
credential in this mode. CLIProxyAPI owns each native OAuth flow and record.
Parable creates local configuration, delegates to those native flows, checks
the authenticated model catalog, and generates exact project agents.

Kimi is paused and is not a setup option.

## Start here

Until the verified `0.1.9` package is explicitly published to npm, run the CLI
from source:

```bash
git clone https://github.com/miguelrios/unc-skills.git
cd unc-skills/parable
PARABLE="$PWD/bin/parable.js"

"$PARABLE" install
"$PARABLE" setup --build-proxy
```

Setup always selects ChatGPT because Sol is the parent. In interactive mode it
asks whether to add Claude and xAI, then starts each selected provider's native
authorization flow. `--build-proxy` explicitly authorizes the pinned source
download and build; without it Parable discovers an existing proxy from
`--proxy-bin`, `PARABLE_CLIPROXY_BIN`, or `PATH`.

For a headless ChatGPT device flow, create configuration without starting auth
and connect each selected vendor explicitly:

```bash
"$PARABLE" setup --build-proxy --no-auth
"$PARABLE" auth add chatgpt --device
"$PARABLE" auth add claude
"$PARABLE" auth add xai
"$PARABLE" auth status
```

Only run the Claude/xAI commands if you selected those vendors. Claude auth
prints an SSH-forward reminder for callback port `54545`; keep that same
command alive until its newly issued callback completes. Old authorization
URLs cannot complete a new PKCE process.

Start the proxy in one terminal:

```bash
"$PARABLE" proxy start
```

Leave it in the foreground. In a second terminal, enter the repository where
you want Claude Code to work:

```bash
cd /path/to/your/project
"$PARABLE" setup finalize
"$PARABLE" claude -- --effort high
```

`setup finalize` performs a read-only `GET /v1/models`, requires the exact Sol
parent and every exact selected child, writes or confirms only Parable-owned
project agents, and prints the launch command. `parable claude` repeats the
same fail-closed catalog gate before stock Claude starts.

You do not need to source `cliproxy.env`: the CLI passes the generated local
client token only to the catalog/Claude child process, converts it to
`ANTHROPIC_AUTH_TOKEN`, and removes the source variable before Claude starts.

## Non-interactive setup

Automation must state its vendor selection and include ChatGPT:

```bash
"$PARABLE" setup \
  --non-interactive \
  --vendors chatgpt,claude,xai \
  --build-proxy \
  --no-auth
```

Supported selections are:

| Selection | Parent and named children |
|---|---|
| `chatgpt` | parent Sol; Terra and Luna agents |
| `claude` | exact Sonnet 5, Opus 4.8, and Haiku 4.5 agents |
| `xai` | exact Grok 4.5 agent |

Unknown vendors, a selection without ChatGPT, missing executables, partial
state, changed generated content, unsafe modes, or symlinks all fail without
overwriting anything. There is deliberately no `--force` path.

## What setup creates

The default configuration root is `~/.config/parable` with mode `0700`:

| File | Mode | Purpose |
|---|---:|---|
| `cliproxy.yaml` | `0600` | literal loopback listener, auth directory, random local client token |
| `cliproxy.env` | `0600` | local `CLIPROXY_API_KEY` export |
| `parable.toml` | `0600` | exact selected parent, agents, and routing |
| `setup.json` | `0600` | non-secret setup manifest used for strict idempotency |

CLIProxyAPI's auth directory is `~/.cli-proxy-api`, mode `0700`. Parable never
parses, copies, or writes provider OAuth token fields. `auth status` opens only
mode-`0600` regular JSON records and emits provider presence/counts plus
mode/parse aggregates—never filenames, paths, accounts, or credential values.

## Native authorization mapping

Parable adds no OAuth implementation. Its commands become exactly:

| Parable command | CLIProxyAPI flags |
|---|---|
| `auth add chatgpt` | `--config … --codex-login` |
| `auth add chatgpt --device` | `--config … --codex-device-login` |
| `auth add claude` | `--config … --claude-login --no-browser` |
| `auth add xai` | `--config … --xai-login --no-browser` |
| `proxy start` | `--config … --local-model` |

Auth subprocesses inherit the terminal. Proxy startup inherits stdio, stays
foreground, forwards `SIGINT`, `SIGTERM`, and `SIGHUP`, and preserves the
proxy's exit status.

## Managed proxy build

`parable proxy build` and `setup --build-proxy` create a new private managed
directory. They never patch an existing checkout. The build stops before
`git am` or Go if either source or patch pin differs:

| Item | Pin |
|---|---|
| CLIProxyAPI base | `v7.2.88` / `93d74a890a44802f656d7f39a573916b2611896e` |
| Vendored patch SHA-256 | `d35b422da321265150fe393da80a686862ef642ee45c65a3e2fb908d689d5d1f` |
| Verified toolchain | Go `1.26.5` |
| Verified harness | Claude Code `2.1.215` |

The builder applies the vendored effort-translation patch, runs the focused Go
test slices, and emits a mode-`0700` `parable-cliproxy-api` executable. A
pre-existing destination is refused rather than reused or deleted.

Prerequisites are Node 18+, Python 3.11+, Git, Go, and stock Claude Code.

## Exact proved cast and effort behavior

The live proof covered all 30 parent/child cells: five Sol parent effort values
times six exact named children. Every child used Bash and the parent consumed
its result.

| Exact child | Subscription | `low` | `medium` | `high` | `xhigh` | `max` |
|---|---|---|---|---|---|---|
| `gpt-5.6-terra` | ChatGPT | exact | exact | exact | exact | exact |
| `gpt-5.6-luna` | ChatGPT | exact | exact | exact | exact | exact |
| `grok-4.5` | xAI | exact | exact | exact | → `high` | → `high` |
| `claude-sonnet-5` | Claude | adaptive exact | adaptive exact | adaptive exact | adaptive exact | adaptive exact |
| `claude-opus-4-8` | Claude | adaptive exact | adaptive exact | adaptive exact | adaptive exact | adaptive exact |
| `claude-haiku-4-5-20251001` | Claude | → enabled/31,999 | → enabled/31,999 | → enabled/31,999 | → enabled/31,999 | → enabled/31,999 |

The authenticated catalog is entitlement truth. A similarly named id, display
alias, different case, or `-latest` suffix never substitutes for a missing
exact id. Subscription plan limits and provider terms still apply.

## npm release boundary

GitHub source is currently `0.1.9`; the npm registry is still `0.1.7`. The
source package passes its complete suite and `npm pack --dry-run`, but this
repository work does not authorize publication.

After a human explicitly approves the release:

```bash
cd parable
npm test
npm run pack:check
npm publish --access public
```

After publication, `npx @parcha/parable@0.1.9 …` can replace the source
`"$PARABLE" …` commands above.

## Evidence

- [Secure setup and pinned builder](evidence/o1-secure-bootstrap/EXIT.md)
- [Native auth, safe status, and proxy lifecycle](evidence/o2-auth-proxy-lifecycle/EXIT.md)
- [Exact finalize and hermetic first launch](evidence/o3-finalize-first-launch/EXIT.md)
- [Unified 30/30 live subscription verdict](evidence/y5-unified-subscription-verdict/EXIT.md)
- [GPT effort patch live proof](evidence/e2-cliproxy-effort-live/EXIT.md)
- [Grok subscription proof](evidence/x5-grok45-verdict/EXIT.md)

This remains an OSS, per-user localhost recipe. It does not require or support
a shared credential broker.
