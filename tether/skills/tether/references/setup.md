# Setup

Tether is an external Hermes plugin plus one canonical skill payload for Codex and Claude Code. It never modifies the Hermes source checkout and never asks for, copies, or persists Slack tokens; the running Hermes gateway retains that credential boundary.

## One-command setup

Run from any directory:

```bash
npx --yes --package=github:miguelrios/unc-skills#main tether setup --harness=both
```

Use `--harness=codex` or `--harness=claude-code` when only one is installed.

The command installs Tether, opens Hermes's own Slack setup, restarts or installs the gateway service when possible, and runs live readiness checks. Hermes generates the current Slack app manifest, including Socket Mode, Interactivity, events, scopes, and slash commands. Follow its prompts to create or update the Slack app and enter the bot/app tokens directly into Hermes.

Slack's website remains the only manual boundary: create the app from Hermes's generated manifest, install it to the workspace, create the `connections:write` app token, and invite the bot to the desired channels.

Completion criterion: `tether doctor` reports a private live broker, at least one authorized operator, and an installed runtime/plugin. A default home channel is optional; without one, pass `--channel` when notifying.

## Existing Hermes Slack setup

Tether automatically reuses Hermes's `SLACK_ALLOWED_USERS`, `GATEWAY_ALLOWED_USERS`, and `SLACK_HOME_CHANNEL` runtime settings. Do not duplicate them in Tether config.

For agent-to-agent conversation, set `SLACK_ALLOW_BOTS=all` and set `SLACK_TRUSTED_BOT_IDS` to a comma-separated list of trusted peer Slack bot IDs or bot user IDs. Tether rejects every unlisted bot before Hermes authorization; an empty list rejects all bots. Restart the gateway and rerun `tether doctor` after changing either setting.

The generated `${XDG_CONFIG_HOME:-~/.config}/tether/config.toml` is for optional overrides only:

- use `default_channel`, `default_owner`, `team_id`, or `allowed_users` for a Tether-specific policy;
- use `codex_resume_args` or `claude_resume_args` for fixed administrator-controlled sandbox flags;
- extend `zellij_agent_commands` when a Zellij-only bridge should recognize another agent executable;
- use `credential_command` plus `credential_env_allowlist` for scoped, short-lived native resume credentials.

An empty combined Hermes/Tether allowlist fails closed. Threads are shared among that explicit allowlist by default; set `default_owner` or pass `--owner` to restrict them to one member.

## Native resume credentials

Resumed Codex and Claude Code processes receive a minimal environment and may use normal user-level authentication. They never inherit Hermes's Slack variables.

For an organization router, make `credential_command` read non-secret bridge metadata as JSON from stdin and return only short-lived environment values as JSON on stdout. Tether accepts only keys named in `credential_env_allowlist`, consumes the output through a pipe, and never logs it. Keep master keys and service-account credentials in the helper's protected store.

## Non-interactive and manual setup

For CI or a headless host, install first and generate the Slack manifest without prompting:

```bash
./install.sh --harness=both
tether setup --non-interactive
```

Then run `hermes gateway setup`, start the gateway, and run `tether doctor`.

## Updates

Hermes and Tether update independently. After a Hermes update, rerun the one-command setup or package installer. It replaces only Tether code, preserves config and bridge state, restarts Hermes, and checks the Slack adapter compatibility surface. Never fall back to direct Slack calls when compatibility fails.

Pin the Git reference to a release tag instead of `#main` when reproducible production installs matter.
