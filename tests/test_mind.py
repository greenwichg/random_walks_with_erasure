"""Tests for the MIND loader + ideology ingestion (rwe/mind.py).

Runs against a tiny fixture (tests/fixtures/mind_demo) so the parser, the
political tagger, the outlet-lean join and the downstream RWE plumbing are all
exercised without the multi-GB MIND download.
"""

from pathlib import Path

import numpy as np
import pytest

from rwe import FeedbackGraph, RWEB
from rwe.mind import (DEFAULT_LEAN, MINDData, load_mind, load_lean_table, _norm)

FIX = Path(__file__).parent / "fixtures" / "mind_demo"


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


def test_save_load_roundtrip(mind, tmp_path):
    p = tmp_path / "mind.npz"
    mind.save(p)
    d2 = MINDData.load(p)
    assert d2.n_users == mind.n_users and d2.n_items == mind.n_items
    assert np.array_equal(d2.dataset.item_ids, mind.dataset.item_ids)
    np.testing.assert_array_equal(np.nan_to_num(d2.item_positions, nan=-99),
                                  np.nan_to_num(mind.item_positions, nan=-99))
