BEGIN;

WITH ranked AS (
    SELECT
        embedding.anchor_item_id,
        row_number() OVER (
            PARTITION BY
                embedding.source_id,
                embedding.session_native_id,
                anchor.text_redacted
            ORDER BY embedding.anchor_item_id DESC
        ) AS position
    FROM turn_embeddings embedding
    JOIN items anchor ON anchor.id=embedding.anchor_item_id
)
DELETE FROM turn_embeddings duplicate
USING ranked
WHERE duplicate.anchor_item_id=ranked.anchor_item_id
  AND ranked.position>1;

INSERT INTO schema_migrations(version) VALUES (25) ON CONFLICT DO NOTHING;

COMMIT;
