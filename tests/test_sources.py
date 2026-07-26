"""Tests for the multi-source ingestion layer (Commit 11) — examples/sources.py.

Proves: adapter normalization into the SINGLE FeedEntry shape, cross-source canonical-URL
deduplication + additive metadata merge, source-priority media merge, per-source quotas, per-source
health under stable keys, poll-failure isolation, the SourceRegistry, SourceBatch, and backward
compatibility (RSS behaviour + existing store/ingest APIs unchanged). Everything runs OFFLINE via the
injectable fetch — no network is contacted.
"""

import pathlib
import sys
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import store as store_mod   # noqa: E402
import rss_ingest as ri     # noqa: E402
import sources              # noqa: E402


@pytest.fixture
def store():
    return store_mod.Store("sqlite://")


NEWSAPI_JSON = {
    "status": "ok", "totalResults": 2,
    "articles": [
        {"source": {"id": None, "name": "CNN"}, "title": "Senate passes the funding bill",
         "description": "desc", "url": "https://cnn.com/2026/politics/senate-bill",
         "urlToImage": "https://cdn.cnn.com/img/senate.jpg", "publishedAt": "2026-07-08T10:00:00Z",
         "content": "Full content [+1200 chars]"},
        {"source": {"id": None, "name": "NPR"}, "title": "Climate deal reached", "description": "d2",
         "url": "https://npr.org/2026/climate", "urlToImage": "https://npr.org/x",   # extensionless
         "publishedAt": "2026-07-08T09:00:00Z", "content": "c2"},
    ],
}
GDELT_JSON = {
    "articles": [
        {"url": "https://bbc.com/news/world-1", "title": "World summit begins",
         "seendate": "20260708T083000Z", "socialimage": "https://bbc.com/img/summit.jpg",
         "domain": "bbc.com", "language": "English", "sourcecountry": "United Kingdom"},
        {"url": "https://reuters.com/markets/x", "title": "Markets update", "seendate": "20260708T070000Z",
         "socialimage": "", "domain": "reuters.com", "language": "English", "sourcecountry": "United States"},
    ],
}


# --------------------------------------------------------------------------- #
# Normalization — every source produces the SAME FeedEntry shape
# --------------------------------------------------------------------------- #
def test_newsapi_normalizes_into_feedentry():
    batch = sources.NewsAPIAdapter(fetch=lambda url: NEWSAPI_JSON).normalize(NEWSAPI_JSON)
    assert batch.provider == "NewsAPI" and batch.source_type == "newsapi" and batch.raw_count == 2
    assert len(batch) == 2 and all(isinstance(e, ri.FeedEntry) for e in batch.entries)
    e = batch.entries[0]
    assert e.url == "https://cnn.com/2026/politics/senate-bill" and e.title.startswith("Senate")
    assert e.body == "Full content [+1200 chars]" and e.published_at.startswith("2026-07-08T10:00")
    assert e.source_type == "newsapi" and e.source_provider == "NewsAPI"
    assert e.image == "https://cdn.cnn.com/img/senate.jpg" and e.image_source == "newsapi"
    assert e.publisher_hint == "CNN" and e.external_id == e.url
    # media.py (unmodified) drops an image URL with no recognizable image extension -> null, not fabricated
    assert batch.entries[1].image is None


def test_gdelt_normalizes_into_feedentry():
    batch = sources.GDELTAdapter(fetch=lambda url: GDELT_JSON).normalize(GDELT_JSON)
    assert batch.provider == "GDELT" and batch.source_type == "gdelt" and len(batch) == 2
    e = batch.entries[0]
    assert e.url == "https://bbc.com/news/world-1" and e.published_at == "2026-07-08T08:30:00+00:00"
    assert e.image == "https://bbc.com/img/summit.jpg" and e.country == "United Kingdom"
    assert e.publisher_hint == "bbc.com" and e.source_type == "gdelt"
    assert batch.entries[1].image is None                      # empty socialimage -> null


def test_poll_once_ingests_with_source_attribution(store):
    agg = sources.NewsAPIAdapter(fetch=lambda url: NEWSAPI_JSON).poll_once(store, ri.make_scorer())
    assert agg["provider"] == "NewsAPI" and agg["new"] == 2 and agg["failed"] == 0 and agg["rawCount"] == 2
    row = store.get_feed_article("https://cnn.com/2026/politics/senate-bill")
    assert row["sourceType"] == "newsapi" and row["sourceProvider"] == "NewsAPI"
    assert row["externalId"] == row["canonicalUrl"] and row["publisher"] == "CNN"


# --------------------------------------------------------------------------- #
# Cross-source dedup + additive merge (one FeedArticle for one canonical URL)
# --------------------------------------------------------------------------- #
def _ingest(store, source_type, provider, *, url, image=None, description="", scorer=None):
    e = ri.FeedEntry(url=url, title="Senate passes the funding bill", description=description,
                     published_at="2026-07-08T10:00:00Z", image=image, image_source=source_type,
                     source_type=source_type, source_provider=provider, external_id=url)
    return ri.ingest_entries([e], provider, f"{source_type}://x", scorer or ri.make_scorer(), store,
                             source_type=source_type)


def test_cross_source_dedup_one_article(store):
    url = "https://cnn.com/2026/politics/senate-bill"
    sc = ri.make_scorer()
    assert _ingest(store, "newsapi", "NewsAPI", url=url, description="from newsapi", scorer=sc)["new"] == 1
    assert _ingest(store, "gdelt", "GDELT", url=url, scorer=sc)["new"] == 0        # duplicate
    assert _ingest(store, "rss", "RSS", url=url, scorer=sc)["new"] == 0            # duplicate
    assert store.count_feed_articles() == 1                                        # ONE row across sources
    row = store.get_feed_article(url)
    assert row["sourceType"] == "newsapi"                                          # first-seen provenance kept
    assert row["description"] == "from newsapi"


def test_metadata_merge_backfills_empty_fields(store):
    url = "https://x.example/a"
    sc = ri.make_scorer()
    # first source has no description; a later source supplies one -> backfilled (additive merge)
    _ingest(store, "gdelt", "GDELT", url=url, description="", scorer=sc)
    _ingest(store, "newsapi", "NewsAPI", url=url, description="a fuller description", scorer=sc)
    assert store.get_feed_article(url)["description"] == "a fuller description"


# --------------------------------------------------------------------------- #
# Source-priority media merge (RSS=100 > NewsAPI=80 > GDELT=60), no persisted priority
# --------------------------------------------------------------------------- #
def test_media_priority_higher_source_wins_regardless_of_order(store):
    url = "https://cnn.com/x"
    sc = ri.make_scorer()
    _ingest(store, "newsapi", "NewsAPI", url=url, image="https://cdn/newsapi.jpg", scorer=sc)
    _ingest(store, "gdelt", "GDELT", url=url, image="https://cdn/gdelt.jpg", scorer=sc)   # 60 < 80: keep
    assert store.get_feed_article(url)["image"] == "https://cdn/newsapi.jpg"
    _ingest(store, "rss", "RSS", url=url, image="https://cdn/rss.jpg", scorer=sc)         # 100 > 80: win
    assert store.get_feed_article(url)["image"] == "https://cdn/rss.jpg"


def test_media_priority_lower_never_overrides(store):
    url = "https://cnn.com/y"
    sc = ri.make_scorer()
    _ingest(store, "rss", "RSS", url=url, image="https://cdn/rss.jpg", scorer=sc)
    _ingest(store, "newsapi", "NewsAPI", url=url, image="https://cdn/newsapi.jpg", scorer=sc)  # 80 < 100
    assert store.get_feed_article(url)["image"] == "https://cdn/rss.jpg"                   # RSS preserved
    # equal priority preserves the existing image
    _ingest(store, "rss", "RSS", url=url, image="https://cdn/rss2.jpg", scorer=sc)
    assert store.get_feed_article(url)["image"] == "https://cdn/rss.jpg"


def test_media_priority_fills_empty(store):
    url = "https://cnn.com/z"
    sc = ri.make_scorer()
    _ingest(store, "gdelt", "GDELT", url=url, image=None, scorer=sc)              # no image
    _ingest(store, "newsapi", "NewsAPI", url=url, image="https://cdn/newsapi.jpg", scorer=sc)
    assert store.get_feed_article(url)["image"] == "https://cdn/newsapi.jpg"      # empty slot filled


def test_source_priority_env_override(monkeypatch):
    monkeypatch.setenv("RWE_SOURCE_PRIORITY", "gdelt:200")
    assert store_mod._media_priority("gdelt") == 200 and store_mod._media_priority("rss") == 100


def _ing_img(store, stype, image, image_source, *, url, scorer):
    """Ingest one entry carrying a realistic per-source image tag (RSS uses a media tag, not 'rss')."""
    e = ri.FeedEntry(url=url, title="t", published_at="2026-07-08T10:00:00Z", image=image,
                     image_source=image_source, source_type=stype, source_provider=stype.upper(),
                     external_id=url)
    ri.ingest_entries([e], stype.upper(), f"{stype}://x", scorer, store, source_type=stype)


def test_media_priority_uses_stored_image_source_not_article_origin(store):
    """Regression: GDELT origin -> RSS upgrades the image -> NewsAPI must NOT replace it. Precedence for
    the stored image comes from ``image_source`` (the RSS media tag maps to rss=100), not the row's
    origin ``source_type`` (still gdelt=60)."""
    url, sc = "https://cnn.com/seq", ri.make_scorer()
    _ing_img(store, "gdelt", "https://img/gdelt.jpg", "gdelt", url=url, scorer=sc)
    assert store.get_feed_article(url)["image"] == "https://img/gdelt.jpg"
    _ing_img(store, "rss", "https://img/rss.jpg", "media:content", url=url, scorer=sc)   # real RSS tag
    assert store.get_feed_article(url)["image"] == "https://img/rss.jpg"                 # RSS(100) > GDELT(60)
    _ing_img(store, "newsapi", "https://img/newsapi.jpg", "newsapi", url=url, scorer=sc)
    assert store.get_feed_article(url)["image"] == "https://img/rss.jpg"                 # RSS(100) > NewsAPI(80): KEPT
    assert store.get_feed_article(url)["sourceType"] == "gdelt"                          # origin provenance unchanged


def test_stored_image_priority_maps_source_correctly():
    p = store_mod._stored_image_priority
    assert p("media:content", "gdelt") == 100 and p("enclosure", None) == 100    # RSS media tags -> rss
    assert p("newsapi", "gdelt") == 80 and p("gdelt", "rss") == 60               # adapter tags -> its source
    assert p(None, "newsapi") == 80 and p("", "rss") == 100                      # untagged -> row origin
    assert p(None, None) == 0


def test_normalize_image_source_contract():
    """The hardened contract: every image_source maps to exactly one of rss/newsapi/gdelt/unknown."""
    n = store_mod.normalize_image_source
    for tag in ("media:content", "media:thumbnail", "media:group", "enclosure", "atom:link", "rss"):
        assert n(tag) == "rss", tag                          # every RSS/Atom media tag -> rss
    assert n("newsapi") == "newsapi" and n("gdelt") == "gdelt"
    assert n(None) == "unknown" and n("") == "unknown" and n("some-cdn-source") == "unknown"
    assert n("NewsAPI") == "newsapi" and n("MEDIA:CONTENT") == "rss"   # case-insensitive


def test_unknown_image_source_never_gets_rss_priority():
    """Hardening regression: a non-empty but unrecognised image_source must NOT inherit RSS priority
    (previously any unrecognised tag fell through to rss=100)."""
    assert store_mod.normalize_image_source("weird-cdn-tag") == "unknown"
    assert store_mod._stored_image_priority("weird-cdn-tag", "gdelt") == 0        # unknown -> 0, not 100
    # so a real source can still replace an unknown-sourced image, and a lower one still can't beat RSS
    assert store_mod._media_priority("newsapi") > store_mod._stored_image_priority("weird-cdn-tag", None)
    assert store_mod._media_priority("newsapi") < store_mod._stored_image_priority("media:content", None)


# --------------------------------------------------------------------------- #
# Quotas — truncate BEFORE ingest_entries
# --------------------------------------------------------------------------- #
def test_quota_truncates_before_ingest(store, monkeypatch):
    monkeypatch.setenv("RWE_NEWSAPI_MAX_ARTICLES", "1")
    agg = sources.NewsAPIAdapter(fetch=lambda url: NEWSAPI_JSON).poll_once(store, ri.make_scorer())
    assert agg["rawCount"] == 2 and agg["new"] == 1 and store.count_feed_articles() == 1


# --------------------------------------------------------------------------- #
# SourceRegistry + enabled filtering
# --------------------------------------------------------------------------- #
def test_source_registry_enabled_filtering(monkeypatch):
    monkeypatch.delenv("RWE_FEED_POLL", raising=False)
    monkeypatch.setenv("RWE_RSS_ENABLED", "false")
    monkeypatch.setenv("RWE_NEWSAPI_ENABLED", "true")
    monkeypatch.setenv("RWE_NEWSAPI_API_KEY", "k")
    monkeypatch.delenv("RWE_GDELT_ENABLED", raising=False)
    reg = sources.default_registry()
    # registered order (GDELT-GKG = the Phase-2 event-geography enricher, intended addition)
    assert [a.provider for a in reg.adapters()] == ["RSS", "NewsAPI", "GDELT", "GDELT-GKG"]
    assert [a.provider for a in reg.enabled()] == ["NewsAPI"]                      # only NewsAPI enabled
    monkeypatch.setenv("RWE_GDELT_ENABLED", "1")
    assert {a.provider for a in reg.enabled()} == {"NewsAPI", "GDELT"}
    # NewsAPI without a key is not enabled even when the flag is on
    monkeypatch.delenv("RWE_NEWSAPI_API_KEY", raising=False)
    assert [a.provider for a in reg.enabled()] == ["GDELT"]


def test_source_batch_shape():
    b = sources.SourceBatch("NewsAPI", "newsapi", "2026-07-08T00:00:00Z", entries=[1, 2, 3], raw_count=3)
    assert b.provider == "NewsAPI" and b.source_type == "newsapi" and len(b) == 3 and b.error is None


def test_gdelt_default_query_is_news_oriented(monkeypatch):
    # the default carries topic keywords — a bare "sourcelang:english" returns non-news junk
    assert "sourcelang:english" in sources.DEFAULT_GDELT_QUERY
    assert sources.DEFAULT_GDELT_QUERY != "sourcelang:english" and "politics" in sources.DEFAULT_GDELT_QUERY.lower()
    monkeypatch.delenv("RWE_GDELT_QUERY", raising=False)
    assert "politics" in sources.GDELTAdapter()._url().lower()          # default applied to the URL
    monkeypatch.setenv("RWE_GDELT_QUERY", "climate")
    assert "query=climate" in sources.GDELTAdapter()._url()             # explicit override wins


def test_newsapi_config_warning_when_enabled_without_key(monkeypatch):
    monkeypatch.setenv("RWE_NEWSAPI_ENABLED", "1")
    monkeypatch.delenv("RWE_NEWSAPI_API_KEY", raising=False)
    a = sources.NewsAPIAdapter()
    assert a.enabled() is False
    assert a.config_warning() and "RWE_NEWSAPI_API_KEY" in a.config_warning()   # points at the fix
    monkeypatch.setenv("RWE_NEWSAPI_API_KEY", "k")                              # a key clears it + enables
    assert a.enabled() is True and a.config_warning() is None
    monkeypatch.setenv("RWE_NEWSAPI_ENABLED", "0")                              # flag off -> nothing intended
    assert sources.NewsAPIAdapter().config_warning() is None


def test_config_warnings_collects_only_misconfigured(monkeypatch):
    monkeypatch.setenv("RWE_NEWSAPI_ENABLED", "1")
    monkeypatch.delenv("RWE_NEWSAPI_API_KEY", raising=False)
    ws = sources.config_warnings(sources.default_registry())
    assert len(ws) == 1 and "NewsAPI" in ws[0]                          # only NewsAPI is misconfigured
    assert sources.GDELTAdapter().config_warning() is None              # keyless -> never misconfigured
    assert sources.RSSAdapter().config_warning() is None


def test_get_json_retries_transient_429(monkeypatch):
    """A 429 (common for GDELT on shared IPs) is retried with backoff and then succeeds."""
    import urllib.error
    calls = {"n": 0}

    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok": true}'

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)
        return _Resp()

    monkeypatch.setattr(sources.urllib.request, "urlopen", fake_urlopen)
    out = sources._get_json("https://x.example/y", retries=3, backoff=0)   # backoff=0 -> no real sleep
    assert out == {"ok": True} and calls["n"] == 3          # retried twice, succeeded on the third


def test_get_json_does_not_retry_non_transient_401(monkeypatch):
    import urllib.error
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(sources.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(urllib.error.HTTPError):
        sources._get_json("https://x.example/y", retries=3, backoff=0)
    assert calls["n"] == 1                                  # 401 is not transient -> no retry


def test_sources_cli_check_lists_adapters(monkeypatch, capsys, tmp_path):
    """`python examples/sources.py check` lists every adapter's status + a by-source summary, and does
    NOT ingest (all disabled here -> no network)."""
    monkeypatch.setenv("RWE_DB_URL", f"sqlite:///{tmp_path / 'c.db'}")
    for v in ("RWE_FEED_POLL", "RWE_RSS_ENABLED", "RWE_NEWSAPI_ENABLED", "RWE_GDELT_ENABLED"):
        monkeypatch.delenv(v, raising=False)
    rc = sources.main(["check"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "RSS" in out and "NewsAPI" in out and "GDELT" in out and "disabled" in out
    assert "catalog:" in out and "by source:" in out


# --------------------------------------------------------------------------- #
# Health monitoring — per source, under a stable key (reuses store.record_feed_health)
# --------------------------------------------------------------------------- #
def test_health_recorded_per_source_key(store):
    poller = sources.MultiSourcePoller(store, ri.make_scorer(),
                                       registry=sources.SourceRegistry(), log=lambda *a, **k: None)
    poller.poll_adapter_once(sources.NewsAPIAdapter(fetch=lambda url: NEWSAPI_JSON))
    poller.poll_adapter_once(sources.GDELTAdapter(fetch=lambda url: GDELT_JSON))
    health = {h["feedUrl"]: h for h in store.list_feed_health()}
    assert "newsapi://top-headlines" in health and "gdelt://doc" in health
    assert health["newsapi://top-headlines"]["healthy"] is True and health["newsapi://top-headlines"]["totalOk"] == 1
    assert health["gdelt://doc"]["healthy"] is True


def test_poll_failure_is_isolated_within_adapter(store):
    def boom(url):
        raise OSError("connection refused")
    agg = sources.NewsAPIAdapter(fetch=boom).poll_once(store, ri.make_scorer())      # must NOT raise
    assert agg["failed"] == 1 and agg["ok"] == 0 and agg["new"] == 0
    assert "connection refused" in agg["errors"][0]["error"]


# --------------------------------------------------------------------------- #
# MultiSourcePoller — one thread per adapter, one adapter's outage never stops another
# --------------------------------------------------------------------------- #
class _FakeAdapter(sources.SourceAdapter):
    def __init__(self, provider, source_type, *, boom=False, marker=None):
        self.provider, self.source_type, self._boom, self._marker = provider, source_type, boom, marker
        self.polled = threading.Event()

    def enabled(self):
        return True

    def interval(self):
        return 3600.0                       # poll once immediately, then sleep long

    def poll_once(self, store_, scorer, *, on_feed=None):
        self.polled.set()
        if self._boom:
            raise RuntimeError("adapter down")     # a hard failure the poller thread must contain
        return _ingest(store_, self.source_type, self.provider, url=self._marker, scorer=scorer)


def test_multisource_poller_isolates_a_failing_adapter(store):
    reg = sources.SourceRegistry()
    good = reg.register(_FakeAdapter("Good", "newsapi", marker="https://good.example/1"))
    boom = reg.register(_FakeAdapter("Boom", "gdelt", boom=True))
    poller = sources.MultiSourcePoller(store, ri.make_scorer(), registry=reg, log=lambda *a, **k: None)
    poller.start()
    try:
        assert good.polled.wait(5) and boom.polled.wait(5)      # both threads ran (isolated)
    finally:
        poller.stop()
    # the failing adapter did not prevent the healthy one from ingesting
    assert store.get_feed_article("https://good.example/1") is not None


# --------------------------------------------------------------------------- #
# Backward compatibility — RSS behaviour + existing store/ingest APIs unchanged
# --------------------------------------------------------------------------- #
def test_rss_ingest_all_tags_source_type_rss(store):
    rss = (b"<?xml version='1.0'?><rss><channel><title>C</title>"
           b"<item><title>Headline</title><link>https://guardian.example/a</link>"
           b"<pubDate>Wed, 08 Jul 2026 10:00:00 GMT</pubDate></item></channel></rss>")
    ri.ingest_all([("Guardian", "https://guardian.example/rss")], ri.make_scorer(), store,
                  fetch=lambda url: rss)
    row = store.get_feed_article("https://guardian.example/a")
    assert row["sourceType"] == "rss" and row["sourceProvider"] == "Guardian"


def test_existing_ingest_entries_without_source_kwargs_still_works(store):
    # a pre-Commit-11 caller passes no source_* kwargs -> stored as None, behaviour unchanged
    e = ri.FeedEntry(url="https://x.example/legacy", title="t", published_at="2026-07-08T00:00:00Z")
    ri.ingest_entries([e], "Legacy", "feed://legacy", ri.make_scorer(), store)
    row = store.get_feed_article("https://x.example/legacy")
    assert row is not None and row["sourceProvider"] == "Legacy"     # falls back to source_publisher


def test_existing_upsert_without_source_params_is_unchanged(store):
    # calling upsert_feed_article the old way (no source_type/provider/external_id) still works
    created = store.upsert_feed_article(
        canonical_url="https://x.example/u", url="https://x.example/u", publisher="P",
        source_publisher="P", title="t", description="", body=None, published_at=None,
        source_feed="f", scored={"article_id": "https://x.example/u", "outlet": "P", "lean": 0.0})
    assert created is True
    row = store.get_feed_article("https://x.example/u")
    assert row["sourceType"] is None and row["externalId"] is None   # legacy rows keep NULL provenance
