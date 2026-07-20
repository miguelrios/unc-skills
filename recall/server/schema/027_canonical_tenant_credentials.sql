BEGIN;

ALTER TABLE collector_credentials
    ADD COLUMN IF NOT EXISTS tenant_id text;

ALTER TABLE collector_credentials
    DROP CONSTRAINT IF EXISTS collector_credentials_tenant_id_check;

ALTER TABLE collector_credentials
    ADD CONSTRAINT collector_credentials_tenant_id_check
    CHECK (
        tenant_id IS NULL
        OR (
            length(tenant_id) BETWEEN 2 AND 256
            AND tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9:._/@+-]{1,255}$'
        )
    );

CREATE INDEX IF NOT EXISTS collector_credentials_tenant_source_idx
    ON collector_credentials(tenant_id, source_id)
    WHERE revoked_at IS NULL AND tenant_id IS NOT NULL;

INSERT INTO schema_migrations(version) VALUES (27) ON CONFLICT DO NOTHING;
COMMIT;
