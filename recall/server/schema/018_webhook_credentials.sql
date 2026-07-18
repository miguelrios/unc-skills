BEGIN;

ALTER TABLE collector_credentials
    ADD COLUMN IF NOT EXISTS webhook_privacy_mode text;

INSERT INTO schema_migrations(version) VALUES (18) ON CONFLICT DO NOTHING;

COMMIT;
