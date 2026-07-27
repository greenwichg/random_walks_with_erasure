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


# --------------------------------------------------------------------------- #
# Blocked candidate generation — an EXACT optimisation, not an approximation.
#
# cluster() no longer scores all pairs; it scores only pairs sharing >=1 token, via an inverted
# index. The guarantee that makes that safe: jaccard(a,b) >= sim > 0 requires |a & b| >= 1, so a
# pair sharing no token can never match. These tests hold the guarantee to the fire by comparing
# against a naive all-pairs reference on randomised input.
# --------------------------------------------------------------------------- #
def _cluster_naive(items, *, tokens, time, sim=cl.DEFAULT_SIM, window_days=cl.DEFAULT_WINDOW_DAYS):
    """The pre-optimisation all-pairs implementation, kept HERE as the reference oracle."""
    n = len(items)
    toks = [tokens(it) for it in items]
    times = [time(it) for it in items]
    dsu = cl.DSU(n)
    for i in range(n):
        if not toks[i]:
            continue
        for j in range(i + 1, n):
            if (toks[j] and cl.jaccard(toks[i], toks[j]) >= sim
                    and cl.within_window(times[i], times[j], window_days)):
                dsu.union(i, j)
    groups = {}
    for i in range(n):
        groups.setdefault(dsu.find(i), []).append(i)
    return list(groups.values())


def _norm(groups):
    return sorted(sorted(g) for g in groups)


def test_blocked_matches_naive_on_randomised_corpora():
    """The core equivalence claim, over many random shapes: identical clusters, every time."""
    import random
    from datetime import datetime, timedelta, timezone

    rnd = random.Random(20260727)
    vocab = [f"tok{i}" for i in range(120)]
    now = datetime(2026, 7, 27, tzinfo=timezone.utc)

    for trial in range(25):
        n = rnd.randint(2, 60)
        events = [[rnd.choice(vocab) for _ in range(6)] for _ in range(max(2, n // 4))]
        items = []
        for _ in range(n):
            ev = rnd.choice(events)
            toks = frozenset(rnd.sample(ev, rnd.randint(2, 5)) + [rnd.choice(vocab)])
            items.append({"t": toks, "d": now - timedelta(hours=rnd.random() * 300)})
        kw = dict(tokens=lambda x: x["t"], time=lambda x: x["d"])
        assert _norm(cl.cluster(items, **kw)) == _norm(_cluster_naive(items, **kw)), \
            f"divergence on trial {trial} (n={n})"


def test_blocked_matches_naive_with_a_hub_token():
    """Worst case for blocking: one token every item carries, so the inverted index degenerates to
    all-pairs. It must still be correct (this is about correctness, not speed)."""
    items = [{"t": frozenset({"election", f"unique{i}"}), "d": None} for i in range(30)]
    kw = dict(tokens=lambda x: x["t"], time=lambda x: x["d"])
    assert _norm(cl.cluster(items, **kw)) == _norm(_cluster_naive(items, **kw))


def test_empty_token_sets_are_isolated_singletons():
    """A title that reduces to nothing (all stop-words) has no postings, so it must never be pulled
    into a cluster — and must still appear as its own group."""
    items = [{"t": cl.title_tokens("the and of it"), "d": None},
             {"t": cl.title_tokens("Senate passes funding bill"), "d": None},
             {"t": cl.title_tokens("Senate passes the funding bill"), "d": None}]
    kw = dict(tokens=lambda x: x["t"], time=lambda x: x["d"])
    assert _norm(cl.cluster(items, **kw)) == [[0], [1, 2]]
    assert _norm(cl.cluster(items, **kw)) == _norm(_cluster_naive(items, **kw))


def test_single_item_and_empty_input():
    kw = dict(tokens=lambda x: x["t"], time=lambda x: x["d"])
    assert cl.cluster([], **kw) == []
    assert _norm(cl.cluster([{"t": frozenset({"a"}), "d": None}], **kw)) == [[0]]
