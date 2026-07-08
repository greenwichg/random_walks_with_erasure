"""Tests for examples/story_service.py — the single owner of Story construction (Commit 7).

Proves clustering into Stories, full Story construction (incl. the nullable image contract), timeline
ordering, coverage calculation, stable IDs that survive new coverage, pagination, sorting, filters,
diagnostics, that Discover + Stories reuse this one service, and that it never touches the recommender.
"""

import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import store as store_mod        # noqa: E402
import story_service as ss       # noqa: E402
import discover                  # noqa: E402

NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)


def _add(st, cu, pub, lean, title, *, category="Politics", days=0, url=None, desc="context"):
    st.upsert_feed_article(
        canonical_url=cu, url=url if url is not None else cu, publisher=pub, source_publisher=pub,
        title=title, description=desc, body=None, published_at=(NOW - timedelta(days=days)).isoformat(),
        source_feed="feed://x",
        scored={"article_id": cu, "outlet": pub, "category": category, "lean": lean, "title": title})


def _senate_and_wildfire(st):
    # Senate event — 3 publishers, L/C/R
    _add(st, "https://npr.org/a1", "NPR", -1.0, "Senate passes the funding bill after debate", days=2)
    _add(st, "https://fox.com/a2", "Fox News", 1.5, "Senate passes funding bill averting shutdown", days=1)
    _add(st, "https://bbc.com/a3", "BBC News", 0.0, "US Senate passes funding bill to avert shutdown", days=0)
    # Wildfire event — 2 publishers, both left (a real blind spot)
    _add(st, "https://cnn.com/b1", "CNN", -1.2, "Wildfires spread across the western coast", category="Climate", days=3)
    _add(st, "https://guardian.com/b2", "The Guardian", -1.5, "Wildfires spread rapidly along western coast",
         category="Climate", days=3)
    # noise — unrelated singletons (won't form a story: < 2 publishers)
    _add(st, "https://wsj.com/c1", "WSJ", 0.8, "Markets rally on tech earnings", category="Business", days=4)


# --------------------------------------------------------------------------- #
# Clustering + construction
# --------------------------------------------------------------------------- #
def test_related_cluster_unrelated_separate():
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    stories = ss.cluster_from_store(st)
    assert len(stories) == 2                                  # Senate + Wildfire; the singleton is dropped
    senate = next(s for s in stories if "Senate" in s["title"])
    assert senate["totalCoverage"] == 3 and senate["publisherCount"] == 3
    assert set(senate["publishers"]) == {"NPR", "Fox News", "BBC News"}


def test_story_construction_fields_and_image_contract():
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    s = ss.cluster_from_store(st)[0]
    for k in ("id", "title", "summary", "topic", "updatedAt", "totalCoverage", "publisherCount",
              "publishers", "publisherDiversity", "earliest", "latest", "firstPublished", "latestUpdate",
              "newest", "oldest", "timeSpanHours", "distribution", "coverage", "timeline"):
        assert k in s
    # nullable image contract — present, null now, ready for Commit 8
    assert s["image"] is None and s["imageSource"] is None and s["imageAttribution"] is None
    assert s["id"].startswith("st_")


def test_timeline_and_span_ordering():
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    senate = next(s for s in ss.cluster_from_store(st) if "Senate" in s["title"])
    # coverage newest-first
    times = [c["publishedAt"] for c in senate["coverage"]]
    assert times == sorted(times, reverse=True)
    assert senate["oldest"] == senate["earliest"] == senate["firstPublished"]
    assert senate["newest"] == senate["latest"] == senate["latestUpdate"]
    assert senate["timeSpanHours"] == 48.0                   # days 2..0 -> 48h span
    assert senate["timeline"][0]["label"] == "First report"


def test_coverage_calculation_and_blindspot():
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    stories = ss.cluster_from_store(st)
    senate = next(s for s in stories if "Senate" in s["title"])
    wild = next(s for s in stories if "Wildfires" in s["title"])
    assert abs(sum(senate["distribution"].values()) - 1.0) < 1e-9
    assert senate["blindspotSide"] is None                   # L+C+R all covered
    assert wild["distribution"]["left"] == 1.0 and wild["blindspotSide"] in {"center", "right"}
    assert wild["publisherDiversity"] == 1.0                 # 2 publishers / 2 articles


# --------------------------------------------------------------------------- #
# Stable IDs survive new coverage of the same event
# --------------------------------------------------------------------------- #
def test_stable_id_survives_new_coverage():
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    sid = next(s for s in ss.cluster_from_store(st) if "Senate" in s["title"])["id"]
    assert ss.get_story(st, sid) is not None
    # a new outlet covers the SAME event -> the cluster grows but the anchored id is unchanged
    _add(st, "https://ap.org/a4", "AP", 0.1, "Senate passes funding bill in late vote", days=1)
    grown = ss.get_story(st, sid)
    assert grown is not None and grown["totalCoverage"] == 4 and grown["id"] == sid


# --------------------------------------------------------------------------- #
# Pagination + sorting + filters (the list envelope)
# --------------------------------------------------------------------------- #
def _many(st, n):
    for e in range(n):                                        # n independent 3-publisher events
        title = f"topic{e}alpha topic{e}beta topic{e}gamma"  # disjoint tokens per event -> no cross-cluster
        for pub, lean in (("NPR", -1.0), ("BBC News", 0.0), ("Fox News", 1.4)):
            _add(st, f"https://{pub}-{e}.x/a", pub, lean, title, days=e % 20)


def test_pagination_envelope():
    st = store_mod.Store("sqlite://"); _many(st, 12)
    p1 = ss.list_stories(st, limit=5, offset=0)
    p2 = ss.list_stories(st, limit=5, offset=5)
    assert p1["total"] == 12 and p1["page"] == 1 and p1["hasMore"] is True and p1["remainingPages"] == 2
    assert p2["page"] == 2 and len(p2["stories"]) == 5
    assert {s["id"] for s in p1["stories"]}.isdisjoint({s["id"] for s in p2["stories"]})


def test_sorting():
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    latest = ss.list_stories(st, sort="latest")["stories"]
    assert latest[0]["latest"] >= latest[-1]["latest"]
    oldest = ss.list_stories(st, sort="oldest")["stories"]
    assert oldest[0]["earliest"] <= oldest[-1]["earliest"]


def test_filters_topic_publisher_lean():
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    assert ss.list_stories(st, topic="Climate")["total"] == 1        # only the wildfire event
    assert ss.list_stories(st, publisher="NPR")["total"] == 1        # only stories including NPR
    assert ss.list_stories(st, lean="right")["total"] == 1           # only the Senate event has right coverage


def test_diagnostics():
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    d = ss.list_stories(st, debug=True)
    assert "clusterMs" in d and d["diagnostics"]["storyCount"] == 2
    assert d["diagnostics"]["largestStory"] == 3 and d["diagnostics"]["avgArticlesPerStory"] == 2.5
    assert d["diagnostics"]["sizeDistribution"] == {"2": 1, "3": 1}


# --------------------------------------------------------------------------- #
# One owner: Discover + Stories reuse the service; no recommender coupling
# --------------------------------------------------------------------------- #
def test_discover_delegates_to_story_service():
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    # discover.cluster_stories / story_detail are thin wrappers over the same service
    assert [s["id"] for s in discover.cluster_stories(st)] == [s["id"] for s in ss.cluster_from_store(st)]
    sid = ss.cluster_from_store(st)[0]["id"]
    assert discover.story_detail(st, sid)["id"] == sid


def test_story_service_imports_no_recommendation_algorithm():
    for banned in ("health_report", "rwe", "simulate_users", "personalize", "narrate_report",
                   "corpus_refresh", "api_server"):
        assert not hasattr(ss, banned), f"story_service must not import {banned}"
