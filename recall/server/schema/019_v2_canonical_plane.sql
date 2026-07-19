BEGIN;

CREATE TABLE IF NOT EXISTS brain_tenants (
    tenant_id text NOT NULL PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (length(tenant_id) BETWEEN 2 AND 256)
);

CREATE TABLE IF NOT EXISTS brain_principals (
    tenant_id text NOT NULL REFERENCES brain_tenants(tenant_id),
    principal_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id, principal_id),
    CHECK (length(principal_id) BETWEEN 2 AND 256)
);

CREATE TABLE IF NOT EXISTS canonical_sources (
    tenant_id text NOT NULL,
    source_id text NOT NULL,
    owner_principal_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id, source_id),
    FOREIGN KEY(tenant_id) REFERENCES brain_tenants(tenant_id),
    FOREIGN KEY(tenant_id, owner_principal_id)
        REFERENCES brain_principals(tenant_id, principal_id),
    CHECK (length(source_id) BETWEEN 2 AND 256)
);

CREATE TABLE IF NOT EXISTS raw_artifacts (
    tenant_id text NOT NULL,
    source_id text NOT NULL,
    artifact_id text NOT NULL,
    storage_backend text NOT NULL CHECK (storage_backend IN ('filesystem', 's3')),
    object_key text NOT NULL,
    content_sha256 char(64) NOT NULL,
    size_bytes bigint NOT NULL CHECK (size_bytes >= 0 AND size_bytes <= 5368709120),
    media_type text NOT NULL,
    encryption text NOT NULL CHECK (encryption IN ('filesystem-managed', 'sse-s3', 'aws:kms')),
    version_id text NOT NULL,
    state text NOT NULL DEFAULT 'live' CHECK (state IN ('live', 'deleted')),
    created_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    PRIMARY KEY(tenant_id, source_id, artifact_id),
    UNIQUE(storage_backend, object_key, version_id),
    FOREIGN KEY(tenant_id, source_id)
        REFERENCES canonical_sources(tenant_id, source_id),
    CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (object_key ~ '^objects/[0-9a-f]{2}/[0-9a-f]{64}$'),
    CHECK ((state='live' AND deleted_at IS NULL) OR (state='deleted' AND deleted_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS canonical_ingest_jobs (
    tenant_id text NOT NULL,
    source_id text NOT NULL,
    job_id text NOT NULL,
    connector_id text NOT NULL,
    mode text NOT NULL CHECK (mode IN ('backfill', 'incremental', 'webhook', 'import')),
    status text NOT NULL CHECK (status IN ('queued', 'running', 'committed', 'failed', 'cancelled')),
    attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id, source_id, job_id),
    FOREIGN KEY(tenant_id, source_id)
        REFERENCES canonical_sources(tenant_id, source_id)
);

CREATE TABLE IF NOT EXISTS canonical_events (
    tenant_id text NOT NULL,
    source_id text NOT NULL,
    event_id text NOT NULL,
    native_id text NOT NULL,
    native_parent_id text,
    artifact_id text NOT NULL,
    job_id text NOT NULL,
    kind text NOT NULL,
    content_sha256 char(64) NOT NULL,
    revision integer NOT NULL CHECK (revision >= 1),
    occurred_at timestamptz NOT NULL,
    observed_at timestamptz NOT NULL,
    is_tombstone boolean NOT NULL DEFAULT false,
    canonical_redacted jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id, source_id, event_id),
    UNIQUE(tenant_id, source_id, native_id, revision),
    UNIQUE(tenant_id, source_id, native_id, content_sha256),
    FOREIGN KEY(tenant_id, source_id, artifact_id)
        REFERENCES raw_artifacts(tenant_id, source_id, artifact_id),
    FOREIGN KEY(tenant_id, source_id, job_id)
        REFERENCES canonical_ingest_jobs(tenant_id, source_id, job_id),
    CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (jsonb_typeof(canonical_redacted) = 'object')
);

CREATE TABLE IF NOT EXISTS canonical_documents (
    tenant_id text NOT NULL,
    source_id text NOT NULL,
    document_id text NOT NULL,
    event_id text NOT NULL,
    artifact_id text NOT NULL,
    native_id text NOT NULL,
    content_sha256 char(64) NOT NULL,
    revision integer NOT NULL CHECK (revision >= 1),
    is_current boolean NOT NULL,
    text_redacted text NOT NULL,
    text_sha256 char(64) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    PRIMARY KEY(tenant_id, source_id, document_id),
    UNIQUE(tenant_id, source_id, native_id, revision),
    FOREIGN KEY(tenant_id, source_id, event_id)
        REFERENCES canonical_events(tenant_id, source_id, event_id),
    FOREIGN KEY(tenant_id, source_id, artifact_id)
        REFERENCES raw_artifacts(tenant_id, source_id, artifact_id),
    CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (text_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK ((is_current AND deleted_at IS NULL) OR (NOT is_current))
);

CREATE UNIQUE INDEX IF NOT EXISTS canonical_documents_one_current_idx
    ON canonical_documents(tenant_id, source_id, native_id)
    WHERE is_current AND deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS canonical_chunks (
    tenant_id text NOT NULL,
    source_id text NOT NULL,
    chunk_id text NOT NULL,
    document_id text NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    receipt text NOT NULL,
    text_redacted text NOT NULL,
    text_sha256 char(64) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz,
    PRIMARY KEY(tenant_id, source_id, chunk_id),
    UNIQUE(tenant_id, receipt),
    UNIQUE(tenant_id, source_id, document_id, ordinal),
    FOREIGN KEY(tenant_id, source_id, document_id)
        REFERENCES canonical_documents(tenant_id, source_id, document_id),
    CHECK (text_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS receipt_redirects (
    tenant_id text NOT NULL,
    source_id text NOT NULL,
    old_receipt text NOT NULL,
    new_receipt text NOT NULL,
    reason text NOT NULL CHECK (reason IN ('v2_migration', 'canonical_rewrite')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id, old_receipt),
    FOREIGN KEY(tenant_id, source_id)
        REFERENCES canonical_sources(tenant_id, source_id),
    CHECK (old_receipt <> new_receipt)
);

CREATE TABLE IF NOT EXISTS forget_tombstones (
    tenant_id text NOT NULL,
    source_id text NOT NULL,
    target_identity_sha256 char(64) NOT NULL,
    mode text NOT NULL CHECK (mode IN ('authoritative_delete', 'explicit_forget')),
    reason text NOT NULL CHECK (reason IN ('source_deleted', 'owner_requested', 'retention_expired')),
    deleted_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id, source_id, target_identity_sha256),
    FOREIGN KEY(tenant_id, source_id)
        REFERENCES canonical_sources(tenant_id, source_id),
    CHECK (target_identity_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE TABLE IF NOT EXISTS canonical_audit_events (
    tenant_id text NOT NULL,
    source_id text NOT NULL,
    audit_id text NOT NULL,
    operation text NOT NULL,
    status text NOT NULL CHECK (status IN ('success', 'rejected', 'failed')),
    subject_sha256 char(64),
    item_count integer CHECK (item_count IS NULL OR item_count >= 0),
    byte_count bigint CHECK (byte_count IS NULL OR byte_count >= 0),
    duration_ms double precision
        CHECK (
            duration_ms IS NULL
            OR (duration_ms >= 0 AND duration_ms < 'Infinity'::double precision)
        ),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(tenant_id, source_id, audit_id),
    FOREIGN KEY(tenant_id, source_id)
        REFERENCES canonical_sources(tenant_id, source_id),
    CHECK (subject_sha256 IS NULL OR subject_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS canonical_events_native_idx
    ON canonical_events(tenant_id, source_id, native_id, revision DESC);
CREATE INDEX IF NOT EXISTS canonical_chunks_document_idx
    ON canonical_chunks(tenant_id, source_id, document_id, ordinal)
    WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS canonical_jobs_status_idx
    ON canonical_ingest_jobs(tenant_id, status, updated_at);

INSERT INTO schema_migrations(version) VALUES (19) ON CONFLICT DO NOTHING;

COMMIT;
