"""Tests for examples/search.py + store.search_feed_articles + pagination.py (Commit 6).

Proves the live catalog search: text + facet + date filtering, sorting, offset pagination (total /
hasMore / remaining pages), canonical-URL preservation (the Read flow), that Discover reuses the same
path, and that search never touches the recommendation engine. Includes a perf sanity check on a
larger catalog.
"""

import pathlib
import sys
import time
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import store as store_mod          # noqa: E402
import search                      # noqa: E402
import discover                    # noqa: E402
from pagination import OffsetPagination   # noqa: E402

NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)


def _add(st, cu, publisher, lean, title, *, category="Politics", desc="context", days=0,
         url=None, source="feed://x"):
    st.upsert_feed_article(
        canonical_url=cu, url=url if url is not None else cu, publisher=publisher,
        source_publisher=publisher, title=title, description=desc, body=None,
        published_at=(NOW - timedelta(days=days)).isoformat(), source_feed=source,
        scored={"article_id": cu, "outlet": publisher, "category": category, "lean": lean, "title": title})


def _corpus(st):
    _add(st, "https://npr.org/1", "NPR", -1.0, "Senate passes funding bill", category="Politics", days=1)
    _add(st, "https://fox.com/2", "Fox News", 1.5, "Border policy fight escalates", category="Politics", days=0)
    _add(st, "https://guardian.com/3", "The Guardian", -1.5, "Wildfires spread west", category="Climate", days=2)
    _add(st, "https://wsj.com/4", "Wall Street Journal", 0.8, "Markets rally on earnings",
         category="Business", days=3, source="feed://markets")
    _add(st, "https://bbc.com/5", "BBC News", 0.0, "Global summit on funding", category="Politics", days=4)


# --------------------------------------------------------------------------- #
# Text search
# --------------------------------------------------------------------------- #
def test_text_search_matches_title():
    st = store_mod.Store("sqlite://"); _corpus(st)
    r = search.search(st, query="funding")
    assert {a["headline"] for a in r["results"]} == {"Senate passes funding bill", "Global summit on funding"}
    assert r["total"] == 2


def test_text_search_matches_publisher_and_is_case_insensitive():
    st = store_mod.Store("sqlite://"); _corpus(st)
    r = search.search(st, query="guardian")
    assert [a["publisher"] for a in r["results"]] == ["The Guardian"]


def test_text_search_matches_description_and_topic():
    st = store_mod.Store("sqlite://"); _corpus(st)
    _add(st, "https://x.com/z", "X", 0.0, "Nothing here", category="Weather", desc="a hurricane approaches")
    assert search.search(st, query="hurricane")["total"] == 1        # description
    assert search.search(st, query="Climate")["total"] == 1          # topic/category


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #
def test_publisher_filter():
    st = store_mod.Store("sqlite://"); _corpus(st)
    r = search.search(st, publisher="Fox News")
    assert [a["publisher"] for a in r["results"]] == ["Fox News"] and r["total"] == 1


def test_lean_filter_buckets():
    st = store_mod.Store("sqlite://"); _corpus(st)
    assert sorted(a["publisher"] for a in search.search(st, lean="left")["results"]) == ["NPR", "The Guardian"]
    assert sorted(a["publisher"] for a in search.search(st, lean="right")["results"]) == ["Fox News", "Wall Street Journal"]
    assert [a["publisher"] for a in search.search(st, lean="center")["results"]] == ["BBC News"]


def test_topic_filter():
    st = store_mod.Store("sqlite://"); _corpus(st)
    r = search.search(st, topic="Climate")
    assert [a["headline"] for a in r["results"]] == ["Wildfires spread west"]


def test_date_range_filter():
    st = store_mod.Store("sqlite://"); _corpus(st)
    frm = (NOW - timedelta(days=1, hours=1)).isoformat()             # only days 0 and 1
    r = search.search(st, date_from=frm)
    assert sorted(a["publisher"] for a in r["results"]) == ["Fox News", "NPR"]


def test_source_filter():
    st = store_mod.Store("sqlite://"); _corpus(st)
    r = search.search(st, source="feed://markets")
    assert [a["publisher"] for a in r["results"]] == ["Wall Street Journal"]


# --------------------------------------------------------------------------- #
# Sorting
# --------------------------------------------------------------------------- #
def test_sort_newest_and_oldest():
    st = store_mod.Store("sqlite://"); _corpus(st)
    newest = [a["headline"] for a in search.search(st, sort="newest")["results"]]
    oldest = [a["headline"] for a in search.search(st, sort="oldest")["results"]]
    assert newest[0] == "Border policy fight escalates"     # days=0
    assert oldest[0] == "Global summit on funding"          # days=4
    assert newest == list(reversed(oldest))


def test_sort_publisher():
    st = store_mod.Store("sqlite://"); _corpus(st)
    pubs = [a["publisher"] for a in search.search(st, sort="publisher")["results"]]
    assert pubs == sorted(pubs, key=str.lower)


# --------------------------------------------------------------------------- #
# Pagination
# --------------------------------------------------------------------------- #
def test_pagination_offset_total_hasmore_remaining():
    st = store_mod.Store("sqlite://"); _corpus(st)                    # 5 articles
    p1 = search.search(st, limit=2, offset=0)
    p2 = search.search(st, limit=2, offset=2)
    p3 = search.search(st, limit=2, offset=4)
    assert p1["total"] == 5 and p1["page"] == 1 and p1["hasMore"] is True and p1["remainingPages"] == 2
    assert p2["page"] == 2 and p2["hasMore"] is True and p2["remainingPages"] == 1
    assert p3["page"] == 3 and p3["hasMore"] is False and p3["remainingPages"] == 0 and len(p3["results"]) == 1
    # pages are disjoint and cover the whole set
    ids = [a["id"] for a in p1["results"] + p2["results"] + p3["results"]]
    assert len(ids) == len(set(ids)) == 5


def test_offset_pagination_meta_unit():
    assert OffsetPagination.from_params(20, 40).meta(100) == {
        "page": 3, "pageSize": 20, "hasMore": True, "remainingPages": 2}
    assert OffsetPagination.from_params(0, 0).meta(7) == {
        "page": 1, "pageSize": 7, "hasMore": False, "remainingPages": 0}
    assert OffsetPagination.from_params(9999, 0, max_limit=200).limit == 200   # clamped


# --------------------------------------------------------------------------- #
# Result shape + Read flow (URL preservation)
# --------------------------------------------------------------------------- #
def test_results_preserve_canonical_url_and_article_shape():
    st = store_mod.Store("sqlite://")
    _add(st, "https://foxnews.com/x", "Fox News", 1.6, "Border plan",
         url="https://www.foxnews.com/politics/border-plan")
    a = search.search(st, query="border")["results"][0]
    assert a["id"] == "https://foxnews.com/x"                          # canonical url == id (Read flow)
    assert a["url"] == "https://www.foxnews.com/politics/border-plan"  # the REAL publisher URL
    for k in ("headline", "publisher", "description", "publishedAt", "lean", "leanBucket", "topic"):
        assert k in a


# --------------------------------------------------------------------------- #
# Debug diagnostics + FTS detection
# --------------------------------------------------------------------------- #
def test_debug_surfaces_timing_and_fts():
    st = store_mod.Store("sqlite://"); _corpus(st)
    r = search.search(st, query="funding", debug=True)
    assert isinstance(r["queryMs"], float) and isinstance(r["ftsAvailable"], bool)
    assert "queryMs" not in search.search(st, query="funding")       # only in debug


# --------------------------------------------------------------------------- #
# Facets + Discover reuse (backward compatibility)
# --------------------------------------------------------------------------- #
def test_facets():
    st = store_mod.Store("sqlite://"); _corpus(st)
    f = st.feed_article_facets()
    assert f["topics"] == ["Business", "Climate", "Politics"]
    assert "Fox News" in f["publishers"] and "NPR" in f["publishers"]


def test_discover_uses_search_path_and_stays_consistent():
    st = store_mod.Store("sqlite://"); _corpus(st)
    disc = discover.list_discover(st, lean="left")
    srch = search.search(st, lean="left", limit=200)
    assert {a["id"] for a in disc["articles"]} == {a["id"] for a in srch["results"]}
    assert disc["topics"] == ["Business", "Climate", "Politics"]      # facets stay over the full catalog


# --------------------------------------------------------------------------- #
# Decoupling: search never touches the recommendation engine
# --------------------------------------------------------------------------- #
def test_search_imports_no_recommendation_algorithm():
    for banned in ("health_report", "rwe", "simulate_users", "personalize", "narrate_report",
                   "corpus_refresh", "api_server"):
        assert not hasattr(search, banned), f"search must not import {banned}"


# --------------------------------------------------------------------------- #
# Performance sanity on a larger catalog
# --------------------------------------------------------------------------- #
def test_performance_on_larger_catalog():
    st = store_mod.Store("sqlite://")
    for i in range(3000):
        pub, lean = [("NPR", -1.2), ("AP", 0.0), ("Fox News", 1.3)][i % 3]
        _add(st, f"https://x.example/{i}", pub, lean, f"headline number {i}",
             category=["Politics", "Climate", "Business"][i % 3], days=i % 30)
    t0 = time.perf_counter()
    r = search.search(st, query="number", publisher="NPR", lean="left", sort="newest", limit=30)
    dt = (time.perf_counter() - t0) * 1000
    assert r["total"] == 1000 and len(r["results"]) == 30              # 3000/3 publishers
    assert dt < 500, f"search too slow: {dt:.0f}ms"                    # index-backed, well under this
