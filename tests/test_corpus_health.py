"""Tests for examples/corpus_health.py — corpus metrics + validation-aware retention.

Proves retention prunes by age/count but is MONOTONIC: it never prunes the catalog below the
configured floors (min total / publishers / per-bucket / fresh), retaining older articles as needed;
it can't manufacture diversity the feeds never had; and it deletes ONLY FeedArticle rows (reads and
every other user-keyed table survive)."""

import pathlib
import sys
import time
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


def test_corpus_metrics_counts_unknown_outlets():
    """W4 observability: articles whose outlet has no registry lean (NaN) are counted separately —
    they never become recommendation candidates. The accounting closes: bucketed + unknown == total."""
    arts = [_a("k1", "NPR", -1.0, 0), _a("k2", "Fox News", 1.5, 1),
            _a("u1", "randomblog.example", float("nan"), 0),
            _a("u2", "another.example", float("nan"), 1)]
    m = ch.corpus_metrics(arts, now=NOW)
    assert m["unknownOutlet"] == 2 and m["unknownOutletPct"] == 50.0
    assert sum(m["perBucket"].values()) + m["unknownOutlet"] == m["total"]


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


# --------------------------------------------------------------------------- #
# Commit 4 additions: missing-metadata metric + validation ceilings (additive)
# --------------------------------------------------------------------------- #
def test_corpus_metrics_missing_metadata():
    arts = [
        {"canonicalUrl": "u1", "url": "u1", "publisher": "NPR", "title": "Has a title",
         "scored": {"article_id": "u1", "outlet": "NPR", "lean": -1.0},
         "publishedAt": (NOW - timedelta(days=0)).isoformat()},                       # complete
        {"canonicalUrl": "u2", "url": "u2", "publisher": "AP", "title": "",           # no title
         "scored": {"article_id": "u2", "outlet": "AP", "lean": 0.0},
         "publishedAt": (NOW - timedelta(days=1)).isoformat()},
        {"canonicalUrl": "u3", "url": "u3", "publisher": "Fox", "title": "T",         # no publication date
         "scored": {"article_id": "u3", "outlet": "Fox", "lean": 1.5}, "publishedAt": None},
    ]
    m = ch.corpus_metrics(arts, now=NOW, fresh_max_age_days=3)
    assert m["missingMetadata"] == 2 and m["missingMetadataPct"] == round(200 / 3, 2)


def test_thresholds_from_env_includes_validation_ceilings(monkeypatch):
    for k in ("RWE_CORPUS_MAX_PER_PUBLISHER", "RWE_CORPUS_MAX_BUCKET_PERCENT",
              "RWE_CORPUS_MAX_ARTICLE_AGE_DAYS", "RWE_CORPUS_MAX_DUPLICATE_PERCENT",
              "RWE_CORPUS_MAX_MISSING_METADATA_PERCENT", "RWE_CORPUS_REQUIRE_HEALTHY_FEEDS"):
        monkeypatch.delenv(k, raising=False)
    th = ch.thresholds_from_env()
    assert th["maxPerPublisher"] == 0 and th["maxBucketPercent"] == 0.0
    assert th["requireHealthyFeeds"] is False
    monkeypatch.setenv("RWE_CORPUS_MAX_PER_PUBLISHER", "40")
    monkeypatch.setenv("RWE_CORPUS_MAX_BUCKET_PERCENT", "55.5")
    monkeypatch.setenv("RWE_CORPUS_REQUIRE_HEALTHY_FEEDS", "1")
    th2 = ch.thresholds_from_env()
    assert th2["maxPerPublisher"] == 40 and th2["maxBucketPercent"] == 55.5
    assert th2["requireHealthyFeeds"] is True


def test_retention_ignores_validation_ceilings():
    # Retention reads only the floor keys; the new ceiling keys must never change its behaviour.
    arts = [_a(f"u{i}", "P", 0.0, i) for i in range(10)]
    th = ch.thresholds_from_env()
    th.update({"minArticles": 0})     # isolate: only the explicit count policy should act
    plan = ch.plan_retention(arts, max_count=4, thresholds=th, now=NOW)
    assert plan["pruned"] == 6        # ceilings (maxPerPublisher, maxBucketPercent, …) are ignored


def test_retention_builds_the_kept_set_exactly_once(monkeypatch):
    """The regression that made the whole site slow, guarded deterministically.

    ``run_retention`` selected kept articles with ``set(plan["keep"])`` written INSIDE the
    comprehension's condition, so Python rebuilt the entire set once per article. On production's
    catalog that was a 27,000-element set constructed 27,000 times: 37.75 s locally, 74-81 s on the
    box, every 80 seconds, on a pass that deletes nothing in steady state.

    It survived review because it is one line that reads correctly, and it survived production
    because the log carried ``deleted`` counts and no durations — a prune that removes zero rows
    looks idle until somebody times it.

    Asserted by COUNTING, not by the clock. The first version of this test used a wall-clock bound
    and passed against the quadratic code, because at a test-sized catalog the defect is only a few
    seconds and any bound loose enough to be CI-safe is loose enough to miss it. Counting how many
    times the set is built has no such gap: correct is exactly one, at every catalog size."""

    class _CountingKeep(list):
        """A keep-list that reports how many times something iterated it."""

        def __init__(self, *a):
            super().__init__(*a)
            self.iterations = 0

        def __iter__(self):
            self.iterations += 1
            return super().__iter__()

    st = store.Store("sqlite://")
    for i in range(40):
        u = f"https://z{i}.example/{i}"
        st.upsert_feed_article(canonical_url=u, url=u, publisher=f"P{i % 5}",
                               source_publisher=f"P{i % 5}", title="t", description="", body=None,
                               published_at=(NOW - timedelta(hours=i)).isoformat(), source_feed="f",
                               scored={"article_id": u, "outlet": f"P{i % 5}", "lean": 0.0,
                                       "category": "x"})

    real_plan, seen = ch.plan_retention, {}

    def counting_plan(*a, **kw):
        plan = real_plan(*a, **kw)
        plan["keep"] = seen["keep"] = _CountingKeep(plan["keep"])
        return plan
    monkeypatch.setattr(ch, "plan_retention", counting_plan)

    res = ch.run_retention(st, max_count=1000, thresholds=TH(), now=NOW)   # cap never binds

    assert res["pruned"] == 0, "the cap does not bind, so this is the steady-state no-op case"
    assert res["metrics"]["total"] == 40, "every article must still reach the metrics pass"
    assert seen["keep"].iterations == 1, (
        f"the kept set was built {seen['keep'].iterations} times for 40 articles — that is the "
        f"quadratic set-in-comprehension back; it must be hoisted out of the loop")
