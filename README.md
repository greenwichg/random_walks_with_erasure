# Random Walks with Erasure (RWE)

A clean, tested Python implementation of

> **Random Walks with Erasure: Diversifying Personalized Recommendations on
> Social and Information Networks**
> Bibek Paudel and Abraham Bernstein. *The Web Conference (WWW '21).*
> [arXiv:2102.09635](https://arxiv.org/abs/2102.09635)

> 🔰 **New here?** Read [`GUIDE.md`](GUIDE.md) — a from-scratch, beginner-friendly
> walkthrough of what this project is, why it exists, and how it was built. This
> README is the technical reference.
>
> 📐 **Want the derivations?** [`docs/MATH.md`](docs/MATH.md) works through every
> formula we implement — the erasure closed form, the ideal-point gradients, and
> all the metrics — each mapped to the exact code that computes it.
>
> 🩺 **The Information Health Report?** [`docs/HEALTH_REPORT.md`](docs/HEALTH_REPORT.md)
> explains every score in the per-user reading-diet report (topic/source/viewpoint/
> echo + reporting/emotion); [`docs/HEALTH_REPORT_PLAN.md`](docs/HEALTH_REPORT_PLAN.md)
> is its feasibility/scope analysis.
>
> 🎞️ **Comparing against the talk?** [`docs/RWE_talk.pptx`](docs/RWE_talk.pptx)
> is an editable slide deck recreating Bibek Paudel's WWW'21 presentation with a
> code-mapping on each slide (regenerate with `python docs/make_deck.py`), and
> [`docs/TALK.md`](docs/TALK.md) is the concise **verification report** —
> slide → code, with the honest caveats.

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
python examples/demo_agent_sim.py          # agent-based newsfeed browsing simulation
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
| `rwe/metrics.py` | §7.3, §7.5, App. A.1 | AUC, Mean Rank, Hit-Rate, Precision; Gini-diversity, Avg-degree, Personalization, Surprisal; RecRange, KS test; ideological shift + position-weighted (UW/TW) diversity. |
| `rwe/data.py` | §7.1 | Interaction loaders, MovieLens-1M loader, train/test split, synthetic generators. |
| `rwe/experiment.py` | §7 | Evaluation runner and hyper-parameter grid search. |
| `rwe/satisfaction.py` | *extension* | Webpage graph, community detection, satisfaction score, `AdaptiveRWEB`. |
| `rwe/agent_sim.py` | *extension* | Agent-based newsfeed browsing simulation (networkx + Louvain/Leiden). |
| `rwe/opinion_dynamics.py` | *extension* | Polarization simulation: does opposite-view exposure converge or diverge opinions? |
| `rwe/guardrails.py` | *extension* | Closed-loop backfire/engagement monitors that cut the dose when exposure starts to polarize. |

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
| Shift, weighted diversity (App. A.1) | `metrics.directed_shift`, `metrics.weighted_position` (UW/TW-Recs), `metrics.weighted_shift` (UW/TW-Shift), `metrics.weighted_range` (UW-Range) |

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

## Extension: agent-based newsfeed browsing simulation

`rwe/agent_sim.py` is a second, **agent-based** take on the satisfaction score
(also *our* extension). Where `satisfaction.py` works on the co-occurrence
projection, this module operates directly on a **networkx web graph** (nodes =
webpages, edges = hyperlinks) with **Louvain/Leiden** communities, an explicit
session **state machine**, and a tunable, ideology-biased **transition policy**.

**Model.** Each agent `i` has a fixed ideology `u_i`; each page's ideology `w`
is the mean ideology of its community. A page is *opposite* iff
`sign(u_i) != sign(w)` **and** `|w| > epsilon` (a deadband excluding centrist
pages). The walk follows

```
P(next = j | current = i) ∝ exp(-alpha * |w_j - u_i|) * edge_weight(i, j)
```

with `alpha = 0` a pure (topology-only) walk, `alpha > 0` a confirmation-bias
walk (drifts back to the agent's side), and `alpha < 0` a "rabbit hole" walk.

**Satisfaction score.** A session runs the state machine
`own-side -> trigger -> tracking -> finalized`: counting starts at the first
opposite page and increments per page while the agent stays in that opposing
community, stopping the instant it leaves. `monte_carlo` returns the full score
*distribution* per agent.

```python
from rwe.agent_sim import (make_synthetic_web_graph, detect_communities,
                           assign_community_ideology, NewsfeedSimulator,
                           run_independent_agents)

G, latent = make_synthetic_web_graph(block_ideologies=(-1.5, 0.0, 1.5), seed=0)
node_comm = detect_communities(G, method="louvain")        # or "leiden"
node_ideo, comm_ideo = assign_community_ideology(latent, node_comm)

sim = NewsfeedSimulator(G, node_ideo, node_comm, epsilon=0.5, alpha=0.0)
agents = run_independent_agents(sim, positions=[-1.5, 1.5], n_trials=400)
print(agents[-1.5].summary())           # mean / std / median / trigger_rate
```

**Validation — alpha sweep.** Mean satisfaction falls monotonically as
confirmation bias rises (synthetic 3-block graph, `demo_agent_sim.py`):

| alpha | -1.0 | -0.5 | 0.0 | 0.5 | 1.0 | 2.0 |
|---|---|---|---|---|---|---|
| left agent (u=-1.5) | 96.2 | 38.6 | 13.2 | 3.9 | 0.9 | 0.06 |

The score then feeds an exposure policy (`exposure_policy` ->
`next_session_opposite_fraction`) that sets how much opposing content to seed in
the next session. Two agent modes are supported: **independent agents** (users
at distinct nearby ideologies) and **cluster agents** (one per community
centroid). Communities can be found with Louvain (default) or, with the optional
`rwe[leiden]` extra, Leiden.

**Which pages did the user access?** `simulate_session(..., return_log=True)`
returns a `SessionLog` — the ordered trajectory of visited pages plus each
page's ideology, community, state (`own`/`trigger`/`tracking`/`exited`) and
whether it counted toward the score (`log.to_frame()` for a table). Because the
scoring state machine is independent of where the trajectory comes from,
`score_trajectory(u_i, pages)` scores an **externally supplied** list of pages —
use it with real newsfeed impression/click logs instead of the simulated walk.

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
- **Shift / weighted diversity (App. A.1).** The exact appendix normalisation is
  not in the main paper, so `directed_shift`, `weighted_shift` and
  `weighted_range` follow the two properties stated in the WWW'21 talk
  (Results III/IV): recommendations should pull a user toward the *opposite*
  side (signed shift), and bridging/range should count more for *extreme* users
  (weighting by `|position − center|`). `UW` uses the user's own position as the
  reference, `TW` the mean training-item position.

## License

MIT.
