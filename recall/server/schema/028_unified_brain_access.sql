BEGIN;

CREATE TABLE IF NOT EXISTS brain_organizations (
    organization_id text PRIMARY KEY,
    organization_kind text NOT NULL
        CHECK (organization_kind IN ('personal', 'company')),
    display_name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (length(organization_id) BETWEEN 2 AND 256),
    CHECK (length(display_name) BETWEEN 1 AND 200)
);

CREATE TABLE IF NOT EXISTS brain_spaces (
    tenant_id text PRIMARY KEY REFERENCES brain_tenants(tenant_id),
    organization_id text NOT NULL
        REFERENCES brain_organizations(organization_id),
    brain_kind text NOT NULL CHECK (brain_kind IN ('personal', 'company')),
    slug text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(organization_id, slug),
    CHECK (slug ~ '^[a-z0-9][a-z0-9-]{0,62}$')
);

CREATE TABLE IF NOT EXISTS brain_memberships (
    organization_id text NOT NULL
        REFERENCES brain_organizations(organization_id),
    principal_id text NOT NULL,
    role text NOT NULL CHECK (role IN ('owner', 'admin', 'member')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(organization_id, principal_id)
);

CREATE TABLE IF NOT EXISTS brain_access_grants (
    tenant_id text NOT NULL REFERENCES brain_spaces(tenant_id),
    principal_id text NOT NULL,
    permission text NOT NULL CHECK (permission IN ('owner', 'admin', 'read')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id, principal_id),
    FOREIGN KEY(tenant_id, principal_id)
        REFERENCES brain_principals(tenant_id, principal_id)
);

CREATE TABLE IF NOT EXISTS canonical_source_grants (
    tenant_id text NOT NULL,
    principal_id text NOT NULL,
    source_id text NOT NULL,
    permission text NOT NULL CHECK (permission IN ('owner', 'read')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id, principal_id, source_id),
    FOREIGN KEY(tenant_id, principal_id)
        REFERENCES brain_principals(tenant_id, principal_id),
    FOREIGN KEY(tenant_id, source_id)
        REFERENCES canonical_sources(tenant_id, source_id)
);

CREATE INDEX IF NOT EXISTS canonical_source_grants_lookup_idx
    ON canonical_source_grants(tenant_id, principal_id, permission, source_id);

CREATE TABLE IF NOT EXISTS mcp_credentials (
    id uuid PRIMARY KEY,
    name text NOT NULL UNIQUE,
    token_sha256 char(64) NOT NULL UNIQUE,
    tenant_id text NOT NULL REFERENCES brain_spaces(tenant_id),
    principal_id text NOT NULL,
    audience text NOT NULL CHECK (audience = 'recall-mcp'),
    scopes text[] NOT NULL
        CHECK (scopes <@ ARRAY['read','forget']::text[] AND scopes @> ARRAY['read']::text[]),
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    revoked_at timestamptz,
    CHECK (expires_at > created_at),
    FOREIGN KEY(tenant_id, principal_id)
        REFERENCES brain_principals(tenant_id, principal_id)
);

CREATE INDEX IF NOT EXISTS mcp_credentials_active_idx
    ON mcp_credentials(token_sha256, audience, expires_at)
    WHERE revoked_at IS NULL;

ALTER TABLE canonical_chunks
    ADD COLUMN IF NOT EXISTS search_vector tsvector
    GENERATED ALWAYS AS (to_tsvector('simple', text_redacted)) STORED;

CREATE INDEX IF NOT EXISTS canonical_chunks_search_idx
    ON canonical_chunks USING gin(search_vector)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS canonical_chunk_embeddings (
    tenant_id text NOT NULL,
    source_id text NOT NULL,
    chunk_id text NOT NULL,
    model text NOT NULL,
    dimensions smallint NOT NULL CHECK (dimensions = 512),
    content_sha256 char(64) NOT NULL,
    runtime_fingerprint text NOT NULL,
    embedding halfvec(512) NOT NULL,
    embedded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id, source_id, chunk_id),
    FOREIGN KEY(tenant_id, source_id, chunk_id)
        REFERENCES canonical_chunks(tenant_id, source_id, chunk_id)
        ON DELETE CASCADE,
    CHECK (content_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS canonical_chunk_embeddings_scope_idx
    ON canonical_chunk_embeddings(tenant_id, source_id, chunk_id);

CREATE INDEX IF NOT EXISTS canonical_chunk_embeddings_hnsw_idx
    ON canonical_chunk_embeddings USING hnsw (embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 64);

INSERT INTO schema_migrations(version) VALUES (28) ON CONFLICT DO NOTHING;

COMMIT;
