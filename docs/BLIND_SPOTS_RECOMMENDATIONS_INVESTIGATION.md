# Investigation — "Blind spots" & "Recommendations for improvement"

**Question:** how are the two Health-Report sections — **Blind spots** and **Recommendations for
improvement** — generated, and are they real-time, per-user analysis of each reader's behaviour?

**Read-only investigation — no code changed.** Every conclusion is cited to the exact file/line so it
is independently verifiable. A confidence level is attached to each finding.

---

## TL;DR

| | Blind spots | Recommendations for improvement |
|---|---|---|
| **Per-user?** | **Yes — fully.** Derived from *this reader's* category distribution vs the catalog. | **Selection: yes. Wording: no.** The 3 shown are this reader's 3 lowest metrics; their text is fixed. |
| **How generated** | Computed: catalog category share − reader category share, filtered & ranked. | Rule: rank the reader's available metrics, take the lowest 3, look each up in a static table. |
| **Templated / hardcoded?** | Topic + gap% are computed; the sentence is an f-string with the catalog % interpolated. | **Title, detail, and the `+N` impact are 100% static constants** keyed by metric. |
| **LLM?** | No. | No. |
| **Impact score (+5) computed?** | — | **No — a fixed integer** in the code (`_IMPROVEMENTS`). Not calculated, not a prediction. |
| **Real-time?** | **Yes** — a new read changes the reader's model version and both sections recompute on the next report request. | **Selection is real-time; the wording/impact never change.** |
| **Cached?** | Yes — per user, keyed by `(reading_version, reception_version)`; recomputed when reads or cross-cutting reception change, otherwise served from cache. | Same model cache (they are built together in one serializer). |

**One clarification up front:** the Health Report's *"Recommendations for improvement"* is **not** the
RWE recommendation feed (`/api/me/recommendations`, the article cards). It is the report's
`improvements` array — a *behaviour-change tip per weak metric*, built by the same serializer that
builds the metrics. Different subsystem, different code path. This document is about the report
`improvements`, per the screenshot.

---

## 1. Execution flow diagram

```
┌─ FRONTEND ───────────────────────────────────────────────────────────────────┐
│ app/(app)/report/page.tsx                                                      │
│   useReport()                              hooks/use-data.ts:26                 │
│     └─ useQuery({ queryFn: services.report })                                  │
│          services.report()                 services/index.ts:53                 │
│            └─ getJson<HealthReport>("/report")                                 │
│   renders  <BlindSpots  items={report.blindSpots}  />   report-widgets.tsx:15  │
│            <Improvements items={report.improvements} />  report-widgets.tsx:43  │
│            (both are PURE renderers — no compute, no fetch)                     │
└───────────────────────────────┬───────────────────────────────────────────────┘
                                 │  GET /report   (same-origin, browser → Next)
┌─ WEB PROXY (Next route handler) ▼──────────────────────────────────────────────┐
│ app/api/report/route.ts                                                        │
│   export const dynamic = "force-dynamic"      ← never cached at the edge        │
│   backendGet<MeasuredHealthReport>("/api/report", engineAuthHeaders())         │
│   → returns the engine JSON VERBATIM (dev: mock; prod-down: 503, no fabrication)│
└───────────────────────────────┬───────────────────────────────────────────────┘
                                 │  GET /api/report   (server → engine, internal secret)
┌─ ENGINE (FastAPI) ▼────────────────────────────────────────────────────────────┐
│ api_fastapi.py:1435  @app.get("/api/report")                                   │
│   → _report_for(active, request, user)          api_fastapi.py:1450             │
│        uid = _real_uid(request)                                                │
│        ├─ has_measured(uid)? ─ YES → personalizer.report(uid)     ┌ MEASURED    │
│        ├─ onboarding outlets? ─ YES → backend.estimate(outlets)   ┌ ESTIMATE    │
│        └─ else                       → demo / reference report    ┌ DEMO        │
└───────────────────────────────┬───────────────────────────────────────────────┘
                                 ▼
   MEASURED                                        ESTIMATE
   personalize.py:282 report(uid)                  api_server.py estimate(outlets)
     _model(uid)  ── cache ──┐                       (outlet-catalog aggregate; 0 reads)
     personalize.py:249      │                        blindSpots  ← catalog share vs
       key (reading_version, │                          the picked outlets' topic mix
            reception_version)│                        improvements ← 3 lowest metrics
       miss → _build_model    │                                     × _IMPROVEMENTS table
       personalize.py:197 ────┘
         └─ backend._serialize_report(corpus, row)   api_server.py:972
              rep = health_report.user_report(...)   api_server.py:980
              blindSpots   ← rep["blind_spots"]  (lines 1023-1028)
              improvements ← 3 lowest metrics × _IMPROVEMENTS (lines 1030-1039)
```

---

## 2. Backend call graph

```
GET /api/report                                              api_fastapi.py:1435
└─ _report_for(active, request, user)                        api_fastapi.py:1450
   ├─ _real_uid(request)                     → reader id (or None = anon/demo)
   ├─ personalizer.has_measured(uid)         → measured vs estimate branch
   │
   ├─[MEASURED]  personalize.report(uid)                     personalize.py:282
   │   └─ _model(uid)                                        personalize.py:249
   │       │  version   = store.count_reads(uid)      ← READING VERSION
   │       │  reception = _reception_key(uid)         ← cross-cutting reception
   │       │  cache hit iff both unchanged → return cached model
   │       └─ _build_model(uid, version, reception)          personalize.py:197
   │            ├─ store.get_reads(uid) → reconstruct reads
   │            ├─ augment reference corpus with the reader
   │            ├─ health_report.compute(...)          (unchanged RWE engine)
   │            └─ save_report(snapshot)  ← persisted per version (/api/me history)
   │   └─ backend._serialize_report(m.corpus, m.reader_row)  api_server.py:972
   │        ├─ rep = health_report.user_report(pop, mind, u) api_server.py:980
   │        │    └─ blind_spots = gaps[:2]                    health_report.py:382,418
   │        │         p_c = shares(UC[u])   ← reader category shares
   │        │         q_c = pop["catalog_cat_share"] ← catalog category shares
   │        │         keep cat where q_c>0.02 AND p_c < 0.5*q_c, rank by (q_c−p_c)
   │        ├─ blindSpots  ← format rep["blind_spots"]        api_server.py:1023-1028
   │        └─ improvements ← sort available metrics by score,
   │                          take lowest 3, map via _IMPROVEMENTS api_server.py:1030-1039
   │
   └─[ESTIMATE]  backend.estimate(outlets)                    api_server.py (builder)
        ├─ blindSpots  ← catalog_cat_share vs picked outlets' topic mix  :1188-1195
        └─ improvements ← lowest 3 available metrics × _IMPROVEMENTS      :1197-1202

STATIC DATA:  _IMPROVEMENTS  (title, detail, impact) per metric           api_server.py:75-97
```

---

## 3. Data sources

| Datum | Source | Origin |
|---|---|---|
| **Reader category shares** `p_c` | `shares(UC[u])` — row `u` of the user×category click matrix | The reader's **actual reads** (measured) / the reader's chosen outlets' catalog mix (estimate). |
| **Catalog category shares** `q_c` | `pop["catalog_cat_share"]` | The reference corpus — what topics are *available*, independent of the reader. |
| **Blind-spot gap %** | `(cat_share − user_share) / cat_share` | Computed at serialize time; clamped to [0,1]. |
| **Blind-spot sentence** | f-string in `_serialize_report` | Template; only the catalog-share % (`round(cat_share*100)`) is interpolated. |
| **Metric scores** (drive which improvements show) | `rep["scores"]` from `user_report` | The unchanged RWE engine over the reader's augmented corpus. |
| **Improvement title / detail / impact** | `_IMPROVEMENTS[metric]` | **Hardcoded constants** in `api_server.py:75-97`. Never computed. |
| **"Helps *Metric*" label** | `t("rec.helps", {metric})` client-side | The metric name (dynamic), localised in the browser. |
| **"Add to goals" state** | `React.useState<Set>` in `Improvements` | **Client-only, in-memory.** No API call; lost on reload. |

---

## 4. Blind spots — findings

**Dynamically computed per user? — YES.** *(Confidence: High)*
`health_report.user_report` (`health_report.py:373-418`) computes, for reader row `u`:
```python
p_c = shares(UC[u])                      # this reader's category distribution
q_c = pop["catalog_cat_share"]           # the catalog's category distribution
gaps = sorted(((cat_u[i], p_c[i], q_c[i]) for i in range(len(cat_u))
               if q_c[i] > 0.02 and p_c[i] < 0.5 * q_c[i]),
              key=lambda x: -(x[2] - x[1]))
...
return dict(..., blind_spots=gaps[:2], ...)
```
A category is a blind spot when it is **≥2 % of the catalog** *and* the reader consumes **less than half**
its catalog share. The **top 2 by absolute under-consumption** are returned. Two readers with different
reading histories get different blind spots — it is a per-reader computation off `UC[u]`.

**Inputs.** The reader's category-click distribution (`UC[u]`) and the catalog category distribution
(`catalog_cat_share`). Nothing else.

**Recomputed every request? Cached? — Cached per model, recomputed on read change.** *(Confidence: High)*
`_serialize_report` runs inside the cached measured model. `_model` (`personalize.py:249`) keys the cache
on `(reading_version = count_reads(uid), reception_version)`. If the reader's read count is unchanged the
report — blind spots included — is served from the cached model; a **new read** bumps `reading_version`,
misses the cache, and `_build_model` recomputes the whole augmented population, so the blind spots refresh.

**Hardcoded / templated? — Computed values, templated sentence.** *(Confidence: High)*
The topic and the gap % are computed. The prose is a fixed template
(`api_server.py:1027-1028`):
> `f"{topic} is {round(cat_share*100)}% of what's available, but barely shows up in your reading."`

Only the topic name and the catalog % vary; the sentence structure is constant. **It is English-only** —
this string bypasses the i18n system (rendered raw at `report-widgets.tsx:34`).

**Estimate path** (`api_server.py:1188-1195`) is analogous but compares `catalog_cat_share` against the
**picked outlets'** aggregated topic mix (0 reads), with a different template ("…but light in the outlets
you picked.").

---

## 5. Recommendations for improvement — findings

**Personalised, rule-based, templated, or LLM? — Rule-based selection + static template. Not LLM.**
*(Confidence: High)*
`_serialize_report` (`api_server.py:1030-1039`):
```python
ranked = sorted((m for m in metrics if m["key"] != "confidence" and m["available"]),
                key=lambda m: m["score"])
improvements = []
for m in ranked[:3]:
    tpl = _IMPROVEMENTS.get(m["key"])
    if tpl:
        improvements.append({"id": f"imp_{m['key']}", "title": tpl[0], "detail": tpl[1],
                             "metric": m["key"], "impact": tpl[2]})
```
The **only** dynamic decision is *which* three cards appear: the reader's **three lowest-scoring available
metrics**. Everything shown on a card — title, detail, impact — is read verbatim from a static table.

**The static table** (`api_server.py:75-97`, `_IMPROVEMENTS`), one entry per metric:

| Metric key | Title | Impact `+N` |
|---|---|---|
| viewpointBalance | "Add two cross-cutting reads a week" | **8** |
| emotionalBalance | "Trade one charged read a day for analysis" | **6** |
| sourceDiversity | "Broaden beyond your top outlets" | **5** |
| echoChamber | "Hear the other side on a contested topic" | **5** |
| topicDiversity | "Widen the range of subjects you read" | **4** |
| reportingRatio | "Anchor opinion with reporting" | **4** |
| openMindedness | "Click the cross-cutting reads we surface" | **5** |

**Are the impact scores (+5) calculated or fixed? — FIXED.** *(Confidence: High)*
`impact` is `tpl[2]` — the third element of the constant tuple above. It is a **hand-authored integer**,
not a model output, not a projected score delta, not a function of the reader's data. The screenshot's
"+5 / +4 / +5" are literally these constants for the three metrics that happened to be the reader's
lowest. The same metric always shows the same `+N` for every reader, forever.

**Deterministic? — Yes.** *(Confidence: High)* Given a reader's metric scores, the selection (sort by
score, take 3) and the text (table lookup) are fully deterministic. No randomness, no sampling, no
generation.

**Estimate path** (`api_server.py:1197-1202`) uses the identical `_IMPROVEMENTS` table and the same
"lowest 3 available metrics" rule.

**"Add to goals" is cosmetic.** *(Confidence: High)* `report-widgets.tsx:45-51` — a local
`useState<Set<string>>` toggled on click. No network request, no persistence; the state is gone on
refresh. It does not feed back into scoring or recommendations.

---

## 6. Personalization analysis

| Aspect | Blind spots | Recommendations |
|---|---|---|
| **User-specific vs generic** | User-specific (from `UC[u]`). | Selection user-specific; content generic per metric. |
| **What varies between users** | Which topics, and the gap %. | Which 3 metrics appear (and their order). |
| **What is identical for everyone** | The sentence template. | Every card's title, detail, and `+N` — fixed per metric. |
| **Localised?** | No — English template rendered raw. | Title/detail English (raw); only the "Helps *X*" chip is localised. |
| **Model / heuristic** | Deterministic heuristic (share comparison, top-2). | Deterministic heuristic (rank metrics, top-3) + lookup table. |

**Net:** blind spots are genuinely a per-reader read of behaviour; the recommendations are a per-reader
*triage* (which weak areas to surface) wrapped around **generic, pre-written advice with fixed "impact"
numbers**. Neither uses an LLM or any learned/predictive impact model.

---

## 7. Real-time behaviour

**Does a new read immediately affect these? — YES for blind spots and for *which* recommendations show;
the recommendation wording/impact never change.** *(Confidence: High)*

The chain that makes it real-time:
1. A read is recorded → `store.count_reads(uid)` increases.
2. Next `/api/report`: `_model(uid)` computes `version = count_reads(uid)` (`personalize.py:253`), which
   no longer matches the cached model → **cache miss**.
3. `_build_model` rebuilds the augmented corpus and metrics; `_serialize_report` recomputes blind spots
   (new `UC[u]`) and re-ranks the lowest-3 metrics.
4. The web proxy is `force-dynamic` and React Query's `useReport` refetches, so the browser shows the new
   result on the next load — no manual cache-bust needed.

**Background jobs? — None.** *(Confidence: High)* Both sections are computed **synchronously inside the
request** (through the model cache). There is no scheduler, worker, or async recompute; the "recompute"
is lazy, triggered by the version mismatch on the next request.

**Persisted intermediate state? — A snapshot, not the serving source.** *(Confidence: High)*
`_build_model` calls `store.save_report(...)` (`personalize.py:243`) to persist the report per version for
`/api/me` history. But the *served* report is computed live from the in-memory cached model, not read back
from that snapshot.

**Caching precisely.** *(Confidence: High)* One cached model per user (latest version only), keyed by
`(reading_version, reception_version)` (`personalize.py:255-262`). Unchanged reads → served from cache
(not recomputed each request). Changed reads **or** changed cross-cutting-recommendation reception →
rebuild. `invalidate(uid)` (`personalize.py:264`) can drop the entry eagerly, but the version check alone
guarantees freshness.

---

## Confidence summary

| # | Finding | Confidence |
|---|---|---|
| 1 | Frontend components are pure renderers of `report.blindSpots` / `report.improvements`; "Add to goals" is client-only, unpersisted. | **High** |
| 2 | `/report` → `force-dynamic` proxy → `GET /api/report` → `_report_for` (measured / estimate / demo). | **High** |
| 3 | Blind spots computed per-user from `UC[u]` vs `catalog_cat_share` (≥2 % catalog, <½ share, top 2). | **High** |
| 4 | Blind-spot sentence is a fixed template (English, un-localised). | **High** |
| 5 | Recommendations = the reader's 3 lowest available metrics (dynamic selection). | **High** |
| 6 | Recommendation title/detail/impact are static constants in `_IMPROVEMENTS`; **impact `+N` is fixed, not calculated**. | **High** |
| 7 | Neither section uses an LLM; both are deterministic. | **High** |
| 8 | Both recompute on a new read (model cache keyed by `reading_version`); no background job; synchronous/lazy. | **High** |
| 9 | Served report is computed live from the cached model; the persisted snapshot feeds `/api/me` history, not serving. | **High** |
| 10 | The report's "Recommendations for improvement" is the `improvements` array, **distinct** from the `/api/me/recommendations` article feed. | **High** |

---

*No code was modified during this investigation.*
