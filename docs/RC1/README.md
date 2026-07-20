# Release Candidate 1 (RC1)

The RC1 documentation hub for the **Information Health** product (web app + engine + browser extension
built on the RWE recommender). This set consolidates and indexes the existing deep documentation for a
release reader, and records the RC1 readiness verdict.

## Verdict

**RC1 is ready.** The critical user journeys are implemented, connected to the real backend, and covered
by a green test pyramid (engine `pytest` ~1,335, web `node --test` 89 + `tsc` + build, and an 11-test
Playwright E2E suite over the real stack). Authentication and production configuration are fail-closed;
the web tier never fabricates data in production. Everything still open is dead-code cleanup or a
post-release enhancement — none of it blocks the candidate. Full rationale in
[`RELEASE_NOTES.md`](RELEASE_NOTES.md) and the classification below.

## The RC1 document set

| Document | Covers |
|---|---|
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Overview, component diagram, request flow, **authentication flow**, data flow, recommendation pipeline, Information Health pipeline, extension integration, deployment architecture (mermaid) |
| [`OPERATIONS.md`](OPERATIONS.md) | Required env vars, startup sequence, production deploy steps, DB initialization, backup & recovery, logs, health checks, **troubleshooting guide** |
| [`ENGINEERING.md`](ENGINEERING.md) | Repository structure, **testing strategy**, backend/engine, frontend, API layer, data model, extension, analytics pipeline |
| [`RELEASE_NOTES.md`](RELEASE_NOTES.md) | Major capabilities, known limitations, deferred technical debt, post-release roadmap |

### Authoritative deep dives (referenced, not duplicated)

The RC1 set is the release-level view; these remain the sources of truth for depth:

- **[`../SYSTEM_ARCHITECTURE_GUIDE.md`](../SYSTEM_ARCHITECTURE_GUIDE.md)** — exhaustive, file-by-file
  architecture, pipelines, and the five invariants.
- **[`../../DEPLOYMENT.md`](../../DEPLOYMENT.md)** — the full deployment guide (backups, Docker, security
  headers, ingestion, rate-limit tuning, data-loss matrix, startup validation).
- **[`../HEALTH_REPORT.md`](../HEALTH_REPORT.md)** / **[`../METRIC_PIPELINE.md`](../METRIC_PIPELINE.md)** /
  **[`../MATH.md`](../MATH.md)** — the Information Health metrics and their derivations.
- **[`../RECOMMENDATION_ENGINE_STATUS.md`](../RECOMMENDATION_ENGINE_STATUS.md)** /
  **[`../RECOMMENDATION_EVALUATION_ENGINE.md`](../RECOMMENDATION_EVALUATION_ENGINE.md)** — the recommender
  and its evaluation sandbox.
- **[`../EXTENSIONS.md`](../EXTENSIONS.md)** / **[`../../extension/README.md`](../../extension/README.md)**
  / **[`../CHROME_WEB_STORE_SUBMISSION.md`](../CHROME_WEB_STORE_SUBMISSION.md)** — the extension.
- **[`../PRIVACY_POLICY.md`](../PRIVACY_POLICY.md)** — data collection & privacy.
- **[`../../web/e2e/README.md`](../../web/e2e/README.md)** — the E2E regression suite.
- **[`../../web/.env.example`](../../web/.env.example)** — the environment template.

## Release-readiness review (task 1)

| Area | Status | Where |
|---|---|---|
| README accuracy | ✅ The top-level README is the research reference and correctly points to the product docs; a link to this hub is added. | [`../../README.md`](../../README.md) |
| Setup instructions | ✅ Local (two processes), Colab, and Docker paths documented and current. | DEPLOYMENT.md, OPERATIONS.md §2 |
| Environment variables | ✅ Complete + validated at startup; consolidated table added. | OPERATIONS.md §1, `.env.example` |
| Deployment instructions | ✅ Docker + non-Docker, fail-fast validation, fronting-proxy guidance. | DEPLOYMENT.md, OPERATIONS.md §3 |
| Production configuration | ✅ Fail-closed auth, locked CORS, mock-off, CSP/headers. | ARCHITECTURE.md §3–4, OPERATIONS.md §1 |
| Developer onboarding | ✅ Repo map, testing strategy, layer architecture. | ENGINEERING.md |

## Open items — classification (task 6)

**Required before RC1** — *none.* The two release-gating items from the Backend Connectivity Audit are
done: fake recommendation feedback is now real & persisted (B1), and the Analytics/Profile 401-vs-503
conflation is fixed (B3). The E2E regression suite requested at stabilization is committed and green.

**Safe after release**
- B4 — refresh the dev-only recommendation mock (structured explanation `parts`); dev-only, mock off in prod.
- Align `settings` GET / `onboarding` POST with the B3 status-preserving pattern (residual 401→503 edge; no fabrication).
- Wire a web-tier crash/error-reporting service (observability).

**Future enhancement**
- B2 — delete the dead symbols (`/api/topics` chain, `useCoachSend`, `/api/me` proxy + `MeModel`, `Article.imageUrl`).
- Consume `rec_feedback` in ranking (behind evaluation gates).
- Minor UX (coach loading skeleton), SQLite→PostgreSQL when concurrency demands, externalized rate limiter.

## Documentation summary (task delivery)

Added in RC1 (this `docs/RC1/` set): `ARCHITECTURE.md`, `OPERATIONS.md`, `ENGINEERING.md`,
`RELEASE_NOTES.md`, and this hub. They **consolidate and index** the existing ~60-doc corpus for a
release reader, add the specific release diagrams (component / request / auth / data / rec / IH /
extension / deployment as mermaid), and fill the genuine gaps: a consolidated environment-variable
table, a troubleshooting guide, a testing-strategy summary, and the product's first Release Notes. No
application behaviour was changed — documentation and release-preparation only.
