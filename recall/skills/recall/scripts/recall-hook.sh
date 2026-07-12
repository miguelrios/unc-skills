#!/usr/bin/env bash
set -u

if [ "${1:-}" = "--help" ]; then
  printf '%s\n' 'Usage: recall-hook.sh [--help]' 'Reads SessionStart JSON on stdin and prints relevant prior art.'
  exit 0
fi

ENGINE="$(dirname "$0")/recall.py"
cwd="$(pwd 2>/dev/null)" || cwd="."
input="$(cat 2>/dev/null)" || input=""
if [ -n "$input" ] && command -v python3 >/dev/null 2>&1; then
  parsed="$(printf '%s' "$input" | python3 -c 'import json,sys
try:
    v=json.load(sys.stdin).get("cwd")
    print(v if isinstance(v,str) and v else "")
except Exception:
    pass' 2>/dev/null)" || parsed=""
  [ -n "$parsed" ] && cwd="$parsed"
fi

branch="$(git -C "$cwd" branch --show-current 2>/dev/null)" || branch=""

# Shallow non-git dirs (/tmp, /var, $HOME itself) are scratch space: cwd
# substring matching would surface every session rooted anywhere under them,
# which is noise at session start. Stay silent there.
if [ -z "$branch" ] && ! git -C "$cwd" rev-parse --git-dir >/dev/null 2>&1; then
  depth="$(printf '%s' "$cwd" | tr -cd '/' | wc -c)"
  [ "$depth" -lt 3 ] && exit 0
fi
related=""
if [ -f "$ENGINE" ] && command -v python3 >/dev/null 2>&1 && command -v timeout >/dev/null 2>&1; then
  related="$(timeout 1 python3 "$ENGINE" related --cwd "$cwd" ${branch:+--branch "$branch"} --limit 4 --mains-only --fast 2>/dev/null)" || related=""
fi

if [ -n "$related" ]; then
  age=""
  db="$HOME/.recall/index.db"
  if [ -f "$db" ] && command -v stat >/dev/null 2>&1; then
    mtime="$(stat -c %Y "$db" 2>/dev/null)" || mtime=""
    now="$(date +%s 2>/dev/null)" || now=""
    if [ -n "$mtime" ] && [ -n "$now" ] && [ "$now" -ge "$mtime" ] 2>/dev/null; then
      age=$(( (now - mtime) / 3600 ))
    fi
  fi
  if [ -n "$age" ]; then header="Prior art (recall index, age ${age}h):"; else header="Prior art (recall index):"; fi
  block="$header"
  n=0
  while IFS=$'\t' read -r path overlap item_cwd item_branch; do
    [ -n "$path" ] || continue
    # Last two path components distinguish pool slots (grepN/parcha) where a
    # plain basename would collapse them all to the repo name.
    slot="$(printf '%s' "${item_cwd:-$cwd}" | awk -F/ 'NF>1{print $(NF-1)"/"$NF} NF<=1{print $0}')" || slot="."
    [ -n "$item_branch" ] || item_branch="$branch"
    block="$block
- $slot $item_branch: $path"
    n=$((n + 1))
    [ "$n" -ge 5 ] && break
  done <<EOF
$related
EOF
  # Whole lines only under the byte cap — a mid-path clip reads as corruption.
  printf '%s\n' "$block" | awk '{total += length($0) + 1; if (total > 800) exit; print}'
fi

lock="$HOME/.recall/.index.lock"
stamp="$HOME/.recall/last-hook-index"
mkdir -p "$HOME/.recall" 2>/dev/null || exit 0
# Stale-lock recovery: a crash mid-index must not disable delta-indexing
# forever. Locks older than 30 minutes are reclaimed.
if [ -d "$lock" ] && command -v stat >/dev/null 2>&1; then
  lm="$(stat -c %Y "$lock" 2>/dev/null)" || lm=""
  ln="$(date +%s 2>/dev/null)" || ln=""
  [ -n "$lm" ] && [ -n "$ln" ] && [ $((ln - lm)) -gt 1800 ] && rmdir "$lock" 2>/dev/null
fi
if ! mkdir "$lock" 2>/dev/null; then exit 0; fi
skip=0
if [ -f "$stamp" ] && command -v stat >/dev/null 2>&1; then
  sm="$(stat -c %Y "$stamp" 2>/dev/null)" || sm=""
  sn="$(date +%s 2>/dev/null)" || sn=""
  [ -n "$sm" ] && [ -n "$sn" ] && { [ "$sn" -lt "$sm" ] || [ $((sn - sm)) -lt 600 ]; } && skip=1
fi
if [ "$skip" -eq 0 ] && [ -f "$ENGINE" ] && command -v python3 >/dev/null 2>&1 && command -v setsid >/dev/null 2>&1; then
  setsid nohup bash -c 'e=$1; l=$2; s=$3; trap '\''rmdir "$l" 2>/dev/null; date +%s > "$s"'\'' EXIT; python3 "$e" index >/dev/null 2>&1' _ "$ENGINE" "$lock" "$stamp" </dev/null >/dev/null 2>&1 &
else
  rmdir "$lock" 2>/dev/null
fi
exit 0
