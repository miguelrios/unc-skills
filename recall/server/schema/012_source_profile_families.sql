BEGIN;

ALTER TABLE source_profiles
    DROP CONSTRAINT IF EXISTS source_profiles_family_check;

ALTER TABLE source_profiles
    ADD CONSTRAINT source_profiles_family_check CHECK (family IN (
        'coding_history', 'deliberate_capture', 'user_export',
        'third_party_research', 'communications', 'schedule', 'contacts',
        'social', 'documents', 'work_activity', 'local_activity',
        'personal_media'
    ));

INSERT INTO schema_migrations(version) VALUES (12) ON CONFLICT DO NOTHING;
COMMIT;
