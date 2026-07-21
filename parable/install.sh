#!/usr/bin/env bash
# Manual install without npm: copy the skill into a Claude Code skills directory.
# Usage: ./install.sh [--project]   (--project installs into ./.claude/skills of the cwd)
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
chmod +x "$DEST"/scripts/*.sh "$DEST"/scripts/*.py
echo "installed skill -> $DEST"
if [ "$PROJECT" -eq 1 ] && [ ! -f "$CONF" ]; then
  mkdir -p "$(dirname "$CONF")"
  cp "$SRC/references/parable.example.toml" "$CONF"
  echo "created config  -> $CONF (edit to add providers/executors)"
elif [ "$PROJECT" -eq 0 ]; then
  echo "next: run ./bin/parable.js setup, then ./bin/parable.js claude in your project"
fi
