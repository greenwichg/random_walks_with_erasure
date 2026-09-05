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

### Engine feature flags (optional)
| Variable | Default | Notes |
|---|---|---|
| `RWE_PLATFORM_API` | `0` (off) | `1` mounts the commercial `/v1` surface into the engine (`docs/PLATFORM_API.md`); `deploy/Caddyfile` already routes `/v1/*` to the engine, inert while the flag is off. Enable with `deploy/ops/platform-enable.sh`, which also runs the identity backfill and validates the result. Keys: `examples/platform_keys.py`. `RWE_PLATFORM_PUBLISH_RATINGS` / `RWE_PLATFORM_PUBLISH_WIKIPEDIA` stay `0` unless the corresponding licence is held. |
| `RWE_PLATFORM_COVERAGE_PER_PUBLISHER` | `3` | Coverage rows one outlet may occupy in a `/v1` story (newest first); `coverageOmitted` counts the rest; `0` = all. A caller may widen it per request with `?coverage_per_publisher=`. |
| `RWE_SEARCH_TERMS` | `0` (off) | `1` switches the consumer Search page to the term search `/v1` uses (FTS5 index `feed_articles_fts`, created and maintained automatically; every word required, any order, stemmed, bm25-ranked). Off keeps the substring match byte-identical. Index health: `examples/search_index.py status`. |
| `RWE_COACH_V2` | `0` (off) | `1` routes a **measured** reader's AI Coach through the v2 intent pipeline (trigger-ladder greeting, weekly recap, intent-routed replies). Off is byte-identical to v1 (pinned by `test_coach_v1_contract.py`). Engine-side only — wired onto the `api` service in `docker-compose.aws.yml`. Only engages once an account has **≥ 5 reads** (`ESTIMATE_MIN_READS`); below that, and with no seeded demo account, the coach serves v1. **Production runs with `RWE_COACH_V2=1`.** |

| `RWE_BREAKING_NOTIFICATIONS` | `0` (off) | `1` lets the engine's poller record a notification event the first time a story becomes *Breaking* (`examples/story_events.py`), which the in-app inbox then delivers to readers who kept `notifications.categories.breaking.inApp` on — capped at 5/day each. Engine-side only; wired onto the `api` service in **both** compose files (the `ingest` service never reaches the detection seam). Read at call time: `deploy/ops/restart.sh api` applies a change with no rebuild, in either direction. **Production runs with `RWE_BREAKING_NOTIFICATIONS=0`.** See `docs/NOTIFICATION_PLATFORM.md`. |
| `RWE_BREAKING_MIN_PUBLISHERS` | `3` | Distinct outlets a story needs before it may interrupt a reader. A quality bar, not a performance one — a single-source "breaking story" is a rumour. |
| `RWE_BREAKING_TTL_HOURS` | `6` | How long a breaking event stays deliverable. Also what makes the daily cap a *cap* rather than a *queue*: a story held back by the cap expires instead of arriving tomorrow as news. |
| `RWE_PUSH_ENABLED` | `0` (off) | `1` lets browsers register push subscriptions and makes the per-device control appear in Settings. **Phase B1 registers only — nothing sends a push yet.** Requires the VAPID trio below; with the switch on and no key the feature reports itself unavailable (fail-closed, deliberately). **Production runs with `RWE_PUSH_ENABLED=0`.** Setup, verification and rollback: `docs/BROWSER_PUSH_OPERATIONS.md`. |
| `RWE_VAPID_PUBLIC_KEY` | *(empty)* | The public half of the VAPID pair, base64url (87 chars, starts `B`). Served to browsers, which subscribe against it. Public by construction — it is handed to `pushManager.subscribe`. |
| `RWE_VAPID_PRIVATE_KEY` | *(empty)* | **Secret.** The signing half (43 chars). Read by nothing in B1; wired now so the pair is generated and stored once. Must be interpolated from `deploy/.env` — a literal in a compose file fails `validate-deployment.py`. |
| `RWE_VAPID_SUBJECT` | *(empty)* | The `mailto:` or `https:` contact push services are given with each send. B2. |
| `RWE_EMAIL_ENABLED` | `0` (off) | `1` turns on the weekly-digest **email** channel — a second transport for the digest the inbox already delivers, not a new notification. Off is a true no-op: the worker returns immediately and the in-app card is untouched. Every reader is opted **out** by default (`notifications.categories.digests.email` = `false`), because a channel nobody opted into is not consent. **Production runs with `RWE_EMAIL_ENABLED=0`.** Setup, verification and rollback: `docs/WEEKLY_DIGEST_EMAIL.md`. |
| `RWE_SMTP_HOST` / `RWE_SMTP_PORT` | *(empty)* / `587` | Any SMTP relay — SES (`email-smtp.<region>.amazonaws.com`), Postmark, SendGrid, a local relay. The engine speaks SMTP rather than one vendor's API so the provider is configuration, not code. 587 negotiates STARTTLS; 465 is implicit TLS. A relay offering **no** TLS is refused rather than used. |
| `RWE_SMTP_USER` / `RWE_SMTP_PASSWORD` | *(empty)* | **Secret.** Relay credentials. For SES these are *SMTP credentials*, which are not your AWS access keys. |
| `RWE_EMAIL_FROM` | *(empty)* | The From address, e.g. `Hidden View <digest@hidden-view.com>`. Its **domain must be verified with the provider and carry SPF + DKIM**, or mail is filed as spam or rejected — DNS work, not deployment work. A rejected sender is retried, never counted against the recipient. |
| `RWE_EMAIL_SECRET` | *(empty)* | **Secret.** Signs unsubscribe tokens (HMAC over purpose + user id). **Without it nothing is sent at all** — mail a reader cannot escape is worse than mail they never got. |
| `RWE_EMAIL_ALLOWLIST` | *(empty)* | Who may receive **at all**, whatever their settings say — an operator gate, not a reader preference, and the one variable here that fails closed by being ABSENT: **unset means nobody**. A comma-separated list of addresses and/or `@domains` during beta; `*` — and only `*` — opens general delivery, so a lost line in `.env` cannot start mailing a live user base. Being outside the list never writes a delivery off: widen it and the waiting digests go out on the next pass. |
| `RWE_PUBLIC_URL` | *(empty)* | Base for the report and unsubscribe links inside the mail, e.g. `https://hidden-view.com`. Unset, every reader is skipped with `no-unsubscribe-url`. |
| `RWE_EMAIL_MAX_PER_RUN` | `500` | Cap per delivery pass. Runs are hourly, so this bounds a burst rather than the week. |
| `RWE_EMAIL_RETRY_MAX` | `3` | Attempts before a delivery is abandoned. Backoff 15 min → 1 h → 6 h. |

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

### Web feature flags (optional — safe to leave unset)

Both are read **at call time**, so a change takes effect on `deploy/ops/restart.sh web` with no rebuild.
Both are wired onto the `web` service in `docker-compose.yml` *and* `docker-compose.aws.yml`; a variable
that is not on the service never reaches the container, whatever `deploy/.env` says — compose reads that
file to substitute `${VAR}`, not to inject it. `deploy/ops/validate-deployment.py` enforces their
presence (`web-identity-recovery-switch`).

| Variable | Default | Notes |
|---|---|---|
| `RWE_IDENTITY_RECOVERY` | `1` (on) | The kill switch for identity recovery — a session that signed in while the engine was unreachable carries no engine user id, and the `jwt` callback repairs it from the token's own claims (`docs/SESSION_IDENTITY_RECOVERY_DESIGN.md`). `0` / `false` / `no` / `off` disables it; **any other value, including empty, leaves it on**. Disabling restores pre-recovery behaviour exactly: a session that already has an id keeps using it, so nobody is signed out or de-attributed. This is the rollback lever — prefer it to a revert. |
| `RWE_BACKEND_TIMEOUT_MS` | `6000` | Deadline for every engine call from the web tier (`lib/engine-timeout.ts`), including the recovery upsert. Left **empty** in both compose files on purpose, so the default has one home, in code. A non-positive or unparseable value falls back to `6000` rather than becoming a zero-millisecond deadline — which would abort every engine call instantly and present exactly like a total outage. |

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

### Monitoring & alerting
| Variable | Value | Used by |
|---|---|---|
| `IH_BASE_URL` | `http://127.0.0.1:8000` | **Non-Docker path only.** On the EC2 stack the engine port is unpublished, so `preflight.sh`'s live probes can't reach it — **leave `IH_BASE_URL` unset** and let `smoke-test.sh` (in-container) do the live checks. |
| `ALERT_WEBHOOK` | Slack / Discord / other webhook | Destination for `deploy/ops/monitor.sh` (5-min health cron) + `backup-offhost.sh` failures. **Unset = log-only** (see Alerting below). |

#### Alerting

`deploy/ops/monitor.sh` (every 5 min) and `backup-offhost.sh` (hourly) call one `alert()` helper
(`deploy/ops/_compose.sh`). Its contract:

- **`ALERT_WEBHOOK` unset →** the problem is written to the cron log **only** (`/var/log/ih-monitor.log`,
  `/var/log/ih-backup.log`). The stack never depends on an alert channel — a missing webhook is safe.
- **`ALERT_WEBHOOK` set →** one concise JSON POST per problem, domain-prefixed and JSON-escaped, carrying
  **both** `text` (Slack, Mattermost, Google Chat) and `content` (Discord) — so a **single** URL works with
  either service, no format flag. A failed POST is itself logged and never breaks the cron.

Configure by pasting one webhook URL into `deploy/.env`:

| Service | How to get the URL | Paste into `deploy/.env` |
|---|---|---|
| **Slack** | Workspace → *Incoming Webhooks* app → *Add to Slack* → pick a channel → copy the `https://hooks.slack.com/services/…` URL | `ALERT_WEBHOOK=https://hooks.slack.com/services/T…/B…/…` |
| **Discord** | Server → *Edit Channel* → *Integrations* → *Webhooks* → *New Webhook* → *Copy Webhook URL* | `ALERT_WEBHOOK=https://discord.com/api/webhooks/…/…` |
| **Other** | Any endpoint accepting `{"text":…}` or `{"content":…}` (e.g. an SNS/Lambda or PagerDuty proxy) | `ALERT_WEBHOOK=https://…` |

No redeploy is needed — the crons read `deploy/.env` each run. **Live-test** it end-to-end:

```bash
cd /opt/ih && source deploy/ops/_compose.sh && alert "test alert from hidden-view.com $(date -u +%FT%TZ)"
# → the message appears in your Slack/Discord channel; `alert:` on stderr means the POST failed (check URL/egress).
```

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
deploy/ops/validate-deployment.py   # WIRING gate: every enabled capability has its env/mounts/secrets/files (CI-enforced)
set -a; . deploy/.env; set +a
deploy/ops/preflight.sh     # CONFIG gate: env + secrets + HTTPS + OAuth + DB (no IH_BASE_URL — see below)
deploy/ops/smoke-test.sh    # LIVE gate: the RUNNING stack end-to-end, probed inside the container
```

> **Wiring gate (drift guard).** `validate-deployment.py` fails if a service turns a capability **on**
> without the config it depends on — e.g. `RWE_FEED_POLL=1` without `RWE_RSS_FEEDS`/the feed mount (the
> 2026-07-21 ingestion incident). Rules are data (`deploy/deployment-rules.json`) and it runs in CI on
> every PR. Details: `docs/DEPLOYMENT_RUNBOOK.md` → *Deployment-dependency validation*.
Both must exit 0 before go-live. **Do not set `IH_BASE_URL`** for `preflight.sh` on the EC2 stack — the
engine port is unpublished, so a host-side `127.0.0.1:8000` probe would false-FAIL; `smoke-test.sh` does
the live engine/OBS1/PA1 checks correctly via `docker compose exec`. See `docs/WAVE0_GO_LIVE_CHECKLIST.md`.
