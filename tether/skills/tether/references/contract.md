# Bridge contract

## Boundary

The Hermes gateway owns the Slack credential and a mode-`0600` Unix socket. Local publishers send newline-delimited JSON to that socket. They never load a Slack token.

One bridge binds:

- one source capability: `codex_session`, `claude_session`, `zellij_pane`, `hermes_session`, or `headless_run`;
- one Slack workspace/channel/thread tuple;
- one owner (`*` means any allowlisted operator);
- one idempotency key.

An explicit run ID always creates `headless_run`, even inside Codex or Claude Code. Native sessions remain the source when those agents run inside Zellij; Zellij coordinates are retained only as origin metadata.

## Inbound routing

Resolve the exact persisted thread before bypassing Slack's mention gate. Then apply the global allowlist and per-bridge owner check. Fail closed at every missing field. Deduplicate by Slack message timestamp.

Queue and serialize native replies per bridge. Strip synthetic Slack thread history before native resume because prior bridge turns already exist in the bound agent session. Terminate the whole continuation process group on cancellation or timeout.

Never forward the gateway's Slack credential environment to a native agent process. Pass prompts on stdin rather than command arguments. Accept credential-helper output only for explicitly allowlisted environment keys. Treat resume flags as administrator configuration, never Slack-controlled data.

## Outcomes

Post native output back to the same Slack thread. For Zellij, capture an allowlisted agent command and its process fingerprint, recheck both before every delivery, inject the operator instruction into that exact pane, and require the pane agent to use the supplied bridge reply command. Never write into a shell or a pane whose process changed. Continue headless work as a durable Hermes conversation using the root report and thread history as context.

Display compact origin metadata in the root message without exposing full session IDs or absolute paths. Apply high-confidence credential redaction at Slack egress, sanitize stored and posted errors, and require agents to omit sensitive content that cannot be detected mechanically. Never route to a replacement session after a stale-source failure.

## Automation checklist

Before migrating a cron or automation, verify all of the following:

- the direct Slack call and token access are removed;
- `--run-id` is unique per scheduled occurrence;
- the idempotency key is stable across retries of that occurrence;
- destination channel, owner, and workspace come from explicit config;
- message text contains no credential or sensitive raw payload;
- a real thread reply reaches the intended durable Hermes context;
- unauthorized and duplicate replies produce no execution.
