BEGIN;

ALTER TABLE item_embeddings
    ADD COLUMN IF NOT EXISTS runtime_fingerprint char(64);

UPDATE item_embeddings
SET runtime_fingerprint = repeat('0', 64)
WHERE runtime_fingerprint IS NULL;

ALTER TABLE item_embeddings
    ALTER COLUMN runtime_fingerprint SET NOT NULL;

INSERT INTO schema_migrations(version) VALUES (11) ON CONFLICT DO NOTHING;

COMMIT;
