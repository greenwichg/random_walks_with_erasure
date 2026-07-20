# RC2.4 — Feedback-Aware Recommendation Ranking

Personalizes **which** improvement recommendations are surfaced, and **in what order**, using signals
that already exist — the RC2.3 lifecycle ledger and the `rec_feedback` table — while preserving
determinism and full explainability. **Generation is unchanged**: the engine still produces the same
weakest-metric set with the same evidence and the same impact estimates. Only the *ranking and
filtering stage* is new. No evaluation, no attribution, no ML, no LLM.

## Where it runs (and why generation is untouched)

Generation lives in `api_server._serialize_report` (pure, no store) and is **not modified** — a direct
`backend.report()` still returns the same three recommendations in ascending-score order. The new
ranking runs one layer up, in `api_fastapi`'s `GET /api/report`, **only for a signed-in reader**, after
the RC2.3 lifecycle annotation, using the lifecycle just loaded plus one cheap feedback-count query. So
anonymous/demo reports are byte-for-byte unchanged, every `api_server` test still holds, and the ranker
is a pure leaf (`improvement_ranking.py`) that reorders/filters a list.

## Ranking signals — exactly how each affects ranking

| Signal | Source | Effect on the recommendation |
|---|---|---|
| **in_progress** | lifecycle | **+3 priority** (the reader is actively working on it → keep it prominent) |
| **accepted** | lifecycle | **+2 priority** (the reader committed to it) |
| **viewed** / shown | lifecycle | neutral |
| **completed** | lifecycle | **suppressed** (`visible:false`, reason `completed`) — auto-reappears when RC2.3 flips the state back to `shown` on metric regression |
| **dismissed** | lifecycle | **suppressed** (reason `dismissed`) unless the metric has regressed ≥ `reappearDrop` points below where it was generated |
| **like** | rec_feedback | receptivity **+1** |
| **read_later** | rec_feedback | receptivity **+1** |
| **dislike** | rec_feedback | receptivity **−1** |
| **ignore** | rec_feedback | receptivity **−1** |

**Base priority** is `−score` (the reader's worst metric first — exactly today's order), so with no
feedback and no lifecycle the ranking equals the generation order.

**Article feedback is a *global receptivity prior*, not per-metric.** `rec_feedback` is keyed by
`article_id` and carries no metric target, so mapping it onto a specific improvement would need
per-article metadata lookups (expensive — violates req 6) and is exactly the cross-signal attribution
reserved for the evaluation phase. Instead the net receptivity
`(like + read_later) − (dislike + ignore)` modulates one thing deterministically: a **net-negative**
reader (rejects recs) gets **stickier suppression** of their dismissals — `reappearDrop` rises from 8 to
12 — honouring their evident "stop pushing recs at me". This is the honest, bounded use of those
signals; per-metric use is deferred, not faked.

## Ranking algorithm (deterministic)

For each generated recommendation (which may carry a `lifecycle` object):

1. **Suppression (filter).**
   - `state == completed` → suppress (`completed`).
   - `state == dismissed` → suppress (`dismissed`) **unless** `currentScore ≤ firstScore − reappearDrop`,
     where `reappearDrop = 8 + (netReceptivity < 0 ? 4 : 0)`; if it regressed enough → keep, signal
     `regressed_after_dismiss`.
2. **Priority.** `priority = −score`, `+3` if in_progress, `+2` if accepted.
3. **Diversity.** Group survivors by **action family** — `cross_cutting`
   {viewpointBalance, echoChamber, openMindedness}, `sources`, `topics`, `register`, `emotion`. Within a
   family keep only the **highest-priority** rec; suppress the rest (reason `overlaps:<family>`). This
   stops two "read the other side" cards from both showing.
4. **Order & rank.** Sort visible recs by `(priority desc, canonical-metric-order asc)` — a fully
   deterministic tie-break — and number them `1..k`. Suppressed recs follow, `visible:false`, `rank:null`.
5. **Explainability.** Every rec gets a `ranking {rank, visible, priority, reason, signals[]}` where
   `signals` lists each applied factor (base priority, lifecycle boost, regression, receptivity,
   diversity). **No hidden factors.**

## Suppression & reappearance (req 3)

- **Completed** recs don't nag. They reappear only if the metric **regresses below the completion bar**
  — handled automatically: RC2.3's reconciler flips a completed rec back to `shown` when
  `is_completed` becomes false, and the ranker then shows it.
- **Dismissed** recs are treated as permanently dismissed **until the metric gets materially worse**
  (`currentScore ≤ firstScore − reappearDrop`) — a deterministic reappearance condition, stricter for a
  net-negative reader.

## Diversity & tie-breaking (req 4)

Overlapping actions are collapsed to the single highest-priority member of the family; the deterministic
tie-break for equal priority is the canonical metric order
(topic, source, reporting, emotional, echo, viewpoint, open-mindedness). Example: `viewpointBalance`
(score 25) and `echoChamber` (score 35) both belong to `cross_cutting`; the worse-scoring
`viewpointBalance` is kept, `echoChamber` is suppressed with reason `overlaps:cross_cutting`.

## Before / after (live ranker output)

Three generated recs — `viewpointBalance` (25), `echoChamber` (35), `sourceDiversity` (30, **dismissed**):

```
BEFORE (generation, all shown):  viewpointBalance · echoChamber · sourceDiversity
AFTER  (ranked + filtered):
  viewpointBalance   visible=true   rank=1
  sourceDiversity    visible=false  reason=dismissed
  echoChamber        visible=false  reason=overlaps:cross_cutting
```

→ only `viewpointBalance` is shown: `sourceDiversity` is suppressed (the reader dismissed it) and
`echoChamber` is suppressed (its cross-cutting action overlaps the higher-priority viewpointBalance).
An **in_progress** rec, by contrast, is promoted above a lower-scoring plain rec (`+3` priority), and a
**net-negative** reader keeps a dismissed rec hidden where a neutral reader would see it reappear.

## Compatibility (req 7)

`ranking` is a **new optional field** on the improvement (`response_model_exclude_none`), present only
for signed-in reports. A consumer that ignores it still sees every generated rec; the updated frontend
renders only `ranking.visible !== false` in `rank` order. `impact`, evidence, lifecycle, and the metric
selection are all unchanged.

## Validation

| Check | Result |
|---|---|
| `pytest tests/test_improvement_ledger.py` | **24 passed** (11 new RC2.4 ranking + integration) |
| `pytest ledger · api_fastapi · api_server · personalize · db_durability · demo_determinism` | **182 passed** |
| Web `tsc --noEmit` | **clean** |
| Web `node --test` | **96 passed** |
| `check:i18n` | **658 keys × 5 languages** |
| `next build` | **succeeds**; `/report` **376 kB** (unchanged) |
| Playwright `health-report.spec` (live engine + web) | **1/1 passed** |

Also fixed under load: a concurrent-insert race on the lifecycle ledger (two simultaneous `/api/report`
fetches) now uses the same savepoint-per-insert pattern as the notifications ledger — no more
`UNIQUE constraint` warning; the write is never lost.

**Requirement-7 demonstrations (tested):**
- **Deterministic ordering** — `test_ranking_is_deterministic`, `test_ranking_base_order_is_worst_metric_first`.
- **Feedback influence** — `test_accepted_and_in_progress_are_promoted`,
  `test_negative_receptivity_makes_dismissal_stickier`.
- **Suppression** — `test_completed_is_suppressed`, `test_dismissed_is_suppressed_and_reappears_only_on_regression`,
  `test_diversity_suppresses_overlapping_action_family`, plus API `test_dismiss_then_report_suppresses_the_recommendation`.
- **Reappearance** — the regression branch of the dismissed test.
- **Backward compatibility** — `test_ranking_backward_compatible_without_lifecycle`,
  `test_anonymous_report_has_no_lifecycle` (no ranking for demo/anon).
- **Explainability** — `test_ranking_exposes_signals_no_hidden_factors`.

## Out of scope (later phases)

Recommendation evaluation and attribution (RC2.5+). Per-metric use of article feedback is deliberately
deferred (it needs the article-metadata association the evaluation phase builds); RC2.4 uses article
feedback only as the documented global receptivity prior above.

---

*RC2.4 modifies only the ranking/filtering stage — generation, evidence, and impact are unchanged.*
