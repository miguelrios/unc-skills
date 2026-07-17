BEGIN;

ALTER TABLE collector_credentials
    ADD COLUMN IF NOT EXISTS capture_origin text;

INSERT INTO schema_migrations(version) VALUES (16) ON CONFLICT DO NOTHING;
COMMIT;
