BEGIN;

CREATE INDEX IF NOT EXISTS items_live_turn_role_time_idx
    ON items(source_id, session_native_id, role, occurred_at, id)
    WHERE deleted_at IS NULL AND role IN ('user','assistant');

INSERT INTO schema_migrations(version) VALUES (17) ON CONFLICT DO NOTHING;

COMMIT;
