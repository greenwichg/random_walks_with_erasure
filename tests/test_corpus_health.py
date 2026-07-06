"""Tests for examples/corpus_health.py — corpus metrics + validation-aware retention.

Proves retention prunes by age/count but is MONOTONIC: it never prunes the catalog below the
configured floors (min total / publishers / per-bucket / fresh), retaining older articles as needed;
it can't manufacture diversity the feeds never had; and it deletes ONLY FeedArticle rows (reads and
every other user-keyed table survive)."""

import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import store          # noqa: E402
import corpus_health as ch   # noqa: E402

NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)


def _a(url, pub, lean, days_ago, cat="Politics"):
    return {"canonicalUrl": url, "url": url, "publisher": pub,
            "scored": {"article_id": url, "outlet": pub, "lean": lean, "category": cat},
            "publishedAt": (NOW - timedelta(days=days_ago)).isoformat()}


def TH(**kw):
    t = {"minArticles": 0, "minPublishers": 0, "minPerBucket": 0, "minFresh": 0, "freshMaxAgeDays": 3}
    t.update(kw)
    return t


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def test_corpus_metrics():
    arts = [_a("u1", "NPR", -1.0, 0), _a("u2", "Fox News", 1.5, 1), _a("u3", "BBC News", 0.0, 10),
            _a("u1", "NPR", -1.0, 0)]   # a duplicate canonical url
    m = ch.corpus_metrics(arts, now=NOW, fresh_max_age_days=3)
    assert m["total"] == 4 and m["publishers"] == 3
    assert m["perBucket"] == {"left": 2, "center": 1, "right": 1}
    assert m["fresh"] == 3           # 0d, 1d, and the dup (0d) — 10d is not fresh
    assert m["duplicatePct"] == 25.0


# --------------------------------------------------------------------------- #
# Raw policy
# --------------------------------------------------------------------------- #
def test_max_count_prunes_oldest():
    arts = [_a(f"u{i}", "P", 0.0, i) for i in range(10)]      # u0 newest … u9 oldest
    plan = ch.plan_retention(arts, max_count=4, thresholds=TH(), now=NOW)
    assert set(plan["keep"]) == {"u0", "u1", "u2", "u3"} and plan["pruned"] == 6


def test_max_age_prunes_old():
    arts = [_a("new", "P", 0.0, 1), _a("mid", "P", 0.0, 5), _a("old", "P", 0.0, 40)]
    plan = ch.plan_retention(arts, max_age_days=7, thresholds=TH(), now=NOW)
    assert set(plan["keep"]) == {"new", "mid"} and plan["prune"] == ["old"]


# --------------------------------------------------------------------------- #
# Floors — retention retains older articles rather than breach them
# --------------------------------------------------------------------------- #
def test_floor_min_articles_retains_older():
    arts = [_a(f"u{i}", "P", 0.0, i) for i in range(10)]
    plan = ch.plan_retention(arts, max_count=2, thresholds=TH(minArticles=6), now=NOW)
    assert plan["kept"] == 6 and plan["pruned"] == 4
    assert plan["rawPruned"] == 8 and plan["retainedForFloor"] == 4   # 4 older articles kept for the floor


def test_floor_min_publishers_retains_missing():
    # newest 3 are all NPR; a count cap of 3 would keep only NPR — the publisher floor pulls back the
    # newest pruned article from each missing outlet.
    arts = [_a("n1", "NPR", -1, 0), _a("n2", "NPR", -1, 1), _a("n3", "NPR", -1, 2),
            _a("f1", "Fox News", 1.5, 5), _a("b1", "BBC News", 0.0, 6)]
    plan = ch.plan_retention(arts, max_count=3, thresholds=TH(minPublishers=3), now=NOW)
    keep = set(plan["keep"])
    assert {"n1", "n2", "n3", "f1", "b1"} <= keep and plan["retainedForFloor"] == 2


def test_floor_min_per_bucket_retains():
    # newest two are left-leaning; the per-bucket floor pulls back an older center + right article.
    arts = [_a("l1", "Guardian", -1.5, 0), _a("l2", "Guardian", -1.5, 1),
            _a("c1", "AP", 0.0, 5), _a("r1", "Fox News", 1.5, 6)]
    plan = ch.plan_retention(arts, max_count=2, thresholds=TH(minPerBucket=1), now=NOW)
    keep = set(plan["keep"])
    assert "c1" in keep and "r1" in keep       # retained to satisfy the center + right floor


def test_floor_min_fresh_retains_fresh():
    arts = [_a(f"fr{i}", "P", 0.0, i) for i in range(3)] + [_a(f"ol{i}", "P", 0.0, 10 + i) for i in range(3)]
    plan = ch.plan_retention(arts, max_count=1, thresholds=TH(minFresh=3), now=NOW)
    keep = set(plan["keep"])
    assert sum(1 for u in keep if u.startswith("fr")) == 3     # three fresh articles retained


# --------------------------------------------------------------------------- #
# Safety invariants
# --------------------------------------------------------------------------- #
def test_prune_is_monotonic_subset_of_raw_policy():
    arts = [_a(f"u{i}", "P", 0.0, i) for i in range(20)]
    plan = ch.plan_retention(arts, max_count=2, thresholds=TH(minArticles=15), now=NOW)
    assert plan["pruned"] <= plan["rawPruned"]                # never prunes MORE than the raw policy
    assert plan["kept"] >= 15                                 # ...and never below the floor


def test_cannot_manufacture_diversity_keeps_everything_relevant():
    # only two publishers exist; a floor of 5 is impossible -> keep both, best effort, never worse.
    arts = ([_a(f"n{i}", "NPR", -1, i) for i in range(5)]
            + [_a(f"f{i}", "Fox News", 1.5, i + 5) for i in range(5)])
    plan = ch.plan_retention(arts, max_count=2, thresholds=TH(minPublishers=5), now=NOW)
    keep = set(plan["keep"])
    assert any(u.startswith("n") for u in keep) and any(u.startswith("f") for u in keep)


def test_no_policy_prunes_nothing():
    arts = [_a(f"u{i}", "P", 0.0, i) for i in range(5)]
    plan = ch.plan_retention(arts, max_age_days=None, max_count=None, thresholds=TH(), now=NOW)
    assert plan["pruned"] == 0 and plan["kept"] == 5


# --------------------------------------------------------------------------- #
# run_retention against a real store — deletes only feed_articles
# --------------------------------------------------------------------------- #
def test_run_retention_deletes_only_feed_articles():
    st = store.Store("sqlite://")
    for i in range(10):
        u = f"https://x.example/{i}"
        st.upsert_feed_article(canonical_url=u, url=u, publisher="P", source_publisher="P", title="t",
                               description="", body=None,
                               published_at=(NOW - timedelta(days=i)).isoformat(), source_feed="f",
                               scored={"article_id": u, "outlet": "P", "lean": 0.0, "category": "x"})
    user = st.upsert_user_by_identity("dev", "u@x", email="u@x", display_name="R")
    st.add_read(user.id, "https://read.example/1",
                {"article_id": "https://read.example/1", "outlet": "NPR", "lean": -1.0, "title": "t"})

    res = ch.run_retention(st, max_count=4, thresholds=TH(), now=NOW)
    assert res["pruned"] == 6 and st.count_feed_articles() == 4       # catalog pruned to the cap
    assert st.count_reads(user.id) == 1                              # reads never touched by retention


def test_run_retention_respects_min_articles_floor():
    st = store.Store("sqlite://")
    for i in range(10):
        u = f"https://y.example/{i}"
        st.upsert_feed_article(canonical_url=u, url=u, publisher="P", source_publisher="P", title="t",
                               description="", body=None,
                               published_at=(NOW - timedelta(days=i)).isoformat(), source_feed="f",
                               scored={"article_id": u, "outlet": "P", "lean": 0.0, "category": "x"})
    # a count cap of 2, but a floor of 8 -> only 2 pruned, 8 retained
    res = ch.run_retention(st, max_count=2, thresholds=TH(minArticles=8), now=NOW)
    assert res["pruned"] == 2 and st.count_feed_articles() == 8 and res["retainedForFloor"] == 6
