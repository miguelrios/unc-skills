BEGIN;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version integer PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sources (
    id text PRIMARY KEY,
    principal_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS source_grants (
    source_id text NOT NULL REFERENCES sources(id),
    principal_id text NOT NULL,
    permission text NOT NULL CHECK (permission IN ('owner', 'read', 'write')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(source_id, principal_id, permission)
);

CREATE TABLE IF NOT EXISTS ingest_batches (
    id uuid PRIMARY KEY,
    idempotency_key text NOT NULL UNIQUE,
    request_sha256 text NOT NULL,
    status text NOT NULL CHECK (status IN ('committed')),
    acknowledgement jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS source_events (
    id bigserial PRIMARY KEY,
    source_id text NOT NULL REFERENCES sources(id),
    native_id text NOT NULL,
    native_parent_id text,
    kind text NOT NULL,
    occurred_at timestamptz NOT NULL,
    observed_at timestamptz NOT NULL,
    principal_id text NOT NULL,
    visibility text NOT NULL,
    content_type text NOT NULL,
    content_sha256 text NOT NULL,
    revision integer NOT NULL,
    envelope jsonb NOT NULL,
    is_tombstone boolean NOT NULL DEFAULT false,
    batch_id uuid NOT NULL REFERENCES ingest_batches(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(source_id, native_id, content_sha256),
    UNIQUE(source_id, native_id, revision)
);

CREATE INDEX IF NOT EXISTS source_events_parent_idx
    ON source_events(source_id, native_parent_id, revision);
CREATE INDEX IF NOT EXISTS source_events_batch_idx ON source_events(batch_id);

CREATE TABLE IF NOT EXISTS sessions (
    source_id text NOT NULL,
    native_id text NOT NULL,
    principal_id text NOT NULL,
    harness text,
    started_at timestamptz,
    ended_at timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    projector_version integer NOT NULL,
    rebuilt_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(source_id, native_id)
);

CREATE TABLE IF NOT EXISTS items (
    id bigserial PRIMARY KEY,
    event_id bigint NOT NULL REFERENCES source_events(id) ON DELETE CASCADE,
    source_id text NOT NULL,
    session_native_id text NOT NULL,
    event_native_id text NOT NULL,
    ordinal integer NOT NULL,
    occurred_at timestamptz,
    role text,
    surface text NOT NULL,
    text_redacted text NOT NULL,
    receipt text NOT NULL UNIQUE,
    projector_version integer NOT NULL,
    deleted_at timestamptz,
    UNIQUE(event_id, ordinal)
);

CREATE INDEX IF NOT EXISTS items_session_idx
    ON items(source_id, session_native_id, occurred_at, ordinal);

CREATE TABLE IF NOT EXISTS chunks (
    id bigserial PRIMARY KEY,
    item_id bigint NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    ordinal integer NOT NULL,
    text_redacted text NOT NULL,
    receipt text NOT NULL,
    UNIQUE(item_id, ordinal)
);

CREATE INDEX IF NOT EXISTS chunks_item_idx ON chunks(item_id);

CREATE TABLE IF NOT EXISTS dead_letters (
    id bigserial PRIMARY KEY,
    batch_id uuid,
    source_id text,
    native_id text,
    error_code text NOT NULL,
    error_summary text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_events (
    id bigserial PRIMARY KEY,
    operation text NOT NULL,
    principal_id text,
    source_id text,
    native_id text,
    status text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS projection_watermarks (
    projector text PRIMARY KEY,
    version integer NOT NULL,
    last_event_id bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO schema_migrations(version) VALUES (1) ON CONFLICT DO NOTHING;
COMMIT;
