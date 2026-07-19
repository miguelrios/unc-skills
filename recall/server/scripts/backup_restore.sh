#!/usr/bin/env bash
set -euo pipefail
umask 077

MODE=${1:-}
BACKUP_DIR=${2:-}
TOOLS_IMAGE=${PG_TOOLS_IMAGE:-postgres:17-alpine}
DATABASE_PROFILE=${RECALL_DATABASE_PROFILE:-production}
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
LIBPQ_ENV="$SCRIPT_DIR/libpq_env.py"

usage() {
  echo "usage: RECALL_DATABASE_URL=... $0 backup DIR" >&2
  echo "   or: RECALL_RESTORE_DATABASE_URL=... $0 restore-test DIR" >&2
  exit 2
}

fingerprint() {
  local url_env=$1
  local snapshot=${2:-}
  local snapshot_sql=''
  if [ -n "$snapshot" ]; then
    snapshot_sql="SET TRANSACTION SNAPSHOT '$snapshot';"
  fi
    python3 "$LIBPQ_ENV" exec --url-env "$url_env" --profile "$DATABASE_PROFILE" -- \
    psql -XAtq -v ON_ERROR_STOP=1 <<SQL
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
$snapshot_sql
WITH fingerprints(name,row_count,digest) AS (
  SELECT 'schema_migrations',count(*),
    COALESCE(md5(string_agg(md5(row_to_json(value)::text),'' ORDER BY version)),md5(''))
    FROM schema_migrations value
  UNION ALL SELECT 'source_events',count(*),
    COALESCE(md5(string_agg(md5(row_to_json(value)::text),'' ORDER BY id)),md5(''))
    FROM source_events value
  UNION ALL SELECT 'brain_tenants',count(*),
    COALESCE(md5(string_agg(md5(row_to_json(value)::text),'' ORDER BY tenant_id)),md5(''))
    FROM brain_tenants value
  UNION ALL SELECT 'brain_principals',count(*),
    COALESCE(md5(string_agg(md5(row_to_json(value)::text),'' ORDER BY tenant_id,principal_id)),md5(''))
    FROM brain_principals value
  UNION ALL SELECT 'canonical_sources',count(*),
    COALESCE(md5(string_agg(md5(row_to_json(value)::text),'' ORDER BY tenant_id,source_id)),md5(''))
    FROM canonical_sources value
  UNION ALL SELECT 'raw_artifacts',count(*),
    COALESCE(md5(string_agg(md5(row_to_json(value)::text),'' ORDER BY tenant_id,source_id,artifact_id)),md5(''))
    FROM raw_artifacts value
  UNION ALL SELECT 'canonical_ingest_jobs',count(*),
    COALESCE(md5(string_agg(md5(row_to_json(value)::text),'' ORDER BY tenant_id,source_id,job_id)),md5(''))
    FROM canonical_ingest_jobs value
  UNION ALL SELECT 'canonical_events',count(*),
    COALESCE(md5(string_agg(md5(row_to_json(value)::text),'' ORDER BY tenant_id,source_id,event_id)),md5(''))
    FROM canonical_events value
  UNION ALL SELECT 'canonical_documents',count(*),
    COALESCE(md5(string_agg(md5(row_to_json(value)::text),'' ORDER BY tenant_id,source_id,document_id)),md5(''))
    FROM canonical_documents value
  UNION ALL SELECT 'canonical_chunks',count(*),
    COALESCE(md5(string_agg(md5(row_to_json(value)::text),'' ORDER BY tenant_id,source_id,chunk_id)),md5(''))
    FROM canonical_chunks value
  UNION ALL SELECT 'receipt_redirects',count(*),
    COALESCE(md5(string_agg(md5(row_to_json(value)::text),'' ORDER BY tenant_id,old_receipt)),md5(''))
    FROM receipt_redirects value
  UNION ALL SELECT 'forget_tombstones',count(*),
    COALESCE(md5(string_agg(md5(row_to_json(value)::text),'' ORDER BY tenant_id,source_id,target_identity_sha256)),md5(''))
    FROM forget_tombstones value
  UNION ALL SELECT 'canonical_audit_events',count(*),
    COALESCE(md5(string_agg(md5(row_to_json(value)::text),'' ORDER BY tenant_id,source_id,audit_id)),md5(''))
    FROM canonical_audit_events value
)
SELECT COALESCE(sum(row_count),0) || ':' ||
       md5(string_agg(name || ':' || row_count || ':' || digest,'|' ORDER BY name))
FROM fingerprints;
COMMIT;
SQL
}

snapshot_newest_epoch() {
  local url_env=$1 snapshot=$2
  python3 "$LIBPQ_ENV" exec --url-env "$url_env" --profile "$DATABASE_PROFILE" -- \
    psql -XAtq -v ON_ERROR_STOP=1 <<SQL
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET TRANSACTION SNAPSHOT '$snapshot';
SELECT GREATEST(
  COALESCE((SELECT extract(epoch FROM max(created_at))::bigint FROM source_events),0),
  COALESCE((SELECT extract(epoch FROM max(created_at))::bigint FROM canonical_events),0)
);
COMMIT;
SQL
}

case "$MODE" in
  backup)
    [ -n "$BACKUP_DIR" ] && [ -n "${RECALL_DATABASE_URL:-}" ] || usage
    mkdir -p "$BACKUP_DIR"
    chmod 700 "$BACKUP_DIR"
    stage=$(mktemp -d "$BACKUP_DIR/.backup.XXXXXX")
    snapshot_pid=''
    snapshot_release="$stage/snapshot-release"
    snapshot_output="$stage/snapshot-id"
    mkfifo "$snapshot_release"
    exec 9<>"$snapshot_release"
    cleanup_backup() {
      if [ -n "$snapshot_pid" ]; then
        printf 'release\n' >&9 || true
        wait "$snapshot_pid" 2>/dev/null || true
      fi
      exec 9>&- 9<&- || true
      rm -rf "$stage"
    }
    trap cleanup_backup EXIT
    started=$(date -u +%s)
    started_ms=$(date -u +%s%3N)
    (
      python3 "$LIBPQ_ENV" exec --url-env RECALL_DATABASE_URL --profile "$DATABASE_PROFILE" -- \
        psql -XAtq -v ON_ERROR_STOP=1 <<SQL
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SELECT pg_export_snapshot();
\! read ignored < "$snapshot_release"
ROLLBACK;
SQL
    ) >"$snapshot_output" &
    snapshot_pid=$!
    for _ in $(seq 1 100); do
      [ -s "$snapshot_output" ] && break
      kill -0 "$snapshot_pid" 2>/dev/null || { wait "$snapshot_pid"; exit 1; }
      sleep 0.05
    done
    read -r database_snapshot < "$snapshot_output"
    case "$database_snapshot" in ''|*[!A-Fa-f0-9-]*) echo "invalid exported database snapshot" >&2; exit 1;; esac
    database_fingerprint=$(fingerprint RECALL_DATABASE_URL "$database_snapshot")
    newest_epoch=$(snapshot_newest_epoch RECALL_DATABASE_URL "$database_snapshot")
    python3 "$LIBPQ_ENV" write --url-env RECALL_DATABASE_URL --profile "$DATABASE_PROFILE" \
      --output "$stage/libpq.env"
    docker run --rm --network host --env-file "$stage/libpq.env" "$TOOLS_IMAGE" \
      pg_dump --snapshot="$database_snapshot" --format=custom --no-owner >"$stage/brain.dump"
    printf 'release\n' >&9
    wait "$snapshot_pid"
    snapshot_pid=''
    dump_sha=$(sha256sum "$stage/brain.dump" | awk '{print $1}')
    completed=$(date -u +%s)
    completed_ms=$(date -u +%s%3N)
    BACKUP_DIR="$stage" STARTED="$started" COMPLETED="$completed" STARTED_MS="$started_ms" COMPLETED_MS="$completed_ms" DUMP_SHA="$dump_sha" \
      DATABASE_FINGERPRINT="$database_fingerprint" NEWEST_EPOCH="$newest_epoch" TOOLS_IMAGE="$TOOLS_IMAGE" DATABASE_SNAPSHOT="$database_snapshot" \
      python3 - <<'PY'
import json, os
from pathlib import Path
manifest = {
    "schema_version": 2,
    "started_epoch": int(os.environ["STARTED"]),
    "completed_epoch": int(os.environ["COMPLETED"]),
    "duration_seconds": int(os.environ["COMPLETED"]) - int(os.environ["STARTED"]),
    "duration_ms": int(os.environ["COMPLETED_MS"]) - int(os.environ["STARTED_MS"]),
    "newest_event_epoch": int(os.environ["NEWEST_EPOCH"]),
    "rpo_seconds_at_backup": max(0, int(os.environ["COMPLETED"]) - int(os.environ["NEWEST_EPOCH"])),
    "dump_sha256": os.environ["DUMP_SHA"],
    "database_fingerprint": os.environ["DATABASE_FINGERPRINT"],
    "database_snapshot": os.environ["DATABASE_SNAPSHOT"],
    "pg_tools_image": os.environ["TOOLS_IMAGE"],
}
Path(os.environ["BACKUP_DIR"], "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
print(json.dumps(manifest, sort_keys=True))
PY
    mv -f "$stage/brain.dump" "$BACKUP_DIR/brain.dump"
    mv -f "$stage/manifest.json" "$BACKUP_DIR/manifest.json"
    chmod 600 "$BACKUP_DIR/brain.dump" "$BACKUP_DIR/manifest.json"
    rm -f "$stage/snapshot-release" "$stage/snapshot-id" "$stage/libpq.env"
    rmdir "$stage"
    exec 9>&- 9<&-
    trap - EXIT
    ;;
  restore-test)
    [ -n "$BACKUP_DIR" ] && [ -n "${RECALL_RESTORE_DATABASE_URL:-}" ] || usage
    [ -f "$BACKUP_DIR/brain.dump" ] && [ -f "$BACKUP_DIR/manifest.json" ] || usage
    snapshot=$(mktemp -d "$BACKUP_DIR/.restore.XXXXXX")
    trap 'rm -rf "$snapshot"' EXIT
    cp --reflink=auto -- "$BACKUP_DIR/brain.dump" "$snapshot/brain.dump"
    cp "$BACKUP_DIR/manifest.json" "$snapshot/manifest.json"
    expected_sha=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["dump_sha256"])' "$snapshot/manifest.json")
    actual_sha=$(sha256sum "$snapshot/brain.dump" | awk '{print $1}')
    [ "$expected_sha" = "$actual_sha" ] || { echo "dump hash mismatch" >&2; exit 1; }
    expected_fingerprint=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["database_fingerprint"])' "$snapshot/manifest.json")
    started=$(date -u +%s)
    started_ms=$(date -u +%s%3N)
    python3 "$LIBPQ_ENV" write --url-env RECALL_RESTORE_DATABASE_URL --profile "$DATABASE_PROFILE" \
      --output "$snapshot/libpq.env"
    docker run --rm --network host --env-file "$snapshot/libpq.env" \
      -v "$snapshot:/backup:ro" "$TOOLS_IMAGE" \
      sh -c 'exec pg_restore --dbname="$PGDATABASE" --clean --if-exists --no-owner /backup/brain.dump'
    actual_fingerprint=$(fingerprint RECALL_RESTORE_DATABASE_URL)
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
