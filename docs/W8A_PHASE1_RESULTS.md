# W8A — Phase 1 Results (Offline Prototype)

**Status:** Offline prototype complete. Gate **G1 (runs + deterministic)** — the only gate the
demo fixture can decide (`docs/W8_EVALUATION_AND_DECISION_GATE.md`: the demo fixture is for
"pipeline smoke-tests + determinism only. No statistical claim"). No production/serving/API/
report-contract/explainability code was created or modified.

- **Prototype:** `examples/w8a_prototype.py` (new, import-isolated — nothing imports it; it
  imports only existing library code). Reuses `rwe.mind.load_mind`/`fit_ideology`,
  `rwe.ideology.IdeologyModel`, `rwe.graph.FeedbackGraph`, `rwe.data.train_test_split`,
  `rwe.experiment.compare` (the existing eval harness), `rwe.metrics`, and the existing
  recommenders `P3`/`RWED`/`RWEB`. The only new code is offline evaluation utilities
  (graph connectivity/degree via `scipy.sparse.csgraph`), permitted by the Phase-1 brief.
- **Input:** `tests/fixtures/mind_demo/` (4 users × 8 items, 13 clicks).
- **Artifact:** `w8a_behavioral.npz` (click matrix + fitted ideology positions; the
  `FeedbackGraph` is `A^G`, a pure function of the matrix) + `w8a_report.json`, written to
  `--out-dir` (default scratch; not committed — the script regenerates them).
- **Reproduce:** `python examples/w8a_prototype.py --fixture tests/fixtures/mind_demo --out-dir <dir>`
  and `python examples/w8a_prototype.py --det-check`.

---

## Measured results

### Behavioral graph — B (MIND demo fixture)

| Group | Metric | Value |
|---|---|---|
| **Graph size** | users × items · nodes · edges | 4 × 8 · 12 · 13 |
| | density | 0.406 |
| **Connectivity** | connected components | **1** (fully connected) |
| | largest-component frac · isolated items/users | 1.0 · 0 / 0 |
| **Degree** | avg user · avg item · median item | 3.25 · 1.625 · 2.0 |
| **Ideology convergence** | objective first → last (31 logged) | −21.53 → −3.41 |
| | monotone non-decreasing? · gain | **yes** · +18.12 |
| | fitted item positions | `[0.75, −0.08, 0.75, −1.19, −0.33, −0.69, 0.75, −1.19]` |
| | `lean_corr` | `None` (0 outlet labels → axis unoriented) |
| **Diversity (all users)** | gini · coverage · personalization · rec_range | 0.901 · 1.00 · 0.506 · 1.25 |
| **Recommendation accuracy** | within-dataset held-out | **DEGENERATE — n_eval_users = 1** |

### Synthetic graph — A (`simulate_users`, 120 × 300, native gold labels)

| Group | Metric | Value |
|---|---|---|
| **Graph size** | users × items · nodes · edges · density | 120 × 300 · 420 · 1736 · 0.048 |
| **Connectivity** | components · largest frac · isolated items/users | **13** · 0.971 · 9 / 3 |
| **Degree** | avg user · avg item · median item | 14.47 · 5.79 · 4.0 |
| **Diversity (all users)** | gini · coverage · personalization · rec_range | 0.286 · 0.70 · 0.766 · 2.30 |
| **Recommendation accuracy** | within-dataset held-out | n_eval_users = **108** (real) |

**Determinism (G1):** `--det-check` → **PASS** — fitted positions, edge count, and component
count are identical across two independent runs.

**Comparison discipline:** the two graphs are reported **side-by-side, each against its OWN
held-out split** — no cross-dataset delta is computed (per the decision-gate methodology). At a
~30× scale gap the only valid Phase-1 conclusion is structural (both pipelines run and produce
sane graphs); no quality comparison is claimed.

---

## What worked

1. **End-to-end pipeline runs on existing components only** — ingest → `fit_ideology` →
   `FeedbackGraph` → `train_test_split` → `experiment.compare` → serialized artifact, no rewrite
   of any algorithm.
2. **G1 determinism: PASS** — byte-stable fitted positions and graph across runs (pinned seed).
3. **Ideology fit converges cleanly** — a monotone non-decreasing objective ascent (−21.53 →
   −3.41), effectively converged by ~iteration 15, even on 4×8 data.
4. **`fit_ideology` visibly recovers co-click structure** — the co-clicked items N1/N3/N7 collapse
   to ≈ +0.75 and N4/N8 to ≈ −1.19, i.e. the latent axis separates the click clusters. The
   mechanism is doing what it should (qualitative on 4 users; not evidence).
5. **Structure + diversity metrics compute** via the new offline utility and existing
   `rwe.metrics`; the synthetic side produces real, discriminating numbers at scale
   (P3 higher raw accuracy; RWE-D higher gini/coverage/personalization; RWE-B highest directed
   `shift@3` = 2.19 — the expected accuracy↔diversity↔bridging trade-off).
6. **Zero blast radius** — import-isolated; existing MIND tests (19) pass unchanged; no
   production file modified.

## What failed / limitations (honest)

1. **The fixture cannot support any statistical claim.** With `min_interactions=3`, only U1 has
   >3 clicks, so the held-out split has **one** eval user and every recommender scores
   identically. This is expected and pre-registered (demo fixture = plumbing only) — it is a
   scale limitation, **not** an architectural block, so per the brief the prototype proceeds and
   reports it rather than working around it.
2. **No outlet labels → `lean_corr = None` → axis sign-arbitrary.** Exactly the W8 premise; the
   fitted axis is a relative co-click coordinate, not "left/right."
3. **One prototype bug found and fixed during development** — `train_test_split` returns
   `(train_matrix, test_pos)` (a `csr_matrix`), not a `Dataset`; my first draft assumed the
   latter. Fixed in-file; not an architectural issue.
4. **Homogenization-over-cycles (decision-gate metric 8) is intentionally NOT implemented here** —
   it needs a multi-round feedback loop at scale and belongs to Phase 3, not the fixture.

## Unexpected observations

1. **The synthetic graph is itself fragmented** — 13 components, 9 isolated items, largest
   component 0.971 at 120×300. Implication: the **G2 connectivity gate must be read relative to
   the synthetic baseline (~0.97 LCC), not an absolute 1.0** — the demo fixture's perfect
   connectivity (1.0) is an artifact of 4 densely-overlapping users, not a target.
2. **`rec_range` under-credits RWE-B's bridging.** On the synthetic graph RWE-B has the **lowest**
   `rec_range@3` (0.82 vs P3's 2.21) yet the **highest** directed `shift@3` (2.19) — it
   concentrates recommendations on the *opposite* pole rather than spanning the full range.
   Implication for **G3 metric 3 (bridge quality): pair `rec_range` with `directed_shift`**;
   `rec_range` alone misreads a directional bridging recommender.
3. **Convergence is fast and clean at toy scale**, which is encouraging for the dense
   `O(users×items)` fit cost concern — but says nothing about MINDsmall-scale convergence, which
   Phase 3 must measure.

## Recommendations

1. **G1 is satisfied → advance to Phase 2/3 on MIND full** (licensed). G2 (connectivity/density/
   stability) and G3 (quality/diversity/homogenization) are *undecidable* on the fixture and need
   real scale.
2. **Carry two metric-interpretation fixes into the decision gate (doc-only, when convenient):**
   (a) interpret the G2 LCC threshold relative to the synthetic baseline; (b) pair `rec_range`
   with `directed_shift` for G3 bridge quality.
3. **Reuse `--det-check`** as the standing G1 determinism harness on MIND full.
4. **Blockers to clear before Phase 3:** MIND licensing (Microsoft terms) and, for dataset C,
   sufficient production reads — unchanged from `docs/W8B_…` and the decision gate.

---

## Verdict

**Gate G1: PASS.** The W8 behavioral-graph pipeline works end-to-end and deterministically on
real MIND-format input, reusing only existing components, with no production coupling. The demo
fixture proves the plumbing and nothing more — every statistical question is correctly deferred
to MIND full under Gates G2–G3. No blocker was hit; the one issue encountered was a prototype-
local bug, fixed in place. Recommend proceeding to Phase 2/3 once MIND licensing is cleared.

*No production, serving, API, report-contract, or explainability code was created or modified.
Deliverables: `examples/w8a_prototype.py` (offline) and this report.*
