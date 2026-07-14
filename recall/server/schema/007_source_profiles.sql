BEGIN;

CREATE TABLE IF NOT EXISTS source_profiles (
    source_id text PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
    family text NOT NULL CHECK (family IN (
        'coding_history', 'deliberate_capture', 'user_export',
        'third_party_research'
    )),
    quality text NOT NULL CHECK (quality IN (
        'unrated', 'standard', 'trusted', 'authoritative'
    )),
    freshness_half_life_days integer NOT NULL
        CHECK (freshness_half_life_days BETWEEN 1 AND 3650),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS source_profiles_family_quality_idx
    ON source_profiles(family, quality);

INSERT INTO schema_migrations(version) VALUES (7) ON CONFLICT DO NOTHING;
COMMIT;
