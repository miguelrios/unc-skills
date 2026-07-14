# Recall connector SDK

A connector fetches one bounded page. `ConnectorRunner` owns everything after
that boundary: closed-schema validation, pre-ingest privacy, a mode-0600 SQLite
outbox, Brain acknowledgement recovery, cursor commits, canonical tombstones, and
content-free doctor state.

Connectors are explicit Python objects. Recall does not discover or execute
plugins, recipes, entry points, or code from the current directory.

```python
from connectors.sdk import ConnectorPage, ConnectorRecord, ConnectorRunner

class MyConnector:
    connector_id = "example.pull"
    source_id = "example:account:mine"

    def pull(self, cursor: str | None) -> ConnectorPage:
        # Fetch with connector-owned credentials. Never put those credentials in
        # content, provenance, cursors, exceptions, or the Brain client.
        return ConnectorPage(
            records=(ConnectorRecord.from_mapping({
                "schema_version": 1,
                "native_id": "stable-upstream-id",
                "occurred_at": "2026-07-14T00:00:00Z",
                "content": {"text": "source value"},
                "provenance": {"uri": "https://source.example/item/stable-upstream-id"},
                "deleted": False,
            }),),
            next_cursor="stable-upstream-checkpoint",
            has_more=False,
        )
```

The cursor is opaque private coordination state. It is persisted only in the
private spool and is never returned by `doctor`. A page cursor commits only after
all retained records and tombstones receive a valid Brain acknowledgement. When
privacy drops every record, the runner can commit the cursor without making a
Brain request.

`ConnectorRateLimited(retry_after_seconds=...)` returns a bounded, jittered retry
hint without sleeping. All other upstream and Brain failures surface only a
stable `ConnectorRunError.error_code`; payload-bearing exception text is not
propagated. The caller owns scheduling and may log only the code and doctor
counts.

External source credentials stay inside the connector. The Brain credential is
source-scoped and stays inside its Brain client. Revoking either side leaves the
pending page and cursor recoverable. Deletion records set `deleted: true`; their
tombstones bypass contextual privacy-judge availability.

## Explicit ChatGPT/Cowork export inbox

`ExportInboxConnector` is the bundled, network-free adapter for user-supplied
OpenAI-style `conversations.json`, JSONL, and ZIP exports. It never discovers
Downloads, Desktop, application support, browser state, or private databases.
The selected directory is flat: nested roots, symlinks, hard links, archive
traversal, and malformed records fail closed.

The catalog stores only hashes, stable native IDs, timestamps, and content-free
provenance. Message bodies cross the shared privacy boundary before the durable
runner spool or Brain network. Removing a file from the inbox does not delete
central history. List its opaque export ID and explicitly queue removal instead:

```bash
recall-brain export-inbox-dry-run --inbox "$HOME/Recall Inbox" --catalog "$STATE/catalog.db"
recall-brain export-inbox-list --inbox "$HOME/Recall Inbox" --catalog "$STATE/catalog.db"
recall-brain export-inbox-remove --inbox "$HOME/Recall Inbox" --catalog "$STATE/catalog.db" exp_...
```

The next scheduled `export-inbox-sync` ACKs reference-safe tombstones. If another
active export still owns the same upstream message, that message remains live.
