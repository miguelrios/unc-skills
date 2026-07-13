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
security add-generic-password -U -s ai.parcha.recall \
  -a claude:mac:my-mac -w '<scoped token>'
security add-generic-password -U -s ai.parcha.recall \
  -a codex:mac:my-mac -w '<scoped token>'

./install.sh \
  --endpoint https://brain.example.ts.net \
  --host-id my-mac \
  --keychain-service ai.parcha.recall \
  --visibility private \
  --sources claude,codex
```

`--sources`, `--visibility`, `--claude-root`, and `--codex-root` are explicit
consent controls. Re-running the installer is an in-place upgrade. Remove the
client and its spool with:

```bash
./uninstall.sh
```

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
