import numpy as np
import scipy.sparse as sp

from rwe.graph import FeedbackGraph


def _toy():
    # 3 users x 4 items
    A = sp.csr_matrix(np.array([
        [1, 1, 0, 0],
        [0, 1, 1, 0],
        [0, 0, 1, 1],
    ], dtype=float))
    return FeedbackGraph(A)


def test_bipartite_structure():
    g = _toy()
    assert g.m == 3 and g.n == 4 and g.N == 7
    # Top-left and bottom-right blocks of A^G must be zero (bipartite).
    AG = g.A_G.toarray()
    assert np.allclose(AG[:3, :3], 0)
    assert np.allclose(AG[3:, 3:], 0)
    # Symmetric (undirected).
    assert np.allclose(AG, AG.T)


def test_transition_is_row_stochastic():
    g = _toy()
    rowsums = np.asarray(g.P.sum(axis=1)).ravel()
    # Every node here has degree > 0, so rows sum to 1.
    assert np.allclose(rowsums, 1.0)


def test_odd_walk_lands_on_items():
    g = _toy()
    dist = g.k_step_distribution([0, 1, 2], k=3)
    # After an odd number of steps from a user, all mass is on item nodes.
    assert np.allclose(dist[:, : g.m], 0.0)
    assert np.allclose(dist[:, g.m :].sum(axis=1), 1.0)


def test_degrees():
    g = _toy()
    assert np.array_equal(g.user_degrees, [2, 2, 2])
    assert np.array_equal(g.item_degrees, [1, 2, 2, 1])


def test_counts_are_binarized():
    A = sp.csr_matrix(np.array([[5, 0], [0, 3]], dtype=float))
    g = FeedbackGraph(A)
    assert set(np.unique(g.A.data)) <= {1.0}
