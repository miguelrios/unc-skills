#!/usr/bin/env bash
set -euo pipefail
umask 077

archive_root=${HOME}/archives
log_file=${archive_root}/archive.log

usage() {
  cat <<'EOF'
Usage: recall-archive.sh [--restore-test|--help]

With no option, archive Claude and Codex state under $HOME/archives.
--restore-test compares three random archived transcript files with source files.
EOF
}

utc_now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

fail() {
  local stage=$1 detail=$2
  mkdir -p "$archive_root" 2>/dev/null || true
  printf '%s FAIL %s: %s\n' "$(utc_now)" "$stage" "$detail" >>"$log_file" 2>/dev/null || true
  printf 'FAIL %s: %s\n' "$stage" "$detail" >&2
  exit 1
}

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

restore_test() {
  local tree=${archive_root}/claude-transcripts file rel source checked=0 skipped=0
  local -a picked=()
  if [[ ! -d $tree ]]; then
    printf 'No archived transcript tree found; checked 0, skipped 0.\n'
    return 0
  fi
  while IFS= read -r -d '' file; do picked+=("$file"); done < <(find "$tree" -type f -print0 | shuf -z -n 3)
  for file in "${picked[@]}"; do
    rel=${file#"$tree"/}
    source=${HOME}/.claude/projects/${rel#projects/}
    if [[ ! -f $source ]]; then
      printf 'SKIP missing source: %s\n' "$rel"
      ((skipped+=1))
      continue
    fi
    if ! cmp -s "$file" "$source"; then
      printf 'CONTENT mismatch: archive=%s source=%s\n' "$file" "$source" >&2
      cmp "$file" "$source" >&2 || true
      return 1
    fi
    printf 'MATCH: %s\n' "$rel"
    ((checked+=1))
  done
  printf 'Restore test complete: checked %d, skipped %d.\n' "$checked" "$skipped"
}

case ${1-} in
  --help) usage; exit 0 ;;
  --restore-test) restore_test; exit $? ;;
  "") ;;
  *) usage >&2; exit 2 ;;
esac

mkdir -p "$archive_root/manifests" || fail setup "cannot create archive directories"
chmod 700 "$archive_root" "$archive_root/manifests" \
  || fail setup "cannot secure archive directories"
labels=(projects sessions todos shell-snapshots)
sources=("$HOME/.claude/projects" "$HOME/.codex/sessions" "$HOME/.claude/todos" "$HOME/.claude/shell-snapshots")
dests=("$archive_root/claude-transcripts/projects" "$archive_root/codex-sessions" "$archive_root/claude-todos" "$archive_root/claude-shell-snapshots")
manifest=${archive_root}/manifests/$(date -u +%Y-%m-%dT%H%M%SZ).json
source_count=0 total_files=0 total_bytes=0 samples_ok=true

printf '{"generated_at":"%s","sources":[' "$(utc_now)" >"$manifest" || fail manifest "cannot write $manifest"
for i in "${!labels[@]}"; do
  src=${sources[$i]} dst=${dests[$i]}
  if [[ ! -d $src ]]; then
    printf 'Skipping missing source: %s\n' "$src" >&2
    continue
  fi
  mkdir -p "$dst" || fail setup "cannot create $dst"
  if ! rsync -a "$src/" "$dst/"; then fail rsync "$src -> $dst"; fi
  # Sample only files quiet for >60min: live transcripts append between the
  # rsync and the hash, and a hot file would flag a false mismatch.
  files=()
  while IFS= read -r -d '' file; do files+=("$file"); done < <(find "$src" -type f -mmin +60 -print0 | sort -z)
  count=$(find "$src" -type f | wc -l)
  bytes=$(find "$src" -type f -printf '%s\n' | awk '{s+=$1} END {print s+0}') || fail manifest "cannot count $src"
  source_count=$((source_count + 1))
  total_files=$((total_files + count))
  total_bytes=$((total_bytes + bytes))
  ((source_count > 1)) && printf ',' >>"$manifest"
  printf '{"name":"%s","source":"%s","destination":"%s","file_count":%d,"total_bytes":%d,"samples":[' \
    "$(json_escape "${labels[$i]}")" "$(json_escape "$src")" "$(json_escape "$dst")" "$count" "$bytes" >>"$manifest"
  stable=${#files[@]}
  sample_count=$((stable < 5 ? stable : 5))
  for ((j=0; j<sample_count; j++)); do
    idx=$((j * stable / sample_count)); file=${files[$idx]}; rel=${file#"$src"/}; copy=${dst}/${rel}
    src_hash=$(sha256sum "$file" | awk '{print $1}') || fail manifest "hash $file"
    copy_hash=missing; match=false
    if [[ -f $copy ]]; then
      copy_hash=$(sha256sum "$copy" | awk '{print $1}') || fail manifest "hash $copy"
      [[ $src_hash == "$copy_hash" ]] && match=true || samples_ok=false
    else
      samples_ok=false
    fi
    ((j > 0)) && printf ',' >>"$manifest"
    printf '{"file":"%s","source_sha256":"%s","archive_sha256":"%s","samples_match":%s}' "$(json_escape "$rel")" "$src_hash" "$copy_hash" "$match" >>"$manifest"
  done
  printf ']}' >>"$manifest"
done
printf ']}\n' >>"$manifest" || fail manifest "cannot finish $manifest"
printf '%s ok sources=%d files=%d bytes=%d samples_ok=%s\n' "$(utc_now)" "$source_count" "$total_files" "$total_bytes" "$samples_ok" >>"$log_file" || fail log "cannot write $log_file"
if [[ $samples_ok != true ]]; then fail samples "one or more archive samples did not match"; fi
