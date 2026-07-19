BEGIN;

UPDATE items item
SET deleted_at = now()
FROM source_events event
WHERE event.id = item.event_id
  AND item.deleted_at IS NULL
  AND EXISTS (
    SELECT 1
    FROM source_events newer
    WHERE newer.source_id = event.source_id
      AND newer.native_id = event.native_id
      AND newer.revision > event.revision
  );

INSERT INTO schema_migrations(version) VALUES (26) ON CONFLICT DO NOTHING;

COMMIT;
