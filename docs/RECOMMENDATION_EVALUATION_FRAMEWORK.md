# Design — Recommendation Evaluation Framework

**Objective:** design how the improvement-recommendation engine (see
`PERSONALIZED_RECOMMENDATIONS_DESIGN.md`) **measures its own quality over time** — did a
recommendation change behaviour, did the targeted metric actually improve, and can we attribute that
improvement honestly and deterministically.

**Design document. No code implemented.** Every mechanism is grounded in the store schema and the
existing offline harnesses (cited inline) so it is buildable, and it holds the same line as the rest of
the product: **deterministic, explainable, no fabrication, no LLM.**

---

## 0. Framing — what is being evaluated, and two things it must not conflate

**Unit of evaluation = an *improvement recommendation*** (a behaviour-change tip targeting one metric,
e.g. "read one article from AP → Source Diversity"). Its success is defined by whether the reader took
the suggested action **and** the targeted metric moved.

Two distinctions the framework must keep straight (the investigation already showed they're different
subsystems, and conflating them would corrupt every number):

| | Improvement recommendation | Article recommendation (the feed) |
|---|---|---|
| What it is | report `improvements[]` tip → a metric | `/api/me/recommendations` article card |
| Reception signals | *(none persisted today — gap G1)* | `rec_events` shown/opened (store.py:292) |
| Explicit feedback | "Add to goals" *(client-only, not persisted — gap G2)* | `rec_feedback` like/dislike/ignore/read_later (store.py:318, **recorded-only**) |
| Outcome | targeted metric Δ over `report_snapshots` | (indirect — feeds reads) |

The article feed's reception (`rec_events`) and feedback (`rec_feedback`) are **input signals** to the
improvement evaluation, not the thing being scored. Requirement 5 (feedback → ranking) operates on
those inputs; requirements 1–4 score the improvement recommendation.

**Two infrastructure gaps must be closed before anything can be measured** — stated up front so the
roadmap is honest:

- **G1 — no improvement-recommendation ledger.** Improvement recs are recomputed each report and never
  persisted with an identity, so "this rec appeared on Tuesday, the reader acted Thursday, it completed
  next week" is currently unobservable. **Proposed: an `improvement_events` table** (design below),
  analogous to `rec_events`.
- **G2 — acceptance isn't captured.** "Add to goals" is local `useState` (report-widgets.tsx:45), lost
  on reload. To measure acceptance it must persist to the ledger.

---

## 1. Success metrics

Each listed signal, mapped to the recommendation object it attaches to, its definition, and where the
data lives (✅ available today · 🔶 needs the ledger/persistence · ⛔ needs a detector).

| Success signal | Attaches to | Definition | Data source |
|---|---|---|---|
| **Recommendation shown** | improvement | The rec was surfaced in a report render. | 🔶 `improvement_events.appeared_at` (G1) |
| **Recommendation opened** | article feed | Reader opened a recommended article. | ✅ `rec_events.opened_at` (store.py:1166) |
| **Article read** | both | A read occurred; `opened_from` says which surface. | ✅ `reads.opened_from` (store.py:269) |
| **Recommendation accepted** | improvement | Reader signalled intent — "Add to goals". | 🔶 ledger `accepted_at` (G2) |
| **Satisfying action taken** | improvement | A read matched the rec's **target** (outlet / topic / side / register). | ⛔ satisfying-read detector (§2) over `reads` scored JSON |
| **Recommendation ignored** | improvement / feed | Shown repeatedly, never accepted/acted; or explicit `ignore`. | 🔶 ledger + ✅ `rec_feedback` |
| **Recommendation dismissed** | improvement / feed | Reader explicitly removed it (needs a dismiss control) or `dislike`. | 🔶 ledger `dismissed_at` + ✅ `rec_feedback` |
| **Metric improvement** | improvement | Targeted metric score rose between snapshots. | ✅ `report_metric_series` (store.py:589) |
| **Sustained improvement** | improvement | The gain held for ≥ K subsequent snapshots (didn't regress). | ✅ `report_metric_series` window |
| **Repeat engagement** | improvement | Reader kept taking the satisfying action after the rec completed. | ✅ `reads` + detector |

**Design stance on what counts as "success":** a recommendation is only a *success* when a **satisfying
action** was taken **and** the **targeted metric improved and sustained**. Acceptance ("Add to goals")
and opens are **leading indicators**, not success — measuring them alone would reward nagging. The
terminal success metric is *sustained, attributed metric gain* (§2).

---

## 2. Attribution model

**The question:** *Source Diversity rose 8 points. Was it recommendation A, recommendation B, or organic
reading?* Attribution must answer this **deterministically** and **honestly**, and it must not fall into
the trap that a **percentile score can move without any behaviour change** (the population shifted on a
corpus refresh).

### 2.1 The substrate

`report_metric_series(uid)` (store.py:589) gives, per snapshot, `{date, overall, metrics{key:score}}`
— **already stored, never recomputed**. So for any metric `M` we have a time series `M_0, M_1, …`. The
reads between snapshot `t` and `t+1` are `R_t = {reads with created_at in (t, t+1]}` (store.py:271),
each carrying its scored JSON (outlet, topic, lean, register, emotion).

### 2.2 Three-way decomposition of a metric delta

Between consecutive snapshots, decompose `ΔM = M_{t+1} − M_t` into three named parts. **Nothing is
hidden in a residual** — every point is labelled.

```
 ΔM (8 pts)
   ├── behaviour contribution   ← the reader's reads in the window   (the attributable part)
   │      ├── recommendation-attributed  ← reads that SATISFY an active rec's target
   │      │        ├── credited to Rec A
   │      │        └── credited to Rec B
   │      └── organic                     ← reads that satisfy no active rec
   └── population drift          ← the reference distribution moved (corpus refresh),
                                   NOT the reader — isolated, never credited to a rec
```

**Isolating population drift (the honesty step).** Because `M = percentile(raw value vs population)`
(`_pct_vs_pop`, api_server.py:1084), recompute `M_{t+1}` **against the population snapshot from time t**
(hold the reference fixed). The difference between that and the real `M_{t+1}` is *population drift* —
score movement the reader didn't cause. Only the population-held-fixed delta is eligible for
behaviour attribution. *(This requires retaining the reference distribution per corpus snapshot — a
small precompute, see §7 of the recommendations design.)*

**Splitting behaviour across individual reads — leave-one-out (LOO).** For the behaviour part, use the
same simulated-percentile machinery the impact estimator uses (`PERSONALIZED_RECOMMENDATIONS_DESIGN`
§5): the marginal contribution of read `r` is
`c(r) = M(with r) − M(without r)`, computed on the reader's own distribution against the fixed
population. This is the leave-one-out attribution already named as the intended model for the recommender
(`rec_explain.py:28` — "Influence percentages (leave-one-out attribution)"). Sum of `c(r)` over the
window ≈ the behaviour contribution; the small gap (order effects / interactions between reads) is
reported as an explicit **interaction residual**, not silently absorbed.

**Crediting recommendations.** A read `r` is *recommendation-attributed* to rec `X` iff it **satisfies
X's target** at the time of the read — the **satisfying-read detector**: `r`'s outlet ∈ X's suggested
outlets, or `r`'s topic = X's blind-spot category, or `r`'s lean is on X's suggested side, etc. The
rec's credited gain = `Σ c(r)` over its satisfying reads. A read satisfying no active rec is *organic*.
Ties (a read satisfies two active recs) split the credit **evenly and deterministically** by the recs'
stable ids (no randomness).

### 2.3 Worked example

> Source Diversity: `M_t = 34 → M_{t+1} = 42` (**ΔM = +8**).
> Hold population fixed → +7 (so **+1 was population drift**, not credited).
> Window reads and their LOO contributions: Reuters +0 (already dominant), **AP +3** (satisfies Rec A
> "add AP/Al Jazeera"), **Al Jazeera +2** (Rec A), Politico +1 (organic), Vox +1 (organic).
> **Attribution:** Rec A = **+5** · organic = **+2** · population drift = **+1** · interaction residual
> = **0**. → *"Of your +8, ~5 came from the two new wire sources we suggested, ~2 from other reading,
> ~1 from the field shifting."*

Every number traces to a stored read and a fixed-population recompute — **explainable and testable**
(a golden fixture pins the decomposition for a fixed reader+window).

### 2.4 What the model deliberately does **not** claim

- It does **not** claim the recommendation *caused* the read (no counterfactual "would they have read AP
  anyway"); it attributes the *metric effect of reads that matched the recommendation*. Framed honestly
  as "reads aligned with this recommendation contributed +5," never "we caused +5."
- Graph metrics (echoChamber, openMindedness) whose LOO needs the walk get a **coarser attribution**
  (window-level, not per-read) in Phase 1 — same deferral as their impact estimate.

---

## 3. Recommendation lifecycle

A finite state machine per `(reader, recommendation identity)`, where identity = `rule_kind` + target
descriptor (so "add AP" and "add Bloomberg" are distinct, and re-suggesting AP is the *same* rec).

```
                         guard fires on the model
                                   │
                                   ▼
        ┌───────────────────► [ ACTIVE ] ◄──────────── reappears after COOLDOWN
        │                     appeared_at              (regression persists)
        │                        │   │
        │      "Add to goals"    │   │  satisfying read (partial, if cadence>1)
        │            ▼           │   ▼
        │      [ ACCEPTED ] ─────┼──► [ IN-PROGRESS ]
        │       accepted_at      │      progress k/N
        │            │           │        │
        │            │           │        ▼  target metric crosses completion threshold
        │            │           │   [ COMPLETED ] ── enters COOLDOWN ──┐
        │            │           │    completed_at                      │
        │            │           │                                      │
        │   no action for N days/snapshots, no dismissal                │
        │            ▼           ▼                                      │
        │      [ ABANDONED ]   [ SUPERSEDED ]  (a higher-impact rule    │
        │       (quality        took its slot; not a failure)          │
        │        signal)             │                                  │
        └────────────────────────────┴──────────────── dismiss ───► [ DISMISSED ] (terminal,
                                                                      respected on cooldown)
```

**Transitions (all deterministic, driven by the model version + ledger):**

- **Appear** — the rule's guard is true on the current cached model (a metric below threshold, a
  concentration/skew condition, a live blind spot). Recorded once in the ledger (`appeared_at`).
- **Accepted** — reader clicks "Add to goals" → `accepted_at` (needs G2 persistence).
- **In-progress** — a satisfying read arrives; for cadence targets ("2/week") track `k/N`.
- **Complete** — the **guard stops firing** *because the deficit closed*: the targeted metric reached
  its completion threshold (e.g. the population median / benchmark), verified against `report_metric_series`.
  This is the honest completion signal — the rec did its job.
- **Disappear** — the guard stops firing (completed, or behaviour changed so the condition no longer
  holds) **or** it's **superseded** by a higher-impact rule for the same reader (only N slots) **or**
  **dismissed**.
- **Reappear** — after completion/dismissal, a **cooldown** (e.g. M snapshots or D days) suppresses
  re-nagging; if the metric regresses below threshold *and* the cooldown elapsed, the rec re-enters
  ACTIVE. Dismissal earns a longer cooldown than completion.
- **Abandoned** — ACTIVE/ACCEPTED for N snapshots with no satisfying read and no dismissal → a quality
  signal (the rec didn't land), not shown differently to the reader but counted in §4.

**Why completion is metric-based, not action-based:** completing on "took one satisfying read" would
mark success before the metric moved. Completing on *the deficit closing* ties the lifecycle to the
outcome the recommendation exists to produce.

### 3.1 The ledger (`improvement_events`, proposed — closes G1/G2)

One row per `(user_id, rec_key)` where `rec_key = rule_kind + target hash`:

| Column | Meaning |
|---|---|
| `user_id`, `rec_key`, `rule_kind`, `metric`, `target` (JSON) | identity + what it targets |
| `appeared_at`, `accepted_at`, `dismissed_at`, `completed_at`, `last_seen_at` | lifecycle timestamps |
| `appeared_snapshot_id`, `completed_snapshot_id` | tie the rec to the metric time series for attribution |
| `state` | ACTIVE / ACCEPTED / IN-PROGRESS / COMPLETED / ABANDONED / SUPERSEDED / DISMISSED |

Idempotent per `(user, rec_key)`, exactly like `rec_events` / `saved_articles`. Product state only —
it drives evaluation and never feeds the recommender's own guards (keeping the generator a pure function
of behaviour, not of its own history — no feedback loop into selection except through the explicit
ranking weights of §5).

---

## 4. Recommendation quality metrics

Cohort-level, computed **offline** in batch from the ledger + `report_metric_series` (deterministic,
reproducible from the logs — the natural home is the `rec_sandbox` offline evaluator, S1, which already
contracts `evaluate` + a report with isolation/determinism/parity/honesty tests). Every rate is also
sliceable **per `rule_kind`** (which recommendations actually work) and per cohort.

| Metric | Formula | Reads as |
|---|---|---|
| **Acceptance rate** | `accepted / shown` | Did readers opt in? (leading) |
| **Action rate** | `≥1 satisfying read / shown` | Did readers actually do it? (stronger than acceptance) |
| **Completion rate** | `completed / shown` (and `/ accepted`) | Did the deficit close? |
| **Improvement rate** | `completed with attributed ΔM>0 / completed` | When it completed, did the metric genuinely rise (vs completing on drift)? |
| **Sustained-improvement rate** | `gain held ≥K snapshots / completed` | Did it stick? |
| **Abandonment rate** | `abandoned / shown` | Wasted impressions. |
| **Dismissal rate** | `dismissed / shown` | Reader rejected the advice. |
| **Average metric gain** | `mean(attributed ΔM over completed)` | Realized impact per rec — the empirical counterpart to the *estimated* band (§5 of the rec design). **Estimated-vs-realized gap is the calibration signal.** |
| **Time-to-complete** | `median(completed_at − appeared_at)` | How long the behaviour change takes. |

**A crucial honesty check baked in:** *improvement rate* uses **attributed** ΔM (population-drift
removed, §2), so a recommendation can't take credit for a score that rose because the field shifted. And
the **estimated-vs-realized gain gap** per `rule_kind` is the headline diagnostic — it tells us whether
the impact bands we show readers are honest, and feeds §6.

---

## 5. Personalization feedback → ranking

Today `rec_feedback` (like/dislike/ignore/read_later) is **recorded-only — no path reads it**
(store.py:323). This design specifies how it *should* influence the improvement engine's **ranking
stage** (stage E of the recommendations design), as a **deterministic, bounded, explainable weight** —
never a hidden on/off flip.

**Signal → weight mapping** (per reader, per target entity — an outlet, topic, or side):

| Feedback (on an article whose target overlaps a rec) | Effect on the overlapping rec's rank weight |
|---|---|
| `like` | small **+** (confirm the direction; keep suggesting near it) |
| `read_later` / saved-but-unread | small **+** (intent registered) — and seed a follow-up nudge |
| `ignore` (repeated) | small **−** (fatigue; rotate to a different target/action) |
| `dislike` | larger **−**; after a **threshold** of dislikes on a target → **suppress** that specific action and pick an alternate (never suppress the *metric* — offer a different route to it) |

**Rules of the mapping (so it stays trustworthy):**
- **Monotone & bounded** — more negative feedback only lowers the weight, clamped to a floor; it can
  re-rank and, past a threshold, suppress a *specific action*, but the underlying metric still gets a
  recommendation (via an alternate target). A reader who dislikes AP gets "try Al Jazeera," not silence.
- **Deterministic** — the weight is a pure function of the reader's feedback counts; same counts → same
  ranking. No decay-by-wall-clock unless the clock is an explicit, testable input.
- **Explainable** — the "Why this?" panel can state "ranked lower because you dismissed similar
  suggestions," so feedback never acts invisibly.
- **Governed** — consuming `rec_feedback` is a deliberate opt-in the table's own docstring reserves;
  it ships behind a flag with tests, exactly as the recommendations design scheduled it for Phase 2.

**Feedback also feeds evaluation, not just ranking:** dismissal/ignore rates per `rule_kind` (§4) tell
us which advice readers reject regardless of whether it would have worked.

---

## 6. Future learning (deterministic, no LLM)

"Learning" here = **fitting parameters from logged outcomes with deterministic estimators, recomputed on
a schedule** — reproducible from the logs, no online RL, no model weights, no LLM.

1. **Calibrate impact from realized outcomes.** The §4 *average metric gain* per `rule_kind` (and the
   estimated-vs-realized gap) becomes an empirical prior. Replace/blend the *simulated* impact band with
   a **calibrated** band: `impact = shrink(simulated, realized_mean, n)` — shrinkage toward the
   population-realized mean, tightening toward the per-user realized mean as that reader's ledger grows.
   Deterministic (means + shrinkage), and it makes the shown band *empirically honest* over time.
2. **Rank rules by realized value, not just deficit.** Selection (which 3 recs to show) can order by
   `realized_acceptance × realized_gain` per `rule_kind` — a learned prior over what works, still a pure
   function of the aggregated ledger. (Deterministic counterpart to a bandit — no exploration
   randomness; "exploration" is scheduled, e.g. deterministically rotate an under-observed rule kind.)
3. **Offline threshold tuning before shipping.** Use `rec_sandbox` (S1) + the Recommendation Regression
   Suite (S3, contract v1) to replay candidate guard thresholds / weight mappings against **held-out
   snapshot histories** and score them on the §4 metrics, before any change reaches readers.
4. **Cohort bootstrap for cold start.** New readers (empty ledger) inherit the **cohort-level** calibrated
   priors; as their own ledger fills, shrink from cohort → personal. Deterministic given the cohort
   aggregate + the reader's rows.
5. **Detect and retire dead rules.** A `rule_kind` with high abandonment and near-zero realized gain
   across the cohort is flagged for retirement/retuning — evaluation closing the loop on the generator.

All five are **batch, reproducible, and testable**: given the same logs they produce the same
parameters, and the parameters are inspectable numbers (means, slopes, weights), not opaque state.

---

## 7. Deliverables summary & implementation roadmap

**Evaluation framework** = the ledger (§3.1) + the attribution model (§2) + the cohort quality metrics
(§4), run offline in `rec_sandbox`, feeding calibration back into the generator (§6) and feedback into
ranking (§5). **Lifecycle diagram, success metrics, attribution model** — §3, §1, §2 above.

### Phase 1 — RC2 (make outcomes observable & attributable)

1. **`improvement_events` ledger** (G1) + **persist "Add to goals"** (G2) — the minimum state without
   which nothing downstream can be measured. Idempotent per `(user, rec_key)`, product-state only.
2. **Satisfying-read detector** — pure function matching a read's scored fields to an active rec's
   target; golden-tested.
3. **Lifecycle state machine** — appear/accept/in-progress/complete/abandon/dismiss/cooldown, driven by
   the ledger + model version; no reader-facing change beyond a **dismiss** control and truthful "in
   progress k/N" state.
4. **Population-drift isolation** — retain the reference distribution per corpus snapshot so a percentile
   delta can be split into behaviour vs drift (the honesty prerequisite for attribution).
5. **Attribution v1 (distribution metrics)** — LOO decomposition with the drift term and interaction
   residual; graph metrics get window-level coarse attribution. Golden fixtures pin the decomposition.

*RC2 exit:* for a fixed reader and window we can state, deterministically and testably, how much of a
metric change each recommendation and organic reading contributed — and the lifecycle of every rec is
recorded.

### Phase 2 — quality metrics, feedback, calibration

6. **Cohort quality dashboard** (§4) in `rec_sandbox` — acceptance/action/completion/improvement/
   abandonment/average-gain, sliced per `rule_kind`; the estimated-vs-realized gap surfaced.
7. **Consume `rec_feedback` in ranking** (§5) — governed, flagged, monotone-bounded weights, with the
   "why this ranked lower" explanation.
8. **Impact calibration** (§6.1) — blend realized gain into the shown band via shrinkage; the band
   becomes empirically honest.

### Future

9. **Realized-value rule ranking + scheduled exploration** (§6.2), **offline threshold tuning** against
   held-out histories (§6.3), **cohort→personal cold-start shrinkage** (§6.4), **dead-rule retirement**
   (§6.5).
10. **True counterfactual attribution for graph metrics** (retire the coarse fallback) once an
    incremental graph update exists.

---

## Confidence & risk

- **Strongest, lowest-risk foundation:** the metric time series already exists and is never recomputed
  (`report_metric_series`), so attribution stands on stored truth, and LOO reuses the impact engine the
  prior design already specifies.
- **The subtle correctness risk is population drift** — a percentile metric moving with no behaviour
  change. The design isolates it explicitly; skipping that step would silently mis-credit recommendations,
  so it is a Phase 1 prerequisite, not a nicety.
- **Main product risk:** persisting acceptance/dismissal changes what we store about a reader — handled
  as product state (like saved/notifications), governed, and never fed back into the generator's guards
  (only into explicit, inspectable ranking weights), so there is no opaque self-reinforcing loop.
- **Determinism/testability preserved by construction:** ledger + attribution + calibration are pure
  functions of logged rows and the frozen corpus, pinned by golden fixtures — no randomness, no LLM.

---

*No code was implemented in this design. Stop after the design.*
