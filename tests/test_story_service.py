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
import location                  # noqa: E402

NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)


def _add(st, cu, pub, lean, title, *, category="Politics", days=0, url=None, desc="context",
         country=None):
    st.upsert_feed_article(
        canonical_url=cu, url=url if url is not None else cu, publisher=pub, source_publisher=pub,
        title=title, description=desc, body=None, published_at=(NOW - timedelta(days=days)).isoformat(),
        source_feed="feed://x", country=country,
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


def test_distribution_excludes_unrated_publishers():
    """L2.2: an unrated outlet is real coverage but casts no distribution vote — never counted as
    centre. Shares are over RATED publishers; the unrated coverage row carries null lean/bucket."""
    st = store_mod.Store("sqlite://")
    _add(st, "https://npr.org/a1", "NPR", -1.0, "Senate passes the funding bill after debate", days=1)
    _add(st, "https://fox.com/a2", "Fox News", 1.5, "Senate passes funding bill averting shutdown", days=1)
    _add(st, "https://obscure.example/a3", "Obscure Tribune", None,
         "Senate passes funding bill to avert shutdown")
    story = next(s for s in ss.cluster_from_store(st) if "Senate" in s["title"])
    assert story["totalCoverage"] == 3 and story["publisherCount"] == 3   # coverage counts everyone
    assert story["distribution"] == {"left": 0.5, "center": 0.0, "right": 0.5}
    assert story["blindspotSide"] == "center"                             # gap among RATED votes
    unrated = next(c for c in story["coverage"] if c["publisher"] == "Obscure Tribune")
    assert unrated["lean"] is None and unrated["leanBucket"] is None


def test_all_unrated_story_is_zero_distribution_no_blindspot():
    """A story covered only by unrated outlets shows an honestly empty distribution (all zero) and
    no blindspot — empty beats wrong, and nothing crashes on null buckets."""
    st = store_mod.Store("sqlite://")
    _add(st, "https://a.example/1", "Tribune A", None, "Dockworkers strike closes the main port", days=1)
    _add(st, "https://b.example/2", "Tribune B", None, "Dockworkers strike closes main port operations")
    story = ss.cluster_from_store(st)[0]
    assert story["distribution"] == {"left": 0.0, "center": 0.0, "right": 0.0}
    assert story["blindspotSide"] is None


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


def test_fallback_summary_handles_empty_topic():
    """A description-less representative with an EMPTY topic (uncategorized stays "") must not
    produce "N publishers covering ." — the orphaned-period card copy seen in production."""
    st = store_mod.Store("sqlite://")
    _add(st, "https://a.example/1", "TechDaily", 0.0, "Samsung introduces new foldable phones",
         category="", desc="", days=1)
    _add(st, "https://b.example/2", "GadgetWire", 0.2, "Samsung introduces new foldable phone line",
         category="", desc="", days=1)
    s = next(iter(ss.cluster_from_store(st)))
    assert s["summary"] == "2 publishers covering this story."
    # A real topic keeps the informative form.
    st2 = store_mod.Store("sqlite://")
    _add(st2, "https://a.example/1", "TechDaily", 0.0, "Samsung introduces new foldable phones",
         category="Tech", desc="", days=1)
    _add(st2, "https://b.example/2", "GadgetWire", 0.2, "Samsung introduces new foldable phone line",
         category="Tech", desc="", days=1)
    assert next(iter(ss.cluster_from_store(st2)))["summary"] == "2 publishers covering tech."


def test_filters_topic_publisher_lean():
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    assert ss.list_stories(st, topic="Climate")["total"] == 1        # only the wildfire event
    assert ss.list_stories(st, publisher="NPR")["total"] == 1        # only stories including NPR
    assert ss.list_stories(st, lean="right")["total"] == 1           # only the Senate event has right coverage


def test_filter_country_is_event_dimension_only():
    """?country= is EVENT location only (intended contract change): publisher homes are a
    separate preserved fact (``publisherCountries``), never a filter substitute. A story with no
    event-located members matches no country but stays in the unfiltered feed ("All")."""
    st = store_mod.Store("sqlite://")
    # Senate event: publisher homes US/GB/GB-unlocated, EVENT located US (two member votes).
    _add(st, "https://npr.org/a1", "NPR", -1.0, "Senate passes the funding bill after debate",
         days=2, country="US")
    _add(st, "https://fox.com/a2", "Fox News", 1.5, "Senate passes funding bill averting shutdown",
         days=1, country="gb")
    _add(st, "https://bbc.com/a3", "BBC News", 0.0, "US Senate passes funding bill to avert shutdown", days=0)
    for url in ("https://npr.org/a1", "https://fox.com/a2"):
        st.replace_article_event_locations(
            url, location.resolve_event_locations([{"country": "United States", "source": "gdelt-gkg"}]))
    # Wildfire event: publisher-located only — no event geography anywhere.
    _add(st, "https://cnn.com/b1", "CNN", -1.2, "Wildfires spread across the western coast",
         category="Climate", days=3, country="US")
    _add(st, "https://guardian.com/b2", "The Guardian", -1.5, "Wildfires spread rapidly along western coast",
         category="Climate", days=3, country="GB")

    senate = next(s for s in ss.cluster_from_store(st) if "Senate" in s["title"])
    assert senate["countries"] == ["US"] and senate["primaryCountry"] == "US"
    assert senate["publisherCountries"] == ["GB", "US"]               # provenance preserved, upper
    assert ss.list_stories(st, country="US")["total"] == 1            # Senate only
    assert ss.list_stories(st, country="us")["total"] == 1            # case-insensitive query
    assert ss.list_stories(st, country="GB")["total"] == 0            # publisher homes never match
    assert ss.list_stories(st, country=None)["total"] == 2            # "All" keeps the whole feed
    wildfire = next(s for s in ss.cluster_from_store(st) if "Wildfires" in s["title"])
    assert wildfire["countries"] == [] and wildfire["primaryCountry"] is None
    assert wildfire["publisherCountries"] == ["GB", "US"]


def test_filter_country_prefers_event_location_over_publisher():
    """The consensus fact derives from EVENT geography alone: one event-located member (BBC,
    event US) is all the evidence, so the story locates US; the other members' publisher homes
    contribute nothing to the filter."""
    st = store_mod.Store("sqlite://")
    _add(st, "https://npr.org/a1", "NPR", -1.0, "Senate passes the funding bill after debate",
         days=2, country="US")
    _add(st, "https://fox.com/a2", "Fox News", 1.5, "Senate passes funding bill averting shutdown",
         days=1, country="US")
    _add(st, "https://bbc.com/a3", "BBC News", 0.0, "US Senate passes funding bill to avert shutdown",
         days=0, country="GB")
    # The BBC member's EVENT is located in the US: the story stays a US story through the event
    # dimension; BBC's GB home no longer locates it (best-known wins per member).
    st.replace_article_event_locations(
        "https://bbc.com/a3", location.resolve_event_locations(
            [{"country": "United States", "source": "gdelt-gkg"}]))
    senate = next(s for s in ss.cluster_from_store(st) if "Senate" in s["title"])
    assert senate["eventCountries"] == ["US"]
    assert senate["countries"] == ["US"]                              # GB gone: event beat publisher
    assert ss.list_stories(st, country="US")["total"] == 1
    assert ss.list_stories(st, country="GB")["total"] == 0


def test_country_facets_are_story_level_and_filter_independent():
    """The picker's source of truth (the production lesson): a located article that never
    clustered into a story must NOT put its country in ``countryFacets`` — article-level facets
    said "US has located articles" while the story filter honestly returned zero stories, a
    dead dropdown option. Story-level facets make that impossible: offered ⇔ ≥1 story matches.
    Facets respect the OTHER filters but never the country filter itself."""
    st = store_mod.Store("sqlite://")
    _add(st, "https://npr.org/a1", "NPR", -1.0, "Senate passes the funding bill after debate",
         days=2, country="US")
    _add(st, "https://fox.com/a2", "Fox News", 1.5, "Senate passes funding bill averting shutdown",
         days=1, country="US")
    for url in ("https://npr.org/a1", "https://fox.com/a2"):
        st.replace_article_event_locations(
            url, location.resolve_event_locations([{"country": "US", "source": "gdelt-gkg"}]))
    _add(st, "https://cnn.com/b1", "CNN", -1.2, "Wildfires spread across the western coast",
         category="Climate", days=3)
    _add(st, "https://guardian.com/b2", "The Guardian", -1.5, "Wildfires spread rapidly along western coast",
         category="Climate", days=3)
    # The production case: an event-located SINGLETON (one publisher → never becomes a story).
    _add(st, "https://lemonde.fr/c1", "Le Monde", -0.5, "Paris hosts the summer athletics final",
         category="Sports", days=1)
    st.replace_article_event_locations(
        "https://lemonde.fr/c1", location.resolve_event_locations([{"country": "FR", "source": "gdelt-gkg"}]))

    env = ss.list_stories(st)
    assert env["countryFacets"] == {"US": 1}                # FR absent: located but storyless
    assert ss.list_stories(st, country="US")["countryFacets"] == {"US": 1}   # own filter excluded
    assert ss.list_stories(st, country="FR")["total"] == 0  # and FR honestly yields no stories
    assert ss.list_stories(st, topic="Climate")["countryFacets"] == {}       # other filters apply


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
