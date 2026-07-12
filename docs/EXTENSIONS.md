# Extensions — beyond the paper

Two extensions built on top of the RWE implementation (*ours*, not from Paudel &
Bernstein WWW'21), both answering the same product question — *how much opposing content
can each user actually tolerate?* — at two levels of modelling detail. Moved verbatim from
the README; the research-package map for these modules is in
[`PAPER_TO_CODE.md`](PAPER_TO_CODE.md), and the closed-loop safety pieces built on the same
idea (`rwe/opinion_dynamics.py`, `rwe/guardrails.py`) are listed there too.

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
