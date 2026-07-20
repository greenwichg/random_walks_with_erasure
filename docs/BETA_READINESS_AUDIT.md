# Beta Readiness Audit

**Question:** is Information Health ready for a closed beta of **100–150 users**?

**Read-only audit — no changes implemented.** Findings are classified Blocker / High / Medium / Low.
Evidence is cited to the code so each finding is verifiable.

---

## Verdict

| | |
|---|---|
| **Beta readiness score** | **83 / 100** |
| **Recommendation** | **GO — conditional.** Ship the closed beta, but wire **web-tier error reporting** (H1) before or within the first days, and land the two quick fixes below (M1 matcher, a light load smoke). None of the findings are blockers. |
| **Blockers** | **0** |

The application is genuinely well-engineered for its stage: fail-closed auth, comprehensive security
headers, a green test pyramid (engine `pytest` ~1,337, web `tsc` / `check:i18n` / `node --test` 96 /
Playwright 11), structured request logging, startup env validation, and SQLite in WAL mode. The gaps
that remain are **operational observability** and a few **consistency/scale** items — not correctness
or security.

### Sub-scores

| Dimension | Score | One-line |
|---|---|---|
| Security | 9.5 / 10 | Fail-closed, CORS-locked, headers, dev-gated — production-grade. |
| User Experience | 9.0 / 10 | Progressive onboarding, RC1-hardened empty states / touch / i18n. |
| Reliability | 8.5 / 10 | Loading/retry/error states everywhere; minor matcher + offline gaps. |
| Deployment | 8.5 / 10 | Fail-fast env validation, docs, WAL; no migrations, single instance. |
| Performance | 7.5 / 10 | Good chart handling; chart pages are heavy; no load test at scale. |
| Observability | 6.0 / 10 | Structured logs + health, but **no error reporting or product analytics**. |

---

## 1. Reliability — 8.5

**Strong.** Every page ships a loading skeleton and `(app)/loading.tsx`; `EmptyState`/`ErrorState`
(with a retry button) are used consistently. React Query retries once; the web→engine proxy has an
`AbortController` timeout (`lib/backend.ts`, 6 s default) and browser calls a 15 s timeout
(`services/api.ts`). The 401 / 503 / transport distinction is preserved (no fabricated data — the E2E
"unavailable backend shows the error state, not fabricated data" test proves it, with the production
mock policy `RWE_ALLOW_MOCK_FALLBACK=false`). Session handling is NextAuth JWT with a middleware
redirect to the onboarding funnel.

| # | Finding | Severity |
|---|---|---|
| M1 | The auth middleware `matcher` (`web/middleware.ts`) omits **`/saved` and `/search`**, so an unauthenticated visitor reaches those shells instead of being redirected to onboarding. No data leaks (the `/api/me/*` calls still 401), but it's an inconsistent, broken-looking experience. | **Medium** |
| L1 | **No offline handling** (no service worker / offline cache) — offline degrades to error states with retry. Acceptable for a web app beta. | Low |
| L2 | **No root `global-error.tsx`** — a crash above the `(app)` boundary (root layout / providers) isn't caught gracefully. | Low |
| L3 | Residual `settings` GET / `onboarding` POST 401→503 status-mapping edge (from the Backend Connectivity Audit) — no fabrication, just a status nuance. | Low |

## 2. Performance — 7.5

Charts render at an explicitly measured width (no Recharts `ResponsiveContainer` collapse), and axes
are now consistent (Analytics axis review). Shared First-Load JS is a healthy **~87.5 kB**.

| # | Finding | Severity |
|---|---|---|
| M2 | The chart-heavy pages — **Dashboard / Report / Analytics ≈ 371–373 kB First-Load JS** (Recharts is the driver) — are heavy on mobile / slow networks. Lazy-loading the chart bundles would cut the entry cost. | **Medium** |
| M4 | **No load test at the target scale.** The 100–150-concurrent profile (SQLite write contention, engine CPU for the RWE recompute on `report`/`recommendations`) hasn't been explicitly exercised. A short k6/Locust smoke would de-risk it. | **Medium** |

## 3. Security — 9.5

**The standout dimension — production-grade.**

- **Fail-closed auth:** in production the engine *refuses to boot* without `RWE_INTERNAL_SECRET`
  (`_verify_production_config`), and the web tier's env validation makes `NEXTAUTH_SECRET`,
  `RWE_INTERNAL_SECRET`, `RWE_BACKEND_URL`, `NEXTAUTH_URL` hard-required in prod.
- **Authorization:** the `X-IH-User-Id` header is honored **only** with the matching internal secret in
  production (`_real_uid`); `/api/me/*` require a real user; anonymous → 401 (E2E-verified).
- **Client/server boundary:** the browser never calls the engine directly — it goes through the Next
  proxy, which attaches the internal secret; API responses are `no-store`.
- **Headers** (`lib/security-headers.mjs`): CSP with `frame-ancestors 'none'`, `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy` (camera/mic/geo/topics
  off), and HSTS in production. **CORS is locked to `[]` in production.**
- Dev-only endpoints (`/api/dev/diagnostics`) return **404 in production**. API tokens are stored
  hashed (plaintext shown once).

| # | Finding | Severity |
|---|---|---|
| L5 | Session is a **30-day JWT** (NextAuth default) with no server-side revocation — fine for a closed beta, but consider a shorter TTL and a documented "revoke a beta tester" path. | Low |

## 4. Observability — 6.0 (weakest area)

The engine emits **structured JSON request logs** (`{event, requestId, method, path, status,
durationMs}`) and exposes `GET /api/health`. What's missing is anything **proactive**.

| # | Finding | Severity |
|---|---|---|
| H1 | **No web-tier error/crash reporting.** `(app)/error.tsx` only `console.error`s (its own comment says "in production this would report to Sentry"). Across 100–150 users you would learn about frontend crashes / 5xx spikes only from user reports or manual log-grepping. Cheap to add, high value. | **High** |
| H2 | **No product-usage analytics / funnel telemetry.** The app's "Analytics" is the *user's own* reading data; there is no instrumentation of onboarding completion, activation, or retention — the very things a beta exists to learn. (Downgrade to Medium if the beta's goal is stability, not learning.) | **High** |
| M-obs | **Health monitoring is an endpoint, not a monitor.** `/api/health` exists but there's no configured uptime check / alerting on it. This is an ops setup task, not a code gap. | Medium |

## 5. User Experience — 9.0

Polished and honest, and hardened over the recent RC1 passes: a progressive onboarding funnel
(value → Estimate → sign-in) that never fabricates data; the Estimate→Measured journey now carries
context across Dashboard/Report/Analytics with coverage progress; metric empty states explain what
unlocks them; empty states (Saved/Recommendations) have CTAs; the sidebar streak is truthful; hover-
only touch traps and the duplicate `<h1>` are fixed; the flagship report is fully localized (5
languages).

| # | Finding | Severity |
|---|---|---|
| L-ux | The primary nav is **11 items across three groups** — comprehensive but a lot for a first-timer; watch beta feedback on discoverability of Stories / Analytics / Coach. | Low |

## 6. Deployment — 8.5

Both tiers **fail fast on misconfiguration** at startup; `.env.example` documents every required key;
`DEPLOYMENT.md` / `OPERATIONS.md` cover Docker + non-Docker, startup, backup/recovery, and
troubleshooting. SQLite runs in **WAL + `busy_timeout=5000` + `synchronous=NORMAL`** (readers never
block the writer), which materially de-risks beta-scale concurrency.

| # | Finding | Severity |
|---|---|---|
| M3 | **No versioned DB migrations** — the schema is created with `create_all` (idempotent). Additive changes are safe, but a non-additive change during beta iteration needs manual care, and rolling *code* back onto an already-migrated DB is unguarded. Adopt a lightweight migration tool (or a documented manual procedure) before the first schema change. | **Medium** |
| M5 | **Single engine instance + per-process rate limiter** — a single point of failure with no horizontal-scaling story for the beta window. Acceptable for 100–150 if monitored; a shared limiter + PostgreSQL are the documented post-beta step. | **Medium** |
| — | **Rollback:** code rollback is clean (stateless web + engine); the risk is only the DB (see M3). Back up the SQLite file before each deploy (OPERATIONS.md covers this). | — |

---

## Findings by severity

| Severity | Findings |
|---|---|
| **Blocker** | *(none)* |
| **High** | H1 no web error reporting · H2 no product/funnel analytics |
| **Medium** | M1 middleware misses /saved,/search · M2 chart-page bundle weight · M3 no DB migrations · M4 no load test at scale · M5 single instance / per-process limiter · M-obs no uptime alerting |
| **Low** | L1 no offline · L2 no root error boundary · L3 settings/onboarding 401→503 edge · L5 30-day non-revocable session · L-ux 11-item nav |

## Remaining work before inviting 100–150 users

**Do first (small, high-value):**
1. **Wire web-tier error reporting** (Sentry or equivalent) in `(app)/error.tsx` + a `global-error.tsx`, and forward the engine's structured logs to an aggregator with a 5xx alert. *(H1)*
2. **Add `/saved` and `/search` to the middleware matcher** so unauthenticated visitors are redirected to onboarding. *(M1)*
3. **Run a short load smoke** (~150 virtual users hitting `report`/`recommendations`/`reads`) and watch SQLite `busy_timeout` and engine latency. *(M4)*
4. **Configure an uptime check + alert** on `GET /api/health`. *(M-obs)*

**Do soon (first week of beta):**
5. **Minimal product analytics** — at least onboarding-funnel + activation events, so the beta produces learning. *(H2)*
6. **Back up the SQLite file on a schedule** and document the restore drill (OPERATIONS.md has the procedure — verify it once for real). *(M3 / rollback)*
7. **Lazy-load the chart bundles** on Report/Analytics/Dashboard to cut First-Load JS. *(M2)*

**Track for post-beta (not gating):**
8. PostgreSQL + a shared rate limiter + a second engine instance when concurrency demands. *(M5)*
9. A versioned migration tool before the first non-additive schema change. *(M3)*
10. Shorter / revocable sessions; offline handling; a root error boundary. *(L1/L2/L5)*

---

*No fixes were implemented in this audit.*
