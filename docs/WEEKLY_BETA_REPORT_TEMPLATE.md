# Weekly Beta Report — Week &lt;N&gt; (&lt;date range&gt;)

Reusable template. Copy to `docs/beta-reports/week-<N>.md` and fill from the shipped instruments:
**PA1** (`/api/analytics/funnel|metrics|retention|events`) · **OBS1** (`/api/metrics`, structured logs,
`/api/health/*`, `/api/internal/{storage,feeds}`) · the Wave 0 feedback grid (`WAVE0_SUCCESS_PLAN.md` §3)
· the bug list (§4). Internal endpoints need `-H "X-IH-Auth:$SECRET"`. Keep it to one page of substance;
replace every `<…>`. Documentation only — reporting reads the product, it never changes it.

> **Snapshot:** Cohort size `<N>` · Wave `<0/1/…>` · Reporting period `<YYYY-MM-DD → YYYY-MM-DD>` ·
> Author `<name>` · Overall status **`<GREEN / YELLOW / RED>`**

---

## 1 · Executive Summary
*3–5 sentences: what we learned, what changed, and the one decision that matters this week.*
- **Headline learning:** `<the single most important thing we now know>`
- **Funnel one-liner:** `<X of N reached a report; top drop-off at <stage>>`
- **This week's decision:** `<expand to <M> / hold / fix <X>>` (detail in §8)
- **Status rationale:** `<why GREEN/YELLOW/RED>`

## 2 · Product Health
*Is the user journey working?* Fill funnel from `/api/analytics/funnel`.

| Funnel stage | Reachers | of N | Δ vs last week | Target | Note |
|---|---:|---:|---:|---:|---|
| App opened | | | | | |
| Onboarding / source connected | | | | | |
| Signed in | | | | | |
| First article read | | | | | |
| Health Report generated | | | | | |
| **Measured Mode** | | | | | |
| Recommendation viewed | | | | | |
| Recommendation accepted | | | | | |
| Returned next day | | | | | |

- **Top drop-off** (`topDropOff`): `<from → to, −X%>` — *hypothesis:* `<why>`
- **Mobile/browser issues observed:** `<none / …>`

## 3 · Operational Health
*Is the system healthy?* From `/api/metrics`, logs, `/api/health/*`, `/api/internal/{storage,feeds}`.

| Signal | This week | Budget / expected | Status |
|---|---|---|---|
| Uptime (readiness 200) | `<%/incidents>` | ~100% | |
| 5xx / unhandled_exception | `<count>` | ~0 | |
| `request_ms` p95 | `<ms>` | `<baseline×2>` | |
| `report_generate_ms` p95 | `<ms>` | `<baseline>` | |
| **`db_query_ms` p95** (contention watch) | `<ms>` | flat vs baseline | |
| `rate_limited` events | `<count>` | rare | |
| `client_error` count | `<count>` | low | |
| Feeds healthy / stale | `<h/s>` | healthy, fresh | |
| Data integrity (quick_check) | `<ok?>` | ok | |
| Disk headroom | `<%>` | ample | |
| **Backups:** taken / verified / off-host | `<n / pass / yes>` | daily, verified | |
| Restore drill this week | `<done? RTO>` | rehearsed | |

## 4 · Analytics Summary
*Are we learning?* From `/api/analytics/metrics` + `/retention`.

| Metric | Value | Prior | Note |
|---|---|---|---|
| Identities / accounts created | | | |
| Activation rate (reached report) | | | |
| Measured-activation rate | | | |
| Time to first report (median) | | | |
| Time to Measured (median) | | | |
| Day-1 retention | | | |
| Day-7 retention | | | (emerges as weeks accrue) |
| Event volume (`/analytics/events` total) | | | pipeline health |

- **Instrument confidence:** `<funnel populating for real users? any gaps vs authoritative tables?>`

## 5 · Recommendation Performance
*Do users engage with what RC2 built?* From `/api/analytics/metrics` + qual.

| Metric | Value | Prior | Note |
|---|---|---|---|
| Recommendation engagement rate | | | opened or any feedback ÷ viewed |
| Recommendation acceptance rate | | | opened or like/read-later ÷ viewed |
| Feedback mix (like/dislike/ignore/read-later) | | | from `recommendation_feedback` events |

- **Relevance signal (qual):** `<what users said about relevance / the "Why?" evidence>`

## 6 · Top User Feedback
*Qualitative learning — the point of the beta.* From the feedback grid; tag Theme + Type.

| # | Theme | Type (Blocker/Friction/Delight/Suggestion) | Freq (/N) | Verbatim quote | Links to hypothesis |
|---|---|---|---:|---|---|
| 1 | | | | "`<quote>`" | `<H1–H5>` |
| 2 | | | | | |
| 3 | | | | | |

- **Protect this delight:** `<the thing ≥2 users loved>`
- **Kill this friction:** `<the thing ≥2 users tripped on>`

## 7 · Top Bugs
From the triage list; include the OBS1 `requestId` for correlation.

| ID | Sev (P0–P3) | Summary | Users hit | Status | requestId / repro |
|---|---|---|---:|---|---|
| | | | | Open/Fixed/Won't-fix | |

- **Rollout impact:** `<paused? rolled back? none>`

## 8 · Decisions Made
*Explicit, data-backed.*

| Decision | Evidence it's based on | Owner | Date |
|---|---|---|---|
| `<expand 5→15 / hold / fix X>` | `<funnel/metric/feedback cited>` | | |

## 9 · Open Risks
*What could bite us — with a trigger + mitigation.*

| Risk | Likelihood | Impact | Trigger to watch | Mitigation / owner |
|---|---|---|---|---|
| SQLite write contention (write-on-read + analytics) | | | `db_query_ms` p95 rising | load smoke; throttle wave; (post-beta scaling) |
| Single-host / single DB | | | volume/host loss | off-host backups + restore drill |
| Client-analytics loss | | | funnel < authoritative tables | cross-check `reads`/`rec_events` |
| `<other>` | | | | |

## 10 · Next-Week Priorities
*≤5, ranked, each with a done-signal. No new features — learning + operations only.*

1. `<priority>` — done when `<measurable signal>`
2. `<priority>` — done when `<…>`
3. `<…>`

---

*Instructions: this template reads the product through PA1 + OBS1 and the beta program artifacts. It is a
reporting document only — filling it in changes no application code.*
