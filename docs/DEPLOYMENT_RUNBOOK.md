# Deployment Runbook — Wave 0 (AWS EC2, hidden-view.com)

> ✅ **Executed successfully — Wave 0 live on `hidden-view.com` (2026-07-20).** As-built system of record:
> [`WAVE0_DEPLOYMENT_CLOSEOUT.md`](WAVE0_DEPLOYMENT_CLOSEOUT.md). Validation + fixes:
> [`WAVE0_PRODUCTION_DEPLOYMENT_REPORT.md`](WAVE0_PRODUCTION_DEPLOYMENT_REPORT.md). All ten verification
> phases (provision → bootstrap → env → deploy → HTTPS → OAuth → backup → monitoring → reboot → restore)
> passed. Alerting: set `ALERT_WEBHOOK` in `deploy/.env` to turn health/backup failures into Slack/Discord
> messages (unset = log-only) — see `docs/PRODUCTION_ENVIRONMENT.md` → Alerting.

The operational runbook for the closed beta: the exact commands to stand up, update, roll back, restart,
restore, and **rebuild from scratch**. Everything is script-driven and **idempotent** — safe to re-run.
This is the "how"; `docs/AWS_EC2_DEPLOYMENT_GUIDE.md` is the "why" (architecture + AWS resources).

> DEPLOYMENT-ONLY: no application behavior changes. The scripts wrap `docker compose`, the app's own
> `db_backup.py`, and the OBS1 health endpoints.

## The lifecycle scripts (`deploy/ops/`)

| Script | Purpose |
|---|---|
| `bootstrap-ec2.sh` | Idempotent host prep (Docker, AWS CLI, swap, log rotation, data dir, seeds `deploy/.env`). `sudo`. |
| `deploy.sh` | Build + start the stack, gate on readiness, enable the backup scheduler, run the smoke test. |
| `update.sh [ref]` | Deploy a release tag; with a previous tag = **rollback**. Data untouched. |
| `restart.sh [svc]` | Restart all / one service (re-reads `deploy/.env`). |
| `restore.sh [src]` | Verify-first restore from S3 or a local backup (see `docs/BACKUP_AND_RESTORE.md`). |
| `backup-offhost.sh [--backup-now]` | **Container-based** backup + integrity check + `aws s3 sync` off-host (no host Python). Runs hourly via cron. |
| `monitor.sh` | **Container-based** health monitor (OBS1 + edge containers) → `ALERT_WEBHOOK`. Runs every 5 min via cron. |
| `smoke-test.sh` | Validate the running stack end-to-end (internal + public). |
| `validate-deployment.py` | **Drift guard** — fails if a service enables a capability without the env/mounts/secrets/config files it depends on (rules in `deploy/deployment-rules.json`). Runs in CI + locally. |
| `preflight.sh` | Deterministic PASS/WARN/FAIL of prod prerequisites. |
| `backup.sh` / `verify-restore.sh` / `healthcheck.sh` | Host-Python probes for the **non-Docker** path (need `python`+SQLAlchemy; **not** used on the EC2 host). |

All wrap: `docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.aws.yml --env-file deploy/.env …`.

---

## Deployment-dependency validation (drift guard)

`deploy/ops/validate-deployment.py` prevents one recurring class of production drift: a service turns a
capability **on** but the config it **depends on** is missing, so the stack looks healthy while silently
doing nothing. (The 2026-07-21 incident: `RWE_FEED_POLL=1` on `api` with no `RWE_RSS_FEEDS`/feed-mount →
the poller resolved 0 feeds and ingested nothing.)

**How it works.** It renders the *merged* compose model (`docker compose config`, base + AWS override,
with its own dummy secrets — no real secrets needed) and applies **declarative rules** from
`deploy/deployment-rules.json`. Each rule says *when a service declares a capability, it must require*
certain **environment variables, bind mounts, secrets, and configuration files**. It also lints for
**hardcoded secrets** (any secret-named key must be `${VAR}` from `deploy/.env`, never a literal). Exit 0
= all satisfied; exit 1 = a human-readable report naming the service, what's missing, why it matters, and
the fix.

**Run it:**

```bash
deploy/ops/validate-deployment.py           # validates every stack in the rules file (prod merge + dev)
```

**Add a future check — no code change,** just a rule in `deploy/deployment-rules.json`:

```jsonc
{ "id": "my-feature-deps",
  "when":    { "service": "api", "env_truthy": "RWE_MY_FLAG" },   // trigger: capability declared
  "require": { "env": ["RWE_MY_INPUT"], "mounts": ["/app/x"], "secrets": ["RWE_MY_SECRET"], "files": ["deploy/x.conf"] },
  "why": "…what breaks without it…", "fix": "…how to wire it…" }
```

**In CI:** the `docker` job runs it automatically (`.github/workflows/ci.yml` → *Validate deployment-dependency
wiring*), so a drift like this fails the build before merge.

---

## A · First deploy (existing, bootstrapped instance)

Assumes the AWS resources exist (deployment guide §2): EC2 (Ubuntu 24.04), Elastic IP, Security Group
(443/80 open, 22 from your IP or SSM), IAM role (S3 + SSM + CloudWatch), S3 backup bucket, repo cloned to
`/opt/ih`.

```bash
cd /opt/ih
sudo deploy/ops/bootstrap-ec2.sh        # 1) idempotent host prep; seeds deploy/.env

$EDITOR deploy/.env                       # 2) fill REQUIRED values (see docs/PRODUCTION_ENVIRONMENT.md)
chmod 600 deploy/.env

# 3) DNS must resolve to this host BEFORE deploy (Caddy needs it for the ACME cert):
dig +short hidden-view.com                # → the Elastic IP  (docs/ROUTE53_CONFIGURATION.md)

deploy/ops/deploy.sh                      # 4) build + start + readiness gate + scheduler + smoke test

# 5) full production preflight + a real sign-in, then the go-live checklist:
set -a; . deploy/.env; set +a
IH_BASE_URL=http://127.0.0.1:8000 deploy/ops/preflight.sh
```
Then work through `docs/WAVE0_GO_LIVE_CHECKLIST.md` and do not invite users until its GO gate is all-green.

---

## B · Deploy a new version
```bash
cd /opt/ih
deploy/ops/update.sh <new-tag>            # fetch + checkout + rebuild + readiness + smoke test
```
Data is untouched (it lives on the host bind-mount, independent of the code checkout). Tag releases so
rollback targets are explicit (`git tag`).

## C · Rollback (app fault)
```bash
deploy/ops/update.sh <previous-good-tag>  # redeploy the last known-good release
```
For a **data** fault (corruption), roll back data instead — section E.

## D · Restart / change config (e.g. add a beta tester)
```bash
$EDITOR deploy/.env                       # e.g. append an email to BETA_ALLOWLIST
deploy/ops/restart.sh web                 # re-reads env (or restart.sh for all services)
```
(If you use `BETA_ALLOWLIST_FILE`, edit the file — it's re-read per sign-in, **no restart** needed.)

## E · Restore data
See `docs/BACKUP_AND_RESTORE.md`. Short form:
```bash
deploy/ops/restore.sh s3://$IH_S3_BUCKET/backups/ih_beta-<ts>.db   # verify-first, safe swap, re-validate
```

---

## F · EC2 Rebuild Runbook — brand-new instance from scratch

Recover the whole deployment onto a **fresh Ubuntu 24.04 instance** with no prior state. Assumes only:
the AWS account, the S3 backup bucket (with your latest backup), and the domain. No tribal knowledge.

### F.1 Provision AWS (console or CLI — deployment guide §2)
1. **IAM role** `ih-ec2-role`: S3 (`PutObject`/`GetObject`/`ListBucket` on the backup bucket) +
   `AmazonSSMManagedInstanceCore` + `CloudWatchAgentServerPolicy`; make an instance profile.
2. **Security Group**: inbound 443 + 80 from `0.0.0.0/0`; 22 from your IP only (or SSM, no SSH). Nothing else.
3. **Launch** t3.medium (or t3.small for ≤30 users), Ubuntu 24.04 LTS, 30 GiB gp3, the IAM instance profile, the SG.
4. **Elastic IP**: allocate + associate (or **re-associate the existing EIP** — then DNS & OAuth need no change).

### F.2 DNS
If you kept the EIP, DNS already points here. Otherwise update the A records `hidden-view.com` + `www` →
new EIP and wait for propagation (`dig +short hidden-view.com`). See `docs/ROUTE53_CONFIGURATION.md`.

### F.3 Host prep + code
```bash
ssh ubuntu@<eip>            # or: aws ssm start-session --target <instance-id>
sudo mkdir -p /opt/ih && sudo chown ubuntu:ubuntu /opt/ih
git clone <repo-url> /opt/ih && cd /opt/ih
git checkout <release-tag>                 # the version you were running
sudo deploy/ops/bootstrap-ec2.sh           # Docker, swap, AWS CLI, log rotation, /opt/ih/data, seeds deploy/.env
```

### F.4 Configuration
```bash
$EDITOR deploy/.env                         # restore the SAME values (secrets from your secret store)
chmod 600 deploy/.env
```
Reuse the **same** `RWE_INTERNAL_SECRET`, `NEXTAUTH_SECRET`, and Google OAuth client. (A new
`NEXTAUTH_SECRET` just forces everyone to sign in again — acceptable, not required.)

### F.5 Start, then restore data (verify-first)
```bash
deploy/ops/deploy.sh                          # build + start + readiness + scheduler + smoke test
                                              #   (starts on a fresh empty DB — replaced next)
# Restore the newest off-host backup through the verify-first path (container integrity check → safe swap):
newest="$(aws s3 ls s3://$IH_S3_BUCKET/backups/ | sort | tail -1 | awk '{print $4}')"
FORCE=1 deploy/ops/restore.sh "s3://$IH_S3_BUCKET/backups/$newest"
```
`restore.sh` verifies the backup **inside the container** (no host Python), snapshots, swaps, restarts,
and re-runs the smoke test. Reuse the same `RWE_INTERNAL_SECRET` so nothing else changes.

### F.6 Verify — monitoring & backups are already automatic
```bash
set -a; . deploy/.env; set +a
IH_BASE_URL=http://127.0.0.1:8000 deploy/ops/preflight.sh      # exit 0
deploy/ops/smoke-test.sh                                        # exit 0
ls -l /etc/cron.d/ih-offhost-backup /etc/cron.d/ih-monitor     # crons re-installed by bootstrap-ec2.sh
```
`bootstrap-ec2.sh` already re-installed the hourly off-host-backup and 5-minute health-monitor crons and
the docker-after-mount ordering, so there is **no host cron to hand-wire**. (CloudWatch alarms, if you use
them, are the one thing to re-attach — deployment guide §7.) Done — the rebuild is live.

**Rebuild RTO** is dominated by DNS propagation (if the EIP changed) and image build (~a few minutes);
plan for well under an hour.

---

## G · Routine operations

**Logs** (capped by `deploy/host/daemon.json`, rotated):
```bash
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.aws.yml --env-file deploy/.env logs -f api
# correlate by requestId in the structured JSON (OBS1)
```
**Disk / memory:** `df -h`, `free -m`, `docker system prune -f` (reclaim image space).

**Rotate secrets:**
```bash
# NEXTAUTH_SECRET (invalidates ALL sessions → everyone re-signs-in):
openssl rand -base64 32     # set in deploy/.env, then: deploy/ops/restart.sh web
# RWE_INTERNAL_SECRET (change on BOTH tiers together):
openssl rand -base64 32     # set once (shared) in deploy/.env, then: deploy/ops/restart.sh
```

## H · Troubleshooting

| Symptom | Check | Fix |
|---|---|---|
| `docker compose up` errors `RWE_INTERNAL_SECRET … must be set` | `deploy/.env` incomplete | Fill REQUIRED values (`docs/PRODUCTION_ENVIRONMENT.md`) |
| web container crash-loops | `… logs web` → env validation error | Set the missing var; `restart.sh web` |
| No HTTPS / cert pending | `dig +short hidden-view.com`; SG 80 open; `… logs caddy` | Fix DNS/SG; Caddy retries automatically |
| `smoke-test.sh` public checks FAIL | DNS not live / SG / Caddy down | Section above; internal checks isolate app vs edge |
| engine not ready | `… logs api`; ingest completed? | Wait; a flaky feed doesn't block (falls back to profile) |
| `!reset`/`!override` error at `config` | Compose < 2.24 | Upgrade Docker (`bootstrap-ec2.sh` warns) |

---

*Related: `docs/AWS_EC2_DEPLOYMENT_GUIDE.md` (architecture), `docs/PRODUCTION_ENVIRONMENT.md` (env),
`docs/BACKUP_AND_RESTORE.md`, `docs/GOOGLE_OAUTH_CONFIGURATION.md`, `docs/ROUTE53_CONFIGURATION.md`,
`docs/WAVE0_GO_LIVE_CHECKLIST.md`.*
