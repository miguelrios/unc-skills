BEGIN;

ALTER TABLE collector_credentials
    ADD COLUMN IF NOT EXISTS principal_id text;

CREATE INDEX IF NOT EXISTS source_grants_principal_permission_source_idx
    ON source_grants(principal_id, permission, source_id);

INSERT INTO schema_migrations(version) VALUES (15) ON CONFLICT DO NOTHING;
COMMIT;
