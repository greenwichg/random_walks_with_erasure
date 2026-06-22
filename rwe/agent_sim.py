"""Agent-based simulation of users browsing a polarized newsfeed web graph.

This module computes an **opposing-viewpoint satisfaction score** per user by
simulating browsing sessions on a ``networkx`` web graph (nodes = webpages,
edges = hyperlinks).  It is a self-contained, agent-based companion to the
co-occurrence/label-propagation model in :mod:`rwe.satisfaction`.

Model
-----
* Each user agent ``i`` has a fixed scalar ideology ``u_i`` on a continuous
  left-right scale (e.g. ``[-2, 2]``).
* Each webpage ``w`` has a scalar ideology equal to the **average ideology of
  the community it belongs to** (communities found by Louvain / Leiden, since
  dense communities tend to share topic and viewpoint).

Trigger condition
    A page ``w`` is *opposite* for user ``i`` iff
    ``sign(u_i) != sign(w)`` **and** ``|w| > epsilon`` (a deadband that excludes
    near-neutral pages).

Satisfaction score
    Counting starts when the agent first lands on an opposite page; it
    increments by 1 for every subsequent page visited *while still inside that
    opposing community*; it stops the instant the agent moves to a page outside
    the community.  The accumulated count is the session's satisfaction score.

Transition policy (during the walk)
    ``P(next = j | current = i) ∝ exp(-alpha * |w_j - u_i|) * edge_weight(i, j)``

    * ``alpha = 0`` -> pure (weighted) random walk; isolates topology effects.
    * ``alpha > 0`` -> confirmation-bias walk (drifts back to the agent's side).
    * ``alpha < 0`` -> "rabbit hole" walk (drifts deeper into opposing content).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np
import networkx as nx


# =========================================================================== #
# STEP 1 -- Graph construction & per-node ideology from community detection
# =========================================================================== #
def detect_communities(G: nx.Graph, method: str = "louvain", weight: str = "weight",
                       resolution: float = 1.0, seed: int = 0) -> dict:
    """Detect communities and return a ``{node: community_index}`` mapping.

    Parameters
    ----------
    method:
        ``"louvain"`` (networkx built-in, default), ``"leiden"`` (requires
        ``leidenalg`` + ``python-igraph``), or ``"label_propagation"``.
    resolution:
        Resolution parameter (higher -> more, smaller communities).
    """
    UG = G.to_undirected() if G.is_directed() else G
    if method == "louvain":
        comms = nx.community.louvain_communities(
            UG, weight=weight, resolution=resolution, seed=seed)
    elif method == "leiden":
        comms = _leiden_communities(UG, weight, resolution, seed)
    elif method == "label_propagation":
        comms = list(nx.community.asyn_lpa_communities(UG, weight=weight, seed=seed))
    else:
        raise ValueError(f"unknown community method: {method!r}")
    node_comm = {}
    for ci, nodes in enumerate(comms):
        for n in nodes:
            node_comm[n] = ci
    return node_comm


def _leiden_communities(UG, weight, resolution, seed):
    """Leiden communities via ``leidenalg`` (optional dependency)."""
    try:
        import igraph as ig
        import leidenalg
    except ImportError as exc:  # pragma: no cover - optional path
        raise ImportError(
            "method='leiden' needs the 'leiden' extra: pip install "
            "'rwe[leiden]' (leidenalg + python-igraph)."
        ) from exc
    nodes = list(UG.nodes())
    idx = {n: i for i, n in enumerate(nodes)}
    edges = [(idx[u], idx[v]) for u, v in UG.edges()]
    weights = [UG[u][v].get(weight, 1.0) for u, v in UG.edges()]
    g = ig.Graph(n=len(nodes), edges=edges)
    part = leidenalg.find_partition(
        g, leidenalg.RBConfigurationVertexPartition, weights=weights,
        resolution_parameter=resolution, seed=seed)
    return [{nodes[i] for i in comm} for comm in part]


def assign_community_ideology(latent_ideology: dict, node_comm: dict):
    """Set each page's ideology to the mean *latent* ideology of its community.

    Parameters
    ----------
    latent_ideology:
        ``{node: latent_scalar}`` -- the raw/observed ideology signal per page.
    node_comm:
        ``{node: community_index}`` from :func:`detect_communities`.

    Returns
    -------
    (node_ideology, community_ideology)
        ``{node: w}`` (the community-averaged ideology used by the simulator) and
        ``{community_index: centroid}``.
    """
    members = defaultdict(list)
    for n, c in node_comm.items():
        members[c].append(latent_ideology[n])
    community_ideology = {c: float(np.mean(v)) for c, v in members.items()}
    node_ideology = {n: community_ideology[node_comm[n]] for n in node_comm}
    return node_ideology, community_ideology


def make_synthetic_web_graph(block_ideologies=(-1.5, 0.0, 1.5), block_size: int = 25,
                             p_in: float = 0.25, p_out: float = 0.01,
                             ideology_noise: float = 0.3, directed: bool = False,
                             seed: int = 0):
    """A planted-partition (stochastic block model) web graph.

    Each block is a dense community with a target ideology centroid; nodes get a
    latent ideology near their block's centroid, and intra-block links are far
    denser than inter-block links so community detection recovers the blocks.

    Returns
    -------
    (G, latent_ideology)
        ``G`` is a ``networkx`` graph with node attributes ``block`` and
        ``latent_ideology``; ``latent_ideology`` is the ``{node: scalar}`` dict.
    """
    rng = np.random.default_rng(seed)
    sizes = [block_size] * len(block_ideologies)
    probs = [[p_in if i == j else p_out for j in range(len(sizes))]
             for i in range(len(sizes))]
    G = nx.stochastic_block_model(sizes, probs, seed=seed, directed=directed)
    latent_ideology = {}
    for n in G.nodes():
        b = G.nodes[n]["block"]
        val = float(block_ideologies[b] + rng.normal(0, ideology_noise))
        latent_ideology[n] = val
        G.nodes[n]["latent_ideology"] = val
    for u, v in G.edges():
        G[u][v].setdefault("weight", 1.0)
    return G, latent_ideology


# =========================================================================== #
# STEP 2-3 -- Session simulator (state machine + transition policy)
# =========================================================================== #
def is_opposite(u_i: float, w: float, epsilon: float) -> bool:
    """Trigger test: ``sign(u_i) != sign(w)`` and ``|w| > epsilon``."""
    return (np.sign(u_i) != np.sign(w)) and (abs(w) > epsilon)


@dataclass
class SessionLog:
    """Record of exactly which pages a user accessed in one session.

    ``pages`` is the ordered trajectory of webpages the user visited (this is
    the "which webpage did the user access" answer); the parallel arrays give,
    per visited page, its ideology, community, the state machine's label
    (``own`` / ``trigger`` / ``tracking`` / ``exited``) and whether that page
    counted toward the satisfaction ``score``.  ``trigger_index`` is the index
    into ``pages`` where opposing-viewpoint exposure began (``None`` if never).
    """

    pages: list
    ideologies: list
    communities: list
    states: list
    counted: list
    score: int
    trigger_index: int | None

    def to_frame(self):
        """Return the per-page session log as a ``pandas.DataFrame``."""
        import pandas as pd
        return pd.DataFrame({
            "step": range(len(self.pages)),
            "page": self.pages,
            "ideology": self.ideologies,
            "community": self.communities,
            "state": self.states,
            "counted": self.counted,
        })


class NewsfeedSimulator:
    """Simulate browsing sessions and score opposing-viewpoint exposure.

    Parameters
    ----------
    G:
        The web graph (``networkx``; directed graphs use out-edges / successors).
    node_ideology:
        ``{node: w}`` ideology per page (typically the community centroid from
        :func:`assign_community_ideology`).
    node_comm:
        ``{node: community_index}``.
    epsilon:
        Deadband for the trigger condition.
    alpha:
        Transition-policy bias (see module docstring).
    """

    def __init__(self, G: nx.Graph, node_ideology: dict, node_comm: dict,
                 epsilon: float = 0.5, alpha: float = 0.0, weight: str = "weight",
                 seed: int = 0):
        self.G = G
        self.epsilon = epsilon
        self.alpha = alpha
        self.weight = weight
        self.rng = np.random.default_rng(seed)

        self.nodes = list(G.nodes())
        self._index = {n: i for i, n in enumerate(self.nodes)}
        self.w = np.array([node_ideology[n] for n in self.nodes], dtype=float)
        self.comm = np.array([node_comm[n] for n in self.nodes], dtype=int)
        # Pre-extract neighbour index lists and edge weights for fast sampling.
        self._nbr, self._nbr_w = [], []
        for n in self.nodes:
            nb = list(G.neighbors(n))   # successors for a DiGraph
            self._nbr.append(np.array([self._index[x] for x in nb], dtype=int))
            self._nbr_w.append(
                np.array([G[n][x].get(weight, 1.0) for x in nb], dtype=float))

    # -- transition policy ----------------------------------------------
    def _next_node(self, cur: int, u_i: float, rng) -> int | None:
        nb = self._nbr[cur]
        if nb.size == 0:
            return None
        logits = -self.alpha * np.abs(self.w[nb] - u_i)
        p = self._nbr_w[cur] * np.exp(logits - logits.max())  # stabilised
        total = p.sum()
        if total <= 0:
            return None
        return int(rng.choice(nb, p=p / total))

    def own_side_nodes(self, u_i: float):
        """Pages that do *not* trigger for the agent (safe session starts)."""
        mask = np.array([not is_opposite(u_i, w, self.epsilon) for w in self.w])
        idx = np.flatnonzero(mask)
        if idx.size == 0:
            idx = np.arange(len(self.nodes))
        return [self.nodes[i] for i in idx]

    # -- session state machine ------------------------------------------
    def _walk_indices(self, start_idx: int, u_i: float, max_steps: int, rng):
        """Yield visited node indices in order (the agent's trajectory)."""
        cur = start_idx
        for _ in range(max_steps):
            yield cur
            nxt = self._next_node(cur, u_i, rng)
            if nxt is None:
                return
            cur = nxt

    def _run_state_machine(self, u_i: float, index_iter):
        """Score a trajectory of visited node indices and record a session log.

        The trajectory may come from the simulated walk *or* from real logged
        browsing data -- the scoring (own-side -> trigger -> tracking ->
        finalised) is identical either way.
        """
        state, score, track_comm, trigger_index = "own", 0, None, None
        pages, ideo, comm, states, counted = [], [], [], [], []
        for step, cur in enumerate(index_iter):
            w, c = float(self.w[cur]), int(self.comm[cur])
            label, did_count = "own", False
            if state == "own":
                if is_opposite(u_i, w, self.epsilon):
                    state, track_comm, score = "tracking", c, 1
                    label, did_count, trigger_index = "trigger", True, step
            elif state == "tracking":
                if c == track_comm:
                    score += 1
                    label, did_count = "tracking", True
                else:
                    label = "exited"        # left the opposing community -> stop
            pages.append(self.nodes[cur]); ideo.append(w); comm.append(c)
            states.append(label); counted.append(did_count)
            if label == "exited":
                break
        log = SessionLog(pages=pages, ideologies=ideo, communities=comm,
                         states=states, counted=counted, score=score,
                         trigger_index=trigger_index)
        return score, log

    def simulate_session(self, u_i: float, start, max_steps: int = 200, rng=None,
                         return_log: bool = False):
        """Run one simulated session.

        Returns the satisfaction ``score`` (default) or, with
        ``return_log=True``, a :class:`SessionLog` recording every page the user
        accessed and how each contributed to the score.
        """
        rng = rng or self.rng
        score, log = self._run_state_machine(
            u_i, self._walk_indices(self._index[start], u_i, max_steps, rng))
        return log if return_log else score

    def score_trajectory(self, u_i: float, pages, return_log: bool = False):
        """Score an externally-supplied trajectory of accessed pages.

        Use this with **real** browsing data: ``pages`` is the ordered list of
        webpage node ids the user actually visited (e.g. from newsfeed
        impression / click logs).  The same state machine that scores simulated
        walks computes the satisfaction score over the real trajectory.
        """
        score, log = self._run_state_machine(
            u_i, (self._index[p] for p in pages))
        return log if return_log else score

    # -- STEP 4: Monte Carlo --------------------------------------------
    def monte_carlo(self, u_i: float, n_trials: int = 200, start_nodes=None,
                    max_steps: int = 200, seed: int | None = None) -> np.ndarray:
        """Return an array of ``n_trials`` session scores for agent ``u_i``.

        Each trial starts from a random ``start_nodes`` entry (own-side pages by
        default), giving the score *distribution* for the agent.
        """
        rng = np.random.default_rng(seed) if seed is not None else self.rng
        if start_nodes is None:
            start_nodes = self.own_side_nodes(u_i)
        start_nodes = list(start_nodes)
        scores = np.empty(n_trials, dtype=float)
        for t in range(n_trials):
            start = start_nodes[rng.integers(len(start_nodes))]
            scores[t] = self.simulate_session(u_i, start, max_steps, rng)
        return scores


# =========================================================================== #
# STEP 5 -- Agent modes
# =========================================================================== #
@dataclass
class AgentResult:
    """Score distribution for one agent."""

    label: str
    ideology: float
    scores: np.ndarray

    @property
    def mean(self) -> float:
        return float(self.scores.mean())

    @property
    def std(self) -> float:
        return float(self.scores.std())

    def summary(self) -> dict:
        return {"agent": self.label, "ideology": self.ideology,
                "mean": self.mean, "std": self.std,
                "median": float(np.median(self.scores)),
                "trigger_rate": float(np.mean(self.scores > 0)),
                "max": float(self.scores.max())}


def run_independent_agents(sim: NewsfeedSimulator, positions, n_trials: int = 200,
                           max_steps: int = 200, seed: int = 0):
    """One agent per ideology position in ``positions`` (distinct nearby users).

    Returns ``{position: AgentResult}``.
    """
    rng = np.random.default_rng(seed)
    out = {}
    for u in positions:
        scores = sim.monte_carlo(u, n_trials=n_trials, max_steps=max_steps,
                                 seed=int(rng.integers(2**31)))
        out[u] = AgentResult(label=f"u={u:+.2f}", ideology=float(u), scores=scores)
    return out


def run_cluster_agents(sim: NewsfeedSimulator, community_ideology: dict,
                       n_trials: int = 200, max_steps: int = 200, seed: int = 0):
    """One agent per community centroid, starting inside its own community.

    Returns ``{community_index: AgentResult}``.
    """
    rng = np.random.default_rng(seed)
    out = {}
    for c, centroid in community_ideology.items():
        starts = [sim.nodes[i] for i in np.flatnonzero(sim.comm == c)]
        scores = sim.monte_carlo(centroid, n_trials=n_trials, start_nodes=starts,
                                 max_steps=max_steps, seed=int(rng.integers(2**31)))
        out[c] = AgentResult(label=f"community {c}", ideology=float(centroid),
                             scores=scores)
    return out


# =========================================================================== #
# STEP 6 -- Alpha sweep
# =========================================================================== #
def alpha_sweep(G: nx.Graph, node_ideology: dict, node_comm: dict, alphas,
                positions, epsilon: float = 0.5, n_trials: int = 200,
                max_steps: int = 200, seed: int = 0):
    """Sweep ``alpha`` and report mean satisfaction per agent.

    Returns a ``pandas.DataFrame`` indexed by ``alpha`` with one column per
    agent position.  Satisfaction should fall as ``alpha`` rises (more
    confirmation bias -> less time in opposing communities).
    """
    import pandas as pd

    rows = []
    for a in alphas:
        sim = NewsfeedSimulator(G, node_ideology, node_comm,
                                epsilon=epsilon, alpha=a, seed=seed)
        res = run_independent_agents(sim, positions, n_trials=n_trials,
                                     max_steps=max_steps, seed=seed)
        rows.append({"alpha": a,
                     **{f"u={u:+.1f}": res[u].mean for u in positions}})
    return pd.DataFrame(rows).set_index("alpha")


# =========================================================================== #
# STEP 7 -- Exposure policy (feedback into the next session)
# =========================================================================== #
def exposure_policy(scores, k: float = 5.0) -> float:
    """Map a satisfaction score (or distribution) to an exposure level ``[0, 1]``.

    Uses a saturating transform ``1 - exp(-mean_score / k)``: a score of 0 maps
    to 0 exposure, and exposure rises toward 1 as the user dwells longer in
    opposing communities.  ``k`` controls sensitivity.
    """
    s = float(np.mean(scores))
    return float(1.0 - np.exp(-max(s, 0.0) / k))


def next_session_opposite_fraction(exposure: float, floor: float = 0.05,
                                   cap: float = 0.6) -> float:
    """Translate an exposure level into the fraction of opposing pages the
    platform seeds into the user's next-session newsfeed.

    Higher satisfaction -> higher exposure -> more opposing content shown next
    time, bounded to ``[floor, cap]`` so no user is fully siloed or flooded.
    """
    return float(floor + np.clip(exposure, 0.0, 1.0) * (cap - floor))
