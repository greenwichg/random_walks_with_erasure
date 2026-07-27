# Operations toolbox (BR1 + AWS deployment)

Turnkey operational scripts for the closed beta. Every one is a **thin wrapper over existing tooling**
(`docker compose`, `examples/db_backup.py`, the OBS1 `/api/health/*` endpoints) — they change **no
application behavior**. See **`docs/DEPLOYMENT_RUNBOOK.md`** (AWS EC2) and
**`docs/BETA_LAUNCH_CHECKLIST.md`** for the runbooks that drive these.

### AWS deployment lifecycle (Docker Compose path)
These wrap `docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.aws.yml --env-file deploy/.env …`
(centralised in `_compose.sh`). Every one is **idempotent** — safe to re-run on a brand-new EC2 instance.

| Script | What it does | Typical use |
|---|---|---|
| `bootstrap-ec2.sh` | Idempotent host prep: Docker + Compose, AWS CLI, 2 GB swap, container log rotation, data dir, seeds `deploy/.env`. `sudo` required. | Once per new instance (safe to re-run). |
| `deploy.sh` | Builds + starts ingest→api→web→caddy, gates on engine readiness, enables the backup scheduler, then runs `smoke-test.sh`. | First deploy and every redeploy. |
| `update.sh [ref]` | Checks out a release tag (or the previous good tag for **rollback**), rebuilds, re-validates. Data untouched. | Ship a new version / roll back. |
| `cd-deploy.sh <ref>` | **CI/CD entry point** (invoked by `.github/workflows/deploy.yml` via SSM): pre-deploy DB snapshot → `update.sh <ref>` → **automatic rollback** to the previously-serving commit on failure, with webhook alerts. Prints a machine-readable `CD_RESULT=` verdict. | GitHub Actions; also runnable by hand. See `docs/CICD_PIPELINE.md`. |
| `suspend.sh` | **Cost suspend** (host half of `.github/workflows/suspend.yml`): final integrity-checked backup → off-host to S3 → graceful `compose stop`. Aborts and keeps serving if the backup fails; the workflow stops the instance afterwards. | Shutting the environment down. See `docs/SUSPEND_RESUME.md`. |
| `resume.sh [ref]` | **Cost resume** (host half of `.github/workflows/resume.yml`): DB integrity check → optional checkout → `up -d` → readiness gate → smoke test. Idempotent; also the rescue path when an instance was started from the console. | Bringing the environment back. |
| `restart.sh [svc]` | Restarts all services (or one) via `up -d` so a `deploy/.env` change is re-read. | After editing env (e.g. allowlist). |
| `restore.sh [src]` | **Verify-first** restore from an `s3://…` URI or a local backup (integrity check → halt writes → safe swap → re-validate). | Recover from data loss/corruption. |
| `backup-offhost.sh [--backup-now]` | **Container-based** backup + integrity check + `aws s3 sync` off-host (no host Python). | Hourly cron (installed by `bootstrap-ec2.sh`); go-live/manual. |
| `monitor.sh` | **Container-based** health monitor: edge containers running + engine live/ready (internal network) → `ALERT_WEBHOOK`. Catches the web crash-loop. | 5-min cron (installed by `bootstrap-ec2.sh`). |
| `smoke-test.sh` | Validates the **running** stack: containers up, engine live/ready (internal Docker network), PA1 gating (200 with secret / 404 without), OBS1 metrics, public HTTPS + TLS + HTTP→HTTPS redirect. | Auto-run post-deploy; anytime. |

### Host-side probes & backup (Python path — also used inside the containers)
| Script | What it does | Typical use |
|---|---|---|
| `preflight.sh` | Deterministic PASS/WARN/FAIL of env, secrets, HTTPS, OAuth, persistent DB, recent backup, monitoring, and (optionally) live health + analytics gating. Exit 0 only if no FAILs. | Before every launch / redeploy, with the prod env loaded. |
| `backup.sh` | One consistent, integrity-checked backup + local retention (`BACKUP_KEEP`) + optional off-host shipment (`BACKUP_OFFHOST_CMD`). | Scheduled hourly (cron / systemd / the compose `scheduler` profile). |
| `verify-restore.sh` | Proves the newest backup is intact **and restorable** — non-destructively (restores a copy to a scratch path, runs `quick_check`, opens the store). | Daily, and always before a real restore. |
| `healthcheck.sh` | Probes the engine's OBS1 `/api/health/{live,ready}` (and optionally the web app); alerts via `ALERT_WEBHOOK` on failure. | cron / systemd, or as the vendor-neutral fallback behind an external uptime monitor. |

The `preflight.sh` / `backup.sh` / `verify-restore.sh` / `healthcheck.sh` scripts `cd` to the repo root
and select the database exactly as the engine does (`RWE_DB_URL` → default). They need **host Python +
SQLAlchemy**, so they are for the **non-Docker** path only. **On the EC2 Docker host (no Python) use the
container-based `backup-offhost.sh` / `monitor.sh` / `restore.sh` instead** — they run the same
`db_backup.py`/OBS1 tooling inside the `backup`/`api` containers. `_compose.sh` is a **sourced helper**
(not run directly).

**Not shipped in the container image** (`deploy/` is excluded from the Docker build context) — these
are host-side operator tools. The Docker path gets recurring backups from the compose `backup-scheduler`
profile (which inlines `db_backup.py`); health/monitoring points an external monitor (or a host cron
running `healthcheck.sh`) at the published engine URL.
