BEGIN;

ALTER TABLE entities DROP CONSTRAINT IF EXISTS entities_pkey;
DROP INDEX IF EXISTS entities_normalized_source_idx;

CREATE UNIQUE INDEX IF NOT EXISTS entities_identity_hash_idx
    ON entities(item_id, kind, md5(value));

CREATE INDEX IF NOT EXISTS entities_normalized_source_idx
    ON entities(normalized text_pattern_ops, source_id)
    WHERE octet_length(normalized) <= 512;

INSERT INTO schema_migrations(version) VALUES (6) ON CONFLICT DO NOTHING;

COMMIT;
