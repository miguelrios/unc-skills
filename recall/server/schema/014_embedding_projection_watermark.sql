BEGIN;

CREATE TABLE IF NOT EXISTS embedding_projection_watermarks (
    runtime_fingerprint char(64) PRIMARY KEY,
    model text NOT NULL,
    dimensions smallint NOT NULL CHECK (dimensions = 512),
    last_item_id bigint NOT NULL DEFAULT 0 CHECK (last_item_id >= 0),
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO schema_migrations(version) VALUES (14) ON CONFLICT DO NOTHING;

COMMIT;
