# RC1 — Engineering Guide

Orientation for engineers working on the Information Health product. Deep dives are cross-linked; the
canonical file-by-file map is [`../SYSTEM_ARCHITECTURE_GUIDE.md`](../SYSTEM_ARCHITECTURE_GUIDE.md).

## 1. Repository structure

```
random_walks_with_erasure/
├── rwe/                 # the research recommender library (RWE-B/-D, ideal-point, metrics)
├── examples/            # the ENGINE — FastAPI service + all engine services
│   ├── api_fastapi.py       # the ASGI app: 43 endpoints, auth, rate-limit, CORS, CSP, errors
│   ├── api_server.py        # engine.Backend: recommend/report/serialize (rwe/ wrapper)
│   ├── store.py             # SQLAlchemy models + repository (the single source of durable state)
│   ├── personalize.py       # measured Information Health report + Open-Mindedness
│   ├── coach_service.py      # AI coach (deterministic ladder; optional LLM narrative)
│   ├── settings_service.py   # preferences leaf (get/update/normalize)
│   ├── story_service.py · search.py · discover.py   # story clustering, search, discover
│   ├── article_analyzer.py  # anonymous single-URL analysis
│   ├── notification_delivery.py / notification_service.py   # materialize-on-fetch notifications
│   ├── ingest.py · rss_ingest.py · feed_source.py   # article catalog + scoring
│   └── ratelimit.py · body_limit / security helpers
├── web/                 # the WEB tier — Next.js 14 (App Router)
│   ├── app/(app)/*          # authenticated pages (dashboard, report, history, …)
│   ├── app/api/*/route.ts   # the server-side proxy (the only thing that calls the engine)
│   ├── components/ · hooks/ · services/ · lib/ · types/ · messages/
│   └── e2e/                 # committed Playwright regression suite (+ playwright.config.ts)
├── extension/           # Manifest V3 browser extension (records reads)
├── deploy/              # Dockerfiles, docker-compose, Colab notebooks, RSS config
├── tests/               # Python engine test suite (pytest)
├── notebooks/           # research + product notebooks
└── docs/                # architecture, health-report maths, research, and this RC1/ set
```

**Boundary rule:** `rwe/` is research (paper-faithful, no product imports). `examples/` is the engine.
`web/` never computes health numbers — it renders and proxies. See "the five invariants" at the end of
[`../SYSTEM_ARCHITECTURE_GUIDE.md`](../SYSTEM_ARCHITECTURE_GUIDE.md).

## 2. Testing strategy

Three layers, each with a fast, deterministic gate. All are green at RC1.

| Layer | Tool | Location | Scope |
|---|---|---|---|
| **Engine unit/contract** | `pytest` | `tests/` (~1,335 tests) | store, serializers, endpoints (via FastAPI `TestClient`), report/rec determinism, auth (401), ingestion, i18n catalog parity |
| **Web unit** | `node --test` (type-stripped TS) | `web/lib/*.test.ts` (89 tests) | pure presentation/aggregation logic (history-insights, rec/coach presentation, settings-diff, i18n, `engine-fallback`) |
| **Web type/build** | `tsc --noEmit`, `next build`, `check:i18n` | `web/` | type safety, production build, catalog parity/placeholder checks |
| **End-to-end** | `@playwright/test` | `web/e2e/` (11 tests, 7 journeys) | the real stack (engine + Next) — auth, reading history, feedback persistence, report estimate→measured, settings, saved, error handling. See [`../../web/e2e/README.md`](../../web/e2e/README.md). |

Principles: **determinism** (fresh DB / isolated user per E2E test; no fixed sleeps — condition-based
waits only), **honesty** (tests assert real engine behaviour, not stubs, wherever persistence/ranking
is claimed), and **contracts** (the recommendation evaluation sandbox freezes the Article/explanation
contract — [`../RECOMMENDATION_EVALUATION_ENGINE.md`](../RECOMMENDATION_EVALUATION_ENGINE.md)).

Run: `pytest -q` · (in `web/`) `npm test`, `npm run typecheck`, `npm run build`, `npm run e2e`.

## 3. Backend / engine architecture

- **`api_fastapi.py`** is the ASGI boundary: request-id + structured logging middleware, body-size and
  rate-limit guards, CORS/CSP, global exception handlers (generic `internal_error`, no leakage), and
  the 43 route handlers. Auth resolves via `_real_uid` (trusted `X-IH-User-Id`) / `_require_real_user`
  (401 for `/api/me/*`).
- **`api_server.py` (`engine.Backend`)** wraps `rwe/`: builds recommenders once at startup, serves
  `recommendations`, `report` (estimate), `serialize_history`, and the one `_article_payload` serializer
  (the single Article shape shared by every surface).
- **`personalize.py`** builds the measured report + Open-Mindedness once a user crosses
  `ESTIMATE_MIN_READS`; results are cached per user and invalidated on new reception.
- **Leaf services** (`settings_service`, `notification_delivery`, `story_service`, `search`, `discover`,
  `article_analyzer`, `coach_service`) are small, independently-tested modules the API composes.
- **Config posture:** `RWE_ENV=production` turns on fail-closed auth (`_require_auth`), config
  validation (refuse to boot on misconfig), locked CORS, and per-scope rate limits.

## 4. Frontend architecture

- **Next.js 14 App Router.** Authenticated pages under `app/(app)/*`; `middleware.ts` gates them behind
  the session.
- **Data access is uniform:** components → React Query hooks (`hooks/use-data.ts`) → `services/index.ts`
  (typed, 1:1 with endpoints) → one axios client (`services/api.ts`) → the proxy. One QueryClient
  (`staleTime 60s`, `retry 1`, no focus-refetch, no polling); mutations invalidate narrowly.
- **State/UX:** optimistic save/unsave with rollback; a fixed grace before read-derived invalidation;
  uniform loading (skeleton) / error (`ErrorState`) / empty (`EmptyState`) states.
- **i18n:** five catalogs (`messages/*.json`) generated from `_build_catalogs.py`, validated by
  `check:i18n`; components map discriminators to catalog strings (no hardcoded copy).

## 5. API layer (the proxy)

The web tier's `app/api/*/route.ts` handlers are the **only** thing that talks to the engine.

- `lib/backend.ts` — `backendGet` (fail-soft null), `backendGetResult` (status-preserving), `backendPost/Delete`,
  `MOCK_FALLBACK_ENABLED`, typed `engineUnavailable()` 503; 6s timeout + client-IP forwarding.
- `lib/engine-auth.ts` — `engineAuthHeaders()` (session → `X-IH-User-Id` + `X-IH-Auth`), token exchange.
- `lib/engine-fallback.ts` — the one place the **401-vs-503-vs-mock** decision lives (never collapsed).
- Policy: exhibit routes fall back to the demo reader for anonymous; `/api/me/*` are strict-401 and
  never mock; production serves real-or-503, never fabricated data.

## 6. Data model

One SQLite database (`store.py`), 14 tables. All durable user state lives here.

| Table | Holds |
|---|---|
| `users`, `identities` | accounts + linked provider identities (Google / dev) |
| `onboarding` | the outlets a user picked (drives the Initial Estimate) |
| `user_settings` | preferences JSON (theme, sliders, reading goal, notifications) |
| `reads` | scored reads — **the substrate** for History / Dashboard / Report / Analytics |
| `scored_articles` | cached per-URL scoring |
| `report_snapshots` | persisted reports → trend / health journey |
| `rec_events` | recommendation impressions + opens → **Open-Mindedness** |
| `rec_feedback` | explicit like/dislike/ignore/read_later (RC1; recorded, not yet consumed) |
| `saved_articles` | the single "Saved" concept (snapshot per save) |
| `notifications` | materialised notifications (idempotent per user/kind/key) |
| `api_tokens` | per-user browser-extension tokens |
| `feed_articles`, `feed_health` | the live news catalog + per-feed ingest health |

Schema is created/migrated automatically at startup (see [`OPERATIONS.md`](OPERATIONS.md) → "Database
initialization"). Additive changes only in RC1; no destructive migrations.

## 7. Extension

Manifest V3 (`extension/`): `background.js` captures a read on a news page and POSTs it to
`{appUrl}/api/me/reads` with a per-user `Authorization: Bearer` token; `options.js` pairs the app URL +
token; `content.js` supplies page metadata; `common.js` has the shared client (+ `common.test.js`). It
writes to the **same** canonical reads pipeline as the in-app button. Packaging + store submission:
[`../EXTENSIONS.md`](../EXTENSIONS.md), [`../CHROME_WEB_STORE_SUBMISSION.md`](../CHROME_WEB_STORE_SUBMISSION.md).

## 8. Analytics pipeline

`/api/me/analytics` (`build_analytics`) derives time series **entirely from stored data** — reads,
`report_snapshots`, and `rec_events` — with no separate analytics store and no fabrication:

- reading-over-time, topic/political/publisher diversity trends (from snapshots),
- emotion + reporting-vs-opinion mix (from scored reads),
- **recommendation acceptance** = opened vs ignored `rec_events` bucketed by day,
- health-improvement trend (from snapshots).

A reader with no history gets honest empty series. In production the proxy returns real-or-503 (never
mock — B3). `rec_feedback` is **not** consumed by analytics (RC1).
