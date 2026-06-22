import numpy as np

from rwe.baselines import ItemKNN, BPRMF
from rwe.data import synthetic_recsys
from rwe.graph import FeedbackGraph


def _graph():
    dataset, _ = synthetic_recsys(n_users=80, n_items=50, seed=0)
    return FeedbackGraph(dataset.matrix)


def test_itemknn_recommends_valid_unseen_items():
    g = _graph()
    rec = ItemKNN(g, k_neighbors=10)
    users = np.arange(g.m)
    recs = rec.recommend(users, top_k=5, exclude_seen=True)
    assert recs.shape == (g.m, 5)
    for u in users:
        seen = set(g.seen_items(u).tolist())
        got = set(int(x) for x in recs[u] if x >= 0)
        assert not (got & seen)
        assert all(0 <= x < g.n for x in got)


def test_bprmf_runs_and_ranks():
    g = _graph()
    rec = BPRMF(g, n_factors=8, n_epochs=5, seed=0)
    users = np.arange(g.m)
    recs = rec.recommend(users, top_k=5)
    assert recs.shape == (g.m, 5)
    # Scores should have the right shape and be finite.
    s = rec.scores(users)
    assert s.shape == (g.m, g.n)
    assert np.isfinite(s).all()


def test_itemknn_self_similarity_excluded():
    g = _graph()
    rec = ItemKNN(g, k_neighbors=5)
    assert np.allclose(np.diag(rec.sim), 0.0)
