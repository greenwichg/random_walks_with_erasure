"""Non-random-walk recommendation baselines (Section 7.3).

* :class:`ItemKNN` -- item-based collaborative filtering (ref. [46]).
* :class:`BPRMF`   -- Bayesian Personalised Ranking matrix factorisation
                      (a strong latent-factor baseline, refs. [26, 45]).

Both expose the same ``recommend`` interface as the random-walk recommenders in
:mod:`rwe.random_walk`, so they can be dropped straight into the experiment
runner.
"""

from __future__ import annotations

import numpy as np
import scipy.sparse as sp

from .graph import FeedbackGraph


def _top_k_from_scores(scores: np.ndarray, seen, top_k: int,
                       exclude_seen: bool) -> np.ndarray:
    recs = np.full((scores.shape[0], top_k), -1, dtype=int)
    S = scores.astype(float, copy=True)
    if exclude_seen:
        for row, items in enumerate(seen):
            S[row, items] = -np.inf
    for row in range(S.shape[0]):
        valid = np.flatnonzero(np.isfinite(S[row]))
        if valid.size == 0:
            continue
        kk = min(top_k, valid.size)
        top = valid[np.argpartition(-S[row, valid], kk - 1)[:kk]]
        top = top[np.argsort(-S[row, top])]
        recs[row, : top.size] = top
    return recs


class ItemKNN:
    """Item-based collaborative filtering with cosine similarity.

    Parameters
    ----------
    k_neighbors:
        Number of most-similar items kept per item (the rest are zeroed).
    """

    def __init__(self, graph: FeedbackGraph, k_neighbors: int = 50):
        self.graph = graph
        self.k_neighbors = k_neighbors
        A = graph.A  # (m, n), binary
        # Column-normalised item vectors -> cosine similarity = A_n^T A_n.
        col_norm = np.sqrt(np.asarray(A.multiply(A).sum(axis=0)).ravel())
        col_norm[col_norm == 0] = 1.0
        A_norm = A.multiply(sp.csr_matrix(1.0 / col_norm))
        sim = (A_norm.T @ A_norm).toarray()
        np.fill_diagonal(sim, 0.0)
        if k_neighbors is not None and k_neighbors < sim.shape[0]:
            # Keep only the top-k_neighbors per item column.
            keep = np.argpartition(-sim, k_neighbors, axis=1)[:, :k_neighbors]
            mask = np.zeros_like(sim, dtype=bool)
            np.put_along_axis(mask, keep, True, axis=1)
            sim = np.where(mask, sim, 0.0)
        self.sim = sim

    def scores(self, user_ids) -> np.ndarray:
        user_ids = np.asarray(list(user_ids), dtype=int)
        profiles = self.graph.A[user_ids].toarray()   # (b, n)
        return profiles @ self.sim                     # (b, n)

    def recommend(self, user_ids, top_k: int = 10, exclude_seen: bool = True):
        user_ids = np.asarray(list(user_ids), dtype=int)
        S = self.scores(user_ids)
        seen = [self.graph.seen_items(u) for u in user_ids]
        return _top_k_from_scores(S, seen, top_k, exclude_seen)


class BPRMF:
    """Bayesian Personalised Ranking matrix factorisation (ref. [45]).

    Learns user/item latent factors by SGD on the BPR pairwise ranking loss.

    Parameters
    ----------
    n_factors, lr, reg, n_epochs:
        Standard BPR hyper-parameters (latent dimension, learning rate, L2
        regularisation, number of passes over the sampled triples).
    """

    def __init__(self, graph: FeedbackGraph, n_factors: int = 32, lr: float = 0.05,
                 reg: float = 0.01, n_epochs: int = 30, seed: int = 0):
        self.graph = graph
        self.n_factors = n_factors
        self.lr = lr
        self.reg = reg
        self.n_epochs = n_epochs
        self.seed = seed
        self._fit()

    def _fit(self):
        rng = np.random.default_rng(self.seed)
        m, n = self.graph.m, self.graph.n
        self.U = rng.normal(0, 0.1, (m, self.n_factors))
        self.V = rng.normal(0, 0.1, (n, self.n_factors))
        A = self.graph.A
        # Per-user positive items; users with no items are skipped.
        pos_items = [A.indices[A.indptr[u]:A.indptr[u + 1]] for u in range(m)]
        users_with_pos = np.array([u for u in range(m) if pos_items[u].size > 0])
        if users_with_pos.size == 0:
            return
        n_samples = max(1, A.nnz)
        for _ in range(self.n_epochs):
            us = rng.choice(users_with_pos, size=n_samples)
            for u in us:
                items = pos_items[u]
                i = items[rng.integers(items.size)]
                j = rng.integers(n)
                while j in items:
                    j = rng.integers(n)
                self._update(u, i, j)

    def _update(self, u, i, j):
        uf, vi, vj = self.U[u], self.V[i], self.V[j]
        x = uf @ (vi - vj)
        sig = 1.0 / (1.0 + np.exp(x))   # sigmoid(-x) = d/dx softplus gradient
        self.U[u] += self.lr * (sig * (vi - vj) - self.reg * uf)
        self.V[i] += self.lr * (sig * uf - self.reg * vi)
        self.V[j] += self.lr * (-sig * uf - self.reg * vj)

    def scores(self, user_ids) -> np.ndarray:
        user_ids = np.asarray(list(user_ids), dtype=int)
        return self.U[user_ids] @ self.V.T

    def recommend(self, user_ids, top_k: int = 10, exclude_seen: bool = True):
        user_ids = np.asarray(list(user_ids), dtype=int)
        S = self.scores(user_ids)
        seen = [self.graph.seen_items(u) for u in user_ids]
        return _top_k_from_scores(S, seen, top_k, exclude_seen)
