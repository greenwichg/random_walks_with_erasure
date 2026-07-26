# AWS EC2 Deployment Guide — Information Health (Wave 0 closed beta, 5–150 users)

> ✅ **DEPLOYED — Wave 0 is live on `hidden-view.com` (2026-07-20).** This guide is the "why". The
> **as-built** record is [`WAVE0_DEPLOYMENT_CLOSEOUT.md`](WAVE0_DEPLOYMENT_CLOSEOUT.md) (architecture, AWS
> resources, runtime topology, security, DR, GA roadmap); the execution log is
> [`WAVE0_PRODUCTION_DEPLOYMENT_REPORT.md`](WAVE0_PRODUCTION_DEPLOYMENT_REPORT.md) (validation + fixes).
> Post-deploy hardening already folded into the repo artifacts: the web→engine sign-in auth header,
> `RWE_COACH_V2` wired onto the `api` service, the Caddy admin-API healthcheck, and full `ALERT_WEBHOOK`
> (Slack/Discord) support.

Senior-DevOps guide to running this project on a single AWS EC2 instance for the closed beta. It reuses
the repo's existing artifacts — `deploy/docker-compose.yml`, `deploy/Dockerfile.{api,web}`, the
`deploy/ops/*` scripts (`preflight.sh`, `backup.sh`, `verify-restore.sh`, `healthcheck.sh`), the OBS1
health/metrics endpoints, the PA1 analytics endpoints, and the BA1 allowlist — and adds only *operator
configuration* (a Caddy reverse proxy + a compose override). **No application code is changed.**

> **These artifacts now live in the repo** (they are no longer hand-created on the box): the compose
> override `deploy/docker-compose.aws.yml`, the reverse proxy `deploy/Caddyfile`, the env template
> `deploy/.env.production.example`, the log-rotation sample `deploy/host/daemon.json`, and the idempotent
> lifecycle scripts `deploy/ops/{bootstrap-ec2,deploy,update,restart,restore,smoke-test}.sh`. The
> **step-by-step, script-driven workflow is `docs/DEPLOYMENT_RUNBOOK.md`** (this guide is the architecture
> + rationale reference). Pairs with `DEPLOYMENT.md`, `docs/PRODUCTION_ENVIRONMENT.md`,
> `docs/BACKUP_AND_RESTORE.md`, `docs/GOOGLE_OAUTH_CONFIGURATION.md`, `docs/ROUTE53_CONFIGURATION.md`.

Conventions: region `us-east-1` (adjust as needed), domain **`hidden-view.com`** (apex; `www` redirects
to it), app dir `/opt/ih`, host data dir `/opt/ih/data`, `$` = shell on the instance.

---

## 1 · AWS architecture

The whole system is two containers (FastAPI engine + Next.js web) that talk over a private Docker
network, fronted by a TLS-terminating reverse proxy. For 5–150 users this fits comfortably on **one
small instance** — no load balancer, no RDS, no ECS. Keep it boring; the SQLite-on-a-volume design the
app already uses is the right size for a beta.

```
        Internet ──443/80──▶ [ Caddy ]  (auto-HTTPS, Let's Encrypt)
 EC2 (Ubuntu 24.04, Docker)      │  reverse_proxy  (private docker network)
                                 ▼
                            [ web:3000 ]  Next.js  ── server-to-server ─▶ [ api:8000 ] FastAPI engine
                                                                                │  SQLite (WAL) on
                                                                                ▼  a persistent volume
                                                                          /opt/ih/data  (bind mount)
   backups: db_backup.py (integrity-checked) ── host cron: aws s3 sync ─▶ [ S3 bucket ] (off-host, versioned)
   monitoring: healthcheck.sh + CloudWatch agent/alarms ─▶ [ SNS ] email/SMS
```

| Component | Choice | Why |
|---|---|---|
| **EC2 instance** | **t3.medium** (2 vCPU / 4 GiB) recommended; **t3.small** (2 GiB) minimum for ≤30 users **with 2 GB swap**. Graviton **t4g.medium** is ~20% cheaper (arm64 wheels exist for numpy/scipy; images are multi-arch). | The engine builds an in-memory recommender at startup and `next build` runs on the box — 4 GiB avoids OOM during builds and leaves headroom. t3/t4g burstable is ideal for bursty beta traffic and is the cheapest production-viable family. |
| **OS** | **Ubuntu Server 24.04 LTS** (Noble; 22.04 LTS also fine) | Current LTS (support to 2029), first-class Docker packages, what the team knows. |
| **Root EBS** | **30 GiB gp3** (20 GiB floor) | Holds the OS + two container images (~1.5 GB) + the SQLite DB & WAL (small) + local backups + logs. gp3's baseline 3000 IOPS / 125 MB/s is plenty for SQLite; you pay only for size. Off-host backups keep local growth bounded. |
| **Security Group** | Inbound: **443** and **80** from `0.0.0.0/0`; **22** from **your IP only** (or use **SSM Session Manager** and open no SSH). Everything else denied. Do **not** expose 3000/8000. Outbound: allow 443/80 (OAuth, Let's Encrypt, S3, RSS feeds). | Least exposure. 80 is required for Let's Encrypt HTTP-01 + an HTTPS redirect. The engine (8000) and web (3000) are never internet-reachable — only Caddy is. |
| **Elastic IP** | **Yes** — allocate one, associate to the instance. | A stable public IP survives stop/start so the Route 53 A record and the Google OAuth callback URL never change. (Note: AWS now bills ~$3.65/mo per in-use public IPv4 — see §8.) |
| **Route 53** | Hosted zone for your domain + an **A record** `hidden-view.com → EIP`. | AWS-native DNS; low TTL makes cutovers fast. If your domain lives at another registrar, an A record there works identically — Route 53 is convenient, not required. |
| **HTTPS** | **Caddy** container (see §4), automatic Let's Encrypt certs + renewal. | Zero-config TLS; one line per host. (Nginx + certbot is the alternative if you need fine-grained control — more moving parts.) |
| **S3 backups** | Private, **versioned** bucket with a lifecycle rule; write via the instance **IAM role** (no static keys). | 11-nines durability, off-host — a lost EBS volume or terminated instance never loses user data. This satisfies the BR1 "off-host backups" launch blocker. |
| **CloudWatch** | CloudWatch **agent** (mem + disk), **alarms** on StatusCheckFailed / high CPU / low disk → **SNS** email/SMS; plus `deploy/ops/healthcheck.sh` on cron probing the OBS1 endpoints. | AWS-native alerting closes the BR1 "nobody is paged" gap. Basic EC2 metrics are free; the agent adds memory/disk which EC2 doesn't emit by default. |

**Why not more?** No ALB (a single instance needs none; Caddy terminates TLS), no RDS/Redis (SQLite +
in-process rate limiter are sufficient and already built), no ECS/EKS (one box is simpler to operate for
a 5–150 user beta). Scale-up is a bigger instance; scale-out (Postgres, multi-node) is an explicit
post-beta roadmap item, not a Wave-0 need.

---

## 2 · AWS setup (step by step)

### 2.1 Prerequisites (once)
- An AWS account, the **AWS CLI** configured locally, and a registered domain.
- A **Google OAuth Client** (Web application) — you'll set its redirect URI in §4.

### 2.2 Create the S3 bucket + IAM role (before the instance)
```bash
# S3 bucket for off-host backups (private, versioned)
aws s3api create-bucket --bucket acme-ih-backups --region us-east-1
aws s3api put-bucket-versioning --bucket acme-ih-backups \
  --versioning-configuration Status=Enabled
aws s3api put-public-access-block --bucket acme-ih-backups \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
# Lifecycle: expire backup objects after 30 days (keep noncurrent versions 30 days too) — set via console or a JSON rule.
```
Create an **IAM role** `ih-ec2-role` (trusted by `ec2.amazonaws.com`) with an inline least-privilege
policy allowing `s3:PutObject`/`s3:ListBucket`/`s3:GetObject` on `arn:aws:s3:::acme-ih-backups[/*]`,
attach the managed **`AmazonSSMManagedInstanceCore`** policy (enables Session Manager — no SSH port
needed), and add **`CloudWatchAgentServerPolicy`**. Create an instance profile from it.

### 2.3 Launch the EC2 instance
Console → Launch instance, or CLI:
```bash
aws ec2 run-instances \
  --image-id <ubuntu-24.04-ami-id> \        # find with: aws ec2 describe-images --owners 099720109477 ...
  --instance-type t3.medium \
  --key-name <your-keypair> \
  --iam-instance-profile Name=ih-ec2-role \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":30,"VolumeType":"gp3"}}]' \
  --security-group-ids <sg-id> \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=ih-beta}]'
```
Then allocate + associate an **Elastic IP**, and point Route 53 `hidden-view.com` at it:
```bash
aws ec2 allocate-address --domain vpc
aws ec2 associate-address --instance-id <i-...> --allocation-id <eipalloc-...>
# Route 53: create an A record hidden-view.com -> <EIP> (console or change-resource-record-sets)
```

### 2.4 Security group rules
| Type | Port | Source | Purpose |
|---|---|---|---|
| HTTPS | 443 | 0.0.0.0/0, ::/0 | app traffic |
| HTTP | 80 | 0.0.0.0/0 | Let's Encrypt HTTP-01 + →HTTPS redirect |
| SSH | 22 | **your.ip.addr/32** | admin (omit entirely if using SSM) |

Prefer **SSM Session Manager** (`aws ssm start-session --target <i-...>`) over opening 22 — no inbound
SSH, audited sessions. If you keep SSH, add `~/.ssh/config`:
```
Host ih-beta
  HostName hidden-view.com
  User ubuntu
  IdentityFile ~/.ssh/<your-keypair>.pem
```

### 2.5 Install Docker + Compose (on the instance)
```bash
sudo apt-get update && sudo apt-get -y upgrade
# add 2 GB swap (protects against OOM on small instances / during builds)
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
# Docker Engine + Compose plugin (official convenience script)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu && newgrp docker
docker --version && docker compose version   # compose v2 ships as a plugin
sudo apt-get -y install awscli               # for S3 backup sync (or use the v2 installer)
```

### 2.6 Clone the repository
```bash
sudo mkdir -p /opt/ih && sudo chown ubuntu:ubuntu /opt/ih
git clone <repo-url> /opt/ih && cd /opt/ih
git checkout <release-tag>          # deploy a pinned tag, never a moving branch
mkdir -p /opt/ih/data               # host-visible data dir (bind-mounted below → backups reachable for S3)
```

### 2.7 Configure environment
Create `/opt/ih/deploy/.env` (compose reads it for `${VAR}` interpolation) — see §3 for every value —
and lock it down:
```bash
umask 077
$EDITOR /opt/ih/deploy/.env       # paste the values from §3
chmod 600 /opt/ih/deploy/.env
```
For a stronger posture, keep secrets in **SSM Parameter Store (SecureString)** or **Secrets Manager**
and render `.env` at deploy time; the env-file is acceptable for a closed beta if permissions are 600
and the box is access-controlled.

---

## 3 · Production configuration (every required variable)

Split by tier. The repo's `deploy/docker-compose.yml` already contains these lines **commented** under
"production hardening" — in your `/opt/ih/deploy/.env` set the values, and uncomment the matching lines
in your copy of the compose (config edit on your box, not an app-code change). Full reference:
`DEPLOYMENT.md` §Configuration.

### Engine (api) — required in production
| Variable | Value / how to set | Notes |
|---|---|---|
| `RWE_ENV` | `production` | Turns on fail-closed auth; the engine **refuses to boot** without the secret below. |
| `RWE_INTERNAL_SECRET` | `openssl rand -base64 32` | **Identical** value on api and web. The only thing that authenticates the web→engine calls. |
| `RWE_DB_URL` | `sqlite:////app/data/ih_beta.db` | Persistent path on the mounted volume. Prod refuses an in-memory/`/tmp` value. |
| `RWE_RECS_SOURCE` | `feed` | Source recommendations from the ingested RSS catalog (real publisher URLs). Falls back to static if the catalog is small. |
| `RWE_LOG_LEVEL` | `INFO` | Structured JSON request logs (OBS1). |

### Web (Next.js) — required in production
| Variable | Value / how to set | Notes |
|---|---|---|
| `RWE_ENV` | `production` | Disables the dev demo-login; enables BA1 by default; the web tier **refuses to boot** if the vars below are missing. |
| `RWE_INTERNAL_SECRET` | *(same as engine)* | Sent as `X-IH-Auth` on server-to-server calls. |
| `RWE_BACKEND_URL` | `http://api:8000` | The engine's service name on the Docker network (never public). |
| `NEXTAUTH_URL` | `https://hidden-view.com` | **https**, exact public URL. Drives OAuth callbacks + `Secure` cookies. |
| `NEXTAUTH_SECRET` | `openssl rand -base64 32` | Signs session JWTs. Rotating it invalidates all sessions (see §7). |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | from Google Cloud Console | The only sign-in method in prod. |
| **`BETA_ACCESS_ENABLED`** | `1` | BA1 invite-only gate (defaults on in prod; set explicitly to be sure). |
| **`BETA_ALLOWLIST`** | `a@x.com, b@y.com, …` (the 5 Wave-0 emails) or `@yourteam.com` | Fail-closed: enabled + empty = **everyone denied**. See `docs/BETA_ACCESS_CONTROL.md`. |
| `BETA_ALLOWLIST_FILE` | *(optional)* `/app/data/allowlist.txt` | A file re-read per sign-in (add testers with **no restart**); mount it via the data volume. |

### Backups & monitoring (host-side)
| Variable | Value | Used by |
|---|---|---|
| `BACKUP_KEEP` | `48` | `deploy/ops/backup.sh` / the `scheduler` profile (local retention). |
| `BACKUP_OFFHOST_CMD` | `aws s3 cp "$1" s3://acme-ih-backups/backups/` | Off-host shipment (instance IAM role supplies S3 auth — no keys). |
| `IH_BASE_URL` | `http://127.0.0.1:8000` | `deploy/ops/healthcheck.sh` engine probe. |
| `ALERT_WEBHOOK` | Slack/Discord webhook, or a wrapper that runs `aws sns publish` | healthcheck alert destination. |

> **Do not** put `RWE_DEV_LOGIN` or `NEXT_PUBLIC_DEV_LOGIN` in production — the demo login must stay off.

---

## 4 · HTTPS with Caddy (automatic Let's Encrypt)

**Caddy** — automatic certificate issuance + renewal, one line of config. Both files below are **committed
to the repo** (`deploy/Caddyfile` and `deploy/docker-compose.aws.yml`); you no longer hand-write them —
you just supply values in `deploy/.env`. Caddy is the **only** internet-facing service and it reaches the
app at `web:3000` over the **private Docker network** (service name, not a host port), so 3000/8000 are
never exposed.

`deploy/Caddyfile` (apex served; `www` 308-redirects to it; `{$APP_DOMAIN}` comes from `deploy/.env`):
```
{
    email {$ACME_EMAIL:ops@hidden-view.com}
}
{$APP_DOMAIN:hidden-view.com} {
    encode zstd gzip
    reverse_proxy web:3000          # internal Docker network — never a host port
}
www.{$APP_DOMAIN:hidden-view.com} {
    redir https://{$APP_DOMAIN:hidden-view.com}{uri} permanent
}
```

`/opt/ih/deploy/docker-compose.aws.yml` (an override layered on the repo compose — adds Caddy, stops
publishing 3000/8000 to the internet, and bind-mounts data to the host so backups are S3-reachable):
```yaml
services:
  caddy:
    image: caddy:2
    restart: unless-stopped
    ports: ["80:80", "443:443"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data          # persisted certs — survives redeploys
      - caddy_config:/config
    depends_on: [web]
  web:
    ports: []                     # reach web only via Caddy on the private network
    environment:
      RWE_ENV: production
      RWE_INTERNAL_SECRET: ${RWE_INTERNAL_SECRET}
      NEXTAUTH_URL: ${NEXTAUTH_URL}
      NEXTAUTH_SECRET: ${NEXTAUTH_SECRET}
      GOOGLE_CLIENT_ID: ${GOOGLE_CLIENT_ID}
      GOOGLE_CLIENT_SECRET: ${GOOGLE_CLIENT_SECRET}
      BETA_ACCESS_ENABLED: "1"
      BETA_ALLOWLIST: ${BETA_ALLOWLIST}
  api:
    ports: []                     # engine never internet-exposed
    environment:
      RWE_ENV: production
      RWE_INTERNAL_SECRET: ${RWE_INTERNAL_SECRET}
  # bind-mount data to the host so db_backup output is reachable for `aws s3 sync`
  # (apply the same `volumes:` override to api / backup-scheduler as needed)
volumes:
  caddy_data:
  caddy_config:
```
> To make the SQLite DB + backups host-visible for S3, also override each data-bearing service's volume
> from `ih-data:/app/data` to `/opt/ih/data:/app/data` (bind mount). Keep it consistent across `api`,
> `backup`, and `backup-scheduler`.

**DNS:** the Route 53 A record `hidden-view.com → EIP` (from §2) must resolve **before** first start so
Caddy can complete the ACME HTTP-01 challenge (port 80 open). Verify: `dig +short hidden-view.com`.

**Google OAuth:** in the Google console set the **Authorized redirect URI** to
`https://hidden-view.com/api/auth/callback/google` and Authorized JavaScript origin to
`https://hidden-view.com`; set `NEXTAUTH_URL=https://hidden-view.com`.

*(Nginx alternative: run `nginx` + `certbot --nginx` with a server block `proxy_pass
http://127.0.0.1:3000;` and a cron `certbot renew`. Caddy removes all of that.)*

---

## 5 · Deployment

**The one-liner:** `deploy/ops/deploy.sh` — it is **idempotent** (safe to re-run on a fresh instance),
builds + starts the stack, gates on engine readiness, enables the backup scheduler, and runs
`deploy/ops/smoke-test.sh`. First-time host prep is `sudo deploy/ops/bootstrap-ec2.sh`; new releases and
rollbacks are `deploy/ops/update.sh [tag]`. See `docs/DEPLOYMENT_RUNBOOK.md` for the full sequence.

Under the hood it runs (from `/opt/ih`, layering the override so `-f` order matters — later wins):
```bash
export COMPOSE="docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.aws.yml --env-file deploy/.env"

# 1) Build + start (ingest → api → web → caddy), in dependency order (compose encodes it)
$COMPOSE up -d --build

# 2) Gate on readiness before anything else
until docker compose -f deploy/docker-compose.yml exec -T api \
      python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health/ready').status==200 else 1)"; \
      do echo "waiting for engine ready…"; sleep 5; done

# 3) Enable recurring local backups (BR1 scheduler profile)
$COMPOSE --profile scheduler up -d backup-scheduler

# 4) Verify (see §6) — the two gates:
set -a; . deploy/.env; set +a
deploy/ops/preflight.sh          # CONFIG gate: env/secrets/HTTPS/OAuth/DB checks — must exit 0
deploy/ops/smoke-test.sh         # LIVE gate: containers + engine ready + PA1/OBS1 + public TLS — must exit 0
```
> Do **not** pass `IH_BASE_URL=http://127.0.0.1:8000` to `preflight.sh` on this stack — the engine port is
> unpublished (only Caddy is exposed), so a host-side probe of `127.0.0.1:8000` would false-FAIL. The live
> engine/OBS1/PA1 checks are done **inside the container** by `smoke-test.sh` (`docker compose exec`).

**Verification** = §6. **Smoke test** = the Smoke checklist in `docs/BETA_LAUNCH_CHECKLIST.md`
(sign-in, report, recommendation, mobile).

**Rollback** (reuse the playbook's standing rule):
```bash
# App fault → redeploy the previous release tag (data untouched):
git fetch --tags && git checkout <previous-good-tag>
$COMPOSE up -d --build
# Data fault (integrity ≠ ok / corruption) → restore from S3 (see §7 "restore backup").
```

---

## 6 · Production verification

Run after every deploy: two gates — `deploy/ops/preflight.sh` (config) and `deploy/ops/smoke-test.sh`
(live, container-based) — plus a manual sign-in.

| Target | Command | Expected |
|---|---|---|
| **Application** | `curl -fsS -o /dev/null -w '%{http_code}\n' https://hidden-view.com` | 200 (or an auth redirect) over valid TLS |
| **Health endpoints (OBS1)** | `docker compose … exec -T api curl -fsS http://127.0.0.1:8000/api/health/ready` | `{"status":"ready",…}` (live also `alive`) |
| **Authentication** | Sign in with an **allowlisted** Google account → dashboard; then a **non-allowlisted** account → `/signin?error=AccessDenied` invite-only message (BA1) | both behave as expected |
| **PA1 analytics** | `curl -fsS -H "X-IH-Auth:$RWE_INTERNAL_SECRET" http://127.0.0.1:8000/api/analytics/funnel` (via the api container/localhost); and `…/api/analytics/funnel` **without** the header | 200 with reachers > 0 after the smoke session; **404** unauthenticated (internal-only) |
| **OBS1 metrics** | `curl -fsS -H "X-IH-Auth:$RWE_INTERNAL_SECRET" …/api/metrics` | `request_ms` / `report_generate_ms` / `db_query_ms` timers present |
| **Backups (container-based)** | `deploy/ops/backup-offhost.sh --backup-now`; `aws s3 ls s3://<bucket>/backups/` | backup + integrity `quickCheck ok` + object in S3 |
| **DB integrity** | `docker compose … --profile backup run --rm backup python examples/db_backup.py status` | `quickCheck ok` |

Two go-signals: **`preflight.sh` exit 0** (config — env/secret/HTTPS/OAuth/DB) **and `smoke-test.sh` exit 0**
(live — containers, engine readiness, PA1 gating, OBS1 metrics, public TLS + redirect, all via the
container). Do **not** set `IH_BASE_URL` for `preflight.sh` here — the engine port is unpublished, so a
host-side `127.0.0.1:8000` probe would false-FAIL; `smoke-test.sh` does the live checks correctly.

### 6a · GKG event-geography enricher — first-cycle verification (Location Intelligence Phase 2)

> **One-shot version of everything below:** `deploy/ops/verify-event-country.sh` runs the whole
> Event Country checklist (flag → enricher health → side-table data → selector options →
> event-semantics negative control → "All" intact → public probe) as PASS/WARN/FAIL lines.
> The manual commands below remain for digging into any line that isn't green.

`RWE_GDELT_GKG` defaults **ON** in the compose file (kill switch: `RWE_GDELT_GKG=0` in
`deploy/.env` + restart). It downloads GDELT's latest 15-minute GKG file each cycle and locates
articles already in the catalog — it never creates articles. After the first ~15–30 minutes:

```bash
# 1) Cycle ran and what it did (records parsed / catalog matches / articles located):
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.aws.yml \
  logs api | grep -i "gdelt://gkg\|GDELT-GKG" | tail -5

# 2) Health row for the enricher (server-to-server, from inside the api container):
docker compose … exec -T api curl -fsS -H "X-IH-Auth:$RWE_INTERNAL_SECRET" \
  http://127.0.0.1:8000/api/internal/feeds | python3 -m json.tool | grep -A6 'gdelt://gkg'

# 3) Event rows actually persisted (the side table the Country filter reads):
docker compose … --profile backup run --rm backup python - <<'EOF'
import sqlite3
db = sqlite3.connect("/app/data/ih_beta.db")
n, = db.execute("SELECT COUNT(*) FROM article_event_locations").fetchone()
top = db.execute("SELECT country, COUNT(DISTINCT canonical_url) FROM article_event_locations "
                 "GROUP BY country ORDER BY 2 DESC LIMIT 8").fetchall()
print("event rows:", n, "| top countries:", top)
EOF

# 4) Product surface: the countries facets fill, and the Stories Country filter appears:
docker compose … exec -T api curl -fsS http://127.0.0.1:8000/api/places/countries | head -c 400
# then in the browser: /stories → the Country dropdown offers only event-located countries,
# and a selection shows stories HAPPENING there (publisher homes never match).
```

**Cold-start backfill — automatic.** Each cycle looks back `RWE_GDELT_GKG_WINDOWS` 15-minute
windows (default 4 = 1 hour), which keeps steady-state cycles cheap but only covers recently
ingested articles. The existing catalog was processed by GDELT hours-to-days ago, so the
enricher detects the cold start itself: **a barely-located catalog — fewer event rows than
`RWE_GDELT_GKG_BACKFILL_THRESHOLD` (default 25) — makes the first cycle per process deep
automatically** (`RWE_GDELT_GKG_BACKFILL_WINDOWS`, default 96 = 24 h; the
cycle's health/log line carries `backfill=True`). Nothing to run, nothing to revert. Manual
control remains: set `RWE_GDELT_GKG_BACKFILL_WINDOWS=0` to disable the auto-backfill, and/or
pin `RWE_GDELT_GKG_WINDOWS` for a one-off deep cycle the old way (override → restart → wait one
cycle → remove → restart). To re-run a backfill later (e.g. after clearing the side table),
the same threshold rule triggers it again on the next container start.

Expected shape: after the backfill cycle, `matched`/`located` > 0 **for articles GDELT
monitors** (GDELT-ingested articles match within the lookback; RSS articles match when GDELT
also covers that outlet) and the Stories Country dropdown appears; steady-state cycles then
keep pace. `records > 0, matched == 0` AFTER a 96-window backfill means the catalog genuinely
doesn't overlap GDELT's monitored set — expected on a tiny/stale catalog, not a fault.
`windowErrors` > 0 with the rest healthy = transient GDELT gaps; the next cycle re-covers them
(overlapping lookback). Rollback: set `RWE_GDELT_GKG=0`, restart — pickers go empty (honest),
nothing else changes, and already-persisted event rows remain valid.

### 6b · NewsAPI source — enablement + first-cycle verification

NewsAPI rides the SAME multi-source pipeline as RSS/GDELT (canonical-URL dedup — cross-source
included — scoring, registry publisher/lean/country resolution, story clustering); the adapter
is `sources.NewsAPIAdapter`, default **OFF**. Enable in `deploy/.env`, then restart the api:

```bash
RWE_NEWSAPI_ENABLED=1
RWE_NEWSAPI_API_KEY=<your key>            # https://newsapi.org — free tier ≈ 100 requests/day
# optional, comma-separated lists are ROTATED one combination per cycle:
# RWE_NEWSAPI_COUNTRY=us,gb   RWE_NEWSAPI_CATEGORY=business,technology
```

**Budget math (the design constraint):** the default 900 s interval spends 96 requests/day at
exactly one request per cycle. Rotation is why lists are safe: N combinations widen coverage
across cycles without multiplying requests. `RWE_NEWSAPI_DAILY_BUDGET` (compose default 90)
short-circuits cycles BEFORE any request once the per-UTC-day count is spent — those cycles log
`newsapi_budget_exhausted` and report `budgetExhausted` instead of touching the health row.

**First-cycle verification** (after one interval, ~15 min):

```bash
cd /opt/ih && source deploy/ops/_compose.sh
dc exec -T api python - <<'PY'
import json, os, urllib.request
req = urllib.request.Request("http://127.0.0.1:8000/api/internal/feeds",
                             headers={"X-IH-Auth": os.environ.get("RWE_INTERNAL_SECRET", "")})
rows = json.loads(urllib.request.urlopen(req, timeout=15).read())
print(json.dumps([r for r in rows if "newsapi" in json.dumps(r).lower()], indent=2))
PY
```

Healthy = a `newsapi://top-headlines` row with a recent `lastSuccess`. Per-cycle aggregates in
the api logs carry `new` / `duplicates` / `failed` / `rawCount` / **`rateLimited`** (HTTP 429s
the retry loop absorbed) — a persistently non-zero `rateLimited` means the interval × combos
outpaces your plan; raise the interval or the budget guard will start skipping. Duplicate-safety
needs no verification step: an article seen by both RSS and NewsAPI merges into ONE row by
canonical URL (first-seen wins; media by source priority) — the same discipline as GDELT.
A startup log line `RWE_NEWSAPI_ENABLED is set but RWE_NEWSAPI_API_KEY is missing` means the
flag is on without a key: the adapter stays off until the key lands (also enforced by
`deploy/ops/validate-deployment.py`, rule `newsapi-key`). Rollback: `RWE_NEWSAPI_ENABLED=0` +
restart — already-ingested NewsAPI articles remain (they are ordinary catalog rows).

### 6c · Additional providers — Guardian, NewsData, GNews, MediaStack, Currents, Google News RSS

All six ride the SAME shared pipeline and (for the keyed five) the same chassis as NewsAPI, so
everything in §6b — rotation semantics, budget short-circuit, `rateLimited` accounting, the
flag-without-key startup warning, the rollback story — applies verbatim; only the env prefix and
the free-tier numbers change. Publishers resolve through `examples/data/outlet_registry.csv`:
rated outlets arrive with their verified lean; unknown outlets ingest honestly unrated (they
appear in `unknown_outlets` stats and the `outlet_coverage.py` worklist — curate, never guess).

| Provider | Prefix (`RWE_<P>_*`) | Free tier | Interval default | Budget default | Health key |
|---|---|---|---|---|---|
| The Guardian | `GUARDIAN` | ~500 req/day | 900 s (96/day) | 450 | `guardian://search` |
| NewsData.io | `NEWSDATA` | ~200 credits/day, size ≤ 10 | 900 s (96/day) | 190 | `newsdata://latest` |
| GNews | `GNEWS` | ~100 req/day, max ≤ 10 | 900 s (96/day) | 95 | `gnews://top-headlines` |
| MediaStack | `MEDIASTACK` | **~500 req/MONTH** | 5400 s (16/day) | 15 | `mediastack://news` |
| Currents | `CURRENTS` | ~600 req/day | 900 s (96/day) | 550 | `currents://latest-news` |
| Google News RSS | `GOOGLENEWS` | keyless | 900 s | — (no key, no budget) | `googlenews://rss` |

Enablement is uniform — in `deploy/.env` set `RWE_<P>_ENABLED=1` (+ `RWE_<P>_API_KEY=<key>` for
the keyed five), then `deploy/ops/restart.sh api` (it uses `up -d`, which re-reads `.env` —
plain `docker compose restart` would NOT). Verify everything in one shot:

```bash
deploy/ops/verify-sources.sh    # per-provider checklist: .env intent, container drift,
                                # adapter enabled/config-warning, fetch health, VERDICT
```

It prints one verdict per provider — `HEALTHY` (with lastSuccess/imported counts),
`ENABLED — awaiting first cycle`, `FAILING` (with the error), `KEY MISSING`,
`RESTART NEEDED` (deploy/.env edited after the container started), or `DISABLED` — plus
ready-to-paste `.env` lines for anything missing. Exit 1 = actionable, 2 = stack down. Key
VALUES never appear in its output (presence + length only). The §6b probe remains useful for
raw health rows: substitute the provider's name in the `"newsapi" in` filter. Validator rules
`guardian-key` / `newsdata-key` / `gnews-key` / `mediastack-key` / `currents-key` enforce
flag⇒key at deploy time.

Provider-specific notes (the honest edges, documented rather than papered over):

* **Guardian** is single-outlet: every article resolves to canonical publisher “The Guardian”
  (registry-verified Lean Left). It thickens that one outlet's coverage — it widens story
  distributions, not the outlet spectrum. Rotation axis is `RWE_GUARDIAN_SECTION`
  (e.g. `world,politics`).
* **NewsData + GNews** free tiers cap articles per request at 10 — the compose `PAGE_SIZE`
  defaults match; raising them only helps on paid tiers.
* **MediaStack's quota is MONTHLY** (~500). The defaults (90-min interval, budget 15/day ≈
  465/month) are sized to survive the month — do not drop the interval below ~5400 s on the
  free tier. HTTPS is paid-only there: `RWE_MEDIASTACK_HTTPS=0` downgrades the fetch to http
  (free tier), a documented trade-off — article metadata then transits unencrypted.
* **Currents** payloads carry no outlet field; the publisher hint is each article URL's own
  domain (www-stripped), which the registry resolves exactly like any domain alias.
* **Google News RSS** is keyless; feeds are built from `RWE_GOOGLENEWS_TOPICS`
  (WORLD/NATION/BUSINESS/TECHNOLOGY/ENTERTAINMENT/SPORTS/SCIENCE/HEALTH) and/or free-text
  `RWE_GOOGLENEWS_QUERIES`, rotated one feed per cycle. The item's `<source>` tag names the
  real outlet — that is the publisher hint. **Known limitation:** item links are Google
  redirect URLs (the encoding is undocumented, so we do not guess-decode), which means
  canonical-URL dedup cannot merge a Google-delivered copy with the same article from the
  publisher's own feed — story clustering still groups them by title. Prefer direct RSS/API
  sources for outlets you already ingest; Google News is best used for breadth (topics and
  queries you have no direct source for).

---

## 7 · Operational runbook

**Deploy a new version**
```bash
# Prefer the wrapper (checkout + rebuild + readiness gate + smoke test):
deploy/ops/update.sh <new-tag>
# Manual equivalent:
cd /opt/ih && git fetch --tags && git checkout <new-tag>
$COMPOSE up -d --build           # rebuilds changed images; ingest→api→web→caddy
# gate on readiness (see §5 step 2), then: deploy/ops/preflight.sh && deploy/ops/smoke-test.sh
```

**Restart services**
```bash
$COMPOSE restart web             # or: api / caddy
$COMPOSE ps                      # health + status
```

**Rotate logs** — configure Docker's json-file driver once (`/etc/docker/daemon.json`), then restart Docker:
```json
{ "log-driver": "json-file", "log-opts": { "max-size": "20m", "max-file": "5" } }
```
```bash
sudo systemctl restart docker && $COMPOSE up -d   # containers pick up the capped, rotated logs
```
(Or ship logs to **CloudWatch Logs** via the `awslogs` driver / CW agent and drop local retention.)

**Restore a backup** (recover from data loss/corruption)
```bash
$COMPOSE stop web                                   # halt new writes
aws s3 cp s3://acme-ih-backups/backups/<newest>.db /opt/ih/data/restore.db
deploy/ops/verify-restore.sh /opt/ih/data/restore.db          # prove it's intact FIRST
docker compose … run --rm backup \
  python examples/db_backup.py restore /app/data/restore.db   # snapshots current, refuses if corrupt
$COMPOSE up -d && deploy/ops/healthcheck.sh          # back to ready
```

**Update the beta allowlist**
- **File mode (no restart):** edit `/opt/ih/data/allowlist.txt` (the `BETA_ALLOWLIST_FILE`) — re-read on
  the next sign-in.
- **Env mode:** edit `BETA_ALLOWLIST` in `deploy/.env`, then `$COMPOSE up -d web`.
- To **evict** someone already signed in immediately, rotate `NEXTAUTH_SECRET` (below) — this ends all
  sessions; the removed email is then denied on re-sign-in.

**Rotate secrets**
```bash
# NEXTAUTH_SECRET (invalidates ALL web sessions → everyone re-signs-in):
openssl rand -base64 32   # set in deploy/.env, then: $COMPOSE up -d web
# RWE_INTERNAL_SECRET (must change on BOTH tiers together, else the web tier is rejected):
openssl rand -base64 32   # set once in deploy/.env (shared), then: $COMPOSE up -d api web
```

**Other:** `df -h` (disk), `free -m` (memory/swap), `docker system prune -f` (reclaim image space),
`docker compose … logs -f api` (tail structured logs; correlate by `requestId`).

---

## 8 · Cost estimate (us-east-1, on-demand, monthly)

**Assumptions:** single instance; 30 GiB gp3; one Elastic IP (in-use IPv4 billed since 2024 at
~$0.005/hr ≈ $3.65/mo); a few GB in S3; Route 53 hosted zone; basic CloudWatch + the agent; beta
egress well under the 100 GB/mo free tier. Prices rounded; a 1-year Savings Plan trims the instance
~30–40% but isn't worth committing for a short beta.

| Line item | 5 users | 30 users | 150 users |
|---|---:|---:|---:|
| EC2 instance | t3.small ~$15 (or t4g.small ~$12) | t3.small ~$15 → t3.medium ~$30 | **t3.medium ~$30** (t4g.medium ~$24) |
| EBS 30 GiB gp3 | ~$2.40 | ~$2.40 | ~$2.40 |
| Public IPv4 (EIP in use) | ~$3.65 | ~$3.65 | ~$3.65 |
| Route 53 hosted zone | ~$0.50 | ~$0.50 | ~$0.50 |
| S3 backups (storage+req) | <$1 | ~$1 | ~$1–2 |
| CloudWatch (agent+alarms) | ~$1–3 | ~$2–3 | ~$3 |
| Data transfer out | ~$0 (free tier) | ~$0 | ~$0–2 |
| **Estimated total** | **~$23–26/mo** | **~$25–42/mo** | **~$40–45/mo** |

The whole 5–150 range fits on one t3.medium; the low end runs on a t3.small/t4g.small. Graviton (t4g)
is the cheapest viable option throughout. The dominant lever is the instance size — everything else is
a few dollars.

---

## 9 · Go-live checklist (before inviting the first 5)

The full, checkbox version is **`docs/WAVE0_GO_LIVE_CHECKLIST.md`**. In brief, do not send invites until:

- [ ] Route 53 A record resolves to the EIP; HTTPS valid (`curl -I https://hidden-view.com`).
- [ ] Google OAuth redirect URI = `https://hidden-view.com/api/auth/callback/google`; `NEXTAUTH_URL` matches.
- [ ] `deploy/.env` complete (RWE_ENV=production, matching `RWE_INTERNAL_SECRET`, `NEXTAUTH_SECRET`,
      Google creds, `RWE_DB_URL` on the volume) and `chmod 600`.
- [ ] **BA1**: `BETA_ACCESS_ENABLED=1` and `BETA_ALLOWLIST` contains the **5 Wave-0 emails** (verified: an
      off-list email is denied).
- [ ] `deploy/ops/preflight.sh` exits **0**; live sign-in + report + recommendation smoke pass.
- [ ] Backups: `scheduler` running, one backup **verified** (`verify-restore.sh`), and present in S3.
- [ ] Monitoring: `healthcheck.sh` on cron with `ALERT_WEBHOOK`/SNS; CloudWatch alarms on
      StatusCheckFailed + disk; a test alert reached a human.
- [ ] PA1: `/api/analytics/funnel` reachable internally (200 with secret, 404 without); RM smoke event visible.
- [ ] Rollback assets staged: previous tag known + newest S3 backup path noted.
- [ ] SSH restricted / SSM only; SG exposes **only** 443/80(/22-from-your-IP).

---

*Documentation only. Reuses the repo's Docker Compose, `deploy/ops/*` scripts, OBS1/PA1 endpoints, and
BA1 allowlist; the Caddy config and compose override are operator files you create on the instance. No
application code was changed.*
