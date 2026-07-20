# Parable first-run onboarding contract

## Command surface

```text
parable setup [--vendors chatgpt[,claude][,xai]] [--proxy-bin PATH]
              [--config-dir DIR] [--port PORT] [--build-proxy]
              [--no-auth] [--non-interactive]
parable proxy build [--install-dir DIR]
parable proxy start
parable auth add chatgpt [--device]
parable auth add claude
parable auth add xai
parable auth status [--json]
```

Interactive `setup` always includes ChatGPT because the parent is exact
`gpt-5.6-sol`; it asks whether to add Claude and xAI. Non-interactive setup
requires `--vendors`, and that list must include `chatgpt`. Kimi is not an
available choice while its onboarding remains paused.

`setup` discovers the proxy in this order: explicit `--proxy-bin`,
`PARABLE_CLIPROXY_BIN`, `parable-cliproxy-api` on `PATH`, then
`cli-proxy-api` on `PATH`. A missing binary fails before files or auth change.
`--build-proxy` explicitly authorizes a managed source build. Interactive
setup may offer that build but must ask before network or compilation work.

`proxy build` owns a new versioned directory under Parable's data directory,
checks out the pinned upstream commit, verifies the vendored patch checksum,
applies it, runs the pinned test slice, and emits a private executable. It
never patches an arbitrary existing checkout and refuses a pre-existing build
destination.

## Generated state

Default configuration root: `~/.config/parable`, mode 0700.

| File | Mode | Contents |
|---|---:|---|
| `cliproxy.yaml` | 0600 | loopback host/port, user auth dir, random local client token |
| `cliproxy.env` | 0600 | shell export for `CLIPROXY_API_KEY` only |
| `parable.toml` | 0600 | Sol parent plus exact selected executor ids and routing |
| `setup.json` | 0600 | schema version, selected vendors, proxy binary, port, and generated paths; no credential |

Default auth root: `~/.cli-proxy-api`, mode 0700. Parable never reads or
writes OAuth token fields. The local client token is 32 random bytes rendered
as hexadecimal, written atomically to the YAML/env files, and never printed.

If none of the four files exists, setup creates the complete set. If all four
exist and `setup.json` describes a valid complete setup, rerunning is an
idempotent no-op followed only by explicitly requested auth. A partial set,
symlink, invalid mode, invalid state, different vendor selection, or different
binary/port fails closed. There is no `--force` overwrite path.

## Vendor mapping

| Selection | Native CLIProxyAPI login | Exact Parable models |
|---|---|---|
| `chatgpt` | `--codex-login`; `--codex-device-login` with `--device` | `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna` |
| `claude` | `--claude-login --no-browser` | `claude-sonnet-5`, `claude-opus-4-8`, `claude-haiku-4-5-20251001` |
| `xai` | `--xai-login --no-browser` | `grok-4.5` |

Auth subprocesses inherit the terminal. Parable does not capture, parse,
persist, transform, or reproduce OAuth output. For remote Claude auth, help
names an SSH forward for `localhost:54545`, requires keeping the same process
alive, and warns that old URLs cannot complete a new PKCE flow.

`auth status` opens user-only JSON records and emits only provider presence,
record count, file-mode validity, and parse-error count. It never returns file
names, account identifiers, token fields, expiry payloads, or paths.

`proxy start` replaces the Parable process with the configured CLIProxyAPI
binary and exact `--config ... --local-model` arguments. It stays foreground;
Parable adds no daemon, log capture, listener override, or secret argument.

## Catalog and launch

Setup selection is intent; the authenticated loopback `/v1/models` catalog is
entitlement truth. Finalization and `parable claude` require the exact parent
and every selected exact child. Missing ids fail before agent generation or
Claude launch. Display aliases, regex selection, and fallback models are
forbidden.

## Fake-success guards

1. Missing proxy binary: no generated file and no auth subprocess.
2. Missing ChatGPT selection: validation error; Sol cannot silently move.
3. Partial/existing/symlinked/over-permissive state: no overwrite.
4. Unsupported vendor: no subprocess.
5. Missing exact catalog id: no generated agents and no Claude launch.
6. Auth status: secret sentinel strings in records never appear in output.
7. Build: wrong source commit or patch checksum stops before `git am`/Go.
8. All generated listeners are literal loopback; remote base URLs remain
   rejected by the existing Parable validator.

## Non-goals

- No broker, LiteLLM, shared deployment, or multi-user credential store.
- No provider API keys, credential copy, OAuth callback helper, or token
  exchange implemented by Parable.
- No background service manager in this chain; foreground start is portable.
- No npm publish without a separate human-authorized release action.
