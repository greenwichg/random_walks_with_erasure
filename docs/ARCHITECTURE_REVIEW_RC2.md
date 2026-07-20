# Architecture Review — post-RC2

A comprehensive, read-only review of the Information Health codebase after the RC2 recommendation
workstreams (RC2.1–RC2.5). **No code was changed.** It assesses 16 dimensions, surfaces hidden debt,
and ends with a prioritized roadmap and a single highest-value next action.

## Bottom line

The product is **feature-rich and well-engineered**; the recommendation engine is **feature-complete
for this architecture** (see §16). The gap to a healthy 100–150-user beta is **not more features** — it
is **operational visibility**. My single recommendation as lead architect:

> **Build the observability foundation** — web-tier error/crash reporting, engine log aggregation with
> a 5xx/latency alert, and an uptime check on `GET /api/health`. It is the lowest-maturity dimension,
> the explicit condition from the Beta Readiness Audit, the cheapest high-leverage work left, and — now
> that RC2 made `GET /api/report` write to the database on every fetch — the only way you will *see* the
> failure modes a real beta will produce.

## Dimension assessment

| # | Area | Maturity | Effort to close | Impact | Key remaining gap |
|---|---|---|---|---|---|
| 1 | Architecture | **8** | M | Med | api_fastapi.py monolith; write-on-read on `GET /api/report` |
| 2 | Maintainability | **8** | M | Med | 2.6k-line api_fastapi; backend-English rec prose; hand-kept mock |
| 3 | Production readiness | **7.5** | M | High | observability + migrations + single instance |
| 4 | Scalability | **6.5** | L | Med (beta) / High (later) | SQLite single-file, single engine, per-process limiter, write-on-read |
| 5 | Security | **9.5** | — | — | 30-day non-revocable JWT (minor) |
| 6 | Performance | **7** | S–M | Med | chart pages ~376 kB; **no load test at scale** |
| 7 | UX | **9** | — | — | 11-item nav discoverability (watch) |
| 8 | Mobile responsiveness | **8.5** | S | Low | chart bundle weight on slow networks |
| 9 | Accessibility | **8** | S | Med | no automated a11y (axe) gate in CI |
| 10 | Testing | **8.5** | S–M | Med | **1390 pytest + 96 node + Playwright**, but no load/perf or a11y test |
| 11 | Deployment readiness | **7.5** | S–M | High | **no versioned migrations**; single instance |
| 12 | Observability | **6** ← weakest | **S** | **High** | **no error reporting, no product analytics, no uptime alert** |
| 13 | Technical debt | **7** | M | Med | write-on-read; monolith; eval cohort O(users×snaps); un-localized prose |
| 14 | API design | **8.5** | S | Med | `GET /api/report` now has write side-effects (non-idempotent) |
| 15 | Data model | **8** | S–M | Med | `create_all` only (no migrations); snapshots store full JSON |
| 16 | Recommendation engine | **9.5** | — | — | **feature-complete for this architecture** (§16) |

*Effort: S ≈ days, M ≈ 1–2 weeks, L ≈ multi-week.*

### 1. Architecture — 8
Clean **leaf-module** pattern: pure, testable leaves (`settings_service`, `notification_*`,
`improvement_ledger`, `improvement_ranking`, `recommendation_eval`) under a thin API tier, with
`api_server` owning generation and `api_fastapi` owning routing/persistence. Good separation kept RC2
generation untouched while ranking/evaluation layered cleanly on top.
- **Hidden debt:** `api_fastapi.py` is now ~2,600 lines and growing — a de-facto monolith mixing
  routing, models, auth, and orchestration. **`GET /api/report` performs DB writes** (RC2.3 lifecycle
  reconcile via `save_improvement_lifecycle`) on every signed-in fetch — a read endpoint with
  side-effects. Dependencies: splitting it into routers is independent, low-risk.

### 2. Maintainability — 8
Strong docstrings, deterministic tests, additive-optional API discipline. **Debt:** the monolith above;
the recommendation prose (evidence, impact explanation, lifecycle) is **backend English, not localized**
(the deferred i18n item flagged in RC2.1.1); `web/mock/data.ts` is hand-maintained and now mirrors five
optional rec sub-objects.

### 3. Production readiness — 7.5
Beta Readiness Audit scored 83/100 (GO-conditional). The conditions are unchanged: observability,
migrations, single-instance. RC2 didn't regress this but added a write path to a read endpoint.

### 4. Scalability — 6.5
SQLite (WAL + `busy_timeout=5000`), a **single engine instance**, and a **per-process** rate limiter.
RC2 **increased write load**: every signed-in report GET now upserts ≤6 lifecycle rows, on top of
`save_report` on model rebuild. Bounded and try/except-guarded, but **untested at the 100–150 concurrent
target**. Documented post-beta path: PostgreSQL + shared limiter + a second engine.

### 5. Security — 9.5
Unchanged and production-grade: fail-closed auth (engine won't boot in prod without
`RWE_INTERNAL_SECRET`), `X-IH-User-Id` honored only with the secret, CORS `[]` in prod, full security
headers, dev endpoints 404 in prod. **RC2 endpoints are correctly gated** — `/api/me/*` require a real
user (401 anon), the cohort quality endpoint is dev-gated (404 in prod). Minor: 30-day non-revocable
JWT.

### 6. Performance — 7
Report model is cached per `(reading_version, reception_version)`; RC2 evaluation reuses snapshots
(recomputes nothing). **But:** chart pages are ~376 kB First-Load JS, there is **still no load test at
beta scale**, and the new write-on-read makes that test more important, not less.

### 7–9. UX / Mobile / Accessibility — 9 / 8.5 / 8
Polished, honest, progressively-disclosed; RC1 hardened touch, single-`<h1>`, empty-state CTAs, and the
flagship report's i18n. Gaps: chart bundle weight on mobile; **no automated a11y gate** (axe/pa11y) in
CI — a11y is maintained by discipline, not enforced.

### 10. Testing — 8.5
**1,390 pytest** (grew ~50 this session), **96 node**, a Playwright journey suite, with a strong
determinism/parity/honesty culture (every RC2 phase shipped golden/deterministic tests). **Gaps:** no
load/perf test, no a11y assertion, no web error-boundary test.

### 11. Deployment readiness — 7.5
Fail-fast env validation, `DEPLOYMENT.md`/`OPERATIONS.md`, WAL backups. **`create_all` only — no
versioned migrations.** This session I added **two tables** (`improvement_lifecycle`) and several
columns via `create_all`; additive changes are safe, but the first **non-additive** change during beta
iteration is unguarded, and rolling code back onto a migrated DB has no story.

### 12. Observability — 6 (weakest)
`(app)/error.tsx` still only `console.error`s (its comment says *"In production this would report to
Sentry"*); there is **no `global-error.tsx`**, **no product/funnel analytics**, and **no configured
uptime/alerting** on `/api/health` (which exists). Across 100–150 users you'd learn about crashes / 5xx
spikes only from user complaints. This is the single biggest lever.

### 13. Technical debt — 7 (manageable, but growing at the API tier)
Hidden / accumulating: **write-on-read** on `GET /api/report`; the **api_fastapi monolith**; the cohort
eval endpoint is **O(users × snapshots)** (dev-gated, fine at beta scale, won't scale); **un-localized**
recommendation prose; **`create_all`** schema evolution.

### 14. API design — 8.5
RESTful, versioned Pydantic models, and an exemplary **additive-optional** backward-compat discipline
across all of RC2 (`exclude_none`, new fields never break old consumers). **Debt:** `GET /api/report`
is no longer idempotent (it writes lifecycle rows) — a REST smell that couples the read path to write
availability (graceful via try/except, but conceptually wrong).

### 15. Data model — 8
Clean SQLAlchemy, WAL, idempotent upserts, and — added this session — **savepoint-per-insert** race
handling on the lifecycle ledger. **Debt:** no migrations; snapshots persist the full report JSON
(denormalized — convenient for eval, but every schema change to the report silently changes snapshot
shape).

### 16. Recommendation engine — 9.5 — FEATURE-COMPLETE for this architecture
Per the brief, stated explicitly: **the recommendation engine is feature-complete for the current
architecture.** RC2 delivered the full arc — per-user **evidence binding** (RC2.1), honesty fixes
(RC2.1.1), **dynamic impact estimation** (RC2.2), a **lifecycle ledger** (RC2.3), **feedback-aware
ranking** (RC2.4), and **evaluation & attribution** (RC2.5) — all deterministic, explainable, and
test-covered. The **only** remaining rec-engine work is the **calibration→estimator learning loop**
(RC2.5 exposes calibration; wiring it back), and that is **correctly future work**: it needs real beta
data *and* the observability/analytics to trust it. **The next high-value action is not another
recommendation feature.**

## Prioritized roadmap

### Immediate — highest ROI (before beta)
1. **Observability foundation.** Wire web error/crash reporting (Sentry or equivalent) in
   `(app)/error.tsx` + a new `global-error.tsx`; forward the engine's structured JSON logs to an
   aggregator with a 5xx/latency alert; configure an uptime check + alert on `GET /api/health`.
   - *Why now:* lowest-maturity dimension; the explicit Beta-Audit GO condition; a **force multiplier**
     that makes every other risk *detectable*. *Unlocks:* safe beta operation, fast incident diagnosis,
     the substrate product analytics plugs into. *Risk if postponed:* you run the beta **blind** — the
     write-on-read/contention risk (below) becomes invisible until users churn. *Before beta:* **yes.**
2. **Middleware matcher fix** (add `/saved`, `/search`). Trivial; bundle with #1. *Before beta: yes.*

### Near-term — first days/week of beta
3. **Load/perf smoke at ~150 VUs** hitting `report` (now write-on-read), `recommendations`, `reads`;
   watch SQLite `busy_timeout` and engine latency. *Why now:* RC2 **amplified write load on a read
   path**; this is the concrete de-risk. *Before beta:* strongly preferred.
4. **Product/funnel analytics** (onboarding→activation→retention). *Why now:* a beta exists to *learn*;
   without this you ship blind to the very questions it should answer. *Unlocks:* the RC2.5 calibration
   loop later. *Before beta:* ideally, else first week.
5. **Lightweight migration discipline** (a documented manual procedure or a small tool). *Why now:* you
   just added tables via `create_all`; the first non-additive change during iteration needs a story.
   *Risk if postponed:* a botched schema change corrupts beta data with no rollback.

### Medium-term
6. **Retire the write-on-read smell** — move lifecycle reconciliation off the `GET /api/report` hot path
   (a dedicated `POST …/view` the client already can call, or an async/batched write), restoring an
   idempotent report read. *Unlocks:* cleaner scaling + caching.
7. **Lazy-load chart bundles** (Report/Analytics/Dashboard) to cut ~376 kB First-Load JS.
8. **Split `api_fastapi.py`** into routers (report, me, recommendations, dev) — pure refactor.
9. **Localize recommendation prose** (evidence/impact/lifecycle) — the deferred i18n debt.
10. **Automated a11y gate** (axe) + a web error-boundary test in CI.

### Long-term
11. **PostgreSQL + shared rate limiter + a second engine instance** when concurrency demands.
12. **Close the recommendation learning loop** — feed RC2.5 calibration back into the RC2.2 estimator
    (shrinkage toward realized), *after* beta data + observability exist. The one remaining rec-engine
    item, correctly last.
13. Shorter/revocable sessions; offline handling.

## Single next action (lead-architect pick)

**Implement the observability foundation (error reporting + log aggregation + uptime/health alerting),
and fold in the two-line middleware matcher fix.**

**Justification — highest overall value:**
- It closes the **weakest dimension (6/10)** and satisfies the **explicit GO condition** from the Beta
  Readiness Audit.
- It is a **force multiplier**: every other roadmap risk — the RC2 write-on-read, SQLite contention, a
  frontend crash, a 5xx spike — only becomes *actionable* once you can see it. Choosing load-testing or
  analytics first still leaves you blind to what you didn't test.
- It is **cheap** (days) and **low-risk** (additive instrumentation, no product logic touched).
- It is a **prerequisite** for the two things a beta is *for*: operating safely and learning (product
  analytics and, eventually, the RC2.5 calibration loop both plug into this substrate).
- Crucially, **RC2 itself raised the stakes**: `GET /api/report` now writes to the database on every
  signed-in fetch. If that causes contention under real concurrency, observability is the difference
  between a diagnosed one-line fix and a silent, churn-inducing beta failure.

You do not need another feature to launch the beta — you need to be able to *see* it.

---

*Read-only architecture review. No code was modified.*
