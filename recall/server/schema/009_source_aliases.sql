BEGIN;

CREATE TABLE IF NOT EXISTS source_aliases (
    alias text PRIMARY KEY CHECK (alias ~ '^[a-z0-9][a-z0-9._-]{1,63}$'),
    source_id text NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS source_aliases_source_idx ON source_aliases(source_id);

INSERT INTO schema_migrations(version) VALUES (9) ON CONFLICT DO NOTHING;

COMMIT;
