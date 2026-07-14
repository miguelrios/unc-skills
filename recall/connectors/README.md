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
