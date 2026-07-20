# BR1 — Beta Launch Readiness: Phase 1 Launch Audit (read-only)

**Operational only.** No product feature, and no change to the recommendation engine, ranking,
lifecycle, evaluation, report calculations, analytics (PA1), observability (OBS1), mobile (MB1),
authentication, UX, or business logic. This phase compares the **current deployment configuration**
against the approved CTO Beta-Readiness Review and classifies every remaining item.

## Method

Read the deployment surface as it exists today: `deploy/docker-compose.yml`, `deploy/Dockerfile.{api,web}`,
`.github/workflows/ci.yml`, `DEPLOYMENT.md`, `examples/db_backup.py` + the `store` backup helpers, the
OBS1 endpoints (`/api/health/live`, `/api/health/ready`, `/api/metrics`), and the PA1 analytics
dashboard. No code was run or changed in this phase.

## What is already in place (and needs no work)

- **Fail-closed prod auth + startup validation** — both tiers refuse to boot mis-configured
  (`_config_errors`, `instrumentation.ts`); `DEPLOYMENT.md` documents the required env.
- **Durable storage** — one SQLite file in WAL mode (`journal_mode=WAL`, `synchronous=NORMAL`,
  `busy_timeout=5000`, `foreign_keys=ON`) on a **named volume** (`ih-data`); prod refuses an
  ephemeral `RWE_DB_URL`.
- **Backup/restore tooling** — `examples/db_backup.py` does a consistent online backup + an
  integrity-checked, snapshot-first restore. **The tool exists; scheduling + off-host + a drill do not.**
- **Security posture** — CSP/HSTS/frame/nosniff headers, CORS locked in prod, `no-store` on `/api/*`,
  rate limits (on by default) + body-size limits, request correlation ids.
- **Observability (OBS1)** — liveness/readiness/metrics endpoints, structured request logs, a
  vendor-agnostic error reporter. **Endpoints exist; nothing is wired to page an operator.**
- **CI** — pytest matrix (3.11/3.12), web typecheck + build, extension tests, **Docker image builds**,
  an aggregate gate.
- **Healthcheck** — compose `api` healthcheck already probes `/api/health`.

## Remaining items — classified

Classification: **Launch blocker** (must be resolved before inviting users) · **High** (do in the first
days) · **Medium** (during beta) · **Future** (after beta). "BR1 delivers" = what this workstream ships
to close it; several items are turnkey operator actions once the tooling/docs land.

| # | Item | Class | Evidence | BR1 delivers |
|---|---|---|---|---|
| **B-1** | **Automated, off-host backups + a tested restore.** Backups are manual (`docker-compose.yml` `backup` service is `profiles:["backup"]`); off-host copy is documented but unautomated. One SQLite file = a lost volume with no off-host copy loses the whole cohort. | **Launch blocker** | `deploy/docker-compose.yml` (backup profile); `DEPLOYMENT.md` §Disaster recovery | Phase 2: `backup.sh` (backup + retention + off-host hook), `verify-restore.sh` (non-destructive), a scheduler (compose sidecar + systemd/cron), recovery checklist |
| **B-2** | **Uptime / 5xx / latency alerting.** OBS1 built `/api/health/{live,ready}` + `/api/metrics`, but nothing alerts — a live-beta outage goes unnoticed until a user complains. | **Launch blocker** | `DEPLOYMENT.md` §Health & observability ("wire it to your platform's probes"); no monitor config in repo | Phase 3: `healthcheck.sh` (probe + webhook), alert-threshold guidance, an operational runbook |
| **H-1** | **CI does not run the frontend unit suite or e2e.** CI runs `node --test lib/*.test.mjs` → only **2** `.test.mjs` files; the **96-test `.test.ts` suite** (`npm test`) and **Playwright e2e (12)** never run in CI, so a logic/critical-flow regression during beta iteration won't fail a PR. | **High** | `.github/workflows/ci.yml` (web job glob); `web/package.json` `test` script | Phase 4: run `npm test` + the `.mjs` tests, add an **e2e job**, add compose-config validation |
| **H-2** | **Production go-live not verified deterministically.** The guardrails enforce fail-closed boot, but there is no single preflight that checks env/secrets/HTTPS/OAuth/persistent-DB/backup/monitoring/analytics/health before invite. | **High** | no preflight script in repo | Phase 5: `preflight.sh` + the deployment checklist |
| **H-3** | **No launch runbook / checklist** (deploy order, rollback, first-day/first-week ops). | **High** | docs/ has design docs, not a launch checklist | Phase 6: `docs/BETA_LAUNCH_CHECKLIST.md` |
| **M-1** | **No load/perf smoke at ~150 users.** SQLite is single-writer; two write-on-read paths add pressure — the report path writes the lifecycle ledger on `GET /api/report` (`api_fastapi.py`, RC2.3) and PA1 writes an analytics row per page view. Low-probability at closed-beta concurrency and now *monitorable* (OBS1 p95/DB latency). | **Medium** | `api_fastapi.py` (`_annotate_improvement_lifecycle` save on read); PA1 sink | Guidance in the checklist (a 150-VU smoke + what to watch); **no app change** — retiring write-on-read touches business logic and is out of BR1 scope |
| **M-2** | **Post-deploy analytics validation.** PA1 is proven in e2e; confirm events flow in the *deployed* env (first-cohort funnel is unrepeatable). | **Medium** | PA1 internal dashboard | Checklist step: query `/api/analytics/funnel` after launch |
| **F-1** | Chart-page bundle (~377 kB First-Load JS) lazy-load. | **Future** | build output | Out of scope (touches MB1/product) |
| **F-2** | Shorter / revocable sessions (30-day JWT). | **Future** | NextAuth JWT | Out of scope (touches authentication) |
| **F-3** | Non-additive DB-migration framework (today: `create_all` + additive `ALTER`). | **Future** | `store.py` `_ensure_*_columns` | Not needed until the first non-additive change |
| **F-4** | PostgreSQL + shared limiter + multi-worker; nonce-CSP; automated a11y gate; rec-prose localization; offline/PWA. | **Future** | CTO review | Out of scope |

## Phase-1 conclusion

**Two launch blockers (B-1 backups, B-2 alerting) and three High items (H-1 CI, H-2 preflight, H-3
runbook) — all operational, none in application code.** BR1 Phases 2–6 close every one of them with
tooling + configuration + documentation that reuses the existing `db_backup.py` and OBS1 endpoints,
changing no application behavior. The Medium items are launch-checklist guidance; the Future items are
explicitly out of BR1's operational scope.

*Read-only phase — no code written.*
