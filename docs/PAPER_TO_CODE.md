# Paper → Code Map

The terse fidelity reference for the research implementation: which file implements which
section of Paudel & Bernstein, *Random Walks with Erasure* (WWW '21), and which code symbol
computes each equation. Companions: [`MATH.md`](MATH.md) derives every formula from scratch,
[`TALK.md`](TALK.md) maps the WWW'21 talk slide by slide, and [`PAPER.md`](PAPER.md) is this
repository's own extension paper.

## The `rwe/` package, file by file

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

The research package is self-contained; everything outside it (`examples/`, `web/`,
`deploy/`) is the Information Health product layered on top and deliberately outside this
map — see [`SYSTEM_ARCHITECTURE_GUIDE.md`](SYSTEM_ARCHITECTURE_GUIDE.md).

## Equation → code

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
