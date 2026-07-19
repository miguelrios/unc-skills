BEGIN;

CREATE TABLE IF NOT EXISTS turn_embeddings (
    anchor_item_id bigint PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    response_item_id bigint NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    source_id text NOT NULL,
    session_native_id text NOT NULL,
    model text NOT NULL,
    dimensions smallint NOT NULL CHECK (dimensions = 512),
    content_sha256 char(64) NOT NULL,
    runtime_fingerprint char(64) NOT NULL,
    embedding halfvec(512) NOT NULL,
    embedded_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS turn_embedding_items (
    anchor_item_id bigint NOT NULL
        REFERENCES turn_embeddings(anchor_item_id) ON DELETE CASCADE,
    item_id bigint NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    PRIMARY KEY(anchor_item_id, item_id)
);

CREATE TABLE IF NOT EXISTS turn_embedding_projection_watermarks (
    runtime_fingerprint char(64) PRIMARY KEY,
    model text NOT NULL,
    dimensions smallint NOT NULL CHECK (dimensions = 512),
    last_anchor_item_id bigint NOT NULL DEFAULT 0
        CHECK (last_anchor_item_id >= 0),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS turn_embedding_dirty_sessions (
    source_id text NOT NULL,
    session_native_id text NOT NULL,
    last_anchor_item_id bigint NOT NULL DEFAULT 0
        CHECK (last_anchor_item_id >= 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(source_id, session_native_id)
);

CREATE OR REPLACE FUNCTION mark_turn_embedding_session_dirty()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
DECLARE
    dirty_source_id text;
    dirty_session_native_id text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        dirty_source_id := OLD.source_id;
        dirty_session_native_id := OLD.session_native_id;
    ELSE
        dirty_source_id := NEW.source_id;
        dirty_session_native_id := NEW.session_native_id;
    END IF;
    INSERT INTO public.turn_embedding_dirty_sessions(
        source_id,session_native_id,last_anchor_item_id,updated_at
    ) VALUES (dirty_source_id,dirty_session_native_id,0,now())
    ON CONFLICT(source_id,session_native_id) DO UPDATE SET
        last_anchor_item_id=0,
        updated_at=excluded.updated_at;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END
$$;

DROP TRIGGER IF EXISTS items_mark_turn_embedding_session_dirty ON items;
CREATE TRIGGER items_mark_turn_embedding_session_dirty
AFTER INSERT OR DELETE OR UPDATE OF deleted_at ON items
FOR EACH ROW EXECUTE FUNCTION mark_turn_embedding_session_dirty();

CREATE INDEX IF NOT EXISTS turn_embeddings_source_anchor_idx
    ON turn_embeddings(source_id, anchor_item_id);

CREATE INDEX IF NOT EXISTS turn_embeddings_runtime_anchor_idx
    ON turn_embeddings(runtime_fingerprint, anchor_item_id);

CREATE INDEX IF NOT EXISTS turn_embeddings_hnsw_idx
    ON turn_embeddings USING hnsw (embedding halfvec_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS turn_embedding_items_item_idx
    ON turn_embedding_items(item_id, anchor_item_id);

CREATE INDEX IF NOT EXISTS turn_embedding_dirty_sessions_updated_idx
    ON turn_embedding_dirty_sessions(updated_at, source_id, session_native_id);

INSERT INTO schema_migrations(version) VALUES (21) ON CONFLICT DO NOTHING;

COMMIT;
