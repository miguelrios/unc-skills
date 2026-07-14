# Recall Brain clients

The client is a stdlib-only, consent-first boundary for approved local coding
history, supported user exports, and deliberate memory writes. It sends the same
versioned envelopes as the Linux collector; it does not inspect ChatGPT, Cowork,
or other application-private databases.

## Build the reproducible macOS bundle

```bash
python3 scripts/build_macos_package.py \
  --source-root . \
  --output dist/recall-brain-macos.tar.gz
```

Two builds from the same tree are byte-identical. `MANIFEST.json` records every
installed file's byte count and SHA-256. The bundle requires Python 3.10+ and
installs only beneath the selected user prefix plus two user LaunchAgent files.

## Inspect, install, and remove

Run a content-free inventory before enabling collection:

```bash
recall-brain dry-run --visibility private \
  --claude-root ~/.claude/projects \
  --codex-root ~/.codex/sessions
```

Put one source-scoped token per selected source into Keychain. The token is read
at runtime and never appears in the plist, process arguments, logs, manifest, or
spool:

```bash
printf '%s' "$SCOPED_CLAUDE_TOKEN" | recall-brain keychain-store \
  --service ai.parcha.recall --account claude:mac:my-mac
printf '%s' "$SCOPED_CODEX_TOKEN" | recall-brain keychain-store \
  --service ai.parcha.recall --account codex:mac:my-mac

./install.sh \
  --endpoint https://brain.example.ts.net \
  --host-id my-mac \
  --keychain-service ai.parcha.recall \
  --visibility private \
  --sources claude,codex
```

`--sources`, `--visibility`, `--claude-root`, and `--codex-root` are explicit
consent controls. `--privacy-mode off|scrub|drop` is also explicit and defaults
to `off` for compatibility. Re-running the installer is an in-place upgrade. Remove the
client and its spool with:

```bash
./uninstall.sh
```

To continuously ingest only files deliberately copied into a flat export inbox,
add `--export-inbox "$HOME/Recall Inbox"`. The installer creates a separate
private LaunchAgent and uses Keychain account `chatgpt-export:mac:<host-id>`.
This does not inspect the ChatGPT app, Cowork, Downloads, or browser storage.
Re-run with `--disable-export-inbox` to unload only that LaunchAgent while
preserving its content-free catalog/spool for recovery; full uninstall removes
all Recall client state but never deletes the user-owned inbox.

## Supported exports and explicit memory

Only files supplied on the command line are considered. JSON, JSONL, and ZIP
archives containing JSON/JSONL are supported; traversal and symlink members are
rejected before any record is read.

```bash
recall-brain export --dry-run ... ~/Downloads/supported-export.zip
recall-brain export ... ~/Downloads/supported-export.zip
recall-brain put ... --text 'remember this' --provenance-uri manual://task
recall-brain delete ... 'recall://memory:mac:my-mac/memory-…?rev=1'
```

The omitted `...` is the connection tuple printed by `recall-brain --help`:
endpoint, exact source ID, principal, visibility, and a Keychain service/account
reference. Export native IDs are content/member stable, so replay is idempotent.
Delete writes a canonical tombstone; deleted evidence no longer appears in
search and its prior receipt no longer resolves.

## Pre-ingest privacy policy

Privacy policy version `recall-privacy-v1` runs on parsed content before a
collector writes version hashes or outbox rows and before export or memory code
makes an HTTP request:

- `off` preserves existing behavior and is the default.
- `scrub` replaces classified spans with category-labelled redactions while
  retaining safe context and provenance.
- `drop` omits the entire classified record. Its receipt contains only action,
  category counts, policy version, and a reason code.

Preview a value without printing it or writing a spool. Structural-only preview
makes no request; configuring the optional judge explicitly sends the value to
the approved router:

```bash
printf '%s' "$VALUE" | recall-brain privacy-preview --privacy-mode scrub
```

The local structural policy covers credential assignments and structured email,
phone, postal, government, financial, and medical identifiers. Contextual names
and meaning require the optional agentic judge. It accepts only an HTTPS staging
LiteLLM URL, a model alias, and a mode-0600 file containing a short-lived scoped
virtual key as `{"virtual_key":"...","scope":"recall-privacy-judge",`
`"expires_at":"...Z"}`; provider-direct URLs, wrong scopes, and expired files are
rejected. Never give it the LiteLLM master
key. Judge failure defaults to `drop`; `ignore` is an explicit availability-over-
privacy choice. The judge sees the text being classified, so enable it only with
informed consent and a router retention policy you accept.

Enabling `scrub` or `drop` compacts pre-existing pending spool rows once with
SQLite secure deletion. It cannot retroactively remove evidence already committed
to the Brain or copies retained in original source transcripts/backups. Delete
already-committed evidence by its canonical receipt before relying on the new
policy. Roll back by reinstalling with `--privacy-mode off`; uninstall removes the
client, LaunchAgents, and entire local spool.

## External connectors

The bundle includes the versioned connector SDK, but installs no external source
and discovers no plugins automatically. A connector must be explicitly installed
and constructed. It owns only source fetching; the shared runner owns privacy,
durable ACK recovery, cursor commits, tombstones, and content-free health state.
See `connectors/README.md` before adding a source.

## Deliberate agent capture over MCP

The bundled stdio MCP server lets Codex, Claude Code, Claude Desktop, or another
local MCP host deliberately save one selected evidence item without giving the
model a Brain credential. Preview one source-scoped config per host; Recall does
not edit harness config:

```bash
recall-brain mcp-config-preview \
  --endpoint https://brain.example.ts.net \
  --source-id capture:mac:my-mac:codex \
  --capture-origin openai-codex \
  --visibility private \
  --keychain-service ai.parcha.recall \
  --keychain-account capture:mac:my-mac:codex \
  --privacy-mode scrub
```

Review the JSON and install it through the host's normal MCP settings. Give each
host its own source-scoped Brain credential and fixed `--capture-origin`; the
model cannot set or override origin in a tool call. The process resolves
Keychain only after launch; stdout carries newline-delimited MCP JSON-RPC and
never logs arguments. The tools are `recall_capture`, `recall_forget`, and
content-free `recall_doctor`. Capture retries are content-identity stable and
return the same receipt. `shared` visibility is available only when explicitly
selected in the process config, never in a tool call. The current stable MCP
revision is `2025-11-25`, with negotiation support for `2025-06-18` clients.

ChatGPT does not connect directly to a local MCP server. Use an approved remote
MCP deployment or OpenAI's Secure MCP Tunnel adapter instead; do not reuse a
local host's source credential for that bridge. See OpenAI's
[developer mode and MCP documentation](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt).
