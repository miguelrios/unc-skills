BEGIN;

ALTER TABLE connector_installations
    ADD COLUMN IF NOT EXISTS run_after timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS lease_owner text,
    ADD COLUMN IF NOT EXISTS lease_expires_at timestamptz,
    ADD COLUMN IF NOT EXISTS last_started_at timestamptz,
    ADD COLUMN IF NOT EXISTS failure_count integer NOT NULL DEFAULT 0;

ALTER TABLE connector_installations
    DROP CONSTRAINT IF EXISTS connector_installations_worker_lease_check;

ALTER TABLE connector_installations
    ADD CONSTRAINT connector_installations_worker_lease_check CHECK (
        failure_count >= 0
        AND (
            (lease_owner IS NULL AND lease_expires_at IS NULL)
            OR (
                lease_owner ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{2,127}$'
                AND lease_expires_at IS NOT NULL
            )
        )
    );

CREATE INDEX IF NOT EXISTS connector_installations_worker_due_idx
    ON connector_installations(run_after, lease_expires_at, id)
    WHERE execution = 'remote_worker' AND state = 'enabled';

INSERT INTO schema_migrations(version) VALUES (31) ON CONFLICT DO NOTHING;

COMMIT;
