"""Satisfaction-driven adaptive exposure to opposing viewpoints.

This module extends Random Walks with Erasure with a feedback loop that
calibrates *per user* how much opposing-viewpoint content is surfaced, based on
a **satisfaction score** measured on the web (page) graph.

Scenario
--------
Project the bipartite user-item feedback graph onto an item-item *webpage*
graph (two pages are linked when co-consumed by users).  Community detection on
this graph reveals dense clusters of pages sharing a topic / viewpoint
(:func:`detect_communities`); each community is labelled by its dominant
ideological position (:func:`community_viewpoints`).

A user's browsing is simulated as a random walk on the webpage graph starting
from their own content (:func:`simulate_walk`).  The **satisfaction score** is
the number of pages the user traverses *inside the first opposing-viewpoint
community they enter*, until they leave that community
(:func:`satisfaction_score`):

* counting begins at the first page whose community opposes the user's own side;
* counting continues while the walk stays in that same community;
* counting stops the moment the walk exits to a page outside it (typically one
  aligned with the user's own perspective).

The score (averaged over several walks by :class:`SatisfactionModel`) is mapped
to an *exposure* level in ``[0, 1]`` which drives :class:`AdaptiveRWEB`: users
who dwell longer in opposing communities are inferred to tolerate more
cross-cutting content and have more of it surfaced; users who bounce straight
back out get a gentler dose ("different but not too far", Section 5.2).
"""

from __future__ import annotations

import numpy as np
import scipy.sparse as sp

from .graph import FeedbackGraph
from .random_walk import RWEB


# --------------------------------------------------------------------------- #
# Community detection on the webpage (item-item) graph
# --------------------------------------------------------------------------- #
def detect_communities(adjacency, max_iter: int = 100, seed: int = 0) -> np.ndarray:
    """Asynchronous **label propagation** community detection (Raghavan et al.).

    Parameters
    ----------
    adjacency:
        Symmetric weighted ``(n, n)`` item-item adjacency (sparse or dense).
    max_iter:
        Maximum number of propagation sweeps.

    Returns
    -------
    np.ndarray
        Contiguous community label (``0 .. K-1``) for each of the ``n`` nodes.
        Each node initially forms its own community and repeatedly adopts the
        (weighted) most frequent label among its neighbours; isolated nodes keep
        their own singleton community.
    """
    A = sp.csr_matrix(adjacency)
    n = A.shape[0]
    rng = np.random.default_rng(seed)
    labels = np.arange(n)
    for _ in range(max_iter):
        changed = False
        for node in rng.permutation(n):
            start, end = A.indptr[node], A.indptr[node + 1]
            nbrs = A.indices[start:end]
            if nbrs.size == 0:
                continue
            weights = A.data[start:end]
            # Weighted vote over neighbour labels; random tie-break.
            nbr_labels = labels[nbrs]
            uniq, inv = np.unique(nbr_labels, return_inverse=True)
            totals = np.zeros(uniq.size)
            np.add.at(totals, inv, weights)
            # Most-frequent neighbour label, ties broken randomly via jitter.
            best = uniq[np.argmax(totals + rng.random(uniq.size) * 1e-9)]
            if labels[node] != best:
                labels[node] = best
                changed = True
        if not changed:
            break
    # Relabel to a contiguous 0..K-1 range.
    _, contiguous = np.unique(labels, return_inverse=True)
    return contiguous


def knn_sparsify(adjacency, k: int) -> sp.csr_matrix:
    """Keep each node's ``k`` strongest edges, then symmetrise.

    Co-occurrence projections are dense (homophilous users co-consume many
    pages), which makes label propagation collapse to a single community.
    Restricting to the strongest links exposes the cluster structure while the
    *full* graph is still used for the surfer walk so weak inter-community ties
    remain traversable.
    """
    C = sp.csr_matrix(adjacency)
    rows, cols, vals = [], [], []
    for i in range(C.shape[0]):
        s, e = C.indptr[i], C.indptr[i + 1]
        idx, w = C.indices[s:e], C.data[s:e]
        if idx.size > k:
            top = np.argpartition(-w, k)[:k]
            idx, w = idx[top], w[top]
        rows.extend([i] * idx.size)
        cols.extend(idx.tolist())
        vals.extend(w.tolist())
    K = sp.coo_matrix((vals, (rows, cols)), shape=C.shape).tocsr()
    return K.maximum(K.T)   # symmetrise (mutual or one-directional kNN)


def community_viewpoints(labels: np.ndarray, item_positions) -> np.ndarray:
    """Mean ideological position of each community (its dominant viewpoint).

    Returns an array indexed by community label.
    """
    item_positions = np.asarray(item_positions, dtype=float)
    n_comm = int(labels.max()) + 1
    sums = np.zeros(n_comm)
    counts = np.zeros(n_comm)
    np.add.at(sums, labels, item_positions)
    np.add.at(counts, labels, 1.0)
    return sums / np.clip(counts, 1, None)


# --------------------------------------------------------------------------- #
# Web (webpage) graph
# --------------------------------------------------------------------------- #
class WebGraph:
    """Item-item *webpage* graph projected from a :class:`FeedbackGraph`.

    Two pages (items) are connected with weight equal to the number of users
    that co-consumed them.  Provides community structure and the surfer
    transition matrix used to simulate browsing.

    Parameters
    ----------
    feedback_graph:
        The bipartite user-item graph the webpage graph is projected from.
    item_positions:
        One-dimensional ideological position of every item (``phi`` / ``psi``).
    """

    def __init__(self, feedback_graph: FeedbackGraph, item_positions):
        self.feedback = feedback_graph
        self.item_positions = np.asarray(item_positions, dtype=float)
        A = feedback_graph.A  # (m, n) binary
        C = (A.T @ A).tocsr()       # co-occurrence counts
        C.setdiag(0)
        C.eliminate_zeros()
        self.adjacency = C
        # Row-normalised surfer transition matrix.
        deg = np.asarray(C.sum(axis=1)).ravel()
        inv = np.zeros_like(deg, dtype=float)
        inv[deg > 0] = 1.0 / deg[deg > 0]
        self.transition = (sp.diags(inv) @ C).tocsr()
        # Pre-extract neighbour lists for fast walk sampling.
        T = self.transition
        self._nbr_idx = [T.indices[T.indptr[i]:T.indptr[i + 1]] for i in range(C.shape[0])]
        self._nbr_p = [T.data[T.indptr[i]:T.indptr[i + 1]] for i in range(C.shape[0])]
        self.labels = None
        self.viewpoints = None

    @property
    def n_items(self) -> int:
        return self.adjacency.shape[0]

    def detect_communities(self, knn: int = 10, **kwargs) -> np.ndarray:
        """Detect communities and their dominant viewpoints; caches both.

        Communities are found on a ``knn``-sparsified copy of the co-occurrence
        graph (see :func:`knn_sparsify`); ``knn=None`` uses the full graph.
        """
        adj = self.adjacency if knn is None else knn_sparsify(self.adjacency, knn)
        self.labels = detect_communities(adj, **kwargs)
        self.viewpoints = community_viewpoints(self.labels, self.item_positions)
        return self.labels

    def simulate_walk(self, start: int, length: int, rng) -> np.ndarray:
        """Simulate a length-``length`` surfer walk from page ``start``."""
        traj = np.empty(length, dtype=int)
        traj[0] = cur = int(start)
        for t in range(1, length):
            idx = self._nbr_idx[cur]
            if idx.size == 0:
                return traj[:t]
            cur = int(rng.choice(idx, p=self._nbr_p[cur]))
            traj[t] = cur
        return traj


# --------------------------------------------------------------------------- #
# Satisfaction score
# --------------------------------------------------------------------------- #
def satisfaction_score(trajectory, labels, viewpoints, user_position,
                       center: float) -> int:
    """Pages traversed inside the first opposing community entered (scenario).

    Parameters
    ----------
    trajectory:
        Sequence of page (item) indices visited by the user.
    labels, viewpoints:
        Community label per page and dominant viewpoint per community.
    user_position:
        The user's own ideological position ``theta_u``.
    center:
        Reference point separating the two sides of the spectrum.

    Returns
    -------
    int
        Length of the contiguous run inside the first community whose viewpoint
        opposes the user's side; ``0`` if the walk never enters one.
    """
    user_sign = np.sign(user_position - center)
    if user_sign == 0:
        user_sign = 1.0
    opposing = np.sign(viewpoints - center) == -user_sign  # per-community mask
    entered = None
    count = 0
    for node in trajectory:
        comm = labels[node]
        if entered is None:
            if opposing[comm]:           # first opposing page -> start counting
                entered, count = comm, 1
        elif comm == entered:            # still inside the opposing community
            count += 1
        else:                            # exited the community -> stop
            break
    return count


class SatisfactionModel:
    """Estimate per-user satisfaction scores and exposure levels.

    Parameters
    ----------
    web_graph:
        A :class:`WebGraph` (communities are detected lazily if needed).
    user_positions:
        Ideological position ``theta`` of every user.
    center:
        Spectrum split point; defaults to the median of ``user_positions``.
    n_walks, walk_length:
        Number of Monte-Carlo browsing walks per user and their length.
    """

    def __init__(self, web_graph: WebGraph, user_positions, center=None,
                 n_walks: int = 20, walk_length: int = 40, seed: int = 0):
        self.web = web_graph
        self.user_positions = np.asarray(user_positions, dtype=float)
        self.center = (float(np.median(self.user_positions))
                       if center is None else center)
        self.n_walks = n_walks
        self.walk_length = walk_length
        self.seed = seed
        if self.web.labels is None:
            self.web.detect_communities()

    def score(self, user_ids) -> np.ndarray:
        """Mean satisfaction score for each user (Monte-Carlo over walks)."""
        user_ids = np.asarray(list(user_ids), dtype=int)
        rng = np.random.default_rng(self.seed)
        labels, views = self.web.labels, self.web.viewpoints
        out = np.zeros(user_ids.shape[0], dtype=float)
        for row, u in enumerate(user_ids):
            seeds = self.web.feedback.seen_items(u)   # the user's own pages
            if seeds.size == 0:
                continue
            scores = []
            for _ in range(self.n_walks):
                start = int(rng.choice(seeds))
                traj = self.web.simulate_walk(start, self.walk_length, rng)
                scores.append(satisfaction_score(
                    traj, labels, views, self.user_positions[u], self.center))
            out[row] = float(np.mean(scores))
        return out

    def exposure(self, user_ids, normalize: str = "minmax",
                 scale: float | None = None) -> np.ndarray:
        """Map satisfaction scores to an exposure level in ``[0, 1]``.

        ``normalize='minmax'`` rescales relative to the evaluated users; passing
        ``scale`` instead divides by a fixed reference (``score / scale``,
        clipped).  Higher exposure -> more opposing content surfaced.
        """
        s = self.score(user_ids)
        if scale is not None:
            return np.clip(s / scale, 0.0, 1.0)
        lo, hi = s.min(), s.max()
        if hi - lo < 1e-12:
            return np.zeros_like(s)
        return (s - lo) / (hi - lo)


# --------------------------------------------------------------------------- #
# Adaptive recommender
# --------------------------------------------------------------------------- #
class AdaptiveRWEB(RWEB):
    """RWE-B whose exposure to opposing content is tuned per user.

    The per-user ``exposure`` (e.g. from :meth:`SatisfactionModel.exposure`)
    sets each user's non-bridge erasure between ``epsilon_low`` and
    ``epsilon_high``: higher exposure -> stronger suppression of same-side
    content -> more opposing-viewpoint items surfaced in the user's list.

    Parameters
    ----------
    exposure:
        Per-user exposure level, shape ``(m,)``, values in ``[0, 1]``.
    epsilon_low, epsilon_high:
        Erasure applied to same-side items at zero and full exposure.
    """

    def __init__(self, graph: FeedbackGraph, user_positions, item_positions,
                 exposure, epsilon_low: float = 0.5, epsilon_high: float = 0.95,
                 **kwargs):
        exposure = np.clip(np.asarray(exposure, dtype=float), 0.0, 1.0)
        if exposure.shape[0] != graph.m:
            raise ValueError("exposure must have one entry per user (length m).")
        epsilon = epsilon_low + exposure * (epsilon_high - epsilon_low)
        self.exposure = exposure
        self.epsilon_low = epsilon_low
        self.epsilon_high = epsilon_high
        super().__init__(graph, user_positions, item_positions,
                         epsilon=epsilon, **kwargs)
