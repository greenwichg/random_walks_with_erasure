# W8A — Behavioral Warm-Start (Design)

**Status:** Design only. Awaiting review. No implementation, no production code, no
existing files modified by this document.

**Scope guardrails (binding):**

- Offline prototype only. No production changes, no serving-path changes, no
  explainability changes, no schema or REPORT CONTRACT changes.
- Nothing in this document is wired into `examples/api_fastapi.py`, `examples/personalize.py`,
  `examples/api_server.py`, or any request path.
- The prototype is a **standalone orchestration script / notebook** that *calls* existing
  library functions in `rwe/` and reuses `examples/eval_mind.py`; it edits none of them.

---

## 0. Why W8A exists (the disproven assumption)

The earlier W8 design assumed MIND articles could be resolved to trusted publisher outlets,
giving a cheap, high-confidence lean axis. The repository audit (see
`docs/` MIND audit trail and the code below) disproved that:

- MIND ships MSN **aggregator** URLs; the original publisher is not in the file
  (`rwe/mind.py:17-18`; `tests/fixtures/mind_demo/news.tsv` has 8 columns, no outlet).
- `rwe/mind.py:158-165` `_outlet_from_url()` returns `""` for any `msn.` host.
- The MSN snapshot resolver is a spike whose recorded result is **HTTP 409 on every
  snapshot** — "a confirmed dead end without Microsoft-issued credentials"
  (`examples/resolve_msn_publisher.py:25-32`).
- No bundled `news_id → outlet` map exists (only the synthetic
  `tests/fixtures/mind_demo/source_map.tsv`).

Because trusted outlet lean cannot be recovered for MIND, W8A drops the outlet axis and
asks a **different, narrower question** below.

---

## 1. Objective and non-goals

**Objective.** Determine, offline, whether **real user click behaviour alone** (from MIND)
can produce a **better recommendation graph** than today's **simulated** click graph — where
"graph" means the bipartite user–item `FeedbackGraph` (`rwe/graph.py:34`) that drives every
RWE recommender via `item_distribution()` (`rwe/graph.py:118`).

**This is a comparison of the base graph, not of the lean axis.** The lean axis (outlets or
`fit_ideology`) is a *separate overlay* used only by the re-ranking step. W8A is about whether
real co-click structure gives better connectivity / diversity / stability than a generative
simulation.

**Non-goals (explicitly out of scope for W8A):**

- Recovering left/right labels for MIND (established impossible from outlets).
- Any change to the served feed, the report, the coach, or explainability.
- Deciding to *ship* a behavioral graph. W8A ends at a **decision gate**, not integration.
- Replacing `simulate_users.py` — it remains the product PoC generator regardless.

---

## 2. Audit answers (Questions 1–6), with code references

### Q1. How `fit_ideology` currently works

Entry point: `MINDData.fit_ideology()` (`rwe/mind.py:322`). Pipeline:

1. It calls `IdeologyModel.fit(self.dataset.matrix, restarts=…)` in **elite-only** mode
   (`rwe/mind.py` inside `fit_ideology`; `rwe/ideology.py:122`). The click matrix `A` is
   passed as the endorsement matrix `R`; there is no content-share matrix `S`, so
   `phi == item positions` and `theta == user positions`.
2. `IdeologyModel` (`rwe/ideology.py:87`) is a **1-D ideal-point model**. For user `u` and
   item `i` it models the click probability as
   `P(click) = σ(−(θ_u − φ_i)² + α_u + β_i)` (`rwe/ideology.py:187-189`): the closer a
   user's latent position `θ_u` is to an item's latent position `φ_i`, the more likely the
   click; `α_u`, `β_i` are per-user / per-item popularity intercepts.
3. Fit is by **Adam gradient ascent** on the data log-likelihood, `n_iter` sweeps
   (`_fit_once`, `rwe/ideology.py:156-206`), L2-regularised by `lam`.
4. `restarts` runs several random inits and keeps the one with the **highest final
   objective** — unsupervised model selection by the model's own likelihood, never by an
   external label (`rwe/ideology.py:138-154`). The objective is non-convex, so this
   stabilises the recovered axis.
5. **Orientation** (`rwe/mind.py`, `fit_ideology` tail): the latent axis sign is arbitrary.
   If `orient_by_lean=True` **and ≥ 3 items already carry a known lean**, the code correlates
   the learned `item_pos` against those known leans and sign-flips to agree, reporting
   `lean_corr`. With fewer than 3 labelled items it cannot orient and `lean_corr` is `None`,
   leaving the global sign arbitrary.
6. Output is an `IdeologyFit` (`user_positions`, `item_positions`, `lean_corr`); the CLI
   stores it via `d.with_ideology(fit)` and serialises with `d.save()`
   (`examples/ingest_mind.py:97-111`; `rwe/mind.py:405`).

**Key point:** `fit_ideology` produces positions **from click behaviour alone (no outlets)**
(`rwe/mind.py:322-324`). Outlet lean is used only for optional *orientation* and as a
*validation* diagnostic — never as the source of positions.

### Q2. Files involved

| Stage | File(s) · symbol | Role |
|---|---|---|
| **MIND ingestion** | `examples/ingest_mind.py` (CLI) → `rwe/mind.py:452` `load_mind` | Parse a MIND dir into a `MINDData` |
| | `rwe/mind.py:101` `_read_news`, `rwe/mind.py:112` `_read_clicks`, `rwe/mind.py:140` `_filter_min` | news.tsv / behaviors.tsv parse + k-core filter |
| **Click matrix construction** | `rwe/mind.py:112` `_read_clicks` → `rwe/data.py:39` `from_interactions` (called at `rwe/mind.py:489`) | `(user_ids, news_ids)` → sparse `Dataset.matrix` |
| | `rwe/data.py:23` `Dataset`, `rwe/data.py:~68` `train_test_split` | Container + held-out split for eval |
| **Latent position estimation** | `rwe/mind.py:322` `MINDData.fit_ideology` → `rwe/ideology.py:87` `IdeologyModel` / `:122` `fit` / `:156` `_fit_once` | Ideal-point fit on the click matrix |
| | `rwe/mind.py:287` `user_positions_from_clicks` | Lean-mean fallback when no fit is stored |
| | `rwe/mind.py:206` `_load_positions_map` | External per-article positions (e.g. `classify_lean.py`) — the text-classifier path |
| **FeedbackGraph construction** | `rwe/graph.py:34` `FeedbackGraph.__init__` (`:47-71`) | Bipartite adjacency `A^G` + transition `P` from the click matrix |
| | `rwe/graph.py:118` `item_distribution` / `:92` `k_step_distribution` | The random-walk signal every recommender consumes |
| | `rwe/mind.py:366` `recommender_inputs` | Prep `(Dataset, θ, item_pos)`; **drops NaN-position items** |
| **(comparison) synthetic graph** | `examples/simulate_users.py`; `examples/api_server.py:531-540` (synthetic boot path) | Generates simulated clicks over a real/synthetic catalog |

### Q3. Which parts depend on outlet information, and which do not

**Do NOT depend on outlets:**

- Click matrix construction — `_read_clicks` reads only History + positive impressions
  (`rwe/mind.py:112-137`). No outlet.
- `FeedbackGraph` — built from the click matrix only; binarises edges, no positions, no
  outlets (`rwe/graph.py:47-71`).
- The random-walk recommendation signal `item_distribution()` — pure graph propagation
  (`rwe/graph.py:118`).
- `fit_ideology` positions — co-click only (`rwe/mind.py:322-324`).

**DO depend on outlets (or a lean axis):**

- The outlet-lean join that fills `item_positions` directly (`rwe/mind.py:495,504`) — this is
  the branch that is ~0% resolvable on MIND.
- `fit_ideology` **orientation** — needs ≥ 3 items with a known lean to fix the sign
  (`rwe/mind.py`, the `orient_by_lean` block). Optional; falls back to arbitrary sign.
- The **re-ranking / erasure** step of the recommenders: `RWEB` uses `item_pos` for
  `is_bridge` and similarity; `RWED` uses item degree (`rwe/random_walk.py`). These act on
  the walk output; they do not build the graph.
- Any ideological-diversity **metric** that needs `item_positions` (e.g.
  `rwe/metrics.py:213` `rec_range_at_k`).

**Consequence:** the base graph and the walk signal are fully behavioral. Outlets/positions
only enter (a) orientation and (b) re-ranking + ideological metrics — all *downstream* of the
graph.

### Q4. What the latent positions represent once outlet labels are removed

`fit_ideology` returns a **standardised 1-D latent coordinate per user and per item** that
best explains **who co-clicks what** (`rwe/ideology.py:187-189`; `rwe/mind.py:322-324`). It is
"the axis that drives co-clicking." On a **political subset** that axis is *usually* ideology —
but the module is explicit that this must be verified, not assumed (`rwe/mind.py` docstring:
"whatever drives co-clicking; on a political subset that is usually ideology — but verify").

Without outlet labels:

- The axis is **unlabelled and sign-arbitrary** — "position 2.1" carries no intrinsic
  "right-wing" meaning; only *relative* distance is meaningful.
- It may capture **topic, popularity, or format** structure rather than ideology if those
  dominate co-clicking on the chosen subset.
- `lean_corr` is the only in-repo check of whether the axis aligns with ideology, and it
  needs ≥ 3 labelled items — which MIND cannot supply from outlets.

So the positions are a **behaviorally-grounded latent similarity coordinate**, not a
"left/right" label.

### Q5. Is the graph still useful without knowing "left" or "right"? **Yes — mostly.**

The graph's usefulness is largely **independent of the lean axis**:

- Base relevance (accuracy, connectivity, long-tail diversity, cold-start) comes from the
  **walk over the click graph** — no positions required (`rwe/graph.py:118`; metrics
  `rwe/metrics.py` `gini_diversity:148`, `catalog_coverage:163`, `ndcg_at_k:132`,
  `personalization:176`).
- What **does** need a labelled axis is the **cross-cutting / bridging** guarantee — RWE-B's
  `is_bridge` and the "opposite side" logic, and any ideological-diversity metric
  (`rec_range_at_k:213`). Without orientation, "bridge to the *other* side" degrades to
  "bridge to a *distant* latent region," which is still diversifying but no longer provably
  left↔right.

**Net:** a behavioral graph is directly useful for recommendation quality and diversity; the
one capability that needs the labelled axis (directional cross-cutting) can still run on the
*relative* axis, with a weaker interpretation until oriented.

### Q6. How this differs from today's simulated graph

Today's graph (`examples/simulate_users.py`) is **generative**: "Items are real; users and
their clicks are synthetic" (`simulate_users.py:6-7`). Each agent has a **known** viewpoint,
and clicks are drawn from a **utility model that already knows the article's lean/outlet/topic**
(`simulate_users.py:14-15`); the output stores `item_positions = GOLD lean, user_positions =
the TRUE synthetic viewpoints` (`simulate_users.py:21-22`).

| | A. Synthetic (today) | B. Behavioral (MIND, W8A) |
|---|---|---|
| Clicks | Sampled from a lean-aware utility model | Real human History + impressions (`rwe/mind.py:112`) |
| Item positions | **Known** (gold lean) | **Inferred** from co-clicks (`fit_ideology`) |
| User positions | **Known** (generated) | Inferred (θ from the fit) |
| Axis meaning | Ideology **by construction** | "Whatever drives co-clicking" — verify |
| Circularity | High: the click model assumes the 1-D lean kernel the recommender then uses | None: clicks are not generated by any model |
| Realism of co-click topology | Only as realistic as the utility model | Real |

The core scientific motivation: the synthetic graph can *only* confirm the assumptions baked
into its generator; the behavioral graph is the first test of the RWE machinery on click
structure nobody designed.

---

## 3. Prototype design (smallest possible)

**Central finding — most of the prototype already exists and is already production-isolated.**
`examples/ingest_mind.py --ideology` performs *ingest → fit latent positions → serialize*, and
`examples/eval_mind.py` performs *load → build `FeedbackGraph` → evaluate* — neither touches
any serving path. W8A therefore adds only a **thin orchestration layer** plus **three new
metrics**, all in a **new** standalone file.

### 3.1 Pipeline (each step maps to existing code)

1. **Ingest MIND → click matrix.**
   `python examples/ingest_mind.py --mind-dir <MIND> --political-only --ideology
   --min-user-clicks 5 --min-item-clicks 5 --out mind_ideo.npz`
   (`examples/ingest_mind.py:97-111`; uses `_read_clicks` → `from_interactions`).
2. **Fit latent positions.** Done inside step 1 by `fit_ideology`
   (`rwe/mind.py:322`; `--ideology-iters`, `--seed`, `--max-cells` control it).
3. **Construct `FeedbackGraph`.** `FeedbackGraph(d.dataset.matrix)` (`rwe/graph.py:34`),
   exactly as `eval_mind._recommenders` does (`examples/eval_mind.py:42`).
4. **Serialize the graph.** `d.save("mind_ideo.npz")` (`rwe/mind.py:405`) already persists the
   matrix + fitted positions; the graph is a pure function of the matrix, so the `.npz` **is**
   the serialized graph. (Optionally also dump `scipy.sparse.save_npz(A^G)` for inspection.)
5. **Build the synthetic comparator (graph A).** Generate `sim_users.npz` via
   `examples/simulate_users.py` (or load an existing one), then `FeedbackGraph` over its
   matrix — identical construction, so A vs B differ only in the clicks.
6. **Evaluate both** (Section 4) with a shared metric battery.

### 3.2 The one new file

`examples/w8a_prototype.py` (or `notebooks/w8a_behavioral_warm_start.ipynb`) — a **new**,
read-only orchestration script that:

- takes two `.npz` inputs (`--synthetic sim_users.npz --behavioral mind_ideo.npz`),
- builds a `FeedbackGraph` for each,
- runs the shared metric battery (reusing `rwe/metrics.py` and `eval_mind` helpers),
- computes the three metrics not yet in the repo (connectivity, stability, homogenization),
- writes a single comparison CSV/JSON and a short printed table.

It imports from `rwe/` and `examples/eval_mind.py`; it **modifies nothing**. Develop and
smoke-test it on the license-free fixture (`tests/fixtures/mind_demo/`, which has both
`news.tsv` and `behaviors.tsv`) before any real-MIND run.

---

## 4. Evaluation metrics (exact computation)

All metrics are computed **offline** on a held-out split
(`rwe/data.py train_test_split`, test_frac 0.3). Where a metric already exists it is reused;
three are new and would live only in the W8A script.

**Reused (already in `rwe/metrics.py`):**

- **Accuracy** — `auc` (`:54`), `hit_rate_at_k` (`:98`), `ndcg_at_k` (`:132`),
  `precision_at_k` (`:104`), `mean_rank` (`:65`): rank held-out positives against the
  recommender's scores for each user; average across users.
- **Recommendation diversity (long-tail)** — `gini_diversity` (`:148`): `1 − Gini` of the
  item recommendation-frequency histogram across all users' top-k. **Catalog coverage**
  (`catalog_coverage:163`): fraction of items that appear in *some* user's list.
  **Personalization** (`:176`): mean pairwise `1 − cosine` between users' top-k sets.
  **Surprisal** (`:199`): self-information of recommended items by popularity.
- **Bridge quality (ideological reach)** — `rec_range_at_k` (`:213`): spread of
  `item_positions` within each user's top-k (needs the fitted axis; interpret as *relative*
  reach when unoriented). Pair with `mean_recommended_position` (`:255`) for shift.

**New (defined here; implemented only in the W8A script):**

- **Graph connectivity.** On the bipartite `A^G` (`rwe/graph.py:60`): (a) fraction of nodes in
  the largest connected component (`scipy.sparse.csgraph.connected_components`); (b) share of
  isolated/degree-0 items via `FeedbackGraph.item_degrees` (`rwe/graph.py:83`). Higher LCC
  share + fewer isolates ⇒ the walk can reach more of the catalog.
- **Graph density.** `A.nnz / (m·n)` from `FeedbackGraph.A` — the click-matrix fill rate; plus
  the degree distributions (`user_degrees` / `item_degrees`, `rwe/graph.py:78-85`) reported as
  median + Gini so a few hubs aren't mistaken for broad density.
- **Recommendation stability.** Re-fit / re-walk under `restarts` seeds (fit) and a bootstrap
  resample of clicks; measure top-k **Jaccard overlap** across seeds per user, averaged.
  Low overlap ⇒ the axis/graph is seed-sensitive (a real risk given the non-convex fit,
  `rwe/ideology.py:138-146`).
- **Cold-start behaviour.** Bucket users by training-click count (e.g. 1–2, 3–5, 6–10, >10);
  report accuracy + coverage per bucket. Directly exercises the k-core threshold
  (`_filter_min`, `rwe/mind.py:140`) and the low-degree walk regime.
- **Homogenization over repeated cycles.** The one metric with no in-repo analogue. Simulate
  T recommendation rounds: each round, append each user's top-1 (or accept-with-probability)
  back into the click matrix, rebuild the `FeedbackGraph`, re-recommend. Track over T:
  catalog coverage (`catalog_coverage`), mean pairwise user-list similarity
  (`personalization`), and `rec_range_at_k`. A collapsing coverage / rising similarity curve
  is echo-chamber homogenization; a flat curve is stability. Run identically on A and B.

**Significance.** Reuse `eval_mind`'s multi-seed averaging + paired Wilcoxon
(`examples/eval_mind.py:94` `_wilcoxon_vs_ref`, `:136` `_per_user_significance`) so A-vs-B
gaps are reported with p-values, not single-seed anecdotes. Use ≥ 7 seeds (the harness's own
guidance, `eval_mind.py` docstring).

---

## 5. Comparison: A (synthetic) vs B (behavioral)

| Dimension | A. Synthetic graph | B. Behavioral (MIND) graph |
|---|---|---|
| **Realism** | Weak — topology reflects the generator's utility model | Strong — real co-click structure |
| **Circularity** | High — clicks assume the same 1-D lean kernel RWE re-ranks on | None — clicks are model-free |
| **Label availability** | Full (gold lean + true θ) — enables clean directional bridging & exact ideological metrics | None — axis inferred, sign arbitrary; directional bridging weakened |
| **Density / connectivity** | Tunable by config (`SimConfig`), can be made dense/connected on demand | Fixed by data; MIND is sparse, long-tailed; more isolates after k-core |
| **Stability** | High (deterministic generator) | At risk — non-convex fit, seed sensitivity (mitigated by `restarts`) |
| **Cold-start realism** | Optimistic (agents always active) | Realistic (real click sparsity) |
| **External validity** | None for the paper (stamped SIMULATION, `simulate_users.py:6-8`) | Real evidence *for MIND*; transfer to the product corpus still unproven |
| **Homogenization test** | Only tests the generator's own dynamics | Tests RWE on real topology — the meaningful result |

**Expected reading.** B should look *worse* on raw accuracy/density (real data is sparse and
noisy) but is the only graph that can *validate* — rather than assume — that RWE's diversity
and anti-homogenization properties hold on structure nobody designed. A is a necessary
control (same construction, known labels), not a competitor to ship.

---

## 6. Implementation plan (dependency-ordered)

### Phase 1 — Read-only investigation *(no code; largely complete)*

1. Confirm the audit answers in Section 2 against current code (done here).
2. Confirm the offline harness runs on the **license-free fixture**
   (`tests/fixtures/mind_demo/`) end-to-end: `ingest_mind.py --ideology` → `eval_mind.py`.
3. Resolve **MIND licensing** before any real-MIND download (Section 7). **Blocking gate for
   Phase 2 on real data.**

### Phase 2 — Offline prototype *(new file only)*

4. Write `examples/w8a_prototype.py` (or the notebook) — orchestration only, imports `rwe/*`
   and `eval_mind` helpers; edits nothing.
5. Implement the three new metrics (connectivity, stability, homogenization) inside that file.
6. Smoke-test entirely on the fixture; pin a seed; assert determinism.

### Phase 3 — Offline evaluation *(runs, no code changes)*

7. Obtain MIND-small (post-licensing), ingest with `--ideology`, serialize `mind_ideo.npz`.
8. Generate / load the synthetic comparator `sim_users.npz`.
9. Run the shared battery + new metrics on A and B; ≥ 7 seeds; Wilcoxon vs A as reference.
10. Produce a single comparison report (CSV/JSON + short prose), including the
    homogenization-over-cycles curves.

### Phase 4 — Decision gate *(no integration)*

11. Evaluate against the criteria in Section 8. Recommend one of: **pursue** (behavioral
    graph beats or matches synthetic on realism-adjusted metrics without homogenizing),
    **iterate** (promising but unstable — tune k-core / restarts / subset), or **park**
    (no advantage, or blocked by licensing/domain-shift).
12. **Stop.** Production integration is a *separate* future milestone with its own design and
    its own serving-change review — explicitly out of W8A.

---

## 7. Risks

- **No outlet labels (confirmed).** Directional cross-cutting and exact ideological-diversity
  metrics lose their "left/right" grounding; they run on the *relative* axis only. Mitigation:
  report relative-reach metrics and treat orientation as a separate, later concern; do **not**
  claim ideology without `lean_corr` evidence (needs ≥ 3 labels MIND can't supply from
  outlets — a small hand-labelled or AllSides-derived seed is the only route, and that is
  out of W8A scope).
- **Arbitrary latent-axis orientation.** The fit's global sign is arbitrary
  (`rwe/mind.py fit_ideology`; `rwe/ideology.py:135-146`). Any A-vs-B comparison must use
  **sign-invariant** metrics (distances, ranges, overlaps), never signed position values.
- **Seed instability / non-convexity.** The ideal-point objective is non-convex; a single fit
  can land in a non-ideological optimum (`rwe/ideology.py:141-146`). Mitigation: `restarts`,
  the stability metric, and reporting variance across ≥ 7 seeds.
- **Transferability.** A graph that works on MIND may not transfer to the product corpus
  (RSS/qbias): different catalog, different click semantics (impressions vs real reads),
  different sparsity. W8A proves nothing about the *live* corpus — only about MIND. State this
  as a hard boundary; transfer is a later, separate question.
- **Domain shift.** MIND is 2019 US MSN news; the product corpus and audience differ. The
  learned axis and topology are period- and platform-specific. Do not extrapolate homogenization
  results across domains without re-running.
- **Licensing (blocking).** MIND has its own Microsoft research license/terms; the dataset
  blob returns 409 "public access is not permitted" (`resolve_msn_publisher.py:26-28`). MIND
  must be obtained through the sanctioned channel and its terms cleared **before** Phase 3.
  The fixture is synthetic and license-free for Phase 1–2 development.
- **Reproducibility.** Determinism depends on pinned seeds across `fit_ideology`,
  `train_test_split`, `restarts`, and the bootstrap resample. Mitigation: a single `--seed`
  threaded everywhere; write a MANIFEST (config + seed + input hashes), mirroring
  `simulate_users.py`'s SIMULATION stamp convention.
- **Compute.** `IdeologyModel` is dense `O(users × items)` per sweep, guarded by `max_cells`
  (`rwe/mind.py fit_ideology` raises above 5e7). Mitigation: `--political-only`, k-core
  filtering, `--sample-users`; keep MIND-small, not MIND-large, for the prototype.

---

## 8. Decision-gate criteria (Phase 4)

Recommend **pursue** only if, on real MIND-small with ≥ 7 seeds:

1. The behavioral graph's accuracy is within a stated tolerance of the synthetic control
   (sparsity-adjusted), **and**
2. Diversity (gini, coverage, personalization) is **no worse**, **and**
3. The homogenization-over-cycles curve is **flat or better** than the synthetic graph's
   (coverage does not collapse; user-list similarity does not climb), **and**
4. Stability (top-k Jaccard across seeds) clears a stated floor.

Otherwise recommend **iterate** or **park**, with the blocking reason named. In all cases the
output is a recommendation and evidence — **not** a production change.

---

## Appendix — file inventory (audit surface)

| File | Relevance to W8A |
|---|---|
| `rwe/mind.py` | Ingestion, click matrix, `fit_ideology`, outlet join, `recommender_inputs` |
| `rwe/ideology.py` | `IdeologyModel` ideal-point fit (latent positions) |
| `rwe/graph.py` | `FeedbackGraph` (behavioral, outlet-free) + random walk |
| `rwe/data.py` | `Dataset`, `from_interactions`, `train_test_split` |
| `rwe/metrics.py` | Accuracy + long-tail + ideological metrics (reuse) |
| `examples/ingest_mind.py` | CLI: ingest + `--ideology` fit + serialize (reuse) |
| `examples/eval_mind.py` | Offline eval harness: graph build + baselines + RQ2/RQ3 + significance (reuse) |
| `examples/simulate_users.py` | The synthetic comparator (graph A) |
| `examples/resolve_msn_publisher.py` | Evidence the outlet path is blocked (409) |
| `tests/fixtures/mind_demo/` | License-free fixture for Phase 1–2 development |

*No production module (`api_fastapi.py`, `api_server.py`, `personalize.py`, `rec_explain.py`,
serving/report/coach paths) is touched by W8A.*
