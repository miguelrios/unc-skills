# Tailnet-private pilot deployment

The application listens on a Unix socket, not TCP. Tailscale Serve is the only network proxy.
On Linux, the server verifies `SO_PEERCRED` and trusts Tailscale identity headers only when the
Unix-socket peer UID is explicitly allowlisted. Ubuntu's sandboxed `tailscaled` uses UID 65534;
root is UID 0. Neither identity is assumable by the interactive user, so a same-user process that
connects directly and forges `Tailscale-User-Login` is rejected. Narrow
`RECALL_TRUSTED_PROXY_UIDS` to the UIDs observed for the local `tailscaled` service.

Install from an immutable, reviewed checkout at `~/services/recall-brain`. The service unit never
points at a contributor's active checkout.

```bash
git worktree add --detach ~/services/recall-brain <reviewed-merged-sha>
python3 -m venv ~/.config/recall-brain/venv
~/.config/recall-brain/venv/bin/pip install -r ~/services/recall-brain/recall/server/requirements.txt
install -m 0600 ~/services/recall-brain/recall/server/deploy/service.env.example ~/.config/recall-brain/service.env
install -m 0644 ~/services/recall-brain/recall/server/deploy/recall-brain.service ~/.config/systemd/user/
install -m 0644 ~/services/recall-brain/recall/server/deploy/recall-brain-backup.service ~/.config/systemd/user/
install -m 0644 ~/services/recall-brain/recall/server/deploy/recall-brain-backup.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now recall-brain
systemctl --user enable --now recall-brain-backup.timer
tailscale serve --bg --https=9443 unix:/run/user/$(id -u)/recall-brain.sock
```

Do not use Funnel. Preserve unrelated Serve listeners by configuring only the dedicated 9443
listener. Collectors receive revocable tokens from `recall_server.cli token-create`; plaintext
is emitted once and only its SHA-256 is stored. Use `--output /secure/mode-0600-file.json` so the
plaintext never enters terminal or session logs; the command refuses to overwrite an existing file.

The pilot uses a five-minute logical-backup timer for a bounded RPO. Before multi-user scale or
C10 production cutover, replace it with continuous WAL archival plus daily base backups; the
same blank-database restore/fingerprint contract remains the gate.

Back up and run a blank-database restore proof:

```bash
RECALL_DATABASE_URL=... recall/server/scripts/backup_restore.sh backup /secure/backup/dir
RECALL_RESTORE_DATABASE_URL=... recall/server/scripts/backup_restore.sh restore-test /secure/backup/dir
```
