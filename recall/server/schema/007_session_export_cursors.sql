BEGIN;

CREATE TABLE IF NOT EXISTS session_export_cursors (
    token_sha256 text PRIMARY KEY,
    source_id text NOT NULL,
    session_native_id text NOT NULL,
    snapshot_max_item_id bigint NOT NULL,
    snapshot_at timestamptz NOT NULL,
    after_event_native_id text NOT NULL DEFAULT '',
    after_ordinal integer NOT NULL DEFAULT -1,
    after_item_id bigint NOT NULL DEFAULT 0,
    after_sequence integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS session_export_cursors_expiry_idx
    ON session_export_cursors(expires_at);

INSERT INTO schema_migrations(version) VALUES (7) ON CONFLICT DO NOTHING;
COMMIT;
