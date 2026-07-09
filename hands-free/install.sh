#!/usr/bin/env bash
# hands-free installer: drops the skill + call_user.py into the harness home.
# NO HOOKS ARE INSTALLED — the agent reads the skill and places calls itself.
# On upgrade, any hook wiring left by hands-free <= 0.2.x is REMOVED.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"

HARNESS="${HANDS_FREE_HARNESS:-auto}"
for arg in "$@"; do
  case "$arg" in
    --harness=*)
      HARNESS="${arg#--harness=}"
      ;;
    --claude-code)
      HARNESS="claude-code"
      ;;
    --codex)
      HARNESS="codex"
      ;;
  esac
done

# Auto-detect: prefer Claude Code if ~/.claude exists, else codex, else claude default.
if [[ "$HARNESS" == "auto" ]]; then
  if [[ -d "$HOME/.claude" ]]; then
    HARNESS="claude-code"
  elif [[ -d "$HOME/.codex" ]]; then
    HARNESS="codex"
  else
    HARNESS="claude-code"
  fi
fi

case "$HARNESS" in
  claude-code)
    HARNESS_HOME="${CLAUDE_HOME:-$HOME/.claude}"
    SETTINGS_FILE="$HARNESS_HOME/settings.json"
    ;;
  codex)
    HARNESS_HOME="${CODEX_HOME:-$HOME/.codex}"
    SETTINGS_FILE="$HARNESS_HOME/hooks.json"
    ;;
  *)
    echo "Unknown harness: $HARNESS (expected claude-code or codex)" >&2
    exit 1
    ;;
esac

HANDS_FREE_HOME="$HARNESS_HOME/hands-free"
SKILL_HOME="$HARNESS_HOME/skills/hands-free"
CALL_USER_SOURCE="$ROOT_DIR/skills/hands-free/scripts/call_user.py"

install -d -m 700 "$HANDS_FREE_HOME/scripts" "$SKILL_HOME/agents" "$SKILL_HOME/scripts" "$SKILL_HOME/references"
install -m 700 "$CALL_USER_SOURCE" "$HANDS_FREE_HOME/scripts/call_user.py"
install -m 644 "$ROOT_DIR/skills/hands-free/SKILL.md" "$SKILL_HOME/SKILL.md"
install -m 644 "$ROOT_DIR/skills/hands-free/references/setup.md" "$SKILL_HOME/references/setup.md"
install -m 644 "$ROOT_DIR/skills/hands-free/agents/openai.yaml" "$SKILL_HOME/agents/openai.yaml"
install -m 700 "$CALL_USER_SOURCE" "$SKILL_HOME/scripts/call_user.py"

if [[ ! -f "$HANDS_FREE_HOME/.env" ]]; then
  install -m 600 "$ROOT_DIR/.env.example" "$HANDS_FREE_HOME/.env"
fi

# Retire artifacts from hands-free <= 0.2.x: hook wiring, the hook script, mode state.
rm -f "$HANDS_FREE_HOME/scripts/hands_free_hook.py" "$SKILL_HOME/scripts/hands_free_hook.py" "$HANDS_FREE_HOME/state.json"

if [[ -f "$SETTINGS_FILE" ]]; then
"$PYTHON_BIN" - "$SETTINGS_FILE" <<'PY'
import json
import pathlib
import sys

settings_path = pathlib.Path(sys.argv[1]).expanduser()
data = json.loads(settings_path.read_text())
hooks = data.get("hooks")
if isinstance(hooks, dict):
    removed = 0
    for event_name in list(hooks.keys()):
        filtered_entries = []
        for entry in hooks[event_name]:
            entry_hooks = entry.get("hooks", []) if isinstance(entry, dict) else []
            if any("hands-free" in str(hook.get("command", "")) for hook in entry_hooks if isinstance(hook, dict)):
                removed += 1
                continue
            filtered_entries.append(entry)
        if filtered_entries:
            hooks[event_name] = filtered_entries
        else:
            del hooks[event_name]
    if not hooks:
        data.pop("hooks", None)
    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    if removed:
        print(f"Removed {removed} legacy hands-free hook entr{'y' if removed == 1 else 'ies'} from {settings_path}.")
PY
fi

echo "Installed hands-free (no hooks) for $HARNESS under $HARNESS_HOME."
echo "Edit $HANDS_FREE_HOME/.env, then just tell your agent: activate hands free."
