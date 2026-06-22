"""Random-walk recommenders, including Random Walks with Erasure (RWE).

Implements Sections 4 and 5 of Paudel & Bernstein (WWW'21):

* :class:`P3`      -- plain ``k``-hop random walk ``v_s P^k`` (baseline ``P^3``).
* :class:`RP3Beta` -- degree-reweighted random walk ``RP^3_beta`` (baseline).
* :class:`RWE`     -- the generic Random Walk with Erasure (Section 4.1).
* :class:`RWED`    -- RWE with the long-tail erasure matrix ``Q^D`` (eq. 4).
* :class:`RWEB`    -- RWE with the political-bridging erasure matrix ``Q^B``
                      (eq. 5).

The erasure process
-------------------
For a single origin ``s`` every erased unit of mass is sent back to ``s`` and
re-walks the *same* ``P^k``.  Writing ``p = P^k[s, :]`` (a distribution over
nodes) and ``q = Q[s, :]`` (the per-destination erasure probabilities), the
iteration of eq. (3) telescopes into a closed form::

    score(s, .) = (p .* (1 - q)) / (1 - sum_j p_j q_j)

i.e. the retained mass at each destination divided by the per-origin constant
``1 - c`` where ``c = sum_j p_j q_j < 1`` is the fraction of mass erased per
pass.  The denominator is constant across items for a given user, so it does
not change the recommendation ranking, but we keep it so that returned scores
match the converged power iteration (see :meth:`RWE.score_iterative`).

Because ``k`` is odd, the walk from a user lands on item nodes, so erasure only
acts on items; the erasure matrices below are therefore expressed over the
``n`` item columns.
"""

from __future__ import annotations

import numpy as np

from .graph import FeedbackGraph

_EPS = 1e-12


class BaseRecommender:
    """Common scoring / ranking machinery for random-walk recommenders."""

    def __init__(self, graph: FeedbackGraph, k: int = 3, batch_size: int = 256):
        if k % 2 == 0:
            raise ValueError("k must be odd so the walk lands on item nodes.")
        self.graph = graph
        self.k = k
        self.batch_size = batch_size

    # Subclasses override one of the two hooks below.
    def _item_erasure(self, user_ids: np.ndarray) -> np.ndarray:
        """Erasure probabilities ``Q`` over items for a batch of users.

        Returns a ``(len(user_ids), n)`` array with entries in ``[0, 1)``.  The
        default (no erasure) reduces RWE to a plain ``k``-hop random walk.
        """
        return np.zeros((len(user_ids), self.graph.n), dtype=float)

    def _score_batch(self, user_ids: np.ndarray) -> np.ndarray:
        """Score all items for a batch of users (rows = users)."""
        p = self.graph.item_distribution(user_ids, self.k)  # (b, n), sums to 1
        q = self._item_erasure(user_ids)                    # (b, n) in [0, 1)
        erased = p * q
        retained = p - erased
        c = erased.sum(axis=1, keepdims=True)               # per-user erased mass
        return retained / np.clip(1.0 - c, _EPS, None)

    # -- public API ------------------------------------------------------
    def scores(self, user_ids) -> np.ndarray:
        """Return an item-score matrix of shape ``(len(user_ids), n)``."""
        user_ids = np.asarray(list(user_ids), dtype=int)
        out = np.empty((user_ids.shape[0], self.graph.n), dtype=float)
        for start in range(0, user_ids.shape[0], self.batch_size):
            batch = user_ids[start : start + self.batch_size]
            out[start : start + len(batch)] = self._score_batch(batch)
        return out

    def recommend(self, user_ids, top_k: int = 10, exclude_seen: bool = True):
        """Return the top-``top_k`` item indices for each user.

        Parameters
        ----------
        exclude_seen:
            If ``True``, items already present in a user's training history are
            removed before ranking (the standard recommendation protocol).

        Returns
        -------
        np.ndarray
            ``(len(user_ids), top_k)`` array of ranked item indices (best
            first).  Missing slots (fewer than ``top_k`` candidates) are ``-1``.
        """
        user_ids = np.asarray(list(user_ids), dtype=int)
        S = self.scores(user_ids)
        if exclude_seen:
            for row, u in enumerate(user_ids):
                S[row, self.graph.seen_items(u)] = -np.inf
        recs = np.full((user_ids.shape[0], top_k), -1, dtype=int)
        for row in range(user_ids.shape[0]):
            valid = np.flatnonzero(np.isfinite(S[row]))
            if valid.size == 0:
                continue
            kk = min(top_k, valid.size)
            top = valid[np.argpartition(-S[row, valid], kk - 1)[:kk]]
            top = top[np.argsort(-S[row, top])]
            recs[row, : top.size] = top
        return recs


class P3(BaseRecommender):
    """Plain ``k``-hop random walk (``P^3`` baseline, ref. [14])."""


class RP3Beta(BaseRecommender):
    """Degree-reweighted ``k``-hop random walk (``RP^3_beta`` baseline, ref. [40]).

    Scores items by ``P^k[s, i] / deg(i) ** beta``; ``beta = 0`` recovers
    :class:`P3`.  Larger ``beta`` promotes low-degree (long-tail) items.
    """

    def __init__(self, graph: FeedbackGraph, beta: float = 0.5, **kwargs):
        super().__init__(graph, **kwargs)
        self.beta = beta
        deg = graph.item_degrees.astype(float)
        self._inv_deg_beta = np.where(deg > 0, deg, 1.0) ** (-beta)

    def _score_batch(self, user_ids: np.ndarray) -> np.ndarray:
        p = self.graph.item_distribution(user_ids, self.k)
        return p * self._inv_deg_beta[None, :]


class RWE(BaseRecommender):
    """Generic Random Walk with Erasure (Section 4.1).

    The erasure behaviour is supplied either by passing ``item_erasure`` (a
    ``(n,)`` vector applied to every user, or a callable mapping a batch of
    user ids to a ``(b, n)`` array) or by subclassing and overriding
    :meth:`_item_erasure`.

    Parameters
    ----------
    item_erasure:
        Base erasure probabilities over items.  Vector, 2-D array indexed by
        user, or callable ``user_ids -> (b, n)`` array.
    v:
        Exponent of the element-wise transform ``Q^{(.v)}`` used during grid
        search (Section 7.3).  ``v = 1`` leaves ``Q`` unchanged.
    """

    def __init__(self, graph: FeedbackGraph, item_erasure=None, v: float = 1.0,
                 **kwargs):
        super().__init__(graph, **kwargs)
        self._erasure_spec = item_erasure
        self.v = v

    def _base_erasure(self, user_ids: np.ndarray) -> np.ndarray:
        spec = self._erasure_spec
        if spec is None:
            return np.zeros((len(user_ids), self.graph.n), dtype=float)
        if callable(spec):
            Q = np.asarray(spec(user_ids), dtype=float)
        else:
            arr = np.asarray(spec, dtype=float)
            Q = np.tile(arr, (len(user_ids), 1)) if arr.ndim == 1 else arr[user_ids]
        return Q

    def _item_erasure(self, user_ids: np.ndarray) -> np.ndarray:
        Q = np.clip(self._base_erasure(user_ids), 0.0, 1.0 - _EPS)
        if self.v != 1.0:
            Q = Q ** self.v
        return np.clip(Q, 0.0, 1.0 - _EPS)

    # Faithful (non-closed-form) reference implementation of eq. (3); used in
    # tests to confirm the closed form matches the converged power iteration.
    def score_iterative(self, user_ids, n_iter: int = 200,
                        tol: float = 1e-12) -> np.ndarray:
        user_ids = np.asarray(list(user_ids), dtype=int)
        p = self.graph.item_distribution(user_ids, self.k)
        q = self._item_erasure(user_ids)
        acc = np.zeros_like(p)
        start_mass = np.ones((user_ids.shape[0], 1))
        for _ in range(n_iter):
            reached = start_mass * p          # distribution from current start
            acc += reached * (1.0 - q)        # retained mass accumulates
            start_mass = (reached * q).sum(axis=1, keepdims=True)
            if np.all(start_mass < tol):
                break
        return acc


class RWED(RWE):
    """RWE for long-tail diversity, strategy *RWE-D* (Section 5.1, eq. 4).

    Uses the degree-based erasure matrix

        Q^D_{beta, (i, j)} = 1 - 1 / D_{j, j} ** beta

    which depends only on the destination item's degree, suppressing popular
    items.  With ``v = 1`` this is mathematically equivalent to
    :class:`RP3Beta` (the retained mass ``p .* (1 - Q^D) = p .* deg^{-beta}``);
    ``v != 1`` lets the strategy deviate, as used in the paper's grid search.
    """

    def __init__(self, graph: FeedbackGraph, beta: float = 0.5, v: float = 1.0,
                 **kwargs):
        deg = graph.item_degrees.astype(float)
        safe_deg = np.where(deg > 0, deg, 1.0)
        q_d = 1.0 - safe_deg ** (-beta)
        super().__init__(graph, item_erasure=q_d, v=v, **kwargs)
        self.beta = beta


class RWEB(RWE):
    """RWE for bridging political viewpoints, strategy *RWE-B* (Section 5.2, eq. 5).

    Given one-dimensional ideological positions ``theta`` for users and
    ``positions`` for items (elites ``phi`` or content ``psi``), the erasure is

        Q^B_{u, i} = sim(u, i)   if item i is a *bridge* w.r.t. user u
                   = epsilon     otherwise

    where ``sim(u, i) = 1 - |pos_i - theta_u| / (max_p - min_p)`` (the
    similarity defined in Section 5.2) and ``epsilon`` is a high erasure value
    that suppresses non-bridge items.

    A bridge is a weak tie whose ideological position is on the *opposite* side
    of the user's own position relative to the population center.  We
    operationalise this as: item ``i`` is a bridge for user ``u`` iff ``i`` and
    ``u`` lie on opposite sides of ``center`` and their distance does not exceed
    ``max_distance`` (the "different but not too far" criterion of Section 5.2).

    Parameters
    ----------
    user_positions, item_positions:
        One-dimensional ideological positions for users (``theta``) and items
        (``phi`` / ``psi``), shapes ``(m,)`` and ``(n,)``.
    epsilon:
        Erasure applied to non-bridge items (paper uses ``0.9``).
    center:
        Reference point splitting "left" from "right".  Defaults to the median
        of ``user_positions``.
    max_distance:
        Maximum ideological gap for an opposite-side item to count as a bridge.
        ``None`` (default) imposes no upper bound (every opposite-side item is a
        candidate bridge).
    """

    def __init__(self, graph: FeedbackGraph, user_positions, item_positions,
                 epsilon: float = 0.9, center: float | None = None,
                 max_distance: float | None = None, v: float = 1.0, **kwargs):
        self.user_positions = np.asarray(user_positions, dtype=float)
        self.item_positions = np.asarray(item_positions, dtype=float)
        self.epsilon = epsilon
        all_pos = np.concatenate([self.user_positions, self.item_positions])
        self._range = max(all_pos.max() - all_pos.min(), _EPS)
        self.center = float(np.median(self.user_positions)) if center is None else center
        self.max_distance = max_distance
        super().__init__(graph, item_erasure=self._compute, v=v, **kwargs)

    def similarity(self, user_ids: np.ndarray) -> np.ndarray:
        """``sim(u, i) = 1 - |pos_i - theta_u| / (max_p - min_p)`` (Section 5.2)."""
        theta = self.user_positions[user_ids][:, None]   # (b, 1)
        pos = self.item_positions[None, :]               # (1, n)
        return 1.0 - np.abs(pos - theta) / self._range

    def is_bridge(self, user_ids: np.ndarray) -> np.ndarray:
        """Boolean ``(b, n)`` mask of bridge items per user."""
        theta = self.user_positions[user_ids][:, None]
        pos = self.item_positions[None, :]
        opposite = (theta - self.center) * (pos - self.center) < 0
        if self.max_distance is not None:
            opposite &= np.abs(pos - theta) <= self.max_distance
        return opposite

    def _compute(self, user_ids: np.ndarray) -> np.ndarray:
        user_ids = np.asarray(user_ids, dtype=int)
        bridge = self.is_bridge(user_ids)
        sim = self.similarity(user_ids)
        Q = np.full(bridge.shape, self.epsilon, dtype=float)
        Q[bridge] = sim[bridge]
        return Q
