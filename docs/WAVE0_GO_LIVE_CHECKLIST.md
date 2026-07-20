# Wave 0 Go-Live Checklist — AWS EC2 (first 5 users)

The exact, sequential checklist to run **before inviting the first five beta users**, on the AWS EC2
deployment. Follow top-to-bottom; do not send invites until the final **GO gate** is all-green. Grounded
in `docs/AWS_EC2_DEPLOYMENT_GUIDE.md` (§ references below), the `deploy/ops/*` scripts, and the BA1/PA1/
OBS1 endpoints. Documentation only — running it changes no application code.

**Fill first:** domain `hidden-view.com` · region `us-east-1` · app dir `/opt/ih` · release tag
`________` · the **5 Wave-0 emails** `________________________________________`.
On the box: `export COMPOSE="docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.aws.yml --env-file deploy/.env"`; `set -a; . deploy/.env; set +a`.

---

## A · Infrastructure (guide §1–2)
- [ ] EC2 launched: **t3.medium** (or t3.small + 2 GB swap for ≤30), **Ubuntu 24.04 LTS**, **30 GiB gp3**.
- [ ] **Elastic IP** allocated + associated (stable public IP).
- [ ] **Security Group**: inbound **443** + **80** from anywhere, **22** from your IP only (or **SSM**, no SSH). 3000/8000 **not** exposed.
- [ ] **IAM instance role** attached: S3 PutObject to the backup bucket + `AmazonSSMManagedInstanceCore` + `CloudWatchAgentServerPolicy`.
- [ ] **S3 bucket** created, **versioned**, public access blocked, 30-day lifecycle.
- [ ] **Route 53** A record `hidden-view.com → EIP`; `dig +short hidden-view.com` returns the EIP.

## B · Host preparation (guide §2.5–2.7)
- [ ] **`sudo deploy/ops/bootstrap-ec2.sh`** run (idempotent — installs Docker + Compose, AWS CLI, 2 GB swap, container log rotation, creates `/opt/ih/data`, seeds `deploy/.env`). Safe to re-run.
- [ ] Docker Engine + Compose **v2.24+** installed (`docker compose version` — the override needs `!reset`/`!override`); user in `docker` group.
- [ ] 2 GB **swap** enabled (`free -m` shows swap) — OOM insurance for builds.
- [ ] `awscli` installed; `aws sts get-caller-identity` shows the instance role.
- [ ] Repo cloned to `/opt/ih`, **checked out at the release tag** (not a moving branch).
- [ ] `/opt/ih/data` host dir created (bind-mount target for the DB + backups).
- [ ] `deploy/.env` written and **`chmod 600`**.

## C · HTTPS / DNS / OAuth (guide §4)
- [ ] `deploy/Caddyfile` + `deploy/docker-compose.aws.yml` are in the repo (Caddy on 80/443; 3000/8000 unpublished; Caddy→`web:3000` over the internal Docker network; data bind-mounted). Values come from `deploy/.env`. `www.hidden-view.com` redirects to the apex.
- [ ] Google OAuth **redirect URI** = `https://hidden-view.com/api/auth/callback/google`; JS origin = `https://hidden-view.com`.
- [ ] `NEXTAUTH_URL=https://hidden-view.com` (https, exact).

## D · Configuration (guide §3)
Engine + web (in `deploy/.env`, prod-hardening lines uncommented in the override):
- [ ] `RWE_ENV=production` on **both** tiers.
- [ ] `RWE_INTERNAL_SECRET` — strong, **identical** on api and web.
- [ ] `NEXTAUTH_SECRET` — strong (`openssl rand -base64 32`).
- [ ] `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` set.
- [ ] `RWE_BACKEND_URL=http://api:8000`; `RWE_DB_URL=sqlite:////app/data/ih_beta.db` (persistent, not `/tmp`/memory).
- [ ] **BA1:** `BETA_ACCESS_ENABLED=1` **and** `BETA_ALLOWLIST` = the **5 Wave-0 emails** (fail-closed — an empty list denies everyone).
- [ ] No `RWE_DEV_LOGIN` / `NEXT_PUBLIC_DEV_LOGIN` in production.
- [ ] Backup env: `BACKUP_OFFHOST_CMD='aws s3 cp "$1" s3://<bucket>/backups/'`, `BACKUP_KEEP=48`.
- [ ] Monitoring env: `IH_BASE_URL=http://127.0.0.1:8000`, `ALERT_WEBHOOK=<slack/SNS>`.

## E · Deploy + readiness (guide §5)
- [ ] **`deploy/ops/deploy.sh`** run — idempotent: builds + starts ingest→api→web→caddy, gates on engine readiness, enables the backup scheduler, then runs the smoke test. (Manual equivalent: `$COMPOSE up -d --build`.)
- [ ] Engine **readiness 200**: `$COMPOSE exec -T api python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/api/health/ready').status)"` → `200`.
- [ ] TLS valid: `curl -I https://hidden-view.com` → `HTTP/2 200`/redirect, valid cert; `curl -I http://hidden-view.com` → 308 → https.

## F · Verification (guide §6) — the go signals are `smoke-test.sh` + `preflight.sh` exit 0
- [ ] **`deploy/ops/smoke-test.sh`** → **0 FAIL** (containers up; engine live/ready over the internal Docker network; PA1 gated 200-with-secret / 404-without; OBS1 metrics; public HTTPS + valid TLS + HTTP→HTTPS redirect).
- [ ] `deploy/ops/preflight.sh` (config gate — env/secrets/HTTPS/OAuth/DB) → **0 FAIL, exit 0**. Do **not** set `IH_BASE_URL` (the engine port is unpublished; live checks are `smoke-test.sh`'s job, done in-container).
- [ ] **Application:** load `https://hidden-view.com` → onboarding/sign-in renders; charts fit on mobile.
- [ ] **Authentication (allow):** sign in with an **allowlisted** Google account → dashboard.
- [ ] **Authentication (deny):** sign in with an **off-list** account → `/signin?error=AccessDenied` invite-only message; log shows `{"event":"beta_access_denied",…}`.
- [ ] **Smoke:** report renders; a recommendation opens the real publisher URL; feedback works.
- [ ] **PA1:** after the smoke session, `curl -H "X-IH-Auth:$RWE_INTERNAL_SECRET" http://127.0.0.1:8000/api/analytics/funnel` shows reachers > 0; the **unauthenticated** call returns **404** (internal-only).
- [ ] **OBS1:** `/api/health/live` alive; `/api/metrics` (with the secret) shows `request_ms`/`db_query_ms` timers.

## G · Backups & monitoring wired (guide §2, §7)
- [ ] `deploy/ops/backup-offhost.sh --backup-now` → exit 0 (container backup + integrity check + S3 sync; **no host Python**).
- [ ] Backup object present **in S3** (`aws s3 ls s3://<bucket>/backups/`).
- [ ] Off-host backup cron installed: `ls -l /etc/cron.d/ih-offhost-backup` (hourly).
- [ ] **Health monitor cron** installed + working: `ls -l /etc/cron.d/ih-monitor`; `deploy/ops/monitor.sh` → `healthy`; a **test alert reached a human** (temporarily point `ALERT_WEBHOOK` at a test channel, stop `web`, confirm the alert fires, restart).
- [ ] CloudWatch (optional, additive): agent + alarms on **StatusCheckFailed** + high CPU + low disk → SNS.
- [ ] **EBS data volume:** on ROOT EBS → `IH_DATA_MOUNT=0`. On a DEDICATED volume → mounted at `IH_DATA_DIR` via `/etc/fstab` (with `nofail`), `IH_DATA_MOUNT=1`, and a **reboot test** confirms data persists.
- [ ] A **restore drill** rehearsed (`deploy/ops/restore.sh` from an S3 backup); recovery RTO recorded.

## H · Rollback readiness (guide §5, §7)
- [ ] Previous good **release tag** known and reachable (`git tag`).
- [ ] Newest **S3 backup path** noted; the restore steps (guide §7) are understood.
- [ ] `docker` log rotation configured (`/etc/docker/daemon.json` max-size/max-file).

---

## 🚦 GO / NO-GO gate

**GO** only when **all** of these are true:

| # | Gate | Check |
|---|---|---|
| 1 | HTTPS + DNS live | `curl -I https://hidden-view.com` valid TLS |
| 2 | Preflight clean | `deploy/ops/preflight.sh` exit 0 |
| 3 | Auth works both ways | allowlisted in ✓ / off-list denied ✓ |
| 4 | BA1 armed | `BETA_ACCESS_ENABLED=1` + exactly the 5 emails |
| 5 | PA1 capturing | funnel reachers > 0, gated 404/200 |
| 6 | OBS1 healthy | readiness 200; metrics present |
| 7 | Backups verified + off-host + automated | `backup-offhost.sh --backup-now` 0 + object in S3 + `ih-offhost-backup` cron |
| 8 | Restore rehearsed | drill done, RTO known |
| 9 | Monitoring automated + alerts | `ih-monitor` cron + test alert received |
| 10 | Rollback staged | previous tag + backup path ready |

**Any NO → hold and remediate. All YES → send the 5 invites**, then follow the Wave-0 monitoring cadence
in `docs/WAVE0_SUCCESS_PLAN.md` (daily dashboard) and the First-24h checklist in
`docs/BETA_LAUNCH_PLAYBOOK.md`.

*Documentation only — no application code is modified by executing this checklist.*
