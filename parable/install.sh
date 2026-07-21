#!/usr/bin/env bash
# Manual source install without npm. A global install copies the skill and enters
# its bundled setup; --project only copies/seeds project-local skill configuration.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)/skills/parable"
if [ "${1:-}" = "--project" ]; then
  DEST="$PWD/.claude/skills/parable"; CONF="$PWD/.claude/parable.toml"; PROJECT=1
else
  DEST="$HOME/.claude/skills/parable"; CONF=""; PROJECT=0
fi
mkdir -p "$(dirname "$DEST")"
rm -rf "$DEST"
cp -R "$SRC" "$DEST"
chmod +x "$DEST"/parable.sh "$DEST"/scripts/*.sh "$DEST"/scripts/*.py
echo "installed skill -> $DEST"
if [ "$PROJECT" -eq 1 ] && [ ! -f "$CONF" ]; then
  mkdir -p "$(dirname "$CONF")"
  cp "$SRC/references/parable.example.toml" "$CONF"
  echo "created config  -> $CONF (edit to add providers/executors)"
elif [ "$PROJECT" -eq 0 ]; then
  echo "starting bundled Parable setup -> $DEST/parable.sh"
  exec "$DEST/parable.sh" "$@"
fi
