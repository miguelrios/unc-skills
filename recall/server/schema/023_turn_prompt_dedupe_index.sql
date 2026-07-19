BEGIN;

CREATE INDEX IF NOT EXISTS turn_embeddings_source_session_anchor_idx
    ON turn_embeddings(source_id, session_native_id, anchor_item_id);

INSERT INTO schema_migrations(version) VALUES (23) ON CONFLICT DO NOTHING;

COMMIT;
