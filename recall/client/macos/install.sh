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
PRIVACY_MODE="off"
EXPORT_INBOX=""
DISABLE_EXPORT_INBOX=0
SUPERVISOR_CONFIG=""
DISABLE_SUPERVISOR=0

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
    --privacy-mode) PRIVACY_MODE=$2; shift 2 ;;
    --export-inbox) EXPORT_INBOX=$2; shift 2 ;;
    --disable-export-inbox) DISABLE_EXPORT_INBOX=1; shift ;;
    --connector-supervisor-config) SUPERVISOR_CONFIG=$2; shift 2 ;;
    --disable-connector-supervisor) DISABLE_SUPERVISOR=1; shift ;;
    --no-load) NO_LOAD=1; shift ;;
    *) echo "usage: install.sh [--endpoint URL --host-id ID --keychain-service SERVICE --visibility private|shared] [--sources claude,codex] [--export-inbox PATH | --disable-export-inbox] [--connector-supervisor-config FILE | --disable-connector-supervisor] [--privacy-mode off|scrub|drop] [--claude-root PATH] [--codex-root PATH] [--prefix PATH] [--launch-agents PATH] [--no-load]" >&2; exit 2 ;;
  esac
done

case "$PRIVACY_MODE" in off|scrub|drop) ;; *) echo "privacy mode must be off, scrub, or drop" >&2; exit 2 ;; esac
if [ -n "$EXPORT_INBOX" ] && [ "$DISABLE_EXPORT_INBOX" -eq 1 ]; then
  echo "export inbox enable and disable options are mutually exclusive" >&2; exit 2
fi
if [ -n "$SUPERVISOR_CONFIG" ] && [ "$DISABLE_SUPERVISOR" -eq 1 ]; then
  echo "connector supervisor enable and disable options are mutually exclusive" >&2; exit 2
fi
if [ -z "$SOURCES" ] && [ -z "$EXPORT_INBOX" ] && [ "$DISABLE_EXPORT_INBOX" -eq 0 ] && [ -z "$SUPERVISOR_CONFIG" ] && [ "$DISABLE_SUPERVISOR" -eq 0 ]; then
  echo "select at least one coding source, export inbox, or connector supervisor action" >&2; exit 2
fi
if [ -n "$SOURCES" ] || [ -n "$EXPORT_INBOX" ]; then
  case "$ENDPOINT" in https://*) ;; *) echo "endpoint must use https" >&2; exit 2 ;; esac
  case "$HOST_ID" in ""|*[!A-Za-z0-9_.-]*) echo "invalid host id" >&2; exit 2 ;; esac
  [ -n "$KEYCHAIN_SERVICE" ] || { echo "keychain service is required" >&2; exit 2; }
  case "$VISIBILITY" in private|shared) ;; *) echo "visibility must be private or shared" >&2; exit 2 ;; esac
fi
if [ -n "$SOURCES" ]; then
  case ",$SOURCES," in *,claude,*|*,codex,*) ;; *) echo "sources must select claude, codex, or claude,codex" >&2; exit 2 ;; esac
  case ",$SOURCES," in *,,*|*,claude,claude,*|*,codex,codex,*|*,claude,codex,claude,*|*,codex,claude,codex,*) echo "invalid duplicate or empty source selection" >&2; exit 2 ;; esac
fi
for SOURCE_NAME in $(echo "$SOURCES" | tr ',' ' '); do
  case "$SOURCE_NAME" in claude|codex) ;; *) echo "unsupported source: $SOURCE_NAME" >&2; exit 2 ;; esac
done
if [ -n "$EXPORT_INBOX" ]; then
  [ -d "$EXPORT_INBOX" ] && [ ! -L "$EXPORT_INBOX" ] || { echo "export inbox must be an explicit non-symlink directory" >&2; exit 2; }
fi

SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mkdir -p "$PREFIX" "$PREFIX/bin" "$PREFIX/lib" "$PREFIX/state" "$LAUNCH_AGENTS"
rm -rf "$PREFIX/lib/client" "$PREFIX/lib/collector" "$PREFIX/lib/connectors" "$PREFIX/lib/privacy" "$PREFIX/runtime"
cp -R "$SOURCE/lib/client" "$PREFIX/lib/client"
cp -R "$SOURCE/lib/collector" "$PREFIX/lib/collector"
cp -R "$SOURCE/lib/connectors" "$PREFIX/lib/connectors"
cp -R "$SOURCE/lib/privacy" "$PREFIX/lib/privacy"
cp -R "$SOURCE/runtime" "$PREFIX/runtime"
cp "$SOURCE/bin/recall-brain" "$PREFIX/bin/recall-brain"
cp "$SOURCE/RUNTIME_LOCK.json" "$PREFIX/RUNTIME_LOCK.json"
chmod 755 "$PREFIX/bin/recall-brain"
chmod 700 "$PREFIX/state"
RUNTIME="$PREFIX/runtime/bin/python3"
[ -x "$RUNTIME" ] || { echo "bundled runtime is not executable" >&2; exit 1; }
"$RUNTIME" - "$PREFIX/RUNTIME_LOCK.json" <<'PY'
import ctypes
import json
import os
import platform
import sqlite3
import ssl
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    lock = json.load(source)
capabilities = lock["capabilities"]
assert sys.version.split()[0] == lock["version"], sys.version
assert platform.python_implementation() == capabilities["implementation"]
assert platform.system() == capabilities["system"], platform.system()
assert platform.machine() == capabilities["machine"], platform.machine()
if capabilities["language"]["zip_strict"]:
    assert list(zip([1], [2], strict=True)) == [(1, 2)]
ctypes.CDLL(None)
verify_paths = ssl.get_default_verify_paths()
assert any(path and os.path.exists(path) for path in (verify_paths.cafile, verify_paths.capath))
assert ssl.create_default_context().get_ca_certs()
connection = sqlite3.connect(":memory:")
try:
    if capabilities["sqlite"]["fts5"]:
        connection.execute("CREATE VIRTUAL TABLE exact_runtime_fts USING fts5(body)")
finally:
    connection.close()
PY

write_plist() {
  HARNESS=$1
  ROOT=$2
  SOURCE_ID="$HARNESS:mac:$HOST_ID"
  LABEL="ai.parcha.recall.$HARNESS"
  PLIST="$LAUNCH_AGENTS/$LABEL.plist"
  if [ "$NO_LOAD" -eq 0 ] && command -v launchctl >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
  fi
  "$RUNTIME" - "$PLIST" "$LABEL" "$RUNTIME" "$PREFIX/lib" "$ENDPOINT" "$SOURCE_ID" "$HARNESS" "$ROOT" "$PREFIX/state/$HARNESS.db" "$KEYCHAIN_SERVICE" "$VISIBILITY" "$PRIVACY_MODE" <<'PY'
import plistlib
import sys

path, label, program, pythonpath, endpoint, source_id, harness, root, spool, service, visibility, privacy_mode = sys.argv[1:]
value = {
    "Label": label,
    "ProgramArguments": [
        program, "-m", "client.cli", "collect", "--endpoint", endpoint,
        "--source-id", source_id, "--principal-id", "owner",
        "--visibility", visibility, "--harness", harness,
        "--root", root, "--spool", spool,
        "--keychain-service", service, "--keychain-account", source_id,
        "--privacy-mode", privacy_mode,
    ],
    "EnvironmentVariables": {
        "PYTHONPATH": pythonpath,
        "RECALL_KEYCHAIN_REFERENCE": "Keychain service/account only",
    },
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

write_export_inbox_plist() {
  SOURCE_ID="chatgpt-export:mac:$HOST_ID"
  LABEL="ai.parcha.recall.chatgpt-export"
  PLIST="$LAUNCH_AGENTS/$LABEL.plist"
  SPOOL="$PREFIX/state/chatgpt-export-runner.db"
  CATALOG="$PREFIX/state/chatgpt-export-catalog.db"
  if [ "$NO_LOAD" -eq 0 ] && command -v launchctl >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
  fi
  "$RUNTIME" - "$PLIST" "$LABEL" "$RUNTIME" "$PREFIX/lib" "$ENDPOINT" "$SOURCE_ID" "$EXPORT_INBOX" "$CATALOG" "$SPOOL" "$KEYCHAIN_SERVICE" "$PRIVACY_MODE" <<'PY'
import plistlib
import sys

path, label, program, pythonpath, endpoint, source_id, inbox, catalog, spool, service, privacy_mode = sys.argv[1:]
value = {
    "Label": label,
    "ProgramArguments": [
        program, "-m", "client.cli", "export-inbox-sync", "--endpoint", endpoint,
        "--source-id", source_id, "--principal-id", "owner", "--visibility", "private",
        "--inbox", inbox, "--catalog", catalog, "--spool", spool,
        "--keychain-service", service, "--keychain-account", source_id,
        "--privacy-mode", privacy_mode,
    ],
    "EnvironmentVariables": {
        "PYTHONPATH": pythonpath,
        "RECALL_KEYCHAIN_REFERENCE": "Keychain service/account only",
    },
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

write_supervisor_plist() {
  LABEL="ai.parcha.recall.connector-supervisor"
  PLIST="$LAUNCH_AGENTS/$LABEL.plist"
  STATE="$PREFIX/state/connector-supervisor.db"
  if [ "$NO_LOAD" -eq 0 ] && command -v launchctl >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
  fi
  PYTHONPATH="$PREFIX/lib" "$RUNTIME" -m client.cli connector-supervisor-config-preview --config "$SUPERVISOR_CONFIG" >/dev/null
  "$RUNTIME" - "$PLIST" "$LABEL" "$RUNTIME" "$PREFIX/lib" "$SUPERVISOR_CONFIG" "$STATE" <<'PY'
import plistlib
import sys

path, label, program, pythonpath, config, state = sys.argv[1:]
value = {
    "Label": label,
    "ProgramArguments": [
        program, "-m", "client.cli", "connector-supervisor-run",
        "--config", config, "--state", state,
    ],
    "EnvironmentVariables": {"PYTHONPATH": pythonpath},
    "RunAtLoad": True,
    "KeepAlive": True,
    "ThrottleInterval": 10,
    "ProcessType": "Background",
    "StandardOutPath": state + ".stdout.log",
    "StandardErrorPath": state + ".stderr.log",
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
if [ -n "$EXPORT_INBOX" ]; then write_export_inbox_plist; fi
if [ -n "$SUPERVISOR_CONFIG" ]; then write_supervisor_plist; fi
if [ "$DISABLE_EXPORT_INBOX" -eq 1 ]; then
  LABEL="ai.parcha.recall.chatgpt-export"
  if [ "$NO_LOAD" -eq 0 ] && command -v launchctl >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
  fi
  rm -f "$LAUNCH_AGENTS/$LABEL.plist"
fi
if [ "$DISABLE_SUPERVISOR" -eq 1 ]; then
  LABEL="ai.parcha.recall.connector-supervisor"
  if [ "$NO_LOAD" -eq 0 ] && command -v launchctl >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 || true
  fi
  rm -f "$LAUNCH_AGENTS/$LABEL.plist"
fi
echo "installed Recall Brain Mac client in $PREFIX"
echo "selected sources: $SOURCES; visibility: $VISIBILITY; privacy: $PRIVACY_MODE"
if [ -n "$SOURCES" ] || [ -n "$EXPORT_INBOX" ]; then
  echo "Keychain accounts use each selected source id as the account"
fi
