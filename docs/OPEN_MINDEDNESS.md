# Open-Mindedness feedback loop — validation

Open-Mindedness is the 8th Information Health metric and the last one that was unpopulated for real
users (they sat at 7/8). In the reference population it is **cross-cutting click-through**: of the
opposite-side articles the feed showed a reader, what fraction did they click
(`health_report.selective_exposure_array` — "an agency signal, distinct from what ended up in their
diet"). This milestone gives a real reader the *same* signal from the *same* recommendation feed:
**of the cross-cutting recommendations the engine surfaced, what fraction did they open.** That
reception feeds the unchanged `health_report.compute(selective=...)`, so Open-Mindedness populates
automatically once the reader engages with the bridging reads.

No recommendation algorithm and no protected research module changed
(`health_report.py`, `rwe/`, `simulate_users.py`, `narrate_report.py` untouched); no second
recommendation pathway was created. The recs are the engine's existing output — this only records
their reception and ranks the reader against the population's own selective-exposure distribution.

## What changed (product layer only)

- **`store.py`** — a `rec_events` table records which recommendations were surfaced to a user (the
  denominator) and which they opened (the numerator), idempotent per `(user, article)`; plus
  `recommendation_reception(user)` = opened/shown over cross-cutting recs.
- **`api_server.py`** — expose the base population's per-user `selective` array (one line) so a real
  reader can be ranked against the same distribution.
- **`personalize.py`** — build the augmented `selective` array (population + the reader's measured
  reception) and pass it to the unchanged `health_report.compute`; cache the measured model by
  `(reading_version, reception_version)` so opening a rec refreshes the report.
- **`api_fastapi.py`** — record recs as surfaced when served; new additive
  `POST /api/me/recommendations/opened` records an open (same trust boundary as `/api/me/reads`).
- **web** — a "Read" action on each recommendation card posts the open through a new proxy route and
  refreshes the report, so Open-Mindedness appears/updates automatically.

Activation gate (release-pinned, env-tunable): a reader must have been surfaced ≥ `RWE_OPENMIND_MIN_SHOWN`
(default 3) cross-cutting recs **and** opened ≥ `RWE_OPENMIND_MIN_OPENED` (default 1). Below that it
is an honest n/a — a single stray click can't fabricate the metric.

## Validation (synthetic reference corpus, n_users=200 — the corpus the contract tests use)

### 1. A measured reader progresses 7/8 → 8/8

| state | metrics | Open-Mindedness |
|---|---|---|
| measured, no reception | **7** (topic, source, reporting, emotional, echo, viewpoint, confidence) | absent (n/a) |
| after opening cross-cutting recs | **8** (+ openMindedness) | populated |

The transition is additive — only `openMindedness` is added; the other 7 scores are byte-identical.
Verified over HTTP end-to-end (`test_open_mindedness_completes_the_metric_set`).

### 2. Recommendation interactions improve Open-Mindedness

The reader was surfaced 6 cross-cutting recs, then opened them one at a time:

| opened | reception rate | Open-Mindedness (percentile) |
|-------:|---------------:|-----------------------------:|
| 0 | 0.00 | n/a (honest — no engagement yet) |
| 1 | 0.17 | 24 |
| 2 | 0.33 | 71 |
| 3 | 0.50 | 95 |
| 4 | 0.67 | 99 |
| 5–6 | 0.83–1.00 | 100 |

Monotonic non-decreasing once active; opening more of the other side raises the score, exactly as the
population metric behaves.

### 3. Recommendations, report, and coach remain consistent

- **Recommendations** are identical before and after reception (article ids, strategies, cross-cutting
  flags) — the loop does not perturb the recommender (reception is not fed into `AdaptiveRWEB`
  exposure; see roadmap).
- **Report** — all 7 pre-existing metric scores are unchanged; Open-Mindedness is purely additive.
- **Coach** — greeting and replies stay valid JSON and grounded; Open-Mindedness becomes an eligible
  citation (the coach still cites its top two available metrics, so behavior is unchanged in shape).

### 4. Estimate vs Measured unchanged

The estimate path already omits Open-Mindedness and is untouched; a below-threshold reader still gets
the Initial Estimate. The metric only ever appears on a Measured report with sufficient reception.

## Benchmarks

| operation | cost | note |
|---|---|---|
| record a recommendation open | 0.57 ms | one idempotent upsert |
| measured-model rebuild on reception change | ~46 ms | the **same** rebuild a new read triggers — no new expensive path |
| `recommendation_reception(user)` | <1 ms typical | grows with recs surfaced (22 ms at a 2000-row stress state) |

The feedback loop adds no new heavyweight computation: it reuses the existing per-version augmented
rebuild, and recording is a single-row write.

## Test results

Full suite **410 passed** (10 new: `rec_events` store methods, the reception→Open-Mindedness
personalize path incl. cache-rebuild + base-corpus isolation, and the HTTP 7→8 progression). Web
typecheck + production build pass.

## Remaining technical debt

- **`recommendation_reception` loads rows** to count them; a `COUNT`/`SUM(opened_at IS NOT NULL)`
  aggregate would keep it O(1) and also trims the model-rebuild path (which calls it). Only matters
  once a user accumulates many surfaced recs.
- **Reception does not yet drive `AdaptiveRWEB`.** The population feeds measured cross-cutting
  reception into the adaptive recommender's exposure; a real user's exposure stays neutral (0.5), by
  design here so recommendations remain consistent for this milestone. Closing that loop is the next
  natural step (below).
- **Denominator depends on the reader visiting the recommendations surface** (that's when "shown" is
  recorded). Fine for the web app; an emailed/embedded rec would need its own surfacing event.
- **Recs are reference-corpus items** (no live URL in this beta), so "open" is an in-app reception
  event, not a navigation. In production, real article URLs would make the open a true click-through
  with no code change to this loop.

## Recommendation for the next milestone

**Close the adaptive loop:** feed a real user's measured cross-cutting reception into their
`AdaptiveRWEB` exposure (the population already does this via `adaptive_satisfaction.measured_exposure`),
so the bridging recommendations size their stretch to how open the reader has actually been — a
personalized, self-improving feed built entirely on the signal this milestone now captures. All eight
metrics are populated for real users, so this is enhancement, not a gap.
