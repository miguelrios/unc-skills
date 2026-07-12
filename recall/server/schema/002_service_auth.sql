BEGIN;

CREATE TABLE IF NOT EXISTS collector_credentials (
    id uuid PRIMARY KEY,
    name text NOT NULL UNIQUE,
    token_sha256 text NOT NULL UNIQUE,
    source_id text,
    scopes text[] NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz
);

CREATE INDEX IF NOT EXISTS collector_credentials_active_idx
    ON collector_credentials(token_sha256) WHERE revoked_at IS NULL;

INSERT INTO schema_migrations(version) VALUES (2) ON CONFLICT DO NOTHING;
COMMIT;
