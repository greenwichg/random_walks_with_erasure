# Information Health — Release Notes (RC1)

First production release candidate of the Information Health product (web app + engine + browser
extension) built on the RWE recommender. This documents what ships, what is intentionally deferred, and
what comes next. Deep history lives in the per-workstream design docs under [`../`](../).

## Major capabilities

**Reading & recommendations**
- **Recommendation feed** from the real RWE family — RWE-B (bounded political bridging), RWE-D
  (long-tail discovery), Adaptive RWE-B, and a conditional Story-Match slot — with per-card,
  evidence-backed "Why?" explanations that re-derive from real recommender evidence.
- **Preference controls** map to per-request recommender parameters (openness→ε, strength→β); a
  reading-goal feeds the dashboard.
- **Recommendation feedback** — like / dislike / ignore / read-later are **persisted** to the engine
  (authenticated); *ignore* survives a reload. (Recorded only in RC1 — see limitations.)

**Information Health**
- **Health Report** — topic / source / viewpoint diversity, echo, reporting-vs-opinion, emotion, and
  Open-Mindedness, computed from real reads. Explicit **Initial Estimate** (from onboarding) below the
  5-read threshold, **Measured** report with axis confidence above it.
- **Reading History** — every read scored once and reflected in History, the Dashboard, the Report, and
  Analytics; per-day Daily Summary and Reflection. Unknown-outlet political lean is shown as **"Unknown"**
  and excluded from aggregation — never a fabricated centre.
- **Dashboard & Analytics** — score, trend, today's metrics; time-series analytics derived entirely
  from stored data (no fabrication).

**Discovery & product**
- **Stories** (clustered events), **Discover**, and **Search** over the live article catalog.
- **Saved** articles (persisted, optimistic UI). **AI Coach** — a deterministic, grounded advisor
  (optional generated narrative with an Anthropic key). **Article Analyzer** — anonymous single-URL
  analysis. **Notifications** — materialised on fetch.
- **Browser extension** — records real reads from any news site into the same canonical pipeline via a
  per-user token.

**Platform & trust**
- Google sign-in (NextAuth JWT), fail-closed production auth, locked CORS, strict CSP + security
  headers, per-scope rate limiting, request-size caps, structured request logging with request-id
  tracing, and automatic DB schema creation/migration.
- **Truthfulness posture:** in production the web tier never serves mock data — an unavailable engine
  yields a typed `503`, and 401 (auth) / 503 (unavailable) / transport failure are kept distinct.
- **i18n:** five languages (catalog-validated).

**Quality**
- Engine `pytest` (~1,335), web `node --test` (89) + `tsc` + production build + `check:i18n`, and a
  committed **Playwright E2E regression suite** (11 tests across 7 critical journeys) running against
  the real stack. All green.

## Known limitations

- **Feedback is recorded, not consumed.** Like/dislike/ignore/read-later persist and *ignore* filters
  the reader's own view, but no ranking or personalization path reads `rec_feedback` yet. Ranking is
  unchanged by feedback (verified).
- **Cold start shows an Estimate.** A new reader sees the onboarding-based Initial Estimate until 5
  reads accrue; some surfaces (e.g. the measured political-balance tile) are hidden until Measured.
- **Synthetic default profile.** Out of the box the engine runs a synthetic reader profile (a product
  PoC, not real behaviour) so the stack runs with zero external data; every metric is still computed by
  the real pipeline. Real catalogs (MIND / Politosphere / RSS) are opt-in.
- **AI Coach without a key** returns a deterministic grounded reply (no generated narrative).
- **Rate limits and recommender memory are per engine process** (N× with `--workers N`); the limiter is
  in-process (no Redis).
- **No web-tier crash reporter** wired yet (errors log to the server with a request-id and to the
  browser console; the error boundary shows a generic message).
- **Discover topic-browse stub** (`/api/topics`) returns `501` and has no UI consumer (dead-ish; see
  deferred debt).

## Deferred technical debt

Tracked from the Backend Connectivity Audit, the Stabilization Review, and [`../TODO.md`](../TODO.md).
None blocks RC1.

- **B2 — dead-code cleanup (not yet done):** `/api/topics` + `useTopics`/`services.topics`/`queryKeys.topics`,
  the unused `useCoachSend` hook, the unused `/api/me` proxy + engine `MeModel`, and the legacy
  `Article.imageUrl` field. Pure deletion; no behaviour change. (Note: the story singular/plural routes
  are a deliberate canonical+alias pair — **not** dead.)
- **B4 — dev mock refresh:** the dev-only recommendation mock lacks the current structured explanation
  `parts`; visible only in development when the engine is down (mock is off in production).
- **Minor proxy consistency:** `settings` GET and `onboarding` POST have a residual 401→503 edge for a
  should-never-happen orphaned session / anonymous call; neither fabricates data. Optionally align them
  with the B3 status-preserving pattern.
- **Observability:** wire a crash/error-reporting service for the web tier.
- **Minor UX:** the AI Coach page lacks a loading skeleton; the read-beacon uses a fixed grace before
  invalidation.

## Post-release roadmap

- **Consume feedback signals** — decide how (if at all) `rec_feedback` should influence ranking, then
  wire it behind evaluation gates (the recommendation sandbox exists for exactly this).
- **B2 cleanup** — delete the dead symbols above (independent, low-risk).
- **Real-data onboarding at scale** — production news catalog defaults and freshness tuning
  (see the FRESHNESS_* and RECOMMENDATION_* docs).
- **Observability** — crash reporting + dashboards over the structured logs.
- **Horizontal scale** — externalize the rate limiter and consider a shared engine cache if moving
  beyond single-process-per-worker.
- **Data store** — SQLite → PostgreSQL when concurrency demands it (the store is a thin repository over
  SQLAlchemy, chosen with this migration in mind).

See [`README.md`](README.md) for the RC1 readiness verdict and the full open-item classification.
