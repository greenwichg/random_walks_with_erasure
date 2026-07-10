"""Commit 18 — content lifecycle for extension-produced FeedArticles (store layer).

An extension read creates a *provisional* catalog article through the same ``upsert_feed_article``
every feed source uses; promotion happens when an independent feed re-discovers it (merge) or when
enough distinct readers corroborate it. Discover hides provisional articles (via the shared search
path's ``include_provisional=False``); Stories/Search/export see everything, unchanged.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import store as store_mod   # noqa: E402


def _st():
    return store_mod.Store("sqlite://")


def _upsert(st, canonical, source_type, **over):
    kw = dict(canonical_url=canonical, url=canonical, publisher=over.pop("publisher", "The Example"),
              source_publisher=None, title=over.pop("title", "T"), description="", body=None,
              published_at=None, source_feed=over.pop("source_feed", "feed://x"),
              scored={"article_id": canonical, "outlet": "The Example", "category": "Politics",
                      "lean": -1.0, "political": True, "title": "T"},
              source_type=source_type)
    kw.update(over)
    return st.upsert_feed_article(**kw)


def test_extension_creates_provisional_feed_sources_create_active():
    st = _st()
    assert _upsert(st, "https://a.example/x", "extension") is True
    assert st.get_feed_article("https://a.example/x")["articleState"] == "provisional"
    assert _upsert(st, "https://b.example/y", "rss") is True
    assert st.get_feed_article("https://b.example/y")["articleState"] is None
    assert _upsert(st, "https://c.example/z", None) is True            # legacy callers unchanged
    assert st.get_feed_article("https://c.example/z")["articleState"] is None


def test_feed_rediscovery_merges_and_promotes():
    """Cases 7/8/9: the same canonical URL from RSS/NewsAPI/GDELT merges (no duplicate) and the
    merge itself promotes the provisional article."""
    for feed_source_type in ("rss", "newsapi", "gdelt"):
        st = _st()
        url = "https://a.example/story"
        assert _upsert(st, url, "extension") is True
        assert _upsert(st, url, feed_source_type, title="Fuller title") is False   # merged, no dup
        row = st.get_feed_article(url)
        assert row["articleState"] == "verified", feed_source_type                        # promoted
        assert row["sourceType"] == "extension"                                     # first-seen kept
        assert st.count_feed_articles() == 1


def test_extension_re_read_does_not_promote():
    st = _st()
    url = "https://a.example/story"
    _upsert(st, url, "extension")
    assert _upsert(st, url, "extension") is False
    assert st.get_feed_article(url)["articleState"] == "provisional"        # extension can't self-promote


def test_multiple_distinct_readers_promote():
    """Case 3 + Case 11: one FeedArticle, N Read rows; the second distinct reader promotes."""
    st = _st()
    url = "https://a.example/story"
    _upsert(st, url, "extension")
    u1 = st.upsert_user_by_identity("google", "r1").id
    u2 = st.upsert_user_by_identity("google", "r2").id
    st.add_read(u1, url, {"article_id": url}, None)
    assert st.maybe_promote_feed_article(url, 2) is False             # one reader: not yet
    st.add_read(u1, url, {"article_id": url}, None)                   # same reader again: still one
    assert st.maybe_promote_feed_article(url, 2) is False
    st.add_read(u2, url, {"article_id": url}, None)
    assert st.maybe_promote_feed_article(url, 2) is True              # second distinct reader
    assert st.get_feed_article(url)["articleState"] == "verified"
    assert st.maybe_promote_feed_article(url, 2) is False             # idempotent (already verified)
    assert st.count_feed_articles() == 1


def test_discover_filter_hides_provisional_everything_else_sees_it():
    st = _st()
    _upsert(st, "https://p.example/1", "extension", publisher="ProvPub")
    _upsert(st, "https://a.example/2", "rss", publisher="ActivePub")

    all_rows, all_total = st.search_feed_articles()                    # Search/Stories default
    assert all_total == 2
    vis_rows, vis_total = st.search_feed_articles(include_provisional=False)   # Discover
    assert vis_total == 1 and vis_rows[0]["canonicalUrl"] == "https://a.example/2"

    assert "ProvPub" in st.feed_article_facets()["publishers"]
    assert "ProvPub" not in st.feed_article_facets(include_provisional=False)["publishers"]


def test_read_demand_helpers():
    st = _st()
    _upsert(st, "https://a.example/1", "rss")
    _upsert(st, "https://a.example/2", "rss")
    u = st.upsert_user_by_identity("google", "r").id
    st.add_read(u, "https://a.example/2", {"article_id": "https://a.example/2"}, None)
    st.add_read(u, "https://elsewhere.example/x", {"article_id": "https://elsewhere.example/x"}, None)

    urls = st.distinct_read_urls()
    assert urls == {"https://a.example/2", "https://elsewhere.example/x"}
    rows = st.feed_articles_by_urls(urls)                              # only catalog rows come back
    assert [r["canonicalUrl"] for r in rows] == ["https://a.example/2"]
