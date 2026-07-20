# RC2.5 — Recommendation Evaluation & Attribution

The deterministic evaluation framework designed in `RECOMMENDATION_EVALUATION_FRAMEWORK.md`, now built.
It measures how effective the Health Report's improvement recommendations *were* over time — outcomes,
metric attribution, estimated-vs-realized calibration, and per-rule quality — **entirely read-only**
over data that already exists (the RC2.3 lifecycle ledger + the report-snapshot history). It changes
**nothing** about generation, selection, ranking, evidence, or impact estimation, and it never adjusts
ranking — it only exposes evaluation data. No heuristics, no probabilities, no learning, no ML.

## Implementation summary

- **New pure leaf `recommendation_eval.py`** — `attribute`, `evaluate_recommendation`, `evaluate_reader`,
  `rule_quality`. Every output is a deterministic function of `(snapshots, lifecycle rows)`.
- **Store (read-only projections):** `report_eval_snapshots(uid)` parses each stored snapshot into
  `{date, reads, mode, metrics{key:score}, estimates{metric:{low,high}}}` (recomputes nothing);
  `list_users_with_improvement_lifecycle()` for the cohort view.
- **API (backward compatible, additive):**
  - `GET /api/me/recommendations/evaluation` — the signed-in reader's own per-recommendation evaluation.
  - `GET /api/dev/recommendations/quality` — cohort per-rule quality & calibration (dev-gated, **404 in
    production**, like `/api/dev/diagnostics`).
- **Untouched:** `api_server` (generation/impact), `improvement_ledger` (RC2.3), `improvement_ranking`
  (RC2.4), and the report contract. No web changes.

## Attribution methodology (deterministic, req 2)

For each recommendation's metric, the **total observed score change** across the reader's snapshot
history is split three ways so the parts **sum to the whole** (`recommendationAttributed + organic +
populationDrift = last − first`):

| Component | Rule (per consecutive snapshot window) |
|---|---|
| **populationDrift** | the window added **no reads**. With reads unchanged the report is served from the same cached model, so a score change there is not the reader's reading — it is the reference population moving under them (a corpus refresh). |
| **recommendationAttributed** | the window **added reads** *and* the recommendation was **already accepted** — the reader engaged the rec, then the metric moved. |
| **organic** | the window added reads but the recommendation was **not yet accepted** — improvement the reader made on their own. |

This is an **association stated honestly** — "reads made while this recommendation was accepted account
for +N" — never "the recommendation caused +N". No per-read counterfactual, no probability.

**Population-drift isolation** falls straight out of the caching contract: a report is cached per
`(reading_version, reception_version)`, so if reads don't change the two snapshots are identical
(Δ = 0) **unless** the population was rebuilt — so a non-zero Δ in a no-new-reads window *is* drift.
(For a reception-based metric like Open-Mindedness this bucket can also hold cross-cutting-reception
change; documented in the leaf.)

### Worked example (from the test suite)

```
snapshots:  d1 reads5 score20 (est 4–8)   d2 reads5 score23   d3 reads8 score30   d4 reads11 score33
            accepted at d3
→ realizedGain      = 33 − 20 = 13
→ populationDrift   = +3   (d1→d2: no new reads)
→ recommendationAttributed = +10  (d2→d3 +7, d3→d4 +3: reads grew, rec accepted)
→ organic           = 0
   (10 + 0 + 3 = 13 ✓)
```

## Estimated vs realized & calibration (req 3, 5)

- **estimatedGain** — the RC2.2 impact band's midpoint, read from the earliest snapshot that carried an
  estimate for the metric (closest to when the rec was generated).
- **realizedGain** — the metric's net score change since the rec was generated (`last − first`).
- **calibrationError** — `recommendationAttributed − estimatedGain` (the honest comparison: the rec's
  *credited* effect vs its *predicted* effect). Computed only for recommendations the reader actually
  acted on (accepted / in-progress / completed); `None` otherwise.
- **attributionConfidence** — a deterministic *tier* (not a probability) from the count of behavioural
  windows: `high` ≥ 3, `medium` ≥ 1, `low` 0, `not_acted` when never accepted.

From the worked example: estimated **6**, attributed **10** → **calibrationError +4 → under_estimates**
(the recommendation did better than the estimate predicted).

**Per-rule calibration** (`rule_quality`, cohort): mean calibration error per metric with an explicit
`calibrationDirection` — `over_estimates` (mean < 0), `under_estimates` (mean > 0), or `calibrated`.
*It only exposes this; it never adjusts the estimator or the ranking.*

## Recommendation quality (req 1, 4)

Per rule (metric), deterministic across the cohort:

`instances · acceptanceRate · completionRate · dismissalRate · abandonmentRate · realizedImprovementMean
· estimatedImpactMean · sustainedRate · calibrationError · calibrationDirection`

Outcomes (req 1) come straight from the lifecycle ledger — generated / shown / viewed / accepted /
dismissed / completed / expired / superseded — tallied per reader (`evaluate_reader.outcomes`) and per
rule. **Sustained improvement** = the metric held within `SUSTAIN_MARGIN` (3) of the completion score for
≥ `SUSTAIN_WINDOWS` (2) later snapshots.

Example rule-quality (from the test suite, one rule, four instances — completed/dismissed/accepted/shown):
`acceptanceRate 0.5 · completionRate 0.25 · dismissalRate 0.25 · calibrationError +2.0 →
under_estimates`.

## Performance (req 7)

Reuses the report snapshots, the lifecycle ledger, and the cached report generation — it **recomputes no
metric**. The per-reader endpoint parses that reader's snapshots (capped at 120) + one ledger query. The
cohort endpoint is a dev/operator tool (bounded by beta cohort size, 404 in production). Nothing runs on
the report request path.

## Validation

| Check | Result |
|---|---|
| `pytest tests/test_recommendation_eval.py` | **10 passed** |
| `pytest recommendation_eval · improvement_ledger · api_fastapi · api_server · personalize · db_durability · demo_determinism` | **192 passed** |
| Web `tsc --noEmit` | **clean** |
| Web `node --test` | **96 passed** |
| `next build` | **succeeds** (`/report` 376 kB, unchanged) |
| Playwright `health-report.spec` (live engine + web) | **1/1 passed** |

**Requirement-8 demonstrations (tested):**
- **Deterministic attribution** — `test_attribution_is_deterministic`, `test_attribution_sums_to_total_and_isolates_drift`.
- **Population-drift isolation** — the no-new-reads window is isolated as drift (`populationDrift == 3`)
  and the components telescope to the total.
- **Estimated vs realized** — `test_evaluate_recommendation_calibration_and_realized` (estimated 6,
  realized 13, attributed 10).
- **Calibration output** — `test_rule_quality_rates_and_calibration_direction` (mean error +2 →
  under_estimates); `test_calibration_none_when_not_acted`.
- **API** — `test_reader_evaluation_endpoint`, `test_dev_quality_endpoint_and_404_in_production`,
  `test_evaluation_requires_auth`.

## Out of scope (future work)

Automatic learning, ML, and any ranking change driven by the calibration output. RC2.5 *exposes*
calibration; wiring it back into the estimator or ranker is deliberately deferred.

---

*RC2.5 is read-only evaluation & attribution — generation, selection, ranking, evidence, and impact are
all unchanged.*
