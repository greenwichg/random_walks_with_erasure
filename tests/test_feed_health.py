"""Tests for feed health monitoring (Commit 3) — store.FeedHealth + the ingestion seams that feed it.

Observational only: recording health tracks availability + quality per feed, but never removes
articles, modifies FeedArticle, or affects recommendations."""

import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import store          # noqa: E402
import rss_ingest     # noqa: E402
import feed_service   # noqa: E402


def _rss(items):
    body = "".join(f"<item><title>{t}</title><link>{u}</link><description>d</description>"
                   f"<pubDate>Wed, 02 Oct 2024 12:00:00 GMT</pubDate></item>" for t, u in items)
    return (f"<?xml version='1.0'?><rss version='2.0'><channel><title>C</title>{body}</channel></rss>").encode()


# --------------------------------------------------------------------------- #
# store.record_feed_health — availability tracking + recovery
# --------------------------------------------------------------------------- #
def test_success_records_quality_and_stays_healthy():
    st = store.Store("sqlite://")
    r = st.record_feed_health(
        "https://f1/feed", ok=True, name="Feed 1", latency_ms=120.0,
        stats={"new": 5, "duplicates": 2, "skipped": 1, "missing_metadata": 1,
               "newest": "2026-07-05T00:00:00+00:00", "oldest": "2026-07-01T00:00:00+00:00"},
        unhealthy_after=3)
    assert r["healthy"] is True and r["consecutiveFailures"] == 0
    assert r["totalPolls"] == 1 and r["totalOk"] == 1 and r["totalFailed"] == 0
    assert r["imported"] == 5 and r["duplicate"] == 2 and r["rejected"] == 1 and r["missingMetadata"] == 1
    assert r["newestPublished"] == "2026-07-05T00:00:00+00:00" and r["lastLatencyMs"] == 120.0


def test_consecutive_failures_mark_unhealthy_then_recover():
    st = store.Store("sqlite://")
    st.record_feed_health("f", ok=True, latency_ms=100.0, stats={"new": 1}, unhealthy_after=3)
    # two failures — still healthy, no transition
    for _ in range(2):
        r = st.record_feed_health("f", ok=False, error=OSError("boom"), latency_ms=50.0, unhealthy_after=3)
    assert r["consecutiveFailures"] == 2 and r["healthy"] is True and r["transition"] is None
    # third failure — crosses the threshold -> unhealthy, transition reported once
    r = st.record_feed_health("f", ok=False, error=OSError("boom"), latency_ms=50.0, unhealthy_after=3)
    assert r["consecutiveFailures"] == 3 and r["healthy"] is False and r["transition"] == "unhealthy"
    assert "boom" in r["lastError"]     # the store records str(error); the poller pre-formats the type
    # a successful poll resets + recovers automatically
    r = st.record_feed_health("f", ok=True, latency_ms=80.0, stats={"new": 2}, unhealthy_after=3)
    assert r["healthy"] is True and r["consecutiveFailures"] == 0 and r["transition"] == "recovered"
    assert r["totalPolls"] == 5 and r["totalOk"] == 2 and r["totalFailed"] == 3   # 1 ok + 3 fail + 1 ok
    assert r["avgLatencyMs"] is not None      # running average over the measured polls


def test_health_recording_is_observational():
    """Recording health writes only feed_health — FeedArticle and reads are never touched."""
    st = store.Store("sqlite://")
    st.upsert_feed_article(canonical_url="https://a.example/1", url="https://a.example/1", publisher="P",
                           source_publisher="P", title="t", description="", body=None,
                           published_at=None, source_feed="f",
                           scored={"article_id": "https://a.example/1", "outlet": "P", "lean": 0.0})
    user = st.upsert_user_by_identity("dev", "u", email="u", display_name="R")
    st.add_read(user.id, "https://a.example/1", {"article_id": "https://a.example/1", "outlet": "P", "lean": 0.0, "title": "t"})
    for _ in range(5):
        st.record_feed_health("f", ok=False, error=OSError("x"), latency_ms=10.0, unhealthy_after=3)
    assert st.count_feed_articles() == 1 and st.count_reads(user.id) == 1
    assert len(st.list_feed_health()) == 1


# --------------------------------------------------------------------------- #
# rss_ingest seams — per-feed callback + quality metrics
# --------------------------------------------------------------------------- #
def test_ingest_all_on_feed_callback_success_and_failure(monkeypatch):
    data = {"https://good.example/feed": _rss([("Good", "https://good.example/1")])}
    def fetch(url, timeout=15.0):
        if "bad" in url:
            raise OSError("connection refused")
        return data[url]
    st = store.Store("sqlite://")
    seen = []
    # pass fetch explicitly — ingest_all's default binds fetch_feed at def-time (monkeypatch wouldn't apply)
    agg = rss_ingest.ingest_all([("Good", "https://good.example/feed"), ("Bad", "https://bad.example/feed")],
                                rss_ingest.make_scorer(), st, fetch=fetch, on_feed=lambda *a: seen.append(a))
    assert agg["ok"] == 1 and agg["failed"] == 1               # partial failure — both processed
    assert len(seen) == 2
    (n1, u1, s1, l1, e1), (n2, u2, s2, l2, e2) = seen
    assert s1 is not None and e1 is None and l1 >= 0.0         # good feed: stats + latency, no error
    assert s2 is None and e2 is not None and l2 >= 0.0         # bad feed: error + latency, no stats


def test_ingest_all_on_feed_error_never_breaks_polling():
    st = store.Store("sqlite://")
    def boom(*_a):
        raise RuntimeError("health write failed")
    agg = rss_ingest.ingest_all([("X", "https://x.example/feed")], rss_ingest.make_scorer(), st,
                                fetch=lambda url: _rss([("X", "https://x.example/1")]), on_feed=boom)
    assert agg["ok"] == 1 and st.count_feed_articles() == 1    # a bad callback never breaks ingestion


def test_ingest_entries_quality_metrics():
    st = store.Store("sqlite://")
    entries = [
        rss_ingest.FeedEntry(url="https://a.example/1", title="Has title", published_at="2026-07-05T00:00:00+00:00"),
        rss_ingest.FeedEntry(url="https://a.example/2", title="", published_at="2026-07-01T00:00:00+00:00"),   # no title
        rss_ingest.FeedEntry(url="https://a.example/3", title="T", published_at=None),                          # no date
    ]
    s = rss_ingest.ingest_entries(entries, "P", "feed", rss_ingest.make_scorer(), st)
    assert s["entries"] == 3 and s["missing_metadata"] == 2      # a/2 (no title) + a/3 (no date)
    assert s["newest"] == "2026-07-05T00:00:00+00:00" and s["oldest"] == "2026-07-01T00:00:00+00:00"
    assert s["new"] == 3 and s["skipped"] == 0                   # all three are valid absolute URLs


# --------------------------------------------------------------------------- #
# feed staleness — a freshness axis over the newest-article date (observational, additive)
# --------------------------------------------------------------------------- #
NOW = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)


def _rec(newest, *, healthy=True, url="https://f.example/feed"):
    """A minimal FeedHealth-shaped record (only the fields annotate_staleness reads)."""
    return {"feedUrl": url, "healthy": healthy, "consecutiveFailures": 0, "newestPublished": newest}


def test_staleness_flags_old_feed_not_fresh():
    old = (NOW - timedelta(days=60)).isoformat()
    fresh = (NOW - timedelta(days=3)).isoformat()
    out = {r["feedUrl"]: r for r in feed_service.annotate_staleness(
        [_rec(old, url="https://old.example/feed"), _rec(fresh, url="https://fresh.example/feed")],
        threshold_days=30, now=NOW)}
    o, f = out["https://old.example/feed"], out["https://fresh.example/feed"]
    assert o["stale"] is True and round(o["newestAgeDays"]) == 60 and o["staleThresholdDays"] == 30
    assert f["stale"] is False and round(f["newestAgeDays"]) == 3


def test_staleness_undated_feed_is_not_stale():
    out = feed_service.annotate_staleness([_rec(None)], threshold_days=30, now=NOW)[0]
    assert out["stale"] is False and out["newestAgeDays"] is None      # unknown age, not "old"


def test_staleness_is_a_separate_axis_from_availability():
    """The CNN case: a feed that polls successfully (healthy) but only serves old content is BOTH
    healthy AND stale — staleness never changes the availability fields."""
    old = (NOW - timedelta(days=400)).isoformat()
    out = feed_service.annotate_staleness([_rec(old, healthy=True)], threshold_days=30, now=NOW)[0]
    assert out["healthy"] is True and out["stale"] is True


def test_staleness_threshold_from_env(monkeypatch):
    monkeypatch.setenv("RWE_FEED_STALE_DAYS", "7")
    assert feed_service.stale_days() == 7
    ten = (NOW - timedelta(days=10)).isoformat()
    out = feed_service.annotate_staleness([_rec(ten)], now=NOW)[0]     # no explicit threshold -> env
    assert out["staleThresholdDays"] == 7 and out["stale"] is True


def test_staleness_default_threshold_is_30(monkeypatch):
    monkeypatch.delenv("RWE_FEED_STALE_DAYS", raising=False)
    assert feed_service.stale_days() == 30


def test_staleness_disabled_when_threshold_non_positive():
    ancient = (NOW - timedelta(days=9999)).isoformat()
    out = feed_service.annotate_staleness([_rec(ancient)], threshold_days=0, now=NOW)[0]
    assert out["stale"] is False and out["newestAgeDays"] is not None  # age still reported, just not flagged
