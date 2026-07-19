BEGIN;

INSERT INTO turn_embedding_dirty_sessions(
    source_id,session_native_id,last_anchor_item_id,updated_at
)
SELECT
    embedding.source_id,
    embedding.session_native_id,
    0,
    now()
FROM turn_embeddings embedding
JOIN items anchor ON anchor.id=embedding.anchor_item_id
GROUP BY embedding.source_id,embedding.session_native_id
HAVING count(*)>count(DISTINCT anchor.text_redacted)
ON CONFLICT(source_id,session_native_id) DO UPDATE SET
    last_anchor_item_id=0,
    updated_at=excluded.updated_at;

INSERT INTO schema_migrations(version) VALUES (24) ON CONFLICT DO NOTHING;

COMMIT;
