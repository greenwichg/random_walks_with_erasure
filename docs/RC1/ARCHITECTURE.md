# RC1 — Release Architecture

Release-level architecture for the **Information Health** product (the web app + engine + browser
extension built on the RWE recommender). This is the consolidated RC1 view; the exhaustive,
file-by-file walkthrough lives in [`../SYSTEM_ARCHITECTURE_GUIDE.md`](../SYSTEM_ARCHITECTURE_GUIDE.md),
the report maths in [`../HEALTH_REPORT.md`](../HEALTH_REPORT.md) / [`../MATH.md`](../MATH.md), and the
recommender research in the top-level [`../../README.md`](../../README.md).

## 1. Overview

Information Health is a two-tier application over a research recommender:

- **Web tier** — a Next.js 14 (App Router) app. Renders the UI and hosts a thin server-side **proxy**
  (`app/api/*`) that is the *only* thing that talks to the engine. It owns sessions (NextAuth /
  Google) and never computes health numbers itself.
- **Engine tier** — a FastAPI service (`examples/api_fastapi.py`) wrapping the `rwe/` recommender,
  the Information Health report, the AI coach, story clustering, search, and the article ingest
  pipeline. It owns all durable state in one SQLite database (`examples/store.py`).
- **Browser extension** — a Manifest V3 extension that records real reads from any news site into the
  same canonical pipeline via a per-user API token.

The design rule that shapes everything: **the engine is the single source of truth for user state and
every health number; the web tier is a rendering + session layer that fails closed** (in production it
never fabricates data — see the mock policy in §3 and [`OPERATIONS.md`](OPERATIONS.md)).

## 2. Component diagram

```mermaid
flowchart TB
  subgraph Client
    B["Browser — Next.js UI<br/>(React 18, React Query)"]
    EXT["Browser Extension (MV3)<br/>records reads via Bearer token"]
  end

  subgraph WebTier["Web tier — Next.js 14 (App Router)"]
    PAGES["Server components / pages"]
    PROXY["/api/* proxy routes<br/>(lib/backend.ts, engine-auth.ts)"]
    AUTH["NextAuth (JWT) + middleware<br/>(lib/auth.ts)"]
  end

  subgraph EngineTier["Engine tier — FastAPI (examples/)"]
    API["api_fastapi.py<br/>43 endpoints, auth, rate-limit, CORS, CSP"]
    ENG["engine (api_server.py) + rwe/<br/>RWE-B / RWE-D / adaptive"]
    HEALTH["Information Health report<br/>personalize.py, metric pipeline"]
    COACH["coach_service.py (AI Coach)"]
    SVC["story_service · search · discover<br/>settings_service · notification_delivery<br/>article_analyzer"]
    INGEST["ingest / rss_ingest / feed_source<br/>(article catalog)"]
  end

  DB[("SQLite<br/>store.py — 14 tables")]
  CORPUS[("Corpus .npz + FeedArticle catalog")]
  LLM["Anthropic API (optional)<br/>coach narrative only"]
  FEEDS["RSS / news feeds (optional)"]

  B -->|HTTPS same-origin| PAGES
  B -->|axios /api/*| PROXY
  EXT -->|POST /api/me/reads<br/>Authorization: Bearer| PROXY
  PAGES --> AUTH
  PROXY -->|server-to-server<br/>X-IH-User-Id + X-IH-Auth| API
  AUTH -->|POST /api/internal/users| API
  API --> ENG --> CORPUS
  API --> HEALTH --> DB
  API --> COACH --> LLM
  API --> SVC --> DB
  API --> INGEST --> DB
  INGEST --> FEEDS
  ENG --> DB
```

## 3. Request flow (the one path every feature takes)

Every screen reads server state through one uniform chain. There is exactly one implementation per
tier, so transport, auth, caching, and error handling live in one place each.

```mermaid
sequenceDiagram
  participant U as Browser (React Query hook)
  participant S as services/api.ts (axios)
  participant P as Next proxy /api/*
  participant E as FastAPI engine
  participant D as SQLite / corpus

  U->>S: useReport() → services.report()
  S->>P: GET /api/report  (session cookie)
  P->>P: engineAuthHeaders() → X-IH-User-Id (+ X-IH-Auth)
  P->>E: GET /api/report  (6s timeout, no-store)
  E->>E: _real_uid() trust check → resolve user
  E->>D: read reads / snapshots / corpus
  E-->>P: 200 report  |  401 auth  |  5xx
  P-->>S: 200 data  |  401  |  503 engine_unavailable (prod) / mock (dev only)
  S-->>U: data → render  |  error → ErrorState
```

**Mock policy (fail-closed).** `MOCK_FALLBACK_ENABLED` (`web/lib/backend.ts`) is **off** whenever
`NODE_ENV=production` unless explicitly overridden. In production an unreachable engine returns a
typed `503`, never fabricated data. Account-state routes (history, saved, settings-save, reads,
feedback, tokens) never mock even in dev. The status-preserving proxy pattern
(`lib/engine-fallback.ts`) keeps **401 (auth), 503 (unavailable), and transport failure distinct** —
they are never collapsed.

## 4. Authentication flow

Three trust tiers, fail-closed. OAuth establishes a JWT session; the web tier attributes every engine
call to a stable engine user id; the extension exchanges a token for that id.

```mermaid
sequenceDiagram
  participant U as User
  participant W as Web (NextAuth)
  participant E as Engine

  Note over U,E: 1) Sign in (once)
  U->>W: Google OAuth (or demo-login in dev)
  W->>E: POST /api/internal/users {provider, providerAccountId}
  E-->>W: { userId }  (stable engine id, upserted)
  W->>W: store engineUserId in the session JWT

  Note over U,E: 2) Every authenticated request
  U->>W: /api/report (session cookie)
  W->>E: X-IH-User-Id: <uid> (+ X-IH-Auth: <secret> in prod)
  E->>E: _real_uid(): honour header iff trusted AND user exists
  E-->>W: user-scoped data (or 401)

  Note over U,E: 3) Extension (per-user token)
  U->>W: POST /api/me/reads  Authorization: Bearer <token>
  W->>E: POST /api/internal/resolve-token {token}
  E-->>W: { userId }
  W->>E: POST /api/me/reads  X-IH-User-Id: <uid>
```

- **Production is fail-closed:** the engine **refuses to boot** in `RWE_ENV=production` without
  `RWE_INTERNAL_SECRET`; without it, it would have to trust any caller presenting `X-IH-User-Id`. The
  web app likewise refuses to boot without `NEXTAUTH_SECRET`, `RWE_INTERNAL_SECRET`, `RWE_BACKEND_URL`,
  and Google OAuth.
- **Anonymous / demo:** exhibit routes (`/api/report`, `/api/dashboard`, `/api/recommendations`,
  `/api/coach`) resolve an anonymous caller to the synthetic **demo reader**; per-user `/api/me/*`
  routes return **401**. Page routes are gated by `web/middleware.ts`.

## 5. Data flow (a real read, end to end)

```mermaid
flowchart LR
  R1["In-app Read button<br/>(record-read.ts beacon)"] --> RE["POST /api/me/reads"]
  R2["Extension on any news site"] --> RE
  RE --> SC["Scorer (ingest.py)<br/>lean · topic · register · emotion · confidence"]
  SC --> STORE[("reads table (scored)")]
  STORE --> HIST["Reading History<br/>serialize_history"]
  STORE --> DASH["Dashboard / today metrics"]
  STORE --> REP["Health Report (measured ≥5 reads)<br/>personalize.py"]
  STORE --> AN["Analytics series<br/>build_analytics"]
  REP --> SNAP[("report_snapshots")]
  RE --> PROMOTE["provisional FeedArticle<br/>(promoted after N independent readers)"]
```

A read is scored once and becomes the shared substrate for History, the Dashboard, the measured
Report (after `ESTIMATE_MIN_READS = 5` reads), and Analytics — one pipeline, no duplicate computation.
Full metric derivations: [`../METRIC_PIPELINE.md`](../METRIC_PIPELINE.md), [`../HEALTH_REPORT.md`](../HEALTH_REPORT.md).

## 6. Recommendation pipeline

```mermaid
flowchart TB
  REQ["GET /api/recommendations?strategy="] --> SERVE["_serve(): personal (measured) vs demo/row"]
  SERVE --> PARAMS["rec_params_from_settings<br/>(openness→epsilon, strength→beta)"]
  PARAMS --> REC["RWE family (rwe/):<br/>RWE-B bridging · RWE-D long-tail · Adaptive · Story slot"]
  REC --> SER["_article_payload serializer<br/>(one Article shape)"]
  SER --> MEDIA["_enrich_rec_media<br/>(real image + publishedAt from catalog)"]
  MEDIA --> EXPL["Evidence Resolver → explanation parts<br/>(evidence ⊆ context, re-derivable)"]
  EXPL --> IMPR["record_recommendations_shown<br/>(impressions → Open-Mindedness)"]
  IMPR --> OUT["ranked, explained feed"]
  OUT -.reception.-> OPEN["POST /api/me/recommendations/opened"]
  OUT -.feedback (recorded, not consumed).-> FB["POST /api/me/recommendations/feedback<br/>like/dislike/ignore/read_later"]
```

Ranking is deterministic and **isolated from feedback** — B1 recommendation feedback is recorded to
`rec_feedback` but consumed by no ranking path (RC1). Sliders map to per-request recommender
parameters; nothing is retrained online. Deep dives: [`../RECOMMENDATION_ENGINE_STATUS.md`](../RECOMMENDATION_ENGINE_STATUS.md),
[`../RECOMMENDATION_EVALUATION_ENGINE.md`](../RECOMMENDATION_EVALUATION_ENGINE.md).

## 7. Information Health pipeline

```mermaid
flowchart LR
  READS[("scored reads")] --> GATE{"count ≥ 5?"}
  GATE -- no --> EST["Initial Estimate<br/>be.estimate(onboarding outlets)<br/>mode = estimate"]
  GATE -- yes --> MEAS["personalizer.report(uid)<br/>mode = measured + axisConfidence"]
  MEAS --> M["Metrics: topic / source / viewpoint diversity,<br/>echo, reporting-vs-opinion, emotion,<br/>Open-Mindedness (rec reception)"]
  M --> VP["Political distribution (report.viewpoint)"]
  M --> BS["Blind spots · improvements"]
  MEAS --> SNAP[("report_snapshots → trend / journey")]
```

Every score is computed by the real pipeline from real reads; unknown-outlet lean is **null, never a
fabricated centre** (L2.2), and estimate vs measured is explicitly labelled. Independent recomputation
lives in the metric-validation notebook (see [`../METRIC_PIPELINE.md`](../METRIC_PIPELINE.md)).

## 8. Extension integration

```mermaid
flowchart LR
  subgraph Extension
    OPT["options.js — pair with app URL + token"]
    BG["background.js — capture read"]
    CON["content.js — page metadata"]
  end
  BG -->|"POST {appUrl}/api/me/reads<br/>Authorization: Bearer <token>"| PROXY["Next proxy"]
  PROXY -->|resolve-token → uid| ENGINE["engine /api/me/reads"]
  ENGINE --> READS[("reads — same table as in-app")]
```

The extension writes to the **same** `/api/me/reads` pipeline the in-app Read button uses (single
source of truth); tokens are minted/revoked in Settings and never exposed on the engine's public
surface. See [`../EXTENSIONS.md`](../EXTENSIONS.md) and [`../../extension/README.md`](../../extension/README.md).

## 9. Deployment architecture

```mermaid
flowchart TB
  subgraph Edge["Fronting proxy / LB (nginx / platform)"]
    TLS["TLS · client_max_body_size · probes"]
  end
  subgraph App["App network"]
    WEB["Next.js (next start)<br/>RWE_ENV=production, mock OFF"]
    ENG["FastAPI engine (uvicorn, N workers)<br/>each worker builds its own in-memory engine"]
  end
  VOL[("SQLite volume<br/>+ timestamped backups")]
  GOOG["Google OAuth"]
  ANTH["Anthropic API (optional)"]

  TLS --> WEB
  WEB -->|"server-to-server, internal<br/>X-IH-Auth = RWE_INTERNAL_SECRET"| ENG
  WEB --> GOOG
  ENG --> VOL
  ENG --> ANTH
```

- The engine is **internal** (not browser-facing): CORS is locked (`[]`) in production; the web tier
  reaches it server-to-server with the shared secret.
- Each uvicorn worker builds its own in-memory recommender at startup — **size memory per worker**,
  and rate limits are per process (N× with `--workers N`).
- Container images: `deploy/Dockerfile.web`, `deploy/Dockerfile.api`, `deploy/docker-compose.yml`.
  Full production guidance: [`../../DEPLOYMENT.md`](../../DEPLOYMENT.md).
