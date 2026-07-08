"""Tests for examples/corpus_validation.py — the corpus-eligibility gate (Commit 4).

Proves the required demonstrations: a healthy corpus passes; each failure mode (publisher imbalance,
missing publishers, missing political bucket, too many duplicates, stale corpus, unhealthy feeds,
missing metadata) is caught; the publisher cap preserves the newest articles; the candidate stays
immutable through validation; and validation is decoupled from every recommendation module (so it
cannot change ranking, scoring, selection, or serialisation). Validation NEVER activates anything.
"""

import copy
import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import store                    # noqa: E402
import corpus_validation as cv  # noqa: E402

NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)


def _a(url, pub, lean, days_ago=0, title="Headline", cat="Politics"):
    """A FeedArticle-shaped dict. ``days_ago=None`` -> no publication date; ``title=""`` -> no title."""
    pub_at = None if days_ago is None else (NOW - timedelta(days=days_ago)).isoformat()
    return {"canonicalUrl": url, "url": url, "publisher": pub, "title": title,
            "scored": {"article_id": url, "outlet": pub, "lean": lean, "category": cat},
            "publishedAt": pub_at}


def TH(**kw):
    """A full threshold dict with every check off, overridable per test."""
    t = {"minArticles": 0, "minPublishers": 0, "minPerBucket": 0, "minFresh": 0, "freshMaxAgeDays": 3,
         "maxPerPublisher": 0, "maxBucketPercent": 0.0, "maxArticleAgeDays": 0,
         "maxDuplicatePct": 0.0, "maxMissingMetadataPct": 0.0, "requireHealthyFeeds": False}
    t.update(kw)
    return t


def _codes(result):
    return {f["code"] for f in result.failures}


# --------------------------------------------------------------------------- #
# Healthy corpus passes
# --------------------------------------------------------------------------- #
def test_healthy_corpus_passes():
    arts = ([_a(f"l{i}", "Guardian", -1.5, i) for i in range(4)]
            + [_a(f"c{i}", "AP", 0.0, i) for i in range(4)]
            + [_a(f"r{i}", "Fox News", 1.5, i) for i in range(4)])
    th = TH(minArticles=10, minPublishers=3, minPerBucket=3, minFresh=1, maxPerPublisher=10,
            maxBucketPercent=60, maxArticleAgeDays=30, maxDuplicatePct=10, maxMissingMetadataPct=10)
    res = cv.validate_corpus(arts, [{"healthy": True}], thresholds=th, now=NOW)
    assert res.eligible is True and res.status == "pass" and res.failures == []


# --------------------------------------------------------------------------- #
# Each failure mode
# --------------------------------------------------------------------------- #
def test_publisher_imbalance_fails():
    arts = [_a(f"n{i}", "NPR", -1.0, i) for i in range(6)] + [_a("f1", "Fox News", 1.5, 0)]
    res = cv.validate_corpus(arts, [], thresholds=TH(maxPerPublisher=3), now=NOW)
    assert not res.eligible and "max_per_publisher" in _codes(res)


def test_missing_publishers_fails():
    arts = [_a(f"n{i}", "NPR", -1.0, i) for i in range(5)]
    res = cv.validate_corpus(arts, [], thresholds=TH(minPublishers=3), now=NOW)
    assert not res.eligible and "min_publishers" in _codes(res)


def test_missing_political_bucket_fails():
    # left + center only — no right-leaning coverage.
    arts = ([_a(f"l{i}", "Guardian", -1.5, i) for i in range(3)]
            + [_a(f"c{i}", "AP", 0.0, i) for i in range(3)])
    res = cv.validate_corpus(arts, [], thresholds=TH(minPerBucket=1), now=NOW)
    assert not res.eligible
    f = next(f for f in res.failures if f["code"] == "min_per_bucket")
    assert "right" in f["buckets"]


def test_too_many_duplicates_fails():
    arts = [_a("u1", "NPR", -1.0, 0), _a("u1", "NPR", -1.0, 0), _a("u2", "AP", 0.0, 1)]   # 1 dup of 3
    res = cv.validate_corpus(arts, [], thresholds=TH(maxDuplicatePct=10), now=NOW)
    assert not res.eligible and "max_duplicate_pct" in _codes(res)
    assert res.metrics["duplicatePct"] == round(100 / 3, 2)


def test_political_imbalance_by_bucket_percent_fails():
    # one bucket dominates the political share -> max_bucket_percent.
    arts = [_a(f"l{i}", "Guardian", -1.5, i) for i in range(9)] + [_a("c1", "AP", 0.0, 0)]
    res = cv.validate_corpus(arts, [], thresholds=TH(maxBucketPercent=60), now=NOW)
    assert not res.eligible and "max_bucket_percent" in _codes(res)


def test_stale_corpus_fails():
    arts = [_a(f"o{i}", "NPR", -1.0, 40 + i) for i in range(3)]    # even the newest is ~40 days old
    res = cv.validate_corpus(arts, [], thresholds=TH(maxArticleAgeDays=7), now=NOW)
    assert not res.eligible and "max_article_age" in _codes(res)


def test_undated_corpus_is_stale_when_age_ceiling_set():
    arts = [_a(f"u{i}", "NPR", -1.0, None) for i in range(3)]      # no publication dates at all
    res = cv.validate_corpus(arts, [], thresholds=TH(maxArticleAgeDays=7), now=NOW)
    assert not res.eligible and "max_article_age" in _codes(res)


def test_unhealthy_feeds_fail_when_required():
    arts = [_a(f"a{i}", "NPR", -1.0, i) for i in range(3)]
    fh = [{"healthy": True}, {"healthy": False}]
    res = cv.validate_corpus(arts, fh, thresholds=TH(requireHealthyFeeds=True), now=NOW)
    assert not res.eligible and "unhealthy_feeds" in _codes(res)
    assert res.metrics["healthyFeeds"] == 1 and res.metrics["unhealthyFeeds"] == 1


def test_require_healthy_feeds_with_no_health_data_fails_closed():
    arts = [_a(f"a{i}", "NPR", -1.0, i) for i in range(3)]
    res = cv.validate_corpus(arts, [], thresholds=TH(requireHealthyFeeds=True), now=NOW)
    assert not res.eligible and "unhealthy_feeds" in _codes(res)   # can't confirm health -> ineligible


def test_missing_metadata_threshold_fails():
    arts = [_a("a1", "NPR", -1.0, 0, title=""),      # no title
            _a("a2", "AP", 0.0, None),               # no publication date
            _a("a3", "Fox News", 1.5, 1)]            # complete
    res = cv.validate_corpus(arts, [], thresholds=TH(maxMissingMetadataPct=10), now=NOW)
    assert not res.eligible and "max_missing_metadata_pct" in _codes(res)
    assert res.metrics["missingMetadata"] == 2


# --------------------------------------------------------------------------- #
# Publisher cap preserves the newest articles (composition, never mutation)
# --------------------------------------------------------------------------- #
def test_publisher_cap_preserves_newest():
    arts = [_a(f"n{i}", "NPR", -1.0, i) for i in range(5)] + [_a("f0", "Fox News", 1.5, 0)]
    cand = cv.build_candidate(arts, max_per_publisher=2, now=NOW)
    npr = [a["canonicalUrl"] for a in cand if a["publisher"] == "NPR"]
    assert npr == ["n0", "n1"]                        # the two NEWEST NPR articles, newest first
    assert "f0" in [a["canonicalUrl"] for a in cand]  # a different publisher is untouched
    assert len(cand) == 3


def test_build_candidate_no_cap_returns_all_newest_first():
    arts = [_a("old", "P", 0.0, 10), _a("new", "P", 0.0, 0), _a("mid", "P", 0.0, 5)]
    cand = cv.build_candidate(arts, max_per_publisher=None, now=NOW)
    assert [a["canonicalUrl"] for a in cand] == ["new", "mid", "old"]


# --------------------------------------------------------------------------- #
# Candidate remains immutable through validation; result is a subset of the SAME rows
# --------------------------------------------------------------------------- #
def test_candidate_immutable_after_validation():
    arts = [_a(f"a{i}", "NPR", -1.0, i, title=f"H{i}") for i in range(4)]
    before = copy.deepcopy(arts)
    cand = cv.build_candidate(arts, max_per_publisher=2, now=NOW)
    res = cv.validate_corpus(cand, [], thresholds=TH(minArticles=1), now=NOW)
    assert arts == before                                   # inputs never mutated
    assert all(any(c is a for a in arts) for c in cand)     # candidate holds the SAME row objects
    assert res.eligible is True


# --------------------------------------------------------------------------- #
# Decoupling guarantee — validation cannot change any recommendation algorithm
# --------------------------------------------------------------------------- #
def test_validation_imports_no_recommendation_modules():
    for banned in ("api_server", "personalize", "simulate_users", "rwe", "health_report",
                   "narrate_report"):
        assert not hasattr(cv, banned), f"corpus_validation must not import {banned}"


# --------------------------------------------------------------------------- #
# Warnings never block; result shape
# --------------------------------------------------------------------------- #
def test_warnings_never_block():
    arts = [_a("l1", "Guardian", -1.5, 0), _a("l2", "Guardian", -1.5, 1)]   # left only
    res = cv.validate_corpus(arts, [{"healthy": False}], thresholds=TH(minArticles=1), now=NOW)
    assert res.eligible is True                             # warnings do not affect eligibility
    codes = {w["code"] for w in res.warnings}
    assert "unhealthy_feeds_present" in codes and "empty_bucket" in codes


def test_to_dict_surfaces_distributions():
    arts = [_a("l1", "Guardian", -1.5, 0), _a("c1", "AP", 0.0, 1), _a("r1", "Fox News", 1.5, 2)]
    d = cv.validate_corpus(arts, [{"healthy": True}], thresholds=TH(minArticles=1), now=NOW).to_dict()
    assert d["eligible"] is True and d["status"] == "pass"
    assert d["politicalDistribution"] == {"left": 1, "center": 1, "right": 1}
    assert set(d["publisherDistribution"]) == {"Guardian", "AP", "Fox News"}
    assert d["freshness"]["newest"] is not None and d["healthyFeeds"] == 1
    assert d["metrics"]["missingMetadataPct"] == 0.0 and "thresholds" in d


# --------------------------------------------------------------------------- #
# evaluate() over a real store — read-only, never throws
# --------------------------------------------------------------------------- #
def test_evaluate_over_store_is_read_only():
    st = store.Store("sqlite://")
    for i in range(6):
        pub, lean = ("NPR", -1.0) if i % 2 else ("Fox News", 1.5)
        u = f"https://x.example/{i}"
        st.upsert_feed_article(canonical_url=u, url=u, publisher=pub, source_publisher=pub, title="t",
                               description="", body=None,
                               published_at=(NOW - timedelta(days=i)).isoformat(), source_feed="f",
                               scored={"article_id": u, "outlet": pub, "lean": lean, "category": "x"})
    user = st.upsert_user_by_identity("dev", "u@x", email="u@x", display_name="R")
    st.add_read(user.id, "https://read.example/1",
                {"article_id": "https://read.example/1", "outlet": "NPR", "lean": -1.0, "title": "t"})

    res = cv.evaluate(st, thresholds=TH(minArticles=1, maxPerPublisher=2), now=NOW)
    assert res.status in {"pass", "fail"} and isinstance(res.to_dict(), dict)
    assert res.candidateSize == 4               # per-publisher cap of 2 over two publishers
    assert st.count_feed_articles() == 6 and st.count_reads(user.id) == 1   # store never mutated


def test_evaluate_never_throws_on_broken_store():
    class Broken:
        def list_feed_articles(self, limit):
            raise RuntimeError("boom")

        def list_feed_health(self):
            return []
    res = cv.evaluate(Broken(), thresholds=TH(minArticles=1), now=NOW)
    assert res.eligible is False and res.status == "error"
    assert res.failures[0]["code"] == "validation_error"
