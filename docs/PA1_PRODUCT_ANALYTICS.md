# PA1 — Product Analytics & Activation Funnel

**Scope:** analytics only. No change to the recommendation engine, ranking, lifecycle, evaluation,
report calculations, authentication, observability (OBS1), mobile compatibility (MB1), or any
business logic. Everything here is **additive, best-effort, and behavior-preserving** — instrumenting
the product measures it without altering a single existing outcome.

**Objective:** a vendor-agnostic product-analytics foundation that measures **activation** and
**engagement** during the closed beta, reusing the OBS1 seam — a swappable client *provider*
beaconing to a backend *sink*, with an internal-only read-back — so the funnel can be captured from
the very first invited user (the first cohort is unrepeatable).

---

## Phase 1 — Analytics design (read-only)

### Architecture

```
 BROWSER (web/lib/analytics.ts)            WEB TIER (Next)              ENGINE (FastAPI)
 ┌──────────────────────────┐             ┌───────────────────┐       ┌───────────────────────────────┐
 │ track(event, props)      │  flush      │ POST /api/events   │ POST  │ POST /api/events              │
 │  • anonId  (localStorage)│────batch───▶│  proxy (+secret,   │──────▶│  validate + resolve uid       │
 │  • sessionId (session)   │  beacon      │   +X-IH-User-Id)   │       │  → store.record_analytics_… │
 │  • buffer + pagehide     │             └───────────────────┘       │                               │
 │  provider abstraction ───┼── setAnalyticsProvider() ─▶ GA/Mixpanel/PostHog/Amplitude/custom later   │
 └──────────────────────────┘                                         │  analytics_events table       │
                                                                       │                               │
 operator / dashboard  ───────────────────────────────────────────────▶ GET /api/analytics/funnel     │
   (internal secret, like /api/metrics; 404 to everyone else)          │      /metrics /events /retention│
                                                                       │  product_analytics.py (pure)  │
                                                                       └───────────────────────────────┘
```

**This is the OBS1 pattern, reused exactly.** OBS1 gave errors a swappable client provider
(`reportError` → `beaconProvider` → `/api/client-errors`) and an internal-only read-back
(`/api/metrics`, gated by `_trusted`). PA1 gives *events* the same shape: a swappable client provider
(`track` → `beaconProvider` → `/api/events`) and internal-only read-backs (`/api/analytics/*`). The
event **taxonomy and funnel maths live in a pure leaf** (`examples/product_analytics.py`), mirroring
how `obs_metrics.py` / `recommendation_eval.py` keep computation dependency-free and deterministic.

**Vendor-agnostic (Phase 2 requirement).** The client binds to **no** vendor. The default provider
beacons to our own `/api/events`; `setAnalyticsProvider()` swaps in Google Analytics / Mixpanel /
PostHog / Amplitude / a custom sink later **without touching a single `track()` call site** — the
identical seam as OBS1's `setErrorReporter` / `set_reporter`.

### Identity & anonymity model

Every event carries three identity fields so the funnel spans the pre-auth → post-auth boundary:

| Field | Source | Lifetime | Purpose |
|---|---|---|---|
| `anonId` | client-generated UUID in `localStorage` | stable per browser | attributes **anonymous** (pre-account) events |
| `sessionId` | client-generated UUID in `sessionStorage` | one browsing session | groups events into sessions; drives retention |
| `userId` | resolved **server-side** from the trusted `X-IH-User-Id` (never client-asserted) | the account | attributes authenticated events to the real account |

- **Anonymous support is first-class.** Before sign-in there is no `userId`; the visitor is the
  `anonId`. Every pre-account funnel stage (App Opened → Source Connected) is counted by `anonId`.
- **Stitching.** The `login_success` event is emitted with the browser's `anonId` *and* is stamped
  server-side with the resolved `userId`. The funnel leaf builds an `anonId → userId` map from these
  stitch rows, so a person's pre-auth (`anonId`) and post-auth (`userId`) events fold into one
  identity. No stitch ⇒ the visitor never advances past sign-in (correct).
- **Pseudonymous by construction.** Events carry no PII — `anonId` is a random UUID, `userId` is the
  internal engine id, and no email/name/IP is ever written to `analytics_events`. Properties are a
  small allow-listed, truncated set per event. This matches the published privacy policy and OBS1's
  "structured, pseudonymous" posture.

### The complete user journey → event taxonomy

Events are `snake_case`, namespaced by area. Every event implicitly carries `anonId`, `sessionId`,
`clientTs` (client ISO), and — stamped by the sink — `serverTs`, `requestId`, and the resolved
`userId`. "Anon?" = fires for anonymous visitors.

| # | Journey step | Event name | Trigger | Key properties | User id | Anon? |
|---|---|---|---|---|---|---|
| — | (any screen) | `app_opened` | first app mount in a session | `path`, `referrer` | uid or — | ✅ |
| — | (any screen) | `page_viewed` | SPA route change | `path` | uid or — | ✅ |
| **Visitor** | lands on onboarding | `onboarding_started` | onboarding flow mounts | `step` | — | ✅ |
| Visitor | advances a step | `onboarding_step_completed` | each onboarding step advance | `step`, `stepIndex` | — | ✅ |
| **Source** | saves outlets | `source_connected` | onboarding outlets submitted | `outletCount` | uid or — | ✅ |
| **Auth** | clicks sign-in | `signin_started` | Google sign-in button click | `method` (`google`) | — | ✅ |
| **Account** | first-ever auth | `account_created` | first authenticated load of a new account | `method` | uid | (stitched) |
| **Auth** | session established | `login_success` | authenticated session detected | `method` | uid | (stitched) |
| **First Read** | reads an article | `article_read` | a Read action is confirmed | `source` (surface), `isFirst` | uid | — |
| **Report** | opens report | `health_report_viewed` | report page renders a report | `mode` (`estimate`\|`measured`), `coverage` | uid | — |
| **Measured** | crosses threshold | *(derived)* `health_report_viewed` where `mode=measured` | — | `coverage` | uid | — |
| **Rec Viewed** | sees recs | `recommendations_viewed` | recommendations page renders ≥1 card | `count` | uid | — |
| **Rec Engaged** | opens a rec | `recommendation_opened` | Read CTA on a recommendation | `strategy`, `crossCutting` | uid | — |
| **Rec Accepted** | positive signal | `recommendation_feedback` | like / dislike / ignore / read-later | `action` | uid | — |
| **Return** | comes back | *(derived)* a `app_opened` on a later calendar day than first-seen | — | — | uid | ✅ |

*Design choices that keep the taxonomy small and honest:*
- **Measured mode** is not a separate event — it's `health_report_viewed` filtered by `mode=measured`.
  One event, one truthful property; the funnel derives the stage. (Fewer event names = less drift.)
- **Return / retention** is *derived* from `app_opened` session-days per identity, not a bespoke
  "returned" event a client can't reliably fire on a later visit.
- **The client is the emitter** (standard product-analytics posture). PA1 does **not** instrument
  `add_read`, the report path, or feedback recording server-side — that would touch business logic the
  brief forbids. The frontend already knows each moment truthfully (the read is confirmed, the report
  payload carries `mode`, the feedback is a click), so it emits the event. Trade-off (client events can
  be lost to adblock/unload) is acceptable at closed-beta scale and noted in Limitations; the dev
  dashboard can cross-check volumes against the existing authoritative tables (`reads`, `report_
  snapshots`, `rec_events`, `rec_feedback`) read-only.

### Activation funnel (Phase 4)

Ten ordered stages; each stage's population is the set of **stitched identities** that emitted its
event at least once. Conversion between consecutive stages is `reachers(next) / reachers(prev)`, and
overall conversion is `reachers(stage) / reachers(stage 0)`. Deterministic — the same event rows
always produce the same funnel.

```
  App Opened                     app_opened
        │  ──────────────────────────────────────  conv₁
  Account Created                account_created
        │  ──────────────────────────────────────  conv₂
  Login Success                  login_success
        │  ──────────────────────────────────────  conv₃
  Source Connected               source_connected
        │  ──────────────────────────────────────  conv₄
  First Article Read             article_read
        │  ──────────────────────────────────────  conv₅
  Health Report Generated        health_report_viewed
        │  ──────────────────────────────────────  conv₆
  Measured Report                health_report_viewed [mode=measured]
        │  ──────────────────────────────────────  conv₇
  Recommendation Viewed          recommendations_viewed
        │  ──────────────────────────────────────  conv₈
  Recommendation Accepted        recommendation_opened OR recommendation_feedback[like|read_later]
        │  ──────────────────────────────────────  conv₉
  Returned Next Day              app_opened on a later day than first-seen
```

The biggest `conv` drop is the **top drop-off point** the dashboard surfaces.

### Product metrics (Phase 5)

All computed deterministically from the event stream by the pure leaf:

| Metric | Definition |
|---|---|
| **Activation rate** | identities reaching *Health Report Generated* ÷ identities reaching *Account Created* |
| **Measured-activation rate** | identities reaching *Measured Report* ÷ *Account Created* |
| **Time to first report** | median(`health_report_viewed`.serverTs − `account_created`.serverTs) per user |
| **Time to measured mode** | median(first `mode=measured` − `account_created`) per user |
| **Recommendation engagement rate** | users with `recommendation_opened`\|`recommendation_feedback` ÷ users with `recommendations_viewed` |
| **Recommendation acceptance rate** | users with `recommendation_opened`\|feedback∈{like,read_later} ÷ users with `recommendations_viewed` |
| **Day-1 retention** | users with a session on first-seen + 1 day ÷ cohort |
| **Day-7 retention** | users with a session on first-seen + 7 days ÷ cohort (future-ready; ~0 during a short beta) |

### Developer dashboard (Phase 6)

Internal-only endpoints, gated by `_trusted` exactly like OBS1's `/api/metrics` (404 to anyone
without the internal secret; never exists for the public):

- `GET /api/analytics/funnel` — the 10-stage funnel: per-stage reachers + stage/overall conversion.
- `GET /api/analytics/metrics` — the product metrics above.
- `GET /api/analytics/events` — event counts by name (+ optional time window).
- `GET /api/analytics/retention` — D1/D7 cohort retention.
- Top drop-off is included in the funnel payload (the largest stage-to-stage drop).

### Storage schema (Phase 3)

A single additive table — `create_all` builds it on boot; no existing table or column changes.

```
analytics_events
  id           PK
  event        String(64)  index        -- taxonomy name (allow-listed; unknown names dropped)
  user_id      int  index  nullable      -- resolved server-side from trusted X-IH-User-Id (never client)
  anon_id      String(64)  index nullable
  session_id   String(64)  nullable
  props        Text (JSON) nullable       -- small, per-event allow-listed, truncated
  client_ts    String(64)  nullable       -- client-reported ISO (advisory)
  server_ts    String(64)  index          -- authoritative receive time (ISO)
  request_id   String(32)  nullable       -- OBS1 correlation id
  created_at   datetime default _utcnow
```

`POST /api/events` accepts a **batch** (`{events: [...]}`) or a single event, validates each against
the taxonomy allow-list, caps the batch size, truncates props, resolves `user_id` server-side, stamps
`server_ts` + `request_id`, and stores. It is best-effort (never raises into the caller), rides the
existing body-size + rate limits, and needs no user auth for anonymous events — the same posture as
`/api/client-errors`.

---

## Phases 2–6 — implementation

All additive; no recommender / report / ranking / lifecycle / eval / auth / observability / MB1 /
business-logic change.

### Phase 2 — vendor-agnostic client (`web/lib/analytics.ts`)

`track(event, props)` buffers events and flushes to the provider on a batch threshold (20), a 3s
timer, and page-hide / tab-background (`pagehide` + `visibilitychange`, via `sendBeacon` so nothing
is lost on unload). The default `beaconProvider` POSTs `{events:[…]}` to `/api/events`;
`consoleProvider` / `noopProvider` are provided; `setAnalyticsProvider()` swaps in a vendor — no
`track()` call site changes. `anonId` (localStorage) + `sessionId` (sessionStorage) are generated
client-side; everything is SSR-guarded and never throws. **Bound to no vendor** (GA / Mixpanel /
PostHog / Amplitude), exactly as required.

The **13 instrumented events**: `AnalyticsListener` (mounted once in the providers) fires `app_opened`
(once/session), `page_viewed` (per route), and `login_success` / `account_created` on
authentication; the onboarding flow fires `onboarding_started` / `onboarding_step_completed` /
`source_connected`; sign-in fires `signin_started`; `ReadArticleButton` fires `article_read`; the
report page fires `health_report_viewed` (with `mode`); the recommendations page fires
`recommendations_viewed` / `recommendation_opened` / `recommendation_feedback`. Every call is a
best-effort side effect that cannot change what the user sees.

### Phase 3 — backend event pipeline (`POST /api/events`)

`web/app/api/events/route.ts` proxies the batch to the engine, attaching the signed-in user's id via
`engineAuthHeaders()` (anonymous batches carry none). The engine sink
(`examples/api_fastapi.py::analytics_events`) validates each event against the taxonomy allow-list
(`product_analytics.normalize`), **resolves the user id server-side** from the trusted `X-IH-User-Id`
(never the client's claim), stamps the authoritative `server_ts` + correlation `request_id`, caps the
batch at 50, and persists via `store.record_analytics_events`. Structured storage is the additive
`analytics_events` table. Best-effort: unknown events are dropped (counted), a storage failure is
reported through the OBS1 reporter and never fails the caller, and it rides the existing body-size +
rate limits. Batching is supported (the client always posts a batch).

### Phases 4–5 — funnel & product metrics (`examples/product_analytics.py`, pure)

The ten-stage funnel with stage/overall conversion + top drop-off, and the product metrics
(activation rate, measured-activation, time-to-first-report, time-to-measured, engagement/acceptance
rate, D1/D7 retention) are computed **deterministically** from the event rows — identity stitching,
median time-to-value, and cohort retention included. Same rows ⇒ same numbers (verified).

### Phase 6 — developer dashboard (internal-only)

`GET /api/analytics/funnel · /metrics · /retention · /events` — each gated by `_trusted` exactly
like OBS1's `/api/metrics`: **404 to any caller without the internal secret**, so production access is
restricted to the web tier / an operator. Top drop-off ships inside the funnel payload.

### A sample stored event (server-stamped)

```json
{
  "event": "health_report_viewed",
  "userId": 42,               // resolved server-side from X-IH-User-Id (not client-asserted)
  "anonId": "8f3c…",          // anonymous identity (stitches pre-auth → this user at login)
  "sessionId": "a19…",
  "props": { "mode": "measured", "coverage": 0.8 },   // allow-listed, scalar-only, truncated
  "clientTs": "2026-07-20T14:05:01.220Z",
  "serverTs": "2026-07-20T14:05:01.402+00:00",         // authoritative
  "requestId": "6506c450016f"                          // OBS1 correlation
}
```

A funnel read-back over a synthetic cohort (one converting user stitched from anonymous → account,
plus one visitor who bounced at App Opened):

```
stage                      reachers  conv(prev)   topDropOff = app_opened → account_created (−50%)
app_opened                     2         —
account_created                1        0.50
login_success                  1        1.00
source_connected               1        1.00
first_article_read             1        1.00
health_report_generated        1        1.00
measured_report                1        1.00
recommendation_viewed          1        1.00
recommendation_accepted        1        1.00
returned_next_day              1        1.00
# metrics: activation 1.0 · time-to-first-report 240s · time-to-measured 82 680s · D1 retention 0.5
```

## Validation results

| Check | Result |
|---|---|
| `pytest tests/test_product_analytics.py` | **14 passed** (taxonomy/normalize, funnel + stitching, metrics, retention, determinism, store round-trip, sink validation + **server-side identity**, dashboard **internal-only**) |
| Backend full suite `pytest tests/` | **1413 passed**, 2 failed — the 2 are `test_demo_account` failures that **also fail on the pristine pre-PA1 tree** (they need `RWE_DEMO_ACCOUNT` seeding absent in this env); **no PA1 regression** |
| Web `tsc --noEmit` | **clean** |
| Web `node --test` | **96 passed** |
| `check:i18n` | **658 keys × 5 languages** (analytics adds no UI strings) |
| `next build` | **succeeds**; adds `/api/events`; shared JS **87.5 kB** (unchanged), `/report` 377 kB (+1 kB) |
| Playwright e2e (real engine + web) | **12/12 passed**, incl. a **new PA1 end-to-end spec** proving client `track()` → proxy → sink → store → `/api/analytics/funnel` records real reachers |

## Notes, privacy & limitations

- **Pseudonymous by construction.** No PII in `analytics_events`: `anonId` is a random UUID, `userId`
  is the internal engine id, props are a per-event allow-list of truncated scalars — matches the
  published privacy policy.
- **Client-emitted events can be lost** (adblock, hard unload before flush). Acceptable at
  closed-beta scale; the dev dashboard can cross-check volumes against the existing authoritative
  tables (`reads`, `report_snapshots`, `rec_events`, `rec_feedback`) which PA1 leaves untouched. A
  later phase could add server-side emission at those moments if exactness is needed — deliberately
  **not** done here to avoid touching the business logic the brief forbids.
- **`account_created` is approximated client-side** (first authenticated session on a browser, via a
  localStorage flag) since the server-authoritative "is-new-account" signal would require touching the
  auth path. Honest and sufficient for the funnel; documented.
- **No vendor drain yet.** Events live in `analytics_events` behind the internal dashboard; the
  provider seam (`setAnalyticsProvider` / a future backend forwarder) is in place to drain into a
  vendor later — the same "seams in place, no dependency yet" posture as OBS1.
- **Rate limits apply** to `/api/events` (batching keeps requests low); an abusive client is throttled
  like any other, and the sink is best-effort so a dropped batch is silently fine.

---

*PA1 measures activation and engagement only — additive, best-effort, behavior-preserving. No
recommendation engine, ranking, lifecycle, evaluation, report calculation, authentication,
observability, mobile, or business-logic change.*
