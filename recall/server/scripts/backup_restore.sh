#!/usr/bin/env bash
set -euo pipefail

MODE=${1:-}
BACKUP_DIR=${2:-}
TOOLS_IMAGE=${PG_TOOLS_IMAGE:-postgres:17-alpine}

usage() {
  echo "usage: RECALL_DATABASE_URL=... $0 backup DIR" >&2
  echo "   or: RECALL_RESTORE_DATABASE_URL=... $0 restore-test DIR" >&2
  exit 2
}

fingerprint() {
  local dsn=$1
  psql "$dsn" -At -v ON_ERROR_STOP=1 -c \
    "SELECT count(*) || ':' || COALESCE(md5(string_agg(source_id || ':' || native_id || ':' || revision || ':' || content_sha256, '|' ORDER BY id)), md5('')) FROM source_events"
}

case "$MODE" in
  backup)
    [ -n "$BACKUP_DIR" ] && [ -n "${RECALL_DATABASE_URL:-}" ] || usage
    mkdir -p "$BACKUP_DIR"
    stage=$(mktemp -d "$BACKUP_DIR/.backup.XXXXXX")
    trap 'rm -rf "$stage"' EXIT
    started=$(date -u +%s)
    started_ms=$(date -u +%s%3N)
    docker run --rm --network host -v "$stage:/backup" "$TOOLS_IMAGE" \
      pg_dump "$RECALL_DATABASE_URL" --format=custom --no-owner --file=/backup/brain.dump
    dump_sha=$(sha256sum "$stage/brain.dump" | awk '{print $1}')
    source_fingerprint=$(fingerprint "$RECALL_DATABASE_URL")
    newest_epoch=$(psql "$RECALL_DATABASE_URL" -At -c "SELECT COALESCE(extract(epoch FROM max(created_at))::bigint,0) FROM source_events")
    completed=$(date -u +%s)
    completed_ms=$(date -u +%s%3N)
    BACKUP_DIR="$stage" STARTED="$started" COMPLETED="$completed" STARTED_MS="$started_ms" COMPLETED_MS="$completed_ms" DUMP_SHA="$dump_sha" \
      SOURCE_FINGERPRINT="$source_fingerprint" NEWEST_EPOCH="$newest_epoch" TOOLS_IMAGE="$TOOLS_IMAGE" \
      python3 - <<'PY'
import json, os
from pathlib import Path
manifest = {
    "schema_version": 1,
    "started_epoch": int(os.environ["STARTED"]),
    "completed_epoch": int(os.environ["COMPLETED"]),
    "duration_seconds": int(os.environ["COMPLETED"]) - int(os.environ["STARTED"]),
    "duration_ms": int(os.environ["COMPLETED_MS"]) - int(os.environ["STARTED_MS"]),
    "newest_event_epoch": int(os.environ["NEWEST_EPOCH"]),
    "rpo_seconds_at_backup": max(0, int(os.environ["COMPLETED"]) - int(os.environ["NEWEST_EPOCH"])),
    "dump_sha256": os.environ["DUMP_SHA"],
    "source_fingerprint": os.environ["SOURCE_FINGERPRINT"],
    "pg_tools_image": os.environ["TOOLS_IMAGE"],
}
Path(os.environ["BACKUP_DIR"], "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
print(json.dumps(manifest, sort_keys=True))
PY
    mv -f "$stage/brain.dump" "$BACKUP_DIR/brain.dump"
    mv -f "$stage/manifest.json" "$BACKUP_DIR/manifest.json"
    rmdir "$stage"
    trap - EXIT
    ;;
  restore-test)
    [ -n "$BACKUP_DIR" ] && [ -n "${RECALL_RESTORE_DATABASE_URL:-}" ] || usage
    [ -f "$BACKUP_DIR/brain.dump" ] && [ -f "$BACKUP_DIR/manifest.json" ] || usage
    snapshot=$(mktemp -d "$BACKUP_DIR/.restore.XXXXXX")
    trap 'rm -rf "$snapshot"' EXIT
    ln "$BACKUP_DIR/brain.dump" "$snapshot/brain.dump"
    cp "$BACKUP_DIR/manifest.json" "$snapshot/manifest.json"
    expected_sha=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["dump_sha256"])' "$snapshot/manifest.json")
    actual_sha=$(sha256sum "$snapshot/brain.dump" | awk '{print $1}')
    [ "$expected_sha" = "$actual_sha" ] || { echo "dump hash mismatch" >&2; exit 1; }
    expected_fingerprint=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_fingerprint"])' "$snapshot/manifest.json")
    started=$(date -u +%s)
    started_ms=$(date -u +%s%3N)
    docker run --rm --network host -v "$snapshot:/backup:ro" "$TOOLS_IMAGE" \
      pg_restore --dbname="$RECALL_RESTORE_DATABASE_URL" --clean --if-exists --no-owner /backup/brain.dump
    actual_fingerprint=$(fingerprint "$RECALL_RESTORE_DATABASE_URL")
    completed=$(date -u +%s)
    completed_ms=$(date -u +%s%3N)
    [ "$expected_fingerprint" = "$actual_fingerprint" ] || { echo "restore fingerprint mismatch" >&2; exit 1; }
    EXPECTED="$expected_fingerprint" ACTUAL="$actual_fingerprint" STARTED="$started" COMPLETED="$completed" STARTED_MS="$started_ms" COMPLETED_MS="$completed_ms" python3 - <<'PY'
import json, os
print(json.dumps({
    "status": "pass",
    "expected_fingerprint": os.environ["EXPECTED"],
    "restored_fingerprint": os.environ["ACTUAL"],
    "rto_seconds": int(os.environ["COMPLETED"]) - int(os.environ["STARTED"]),
    "rto_ms": int(os.environ["COMPLETED_MS"]) - int(os.environ["STARTED_MS"]),
}, sort_keys=True))
PY
    rm -rf "$snapshot"
    trap - EXIT
    ;;
  *) usage ;;
esac
