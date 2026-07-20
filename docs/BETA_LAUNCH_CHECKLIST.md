# Beta Launch Checklist — Information Health (100–150 user closed beta)

Operational runbook for taking the product live. Pairs with **`DEPLOYMENT.md`** (how to run/ship) and
the BR1 ops tooling in **`deploy/ops/`**. Everything here is configuration + procedure — no application
change. Work top-to-bottom; do not send invites until every **[blocker]** box is checked.

> **The BR1 ops toolbox** (`deploy/ops/`, all read-only wrappers over existing tools):
> - `preflight.sh` — deterministic PASS/WARN/FAIL of env, secrets, HTTPS, OAuth, DB, backups, monitoring, endpoints.
> - `backup.sh` — one backup + retention + optional off-host (reuses `examples/db_backup.py`).
> - `verify-restore.sh` — proves the newest backup is intact & restorable **without touching prod**.
> - `healthcheck.sh` — probes the OBS1 `/api/health/{live,ready}` endpoints; alerts on failure.

---

## 0 · Pre-launch (T-minus)

### 0.1 Secrets & environment — **[blocker]**
Generate strong secrets and set them **identically** where required:
```bash
openssl rand -base64 32   # RWE_INTERNAL_SECRET   (same value on engine AND web)
openssl rand -base64 32   # NEXTAUTH_SECRET       (web)
```
Required in production (both tiers refuse to boot without them — `DEPLOYMENT.md` §Startup validation):

| Var | Where | Value |
|---|---|---|
| `RWE_ENV=production` | engine + web | turns on fail-closed auth + disables dev demo-login |
| `RWE_INTERNAL_SECRET` | engine + web | identical shared secret |
| `NEXTAUTH_SECRET` | web | session-JWT signing key |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | web | Google OAuth (only sign-in method in prod) |
| `NEXTAUTH_URL` | web | **https** canonical URL (OAuth callback + Secure cookies) |
| `RWE_BACKEND_URL` | web | engine origin (private network) |
| `RWE_DB_URL` | engine | persistent volume path, e.g. `sqlite:////app/data/ih_beta.db` (never `:memory:` or `/tmp`) |

### 0.2 HTTPS & network — **[blocker]**
- [ ] Web app served over **HTTPS** (`NEXTAUTH_URL` https so cookies are `Secure`/`__Secure-`).
- [ ] Engine port is **not publicly exposed** (web tier reaches it server-to-server on a private network).
- [ ] A hard body limit (`client_max_body_size`) on the fronting proxy/LB (defense-in-depth vs. chunked uploads — `DEPLOYMENT.md`).

### 0.3 OAuth — **[blocker]**
- [ ] Google OAuth client created; **Authorized redirect URI** = `https://<app>/api/auth/callback/google`.
- [ ] `GOOGLE_CLIENT_ID`/`SECRET` set on the web tier; a manual sign-in round-trips end-to-end.

### 0.4 Backups scheduled + verified — **[blocker B-1]**
- [ ] A recurring backup is scheduled (pick one):
  - **Compose:** `docker compose -f deploy/docker-compose.yml --profile scheduler up -d backup-scheduler` (hourly, keeps 48).
  - **systemd/cron (host with the repo):** run `deploy/ops/backup.sh` hourly — see §Scheduling recipes.
- [ ] **Off-host** copies configured (a lost volume must not take the only copy): set `BACKUP_OFFHOST_CMD` (e.g. `aws s3 cp "$1" s3://…`) **or** bind-mount the backups dir to the host and sync it.
- [ ] First backup taken and **verified restorable**:
  ```bash
  deploy/ops/backup.sh            # or: docker compose … run --rm backup
  deploy/ops/verify-restore.sh    # exit 0 = intact & restorable (non-destructive)
  ```

### 0.5 Monitoring wired — **[blocker B-2]**
- [ ] An external uptime monitor **or** `deploy/ops/healthcheck.sh` (cron/systemd) points at the engine's OBS1 endpoints.
- [ ] Failure alerts reach a human: set `ALERT_WEBHOOK` (Slack-compatible) for `healthcheck.sh`, or configure the monitor's alerting.
- [ ] Alert thresholds decided (see §Monitoring checklist).

### 0.6 Deterministic preflight — **[blocker]**
Load the production env and run the preflight; it must exit **0** (no FAILs):
```bash
set -a; . ./prod.env; set +a
IH_BASE_URL=https://engine.internal deploy/ops/preflight.sh
```
Checks env, secrets, HTTPS, OAuth, persistent DB, a recent backup, monitoring config, and (if
`IH_BASE_URL` set) live health + that the analytics dashboard is internal-only.

### 0.7 Green CI — **[blocker]**
- [ ] `ci-success` is green on the release commit: pytest (3.11/3.12), web typecheck + **full unit suite** + i18n + build, extension tests, **Playwright e2e**, Docker builds + **compose validation**.

---

## 1 · Deployment order

Bring the stack up in dependency order (Compose encodes this; do it manually for a split deploy):

1. **RSS ingest (one-shot)** → seeds the `FeedArticle` catalog so recommendations carry real URLs. Exits 0 even if a feed is down (falls back to the static corpus).
2. **Engine (api)** → wait for **readiness 200**: `curl -fsS $ENGINE/api/health/ready` → `{"status":"ready",…}`. It builds the corpus once at startup.
3. **Web** → `depends_on: api healthy`; serves the production build (mock fallback OFF).
4. **backup-scheduler** (profile `scheduler`) → start it once the DB volume is mounted.
5. **healthcheck** cron/monitor → enable after the engine is ready.

```bash
# single host, all-in-one:
docker compose -f deploy/docker-compose.yml up -d --build            # ingest → api → web
docker compose -f deploy/docker-compose.yml --profile scheduler up -d backup-scheduler
# then verify:
curl -fsS http://localhost:8000/api/health/ready && echo OK
```

---

## 2 · Rollback plan

Decide the failure class, then act:

| Failure | Rollback |
|---|---|
| **Bad app release** (crash loop, broken flow) | Redeploy the **previous image tag** (engine and/or web). Images are immutable and built in CI; keep the last-good tag pinned. The DB is untouched — no data step needed. |
| **Bad config** (wrong secret/URL) | The tier **refuses to boot** and logs the exact problem (startup validation). Fix the env and restart; no rollback of code. |
| **Data corruption / bad write** | **Restore from backup** (§4). Stop the web tier first so no new writes land, restore the newest good backup, restart. |
| **Engine won't become ready** | Check `/api/health/ready` body (`store`/`backend` flags) + logs; if corpus build fails, roll back the engine image and re-run ingest. Web shows honest error states meanwhile (no fabricated data). |

**Golden rule:** app rollback is an image swap; data rollback is a restore. Never edit the live SQLite file by hand.

---

## 3 · Monitoring checklist (reuses OBS1)

Watch these; alert on the **bold** ones.

| Signal | Source | Healthy | Alert |
|---|---|---|---|
| **Liveness** | `GET /api/health/live` | `200 {"status":"alive"}` | any non-200 / timeout → **page** |
| **Readiness** | `GET /api/health/ready` | `200 {"status":"ready"}` | `503`/other for >2 min → **page** |
| Web app | `GET https://<app>/` | 200 or auth redirect | down → **page** |
| **5xx rate** | request logs `{"event":"request","status":≥500}` / `unhandled_exception` | ~0 | sustained 5xx (>1–2%/5 min) → **alert** |
| **p95 latency** | `GET /api/metrics` → `request_ms|…`, `report_generate_ms`, `db_query_ms` | p95 < ~500 ms | p95 rising / DB p95 climbing → **alert** (write-contention signal) |
| Rate limiting | logs `{"event":"rate_limited",…}` | rare | frequent → a client is hot or limits too tight |
| Client errors | `POST /api/client-errors` → `{"event":"client_error"}` | low | spike → a broken frontend build |
| Feed health | `GET /api/internal/feeds` (internal) | feeds healthy, not stale | many unhealthy/stale → recommendations go static |
| **Disk** | host | headroom | DB + WAL + backups filling the volume → **alert** (SQLite writes fail when full) |

`/api/metrics` and `/api/internal/*` are **internal-only** — pass the internal secret header
(`X-IH-Auth: $RWE_INTERNAL_SECRET`); an un-headered call gets `404` (that's the correct posture).

---

## 4 · Backup verification & restore

### Verify (do daily, automatable)
```bash
deploy/ops/verify-restore.sh        # non-destructive: integrity (quick_check) + store opens + tables queryable
# also glance at storage + backup inventory:
python examples/db_backup.py status
```

### Restore procedure (recovery)
```bash
# 1. STOP the web tier (halt new writes)          docker compose stop web
# 2. Pull the newest GOOD backup from off-host     aws s3 cp s3://…/<newest>.db ./restore.db
# 3. Verify it BEFORE swapping                      deploy/ops/verify-restore.sh ./restore.db
# 4. Restore (snapshots the current DB first, integrity-checks the backup, then atomic swap):
python examples/db_backup.py restore ./restore.db      # refuses if the backup is corrupt
# 5. Restart the engine + web                       docker compose up -d
# 6. Confirm                                         curl -fsS $ENGINE/api/health/ready
```
`restore` writes the pre-restore DB to `…​.pre-restore` — keep it until you've confirmed the restore is good.

### Recovery checklist
- [ ] Off-host backups exist and are < 24 h old.
- [ ] `verify-restore.sh` passes on the newest backup.
- [ ] A **restore drill** has been rehearsed into a scratch path (do this before launch, not during an incident).
- [ ] `quick_check` on the live DB is `ok` (`db_backup.py status` / `GET /api/internal/storage`).

---

## 5 · First-day operational checklist

Within the first hours after the first invites:
- [ ] **Sign-in works** for a real invited user (OAuth round-trip, lands on onboarding/dashboard).
- [ ] **No 5xx spike** in the request logs; `unhandled_exception` count ~0.
- [ ] **Analytics is flowing** (first-cohort funnel is unrepeatable — confirm capture now):
  ```bash
  curl -fsS -H "X-IH-Auth: $RWE_INTERNAL_SECRET" $ENGINE/api/analytics/funnel   # app_opened / login_success reachers > 0
  curl -fsS -H "X-IH-Auth: $RWE_INTERNAL_SECRET" $ENGINE/api/analytics/events    # event counts climbing
  ```
- [ ] **Backups running** — a new file appears each interval; `verify-restore.sh` passes.
- [ ] **Health green** — `healthcheck.sh` exits 0; readiness 200.
- [ ] **Latency sane** — `/api/metrics` p95 within budget; `db_query_ms` p95 not climbing.
- [ ] **Disk headroom** on the data volume.

## 6 · First-week monitoring checklist

- [ ] **Activation funnel** trend (`/api/analytics/funnel`): where is the top drop-off? Is anyone reaching *Measured Report* / *Recommendation Accepted*?
- [ ] **Product metrics** (`/api/analytics/metrics`): activation rate, time-to-first-report, D1 retention (D7 fills in as the week completes).
- [ ] **Error budget**: 5xx rate stays ~0; investigate any `unhandled_exception` / `client_error` clusters by `requestId`.
- [ ] **Latency + DB**: `/api/metrics` p95 and `db_query_ms` p95 stable as data grows (the write-on-read report path + analytics writes are the ones to watch — see §Known risks).
- [ ] **DB growth**: `db_backup.py status` size trend; confirm retention isn't unbounded (analytics_events grows per page view).
- [ ] **Backup/restore drill**: once during the week, restore a backup into a scratch path and diff.
- [ ] **Feed health** (`/api/internal/feeds`): feeds staying healthy/fresh so recommendations carry real URLs.
- [ ] **A ~150-VU load smoke** (if not done pre-launch): confirm SQLite write-contention holds; watch `db_query_ms` p95.

---

## Scheduling recipes

**systemd timer (host with the repo + Python):**
```ini
# /etc/systemd/system/ih-backup.service
[Service]
Type=oneshot
WorkingDirectory=/opt/information-health
Environment=RWE_DB_URL=sqlite:////opt/information-health/data/ih_beta.db
Environment=BACKUP_KEEP=48
# Environment=BACKUP_OFFHOST_CMD=aws s3 cp "$1" s3://acme-ih-backups/
ExecStart=/opt/information-health/deploy/ops/backup.sh
```
```ini
# /etc/systemd/system/ih-backup.timer   →  systemctl enable --now ih-backup.timer
[Timer]
OnCalendar=hourly
Persistent=true
[Install]
WantedBy=timers.target
```

**cron (backup hourly + health every 5 min):**
```cron
0 * * * *   cd /opt/information-health && RWE_DB_URL=sqlite:////opt/information-health/data/ih_beta.db BACKUP_KEEP=48 deploy/ops/backup.sh   >> /var/log/ih-backup.log 2>&1
*/5 * * * * cd /opt/information-health && IH_BASE_URL=http://127.0.0.1:8000 ALERT_WEBHOOK=$IH_ALERT_WEBHOOK deploy/ops/healthcheck.sh      >> /var/log/ih-health.log 2>&1
```

**Docker-only host:** use the `scheduler` compose profile for backups (§0.4) and a host cron running
`healthcheck.sh` against the published engine URL (or point an external monitor at `/api/health/ready`).

---

## Known risks carried into the beta (accept + monitor)

- **SQLite single-writer + write-on-read**: `GET /api/report` writes the lifecycle ledger (RC2.3) and
  PA1 writes an analytics row per page view. Low-probability contention at closed-beta concurrency,
  now **monitorable** via `db_query_ms` p95. *Mitigation is out of BR1 scope (touches business logic);*
  run a 150-VU smoke and watch DB latency.
- **Single host / single DB file**: no HA. Recovery = restore from an **off-host** backup (§4) — hence
  the blocker on off-host + a rehearsed drill.
- **Client-emitted analytics can be lost** (adblock/hard unload). Cross-check volumes against the
  authoritative tables if a number looks off (`reads`, `report_snapshots`, `rec_events`).
- **30-day non-revocable sessions**; **`account_created` is client-approximated** — documented in PA1.

---

*BR1 operational readiness — configuration + procedure + tooling over the existing `db_backup.py` and
OBS1 endpoints. No product feature, and no change to the recommendation engine, ranking, lifecycle,
evaluation, report calculations, analytics, observability, mobile, authentication, UX, or business logic.*
