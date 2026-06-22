# Random Walks with Erasure (RWE)

A clean, tested Python implementation of

> **Random Walks with Erasure: Diversifying Personalized Recommendations on
> Social and Information Networks**
> Bibek Paudel and Abraham Bernstein. *The Web Conference (WWW '21).*
> [arXiv:2102.09635](https://arxiv.org/abs/2102.09635)

RWE is a modified random-walk exploration of the bipartite user–item feedback
graph in which the mass reaching certain nodes is systematically *erased* and
sent back to the walk's origin. By shaping an **erasure matrix `Q`**, the same
framework can diversify recommendations along different axes:

- **RWE-D** — promote **long-tail** items (degree-based erasure).
- **RWE-B** — **bridge political viewpoints** by surfacing reachable content on
  the opposite side of a user's ideological position (ideology-based erasure).

The package also implements the paper's **political ideology detection** (a
joint ideal-point model over endorsement and content-share graphs), the
recommendation **baselines**, and the full suite of **evaluation metrics**.

It additionally provides a **satisfaction-driven adaptive exposure** extension
(`rwe/satisfaction.py`, *not part of the original paper*) that calibrates how
much opposing content each user sees, based on how long they dwell in
opposing-viewpoint communities while browsing — see
[Extension](#extension-satisfaction-driven-adaptive-exposure) below.

---

## Installation

```bash
pip install -e .          # installs numpy, scipy, pandas
pip install -e ".[test]"  # also installs pytest
```

Requires Python ≥ 3.9.

## Quick start

```python
import numpy as np
from rwe import FeedbackGraph, P3, RP3Beta, RWED, RWEB, IdeologyModel

# A binary user-item feedback matrix (m users x n items).
A = ...                       # scipy sparse or dense array
g = FeedbackGraph(A)

# Plain 3-hop random walk and its long-tail-diversifying variants.
recs = P3(g).recommend(user_ids=[0, 1, 2], top_k=10)
recs = RP3Beta(g, beta=0.5).recommend([0, 1, 2], top_k=10)
recs = RWED(g, beta=0.5, v=0.7).recommend([0, 1, 2], top_k=10)

# Political bridging needs one-dimensional ideological positions, which you can
# detect from endorsement / content-share graphs:
res = IdeologyModel().fit(R=endorsement_matrix, S=content_share_matrix)
recs = RWEB(g, user_positions=res.theta, item_positions=res.phi,
            epsilon=0.7).recommend([0, 1, 2], top_k=10)
```

## Run the demos

```bash
python examples/demo_synthetic.py          # full pipeline on synthetic data
python examples/demo_movielens.py          # long-tail benchmark (synthetic fallback)
python examples/demo_movielens.py --ratings /path/to/ml-1m/ratings.dat
python examples/demo_satisfaction.py       # satisfaction-driven adaptive exposure
```

`demo_synthetic.py` reproduces the paper's three headline results on data with a
known ground truth: joint ideology detection beats elite-only (*Result I*),
RWE-D matches RP3-β on accuracy and diversity (*Result II*), and RWE-B produces
the widest ideological spread with a statistically significant KS difference
(*Result III*).

## Run the tests

```bash
pytest -q
```

The suite verifies the core mathematical properties — most notably that the
closed-form RWE score equals the converged power iteration of eq. (3), and that
**RWE-D with `v=1` is exactly RP3-β** (Section 5.1) — plus ideology recovery on
planted data and the behaviour of every metric.

---

## How RWE works (and why scoring is fast)

For a single origin `s`, every erased unit of mass returns to `s` and re-walks
the **same** `Pᵏ`. Writing `p = Pᵏ[s, :]` and `q = Q[s, :]`, the iteration of
eq. (3) telescopes into a closed form:

```
score(s, ·) = (p ⊙ (1 − q)) / (1 − Σⱼ pⱼ qⱼ)
```

i.e. the retained mass at each destination, divided by a per-user constant. The
denominator does not change the ranking, so scoring is a single sparse `Pᵏ`
propagation followed by an element-wise reweighting. `score_iterative()` runs
the literal power iteration and is used in the tests to confirm the two agree.

Because `k` is odd, a walk from a user lands on item nodes, so erasure acts only
on items and the erasure matrices are expressed over the `n` item columns.

## Repository layout

| File | Paper section | Contents |
|------|---------------|----------|
| `rwe/graph.py` | §3 | Bipartite adjacency `A^G` (eq. 1), transition matrix `P = D⁻¹A^G` (eq. 2), `k`-step propagation `vₛPᵏ`. |
| `rwe/random_walk.py` | §4–5 | `P3`, `RP3Beta` baselines; `RWE` (eq. 3, closed-form + iterative); `RWED` (eq. 4); `RWEB` (eq. 5). |
| `rwe/ideology.py` | §6 | Joint / elite-only ideal-point model (eqs. 6–11), Adam-optimised. |
| `rwe/baselines.py` | §7.3 | Item-based CF (`ItemKNN`) and BPR matrix factorisation (`BPRMF`). |
| `rwe/metrics.py` | §7.3, §7.5 | AUC, Mean Rank, Hit-Rate, Precision; Gini-diversity, Avg-degree, Personalization, Surprisal; RecRange, KS test. |
| `rwe/data.py` | §7.1 | Interaction loaders, MovieLens-1M loader, train/test split, synthetic generators. |
| `rwe/experiment.py` | §7 | Evaluation runner and hyper-parameter grid search. |
| `rwe/satisfaction.py` | *extension* | Webpage graph, community detection, satisfaction score, `AdaptiveRWEB`. |

## Equation → code map

| Paper | Code |
|-------|------|
| eq. 1 — `A^G = [[0, A],[Aᵀ, 0]]` | `FeedbackGraph.A_G` |
| eq. 2 — `P = D⁻¹A^G` | `FeedbackGraph.P` |
| eq. 3 — RWE iteration `(Pᵏ ∘ Q)𝟙 ∘ I_.,s` | `RWE._score_batch` (closed form) / `RWE.score_iterative` |
| eq. 4 — `Q^D = 1 − 1/Dᵝ` | `RWED` |
| eq. 5 — `Q^B = sim(u,i)` if bridge else `ε` | `RWEB` (`similarity`, `is_bridge`) |
| eqs. 6–8 — elite-only ideal point | `IdeologyModel.fit(R)` |
| eqs. 9–11 — joint ideal point | `IdeologyModel.fit(R, S)` |
| RecRange@k, KS (§7.5) | `metrics.rec_range_at_k`, `metrics.ks_statistic` |

## Extension: satisfaction-driven adaptive exposure

`rwe/satisfaction.py` adds a feedback loop on top of RWE-B (this is *our*
extension, not from the paper). Instead of exposing the same amount of opposing
content to everyone, it tailors the dose to each user's demonstrated tolerance.

**Pipeline**

1. **Webpage graph & communities.** Project the bipartite feedback graph onto an
   item–item *webpage* graph (pages linked when co-consumed). Detect communities
   with label propagation on a k-NN-sparsified copy (`WebGraph.detect_communities`)
   — dense clusters of pages that share a viewpoint — and label each community by
   its dominant ideology (`community_viewpoints`).
2. **Satisfaction score.** Simulate a user's browsing as a random walk on the
   *full* webpage graph from their own content (`WebGraph.simulate_walk`). The
   **satisfaction score** is the number of pages traversed inside the *first
   opposing community* entered, counted until the walk leaves it
   (`satisfaction_score`); `SatisfactionModel` averages this over several walks.
3. **Adaptive exposure.** Map the score to an exposure level in `[0, 1]`
   (`SatisfactionModel.exposure`) that sets each user's same-side erasure in
   `AdaptiveRWEB`: higher exposure → more opposing content surfaced.

```python
from rwe import WebGraph, SatisfactionModel, AdaptiveRWEB

web = WebGraph(feedback_graph, item_positions)
web.detect_communities(knn=5)
exposure = SatisfactionModel(web, user_positions).exposure(range(feedback_graph.m))
recs = AdaptiveRWEB(feedback_graph, user_positions, item_positions,
                    exposure=exposure).recommend(range(feedback_graph.m), top_k=10)
```

**Why adapt?** A fixed, aggressive bridging strategy flips *every* user — including
those who immediately bounce out of opposing communities — to almost entirely
opposing content. `demo_satisfaction.py` shows the contrast (synthetic data):

| opposite-content fraction | low-tolerance users | high-tolerance users |
|---|---|---|
| fixed RWE-B (ε=0.9) | 0.97 | 0.99 |
| AdaptiveRWEB (satisfaction) | 0.00 | 0.17 |

Low-tolerance users are protected from being overwhelmed while high-tolerance
users are still bridged toward opposing viewpoints — the "different but not too
far" idea of Section 5.2, made per-user.

> **Design notes.** Communities are detected structurally on a k-NN-sparsified
> co-occurrence graph (the raw projection is too dense and collapses label
> propagation to one community), while the surfer walk uses the *full* graph so
> weak inter-community ties remain traversable — matching the paper's
> dense-clusters-plus-weak-ties picture. The score→exposure→content direction
> (more dwell ⇒ more exposure) is a modelling choice and is configurable.

## Datasets

The paper's Twitter datasets (UK2016 / US2016 / DE2017) are not redistributable.
`rwe.data` therefore provides generic loaders (`load_csv`, `from_interactions`),
a MovieLens-1M loader, and synthetic generators with planted ideological
structure (`synthetic_ideology`, `synthetic_political`, `synthetic_recsys`) so
the entire pipeline is runnable out of the box.

## Notes & deviations

- **Metric directionality.** `gini_diversity` is reported as `1 − Gini` of the
  catalog recommendation-frequency distribution, so that — like the paper —
  higher means more diverse (recommendations spread more evenly across items).
- **Bridge definition.** The paper defines a *bridge* informally as a weak tie
  on the opposite ideological side of the user. `RWEB` operationalises this as
  *opposite side of the population center, optionally within `max_distance`*;
  the criterion is overridable.
- **Optimization.** The ideal-point objective is maximised with Adam (the
  all-pairs gradients vary widely in scale across parameter blocks), which is
  more robust than a single fixed step size while implementing the same
  alternating-update objective of eqs. (8)/(11).

## License

MIT.
