BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS item_embeddings (
    item_id bigint PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    source_id text NOT NULL,
    model text NOT NULL,
    dimensions smallint NOT NULL CHECK (dimensions = 512),
    projector_version integer NOT NULL,
    content_sha256 char(64) NOT NULL,
    embedding halfvec(512) NOT NULL,
    embedded_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS item_embeddings_source_idx
    ON item_embeddings(source_id, item_id);

CREATE INDEX IF NOT EXISTS item_embeddings_hnsw_idx
    ON item_embeddings USING hnsw (embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 64);

INSERT INTO schema_migrations(version) VALUES (10) ON CONFLICT DO NOTHING;

COMMIT;
