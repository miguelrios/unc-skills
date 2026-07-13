#!/bin/sh
set -eu

PREFIX="$HOME/Library/Application Support/RecallBrain"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
ENDPOINT=""
HOST_ID=""
KEYCHAIN_SERVICE=""
VISIBILITY=""
SOURCES=""
CLAUDE_ROOT="$HOME/.claude/projects"
CODEX_ROOT="$HOME/.codex/sessions"
NO_LOAD=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX=$2; shift 2 ;;
    --launch-agents) LAUNCH_AGENTS=$2; shift 2 ;;
    --endpoint) ENDPOINT=$2; shift 2 ;;
    --host-id) HOST_ID=$2; shift 2 ;;
    --keychain-service) KEYCHAIN_SERVICE=$2; shift 2 ;;
    --visibility) VISIBILITY=$2; shift 2 ;;
    --sources) SOURCES=$2; shift 2 ;;
    --claude-root) CLAUDE_ROOT=$2; shift 2 ;;
    --codex-root) CODEX_ROOT=$2; shift 2 ;;
    --no-load) NO_LOAD=1; shift ;;
    *) echo "usage: install.sh --endpoint URL --host-id ID --keychain-service SERVICE --visibility private|shared --sources claude,codex [--claude-root PATH] [--codex-root PATH] [--prefix PATH] [--launch-agents PATH] [--no-load]" >&2; exit 2 ;;
  esac
done

case "$ENDPOINT" in https://*) ;; *) echo "endpoint must use https" >&2; exit 2 ;; esac
case "$HOST_ID" in ""|*[!A-Za-z0-9_.-]*) echo "invalid host id" >&2; exit 2 ;; esac
[ -n "$KEYCHAIN_SERVICE" ] || { echo "keychain service is required" >&2; exit 2; }
case "$VISIBILITY" in private|shared) ;; *) echo "visibility must be private or shared" >&2; exit 2 ;; esac
case ",$SOURCES," in
  *,claude,*|*,codex,*) ;;
  *) echo "sources must select claude, codex, or claude,codex" >&2; exit 2 ;;
esac
case ",$SOURCES," in *,,*|*,claude,claude,*|*,codex,codex,*|*,claude,codex,claude,*|*,codex,claude,codex,*) echo "invalid duplicate or empty source selection" >&2; exit 2 ;; esac
for SOURCE_NAME in $(echo "$SOURCES" | tr ',' ' '); do
  case "$SOURCE_NAME" in claude|codex) ;; *) echo "unsupported source: $SOURCE_NAME" >&2; exit 2 ;; esac
done

SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mkdir -p "$PREFIX" "$PREFIX/bin" "$PREFIX/lib" "$PREFIX/state" "$LAUNCH_AGENTS"
rm -rf "$PREFIX/lib/client" "$PREFIX/lib/collector"
cp -R "$SOURCE/lib/client" "$PREFIX/lib/client"
cp -R "$SOURCE/lib/collector" "$PREFIX/lib/collector"
cp "$SOURCE/bin/recall-brain" "$PREFIX/bin/recall-brain"
chmod 755 "$PREFIX/bin/recall-brain"
chmod 700 "$PREFIX/state"

write_plist() {
  HARNESS=$1
  ROOT=$2
  SOURCE_ID="$HARNESS:mac:$HOST_ID"
  LABEL="ai.parcha.recall.$HARNESS"
  PLIST="$LAUNCH_AGENTS/$LABEL.plist"
  if [ "$NO_LOAD" -eq 0 ] && command -v launchctl >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
  fi
  python3 - "$PLIST" "$LABEL" "$PREFIX/bin/recall-brain" "$ENDPOINT" "$SOURCE_ID" "$HARNESS" "$ROOT" "$PREFIX/state/$HARNESS.db" "$KEYCHAIN_SERVICE" "$VISIBILITY" <<'PY'
import plistlib
import sys

path, label, program, endpoint, source_id, harness, root, spool, service, visibility = sys.argv[1:]
value = {
    "Label": label,
    "ProgramArguments": [
        program, "collect", "--endpoint", endpoint,
        "--source-id", source_id, "--principal-id", "owner",
        "--visibility", visibility, "--harness", harness,
        "--root", root, "--spool", spool,
        "--keychain-service", service, "--keychain-account", source_id,
    ],
    "EnvironmentVariables": {"RECALL_KEYCHAIN_REFERENCE": "Keychain service/account only"},
    "RunAtLoad": True,
    "StartInterval": 30,
    "ProcessType": "Background",
    "StandardOutPath": spool + ".stdout.log",
    "StandardErrorPath": spool + ".stderr.log",
}
with open(path, "wb") as output:
    plistlib.dump(value, output, sort_keys=True)
PY
  chmod 600 "$PLIST"
  if [ "$NO_LOAD" -eq 0 ] && command -v launchctl >/dev/null 2>&1; then
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
  fi
}

case ",$SOURCES," in *,claude,*) write_plist claude "$CLAUDE_ROOT" ;; esac
case ",$SOURCES," in *,codex,*) write_plist codex "$CODEX_ROOT" ;; esac
echo "installed Recall Brain Mac client in $PREFIX"
echo "selected sources: $SOURCES; visibility: $VISIBILITY"
echo "Keychain accounts use each selected source id as the account"
