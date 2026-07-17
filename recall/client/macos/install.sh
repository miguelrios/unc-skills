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
COWORK_ROOT="$HOME/Library/Application Support/Claude/local-agent-mode-sessions"
NO_LOAD=0
PRIVACY_MODE="scrub"
EXPORT_INBOX=""
DISABLE_EXPORT_INBOX=0
DISABLE_SOURCES=""
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
    --cowork-root) COWORK_ROOT=$2; shift 2 ;;
    --privacy-mode) PRIVACY_MODE=$2; shift 2 ;;
    --export-inbox) EXPORT_INBOX=$2; shift 2 ;;
    --disable-export-inbox) DISABLE_EXPORT_INBOX=1; shift ;;
    --disable-source) DISABLE_SOURCES="$DISABLE_SOURCES $2"; shift 2 ;;
    --connector-supervisor-config) SUPERVISOR_CONFIG=$2; shift 2 ;;
    --disable-connector-supervisor) DISABLE_SUPERVISOR=1; shift ;;
    --no-load) NO_LOAD=1; shift ;;
    *) echo "usage: install.sh [--endpoint URL --host-id ID --keychain-service SERVICE --visibility private|shared] [--sources claude-code,codex|chatgpt-codex-desktop,cowork] [--export-inbox PATH | --disable-export-inbox] [--disable-source claude-code|codex|chatgpt-codex-desktop|cowork|chatgpt-export] [--connector-supervisor-config FILE | --disable-connector-supervisor] [--privacy-mode off|scrub|drop] [--claude-root PATH] [--codex-root PATH] [--cowork-root PATH] [--prefix PATH] [--launch-agents PATH] [--no-load]" >&2; exit 2 ;;
  esac
done

case "$PRIVACY_MODE" in off|scrub|drop) ;; *) echo "privacy mode must be off, scrub, or drop" >&2; exit 2 ;; esac
if [ -n "$EXPORT_INBOX" ] && [ "$DISABLE_EXPORT_INBOX" -eq 1 ]; then
  echo "export inbox enable and disable options are mutually exclusive" >&2; exit 2
fi
if [ -n "$SUPERVISOR_CONFIG" ] && [ "$DISABLE_SUPERVISOR" -eq 1 ]; then
  echo "connector supervisor enable and disable options are mutually exclusive" >&2; exit 2
fi
if [ -z "$SOURCES" ] && [ -z "$EXPORT_INBOX" ] && [ "$DISABLE_EXPORT_INBOX" -eq 0 ] && [ -z "$DISABLE_SOURCES" ] && [ -z "$SUPERVISOR_CONFIG" ] && [ "$DISABLE_SUPERVISOR" -eq 0 ]; then
  echo "select at least one coding source, export inbox, or connector supervisor action" >&2; exit 2
fi
if [ -n "$SOURCES" ] || [ -n "$EXPORT_INBOX" ]; then
  case "$ENDPOINT" in https://*) ;; *) echo "endpoint must use https" >&2; exit 2 ;; esac
  case "$HOST_ID" in ""|*[!A-Za-z0-9_.-]*) echo "invalid host id" >&2; exit 2 ;; esac
  [ -n "$KEYCHAIN_SERVICE" ] || { echo "keychain service is required" >&2; exit 2; }
  case "$VISIBILITY" in private|shared) ;; *) echo "visibility must be private or shared" >&2; exit 2 ;; esac
fi
NORMALIZED_SOURCES=""
if [ -n "$SOURCES" ]; then
  case ",$SOURCES," in *,,*) echo "invalid duplicate or empty source selection" >&2; exit 2 ;; esac
fi
for SOURCE_NAME in $(echo "$SOURCES" | tr ',' ' '); do
  case "$SOURCE_NAME" in
    claude|claude-code) NORMALIZED=claude ;;
    codex|chatgpt-codex-desktop) NORMALIZED=codex ;;
    cowork) NORMALIZED=cowork ;;
    *) echo "unsupported source: $SOURCE_NAME" >&2; exit 2 ;;
  esac
  case ",$NORMALIZED_SOURCES," in *,$NORMALIZED,*) echo "invalid duplicate or empty source selection" >&2; exit 2 ;; esac
  NORMALIZED_SOURCES="${NORMALIZED_SOURCES:+$NORMALIZED_SOURCES,}$NORMALIZED"
done
SOURCES=$NORMALIZED_SOURCES
case ",$SOURCES," in *,cowork,*)
  [ "$PRIVACY_MODE" != "off" ] || { echo "Cowork requires scrub or drop privacy" >&2; exit 2; }
  [ -d "$COWORK_ROOT" ] && [ ! -L "$COWORK_ROOT" ] || { echo "Cowork root must be an explicit non-symlink directory" >&2; exit 2; }
;; esac
for SOURCE_NAME in $DISABLE_SOURCES; do
  case "$SOURCE_NAME" in claude|claude-code|codex|chatgpt-codex-desktop|cowork|chatgpt-export) ;; *) echo "unsupported disable source: $SOURCE_NAME" >&2; exit 2 ;; esac
done
if [ -n "$EXPORT_INBOX" ]; then
  [ -d "$EXPORT_INBOX" ] && [ ! -L "$EXPORT_INBOX" ] || { echo "export inbox must be an explicit non-symlink directory" >&2; exit 2; }
fi

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

SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mkdir -p "$PREFIX" "$PREFIX/bin" "$PREFIX/lib" "$PREFIX/state" "$LAUNCH_AGENTS"
rm -rf "$PREFIX/lib/client" "$PREFIX/lib/collector" "$PREFIX/lib/connectors" "$PREFIX/lib/contracts" "$PREFIX/lib/privacy" "$PREFIX/runtime"
cp -R "$SOURCE/lib/client" "$PREFIX/lib/client"
cp -R "$SOURCE/lib/collector" "$PREFIX/lib/collector"
cp -R "$SOURCE/lib/connectors" "$PREFIX/lib/connectors"
cp -R "$SOURCE/lib/contracts" "$PREFIX/lib/contracts"
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
    stop_launch_agent "$LABEL"
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
    "Umask": 0o077,
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
    stop_launch_agent "$LABEL"
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
    "Umask": 0o077,
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

write_cowork_plist() {
  SOURCE_ID="cowork:mac:$HOST_ID"
  LABEL="ai.parcha.recall.cowork"
  PLIST="$LAUNCH_AGENTS/$LABEL.plist"
  SPOOL="$PREFIX/state/cowork.db"
  if [ "$NO_LOAD" -eq 0 ] && command -v launchctl >/dev/null 2>&1; then
    stop_launch_agent "$LABEL"
  fi
  "$RUNTIME" - "$PLIST" "$LABEL" "$RUNTIME" "$PREFIX/lib" "$ENDPOINT" "$SOURCE_ID" "$COWORK_ROOT" "$SPOOL" "$KEYCHAIN_SERVICE" "$PRIVACY_MODE" <<'PY'
import plistlib
import sys

path, label, program, pythonpath, endpoint, source_id, root, spool, service, privacy_mode = sys.argv[1:]
value = {
    "Label": label,
    "ProgramArguments": [
        program, "-m", "client.cli", "cowork-local-sync", "--endpoint", endpoint,
        "--source-id", source_id, "--principal-id", "owner",
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
    "Umask": 0o077,
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
    stop_launch_agent "$LABEL"
  fi
  if [ -n "$EXPORT_INBOX" ]; then
    PYTHONPATH="$PREFIX/lib" "$RUNTIME" -m client.cli connector-supervisor-config-preview \
      --config "$SUPERVISOR_CONFIG" --reserved-export-inbox "$EXPORT_INBOX" >/dev/null
  else
    PYTHONPATH="$PREFIX/lib" "$RUNTIME" -m client.cli connector-supervisor-config-preview \
      --config "$SUPERVISOR_CONFIG" >/dev/null
  fi
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
    "Umask": 0o077,
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
case ",$SOURCES," in *,cowork,*) write_cowork_plist ;; esac
if [ -n "$EXPORT_INBOX" ]; then write_export_inbox_plist; fi
if [ -n "$SUPERVISOR_CONFIG" ]; then write_supervisor_plist; fi
if [ "$DISABLE_EXPORT_INBOX" -eq 1 ]; then
  LABEL="ai.parcha.recall.chatgpt-export"
  if [ "$NO_LOAD" -eq 0 ] && command -v launchctl >/dev/null 2>&1; then
    stop_launch_agent "$LABEL"
  fi
  rm -f "$LAUNCH_AGENTS/$LABEL.plist"
fi
for SOURCE_NAME in $DISABLE_SOURCES; do
  case "$SOURCE_NAME" in
    claude|claude-code) LABEL="ai.parcha.recall.claude" ;;
    codex|chatgpt-codex-desktop) LABEL="ai.parcha.recall.codex" ;;
    cowork) LABEL="ai.parcha.recall.cowork" ;;
    chatgpt-export) LABEL="ai.parcha.recall.chatgpt-export" ;;
  esac
  if [ "$NO_LOAD" -eq 0 ] && command -v launchctl >/dev/null 2>&1; then
    stop_launch_agent "$LABEL"
  fi
  rm -f "$LAUNCH_AGENTS/$LABEL.plist"
done
if [ "$DISABLE_SUPERVISOR" -eq 1 ]; then
  LABEL="ai.parcha.recall.connector-supervisor"
  if [ "$NO_LOAD" -eq 0 ] && command -v launchctl >/dev/null 2>&1; then
    stop_launch_agent "$LABEL"
  fi
  rm -f "$LAUNCH_AGENTS/$LABEL.plist"
fi
echo "installed Recall Brain Mac client in $PREFIX"
echo "selected sources: $SOURCES; visibility: $VISIBILITY; privacy: $PRIVACY_MODE"
if [ -n "$SOURCES" ] || [ -n "$EXPORT_INBOX" ]; then
  echo "Keychain accounts use each selected source id as the account"
fi
