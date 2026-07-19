BEGIN;

CREATE INDEX IF NOT EXISTS items_live_user_session_id_idx
    ON items(source_id, session_native_id, id)
    WHERE deleted_at IS NULL AND role='user' AND btrim(text_redacted)<>'';

INSERT INTO schema_migrations(version) VALUES (22) ON CONFLICT DO NOTHING;

COMMIT;
