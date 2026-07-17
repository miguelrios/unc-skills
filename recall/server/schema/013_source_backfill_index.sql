BEGIN;

CREATE INDEX IF NOT EXISTS items_live_source_id_idx
    ON items(source_id, id)
    WHERE deleted_at IS NULL AND btrim(text_redacted) <> '';

INSERT INTO schema_migrations(version) VALUES (13) ON CONFLICT DO NOTHING;

COMMIT;
