import numpy as np
import scipy.sparse as sp

from rwe import FeedbackGraph, data
from rwe.random_walk import RWEB
from rwe.satisfaction import (WebGraph, SatisfactionModel, AdaptiveRWEB,
                              detect_communities, community_viewpoints,
                              satisfaction_score, knn_sparsify)


def _two_clique_graph():
    A = np.zeros((6, 6))
    for clique in ([0, 1, 2], [3, 4, 5]):
        for i in clique:
            for j in clique:
                if i != j:
                    A[i, j] = 1.0
    A[2, 3] = A[3, 2] = 0.1  # single weak bridge between the cliques
    return sp.csr_matrix(A)


def test_label_propagation_finds_two_communities():
    labels = detect_communities(_two_clique_graph(), seed=0)
    assert labels.max() + 1 == 2
    assert labels[0] == labels[1] == labels[2]
    assert labels[3] == labels[4] == labels[5]
    assert labels[0] != labels[3]


def test_community_viewpoints_are_means():
    labels = np.array([0, 0, 1, 1])
    pos = np.array([-2.0, -1.0, 1.0, 3.0])
    views = community_viewpoints(labels, pos)
    assert np.allclose(views, [-1.5, 2.0])


def test_knn_sparsify_reduces_edges():
    dense = sp.csr_matrix(np.ones((10, 10)) - np.eye(10))
    sparse = knn_sparsify(dense, k=2)
    assert sparse.nnz < dense.nnz


def test_satisfaction_counts_run_in_first_opposing_community():
    # communities: 0 = left (-1), 1 = right (+1). user is left -> opposing = {1}.
    labels = np.array([0, 0, 1, 1, 1, 0, 1])
    views = np.array([-1.0, 1.0])
    traj = np.array([0, 1, 2, 3, 4, 5, 6])
    # enters community 1 at index 2, stays for 3 pages (2,3,4), exits at 5.
    assert satisfaction_score(traj, labels, views, user_position=-1.0, center=0.0) == 3


def test_satisfaction_zero_when_no_opposing_visited():
    labels = np.array([0, 0, 0])
    views = np.array([-1.0, 1.0])
    traj = np.array([0, 1, 2])
    assert satisfaction_score(traj, labels, views, user_position=-1.0, center=0.0) == 0


def test_satisfaction_counts_from_start_if_already_opposing():
    labels = np.array([1, 1, 0])
    views = np.array([-1.0, 1.0])
    traj = np.array([0, 1, 2])
    assert satisfaction_score(traj, labels, views, user_position=-1.0, center=0.0) == 2


def _political_setup(seed=1):
    d = data.synthetic_political(n_users=150, n_items=60, seed=seed)
    g = FeedbackGraph(d["matrix"])
    return g, d["user_positions"], d["item_positions"]


def test_webgraph_walk_stays_on_graph():
    g, _, ipos = _political_setup()
    web = WebGraph(g, ipos)
    rng = np.random.default_rng(0)
    start = int(g.seen_items(0)[0])
    traj = web.simulate_walk(start, length=30, rng=rng)
    assert traj[0] == start
    assert all(0 <= node < g.n for node in traj)


def test_exposure_in_unit_interval():
    g, upos, ipos = _political_setup()
    web = WebGraph(g, ipos)
    web.detect_communities(knn=10, seed=0)
    sat = SatisfactionModel(web, upos, n_walks=10, walk_length=30, seed=0)
    exp = sat.exposure(np.arange(g.m))
    assert exp.min() >= 0.0 and exp.max() <= 1.0


def test_adaptive_exposure_monotonic():
    """More exposure must surface more opposite-side content."""
    g, upos, ipos = _political_setup()
    users = np.arange(g.m)
    center = float(np.median(upos))

    def opp_fraction(recs):
        fr = []
        for r, u in zip(recs, users):
            items = r[r >= 0]
            if items.size == 0:
                continue
            opp = np.sign(ipos[items] - center) == -np.sign(upos[u] - center)
            fr.append(opp.mean())
        return np.mean(fr)

    lo = AdaptiveRWEB(g, upos, ipos, exposure=np.zeros(g.m)).recommend(users, top_k=10)
    hi = AdaptiveRWEB(g, upos, ipos, exposure=np.ones(g.m)).recommend(users, top_k=10)
    assert opp_fraction(hi) > opp_fraction(lo)


def test_adaptive_reduces_to_rweb_at_constant_exposure():
    g, upos, ipos = _political_setup()
    users = np.arange(g.m)
    eps_low, eps_high = 0.5, 0.95
    c = 0.4
    eps = eps_low + c * (eps_high - eps_low)
    adaptive = AdaptiveRWEB(g, upos, ipos, exposure=np.full(g.m, c),
                            epsilon_low=eps_low, epsilon_high=eps_high)
    fixed = RWEB(g, upos, ipos, epsilon=eps)
    assert np.array_equal(adaptive.recommend(users, top_k=10),
                          fixed.recommend(users, top_k=10))


def test_adaptive_validates_exposure_length():
    g, upos, ipos = _political_setup()
    try:
        AdaptiveRWEB(g, upos, ipos, exposure=np.ones(g.m - 1))
    except ValueError:
        return
    raise AssertionError("exposure with wrong length should raise ValueError")
