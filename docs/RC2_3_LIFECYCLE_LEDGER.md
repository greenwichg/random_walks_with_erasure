# RC2.3 — Recommendation Ledger & Lifecycle

Persistent identity and lifecycle tracking for the Health Report's improvement recommendations. The
recommendation **generation, selection, ordering, rules, evidence, and impact are unchanged** — RC2.3
only *records* what happens to each recommendation over time, so a later phase can evaluate and (only
then) act on it. No ranking change, no feedback-aware ranking, no evaluation/attribution.

## Implementation summary

- **Stable identity (req 1).** A recommendation's identity is its existing `id` = `imp_<metric>` — the
  recommendation "improve metric X" is materially the same across report regenerations regardless of
  which outlets/topics it names, so it keeps one ledger row and one history. The identity is stable
  through the Estimate→Measured transition (the key is metric-based, not reads-based).
- **Ledger (req 2).** A new `improvement_lifecycle` table: **one row per `(user_id, rec_key)`** holding
  the current `state` plus a timestamp column for every transition. A stamped timestamp is never
  cleared, so the ordered history is reconstructable from the columns; `first_score`/`completed_score`
  anchor the completion rule.
- **State machine (req 3).** A pure, deterministic leaf `improvement_ledger.reconcile(current, ledger,
  scores, now)` computes the new state for every recommendation and the terminal transitions for
  departed ones. `now` is injected (deterministic in tests). The API does the store I/O around it; the
  leaf touches no store/recommender/report.
- **Completion detection (req 4).** Deterministic, from existing report scores only — see the table
  below. Never heuristic.
- **API (req 5).** The report annotates each improvement with an optional `lifecycle` object; three
  endpoints record the explicit reader signals; one endpoint returns the full ledger. All additive and
  backward compatible.
- **Performance (req 6).** Reconciliation runs on the report **already built** (no recompute, no model
  rebuild) and issues a bounded number of tiny upserts (≤ #open recs, typically ≤ 6). Anonymous/demo
  reports skip it entirely.

### Files
`examples/store.py` (table + `list`/`save`/`record_event` methods), **new** `examples/improvement_ledger.py`
(pure reconciler), `examples/api_fastapi.py` (annotation wiring + 3 endpoints + models), `web/types/domain.ts`
(`ImprovementLifecycle`), `web/components/report/report-widgets.tsx` (seed "added to goals" from the
persisted state), `web/mock/data.ts`, **new** `tests/test_improvement_ledger.py`. `api_server.py` is
**unchanged** (generation/selection/impact untouched).

## Schema changes

New table `improvement_lifecycle` (created idempotently by `create_all` — additive, no migration):

| Column | Purpose |
|---|---|
| `id` PK, `user_id` FK(index) | row identity / owner |
| `rec_key` (`imp_<metric>`), `metric` | **stable recommendation identity** (unique per user) |
| `state` | current lifecycle state (see machine below) |
| `first_score`, `current_score`, `completed_score` | completion-rule anchors (set-once for first/completed) |
| `generated_at`, `shown_at`, `viewed_at`, `accepted_at`, `dismissed_at`, `completed_at`, `expired_at`, `superseded_at` | per-transition timestamps (stamped once; `shown_at` refreshes each serve) |
| `superseded_by` | the rec that took this one's slot |
| `created_at`, `updated_at` | bookkeeping |

Unique constraint `(user_id, rec_key)` → idempotent upsert, one history per recommendation.

## Lifecycle state machine

```
                         report generates the rec
                                   │
                                   ▼
   (reader signals)          [ SHOWN ] ◄──────── re-generated next report (shownAt refreshes)
   POST …/{key}/view  ─────►  [ VIEWED ]
   POST …/{key}/accept ────►  [ ACCEPTED ] ──(metric ticks up)──► [ IN_PROGRESS ]
   POST …/{key}/dismiss ───►  [ DISMISSED ]
                                   │
                completion rule met (any state) ─────────────► [ COMPLETED ] (terminal)
                                   │
   rec no longer generated ───────┼── a new rec took its slot ─► [ SUPERSEDED ] (terminal)
                                   └── no replacement ─────────► [ EXPIRED ]    (terminal)
```

- **Generated → Shown**: the server materialises the rec in a report; `generated_at` (set-once) and
  `shown_at` (each serve) are stamped, `first_score` captured.
- **Accepted → In Progress → Completed**: `accept` stamps `accepted_at`; the state becomes
  `in_progress` once the metric rises above `first_score`; `completed` when the completion rule fires.
- **Dismissed**: `dismiss` stamps `dismissed_at`. *(Recorded only — selection is unchanged, so a
  dismissed rec that is still the reader's weakest metric still appears; a future feedback-aware phase
  may hide it.)*
- **Expired / Superseded**: computed at report-time when an open rec is no longer generated —
  **superseded** if a brand-new rec entered this cycle (slot reallocated, `superseded_by` recorded),
  otherwise **expired**. Completion takes precedence over both.
- Terminal states (`completed`/`expired`/`superseded`) are never revived by later reconciliation.

## Completion detection (deterministic — req 4)

A recommendation completes when its **targeted metric's score reaches at least its benchmark (the
typical reader, 50) *and* has improved by at least `COMPLETION_MARGIN` (5) points since the
recommendation was generated**:

```
completed  ⇔  current_score ≥ 50  AND  (current_score − first_score) ≥ 5
```

This is the same transition for every recommendation type (all seven improvable metrics score against
the same benchmark); per type:

| Recommendation (`rec_key`) | Metric transition that completes it |
|---|---|
| `imp_topicDiversity` | Topic Diversity: `first → ≥50 and ≥ first+5` |
| `imp_sourceDiversity` | Source Diversity: `first → ≥50 and ≥ first+5` |
| `imp_viewpointBalance` | Viewpoint Balance: `first → ≥50 and ≥ first+5` |
| `imp_echoChamber` | Echo Chamber Score: `first → ≥50 and ≥ first+5` |
| `imp_emotionalBalance` | Emotional Balance: `first → ≥50 and ≥ first+5` |
| `imp_reportingRatio` | Reporting Ratio: `first → ≥50 and ≥ first+5` |
| `imp_openMindedness` | Open-Mindedness: `first → ≥50 and ≥ first+5` |

Uses only scores already on the report — nothing is inferred.

## API (backward compatible — req 5)

- `GET /api/report` — each improvement gains an optional `lifecycle {recKey, state, firstScore,
  currentScore, …timestamps…}` for a **signed-in** reader (absent for anonymous/demo and older
  payloads; `impact`/evidence/`impactEstimate` unchanged).
- `POST /api/me/recommendations/improvements/{rec_key}/{accept|dismiss|view}` — record a reader signal
  (401 anon, 422 unknown event). Idempotent.
- `GET /api/me/recommendations/improvements` — the reader's full lifecycle ledger, oldest first.

Frontend: the "Add to goals" button seeds its state from the persisted `lifecycle` (accepted /
in_progress / completed → shown as added), so acceptance survives a reload. No new strings.

## Validation results

| Check | Result |
|---|---|
| `pytest tests/test_improvement_ledger.py` | **13 passed** (reconciler + store + API) |
| `pytest ledger · api_fastapi · api_server · personalize · db_durability · demo_determinism` | **171 passed** |
| Web `tsc --noEmit` | **clean** |
| Web `node --test` | **96 passed** |
| `check:i18n` | **658 keys × 5 languages** |
| `next build` | **succeeds**; `/report` **376 kB** (unchanged) |
| Playwright `health-report.spec` (live engine + web) | **1/1 passed** |

**Requirement-7 demonstrations (all tested):**
- **Stable IDs** — `test_report_annotates_lifecycle_and_ids_are_stable`: `recKey == id == imp_<metric>`,
  and `generatedAt` is identical across two report regenerations (set-once).
- **Persistence** — the store round-trip test + `GET /api/me/recommendations/improvements` returns the
  reconciled rows after a report.
- **Regeneration** — two `/api/report` calls keep the same recKeys and generatedAt while `shownAt`
  refreshes.
- **Completion** — `test_...completed` (deterministic rule: 20 → 55 completes) and
  `test_departed_rec_completes_over_supersession`.
- **Dismissal** — `test_accept_and_dismiss_are_recorded_and_reflected` (POST dismiss → state
  `dismissed`, `dismissedAt` set).
- **Supersession** — `test_superseded_when_a_new_rec_takes_the_slot` (`superseded_by` recorded) vs
  `test_expired_when_dropped_with_no_replacement`.
- **Determinism** — `test_reconcile_is_deterministic` (injected `now`).

## Out of scope (later RC2 phases)

Recommendation ranking, feedback-aware ranking, and the evaluation/attribution framework. Dismissal is
recorded but does **not** filter the recommendation set (that would be a selection/ranking change). A
minor known nuance: `first_score` captured during an Estimate is compared against later Measured scores
(both 0–100 percentiles) — acceptable, and noted for the evaluation phase.

---

*RC2.3 records identity + lifecycle only — generation, selection, ordering, rules, evidence, and impact
are unchanged.*
