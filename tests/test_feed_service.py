"""Tests for the automatic RSS polling service (examples/feed_service.py).

Proves it reuses the existing ingestion (incremental + deduped), survives partial + transient feed
failures, retries, runs its optional cycle hook, and starts/stops gracefully — without duplicating
ingestion or touching any recommendation code."""

import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import store          # noqa: E402
import rss_ingest     # noqa: E402
import feed_service   # noqa: E402


def _rss(items):
    """Minimal RSS 2.0 bytes for (title, link) pairs."""
    body = "".join(
        f"<item><title>{t}</title><link>{u}</link>"
        f"<description>context</description><pubDate>Wed, 02 Oct 2024 12:00:00 GMT</pubDate></item>"
        for t, u in items)
    return (f"<?xml version='1.0'?><rss version='2.0'><channel><title>Demo</title>"
            f"{body}</channel></rss>").encode("utf-8")


def test_enabled_flag(monkeypatch):
    monkeypatch.delenv("RWE_FEED_POLL", raising=False)
    assert feed_service.enabled() is False
    monkeypatch.setenv("RWE_FEED_POLL", "1")
    assert feed_service.enabled() is True
    monkeypatch.setenv("RWE_FEED_POLL", "true")
    assert feed_service.enabled() is True


def test_poll_imports_new_then_dedups(monkeypatch):
    """A poll imports genuinely new articles; a repeat poll imports nothing (dedup by canonical URL)."""
    feeds = {
        "https://a.com/feed": _rss([("A one", "https://a.com/1"), ("A two", "https://a.com/2")]),
        "https://b.com/feed": _rss([("B one", "https://b.com/1")]),
    }
    monkeypatch.setattr(rss_ingest, "fetch_feed", lambda url, timeout=15.0: feeds[url])
    monkeypatch.setenv("RWE_RSS_FEEDS", "https://a.com/feed,https://b.com/feed")
    st = store.Store("sqlite://")
    p = feed_service.FeedPoller(st)

    a1 = p.poll_once()
    assert a1["feeds"] == 2 and a1["ok"] == 2 and a1["failed"] == 0
    assert a1["new"] == 3 and st.count_feed_articles() == 3

    a2 = p.poll_once()                                   # incremental: nothing new the second time
    assert a2["new"] == 0 and a2["duplicates"] == 3 and st.count_feed_articles() == 3


def test_partial_failure_does_not_stop_cycle(monkeypatch):
    """One feed failing never aborts the rest — the healthy feed still ingests."""
    def fetch(url, timeout=15.0):
        if "bad" in url:
            raise OSError("connection refused")
        return _rss([("Good story", "https://good.com/1")])
    monkeypatch.setattr(rss_ingest, "fetch_feed", fetch)
    monkeypatch.setenv("RWE_RSS_FEEDS", "https://bad.com/feed,https://good.com/feed")
    st = store.Store("sqlite://")

    agg = feed_service.FeedPoller(st, retries=0).poll_once()
    assert agg["failed"] == 1 and agg["ok"] == 1 and agg["new"] == 1
    assert st.count_feed_articles() == 1
    assert agg["errors"] and "bad.com" in agg["errors"][0]["feed"]


def test_fetch_retries_then_succeeds(monkeypatch):
    """A transient fetch error is retried (capped exponential backoff) before the feed is ingested."""
    calls = {"n": 0}
    def fetch(url, timeout=15.0):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("transient")
        return _rss([("Ok", "https://ok.com/1")])
    monkeypatch.setattr(rss_ingest, "fetch_feed", fetch)
    monkeypatch.setenv("RWE_RSS_FEEDS", "https://ok.com/feed")
    st = store.Store("sqlite://")

    agg = feed_service.FeedPoller(st, retries=3, backoff=0.001).poll_once()
    assert agg["ok"] == 1 and agg["new"] == 1 and calls["n"] == 3       # 2 failures + 1 success


def test_no_feeds_configured(monkeypatch):
    monkeypatch.delenv("RWE_RSS_FEEDS", raising=False)
    st = store.Store("sqlite://")
    agg = feed_service.FeedPoller(st, feeds_spec="").poll_once()
    assert agg["feeds"] == 0 and agg["new"] == 0 and agg["catalog"] == 0


def test_on_cycle_hook_receives_stats(monkeypatch):
    """The optional hook (a later commit's hot-refresh seam) is called with the cycle stats."""
    monkeypatch.setattr(rss_ingest, "fetch_feed", lambda url, timeout=15.0: _rss([("X", "https://x.com/1")]))
    monkeypatch.setenv("RWE_RSS_FEEDS", "https://x.com/feed")
    seen = []
    st = store.Store("sqlite://")
    feed_service.FeedPoller(st, on_cycle=lambda agg: seen.append(agg["new"])).poll_once()
    assert seen == [1]


def test_on_cycle_error_never_breaks_poll(monkeypatch):
    """A raising hook is swallowed — the poll still succeeds and the catalog is updated."""
    monkeypatch.setattr(rss_ingest, "fetch_feed", lambda url, timeout=15.0: _rss([("Y", "https://y.com/1")]))
    monkeypatch.setenv("RWE_RSS_FEEDS", "https://y.com/feed")
    st = store.Store("sqlite://")
    def boom(_agg):
        raise RuntimeError("downstream refresh failed")
    agg = feed_service.FeedPoller(st, on_cycle=boom).poll_once()
    assert agg["new"] == 1 and st.count_feed_articles() == 1


def test_start_stop_graceful(monkeypatch):
    """The background thread polls on its interval and stops promptly + cleanly."""
    monkeypatch.setattr(rss_ingest, "fetch_feed", lambda url, timeout=15.0: _rss([("Z", "https://z.com/1")]))
    monkeypatch.setenv("RWE_RSS_FEEDS", "https://z.com/feed")
    st = store.Store("sqlite://")
    p = feed_service.FeedPoller(st, interval=0.05)
    assert p.running is False
    p.start()
    deadline = time.time() + 5
    while st.count_feed_articles() < 1 and time.time() < deadline:
        time.sleep(0.02)
    assert p.running is True and st.count_feed_articles() >= 1
    p.stop(join_timeout=5)
    assert p.running is False
