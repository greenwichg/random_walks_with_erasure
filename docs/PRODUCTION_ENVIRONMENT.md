# Production Environment — Wave 0 (hidden-view.com)

Every environment variable the production stack reads, where it is set, and why. The authoritative,
secret-free template is **`deploy/.env.production.example`** — copy it to `deploy/.env`, fill the
REQUIRED values, and `chmod 600`. This doc explains each value and the fail-closed behavior that makes a
misconfigured deploy stay *closed* rather than open.

> DEPLOYMENT-ONLY: none of this changes application behavior. The engine and web tier already implement
> fail-closed production mode; here we only supply values. Full engine/web config reference: `DEPLOYMENT.md`.

## Where env comes from

```
deploy/.env  ──(--env-file)──▶  docker compose (base + docker-compose.aws.yml)  ──▶  container environment
     │                                                                                      │
     └── also read by the host-side ops scripts (deploy/ops/*) via _compose.sh              └── validated
                                                                                                at startup
```

`deploy/docker-compose.aws.yml` maps `deploy/.env` values onto the `api`, `web`, `caddy`, and
`backup-scheduler` services with `${VAR}` interpolation. **`chmod 600 deploy/.env`** — it holds secrets;
it is git-ignored (`.gitignore` → `/deploy/.env`). Prefer SSM Parameter Store / Secrets Manager for a
stronger posture (render `.env` at deploy time); the 600-mode file is acceptable for a closed beta on an
access-controlled box.

## Required variables

### Domain & TLS (consumed by Caddy)
| Variable | Example | Notes |
|---|---|---|
| `APP_DOMAIN` | `hidden-view.com` | Apex served; `www.<domain>` 308-redirects to it. Substituted into `deploy/Caddyfile`. |
| `ACME_EMAIL` | `ops@hidden-view.com` | Let's Encrypt account email (expiry notices / recovery). |

### Engine (FastAPI `api`) — fail-closed auth
| Variable | Value | Notes |
|---|---|---|
| `RWE_ENV` | `production` | Turns on fail-closed auth. The engine **refuses to boot** without `RWE_INTERNAL_SECRET`. |
| `RWE_INTERNAL_SECRET` | `openssl rand -base64 32` | The only thing that authenticates web→engine calls. **Identical** on `api` and `web`. |
| `RWE_DB_URL` | `sqlite:////app/data/ih_beta.db` | Persistent path on the bind-mounted volume. Prod rejects in-memory / `/tmp`. |

### Web (Next.js `web`) — validated at startup (`web/instrumentation.ts`)
| Variable | Value | Notes |
|---|---|---|
| `RWE_ENV` | `production` | Disables the dev demo-login; enables BA1 by default; **`process.exit(1)`** if the vars below are missing. |
| `RWE_INTERNAL_SECRET` | *(same as engine)* | Sent as `X-IH-Auth` to the engine. |
| `RWE_BACKEND_URL` | `http://api:8000` | Engine's service name on the private Docker network (never public). |
| `NEXTAUTH_URL` | `https://hidden-view.com` | **https**, exact public URL. Drives OAuth callbacks + `Secure` cookies. See `docs/GOOGLE_OAUTH_CONFIGURATION.md`. |
| `NEXTAUTH_SECRET` | `openssl rand -base64 32` | Signs session JWTs. Rotating it invalidates all sessions. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | from Google Cloud Console | The only sign-in method in production. |
| `BETA_ACCESS_ENABLED` | `1` | BA1 invite-only gate (defaults on in prod; set explicitly). |
| `BETA_ALLOWLIST` | `a@x.com, b@y.com` or `@team.com` | The Wave-0 emails. **Fail-closed: enabled + empty = everyone denied.** See `docs/BETA_ACCESS_CONTROL.md`. |

### Off-host backups & host data
| Variable | Value | Used by |
|---|---|---|
| `IH_DATA_DIR` | `/opt/ih/data` (default) | Host dir bind-mounted to `/app/data` (DB + backups). Created by `bootstrap-ec2.sh`. |
| `IH_DATA_MOUNT` | `0` (root EBS) / `1` (dedicated volume) | `1` makes `deploy.sh` refuse to start unless `IH_DATA_DIR` is a **mounted** filesystem — prevents a boot-before-mount race from creating a fresh empty DB. |
| `IH_S3_BUCKET` | `my-ih-beta-backups` | Off-host target for the hourly `deploy/ops/backup-offhost.sh` cron + `restore.sh` (host `aws s3`, instance IAM role). Bucket name only. |
| `BACKUP_KEEP` | `48` | Local backups to retain (~2 days hourly). |
| `BACKUP_INTERVAL` | `3600` | Seconds between scheduler backups. |

> **Automated by `bootstrap-ec2.sh`:** an hourly off-host-backup cron (`ih-offhost-backup` → `backup-offhost.sh`)
> and a 5-minute health-monitor cron (`ih-monitor` → `monitor.sh`, alerting to `ALERT_WEBHOOK`). Both read
> this file. Fill `IH_S3_BUCKET` and `ALERT_WEBHOOK` or they no-op.

### Monitoring
| Variable | Value | Used by |
|---|---|---|
| `IH_BASE_URL` | `http://127.0.0.1:8000` | `deploy/ops/healthcheck.sh` / `preflight.sh` engine probe. |
| `ALERT_WEBHOOK` | Slack/Discord webhook, or an SNS wrapper | `healthcheck.sh` alert destination. |

## Optional / must-NOT-set
- `BETA_ALLOWLIST_FILE` — a file re-read per sign-in (add testers with no restart); mount under the data volume.
- `BACKUP_OFFHOST_CMD` — in-container S3 shipment; only if you add an AWS CLI to the image (default ships from the host).
- **Never set** `RWE_DEV_LOGIN` / `NEXT_PUBLIC_DEV_LOGIN` in production — the demo login must stay off.

## Fail-closed behavior (why the defaults are safe)

| Misconfiguration | What happens | Why it's safe |
|---|---|---|
| `RWE_ENV=production` but no `RWE_INTERNAL_SECRET` | Engine refuses to boot; `docker compose up` errors on the `${…:?}` guard | No unauthenticated engine ever serves |
| Web missing a required secret | Web container `process.exit(1)` → crash-loops | Broken deploy never serves a half-configured app |
| `BETA_ACCESS_ENABLED=1`, empty `BETA_ALLOWLIST` | **Everyone denied** (logged) | A misconfigured invite gate stays private, never accidentally open |
| App/engine host ports | Unpublished in prod (`ports: !reset []`) | Only Caddy (443/80) is reachable; engine/app are private |

## Verifying the environment

```bash
set -a; . deploy/.env; set +a
IH_BASE_URL=http://127.0.0.1:8000 deploy/ops/preflight.sh   # env + secrets + HTTPS + OAuth + DB + backup + monitoring
deploy/ops/smoke-test.sh                                     # the RUNNING stack, end-to-end
```
Both must exit 0 before go-live. See `docs/WAVE0_GO_LIVE_CHECKLIST.md`.
