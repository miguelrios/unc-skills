BEGIN;

CREATE TABLE IF NOT EXISTS admin_credentials (
    id uuid PRIMARY KEY,
    name text NOT NULL UNIQUE,
    token_sha256 char(64) NOT NULL UNIQUE,
    principal_id text NOT NULL,
    audience text NOT NULL CHECK (audience = 'recall-admin'),
    scopes text[] NOT NULL
        CHECK (scopes <@ ARRAY['manage']::text[] AND scopes @> ARRAY['manage']::text[]),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz,
    CHECK (length(principal_id) BETWEEN 2 AND 256),
    CHECK (expires_at > created_at)
);

CREATE INDEX IF NOT EXISTS admin_credentials_active_idx
    ON admin_credentials(token_sha256, audience, expires_at)
    WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS admin_sessions (
    id uuid PRIMARY KEY,
    credential_id uuid NOT NULL REFERENCES admin_credentials(id),
    session_sha256 char(64) NOT NULL UNIQUE,
    csrf_sha256 char(64) NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz,
    CHECK (expires_at > created_at)
);

CREATE INDEX IF NOT EXISTS admin_sessions_active_idx
    ON admin_sessions(session_sha256, expires_at)
    WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS provider_connections (
    id uuid PRIMARY KEY,
    principal_id text NOT NULL,
    provider text NOT NULL,
    subject_id text NOT NULL,
    status text NOT NULL
        CHECK (status IN ('connected','degraded','revoked')),
    granted_scopes text[] NOT NULL,
    encrypted_credentials bytea NOT NULL,
    encryption_key_id text NOT NULL,
    expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz,
    UNIQUE(principal_id, provider, subject_id),
    UNIQUE(id, principal_id),
    CHECK (provider ~ '^[a-z][a-z0-9_-]{1,63}$'),
    CHECK (length(subject_id) BETWEEN 1 AND 256),
    CHECK (length(encryption_key_id) BETWEEN 1 AND 128)
);

CREATE TABLE IF NOT EXISTS connector_installations (
    id uuid PRIMARY KEY,
    tenant_id text NOT NULL REFERENCES brain_spaces(tenant_id),
    principal_id text NOT NULL,
    connector_id text NOT NULL,
    source_id text NOT NULL,
    connection_id uuid REFERENCES provider_connections(id),
    execution text NOT NULL
        CHECK (execution IN ('source_local','remote_worker')),
    state text NOT NULL
        CHECK (state IN ('configured','enabled','paused','revoked','uninstalled')),
    privacy_mode text NOT NULL CHECK (privacy_mode IN ('scrub','drop')),
    selectors jsonb NOT NULL DEFAULT '{}'::jsonb,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision >= 1),
    last_error_code text,
    last_success_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(tenant_id, principal_id, connector_id),
    UNIQUE(tenant_id, source_id),
    FOREIGN KEY(tenant_id, principal_id)
        REFERENCES brain_access_grants(tenant_id, principal_id),
    FOREIGN KEY(connection_id, principal_id)
        REFERENCES provider_connections(id, principal_id),
    CHECK (connector_id ~ '^[a-z][a-z0-9._-]{2,127}$'),
    CHECK (length(source_id) BETWEEN 3 AND 256),
    CHECK (jsonb_typeof(selectors) = 'object'),
    CHECK (
        last_error_code IS NULL
        OR last_error_code ~ '^[a-z][a-z0-9_]{2,127}$'
    )
);

CREATE INDEX IF NOT EXISTS connector_installations_control_idx
    ON connector_installations(principal_id, tenant_id, state, connector_id);

CREATE TABLE IF NOT EXISTS oauth_sessions (
    state_sha256 char(64) PRIMARY KEY,
    principal_id text NOT NULL,
    provider text NOT NULL,
    encrypted_context bytea NOT NULL,
    encryption_key_id text NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    consumed_at timestamptz,
    CHECK (provider ~ '^[a-z][a-z0-9_-]{1,63}$'),
    CHECK (length(encryption_key_id) BETWEEN 1 AND 128),
    CHECK (expires_at > created_at)
);

CREATE TABLE IF NOT EXISTS control_audit_events (
    id uuid PRIMARY KEY,
    principal_id text NOT NULL,
    operation text NOT NULL,
    status text NOT NULL CHECK (status IN ('success','denied','failed')),
    target_sha256 char(64),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (operation ~ '^[a-z][a-z0-9._-]{2,127}$')
);

INSERT INTO schema_migrations(version) VALUES (29) ON CONFLICT DO NOTHING;

COMMIT;
