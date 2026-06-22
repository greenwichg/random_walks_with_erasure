"""Bipartite user-item feedback graph and random-walk propagation.

This module implements the preliminaries from Section 3 of

    Bibek Paudel and Abraham Bernstein. 2021.
    "Random Walks with Erasure: Diversifying Personalized Recommendations
    on Social and Information Networks." WWW '21.

Concretely it builds

* the user-item feedback matrix ``A`` (Section 3.1),
* the bipartite adjacency matrix

      A^G = [[0, A], [A^T, 0]]                                   (eq. 1)

  of dimension ``(m + n) x (m + n)``, and
* the row-normalized transition-probability matrix

      P = D^{-1} A^G,  where D_ii = sum_j A^G_ij                 (eq. 2)

and provides ``k``-step random-walk propagation ``v_s P^k`` used by every
recommender in :mod:`rwe.random_walk`.

Nodes are indexed so that user ``u`` (``0 <= u < m``) is node ``u`` and item
``i`` (``0 <= i < n``) is node ``m + i`` in the joint ``(m + n)`` space.
"""

from __future__ import annotations

import numpy as np
import scipy.sparse as sp


class FeedbackGraph:
    """Bipartite user-item feedback graph.

    Parameters
    ----------
    feedback:
        ``(m, n)`` user-item feedback matrix (dense array or any SciPy sparse
        matrix).  Non-zero entries denote observed (implicit) feedback.
    binarize:
        If ``True`` (default, matching the paper's "unweighted and undirected
        graph"), every observed edge gets weight 1 regardless of its count.
    """

    def __init__(self, feedback, binarize: bool = True):
        A = sp.csr_matrix(feedback, dtype=float)
        A.eliminate_zeros()
        if binarize:
            A = A.copy()
            A.data[:] = 1.0
        self.A = A
        self.m, self.n = A.shape
        self.N = self.m + self.n

        # Bipartite adjacency A^G  (eq. 1).
        zero_uu = sp.csr_matrix((self.m, self.m))
        zero_ii = sp.csr_matrix((self.n, self.n))
        self.A_G = sp.bmat(
            [[zero_uu, A], [A.T, zero_ii]], format="csr"
        )

        # Degree matrix D and transition matrix P = D^{-1} A^G  (eq. 2).
        degree = np.asarray(self.A_G.sum(axis=1)).ravel()
        self.degree = degree
        inv = np.zeros_like(degree)
        nz = degree > 0
        inv[nz] = 1.0 / degree[nz]
        D_inv = sp.diags(inv)
        self.P = (D_inv @ self.A_G).tocsr()
        # Pre-transpose once: we propagate dense batches as (P^T @ X^T)^T which
        # keeps the heavy multiply as a sparse-times-dense product.
        self._P_T = self.P.T.tocsr()

    # -- degrees ---------------------------------------------------------
    @property
    def user_degrees(self) -> np.ndarray:
        """Number of items each user interacted with."""
        return self.degree[: self.m]

    @property
    def item_degrees(self) -> np.ndarray:
        """Number of users that interacted with each item (item popularity)."""
        return self.degree[self.m :]

    # -- propagation -----------------------------------------------------
    def _propagate_once(self, X: np.ndarray) -> np.ndarray:
        """One random-walk step for a dense batch ``X`` of shape ``(b, N)``."""
        return np.asarray(self._P_T @ X.T).T

    def k_step_distribution(self, user_ids, k: int) -> np.ndarray:
        """Return ``v_s P^k`` for the given seed *users*.

        Parameters
        ----------
        user_ids:
            Iterable of user indices (``0 <= u < m``) used as walk origins.
        k:
            Number of random-walk steps.  Odd ``k`` lands the walk on item
            nodes (the bipartite graph alternates sides each step).

        Returns
        -------
        np.ndarray
            Dense ``(len(user_ids), N)`` array; row ``b`` is the mass
            distribution over all ``N`` nodes after ``k`` steps from
            ``user_ids[b]``.
        """
        user_ids = np.asarray(list(user_ids), dtype=int)
        b = user_ids.shape[0]
        X = np.zeros((b, self.N), dtype=float)
        X[np.arange(b), user_ids] = 1.0
        for _ in range(k):
            X = self._propagate_once(X)
        return X

    def item_distribution(self, user_ids, k: int) -> np.ndarray:
        """``k``-step distribution restricted to item nodes, shape ``(b, n)``."""
        return self.k_step_distribution(user_ids, k)[:, self.m :]

    # -- convenience -----------------------------------------------------
    def seen_items(self, user_id: int) -> np.ndarray:
        """Item indices the user already interacted with (training items)."""
        start, end = self.A.indptr[user_id], self.A.indptr[user_id + 1]
        return self.A.indices[start:end]
