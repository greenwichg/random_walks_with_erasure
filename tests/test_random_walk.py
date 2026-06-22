import numpy as np
import scipy.sparse as sp

from rwe.graph import FeedbackGraph
from rwe.random_walk import P3, RP3Beta, RWE, RWED, RWEB


def _graph(seed=0, m=30, n=20, density=0.2):
    rng = np.random.default_rng(seed)
    A = (rng.random((m, n)) < density).astype(float)
    A[np.arange(m), rng.integers(0, n, m)] = 1.0  # ensure no empty users
    return FeedbackGraph(sp.csr_matrix(A))


def test_rwe_closed_form_matches_iteration():
    """The closed-form RWE score must equal the converged power iteration (eq. 3)."""
    g = _graph()
    rng = np.random.default_rng(1)
    q = rng.uniform(0, 0.95, g.n)            # arbitrary per-item erasure
    rec = RWE(g, item_erasure=q)
    users = np.arange(g.m)
    closed = rec.scores(users)
    iterative = rec.score_iterative(users, n_iter=500)
    assert np.allclose(closed, iterative, atol=1e-8)


def test_zero_erasure_equals_p3():
    g = _graph()
    users = np.arange(g.m)
    p3 = P3(g).scores(users)
    rwe0 = RWE(g, item_erasure=np.zeros(g.n)).scores(users)
    assert np.allclose(p3, rwe0)


def test_rwed_v1_matches_rp3beta_ranking():
    """RWE-D with v=1 must produce the same ranking as RP3-beta (paper, Sec 5.1)."""
    g = _graph()
    users = np.arange(g.m)
    beta = 0.7
    rp3 = RP3Beta(g, beta=beta).recommend(users, top_k=8)
    rwed = RWED(g, beta=beta, v=1.0).recommend(users, top_k=8)
    assert np.array_equal(rp3, rwed)


def test_rwed_scores_proportional_to_rp3beta():
    g = _graph()
    users = np.arange(g.m)
    beta = 0.5
    rp3 = RP3Beta(g, beta=beta).scores(users)
    rwed = RWED(g, beta=beta, v=1.0).scores(users)
    # Equal up to a positive per-user constant (the 1/(1-c) normalisation).
    ratio = np.where(rp3 > 0, rwed / np.where(rp3 == 0, 1, rp3), np.nan)
    for row in range(g.m):
        r = ratio[row][~np.isnan(ratio[row])]
        if r.size > 1:
            assert np.allclose(r, r[0])


def test_recommend_excludes_seen():
    g = _graph()
    rec = P3(g)
    users = np.arange(g.m)
    recs = rec.recommend(users, top_k=5, exclude_seen=True)
    for u in users:
        seen = set(g.seen_items(u).tolist())
        assert not (set(int(x) for x in recs[u] if x >= 0) & seen)


def test_even_k_rejected():
    g = _graph()
    try:
        P3(g, k=2)
    except ValueError:
        return
    raise AssertionError("even k should raise ValueError")


def test_rweb_increases_ideological_range():
    """On a homophilous graph, RWE-B widens the ideological spread of the
    recommendation lists relative to plain P3 (the RecRange result, Sec 7.5)."""
    from rwe.data import synthetic_political
    from rwe.metrics import rec_range_at_k

    d = synthetic_political(n_users=200, n_items=80, seed=0)
    g = FeedbackGraph(d["matrix"])
    user_pos, item_pos = d["user_positions"], d["item_positions"]
    users = np.arange(g.m)

    p3_range = rec_range_at_k(P3(g).recommend(users, top_k=10), item_pos)
    rweb_range = rec_range_at_k(
        RWEB(g, user_pos, item_pos, epsilon=0.7).recommend(users, top_k=10),
        item_pos)
    assert rweb_range > p3_range


def test_rweb_bridges_to_opposite_side():
    """RWE-B should pull left-leaning users' recommendations toward the
    opposite (right) side compared with the ideology-blind P3 baseline."""
    from rwe.data import synthetic_political

    d = synthetic_political(n_users=200, n_items=80, seed=0)
    g = FeedbackGraph(d["matrix"])
    user_pos, item_pos = d["user_positions"], d["item_positions"]
    users = np.arange(g.m)
    left = np.flatnonzero(user_pos < -0.5)

    def mean_recpos(recs):
        return np.mean([item_pos[recs[u][recs[u] >= 0]].mean() for u in left])

    p3 = mean_recpos(P3(g).recommend(users, top_k=10))
    rweb = mean_recpos(
        RWEB(g, user_pos, item_pos, epsilon=0.9).recommend(users, top_k=10))
    assert rweb > p3


def test_erasure_bounds_respected():
    g = _graph()
    # Even with extreme inputs, erasure stays in [0, 1).
    rec = RWE(g, item_erasure=np.full(g.n, 5.0), v=2.0)
    Q = rec._item_erasure(np.arange(g.m))
    assert Q.min() >= 0.0 and Q.max() < 1.0
