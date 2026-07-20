# RC2.2 — Dynamic Impact Estimation

Replaces the fixed recommendation impact constant (the old `+5` from the `_IMPROVEMENTS` table) with a
**deterministic, evidence-based estimated impact range** (e.g. `+3–6`), computed from the cached report
model. Recommendation **selection, ordering, rules, and evidence binding are unchanged** — only the
impact value is replaced. No ledger, lifecycle, feedback ranking, ranking changes, or evaluation
framework.

## What changed

- **Impact is now a range, computed per reader.** Each improvement carries a new optional
  `impactEstimate {low, high, method, metric, confidence, fromScore, toScore, explanation}`. The
  backward-compat scalar `impact` becomes the **band midpoint** (so single-scalar consumers still work).
- **Two deterministic methods**, matching the approved design (`PERSONALIZED_RECOMMENDATIONS_DESIGN` §5):
  - **`simulated`** — for the five distribution metrics of a *measured* report (topic/source diversity,
    reporting ratio, emotional balance, viewpoint balance): perturb the reader's own distribution by the
    suggested action and re-percentile against the population.
  - **`deficit`** — deterministic fallback for the graph metrics (echoChamber, openMindedness, whose raw
    value needs the walk) and for any *estimate* report (no reads to simulate).
- **Files:** `examples/api_server.py` (estimator + wiring into both improvement builders),
  `examples/api_fastapi.py` (`ImpactEstimateModel` + optional field), `web/types/domain.ts`
  (`ImpactEstimate`), `web/components/report/report-widgets.tsx` (range badge + explanation on hover),
  `web/mock/data.ts`, `tests/test_api_server.py` (7 new tests; one RC2.1 impact test rescoped).

## Estimation methodology

**The score is a percentile.** Every metric score is the percentile rank of the reader's raw value
within the reference population (`health_report.percentiles` / `Backend._pct_vs_pop`). So "impact" has a
precise meaning: *how many percentile points the score moves if the reader takes the suggested action.*

**Simulation (distribution metrics).** Reusing `health_report`'s **own** raw functions — no scoring
change — the estimator:
1. reads the reader's current distribution from the cached model (`corpus.pop`: `UC`/`UO` counts,
   `n_clicks`, `attn`, `cross`, `n_pol`, and the raw arrays `topic`/`eff_src`/`reporting`/`balance`/`cross`);
2. applies the suggested action to that distribution — e.g. topicDiversity adds a read to the most
   under-covered category and recomputes `normalized_entropy`; sourceDiversity adds a new outlet and
   recomputes `effective_number`; reporting/emotional/viewpoint shift the relevant mean/share by one read;
3. re-percentiles the perturbed raw value against the population raw array (fraction-below), and takes
   the gain over the current percentile.

The band comes from applying the action at **two intensities** matched to the recommendation's cadence
(1× for `low`, a few× for `high`), so `+low–high` reads as "do it once → …, keep it up → …".

**Deficit fallback (graph metrics + estimate reports).** A coarse guide from how far the score sits
below the typical reader: `band ≈ [10%, 25%] of min(headroom, max(gap, 4))`. Explicitly labelled
`method: "deficit"` and `confidence: "low"` so it is never mistaken for a per-action simulation.

**Credibility cap.** A few reads can swing a *sparse* reader's raw metric a long way, so an uncapped
simulation can read `+25–43` — which looks like a bug and breaks parity with the prior `+4–8` scale. The
band is scaled into `[0, 10]` (shape preserved) and, when it had to be capped, `confidence` drops to
`low` — the honest signal that the underlying estimate was volatile.

**Confidence.** `simulated` + ≥20 reads + uncapped → `high`; `simulated` + ≥8 reads + uncapped →
`medium`; otherwise `low`; `deficit` → always `low`.

**Explainability (requirement 4).** Every estimate exposes its `method`, affected `metric`,
`confidence`, the `fromScore`→`toScore` percentile move (anchored to the score shown on the card), and a
plain-language `explanation` of how it was derived. All deterministic — pure functions of the report
model, no randomness, no clock, **no new database query** (everything comes from the already-cached
`corpus.pop`).

## Before / after (live engine output, multiple reader profiles)

Old value is the fixed `_IMPROVEMENTS` constant; new is the computed band `[method/confidence]` with the
percentile move.

**High-confidence reader (≥20 reads, uncapped)**
```
viewpointBalance   was +8   now +3–6   [simulated/high]    79 → 82–85
```

**Medium-confidence reader**
```
topicDiversity     was +4   now +1–3   [simulated/medium]   1 → 2–4
```

**Sparse reader (few reads → volatile → capped, low confidence)**
```
topicDiversity     was +4   now +6–10  [simulated/low]     21 → 27–31
sourceDiversity    was +5   now +4–10  [simulated/low]     31 → 35–41
emotionalBalance   was +6   now +5–10  [simulated/low]     44 → 49–54
```

**Estimate report (zero reads → deficit fallback)**
```
viewpointBalance   was +8   now +4–9   [deficit/low]
sourceDiversity    was +5   now +3–8   [deficit/low]
reportingRatio     was +4   now +2–4   [deficit/low]
```

The old system showed the **same** `+8` / `+5` / `+4` to every reader; the new bands vary by the
reader's actual diet and read count, and each carries an explanation such as: *"Simulated: taking this
step would move your Viewpoint Balance percentile from 79 to about 82–85 (+3–6), by recomputing the
metric with the added reading against the reference population."*

## Compatibility

Backward compatible: `impact` (int) stays and is the band midpoint; `impactEstimate` is a **new optional
field** (`response_model_exclude_none`), absent on older payloads. The frontend shows the range when
present and falls back to `+{impact}`. No field renamed or removed; selection, ordering, rules, and the
RC2.1 evidence remain identical.

## Validation

| Check | Result |
|---|---|
| `pytest tests/test_api_server.py` | **61 passed** (7 new RC2.2 tests) |
| `pytest api_server · api_fastapi · personalize · enrich · demo_determinism` | **173 passed** |
| Web `tsc --noEmit` | **clean** |
| Web `node --test` | **96 passed** |
| `check:i18n` | **658 keys × 5 languages**, no unused keys |
| `next build` | **succeeds**; `/report` **376 kB** (unchanged) |
| Playwright `health-report.spec` (live engine + web) | **1/1 passed** |

**Requirement-7 verification**
- **Deterministic output** — `test_impact_is_deterministic`: two reports give byte-identical estimates.
- **Compared to previous fixed values** — before/after table above; `test_impact_is_dynamic_not_fixed_constant`
  proves the same metric yields varied bands across readers (a constant would not).
- **Stable performance** — the estimator is bounded numpy on data already in the cached model; `/report`
  First-Load JS is unchanged (376 kB) and no new DB query is issued (the estimator takes only `pop`).
- **Unchanged ordering & evidence** — `test_impact_addition_left_selection_and_evidence_intact` +
  `test_improvements_selection_and_copy_unchanged`.
- **Method split honoured** — `test_impact_method_split_simulated_vs_deficit` and
  `test_impact_estimate_mode_uses_deficit`.
- **Band well-formed & capped** — `test_impact_estimate_present_and_well_formed`
  (0 ≤ low ≤ high ≤ `_MAX_IMPACT`; midpoint = `impact`; `toScore` consistent).

## Out of scope (deferred, per the brief)

Recommendation persistence/ledger, lifecycle, feedback-aware ranking, ranking changes, and the
evaluation framework. Historical calibration of the bands from realized outcomes (design §6) is a later
phase; RC2.2 is simulation + deficit fallback only.

---

*RC2.2 replaces the impact value only — selection, ordering, rules, and evidence are unchanged.*
