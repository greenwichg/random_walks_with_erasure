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
[Extensions](#extensions-beyond-the-paper) below.

## Documentation & guides

> 🔰 **New here?** Read [`GUIDE.md`](GUIDE.md) — a from-scratch, beginner-friendly
> walkthrough of what this project is, why it exists, and how it was built. This
> README is the technical reference.
>
> 📐 **Want the derivations?** [`docs/MATH.md`](docs/MATH.md) works through every
> formula we implement — the erasure closed form, the ideal-point gradients, and
> all the metrics — each mapped to the exact code that computes it.
>
> 📋 **The Information Health Report?** [`docs/HEALTH_REPORT.md`](docs/HEALTH_REPORT.md)
> explains every score in the per-user reading-diet report (topic/source/viewpoint/
> echo + reporting/emotion); [`docs/HEALTH_REPORT_PLAN.md`](docs/HEALTH_REPORT_PLAN.md)
> is its feasibility/scope analysis.
>
> 🔬 **Debugging or regression-testing the recommender?**
> [`docs/RECOMMENDATION_EVALUATION_ENGINE.md`](docs/RECOMMENDATION_EVALUATION_ENGINE.md) — the
> internal evaluation sandbox: inject articles into an ephemeral corpus copy, run the real
> engine, and get rankings, verdicts, and explanations as one frozen-contract JSON report
> (zero production writes, deterministic).
>
> 🎞️ **Comparing against the talk?** [`docs/RWE_talk.pptx`](docs/RWE_talk.pptx)
> is an editable slide deck recreating Bibek Paudel's WWW'21 presentation with a
> code-mapping on each slide (regenerate with `python docs/make_deck.py`), and
> [`docs/TALK.md`](docs/TALK.md) is the concise **verification report** —
> slide → code, with the honest caveats.

## Run it in Colab

> 📓 **Run it on real data in Colab** — each notebook downloads the data, ingests,
> and prints the RQ2/RQ3 tables end-to-end (Drive-cache resilient):
> [![Open MIND in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/greenwichg/random_walks_with_erasure/blob/c41d26fccfa261f7b23a0666d3fa1756f3345f85/notebooks/run_mind_eval.ipynb)
> **MIND** (news; text-lean + co-click axes) ·
> [![Open Politosphere in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/greenwichg/random_walks_with_erasure/blob/c41d26fccfa261f7b23a0666d3fa1756f3345f85/notebooks/run_politosphere_eval.ipynb)
> **Politosphere** (Reddit; behavioral ideal-point axis).
>
> 🤖 **Product PoC — synthetic users, *not* research evidence** — an agent-based simulator
> drives the whole product (RWE recommender → Information Health Report → AI Coach → closed
> loop) on **synthetic** traffic over a real article catalog, to stress-test before real
> users exist (strict research/product separation; see [`docs/PRODUCT_SIMULATION.md`](docs/PRODUCT_SIMULATION.md)):
> [![Open Product Sim in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/greenwichg/random_walks_with_erasure/blob/c41d26fccfa261f7b23a0666d3fa1756f3345f85/notebooks/product_simulation.ipynb)
> **Synthetic-user simulation**.
>
> 🚀 **Run the full product (web app + engine) live** — clones the branch, boots the FastAPI
> engine + the Next.js frontend, and opens a public URL. Onboarding → your **Initial
> Information Health Estimate** works with no credentials (Google sign-in is an optional cell):
> [![Open the app in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/greenwichg/random_walks_with_erasure/blob/23b8fca26dedc1ce4f6bc1455abf9600e9386c16/deploy/information_health_colab.ipynb)
> **Full-stack app demo**.
>
> 🧪 **Validate Information Health metrics independently** — recomputes every dashboard metric from
> Reading History and prints PASS / FAIL drift reports; offline, with an optional *Dashboard
> Verification* mode:
> [![Open Metric Validation in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/greenwichg/random_walks_with_erasure/blob/da845429e9e200a8db2f2347b8a057f5fbcaf13a/deploy/metric_validation_colab.ipynb)
> **Developer validation notebook** (see [`docs/METRIC_PIPELINE.md`](docs/METRIC_PIPELINE.md)).
>
> 🔌 **Browser Extension Playground** — connect the InfoDiet extension to a live Colab-hosted copy
> of the app and watch one real read travel the whole pipeline on a live status dashboard, with
> one-click inspectors and an automated PASS / WAIT / FAIL experiment kit:
> [![Open the Extension Playground in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/greenwichg/random_walks_with_erasure/blob/e65b8fb2878e7999b966c2b70d498d923ba6a436/deploy/browser_extension_playground.ipynb)
> **Extension onboarding & E2E playground** (see [`docs/EXTENSION_E2E_EXPERIMENT.md`](docs/EXTENSION_E2E_EXPERIMENT.md)).
>
> 🎯 **Prove recommendations are justified** (*why was this served?*) — PASS / FAIL invariants over
> nine golden scenarios or your own exported reads: `evidence ⊆ context`, explanations re-derive
> clean, deterministic, history-sensitive. Offline:
> [![Open Recommendation Validation in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/greenwichg/random_walks_with_erasure/blob/7f74176ed56063dfa32143658332cc14ecbc14e3/deploy/rec_validation_colab.ipynb)
> **Recommendation validation notebook**.
>
> 🩺 **Audit Story-Match coverage on real data** (*why wasn't more served?* — the 🎯 notebook's
> complement) — a read-only audit of your corpus: coverage & conversion rates, missed opportunities
> with the exact excluding reason, and a limiter verdict (coverage / ranking / freshness / graph /
> cap / none); byte-identical to `audit_story_coverage.py --report`:
> [![Open Story Coverage Audit in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/greenwichg/random_walks_with_erasure/blob/ddb24917d0d08954bb820093472cd248eb65e864/deploy/story_coverage_audit_colab.ipynb)
> **Story Coverage audit notebook**.

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

## Research implementation

The `rwe/` package implements the paper end to end: every module carries its paper section
(graph & transition matrix §3, the erasure walkers §4–5, the ideal-point model §6, baselines
& metrics §7), and every equation maps to a named symbol in code. The full file-by-file and
equation-by-equation tables live in [`docs/PAPER_TO_CODE.md`](docs/PAPER_TO_CODE.md);
[`docs/MATH.md`](docs/MATH.md) derives each formula from scratch, and
[`docs/TALK.md`](docs/TALK.md) verifies the WWW'21 talk slide by slide. Everything outside
`rwe/` (`examples/`, `web/`, `deploy/`) is the Information Health product built on top —
see [`docs/SYSTEM_ARCHITECTURE_GUIDE.md`](docs/SYSTEM_ARCHITECTURE_GUIDE.md).

## Extensions (beyond the paper)

Two extensions (*ours*, not from the paper) answer the same product question — *how much
opposing content can each user actually tolerate?* — at two levels of detail. Full
pipelines, code samples, and measured results: [`docs/EXTENSIONS.md`](docs/EXTENSIONS.md).

- **Satisfaction-driven adaptive exposure** (`rwe/satisfaction.py`) — project the feedback
  graph onto an item–item webpage graph, detect viewpoint communities, measure how long a
  simulated browse *dwells* in the first opposing community (the **satisfaction score**),
  and map it to a per-user exposure level for `AdaptiveRWEB`. Fixed aggressive bridging
  flips everyone to ~all-opposing content; the adaptive dose protects low-tolerance users
  (0.97 → 0.00 opposite-fraction) while still bridging high-tolerance ones.
- **Agent-based newsfeed browsing simulation** (`rwe/agent_sim.py`) — the same score on an
  explicit networkx web graph with Louvain/Leiden communities, a session state machine, and
  an ideology-biased transition policy (`alpha`: confirmation-bias ↔ rabbit-hole). Mean
  satisfaction falls monotonically as confirmation bias rises, and `score_trajectory()`
  scores real impression logs, not just simulated walks.

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
