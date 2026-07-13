BEGIN;

CREATE TABLE IF NOT EXISTS entities (
    item_id bigint NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    source_id text NOT NULL REFERENCES sources(id),
    kind text NOT NULL,
    value text NOT NULL,
    normalized text NOT NULL,
    PRIMARY KEY(item_id, kind, value)
);

CREATE INDEX IF NOT EXISTS entities_normalized_source_idx
    ON entities(normalized text_pattern_ops, source_id);

CREATE INDEX IF NOT EXISTS entities_item_idx ON entities(item_id);

CREATE TABLE IF NOT EXISTS projection_backfills (
    name text PRIMARY KEY,
    target_item_id bigint NOT NULL,
    last_item_id bigint NOT NULL DEFAULT 0,
    completed_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO schema_migrations(version) VALUES (5) ON CONFLICT DO NOTHING;

COMMIT;
