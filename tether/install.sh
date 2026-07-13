#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS="auto"
for arg in "$@"; do
  case "$arg" in
    --harness=*) HARNESS="${arg#--harness=}" ;;
    --codex) HARNESS="codex" ;;
    --claude-code) HARNESS="claude-code" ;;
    --both) HARNESS="both" ;;
    *) echo "usage: $0 [--harness=auto|codex|claude-code|both]" >&2; exit 2 ;;
  esac
done

if [[ "$HARNESS" == "auto" ]]; then
  if [[ -d "${CODEX_HOME:-$HOME/.codex}" && -d "${CLAUDE_HOME:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}}" ]]; then
    HARNESS="both"
  elif [[ -d "${CODEX_HOME:-$HOME/.codex}" ]]; then
    HARNESS="codex"
  else
    HARNESS="claude-code"
  fi
fi

case "$HARNESS" in
  codex|claude-code|both) ;;
  *) echo "unknown harness: $HARNESS" >&2; exit 2 ;;
esac

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
RUNTIME_HOME="$DATA_HOME/tether"
CONFIG_DIR="$CONFIG_HOME/tether"
PLUGIN_HOME="$HERMES_HOME/plugins/tether"
SKILL_SOURCE="$ROOT_DIR/skills/tether"

install -d -m 700 "$RUNTIME_HOME" "$CONFIG_DIR" "$PLUGIN_HOME" "$HOME/.local/bin"
install -m 600 "$ROOT_DIR/runtime/bridge_runtime.py" "$RUNTIME_HOME/bridge_runtime.py"
install -m 700 "$SKILL_SOURCE/scripts/tether_notify.py" "$RUNTIME_HOME/tether_notify.py"
install -m 600 "$ROOT_DIR/runtime/plugin/__init__.py" "$PLUGIN_HOME/__init__.py"

if [[ ! -f "$CONFIG_DIR/config.toml" ]]; then
  install -m 600 "$ROOT_DIR/runtime/config.example.toml" "$CONFIG_DIR/config.toml"
fi

install_skill() {
  local harness_home="$1"
  local destination="$harness_home/skills/tether"
  install -d -m 700 "$destination/agents" "$destination/references" "$destination/scripts"
  install -m 644 "$SKILL_SOURCE/SKILL.md" "$destination/SKILL.md"
  install -m 644 "$SKILL_SOURCE/agents/openai.yaml" "$destination/agents/openai.yaml"
  install -m 644 "$SKILL_SOURCE/references/setup.md" "$destination/references/setup.md"
  install -m 644 "$SKILL_SOURCE/references/contract.md" "$destination/references/contract.md"
  install -m 700 "$SKILL_SOURCE/scripts/tether_notify.py" "$destination/scripts/tether_notify.py"
}

if [[ "$HARNESS" == "codex" || "$HARNESS" == "both" ]]; then
  install_skill "${CODEX_HOME:-$HOME/.codex}"
fi
if [[ "$HARNESS" == "claude-code" || "$HARNESS" == "both" ]]; then
  install_skill "${CLAUDE_HOME:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}}"
fi

install -m 700 "$ROOT_DIR/runtime/tether" "$HOME/.local/bin/tether"

echo "Installed Tether for $HARNESS."
echo "Next: run tether setup. Existing Hermes Slack channel and allowlist settings are reused."
