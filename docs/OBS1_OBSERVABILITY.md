# OBS1 — Observability Foundation

The single highest-value action from the post-RC2 architecture review: give the application **eyes**
before the closed beta. All changes are **additive, low-risk, and behavior-preserving** — no
recommendation engine, report generation, ranking, evaluation, lifecycle, or database-schema change.

## Implementation summary

Built on the **existing** backend telemetry (the engine already emitted structured JSON request logs
with a `requestId` correlation id and had full exception handlers) — OBS1 adds the missing *abstractions,
metrics, health split, and the whole frontend side*.

**New leaves (pure, dependency-free):**
- `examples/obs_metrics.py` — in-process metrics: counters + latency histograms (p50/p95/p99), thread-safe,
  **bounded** (fixed buckets + a series cap), and **guarded** so recording can never raise into a caller.
- `examples/error_reporting.py` — vendor-agnostic exception-reporter abstraction. Default `LoggingReporter`
  emits one structured JSON line per exception; `set_reporter()` swaps in a Sentry / App Insights / OTel /
  custom adapter later **without touching call sites**.

**Backend wiring (`examples/api_fastapi.py`, all additive):**
- The request middleware now records per-**route-template** request counts + latency into `obs_metrics`
  (route template, not raw path, so cardinality stays bounded).
- `report()` wraps `_report_for` in a timer (`report_generate_ms`) — it **times** generation, never alters it.
- The generic exception handler routes unexpected exceptions through `error_reporting.report_exception`
  (full traceback + `requestId`/path/method), on top of the existing correlation log line.
- **Health split:** `/api/health/live` (liveness — process up, no dependency checks) and
  `/api/health/ready` (readiness — store + engine built; **503** until ready). The original `/api/health`
  is unchanged.
- `/api/metrics` — the metrics snapshot, **internal-only** (`_trusted`: the web tier / an operator with
  the internal secret; **404** to anyone else, like the dev endpoints).
- `POST /api/client-errors` — the sink the frontend beacons to, so a browser crash lands in the same
  correlated log stream + reporter as a server error (fields truncated; covered by existing size/rate limits).
- DB query latency (`db_query_ms`) is captured via SQLAlchemy cursor events registered at startup on the
  store engine — **`store.py` is untouched** (no observability dependency leaks into it).

**Frontend (`web/`):**
- `lib/observability.ts` — vendor-agnostic `reportError()` with a swappable **provider** interface.
  Default: `consoleProvider` in dev, `beaconProvider` → same-origin `/api/client-errors` in prod. A
  `setErrorReporter()` hook plugs in Sentry / App Insights / OTel / a custom provider later.
- `app/global-error.tsx` — **new** root error boundary (catches errors that escape the root layout /
  providers). Self-contained (own `<html>`/`<body>`, inline styles, plain English) since it renders in
  place of the root layout; reports through `reportError`.
- `app/(app)/error.tsx` — now reports through `reportError` instead of a bare `console.error`.
- `app/api/client-errors/route.ts` — same-origin proxy forwarding the beacon to the engine (best-effort).
- `middleware.ts` — added `/saved` and `/search` to the auth matcher (architecture-review M1): an
  unauthenticated visitor is now redirected to onboarding instead of reaching those shells.

## Architecture

```
 BROWSER                          WEB TIER (Next)                 ENGINE (FastAPI)
 ┌───────────────┐                ┌───────────────────┐          ┌──────────────────────────────┐
 │ error.tsx     │ reportError()  │ /api/client-errors │  POST    │ POST /api/client-errors       │
 │ global-error  │───────────────▶│  proxy (+secret)   │─────────▶│  → _log + error_reporting     │
 │ lib/observ.   │  beacon        └───────────────────┘          │                               │
 └───────────────┘                                                │ @middleware _observability    │
                                                                  │  • set requestId (contextvar) │
 any request ───────────────────────────────────────────────────▶│  • time + log {requestId,…}   │
                                                                  │  • obs_metrics.record_request │
                                                                  │                               │
                                                                  │ report() → timer(report_gen)  │
                                                                  │ exception → error_reporting   │
                                                                  │ store engine → db_query_ms    │
                                                                  │                               │
                                                                  │ GET /api/health/live  (200)   │
                                                                  │ GET /api/health/ready (200/503)│
                                                                  │ GET /api/metrics  (internal)  │
                                                                  └──────────────────────────────┘
                             error_reporting.set_reporter(...)  ──▶  Sentry / App Insights / OTel / custom
```

## Request flow (tracing)

`X-Request-ID` (client-supplied or generated) → set on the `_request_id` **contextvar** in the middleware
→ stamped on **every** structured log line (`{event, requestId, …}`), on the exception report, on the
error envelope (`error.requestId`), and echoed back in the `X-Request-ID` **response header**. One id
correlates the browser, the request log, the exception, and the metrics — end to end, unchanged business
logic.

## Error flow

```
render/runtime error → error.tsx | global-error.tsx → reportError()
   dev  → consoleProvider (console, no network)
   prod → beaconProvider → POST /api/client-errors (web) → engine /api/client-errors
                                                          → _log("client_error", …) + error_reporting
server exception → @app.exception_handler(Exception) → _log("unhandled_exception") +
                    error_reporting.report_exception(exc, path, method, requestId) → typed 500 envelope
```
Every hop is best-effort: reporting **never** raises into the code it instruments.

## Example structured logs

```json
{"event":"request","requestId":"6506c450016f","method":"GET","path":"/api/report","status":200,"durationMs":103.4}
{"event":"exception","error":"ValueError","message":"kaboom","traceback":"…","path":"/api/report","requestId":"6506c450016f"}
{"event":"client_error","requestId":"-","name":"TypeError","message":"undefined is not a function","url":"/report"}
```

Metrics snapshot (`GET /api/metrics`, internal):
```json
{"uptimeSeconds":42.1,"series":{"counters":6,"timers":4},
 "counters":{"requests_total|GET /api/report|2xx":3},
 "timers":{"report_generate_ms":{"count":3,"avgMs":38.2,"p95Ms":100,"p99Ms":250},
           "db_query_ms":{"count":51,"avgMs":0.6,"p95Ms":5},
           "request_ms|GET /api/report":{"count":3,"p95Ms":100}}}
```

## Validation results

| Check | Result |
|---|---|
| `pytest tests/test_observability.py` | **11 passed** |
| `pytest observability · api_fastapi · api_server · improvement_ledger · recommendation_eval · db_durability · demo_determinism` | **188 passed** |
| Web `tsc --noEmit` | **clean** |
| Web `node --test` | **96 passed** |
| `check:i18n` | **658 keys × 5 languages** |
| `next build` | **succeeds** — adds `/api/client-errors` + `global-error`; shared JS **87.5 kB**, `/report` **376 kB** (both unchanged) |
| Playwright `health-report.spec` (live engine + web) | **1/1 passed** |

**Tested:** metrics recording/bounds/percentiles + never-raises; reporter swappable + receives context +
never raises + structured JSON; liveness/readiness (+ `/api/health` unchanged); `/api/metrics` records
report & DB timings and is **internal-only in production** (404 without the secret, 200 with it); request
correlation id echoed; client-error sink accepts + logs.

## Notes & non-goals

- **No external monitoring dependency yet** — metrics live in memory behind `/api/metrics`; a later phase
  drains them into Prometheus/OTel and registers a vendor error reporter. The **seams are in place**.
- **No behavior change:** every instrument is additive and guarded; recommendation generation, ranking,
  evaluation, lifecycle, report contract, and the DB schema are untouched.
- The public `/api/client-errors` (web) is best-effort and rides the existing size + rate limits; a beat
  a later phase can add a dedicated client-error rate bucket if abuse appears.

---

*OBS1 improves production readiness only — additive, low-risk, behavior-preserving.*
