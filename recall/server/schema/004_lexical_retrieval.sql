BEGIN;

CREATE INDEX IF NOT EXISTS items_search_vector_idx
    ON items USING gin (to_tsvector('simple', text_redacted));

CREATE INDEX IF NOT EXISTS items_source_session_time_idx
    ON items(source_id, session_native_id, occurred_at, id)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS source_events_original_path_idx
    ON source_events ((envelope #>> '{provenance,original_path}'));

INSERT INTO schema_migrations(version) VALUES (4) ON CONFLICT DO NOTHING;

COMMIT;
