---
name: recall
description: Find prior Claude Code and Codex sessions with an indexed local search engine, then continue the work, repeat it with fresh inputs, or distill it into a skill. Runs from Claude Code, Codex, or pi; the current index covers Claude Code and Codex transcripts. Use when the user names Recall, says "find that conversation where…", "what did we do last time about…", "continue what we did yesterday on X", "what did codex do on this branch", "turn what we did about Y into a skill", or "remember when you…". Not for searching code (use grep on the repo) or for facts already in MEMORY.md.
---

# /recall — session memory engine

Claude Code and Codex sessions on this machine are indexed into a local
SQLite engine. You do not grep transcripts; you query the index and read only
the winning session. All commands go through one CLI:

```bash
python3 scripts/recall.py <command>       # relative to this skill directory
```

## Local, central, and shadow modes

With no central configuration, every command behaves exactly as the local engine described below.
Setting `RECALL_URL` selects the tailnet central service for read commands (`search`, `show`,
`related`, and `doctor`) and enables deliberate writes (`put` and `delete`). The same flags and output shapes remain valid, and every displayed remote
hit includes its resolvable receipt on the `WHY` line.

Use an explicit `/mcp` suffix for a public or managed MCP endpoint, for example
`RECALL_URL=https://recall.example.com/mcp`. The skill then calls the scoped
`recall_search`, `recall_show`, `recall_related`, `recall_capture`, and
`recall_forget` tools directly; `doctor` uses MCP ping. `session-export` has no
MCP tool and fails closed. A URL without `/mcp` preserves the legacy REST
transport.

For a persistent per-device read profile, use a mode-0600 regular file at
`~/.config/recall-brain/client.json` with the exact shape
`{"schema_version":1,"url":"https://brain.example.com/mcp",`
`"token_file":"/absolute/private/read-token.json"}`. The referenced token file
must also be a non-symlink mode-0600 regular file. Environment variables override
the profile field by field; `RECALL_MODE=local` remains the instant rollback.
Neither config validation nor transport errors render either private path.

Remote search can route explicitly without weakening credential scope:

```bash
python3 scripts/recall.py search "budget decision" --source-id cowork:mac:owner
python3 scripts/recall.py search "budget decision" --source-family coding_history
python3 scripts/recall.py search "budget decision" --source-alias cowork
```

Aliases are configured by the Brain owner and resolve to one exact source. Requested source ID,
family, and alias filters are intersected with any source-scoped bearer; they can narrow results but
never broaden authorization. The remote response includes content-free routing diagnostics. Source
routing fails closed in local mode because the local index has no central source authority.

Use `RECALL_MODE=local|remote|shadow` when the mode must be explicit:

- `local` is the config-only rollback switch and never calls the central service.
- `remote` fails closed on transport/auth errors; it never silently returns stale local results.
- `shadow` returns the local result while recording a receipt-level local/central comparison under
  `~/.recall/shadow.jsonl` (override with `RECALL_SHADOW_LOG`).

Interactive tailnet access uses the Tailscale identity boundary. If a scoped bearer is required,
set `RECALL_TOKEN_FILE` to a mode-`0600` JSON file containing `{"token":"..."}`. Never put a token
in `RECALL_URL`, shell history, a repository, or evidence. `index` remains local in every mode, so
switching read modes cannot rewrite either the local SQLite index or central canonical events.

## Deliberate memory writes

When the user explicitly asks to remember durable information, write it through
the central evidence protocol rather than editing an opaque memory file:

```bash
export RECALL_WRITE_SOURCE_ID="memory:mac:$(hostname -s)"  # credential must be scoped to this exact source
python3 scripts/recall.py put "the durable fact or work receipt" \
  --visibility private --provenance-uri "manual://current-task"
python3 scripts/recall.py delete 'recall://memory:mac:host/memory-…?rev=1'
```

`put` returns the canonical receipt. Preserve it when reporting the write;
`delete` requires that receipt and emits a tombstone under the same source and
native ID. REST writes require the exact source ID. MCP writes are instead
bound to the source and origin of the scoped host credential, so the client
does not transmit either authority. All writes require remote mode and fail
closed if the endpoint or scoped credential is unavailable. Never infer a
shared visibility choice: default to `private`, and use `shared` only when the
user deliberately selects it. Secret-shaped lines are redacted before ingest.

Completed Grep AI research can be imported through the packaged read-only v2
connector. Use `grep-ai-config-preview` to inspect the private one-shot command,
then `grep-ai-sync`; Grep `research:read` authority and Brain source authority
must use separate Keychain or mode-0600 references. The connector never creates
jobs or infers deletion from list absence. Use the returned Brain receipt for an
explicit `delete`.

Use `connector-registry-preview` for the static, content-free inventory of
capture, export inbox, and Grep AI trust boundaries. Use
`connector-registry-status` with authority-presence flags and an optional spool
to inspect only bounded health/count/checkpoint facts. Neither command reads
credential values or source content, and status never syncs or repairs state.

Use `connector-supervisor-preview` to inspect the static cadence/lease/backoff
contract and `connector-supervisor-status --state <private-db>` for aggregate
ready/due/leased/parked/outcome counts. Status is immutable and never renders a
job key, connector/source identity, path, cursor, command, credential, exception,
or content. The supervisor schedules only explicitly constructed registered pull
connectors; it does not discover plugins or own connector configuration.

For a deliberately configured Mac service, keep the closed two-source host JSON
in a mode-0700 directory as a mode-0600 regular file. Validate it with
`connector-supervisor-config-preview --config <file>`; this reads no authority
or source content. Install it with `--connector-supervisor-config <file>` or run
one bounded cycle with `connector-supervisor-run --config <file> --state
<private-db> --once`. Config may contain only file/Keychain references—never
credential values—and Brain/Grep authority references must be distinct. Use
`--disable-connector-supervisor` to unload the agent without deleting its
recoverable private state.

When the packaged Brain client is available, offer its opt-in pre-ingest privacy
policy for transcript/export/memory writes: `off` preserves compatibility,
`scrub` retains safe context, and `drop` omits the classified record before spool
or network. Run `privacy-preview` for a content-free category/action receipt.
Explain that this does not delete evidence already committed or alter the original
transcript; deletion still requires the canonical receipt. Never enable the
optional contextual-PII judge without consent, and route it only through staging
LiteLLM with a short-lived scoped virtual key—never a master key or direct provider.

## Consented ChatGPT exports and Cowork local project logs

When the packaged Brain client is installed, use its explicit export inbox for
ChatGPT exports. Never scrape application databases, caches, browser storage,
Desktop, or Downloads. Inventory only the directory the user selected:

```bash
recall-brain export-inbox-dry-run --inbox "$HOME/Recall Inbox" \
  --catalog "$HOME/Library/Application Support/RecallBrain/state/chatgpt-export-catalog.db" \
  --privacy-mode scrub
```

`export-inbox-list` returns opaque export IDs. `export-inbox-remove ... exp_...`
queues reference-safe tombstones; deleting a local file alone deliberately does
not delete central memory. Use `--export-inbox` during Mac package installation
to opt into scheduled sync, and `--disable-export-inbox` to unload that agent
without destroying its recoverable catalog/spool.

For Claude Cowork, the user may separately opt into the packaged `cowork`
collector. This is a narrow exception for Cowork's local project-log surface
beneath an explicitly selected `local-agent-mode-sessions` root; it is
not permission to inspect a Claude application database, cache, audit log,
attachment store, browser store, session metadata file, Desktop, or Downloads.
Only user/assistant natural-language records under the nested
`.claude/projects` logs are eligible. Privacy must be `scrub` or `drop` and is
applied before spool or network writes. Local absence and archive state never
imply deletion.

Install the unified utility with explicit selections such as
`--sources claude-code,codex,cowork` and `--export-inbox <selected-directory>`.
Use `recall-brain mac-status` for a content-free enabled/health/lag/checkpoint
view. Use `recall-brain mac-disable --source <class>` to unload one source while
retaining its recoverable state; uninstall also retains state unless the user
explicitly selects `--delete-state`.

## Deliberate capture from any MCP host

Prefer the packaged `recall_capture` MCP tool when the user wants an agent to
remember a selected decision, result, or external finding. Capture one concise
evidence object with a timestamp, title/body, tags, and a non-secret provenance
URI; the host configuration supplies the truthful, fixed origin. Return its
canonical receipt. Do not capture whole
transcripts, hidden reasoning, ambient context, secrets, or third-party results
the user did not select. The MCP process—not the model—owns origin, the
source-scoped credential, and privacy policy. Use one source profile per host;
never reuse another host's authority or try to send `origin` in the tool call.
Use `recall_forget` only with the exact receipt; never approximate identity from
search text. ChatGPT needs a remote MCP or Secure MCP Tunnel adapter rather than
the local stdio configuration.

## First: pick the outcome

1. **Find / verify** — answer "did we…", "which session…", "how did we…".
   Search, read the best hit's relevant window, answer with the session path
   as the receipt.
2. **Continue** — resume in-progress work. Needs the session's tail plus its
   branch and worktree.
3. **Repeat** — redo the same kind of task with fresh inputs. Needs the
   original driving prompts, verbatim.
4. **Skill-ify** — turn the recipe into a reusable skill. Needs the steps that
   worked, minus one-off data. Chain into the harness's skill creator when one
   is installed; otherwise write the standard `SKILL.md` package directly.

Ask only if the outcome is genuinely ambiguous.

## Search

```bash
python3 scripts/recall.py search "<what the user said>" [filters]
```

- Pass the user's phrasing plus any identifier you have — identifiers (job
  UUIDs, PR numbers, pod names, error strings, filenames) are the strongest
  evidence and are matched exactly, including inside tool output.
- Filters are mechanical — apply them, never approximate them in the query
  text:
  | ask | flag |
  |---|---|
  | "last 48h", "back in May" | `--since 2026-05-01 --until 2026-06-01` (UTC, both bounds inclusive of the instant — to cover a full local day, use the NEXT day's date as `--until`; convert the user's local day first) |
  | "in the other worktree/checkout" | `--cwd <any-cwd-substring>` |
  | "what did codex do" | `--harness codex` |
  | branch-scoped | `--branch <substring>` |
- Output is ranked sessions with date, cwd, slot, branch, a matched snippet,
  and WHY it matched. Empty output means nothing cleared the evidence gate —
  NOT proof the work never happened: retry once with a distinctive identifier
  or a wider window; if still empty, tell the user what you searched and that
  the trail is cold.
- `--paths` prints bare file paths (for scripting); `--limit N` widens.

## Read the best-supported hit

Check the WHY line on the top few results first — if the matched terms are all
generic words, the evidence is weak; open the result whose WHY carries an
identifier, phrase, or exact-entity match, not blindly rank 1.

```bash
python3 scripts/recall.py show <path> --prompts                    # user prompts only
python3 scripts/recall.py show <path> --around 2026-07-03T14:20    # ±3-turn window; use the date printed in the search result
python3 scripts/recall.py show <path> --tail 30                    # the session's final turns (for Continue)
```

Pass the session file path from the search output. Never cat a transcript —
sessions reach 80 MB; `show` parses and prints only what you asked for.

## Export one exact session for another skill

Use the machine-readable session export when `/recap` or another evidence consumer needs complete,
ordered coverage rather than a human window:

```bash
python3 scripts/recall.py session-export --current --limit 1000
python3 scripts/recall.py session-export --target <exact-path-or-receipt> --limit 1000
python3 scripts/recall.py session-export --cursor <opaque-next-cursor> --limit 1000
```

Each JSON page contains stable evidence IDs, redacted text and digests, sanitized typed entities
(including native tool identity when observed), native session identity, projection/privacy
versions, a boundary receipt, a content-free page receipt, and `complete` plus `next_cursor`.
Consume pages in sequence and accept immutable-snapshot completeness only on the final page; inspect
`source_snapshot_stable` before claiming a live source did not advance. Local cursors are stored
owner-private under `~/.recall`; central cursors are random, source-authorized server state. Neither
cursor encodes transcript text or a path.

`--current` resolves Codex only through exact `CODEX_THREAD_ID`, and Claude through exact
`CLAUDE_SESSION_ID` when the harness exposes it. Otherwise it fails closed with content-free ranked
candidate receipts; pass the exact path found by Recall rather than guessing. Child and continuation
sessions are separate boundaries by default. For local/central evidence-ID parity, collectors set
the source ID; a standalone local export uses `RECALL_EXPORT_SOURCE_ID` when configured and an
explicit `local:<harness>` source otherwise.

To resolve a local native relationship graph for Recap without reading transcript prose, use:

```bash
python3 scripts/recall.py session-relations --current --include-children
python3 scripts/recall.py session-relations --target <exact-path> --chain
python3 scripts/recall.py session-relations --target <exact-path> --chain --include-children
```

The closed `recall.session-relations.v1` JSON uses Claude `sessionId`/`agentId` sidechain metadata
and Codex `parent_thread_id`/`forked_from_id` metadata. It excludes merely adjacent or similar
sessions and fails when a requested native link is missing or ambiguous. This command is local-only
until the central Recall service implements the same graph contract.

## Related work (no query needed)

```bash
python3 scripts/recall.py related --cwd "$(pwd)" --branch "$(git branch --show-current)"
```

Sessions sharing this project, branch, or touched files — ranked by overlap
and recency. Use at session start when the user references prior work without
naming it.

## Outcome playbooks

**Find / verify** — search → `show --around` the matched timestamp → answer
with evidence. Two commands, usually.

**Continue** — search → `show --tail 30` for the final state (last actions,
tool results, open errors) → check the session's branch/slot still exists
(`git -C <cwd> branch --show-current`) → summarize: "Found `<session>` in
`<cwd>` on `<date>`, last action `<x>`, branch `<b>` — resume there or here?"

**Repeat** — search → `show --prompts` → present the driving prompts verbatim
and confirm fresh inputs (dates, scope) before re-running.

**Skill-ify** — search → `show` the working window → separate the durable
recipe (commands, endpoints, auth patterns) from one-off data (specific IDs,
dates) → invoke the available skill creator with the recipe and a proposed
   name, or create a standard Agent Skills directory when none is installed.
5. **Remember / forget** — only on an explicit request, `put` the durable text
   with a provenance URI and return its receipt; `delete` that exact receipt
   when asked to forget it.

## Index health

```bash
python3 scripts/recall.py index      # incremental; run if results look stale
python3 scripts/recall.py doctor     # coverage, index age, retention watchdog
```

`doctor` warning about `cleanupPeriodDays` means transcript retention got
re-enabled — surface that to the user immediately; history is being deleted.

## Gotchas

- The engine indexes user text, assistant text, and tool input/output — but
  reasoning/thinking blocks are never stored, and secret-shaped lines are
  redacted at ingest. If the only trace of something was a thinking block, it
  is not findable.
- Codex sessions are one file per rollout under a date tree; `show` handles
  both schemas transparently.
- Running Recall from pi is supported, but pi's own session format is not yet
  indexed. Do not claim that a cold result proves no pi session exists.
- A query about work that never happened can still return lexically-adjacent
  sessions. The ranked WHY line tells you what actually matched — read it
  before asserting the session answers the question.
- Subagent and workflow transcripts are indexed as their own sessions and
  live under the parent session's directory (`<session-uuid>/subagents/…`) —
  the path itself tells you which main session spawned them.

## References

- [references/query-cookbook.md](references/query-cookbook.md) — worked
  examples per stratum: identifiers, error strings, time windows,
  cross-worktree, cross-harness, paraphrase.
