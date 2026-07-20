# Operations toolbox (BR1)

Turnkey operational scripts for the closed beta. Every one is a **read-only wrapper over existing
tooling** (`examples/db_backup.py`, the OBS1 `/api/health/*` endpoints) — they change **no application
behavior**. See **`docs/BETA_LAUNCH_CHECKLIST.md`** for the launch runbook that drives these.

| Script | What it does | Typical use |
|---|---|---|
| `preflight.sh` | Deterministic PASS/WARN/FAIL of env, secrets, HTTPS, OAuth, persistent DB, recent backup, monitoring, and (optionally) live health + analytics gating. Exit 0 only if no FAILs. | Before every launch / redeploy, with the prod env loaded. |
| `backup.sh` | One consistent, integrity-checked backup + local retention (`BACKUP_KEEP`) + optional off-host shipment (`BACKUP_OFFHOST_CMD`). | Scheduled hourly (cron / systemd / the compose `scheduler` profile). |
| `verify-restore.sh` | Proves the newest backup is intact **and restorable** — non-destructively (restores a copy to a scratch path, runs `quick_check`, opens the store). | Daily, and always before a real restore. |
| `healthcheck.sh` | Probes the engine's OBS1 `/api/health/{live,ready}` (and optionally the web app); alerts via `ALERT_WEBHOOK` on failure. | cron / systemd, or as the vendor-neutral fallback behind an external uptime monitor. |

All scripts `cd` to the repo root and select the database exactly as the engine does
(`RWE_DB_URL` → default). Run them from a host that has Python + the repo and access to the DB (the
non-Docker production path), or adapt via the compose services. Environment knobs are documented in
each script's header.

**Not shipped in the container image** (`deploy/` is excluded from the Docker build context) — these
are host-side operator tools. The Docker path gets recurring backups from the compose `backup-scheduler`
profile (which inlines `db_backup.py`); health/monitoring points an external monitor (or a host cron
running `healthcheck.sh`) at the published engine URL.
