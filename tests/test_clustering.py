"""Tests for examples/clustering.py — the reusable deterministic clustering primitive (Commit 7).

Proves related items cluster, unrelated stay separate, the time window splits look-alikes, and the
grouping is deterministic — with no Story/FeedArticle knowledge in this layer."""

import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import clustering as cl   # noqa: E402

T0 = datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc)


def _items(*specs):
    return [{"t": t, "when": T0 + timedelta(days=d)} for t, d in specs]


def _groups(items, **kw):
    g = cl.cluster(items, tokens=lambda x: cl.title_tokens(x["t"]), time=lambda x: x["when"], **kw)
    return sorted(sorted(grp) for grp in g)


def test_title_tokens_and_jaccard():
    a = cl.title_tokens("Senate passes the funding bill")
    b = cl.title_tokens("Senate funding bill vote")
    assert "senate" in a and "the" not in a          # stop-word dropped
    assert 0.0 < cl.jaccard(a, b) <= 1.0
    assert cl.jaccard(a, frozenset()) == 0.0


def test_related_items_cluster_together():
    items = _items(("Senate passes funding bill after debate", 0),
                   ("Senate passes funding bill averting shutdown", 0),
                   ("Wildfires spread across western coast", 0))
    assert _groups(items, sim=0.28, window_days=6) == [[0, 1], [2]]


def test_unrelated_items_stay_separate():
    items = _items(("Markets rally on tech earnings", 0),
                   ("Local team wins the championship", 0),
                   ("New climate policy unveiled today", 0))
    assert _groups(items, sim=0.28, window_days=6) == [[0], [1], [2]]


def test_time_window_splits_lookalikes():
    # identical-ish titles, but 40 days apart -> the window keeps them in separate clusters
    items = _items(("Senate passes funding bill", 0), ("Senate passes funding bill", 40))
    assert _groups(items, sim=0.28, window_days=6) == [[0], [1]]
    assert _groups(items, sim=0.28, window_days=60) == [[0, 1]]   # widen the window -> they merge


def test_missing_time_never_blocks_a_match():
    g = cl.cluster([{"t": "Senate funding bill"}, {"t": "Senate funding bill vote"}],
                   tokens=lambda x: cl.title_tokens(x["t"]), time=lambda x: None, window_days=1)
    assert sorted(sorted(grp) for grp in g) == [[0, 1]]


def test_deterministic():
    items = _items(("Senate passes funding bill", 0), ("Senate funding bill vote", 0),
                   ("Wildfires spread west", 0), ("Wildfires spread rapidly west", 1))
    assert _groups(items) == _groups(items)          # same input -> same groups + order
