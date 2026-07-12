#!/usr/bin/env sh
# Manual install without npm: copy the recall skill into a Claude Code skills directory.
set -eu

PROJECT=0
HOOK=0
for arg in "$@"; do
  case "$arg" in
    --project) PROJECT=1 ;;
    --hook) HOOK=1 ;;
    *) echo "usage: $0 [--project] [--hook]" >&2; exit 2 ;;
  esac
done

SRC="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/skills/recall"
if [ "$PROJECT" -eq 1 ]; then
  DEST="$PWD/.claude/skills/recall"
else
  DEST="$HOME/.claude/skills/recall"
fi
mkdir -p "$(dirname "$DEST")"
rm -rf "$DEST"
cp -R "$SRC" "$DEST"
chmod +x "$DEST/scripts/recall.py" "$DEST/scripts/recall-hook.sh"
echo "installed skill -> $DEST"

if [ "$HOOK" -eq 1 ]; then
  echo ""
  echo "Add this SessionStart hook to your Claude settings.json:"
  echo "{"
  echo "  \"hooks\": {\"SessionStart\": [{\"hooks\": [{\"type\": \"command\", \"command\": \"$DEST/scripts/recall-hook.sh\"}]}]}"
  echo "}"
fi
