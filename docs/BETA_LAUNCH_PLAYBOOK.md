# Beta Launch Playbook — Information Health (100–150 user closed beta)

**Release Manager's operational playbook.** Read-only: this document *guides* the deployment; it changes
no code. It sequences the launch across six phases, each with operator actions, exact commands, expected
results, rollback criteria, monitoring checkpoints, and decision gates. It drives the BR1 tooling
(`deploy/ops/`) and the OBS1 / PA1 endpoints; pairs with `DEPLOYMENT.md`, `docs/BETA_LAUNCH_CHECKLIST.md`,
and `docs/BR1_LAUNCH_AUDIT.md`.

## Roles & conventions

- **Release Manager (RM)** — owns the go/no-go, sequences the phases, calls rollback.
- **Operator (OPS)** — runs the commands on the deploy host.
- Placeholders: `$ENGINE` = private engine origin (e.g. `http://api:8000`), `$WEB` = public HTTPS app
  URL, `$SECRET` = `RWE_INTERNAL_SECRET`. Internal endpoints require header `X-IH-Auth: $SECRET`.
- **All commands are non-destructive** except the explicit `restore` in the rollback path (which itself
  snapshots the current DB first and refuses a corrupt backup).

---

## Phase 1 · T-7 days — Foundations & dress rehearsal

**Goal:** prove the whole stack deploys, backs up, restores, and is monitorable in a staging environment
identical to prod. Nothing user-facing yet.

**Operator actions**
1. Stand up a **staging** host mirroring prod (same image tags, `RWE_ENV=production`, a *separate* DB).
2. Provision secrets (`openssl rand -base64 32` for `RWE_INTERNAL_SECRET` + `NEXTAUTH_SECRET`), a Google
   OAuth **test** client, HTTPS, and a persistent volume.
3. Confirm CI is green on the release commit and the images are built/tagged.
4. Run a full deploy → backup → **restore drill** → monitoring loop on staging.

**Commands**
```bash
# CI gate (release commit must be green)
#   GitHub → Actions → "CI success" ✓  (python, web, extension, e2e, docker)

# Deploy staging
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml --profile scheduler up -d backup-scheduler

# Preflight (loads the staging prod env)
set -a; . ./staging.env; set +a
IH_BASE_URL=$ENGINE deploy/ops/preflight.sh

# Backup + RESTORE DRILL (the rehearsal that matters most)
deploy/ops/backup.sh
deploy/ops/verify-restore.sh                 # non-destructive integrity + restore proof
#   then a real restore into staging, timed end-to-end:
docker compose stop web
python examples/db_backup.py restore "$(ls -1t <backupdir>/*.db | head -1)"
docker compose up -d && curl -fsS $ENGINE/api/health/ready
```

**Expected results**
- CI `ci-success` ✓. `preflight.sh` exits **0** (WARNs allowed). `verify-restore.sh` exits **0**.
- A restore completes and the engine returns `{"status":"ready"}`; **record the restore wall-clock time**
  (your recovery RTO).

**Monitoring checkpoints** — `healthcheck.sh` green; `/api/metrics` returns timers; structured logs show
`{"event":"request",…}` + a `startup` line.

**Analytics validation** — drive the staging app (sign in, view report, open a rec), wait ~4 s, then:
```bash
curl -fsS -H "X-IH-Auth: $SECRET" $ENGINE/api/analytics/funnel   # app_opened/health_report_viewed reachers > 0
```

**Backup verification** — `verify-restore.sh` OK; a backup file appears each scheduler interval.

**Health verification** — `/api/health/live` alive, `/api/health/ready` 200.

**Rollback criteria (this phase)** — staging is throwaway; if deploy/restore fails, **fix and re-drill**.
No production impact. Do not proceed to T-3 until the restore drill succeeds.

**🚦 Decision gate G1 (T-7):** *Dress rehearsal passed?* — deploy, backup, **restore**, monitoring, and
analytics all verified on staging. **NO-GO → stay at T-7** until green.

---

## Phase 2 · T-3 days — Production provisioning & security

**Goal:** production infrastructure fully provisioned and hardened; the only thing missing is the invite.

**Operator actions**
1. Provision the **production** host/volume, DNS, and TLS cert for `$WEB`.
2. Set production secrets + the **real** Google OAuth client (redirect URI
   `https://<app>/api/auth/callback/google`). `RWE_INTERNAL_SECRET` identical on both tiers.
3. Configure **off-host** backups (`BACKUP_OFFHOST_CMD` or a bind-mount + host sync) and **alerting**
   (`ALERT_WEBHOOK` and/or an external uptime monitor on `/api/health/ready`).
4. Lock the network: engine port **not** publicly reachable; LB body-size cap set.

**Commands**
```bash
set -a; . ./prod.env; set +a
IH_BASE_URL=$ENGINE deploy/ops/preflight.sh     # MUST exit 0 (no FAILs)
# security spot-checks
curl -s -o /dev/null -w '%{http_code}\n' $ENGINE/api/metrics            # 404 without the secret
curl -s -o /dev/null -w '%{http_code}\n' -H "X-IH-Auth: $SECRET" $ENGINE/api/metrics   # 200 with it
curl -sI $WEB | grep -iE 'content-security-policy|strict-transport|x-frame-options'    # headers present
```

**Expected results** — `preflight.sh` **0 FAIL**; `/api/metrics` = 404 unauthenticated / 200 with secret;
CSP + HSTS + `X-Frame-Options: DENY` present on `$WEB`; engine unreachable from the public internet.

**Monitoring checkpoints** — alert path tested: trigger a deliberate failure (stop the engine briefly)
and confirm the alert **fires** to a human. Uptime monitor shows the endpoints.

**Analytics validation** — `curl -H "X-IH-Auth:$SECRET" $ENGINE/api/analytics/events` reachable (counts may
be 0 pre-users); an **unauthenticated** call returns **404** (gating correct).

**Backup verification** — a scheduled backup lands **and** appears **off-host**; `verify-restore.sh` OK.

**Health verification** — `preflight.sh` live+ready probes pass against prod.

**Rollback criteria** — no users yet; any FAIL here is a **fix-forward**, not a rollback.

**🚦 Decision gate G2 (T-3):** *Production hardened?* — preflight 0-FAIL, secrets/HTTPS/OAuth set,
off-host backups verified, alerting proven to fire. **NO-GO → remediate**; do not schedule invites.

---

## Phase 3 · T-1 day — Freeze, final verification & readiness sign-off

**Goal:** code freeze; final end-to-end verification; the GO/NO-GO packet assembled.

**Operator actions**
1. **Freeze** the release commit/tag (no further merges to the deploy branch).
2. Full production smoke as a real user (see the **Smoke-test checklist**).
3. Confirm backups are running on schedule and one is < 1 h old and off-host.
4. Pre-stage the **rollback** assets: previous good image tag pinned; newest off-host backup path noted.

**Commands**
```bash
set -a; . ./prod.env; set +a
IH_BASE_URL=$ENGINE deploy/ops/preflight.sh        # 0 FAIL
deploy/ops/verify-restore.sh                        # newest backup intact & restorable
deploy/ops/healthcheck.sh                           # exit 0
curl -fsS $ENGINE/api/health/ready                  # {"status":"ready"}
curl -fsS -H "X-IH-Auth:$SECRET" $ENGINE/api/internal/feeds | head    # feeds healthy (recs carry URLs)
docker image ls | grep ih-                          # confirm current + previous tags exist
```

**Expected results** — every check green; a manual OAuth sign-in reaches the dashboard; a report renders;
a recommendation opens the real publisher URL; the funnel shows the RM's own test events.

**Monitoring checkpoints** — baseline captured: current `/api/metrics` p95 for `request_ms`,
`report_generate_ms`, `db_query_ms`; 5xx count ~0; disk headroom recorded.

**Analytics validation** — RM's smoke session appears in `/api/analytics/funnel` (app_opened → …). Reset
expectations: the **first real cohort's funnel is unrepeatable** — capture from invite #1.

**Backup verification** — newest backup < 1 h old, off-host copy confirmed, `verify-restore.sh` OK.

**Health verification** — live + ready green; feeds healthy/fresh.

**Rollback criteria** — if the freeze smoke fails, **do not launch**; fix, re-freeze, re-verify.

**🚦 Decision gate G3 (T-1, formal GO/NO-GO):** RM runs the **GO/NO-GO Decision Matrix** (end of doc).
All **[blocker]** rows GREEN ⇒ **GO**. Any blocker RED ⇒ **NO-GO**, slip the date.

---

## Phase 4 · Launch Day — Controlled rollout

**Goal:** deploy the frozen release and invite users in **controlled waves**, watching the funnel live.

**Operator actions (in order)**
1. Final pre-flight (env unchanged since T-1).
2. Deploy in dependency order and confirm readiness **before** any invite.
3. Invite **Wave 1 (~10–15 users)**; hold 30–60 min watching health + funnel.
4. If green, invite **Wave 2 (~50)**; hold; then **Wave 3 (remainder)**.

**Commands**
```bash
set -a; . ./prod.env; set +a
IH_BASE_URL=$ENGINE deploy/ops/preflight.sh                  # 0 FAIL

# Deploy (ingest → api → web) and GATE on readiness
docker compose -f deploy/docker-compose.yml up -d --build
until curl -fsS $ENGINE/api/health/ready >/dev/null; do echo "waiting for ready…"; sleep 5; done
docker compose -f deploy/docker-compose.yml --profile scheduler up -d backup-scheduler

# Post-deploy smoke (run the Smoke-test checklist), then INVITE WAVE 1.
# Watch loop (funnel + health), refresh every ~2 min during each hold:
watch -n 120 'curl -fsS -H "X-IH-Auth:'$SECRET'" '$ENGINE'/api/analytics/funnel;
              curl -s -o /dev/null -w "ready:%{http_code}\n" '$ENGINE'/api/health/ready'
```

**Expected results** — readiness 200 before invites; Wave-1 users sign in, complete onboarding, see a
report; funnel `app_opened → login_success → health_report_viewed` reachers climb; 5xx ~0.

**Monitoring checkpoints (each wave hold)**
- `/api/health/ready` = 200 continuously.
- `/api/metrics`: `request_ms`/`report_generate_ms`/`db_query_ms` p95 within ~2× the T-1 baseline.
- Logs: no `unhandled_exception`; `client_error` rare; `rate_limited` rare.
- Disk headroom stable.

**Analytics validation** — after Wave 1: `login_success` reachers ≈ users who signed in;
`health_report_viewed` > 0; drop-offs plausible. **If the funnel is flat while users are active →
analytics pipeline problem** (investigate the `/api/events` proxy/sink; do **not** roll back the app for
this alone — analytics is best-effort).

**Backup verification** — a backup lands during launch; `verify-restore.sh` OK mid-launch.

**Health verification** — `healthcheck.sh` exit 0 throughout.

**Rollback criteria (Launch Day)** — **roll back the app image** (previous tag) if any of:
- readiness stuck non-200 > 5 min after deploy, or crash-loop;
- 5xx rate > ~5% sustained over 10 min, or a broad `unhandled_exception` cluster;
- OAuth sign-in broken for multiple users;
- data-integrity signal (`/api/internal/storage` quick_check ≠ ok) → **halt invites + data-restore path**.
```bash
# App rollback (data untouched):
docker compose -f deploy/docker-compose.yml up -d --build   # after pinning the PREVIOUS good image tag
until curl -fsS $ENGINE/api/health/ready >/dev/null; do sleep 5; done
```

**🚦 Decision gates (per wave):** *Wave N healthy?* — health green + funnel advancing + 5xx ~0 for the
hold window ⇒ **proceed to Wave N+1**. Otherwise **pause invites**, diagnose, or roll back.

---

## Phase 5 · First 24 hours — Stabilize & confirm learning

**Goal:** confirm the system is stable under the full cohort and that we are actually *learning* (funnel
captured).

**Operator actions** — staff an on-call window; check in at ~+1 h, +4 h, +12 h, +24 h.

**Commands (each check-in)**
```bash
deploy/ops/healthcheck.sh
curl -fsS -H "X-IH-Auth:$SECRET" $ENGINE/api/analytics/funnel
curl -fsS -H "X-IH-Auth:$SECRET" $ENGINE/api/analytics/metrics     # activation, time-to-first-report
curl -fsS -H "X-IH-Auth:$SECRET" $ENGINE/api/metrics               # p95 trend
deploy/ops/verify-restore.sh                                        # a backup is real & restorable
# scan for errors by correlation id:
#   logs | grep -E '"event":"(unhandled_exception|client_error)"'
```

**Expected results** — readiness 200 throughout; p95 stable (not climbing with data); activation rate and
time-to-first-report populating; ≥1 verified backup taken since launch.

**Monitoring checkpoints** — 5xx ~0; `db_query_ms` p95 flat (the write-on-read + analytics-write contention
signal); `rate_limited` not spiking; disk growth linear and bounded.

**Analytics validation** — funnel has all early stages populated; **top drop-off** identified (data, not
opinion); `recommendation_viewed`/`accepted` appearing for engaged users.

**Backup verification** — hourly backups present + off-host; one `verify-restore.sh` pass logged.

**Health verification** — `healthcheck.sh` exit 0 at every check-in.

**Rollback criteria (24 h)** — sustained readiness failure, a data-integrity failure (quick_check ≠ ok),
or a security incident → app rollback and/or **data restore** (Backup-Restore checklist). A rising
`db_query_ms` p95 is a *warning* (throttle invites / defer, not an immediate rollback).

**🚦 Decision gate G4 (+24 h):** *Stable & learning?* — health green for 24 h, 5xx ~0, funnel captured,
backups verified. **GO → continue to Week 1.** Degraded → hold cohort size / mitigate. Broken → roll back.

---

## Phase 6 · First Week — Learn, harden, plan

**Goal:** turn the beta into decisions; confirm durability over days; schedule the one open perf item.

**Operator actions** — daily health + backup check; mid-week **restore drill**; end-of-week review.

**Commands (daily + weekly)**
```bash
# daily
deploy/ops/healthcheck.sh && deploy/ops/verify-restore.sh
curl -fsS -H "X-IH-Auth:$SECRET" $ENGINE/api/analytics/metrics       # activation, D1 retention
python examples/db_backup.py status                                  # DB size growth + backup inventory
# weekly
#  - restore a backup into a SCRATCH path and diff (rehearse recovery)
#  - run the ~150-VU load smoke; watch db_query_ms p95 (BR1 known-risk)
curl -fsS -H "X-IH-Auth:$SECRET" $ENGINE/api/analytics/retention      # D1 (D7 fills in as the week completes)
curl -fsS -H "X-IH-Auth:$SECRET" $ENGINE/api/internal/feeds           # feeds healthy/fresh
```

**Expected results** — activation & D1 retention trends readable; DB growth bounded (analytics_events grows
per page view — confirm retention isn't unbounded); latency stable as data accumulates; feeds healthy.

**Monitoring checkpoints** — weekly p95 trend flat; error budget intact (5xx ~0); disk projection safe for
the beta window; `client_error` clusters (if any) traced to a build and fixed forward.

**Analytics validation** — full funnel + conversions reviewed; product decisions drawn (onboarding
drop-off, whether users reach *Measured Report* and *accept* recommendations). D7 retention emerging.

**Backup verification** — a **successful scratch restore drill** completed mid-week; off-host retention
healthy.

**Health verification** — 7 days of green `healthcheck.sh`.

**Rollback criteria** — same as 24 h; by now most risk is data growth / latency creep, addressed by the
load-smoke findings and (post-beta) the scaling roadmap — **not** app rollback.

**🚦 Decision gate G5 (+7 d):** *Beta healthy & instructive?* — durability proven (restore drill), metrics
captured, no unresolved sev-1. **GO → sustain / expand cohort.** Otherwise pause + remediate.

---

## Consolidated checklists

### ✅ Deployment checklist
- [ ] CI `ci-success` green on the release tag.
- [ ] `RWE_ENV=production` on both tiers; `RWE_INTERNAL_SECRET` **identical** on both.
- [ ] `NEXTAUTH_SECRET`, `GOOGLE_CLIENT_ID/SECRET`, `NEXTAUTH_URL` (**https**) set.
- [ ] `RWE_DB_URL` = persistent volume path (not `:memory:` / `/tmp`); `RWE_BACKEND_URL` set.
- [ ] Engine port private; LB body-size cap set.
- [ ] `deploy/ops/preflight.sh` exits **0**.
- [ ] Deploy order ingest → **api (ready 200)** → web; `backup-scheduler` up.

### ✅ Smoke-test checklist (run as a real user, post-deploy)
- [ ] Landing/onboarding loads; Initial Estimate builds.
- [ ] **Sign-in** (Google OAuth) round-trips → dashboard.
- [ ] Health Report renders (Estimate or Measured); charts fit (no horizontal scroll on mobile).
- [ ] Recommendations load; **Read** opens the **real publisher URL**; feedback + Why? work.
- [ ] History/Analytics/Saved/Search/Settings load with real data; theme + language switch.
- [ ] An unauthenticated visit to a protected page → redirect to onboarding; `/api/me/*` → 401.

### ✅ Monitoring checklist
- [ ] `/api/health/live` alive · `/api/health/ready` 200 (external monitor + `healthcheck.sh`).
- [ ] `/api/metrics` p95 (`request_ms`, `report_generate_ms`, `db_query_ms`) within budget.
- [ ] 5xx / `unhandled_exception` ~0; `client_error` rare; `rate_limited` rare.
- [ ] `/api/internal/feeds` healthy/fresh; disk headroom OK.
- [ ] Alerts proven to reach a human.

### ✅ Analytics checklist
- [ ] `/api/events` accepts beacons (proxy + sink) — internal dashboard shows counts climbing.
- [ ] `/api/analytics/funnel` reachers advance (app_opened → login_success → health_report_viewed → …).
- [ ] `/api/analytics/metrics` activation + time-to-first-report populate.
- [ ] Dashboard **internal-only**: 404 without `X-IH-Auth`, 200 with it.
- [ ] First-cohort funnel captured from invite #1.

### ✅ Authentication checklist
- [ ] Google OAuth is the **only** sign-in (dev demo-login disabled in prod).
- [ ] Session cookie `HttpOnly` + `SameSite=Lax` + `Secure`/`__Secure-` (https).
- [ ] Fail-closed: engine rejects per-user calls without the internal secret; refuses to boot if misconfigured.
- [ ] Sign-out clears the session; middleware redirects unauth visitors; `/api/me/*` → 401 when anonymous.

### ✅ Backup-restore verification checklist
- [ ] Scheduled backups running (compose `scheduler` or cron/systemd).
- [ ] **Off-host** copies present (a lost volume ≠ lost data).
- [ ] `deploy/ops/verify-restore.sh` exits 0 (integrity `quick_check ok`, store opens).
- [ ] A **real restore drill** rehearsed into a scratch path; **RTO recorded**.
- [ ] Restore path known: stop web → pull off-host backup → `verify-restore.sh <file>` →
      `db_backup.py restore <file>` (snapshots current, refuses if corrupt) → restart → ready 200.

---

## GO / NO-GO decision matrix

Evaluated at **G3 (T-1)** for launch, and re-used at each wave / check-in. **Any blocker RED ⇒ NO-GO.**

| # | Criterion | Signal / command | GO (green) | NO-GO (red) | Blocker? |
|---|---|---|---|---|---|
| 1 | CI green | `ci-success` on the release tag | all jobs ✓ | any job ✗ | **Yes** |
| 2 | Preflight | `deploy/ops/preflight.sh` | exit 0, 0 FAIL | any FAIL | **Yes** |
| 3 | Secrets & prod mode | preflight env rows | all set, `RWE_ENV=production` | missing/weak secret | **Yes** |
| 4 | HTTPS + OAuth | `curl -sI $WEB`; manual sign-in | https, headers, sign-in works | http, or sign-in fails | **Yes** |
| 5 | Persistent DB | `RWE_DB_URL` | volume path | `:memory:` / `/tmp` | **Yes** |
| 6 | Backups + off-host | `verify-restore.sh`; off-host copy | exit 0 + off-host present | no recent/off-host backup | **Yes** |
| 7 | Restore drill | rehearsed restore (T-7/T-1) | completed, RTO known | never rehearsed / failed | **Yes** |
| 8 | Monitoring + alerts | `healthcheck.sh`; alert test | green + alert fired | no alerting wired | **Yes** |
| 9 | Health | `/api/health/ready` | 200 ready | 503 / down | **Yes** |
| 10 | Engine private | external probe of engine port | unreachable publicly | publicly reachable | **Yes** |
| 11 | Analytics pipeline | `/api/analytics/funnel` (RM smoke) | reachers > 0, gated 404/200 | flat while active, or exposed | No (High) |
| 12 | Feeds healthy | `/api/internal/feeds` | healthy/fresh | many unhealthy/stale | No (Med) |
| 13 | Latency baseline | `/api/metrics` p95 | within budget | already elevated | No (Med) |
| 14 | Rollback assets | previous image tag + off-host backup | both staged | not staged | **Yes** |

**Decision rule:**
- **GO** — every **blocker** row GREEN. Proceed with the wave rollout (Phase 4).
- **CONDITIONAL GO** — all blockers GREEN but a non-blocker (11–13) YELLOW: launch **Wave 1 only**, watch
  the affected signal, decide on Wave 2 from live data.
- **NO-GO** — any blocker RED: slip the launch, remediate, re-run G3.

**Standing rollback rule (all phases):** app fault ⇒ redeploy the previous image tag (data untouched);
data fault (quick_check ≠ ok / corruption) ⇒ stop web, restore the newest verified off-host backup, restart.
Never hand-edit the live SQLite file.

---

*Read-only operational playbook — reuses the BR1 `deploy/ops/` tooling and the OBS1 / PA1 endpoints.
No product feature, and no change to any application code, was made to produce it.*
