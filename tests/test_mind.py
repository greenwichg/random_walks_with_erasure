"""Tests for the MIND loader + ideology ingestion (rwe/mind.py).

Runs against a tiny fixture (tests/fixtures/mind_demo) so the parser, the
political tagger, the outlet-lean join and the downstream RWE plumbing are all
exercised without the multi-GB MIND download.
"""

from pathlib import Path

import numpy as np
import pytest
import scipy.sparse as sp

from rwe import FeedbackGraph, RWEB, P3, data, experiment
from rwe.data import Dataset
from rwe.mind import (DEFAULT_LEAN, MINDData, load_mind, load_lean_table, _norm)

FIX = Path(__file__).parent / "fixtures" / "mind_demo"


def _mind_from(matrix, leans, political=None):
    """Build a minimal MINDData around a click matrix + planted item leans."""
    m, n = matrix.shape
    obj = lambda: np.array([""] * n, dtype=object)
    ds = Dataset(matrix=matrix.tocsr(),
                 user_ids=np.array([f"u{i}" for i in range(m)]),
                 item_ids=np.array([f"i{j}" for j in range(n)]))
    pol = np.ones(n, bool) if political is None else np.asarray(political)
    return MINDData(ds, obj(), obj(), obj(), obj(), pol, np.asarray(leans, float))


@pytest.fixture
def mind():
    return load_mind(FIX, source_map=FIX / "source_map.tsv")


def _pos(d, news_id):
    j = int(np.flatnonzero(d.dataset.item_ids == news_id)[0])
    return d.item_positions[j], bool(d.political[j])


def test_shapes_and_matrix(mind):
    assert (mind.n_users, mind.n_items) == (4, 8)        # U1..U4, N1..N8
    # U1 clicked history N1,N2 + positive impressions N3,N7 -> 4 items
    A = mind.dataset.matrix.tocsr()
    u1 = int(np.flatnonzero(mind.dataset.user_ids == "U1")[0])
    assert A[u1].nnz == 4
    assert set(A.data) == {1.0}                          # binary implicit feedback


def test_political_mask(mind):
    political = {nid for nid in mind.dataset.item_ids if _pos(mind, nid)[1]}
    assert political == {"N1", "N2", "N3", "N7"}         # politics + elections only
    # newsworld / sports / finance / lifestyle are not political
    assert _pos(mind, "N6")[1] is False


def test_outlet_lean_join(mind):
    assert _pos(mind, "N1")[0] == 2.0                    # Fox News  -> right
    assert _pos(mind, "N2")[0] == -1.0                   # CNN       -> lean-left
    assert _pos(mind, "N3")[0] == -2.0                   # MSNBC     -> left
    assert _pos(mind, "N5")[0] == 0.0                    # Reuters   -> center
    assert np.isnan(_pos(mind, "N4")[0])                 # ESPN: no lean in table
    assert np.isnan(_pos(mind, "N8")[0])                 # N8: no outlet in source map


def test_user_positions_from_clicks(mind):
    theta = mind.user_positions_from_clicks(fill=0.0)
    u1 = int(np.flatnonzero(mind.dataset.user_ids == "U1")[0])
    # U1 clicked N1(+2), N2(-1), N3(-2), N7(+2) -> mean 0.25
    assert theta[u1] == pytest.approx(0.25)


def test_political_subset_and_end_to_end(mind):
    sub = mind.political_subset(require_lean=True)
    assert sub.n_items == 4                              # N1, N2, N3, N7
    assert sub.n_users == 3                              # U4 clicked none of them
    assert np.all(~np.isnan(sub.item_positions))
    # the subset plugs straight into FeedbackGraph + RWEB
    g = FeedbackGraph(sub.dataset.matrix)
    recs = RWEB(g, sub.user_positions_from_clicks(fill=0.0), sub.item_positions,
                epsilon=0.9).recommend(range(sub.n_users), top_k=3)
    assert recs.shape == (3, 3)


def test_without_source_map_still_builds():
    d = load_mind(FIX)                                   # no outlet -> no lean
    assert d.n_items == 8
    assert d.summary()["items_with_lean"] == 0
    assert np.all(np.isnan(d.item_positions))
    assert d.political.sum() == 4                        # political mask still works


def test_min_click_filtering():
    d = load_mind(FIX, min_item_clicks=2)
    # only items clicked by >=2 users survive: N1(U1,U3), N2(U1,U2), N3(U1,U3),
    # N6(U2,U4), N7(U1,U3); singletons N4,N5,N8 are dropped.
    assert set(d.dataset.item_ids) == {"N1", "N2", "N3", "N6", "N7"}


def test_lean_table_and_norm(tmp_path):
    assert _norm("Fox News") == _norm("foxnews.com") == "foxnews"
    assert DEFAULT_LEAN["foxnews"] == 2
    # a user-supplied lean table accepts both ints and L..R labels
    p = tmp_path / "lean.csv"
    p.write_text("outlet,lean\nFox News,right\nCNN,-1\n")
    table = load_lean_table(p)
    assert table[_norm("Fox News")] == 2
    assert table[_norm("CNN")] == -1


def test_fit_ideology_recovers_axis_from_clicks():
    # Block-separable clicks: left users click left items, right users right items.
    rows = [0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
    cols = [0, 1, 0, 1, 0, 1, 2, 3, 2, 3, 2, 3]          # items: L1 L2 | R1 R2
    A = sp.csr_matrix((np.ones(12), (rows, cols)), shape=(6, 4))
    d = _mind_from(A, leans=[-2, -1, 1, 2])              # planted L..R leans
    fit = d.fit_ideology(n_iter=600, seed=0)
    # the latent axis recovered from clicks alone aligns with the planted leans
    assert fit.lean_corr is not None and fit.lean_corr > 0.5
    theta = fit.user_positions
    assert theta[:3].mean() < theta[3:].mean()           # left users left of right users


def test_fit_ideology_on_fixture_plugs_into_rweb(mind):
    sub = mind.political_subset(require_lean=False)       # no outlet labels needed
    fit = sub.fit_ideology(n_iter=200, seed=0)
    assert fit.user_positions.shape == (sub.n_users,)
    assert fit.item_positions.shape == (sub.n_items,)
    assert np.all(np.isfinite(fit.user_positions))
    d2 = sub.with_ideology(fit)
    assert d2.user_positions is not None
    g = FeedbackGraph(d2.dataset.matrix)
    recs = RWEB(g, d2.user_positions, d2.item_positions,
                epsilon=0.9).recommend(range(d2.n_users), top_k=2)
    assert recs.shape == (d2.n_users, 2)


def test_fit_ideology_max_cells_guard(mind):
    with pytest.raises(ValueError):
        mind.fit_ideology(max_cells=1)                   # refuses a too-large dense fit


def test_sample_users(mind):
    s = mind.sample_users(2, seed=0)
    assert s.n_users == 2
    assert 0 < s.n_items <= mind.n_items                 # click-less items dropped
    assert set(s.dataset.user_ids) <= set(mind.dataset.user_ids)
    assert mind.sample_users(99).n_users == mind.n_users  # n >= n_users -> unchanged


def test_recommender_inputs_drops_unknown_and_empty():
    rows = [0, 0, 1, 1, 2, 2]
    cols = [0, 1, 4, 5, 2, 3]                            # u2 only clicks NaN-pos items
    A = sp.csr_matrix((np.ones(6), (rows, cols)), shape=(3, 6))
    d = _mind_from(A, leans=[-2, -1, np.nan, np.nan, 1, 2])
    ds, theta, item_pos = d.recommender_inputs()
    assert ds.n_items == 4                               # the 2 NaN-position items dropped
    assert ds.n_users == 2                               # u2 left click-less -> dropped
    assert np.all(np.isfinite(item_pos)) and np.all(np.isfinite(theta))
    assert len(theta) == ds.n_users and len(item_pos) == ds.n_items


def test_compare_table_has_accuracy_div_and_ideology_columns(mind):
    ds, theta, item_pos = mind.political_subset(require_lean=True).recommender_inputs()
    train, test_pos = data.train_test_split(ds, test_frac=0.3, seed=0)
    g = FeedbackGraph(train)
    table = experiment.compare(
        {"P3": P3(g), "RWE-B": RWEB(g, theta, item_pos, epsilon=0.9)},
        g, test_pos, top_k=2, diversity_k=2,
        item_positions=item_pos, user_positions=theta, n_users_total=g.m)
    for col in ("ndcg@2", "coverage@2", "rec_range@2", "shift@2", "uw_shift"):
        assert col in table.columns


def test_with_ideology_save_load_user_positions(mind, tmp_path):
    sub = mind.political_subset(require_lean=True)
    d = sub.with_ideology(sub.fit_ideology(n_iter=50, seed=0))
    p = tmp_path / "m.npz"
    d.save(p)
    d2 = MINDData.load(p)
    assert d2.user_positions is not None
    np.testing.assert_allclose(d2.user_positions, d.user_positions)


def test_save_load_roundtrip(mind, tmp_path):
    p = tmp_path / "mind.npz"
    mind.save(p)
    d2 = MINDData.load(p)
    assert d2.n_users == mind.n_users and d2.n_items == mind.n_items
    assert np.array_equal(d2.dataset.item_ids, mind.dataset.item_ids)
    np.testing.assert_array_equal(np.nan_to_num(d2.item_positions, nan=-99),
                                  np.nan_to_num(mind.item_positions, nan=-99))
