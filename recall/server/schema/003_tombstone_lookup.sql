BEGIN;

CREATE INDEX IF NOT EXISTS items_source_event_idx
    ON items(source_id, event_native_id);

INSERT INTO schema_migrations(version) VALUES (3) ON CONFLICT DO NOTHING;

COMMIT;
