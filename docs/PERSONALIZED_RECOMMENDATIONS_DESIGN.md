# Design — Personalized Improvement Recommendations (next-generation)

**Objective:** evolve the Health Report's *"Recommendations for improvement"* from
**static-template advice with fixed impact constants** into **recommendations synthesised from each
reader's own behaviour**, while preserving the three properties the current system gets right:
**determinism, explainability, and testability.**

**This is a design document. No code is implemented.** Every proposal is grounded in the code that
exists today (cited inline) so the roadmap is buildable, not aspirational.

**Scope note.** "Recommendations for improvement" = the report's `improvements` array (a
behaviour-change tip per weak metric). It is **not** the `/api/me/recommendations` article feed. This
design changes only the `improvements` builder and the evidence it emits.

---

## 0. Design principles (inherited, non-negotiable)

The recommendation-explainability work already established the house rule and we keep it:

> **Every explanation shown must be traceable to real evidence produced by the engine — never
> inferred from templates or placeholder values.** (`rec_explain.py:4-6`)

Four invariants the new engine must satisfy, each already true of some subsystem today:

| Invariant | Precedent in the codebase |
|---|---|
| **Deterministic** — same stored state + same corpus snapshot → identical output, byte-for-byte. | `rec_pipeline` determinism stage; report is a pure function of the cached model. |
| **Explainable** — no opaque score; trigger, evidence, action, and benefit are all exposed. | `rec_explain` evidence contract (match band, shares, connectivity). |
| **Testable** — golden fixtures pin the output for fixed inputs. | 9 scenario golden fixtures in `rec_pipeline`; metric-pipeline goldens. |
| **Never fabricate** — if a signal is missing or thin, say so; don't invent a number. | Metric empty-state (`available:false`); estimate omits Open-Mindedness. |

---

## 1. Current architecture

```
 Reading History                      reads table (store.py:250) — scored JSON per read
        │                             (idempotent per user+url; carries opened_from, read_source)
        ▼
 Metric Calculation                   personalize._build_model → health_report.compute (RWE engine)
        │                             each metric = PERCENTILE of the reader's raw value vs the
        │                             reference population   (_pct_vs_pop, api_server.py:1084)
        ▼
 Weakest Metrics                       sort available metrics by score asc, take lowest 3
        │                             (api_server.py:1030-1032)
        ▼
 Template Selection                    _IMPROVEMENTS[metric] → (title, detail, impact)
        │                             STATIC dict, fixed +N impact  (api_server.py:75-97)
        ▼
 Health Report                         report.improvements[]  → <Improvements> (pure render)
                                       report-widgets.tsx:43
```

**What's already personalised:** *which* three metrics appear (the reader's three lowest).
**What's static:** every word on the card, and the `+N` impact (a hand-authored constant).

**What's already computed and available but unused by the improvement cards** — this is the key
opportunity. The very same `_serialize_report` that builds `improvements` *also* builds, from the
reader's real data:

- `report.sources[]` — per-outlet `{share, count, lean}`, top 9 (api_server.py:1007-1012).
- `report.topics[]` — per-category `{share, count}`, top 10 (api_server.py:1003-1006).
- `report.blindSpots[]` — under-consumed categories with a computed gap (api_server.py:1023-1028).
- `report.viewpoint` — `{left, center, right}` mix; `report.attention` — emotion mix.
- Each metric's `score` (percentile) and `benchmark` (population median = 50; confidence 70).

The redesign is largely **composing evidence the engine already produces** into the recommendation,
plus a **dynamic impact estimator** to replace the constant.

---

## 2. Proposed architecture

A five-stage pipeline that replaces *"Template Selection"* with *"Signal analysis → Rule firing →
Evidence binding → Impact estimation → Rendering"*. It runs **inside the existing cached model** (no
new serving path, no new cache tier).

```
                 ┌─────────────────────────────────────────────────────────────┐
                 │  CACHED MEASURED MODEL  personalize._model (store.py-keyed)  │
                 │  key = (reading_version, reception_version)                  │
                 └─────────────────────────────────────────────────────────────┘
                                          │  built once per version
        ┌─────────────────────────────────┼─────────────────────────────────────┐
        ▼                                 ▼                                       ▼
 (A) SIGNAL EXTRACTION           (B) RULE ENGINE                        (C) EVIDENCE BINDER
 pull the reader's already-      deterministic condition set,           for each fired rule, attach the
 computed signals into one       one rule per improvement "kind";       exact numbers that triggered it
 typed ReaderSignals struct      a rule fires iff its guard is true      (shares, counts, gap, deficit)
 (metrics, shares, blindSpots,   (e.g. sourceDiversity below median      — pulled from ReaderSignals,
 viewpoint, attention, reads,    AND top-2 outlets > 60% of reads)       never re-derived
 saved, rec reception, feedback)          │                                       │
        └─────────────────────────────────┼───────────────────────────────────────┘
                                          ▼
                              (D) IMPACT ESTIMATOR
                              per fired rule, estimate the metric gain of the suggested action
                              (simulated marginal read → recompute percentile; see §5)
                                          │
                                          ▼
                              (E) SELECT + RANK + RENDER
                              rank fired rules by (estimated impact, then metric deficit);
                              take top N (default 3); emit a structured Recommendation with
                              trigger / evidence / action / benefit  (see §6)
                                          │
                                          ▼
                              report.improvements[]  (richer contract; back-compatible superset)
```

**Determinism is structural**, not a promise: stages A–E are pure functions of `(ReaderSignals,
corpus snapshot)`. `ReaderSignals` is itself a pure projection of stored rows + the frozen corpus.
Ranking ties break on the metric key (a stable string). No randomness, no sampling, no clock, no LLM.

**Rules replace templates, but a rule is not a template.** A rule owns: a **guard** (a boolean over
signals), an **evidence selector** (which numbers to bind), an **action generator** (what concrete
step, computed from evidence — e.g. *which* outlet to add), and an **impact hook** (which simulator to
call). The *sentence* is still a localised format string, but every slot is a bound number or a
data-selected entity — the difference from today is that titles, details, actions, and impact are all
**functions of the reader's data**, not constants.

---

## 3. Recommendation inputs (signal inventory)

Every signal that could feed a rule, with its **current availability** (is it stored/computed today),
**reliability** (how trustworthy/dense is it), and **usefulness** (what it can drive). Availability is
cited to code.

| Signal | Source | Availability | Reliability | Usefulness |
|---|---|---|---|---|
| **Reading history** (per-read scored JSON, `opened_from`, `read_source`) | `reads` table (store.py:250-271) | **Available now.** | High — verbatim scored fields; idempotent per URL (no double-count). Repeated reads don't stack (store.py:251). | The substrate for everything. `opened_from` tells you *where* a read came from (recommendations/discover/search/saved). |
| **Topic distribution** (per-category share + count) | `report.topics` (api_server.py:1003-1006) | **Available now** (in every measured report). | High — real click shares. Thin for very-low-read users (guard on `count_reads`). | Drives topicDiversity + blind-spot phrasing ("you circle 3 topics"). |
| **Publisher diversity** (per-outlet share, count, lean) | `report.sources` (api_server.py:1007-1012) | **Available now.** | High — real shares; lean from outlet registry. | Drives the flagship example: "82% from Reuters & BBC." Names concrete outlets. |
| **Viewpoint balance** (left/center/right mix) | `report.viewpoint` (api_server.py:1018-1019) | **Available now.** | Medium — needs political reads; NaN-guarded to zeros for low-political readers. | Drives viewpointBalance + echoChamber actions; supplies the "which side" for cross-cutting suggestions. |
| **Emotional exposure** (fear/outrage/analysis/…) | `report.attention` (api_server.py:1020-1021) | **Available now.** | Medium — model-derived emotion labels; defaults to uniform when absent. | Drives emotionalBalance ("38% of your reads lean fear/outrage"). |
| **Reporting vs opinion ratio** | `reportingRatio` metric raw (`register`) | **Available now** as a score; **raw ratio not surfaced** on the card. | Medium — register classifier. | Drives reportingRatio action; needs the raw ratio exposed to phrase it. |
| **Blind spots** (under-consumed categories + gap) | `report.blindSpots` (api_server.py:1023-1028) | **Available now.** | High — computed (catalog share vs reader share). | Directly a recommendation trigger ("Economy is 9% of the catalog, ~0% of your reading"). |
| **Recommendation reception** (shown/opened, cross_cutting) | `rec_events` (store.py:292-310); `recommendation_reception()` (store.py:1189) | **Available now.** | High for the counts; drives Open-Mindedness gate today. | Drives openMindedness action ("we surfaced 12 cross-cutting reads; you opened 1"). |
| **Recommendation feedback** (like/dislike/ignore/read_later) | `rec_feedback` (store.py:318-336) | **Stored, but RECORDED-ONLY — no consumer reads it** (store.py:323-325). | Sparse early; explicit and high-signal when present. | *Preference / suppression* signal: dislike → down-weight an action; read_later → the reader intends to act. **Wiring it in is a governance step (see §8 Phase 2).** |
| **Saved articles** | `saved_articles` (store.py:339-353); `count_saved` (store.py:1137) | **Available now**, but self-contained — touches no recommender/report today (store.py:344). | Medium — explicit intent, but saving ≠ reading. | Weak positive intent; can seed "you saved 3 Economy pieces but read 0 — start with one." |
| **Reading streak** | derived from `reads` timestamps (dashboard summary already computes `streakDays`) | **Available now** (surfaced in sidebar/dashboard). | Medium — timezone-sensitive; a behavioural nudge, not a diet signal. | Tone/urgency only ("keep your 6-day streak going with…"). **Not** an impact input. |
| **Reading frequency** (reads/week, recency) | `reads.created_at` / `observed_at` (store.py:263,271) | **Derivable now** (not currently aggregated). | Medium. | Gates *whether* to recommend (a dormant reader needs re-engagement, not diet tuning) and calibrates realism ("one read this week"). |
| **Report snapshots** (past reports per version) | `report_snapshots` (store.py:224-237); `list_report_snapshots` (store.py:576) | **Available now** — persisted every model rebuild (personalize.py:243). | Grows with tenure; empty for new users (cold start). | **Historical calibration** for impact (§5): how much did *this* reader's metric actually move per unit of the behaviour last time. |

**Availability summary:** 9 of 12 signals are **already computed and in-hand** at the point
`improvements` is built. `rec_feedback` needs a governance decision to consume; frequency/recency need
a small aggregation; snapshots need a calibration pass. **Nothing requires new data collection for
Phase 1.**

---

## 4. Personalized recommendation generation

Each recommendation is generated by a **rule**, and each rule emits the four required parts. Contrast:

> **Today:** `"Broaden beyond your top outlets."` — same string for every reader.
>
> **Proposed:** *"82% of your reading comes from Reuters and BBC. Reading one article this week from
> AP or Al Jazeera would improve your Source Diversity (est. +4–6)."*

Every element of that sentence is bound from evidence the engine already has:

| Part | Bound from | Code source today |
|---|---|---|
| `82%` (trigger) | sum of top-2 `report.sources[].share` | api_server.py:1007-1012 |
| `Reuters and BBC` | the names of those top-2 sources | `report.sources[].source` |
| `AP or Al Jazeera` (action) | catalog outlets **near the reader's under-represented lean**, not in their top set | outlet registry + `sources[].lean` |
| `one article this week` | realism-scaled to reading frequency | `reads.created_at` aggregation |
| `Source Diversity` | the metric the rule targets | `_METRIC_KEYS` |
| `+4–6` (benefit) | simulated marginal-read percentile delta (§5) | new estimator |

**Rule catalogue (initial set — one per improvable metric, mirrors `_IMPROVEMENTS` keys so it is a
drop-in superset):**

| Rule | Guard (fires when…) | Action generator (concrete step) |
|---|---|---|
| `source_concentration` → sourceDiversity | score < median **and** top-2 outlet share > 60% | name 1–2 catalog outlets outside the reader's top set, near their thin lean |
| `topic_narrowness` → topicDiversity | score < median **and** effective #topics small | name the strongest blind-spot category to add |
| `one_sided` → viewpointBalance / echoChamber | viewpoint mix skewed > X to one side | suggest a specific opposite-but-centre-adjacent outlet/topic |
| `charged_diet` → emotionalBalance | fear+outrage attention share > threshold | suggest swapping one charged read for analysis register |
| `opinion_heavy` → reportingRatio | reporting share < threshold | suggest pairing commentary with a straight-reporting source |
| `unreceptive` → openMindedness | cross-cutting shown ≫ opened (rec reception) | suggest opening the cross-cutting reads already surfaced |
| `blind_spot` → topicDiversity | a `blindSpots[]` entry exists with gap > threshold | name that category + a catalog source that covers it |

Guards read only from `ReaderSignals`; thresholds are **named constants in one place** (testable,
tunable), not scattered. A rule that can't bind a concrete action (e.g. no suitable alternate outlet
exists in the catalog) **does not fire** — we never suggest an action we can't ground.

**Cold-start / thin-data behaviour (honesty first):** below the measured threshold the report is an
Estimate; there, rules that need read history (source concentration, reception) can't bind, so they
fall back to the **current behaviour** — the metric-keyed generic tip — clearly the estimate's job.
For a measured-but-sparse reader, a rule with an unstable signal emits its generic form and **omits the
numeric impact** rather than showing false precision (same discipline as the metric empty-state).

---

## 5. Impact estimation

Replace the fixed `+N` with an estimated metric gain. Four candidate approaches; the crucial fact that
makes principled estimation possible is that **a metric score is a percentile of the reader's raw value
against the reference population** (`_pct_vs_pop`, api_server.py:1084) — so "impact" has a precise
meaning: *how many percentile points the score moves if the reader takes the action.*

| Approach | How | Determinism | Cost | Honesty / accuracy | Verdict |
|---|---|---|---|---|---|
| **(1) Metric deficit** | impact ∝ `benchmark − score` (distance below the population median). | Pure. | ~0. | Cheap proxy; **not** a prediction of the action's effect — just "how far below typical." Can mislead (a big deficit doesn't mean one read fixes it). | **Fallback** where simulation isn't cheap. |
| **(2) Simulated score change (counterfactual)** | Add the hypothetical read(s) to the reader's own distribution, **recompute the raw metric and its percentile**, report Δ. | Pure (deterministic perturbation). | Low–medium: for **distribution metrics** (topic/source diversity, reporting, emotional, viewpoint mix) the raw value is a closed-form function of the reader's shares → recomputing after +1 read is O(categories/outlets), cheap. For **graph metrics** (echoChamber, openMindedness) it needs the walk/graph → expensive. | Highest — it answers exactly "what does *this* action do to *this* score." Naturally yields a small, believable number. | **Primary for distribution metrics (RC2).** |
| **(3) Historical improvement** | From `report_snapshots`, regress this reader's past metric deltas against the behaviour they changed; use the slope as impact. | Pure given snapshots. | Low at serve (coeffs precomputed on rebuild). | Personalised and empirical — but **cold-starts empty** and is confounded (many things change between snapshots). | **Phase 2 calibration** layered on (2). |
| **(4) Confidence intervals** | Instead of a point, emit a **range** (e.g. +4–6) reflecting estimation uncertainty (few reads → wider). | Pure. | ~0 on top of (2). | Most honest — refuses false precision when data is thin. UI cost: a range, not a badge number. | **Adopt as the presentation of (2)/(3).** |

**Recommended design:**
- **RC2:** approach **(2) for the five distribution metrics**, computed live via a marginal-read
  perturbation of the reader's shares, presented as a **band** per (4) (e.g. `+4–6`); approach **(1)
  metric-deficit banding as the fallback** for echoChamber/openMindedness (graph metrics), where a
  cheap live simulation isn't available — explicitly labelled as a rough guide, not a per-action delta.
- **Phase 2:** layer **(3)** — once a reader has ≥ K snapshots, blend the historical slope with the
  simulated estimate (shrinkage toward the simulation when history is thin), narrowing the band.
- The band is **clamped and rounded to integers** and never presented as more precise than the
  underlying signal supports; a metric whose simulation is unstable shows **no number**, only the
  qualitative action (honesty rule).

**Why not a single "+5"?** Because it is unfalsifiable and identical for everyone. A simulated band is
**testable** (golden fixtures assert the band for a fixed reader), **explainable** (we can show the
before/after percentile), and **honest** (it widens or disappears when we don't know).

---

## 6. Explainability contract

Every recommendation exposes four parts — **no opaque scoring**. This extends the existing
`improvements` item (superset: old consumers keep working; `title`/`detail`/`impact` stay present,
`impact` becoming the band's midpoint for back-compat).

```jsonc
{
  "id": "imp_sourceDiversity",
  "metric": "sourceDiversity",
  "kind": "source_concentration",          // which rule fired

  "trigger":  {                            // WHAT BEHAVIOUR fired the rule
    "summary": "82% of your reading is from your top 2 outlets",
    "signals": [{ "name": "top2SourceShare", "value": 0.82, "threshold": 0.60 }]
  },
  "evidence": {                            // THE NUMBERS, bound from real data
    "topSources": [ {"source":"Reuters","share":0.47}, {"source":"BBC","share":0.35} ],
    "suggestedSources": [ {"source":"AP","lean":0.0}, {"source":"Al Jazeera","lean":-0.1} ],
    "metricScore": 34, "benchmark": 50
  },
  "action":   {                            // CONCRETE STEP, computed from evidence
    "text": "Read one article this week from AP or Al Jazeera",
    "cadence": "one this week"             // realism-scaled to reading frequency
  },
  "benefit":  {                            // EXPECTED BENEFIT, estimated not fixed
    "metric": "sourceDiversity",
    "estimate": { "low": 4, "high": 6, "method": "simulated" }  // or {"band":null,"method":"deficit"}
  },

  "title":  "Broaden beyond Reuters and BBC",     // derived from evidence (back-compat field)
  "detail": "82% of your reading is from Reuters and BBC. …",
  "impact": 5                                     // band midpoint (back-compat with today's UI)
}
```

**Traceability guarantee:** the binder copies numbers **from `ReaderSignals`** (which is itself a copy
of report/store values); it never re-derives or invents. A test asserts that every number in
`trigger`/`evidence` equals its source field in the same report — the parity discipline `rec_explain`
already uses.

**Frontend:** `report-widgets.tsx`'s `Improvements` renders `title`/`detail`/`impact` today; the new
parts render progressively — a "Why this?" disclosure showing `trigger` + `evidence`, and the band in
place of the fixed badge. `title`/`detail` become **localisable via bound slots** (fixing the current
raw-English gap the investigation flagged) because they're now format strings with data arguments.

---

## 7. Performance

The recommendation set is built **inside `_serialize_report`, which runs inside the cached `_model`**
(personalize.py:197-262). So the cost model is: *paid once per `(reading_version, reception_version)`,
then free until the reader reads again or their cross-cutting reception changes.* No new cache tier.

| Layer | What | Cost | Where it runs |
|---|---|---|---|
| **Precomputed (per corpus snapshot)** | Reference population distributions (`*_pct`, `catalog_cat_share`), outlet lean/registry, candidate alternate-outlet index per lean bucket. | Amortised over all users; already computed for scoring. | Corpus refresh, not the request path. |
| **Precomputed (per user, on model rebuild)** | Phase 2 historical-impact coefficients from `report_snapshots`. | O(snapshots), on the same rebuild that already runs. | `_build_model`. |
| **Live (inside the cached report build)** | Signal extraction (copy), rule guards (≤ 7 booleans), evidence binding (array reads), **impact simulation** (≤ 5 distribution metrics × marginal-read recompute over the reader's shares) → **bounded O(#metrics × #categories)**, a few thousand float ops. | Small next to the RWE `compute` already in the build. | `_serialize_report`. |
| **Cached (served)** | The whole `improvements[]` array, as part of the report. | Free on cache hit. | `_model` cache, invalidated on new read / reception change (personalize.py:255-262). |
| **Never live** | Full re-augmentation or a fresh graph walk *per candidate action* (the expensive counterfactual for graph metrics). | Would multiply the RWE compute by #candidates. | Excluded from RC2; graph-metric impact uses the deficit fallback. |

**Net:** RC2 adds only bounded arithmetic to a build that already runs the RWE engine; it inherits the
existing invalidation semantics exactly, so **real-time behaviour is unchanged** — a new read still
refreshes recommendations on the next request, now with recomputed evidence *and* impact.

---

## 8. Example recommendations for several reader profiles

Illustrative outputs (numbers shown as they'd bind from each profile's report):

**Profile A — "The wire-service loyalist"** (reads Reuters+BBC almost exclusively, balanced lean):
> **Broaden beyond Reuters and BBC.** *82% of your reading is from Reuters and BBC. Reading one
> article this week from AP or Al Jazeera would lift Source Diversity.* `est. +4–6` · fired by:
> top-2 outlet share 82% > 60%.

**Profile B — "The one-sided partisan"** (viewpoint 78% right, low echo score):
> **Hear the other side on a contested topic.** *78% of your political reading leans right. One
> good-faith center-left piece on an issue you already follow would loosen the echo chamber.*
> `est. +3–5` · fired by: viewpoint skew 0.78 > 0.65.

**Profile C — "The outrage reader"** (fear+outrage 41% of attention):
> **Trade one charged read a day for analysis.** *41% of your reading leans on fear and outrage.
> Swapping one for a calm analysis piece raises Emotional Balance.* `est. +5–7` · fired by:
> charged-attention share 0.41 > 0.30.

**Profile D — "The narrow-topic reader" (blind spot)** (never reads Economy; catalog 9% Economy):
> **Widen into Economy.** *Economy is 9% of what's available but ~0% of your reading. One Economy
> read from Reuters or Bloomberg would broaden your Topic Diversity.* `est. +4–6` · fired by:
> blind-spot gap 0.98.

**Profile E — "The un-receptive reader"** (we surfaced 14 cross-cutting recs, opened 1):
> **Open the cross-cutting reads we surface.** *We showed you 14 opposite-side reads; you opened 1.
> Opening a couple lifts Open-Mindedness — the metric that measures receptiveness.* `guide only` ·
> fired by: cross-cutting open rate 1/14 (graph metric → deficit-banded, no point estimate).

**Profile F — "The estimate reader" (sub-threshold, onboarding only)**:
> **Add two cross-cutting reads a week.** *(generic tip — your report is still an Estimate; read a
> few articles to unlock personalised recommendations.)* — no numeric impact (honesty: not enough
> data). Same as today, by design.

Note how A–D bind concrete outlets/topics and a simulated band; E degrades honestly to a guide; F is
the unchanged estimate fallback.

---

## 9. Implementation roadmap

### Phase 1 — RC2 (build on what's in-hand; no new data collection)

1. **`recommendation_engine` leaf module** (mirrors the `rec_pipeline`/`settings_service` leaf pattern):
   `ReaderSignals` extractor (pure projection of the report + store counts), the 7-rule engine with
   named thresholds, and the evidence binder. Wired into `_serialize_report` behind a flag so the old
   `_IMPROVEMENTS` path remains as the fallback/estimate behaviour.
2. **Simulated impact for the 5 distribution metrics** (approach 2) + **deficit-band fallback** for the
   2 graph metrics; presented as an integer **band** (approach 4). Back-compat `impact` = band midpoint.
3. **Superset `improvements` contract** (§6): add `trigger`/`evidence`/`action`/`benefit`; keep
   `title`/`detail`/`impact`. Expose the raw reporting ratio and top-2 source share the rules need.
4. **Localisation done right:** `title`/`detail`/`action` become i18n format strings with bound slots
   (closes the raw-English gap the investigation found) — across all 5 catalogs.
5. **Frontend:** extend `<Improvements>` with a "Why this?" disclosure (trigger+evidence) and the band
   badge; touch-accessible per the RC1 tooltip pattern. Pure presentation.
6. **Tests:** golden fixtures for profiles A–F (deterministic output pinned), a **parity test**
   (every evidence number equals its report source), an **impact-monotonicity test** (more deficit ⇒
   ≥ impact), and a **no-fabrication test** (thin data ⇒ no number). Mirror the `rec_pipeline` fixture
   discipline.

*RC2 exit:* every improvement card names a real behaviour and a real action, with a defensible impact
band, fully deterministic and golden-pinned — and the estimate path is unchanged.

### Phase 2 — Historical calibration & preference signals

7. **Historical impact (approach 3):** precompute per-user slope from `report_snapshots` on model
   rebuild; blend with the simulation (shrinkage), narrowing the band as tenure grows.
8. **Consume `rec_feedback`** (currently recorded-only, store.py:323): `dislike`/`ignore` **suppress**
   or down-rank a repeated action; `read_later`/saved-but-unread seed intent-based nudges. Governance:
   a deliberate, tested opt-in — the table's docstring explicitly reserves this decision.
9. **Frequency/recency gating:** aggregate reads/week to (a) scale action cadence realistically and
   (b) switch a dormant reader to a re-engagement recommendation instead of diet tuning.
10. **Diversity of recommendations:** avoid three cards all pointing at the same action; rank with a
    light penalty for overlapping actions.

### Future

11. **Full counterfactual for graph metrics:** a cheap incremental graph update so echoChamber /
    openMindedness get true simulated impact instead of the deficit fallback (retire the fallback).
12. **A/B-able rule weights** behind the sandbox (`rec_sandbox`) so rule thresholds and impact methods
    can be evaluated offline against held-out snapshots before shipping.
13. **Cross-surface coherence:** align the report's improvement actions with the AI Coach's weekly
    review and the notification nudges, so the reader hears one consistent recommendation.

---

## Confidence & risk notes

- **Highest-confidence, lowest-risk piece:** the evidence binding (§4/§6) — it only *composes numbers
  the report already computes*, so it's almost pure presentation and fully testable.
- **Main technical risk:** the simulated impact for **graph metrics** (echo, open-mindedness) — the
  design deliberately defers these to a deficit fallback in RC2 rather than shipping an expensive or
  shaky live counterfactual.
- **Main product risk:** naming a specific alternate outlet the reader may dislike — mitigated by
  choosing from catalog outlets near their *thin* lean and (Phase 2) suppressing disliked ones.
- **Determinism/testability are preserved by construction** — no LLM, no randomness; every stage is a
  pure function pinned by golden fixtures, exactly like the existing metric and rec pipelines.

---

*No code was implemented in this design. Stop after the design.*
