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
    # registered order (GDELT-GKG = the Phase-2 event-geography enricher, kept LAST — it
    # annotates articles the others ingested; the six 2026-07 providers sit between NewsAPI
    # and GDELT and are all disabled by default)
    assert [a.provider for a in reg.adapters()] == [
        "RSS", "NewsAPI", "Guardian", "NewsData", "GNews", "MediaStack", "Currents",
        "GoogleNews", "GDELT", "GDELT-GKG"]
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


# --------------------------------------------------------------------------- #
# NewsAPI production hardening: page size, list rotation, daily budget, 429 accounting.
# --------------------------------------------------------------------------- #
def test_newsapi_page_size_env_and_clamp(monkeypatch):
    """Explicit RWE_NEWSAPI_PAGE_SIZE wins (clamped to NewsAPI's 1..100); without it the page
    size never fetches more than the ingest quota would keep."""
    a = sources.NewsAPIAdapter(fetch=lambda url: {"articles": []})
    monkeypatch.setenv("RWE_NEWSAPI_PAGE_SIZE", "25")
    assert "pageSize=25" in a._url({})
    monkeypatch.setenv("RWE_NEWSAPI_PAGE_SIZE", "500")
    assert "pageSize=100" in a._url({})                     # clamped to the API cap
    monkeypatch.delenv("RWE_NEWSAPI_PAGE_SIZE")
    monkeypatch.setenv("RWE_NEWSAPI_MAX_ARTICLES", "5")
    assert "pageSize=5" in a._url({})                       # quota-bounded fallback
    monkeypatch.delenv("RWE_NEWSAPI_MAX_ARTICLES")
    assert "pageSize=100" in a._url({})


def test_newsapi_rotation_cycles_combos_and_stamps_entries(monkeypatch):
    """Comma-separated CATEGORY/COUNTRY lists rotate ONE combination per fetch (N combos never
    multiply the request rate), and each batch's entries are stamped with the combo the fetch
    actually used — not a stale env read."""
    monkeypatch.setenv("RWE_NEWSAPI_CATEGORY", "business,technology")
    monkeypatch.setenv("RWE_NEWSAPI_COUNTRY", "us,gb")
    urls = []
    art = {"source": {"name": "X"}, "title": "t", "url": "https://x.example/1",
           "publishedAt": "2026-07-08T10:00:00Z"}
    a = sources.NewsAPIAdapter(fetch=lambda url: (urls.append(url) or {"articles": [dict(art)]}))
    combos_seen = []
    for _ in range(5):                                      # 4 combos -> the 5th wraps around
        batch = a.normalize(a.fetch())
        e = batch.entries[0]
        combos_seen.append((e.country, e.category))
    assert combos_seen[:4] == [("us", "business"), ("us", "technology"),
                               ("gb", "business"), ("gb", "technology")]
    assert combos_seen[4] == combos_seen[0]                 # rotation wraps deterministically
    assert len(urls) == 5 and all(u.count("country=") == 1 for u in urls)
    assert "country=us" in urls[0] and "category=business" in urls[0]
    assert "country=gb" in urls[2]


def test_newsapi_daily_budget_short_circuits_before_fetch(store, monkeypatch):
    """A spent RWE_NEWSAPI_DAILY_BUDGET SKIPS the cycle before any request: no fetch call, no
    error, budgetExhausted on the aggregate — never a failure row for a deliberate skip."""
    monkeypatch.setenv("RWE_NEWSAPI_DAILY_BUDGET", "2")
    calls = []
    a = sources.NewsAPIAdapter(fetch=lambda url: (calls.append(url) or NEWSAPI_JSON))
    sc = ri.make_scorer()
    first = a.poll_once(store, sc)
    second = a.poll_once(store, sc)
    third = a.poll_once(store, sc)
    assert len(calls) == 2                                  # the third cycle never fetched
    assert first["failed"] == 0 and "budgetExhausted" not in first
    assert third.get("budgetExhausted") is True and third["failed"] == 0 and third["new"] == 0
    assert all("rateLimited" in agg for agg in (first, second, third))


def test_get_json_reports_transient_429s_then_succeeds(monkeypatch):
    """_get_json retries 429 with backoff AND surfaces every transient hit through on_transient —
    rate-limit pressure is counted, never silently absorbed by the retry loop."""
    import io
    import urllib.error

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"status": "ok"}'

    attempts = {"n": 0}

    def fake_urlopen(req, timeout=None):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", None, io.BytesIO(b""))
        return _Resp()

    monkeypatch.setattr(sources.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    events = []
    out = sources._get_json("https://newsapi.org/v2/top-headlines?country=us",
                            on_transient=events.append)
    assert out == {"status": "ok"} and events == [429, 429] and attempts["n"] == 3


# --------------------------------------------------------------------------- #
# Six-provider expansion on the shared chassis: Guardian, NewsData, GNews, MediaStack,
# Currents (KeyedJSONAdapter) + Google News RSS (keyless XML). Same contract as every other
# adapter: normalize into FeedEntry, resolve publishers through the registry downstream.
# --------------------------------------------------------------------------- #
GUARDIAN_JSON = {
    "response": {"status": "ok", "total": 2, "results": [
        {"id": "world/2026/jul/26/summit", "sectionName": "World news",
         "webPublicationDate": "2026-07-26T09:00:00Z", "webTitle": "Summit opens in Geneva",
         "webUrl": "https://www.theguardian.com/world/2026/jul/26/summit",
         "fields": {"trailText": "Leaders gather.", "thumbnail": "https://media.guim.co.uk/x/500.jpg"}},
        {"id": "politics/2026/jul/26/vote", "sectionName": "Politics",
         "webPublicationDate": "2026-07-26T08:00:00Z", "webTitle": "Vote scheduled",
         "webUrl": "https://www.theguardian.com/politics/2026/jul/26/vote"},
    ]},
}
NEWSDATA_JSON = {
    "status": "success", "results": [
        {"article_id": "nd1", "title": "Grid upgrade announced", "link": "https://example-post.com/grid",
         "description": "d", "pubDate": "2026-07-26 10:30:00", "image_url": "https://cdn.example-post.com/g.png",
         "source_id": "example_post", "source_name": "Example Post",
         "country": ["united states of america"], "category": ["technology"], "language": "english"},
        {"article_id": "nd2", "title": "No link", "link": "", "pubDate": "2026-07-26 09:00:00"},
    ],
}
GNEWS_JSON = {
    "totalArticles": 1, "articles": [
        {"title": "Rates held steady", "description": "d", "content": "full text",
         "url": "https://apnews.com/article/rates-1", "image": "https://apnews.com/img/r.jpg",
         "publishedAt": "2026-07-26T11:00:00Z",
         "source": {"name": "Associated Press", "url": "https://apnews.com"}},
    ],
}
MEDIASTACK_JSON = {
    "pagination": {"count": 1}, "data": [
        {"author": None, "title": "Port reopens", "description": "d",
         "source": "Reuters", "url": "https://reuters.com/world/port-1",
         "image": "https://reuters.com/img/p.jpg", "category": "general",
         "language": "en", "country": "gb", "published_at": "2026-07-26T07:45:00+00:00"},
    ],
}
CURRENTS_JSON = {
    "status": "ok", "news": [
        {"id": "cu1", "title": "Reactor milestone", "description": "d",
         "url": "https://www.newscientist.com/article/reactor",
         "author": "", "image": "None", "language": "en",
         "category": ["science"], "published": "2026-07-26 06:15:00 +0000"},
    ],
}
GOOGLENEWS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Top stories - Google News</title>
<item>
  <title>Ceasefire talks resume - BBC News</title>
  <link>https://news.google.com/rss/articles/CBMiAbc?oc=5</link>
  <pubDate>Sun, 26 Jul 2026 05:00:00 GMT</pubDate>
  <description>snippet</description>
  <source url="https://www.bbc.com">BBC News</source>
</item>
<item>
  <title>Untitled wire item</title>
  <link>https://news.google.com/rss/articles/CBMiDef?oc=5</link>
  <pubDate>Sun, 26 Jul 2026 04:00:00 GMT</pubDate>
</item>
<item>
  <title>No link item</title>
</item>
</channel></rss>"""


def test_guardian_normalizes_into_feedentry():
    batch = sources.GuardianAdapter(fetch=lambda url: GUARDIAN_JSON).normalize(GUARDIAN_JSON)
    assert batch.provider == "Guardian" and batch.source_type == "guardian" and batch.raw_count == 2
    e = batch.entries[0]
    assert e.url == "https://www.theguardian.com/world/2026/jul/26/summit"
    assert e.title == "Summit opens in Geneva" and e.description == "Leaders gather."
    assert e.published_at.startswith("2026-07-26T09:00") and e.category == "World news"
    assert e.image == "https://media.guim.co.uk/x/500.jpg" and e.image_source == "guardian"
    assert e.external_id == "world/2026/jul/26/summit"
    assert e.publisher_hint == "theguardian.com"            # single-outlet source: hint is FIXED
    assert batch.entries[1].image is None                   # no fields block -> no fabricated image


def test_guardian_resolves_registry_publisher_and_lean(store):
    """The passthrough that makes the whole integration honest: a Guardian article lands with the
    registry's CANONICAL outlet + verified lean — no per-provider publisher logic anywhere."""
    agg = sources.GuardianAdapter(fetch=lambda url: GUARDIAN_JSON).poll_once(store, ri.make_scorer())
    assert agg["new"] == 2 and agg["failed"] == 0
    # canonical_url strips "www." — the same canonicalization every source shares (dedup key).
    row = store.get_feed_article("https://theguardian.com/world/2026/jul/26/summit")
    assert row["publisher"] == "The Guardian"               # canonical, via outlet_registry.csv
    assert row["scored"]["lean"] == -1.0                    # verified Lean Left, from the registry
    assert row["sourceType"] == "guardian" and row["sourceProvider"] == "Guardian"


def test_unknown_publisher_stays_honest_no_lean(store):
    """An outlet the registry does not know ingests fine but casts NO lean vote (NaN) — counted in
    unknown_outlet stats, never guessed. This is the fail-honest contract for all new providers."""
    agg = sources.NewsDataAdapter(fetch=lambda url: NEWSDATA_JSON).poll_once(store, ri.make_scorer())
    assert agg["new"] == 1
    row = store.get_feed_article("https://example-post.com/grid")
    assert row["publisher"] == "Example Post"               # hint kept as the outlet name
    lean = row["scored"]["lean"]
    assert lean is None or lean != lean                     # NaN/None — honestly unknown, no default


def test_newsdata_normalizes_into_feedentry():
    batch = sources.NewsDataAdapter(fetch=lambda url: NEWSDATA_JSON).normalize(NEWSDATA_JSON)
    assert batch.provider == "NewsData" and batch.raw_count == 2 and len(batch) == 1   # linkless dropped
    e = batch.entries[0]
    assert e.url == "https://example-post.com/grid" and e.external_id == "nd1"
    assert e.published_at is not None and e.published_at.startswith("2026-07-26T10:30")  # space-date form
    assert e.category == "technology" and e.country == "united states of america"
    assert e.language == "english"                          # resolver normalizes names downstream
    assert e.publisher_hint == "Example Post" and e.image == "https://cdn.example-post.com/g.png"


def test_gnews_normalizes_into_feedentry(monkeypatch):
    monkeypatch.setenv("RWE_GNEWS_LANGUAGE", "en")
    a = sources.GNewsAdapter(fetch=lambda url: GNEWS_JSON)
    batch = a.normalize(GNEWS_JSON)
    e = batch.entries[0]
    assert e.url == "https://apnews.com/article/rates-1" and e.body == "full text"
    assert e.publisher_hint == "apnews.com"                 # source.url reduced to its bare host —
    assert e.language == "en"                               # never a scheme-bearing outlet name
    assert e.source_type == "gnews" and e.source_provider == "GNews"
    named = dict(GNEWS_JSON["articles"][0], source={"name": "Associated Press", "url": ""})
    e2 = sources.GNewsAdapter(fetch=lambda url: {})._entry(named, {})
    assert e2.publisher_hint == "Associated Press"          # no URL -> display name fallback


def test_mediastack_normalizes_into_feedentry():
    batch = sources.MediaStackAdapter(fetch=lambda url: MEDIASTACK_JSON).normalize(MEDIASTACK_JSON)
    e = batch.entries[0]
    assert e.url == "https://reuters.com/world/port-1" and e.publisher_hint == "Reuters"
    assert e.published_at is not None and e.published_at.startswith("2026-07-26T07:45")
    assert e.category == "general" and e.language == "en" and e.country == "gb"
    assert e.source_type == "mediastack" and e.source_provider == "MediaStack"


def test_mediastack_url_scheme_and_plural_params(monkeypatch):
    """Free-tier honesty: HTTPS is paid-only on MediaStack, so RWE_MEDIASTACK_HTTPS=0 switches to
    http; axis params use MediaStack's PLURAL names (countries/categories/languages)."""
    monkeypatch.setenv("RWE_MEDIASTACK_API_KEY", "k")
    monkeypatch.setenv("RWE_MEDIASTACK_COUNTRY", "gb")
    a = sources.MediaStackAdapter(fetch=lambda url: MEDIASTACK_JSON)
    url = a._url(a._combos()[0])
    assert url.startswith("https://") and "countries=gb" in url and "access_key=k" in url
    monkeypatch.setenv("RWE_MEDIASTACK_HTTPS", "0")
    assert a._url(a._combos()[0]).startswith("http://")
    assert a.interval() == 28800.0                          # 8-h default fits the 100 req/MONTH free tier


def test_currents_normalizes_into_feedentry():
    batch = sources.CurrentsAdapter(fetch=lambda url: CURRENTS_JSON).normalize(CURRENTS_JSON)
    e = batch.entries[0]
    assert e.url == "https://www.newscientist.com/article/reactor" and e.external_id == "cu1"
    assert e.publisher_hint == "newscientist.com"           # no source field -> URL host, www-stripped
    assert e.image is None                                  # literal "None" string dropped, never stored
    assert e.published_at is not None and e.published_at.startswith("2026-07-26T06:15")  # "+0000" form
    assert e.category == "science" and e.language == "en"


def test_googlenews_normalizes_with_source_tags(monkeypatch):
    monkeypatch.setenv("RWE_GOOGLENEWS_TOPICS", "WORLD")
    a = sources.GoogleNewsAdapter(fetch_bytes=lambda url: GOOGLENEWS_XML)
    batch = a.normalize(a.fetch())
    assert batch.provider == "GoogleNews" and batch.source_type == "googlenews"
    assert batch.raw_count == 3 and len(batch) == 2         # linkless item dropped
    e = batch.entries[0]
    assert e.publisher_hint == "bbc.com"                    # <source url=> host names the REAL outlet
    assert e.title == "Ceasefire talks resume"              # " - BBC News" suffix stripped
    assert e.published_at is not None and e.published_at.startswith("2026-07-26T05:00")
    assert e.category == "World" and e.language == "en" and e.country == "US"
    assert batch.entries[1].publisher_hint is None          # no source tag -> honestly unknown
    assert batch.entries[1].title == "Untitled wire item"   # no matching suffix -> untouched


def test_googlenews_feed_rotation_and_fallback(monkeypatch):
    """TOPICS + QUERIES build the feed list (invalid topics dropped), rotated one per cycle;
    with neither configured the single front-page feed is used."""
    urls = []
    a = sources.GoogleNewsAdapter(fetch_bytes=lambda url: (urls.append(url) or GOOGLENEWS_XML))
    monkeypatch.setenv("RWE_GOOGLENEWS_TOPICS", "WORLD,BUSINESS,BOGUS")
    monkeypatch.setenv("RWE_GOOGLENEWS_QUERIES", "climate change")
    for _ in range(4):                                      # 3 feeds -> the 4th wraps
        a.fetch()
    assert "/headlines/section/topic/WORLD?" in urls[0]
    assert "/headlines/section/topic/BUSINESS?" in urls[1]
    assert "/rss/search?" in urls[2] and "q=climate+change" in urls[2]
    assert urls[3] == urls[0] and not any("BOGUS" in u for u in urls)
    assert all("hl=en-US" in u and "gl=US" in u and "ceid=US%3Aen" in u for u in urls)
    monkeypatch.delenv("RWE_GOOGLENEWS_TOPICS")
    monkeypatch.delenv("RWE_GOOGLENEWS_QUERIES")
    feeds = a._feeds()
    assert len(feeds) == 1 and feeds[0][2].startswith("https://news.google.com/rss?")


@pytest.mark.parametrize("cls,prefix", [
    (sources.GuardianAdapter, "GUARDIAN"), (sources.NewsDataAdapter, "NEWSDATA"),
    (sources.GNewsAdapter, "GNEWS"), (sources.MediaStackAdapter, "MEDIASTACK"),
    (sources.CurrentsAdapter, "CURRENTS"),
])
def test_keyed_adapter_enable_gating_and_config_warning(monkeypatch, cls, prefix):
    """Every keyed adapter shares the chassis contract: flag alone -> disabled + a startup config
    warning naming the missing key; flag+key -> enabled; neither -> silently disabled."""
    for suffix in ("ENABLED", "API_KEY"):
        monkeypatch.delenv(f"RWE_{prefix}_{suffix}", raising=False)
    a = cls(fetch=lambda url: {})
    assert not a.enabled() and a.config_warning() is None
    monkeypatch.setenv(f"RWE_{prefix}_ENABLED", "1")
    assert not a.enabled()
    warning = a.config_warning()
    assert warning and f"RWE_{prefix}_API_KEY" in warning
    monkeypatch.setenv(f"RWE_{prefix}_API_KEY", "k")
    assert a.enabled() and a.config_warning() is None


@pytest.mark.parametrize("cls,auth_param,page_param", [
    (sources.GuardianAdapter, "api-key=k", "page-size="),
    (sources.NewsDataAdapter, "apikey=k", "size="),
    (sources.GNewsAdapter, "apikey=k", "max="),
    (sources.CurrentsAdapter, "apiKey=k", "page_size="),
])
def test_keyed_adapter_url_carries_auth_and_page_size(monkeypatch, cls, auth_param, page_param):
    a = cls(fetch=lambda url: {})
    monkeypatch.setenv(f"RWE_{a.env_prefix}_API_KEY", "k")
    url = a._url(a._combos()[0])
    assert auth_param in url and page_param in url


def test_default_registry_registers_all_providers_with_unique_health_keys():
    reg = sources.default_registry()
    adapters = reg.adapters()
    providers = [a.provider for a in adapters]
    assert providers == ["RSS", "NewsAPI", "Guardian", "NewsData", "GNews", "MediaStack",
                         "Currents", "GoogleNews", "GDELT", "GDELT-GKG"]
    keys = [a.health_key for a in adapters if a.health_key]
    assert len(keys) == len(set(keys))                      # health rows never collide across sources
