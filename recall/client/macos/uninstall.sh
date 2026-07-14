#!/bin/sh
set -eu

PREFIX="$HOME/Library/Application Support/RecallBrain"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
NO_LOAD=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX=$2; shift 2 ;;
    --launch-agents) LAUNCH_AGENTS=$2; shift 2 ;;
    --no-load) NO_LOAD=1; shift ;;
    *) echo "usage: uninstall.sh [--prefix PATH] [--launch-agents PATH] [--no-load]" >&2; exit 2 ;;
  esac
done

stop_launch_agent() {
  TARGET="gui/$(id -u)/$1"
  launchctl bootout "$TARGET" >/dev/null 2>&1 || true
  ATTEMPTS=0
  while launchctl print "$TARGET" >/dev/null 2>&1; do
    ATTEMPTS=$((ATTEMPTS + 1))
    if [ "$ATTEMPTS" -ge 100 ]; then
      echo "launch agent stop did not converge" >&2
      return 1
    fi
    sleep 0.1
  done
}

for HARNESS in claude codex chatgpt-export connector-supervisor; do
  LABEL="ai.parcha.recall.$HARNESS"
  if [ "$NO_LOAD" -eq 0 ] && command -v launchctl >/dev/null 2>&1; then
    stop_launch_agent "$LABEL"
  fi
  rm -f "$LAUNCH_AGENTS/$LABEL.plist"
done
rm -rf "$PREFIX"
echo "uninstalled Recall Brain Mac client from $PREFIX"
