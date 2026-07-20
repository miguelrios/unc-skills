BEGIN;

ALTER TABLE connector_installations
    ADD COLUMN IF NOT EXISTS device_id text;

ALTER TABLE collector_credentials
    ADD COLUMN IF NOT EXISTS installation_id uuid
        REFERENCES connector_installations(id);

ALTER TABLE connector_installations
    DROP CONSTRAINT IF EXISTS connector_installations_tenant_id_principal_id_connector_id_key;

ALTER TABLE connector_installations
    DROP CONSTRAINT IF EXISTS connector_installations_tenant_id_source_id_key;

CREATE UNIQUE INDEX IF NOT EXISTS connector_installations_remote_route_key
    ON connector_installations(tenant_id, principal_id, connector_id)
    WHERE device_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS connector_installations_device_route_key
    ON connector_installations(principal_id, connector_id, device_id)
    WHERE device_id IS NOT NULL
      AND state NOT IN ('revoked', 'uninstalled');

CREATE UNIQUE INDEX IF NOT EXISTS connector_installations_active_source_key
    ON connector_installations(tenant_id, source_id)
    WHERE state NOT IN ('revoked', 'uninstalled');

CREATE UNIQUE INDEX IF NOT EXISTS collector_credentials_installation_key
    ON collector_credentials(installation_id)
    WHERE installation_id IS NOT NULL;

ALTER TABLE connector_installations
    DROP CONSTRAINT IF EXISTS connector_installations_device_id_check;

ALTER TABLE connector_installations
    ADD CONSTRAINT connector_installations_device_id_check CHECK (
        device_id IS NULL
        OR device_id ~ '^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$'
    );

INSERT INTO schema_migrations(version) VALUES (30) ON CONFLICT DO NOTHING;

COMMIT;
