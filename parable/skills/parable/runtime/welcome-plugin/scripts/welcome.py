#!/usr/bin/env python3
"""Emit Parable's user-only Claude Code SessionStart card."""

import json
import os


message = os.environ.get("PARABLE_WELCOME_MESSAGE", "").strip()
print(json.dumps(
    {"systemMessage": "\n" + message, "suppressOutput": True} if message else {},
    ensure_ascii=False,
))
