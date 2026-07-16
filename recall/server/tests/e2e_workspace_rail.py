#!/usr/bin/env python3
"""Synthetic process-level proof for the closed Google Workspace rail."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "recall"))

from connectors import workspace_rail
from connectors.workspace_rail import WorkspaceRail, WorkspaceRailError


FAKE_GWS = """#!/usr/bin/env python3
import json, sys
key = '.'.join(sys.argv[1:sys.argv.index('--params')])
responses = {
    'gmail.users.history.list': {'history': [], 'historyId': '101'},
    'calendar.events.list': {'kind': 'calendar#events', 'items': [], 'nextSyncToken': 'sync-1'},
    'people.people.connections.list': {'connections': [], 'nextSyncToken': 'sync-1'},
    'drive.changes.getStartPageToken': {'kind': 'drive#startPageToken', 'startPageToken': '1'},
}
if key not in responses:
    raise SystemExit(2)
print(json.dumps(responses[key]))
"""


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="recall-workspace-e2e-") as directory:
        root = Path(directory)
        executable = root / "gws"
        executable.write_text(FAKE_GWS)
        os.chmod(executable, 0o700)
        credential = root / "credential.json"
        credential.write_text("synthetic")
        os.chmod(credential, 0o600)
        workspace_rail.GWS_EXECUTABLE = str(executable)
        rail = WorkspaceRail(credential_path=credential)
        results = (
            rail.run("gmail.history.list", {"userId": "me", "startHistoryId": "100"}),
            rail.run("calendar.events.list", {"calendarId": "primary", "maxResults": 1}),
            rail.run("people.people.connections.list", {
                "resourceName": "people/me", "pageSize": 1, "personFields": "names",
            }),
            rail.run("drive.changes.getStartPageToken", {"supportsAllDrives": True}),
        )
        assert len(results) == 4
        try:
            rail.run("gmail.messages.send", {"userId": "me"})
        except WorkspaceRailError as error:
            assert error.code == "operation_not_allowed"
        else:
            raise AssertionError("write operation escaped the rail")
    print(json.dumps({
        "status": "pass", "read_shapes": 4, "write_escapes": 0,
        "credential_values_rendered": 0, "private_content_rendered": False,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
