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

import pytest                     # noqa: E402

# Fixtures are anchored to the REAL current day, not a frozen date. Stories are built from a rolling
# scan window (story_service.scan_days), so a corpus pinned to a calendar date silently ages out of
# the product's window and the suite starts asserting against an empty catalog.
NOW = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)


@pytest.fixture(autouse=True)
def _isolate_story_cache(monkeypatch):
    """Two isolations this module needs:

    * The clustered-build cache is module-level; without clearing it, one test's clusters leak into
      the next (same parameters, different in-memory store).
    * conftest opens the scan window wide so date-pinned fixtures elsewhere stay in scope. THIS
      module owns the window's behaviour, so it drops that override and runs against the real
      default — fixtures here are anchored to the real current day for exactly that reason.
    """
    monkeypatch.delenv("RWE_STORIES_SCAN_DAYS", raising=False)
    ss.clear_cache()
    yield
    ss.clear_cache()


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
    # Wildfire event — 3 LEFT-rated publishers (a real blind spot). Three is the floor: with fewer
    # rated outlets than lean buckets an empty bucket is forced, so the claim would be arithmetic
    # rather than a finding (MIN_RATED_FOR_BLINDSPOT).
    _add(st, "https://cnn.com/b1", "CNN", -1.2, "Wildfires spread across the western coast", category="Climate", days=3)
    _add(st, "https://guardian.com/b2", "The Guardian", -1.5, "Wildfires spread rapidly along western coast",
         category="Climate", days=3)
    _add(st, "https://msnbc.com/b3", "MSNBC", -1.4, "Wildfires spread fast along the western coast",
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
    # No claim: an unrated outlet is not part of the sample a gap rests on either, so three
    # publishers here are only TWO rated ones — below MIN_RATED_FOR_BLINDSPOT, where an empty
    # bucket is forced by arithmetic rather than observed.
    assert story["blindspotSide"] is None
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
    assert wild["publisherDiversity"] == 1.0                 # 3 publishers / 3 articles


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
        # Ages stay inside the scan window — this fixture exercises paging, not retention.
        for pub, lean in (("NPR", -1.0), ("BBC News", 0.0), ("Fox News", 1.4)):
            _add(st, f"https://{pub}-{e}.x/a", pub, lean, title, days=e % 5)


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
    assert d["diagnostics"]["largestStory"] == 3 and d["diagnostics"]["avgArticlesPerStory"] == 3.0
    assert d["diagnostics"]["sizeDistribution"] == {"3": 2}


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


# --------------------------------------------------------------------------- #
# M3 — the coverage-gap lens: ?blindspot= filter + counted blindspotFacets.
# --------------------------------------------------------------------------- #
def _gap_catalog(st):
    """Three stories: Senate (L/C/R — balanced, no gap), Wildfire (3 left-rated -> gap on
    centre, deterministic first-empty), Port strike (all-unrated -> UNKNOWN, no gap ever)."""
    _senate_and_wildfire(st)
    _add(st, "https://a.example/p1", "Tribune A", None, "Dockworkers strike closes the main port", days=1)
    _add(st, "https://b.example/p2", "Tribune B", None, "Dockworkers strike closes main port operations")


def test_blindspot_filter_matches_only_detected_gaps():
    st = store_mod.Store("sqlite://"); _gap_catalog(st)
    unfiltered = ss.list_stories(st)
    assert unfiltered["total"] == 3                       # incl. the unknown-distribution story

    any_gap = ss.list_stories(st, blindspot="any")
    assert [s["title"] for s in any_gap["stories"]] and any_gap["total"] == 1
    assert "Wildfires" in any_gap["stories"][0]["title"]
    assert any_gap["stories"][0]["blindspotSide"] == "center"

    assert ss.list_stories(st, blindspot="center")["total"] == 1
    assert ss.list_stories(st, blindspot="left")["total"] == 0
    # balanced-or-unknown never matches: the all-unrated story is not a "gap", it is unknown
    titles_any = {s["title"] for s in any_gap["stories"]}
    assert not any("Dockworkers" in t for t in titles_any)


def test_blindspot_facets_counted_before_own_filter():
    """The picker's source of truth: side counts under the OTHER filters, computed before the
    blindspot filter itself — selecting a side must not collapse the facet dict."""
    st = store_mod.Store("sqlite://"); _gap_catalog(st)
    body = ss.list_stories(st, blindspot="left")          # zero results…
    assert body["total"] == 0
    assert body["blindspotFacets"] == {"center": 1}       # …but the facets still offer centre
    # and an unrelated filter narrows the facet counts (standard faceting)
    assert ss.list_stories(st, topic="Politics")["blindspotFacets"] == {}


# --------------------------------------------------------------------------- #
# The clustering candidate set is a TIME window, not a row count.
#
# Regression guard for the story collapse: max_scan=2000 rows newest-first made story yield a
# function of ingestion RATE, so every provider added shrank the hours those rows covered and
# produced FEWER stories (measured in production: a 12.5-hour effective window against a 6-day
# clustering threshold, 89 stories from a 12,790-article catalog).
# --------------------------------------------------------------------------- #
class _RecordingStore:
    """A store stand-in that records how _fetch bounded the candidate set."""
    url = "sqlite://recording"

    def __init__(self):
        self.calls = []

    def search_feed_articles(self, **kw):
        self.calls.append(kw)
        return [], 0

    def event_countries_for_urls(self, urls):
        return {}

    def count_feed_articles(self):
        return 0


def test_fetch_bounds_by_time_with_only_a_size_backstop():
    """The precise regression: the bound handed to SQL must be a DATE, and the row cap must be a
    far-off backstop rather than the 2000-row rule that made story yield track ingestion rate."""
    import clustering
    st = _RecordingStore()
    ss._fetch(st)
    kw = st.calls[0]
    assert kw["date_from"] is not None, "no time bound -> yield depends on ingestion rate again"
    start = datetime.fromisoformat(kw["date_from"])
    expected = datetime.now(timezone.utc) - timedelta(days=clustering.DEFAULT_WINDOW_DAYS)
    assert abs((start - expected).total_seconds()) < 120
    assert kw["pagination"].limit >= 60000, "row cap must be a backstop, not the relevance rule"
    assert kw["sort"] == "newest"


def test_articles_outside_the_window_are_excluded():
    """The window is a real bound, not decoration."""
    st = store_mod.Store("sqlite://")
    _add(st, "https://npr.org/anc", "NPR", -1.0, "Ancient event nobody recalls", days=400)
    _add(st, "https://bbc.com/anc", "BBC News", 0.0, "Ancient event nobody recalls now", days=400)
    assert ss.list_stories(st)["total"] == 0


def test_explicit_date_from_overrides_the_default_window():
    """A caller asking for a date range is never silently narrowed to the default window."""
    st = store_mod.Store("sqlite://")
    _add(st, "https://npr.org/anc", "NPR", -1.0, "Ancient event nobody recalls", days=400)
    _add(st, "https://bbc.com/anc", "BBC News", 0.0, "Ancient event nobody recalls now", days=400)
    wide = (NOW - timedelta(days=500)).isoformat()
    assert ss.list_stories(st, date_from=wide)["total"] == 1


def test_scan_days_is_configurable(monkeypatch):
    st = store_mod.Store("sqlite://")
    _add(st, "https://npr.org/x", "NPR", -1.0, "Council approves the transit levy", days=20)
    _add(st, "https://bbc.com/x", "BBC News", 0.0, "Council approves transit levy", days=20)
    assert ss.list_stories(st)["total"] == 0            # 20 days > the 6-day default
    monkeypatch.setenv("RWE_STORIES_SCAN_DAYS", "45")
    ss.clear_cache()
    assert ss.list_stories(st)["total"] == 1
    monkeypatch.setenv("RWE_STORIES_SCAN_DAYS", "not-a-number")
    ss.clear_cache()
    assert ss.list_stories(st)["total"] == 0            # junk falls back, never widens silently


# --------------------------------------------------------------------------- #
# Clustered-build cache
# --------------------------------------------------------------------------- #
def test_cache_serves_repeat_calls_without_reclustering(monkeypatch):
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    calls = {"n": 0}
    real = ss.build_stories

    def counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(ss, "build_stories", counting)
    ss.clear_cache()
    first = ss.list_stories(st)["total"]
    second = ss.list_stories(st, sort="latest")["total"]     # different sort, same build
    third = ss.list_stories(st, lean="left")                 # different filter, same build
    assert first == second and calls["n"] == 1, "filters/sort must not force a rebuild"
    assert third["total"] >= 0


def test_ingest_invalidates_the_cache_immediately():
    """A new article must be visible at once — a pure TTL cache would hide it, and the story detail
    page would disagree with the list that linked to it."""
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    before = next(s for s in ss.list_stories(st)["stories"] if "Senate" in s["title"])
    _add(st, "https://ap.org/a9", "AP", 0.1, "Senate passes funding bill in late vote", days=1)
    after = next(s for s in ss.list_stories(st)["stories"] if "Senate" in s["title"])
    assert after["totalCoverage"] == before["totalCoverage"] + 1


def test_cache_disabled_by_zero_ttl(monkeypatch):
    monkeypatch.setenv("RWE_STORIES_CACHE_TTL", "0")
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    ss.clear_cache()
    assert ss.list_stories(st)["total"] == ss.list_stories(st)["total"]


def test_cache_never_leaks_between_stores():
    """Two stores, identical parameters — each must see only its own catalog."""
    a = store_mod.Store("sqlite://"); _senate_and_wildfire(a)
    b = store_mod.Store("sqlite://")
    _add(b, "https://x.example/1", "NPR", -1.0, "Harbour pilots ratify their contract", days=1)
    _add(b, "https://y.example/1", "BBC News", 0.0, "Harbour pilots ratify contract", days=1)
    ta = {s["title"] for s in ss.list_stories(a, limit=50)["stories"]}
    tb = {s["title"] for s in ss.list_stories(b, limit=50)["stories"]}
    assert ta and tb and ta.isdisjoint(tb)


def test_get_story_sees_the_same_window_as_the_list():
    """Every id the list hands out must resolve — a narrower scan in get_story would 404 links the
    list had just rendered."""
    st = store_mod.Store("sqlite://"); _many(st, 12)
    listed = ss.list_stories(st, limit=100)["stories"]
    assert listed
    for s in listed:
        assert ss.get_story(st, s["id"]) is not None, f"list rendered {s['id']} but detail 404s"


def test_cache_invalidates_when_content_changes_at_constant_row_count():
    """Regression: the cache key must be a content fingerprint, not a row COUNT.

    Deleting N articles and inserting N others leaves the count identical while the catalog is
    entirely different — which is exactly what a retention prune plus an ingest in the same interval
    does. Keyed on count alone, the second read is served the first read's clusters."""
    st = store_mod.Store("sqlite://")
    first = ["https://one.example/a", "https://two.example/a"]
    for cu, pub in zip(first, ("NPR", "BBC News")):
        _add(st, cu, pub, 0.0, "Harbour pilots ratify their contract", days=1)
    assert any("Harbour" in s["title"] for s in ss.list_stories(st)["stories"])

    st.delete_feed_articles(first)
    second = ["https://three.example/a", "https://four.example/a"]
    for cu, pub in zip(second, ("CNN", "Fox News")):
        _add(st, cu, pub, 0.0, "Rail operators publish the winter timetable", days=1)

    titles = {s["title"] for s in ss.list_stories(st)["stories"]}     # same row count as before
    assert any("Rail operators" in t for t in titles), "stale build served at constant row count"
    assert not any("Harbour" in t for t in titles)


def test_warm_cache_populates_the_default_view():
    """The poller warms exactly the key /api/stories reads, so the first reader after an ingest
    does not pay the rebuild."""
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    ss.clear_cache()
    assert ss.warm_cache(st) == 2

    calls = {"n": 0}
    real = ss.build_stories
    try:
        ss.build_stories = lambda *a, **kw: (calls.__setitem__("n", calls["n"] + 1), real(*a, **kw))[1]
        body = ss.list_stories(st)
        assert body["total"] == 2
        assert calls["n"] == 0, "warm build was not reused — the reader rebuilt"
    finally:
        ss.build_stories = real


def test_warm_cache_is_invalidated_by_the_next_ingest():
    """A warm build must not outlive the catalog it was built from."""
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    ss.clear_cache()
    assert ss.warm_cache(st) == 2
    _add(st, "https://ap.org/new", "AP", 0.1, "Harbour pilots ratify their contract", days=1)
    _add(st, "https://re.example/new", "Reuters", 0.0, "Harbour pilots ratify contract", days=1)
    assert ss.list_stories(st)["total"] == 3


# --------------------------------------------------------------------------- #
# Cluster geography coherence — measured on the INCIDENT, never the publisher.
#
# geoCoherence is the share of LOCATED members whose incident countries include the story's
# consensus country. It is a member-AGREEMENT measure, which is why it separates a genuine
# multi-country story (members agree on the lead country, each adding others) from a false merge
# (members name different places entirely).
# --------------------------------------------------------------------------- #
def _locate(st, cu, *countries):
    st.replace_article_event_locations(
        cu, [location.EventLocation(country=c, source="test") for c in countries])


def test_a_us_publisher_reporting_from_india_belongs_to_the_indian_story():
    """The stated requirement: the INCIDENT's location decides, not the publisher's home."""
    st = store_mod.Store("sqlite://")
    _add(st, "https://cnn.com/g1", "CNN", -1.0, "Bridge collapse in Gujarat kills dozens",
         category="World", days=1, country="US")           # US publisher…
    _add(st, "https://ndtv.com/g1", "NDTV", None, "Bridge collapse in Gujarat kills dozens today",
         category="World", days=1, country="IN")
    _locate(st, "https://cnn.com/g1", "IN")                # …reporting an incident in India
    _locate(st, "https://ndtv.com/g1", "IN")

    story = ss.cluster_from_store(st)[0]
    assert story["countries"] == ["IN"], "the story must be located by the incident, not the byline"
    assert story["geoCoherence"] == 1.0 and story["locatedMembers"] == 2
    assert story["publisherCountries"] == ["IN", "US"]     # provenance preserved, never substituted


def test_publisher_country_alone_never_locates_a_story():
    """No incident locations at all -> no country and no coherence score. Absence of evidence is
    not a location, and it is not incoherence either."""
    st = store_mod.Store("sqlite://")
    _add(st, "https://cnn.com/x", "CNN", -1.0, "Trade talks resume in the capital",
         days=1, country="US")
    _add(st, "https://fox.com/x", "Fox News", 1.5, "Trade talks resume in capital today",
         days=1, country="US")
    story = ss.cluster_from_store(st)[0]
    assert story["countries"] == [] and story["publisherCountries"] == ["US"]
    assert story["geoCoherence"] is None, "unlocated must be None, never 0.0"
    assert story["locatedMembers"] == 0


def test_a_genuine_multi_country_story_scores_coherent():
    """The false-positive guard: an explainer citing fires in several countries is coherent as long
    as its members AGREE on the lead country, however many others each one adds."""
    st = store_mod.Store("sqlite://")
    _add(st, "https://a.example/f", "Tribune A", 0.0, "What is a fire cloud and how do they form",
         category="Climate", days=1)
    _add(st, "https://b.example/f", "Tribune B", 0.0, "What is a fire cloud and how do they form now",
         category="Climate", days=1)
    _locate(st, "https://a.example/f", "FR", "ES", "AU")
    _locate(st, "https://b.example/f", "FR", "GB", "US")
    story = ss.cluster_from_store(st)[0]
    assert story["countries"] == ["FR"]
    assert story["geoCoherence"] == 1.0, "members agreeing on the lead country is coherent"
    assert len(story["eventCountries"]) == 5             # breadth alone must not look like a defect


def test_a_false_merge_scores_incoherent():
    """The production case: members located in unrelated countries, merged on shared title tokens.
    publisherDiversity rates such a cluster healthy; this must not."""
    st = store_mod.Store("sqlite://")
    # A title distinctive enough to genuinely cluster (the tokeniser now refuses boilerplate like
    # "Local news in brief" outright) — this test is about the METRIC, so the merge must happen.
    for pub, ctry in [("A", "US"), ("B", "YE"), ("C", "SG"), ("D", "DJ")]:
        cu = f"https://{pub}.example/brief"
        _add(st, cu, f"Outlet {pub}", 0.0, "Container terminal strike enters second week", days=1)
        _locate(st, cu, ctry)
    story = ss.cluster_from_store(st)[0]
    assert story["publisherCount"] == 4
    assert story["publisherDiversity"] == 1.0            # the discriminator that MISSES this
    assert story["geoCoherence"] == 0.25, "one member in four backs the consensus"
    assert story["locatedMembers"] == 4


def test_unlocated_members_abstain_rather_than_dilute():
    """A member nobody located is not evidence against the ones who were — coherence is over
    LOCATED members only, or a sparse feed would make every story look incoherent."""
    st = store_mod.Store("sqlite://")
    for i in range(4):
        cu = f"https://p{i}.example/q"
        _add(st, cu, f"Outlet {i}", 0.0, "Ferry runs aground near the northern port", days=1)
    _locate(st, "https://p0.example/q", "GR")
    _locate(st, "https://p1.example/q", "GR")
    story = ss.cluster_from_store(st)[0]
    assert story["totalCoverage"] == 4 and story["locatedMembers"] == 2
    assert story["geoCoherence"] == 1.0


def test_one_prolific_outlet_cannot_outvote_the_rest():
    """Votes are per MEMBER per country, so filing more copy does not buy more say."""
    st = store_mod.Store("sqlite://")
    for i in range(3):
        cu = f"https://loud.example/{i}"
        _add(st, cu, "Loud Wire", 0.0, f"Summit talks continue in the capital {i}", days=1)
        _locate(st, cu, "RU")
    for i in range(2):
        cu = f"https://calm.example/{i}"
        _add(st, cu, f"Calm Post {i}", 0.0, f"Summit talks continue in the capital {i}", days=1)
        _locate(st, cu, "CH")
    votes = {}
    for s_ in ss.cluster_from_store(st):
        votes.update(s_["countryVotes"])
    assert votes.get("RU", 0) == 3 and votes.get("CH", 0) == 2   # per member, not per publisher


def test_tied_consensus_counts_members_backing_either_winner():
    st = store_mod.Store("sqlite://")
    _add(st, "https://a.example/t", "A", 0.0, "Border crossing reopens after long closure", days=1)
    _add(st, "https://b.example/t", "B", 0.0, "Border crossing reopens after a long closure", days=1)
    _locate(st, "https://a.example/t", "PL")
    _locate(st, "https://b.example/t", "UA")
    story = ss.cluster_from_store(st)[0]
    assert story["countries"] == ["PL", "UA"]            # a genuine two-country event keeps both
    # Honest, not flattering: each member named ONE country and they differ, so only half the
    # located members back the strongest. Ambiguous geography reads as ambiguous.
    assert story["geoCoherence"] == 0.5


def test_members_recognising_both_places_of_a_two_country_event_stay_coherent():
    """The case the top-vote rule protects: when members agree the event spans both places, it is
    coherent — unlike members that each name a different single place."""
    st = store_mod.Store("sqlite://")
    _add(st, "https://a.example/b", "A", 0.0, "Border crossing reopens after long closure", days=1)
    _add(st, "https://b.example/b", "B", 0.0, "Border crossing reopens after a long closure", days=1)
    _locate(st, "https://a.example/b", "PL", "UA")
    _locate(st, "https://b.example/b", "PL", "UA")
    story = ss.cluster_from_store(st)[0]
    assert story["countries"] == ["PL", "UA"] and story["geoCoherence"] == 1.0


# --------------------------------------------------------------------------- #
# Cluster trust — the launch gates over geoCoherence.
#
# Two surfaces consume the verdict: the blindspot claim (withheld from a cluster the independent
# signal contradicts) and the default ranking (which must not lead with one). Both exist because
# single-linkage chaining ACCUMULATES publishers, so a false merge's wrongness and its rank have
# the same cause.
# --------------------------------------------------------------------------- #
def _false_merge(st, n=5):
    """Members located in unrelated countries, merged on shared title tokens — coherence 1/n.
    Five members so the score clears MIN_LOCATED_FOR_TRUST and is actually actionable."""
    for pub, ctry in list(zip("ABCDEF", ["US", "YE", "SG", "DJ", "CU", "OM"]))[:n]:
        cu = f"https://{pub}.example/strike"
        _add(st, cu, f"Outlet {pub}", 0.0, "Container terminal strike enters second week", days=1)
        _locate(st, cu, ctry)


def _trust(total, coh, located=9, **kw):
    kw.setdefault("floor", 0.7)
    kw.setdefault("unverified_size", 50)
    return ss._cluster_trust(total, coh, located, **kw)


def test_trust_leaves_the_median_story_alone():
    """A two-member cluster is ONE pairwise decision — chaining needs A~B, B~C and A≁C. The
    catalog median is 2, so the gates skip the typical story by construction, not by tuning."""
    assert _trust(2, 0.1) == ss.TRUST_OK
    assert _trust(1, None) == ss.TRUST_OK


def test_trust_verdicts():
    assert (_trust(10, 0.62), _trust(10, 0.70)) == (ss.TRUST_LOW, ss.TRUST_OK), "floor is inclusive"
    # No score at all: unusual only once the cluster is big enough for that to be notable.
    assert _trust(10, None, 0) == ss.TRUST_OK
    assert _trust(51, None, 0) == ss.TRUST_UNVERIFIED


def test_a_thin_coherence_score_cannot_condemn_a_cluster():
    """The defect the first production run of these gates exposed. Two located members that
    disagree score 0.50, and in a small cluster the commonest cause is a genuinely two-country
    story — a Hungarian Grand Prix with a British driver, Zelenskyy on Russia and Iran. The ratio
    exists at n=2; it is not evidence there. Below MIN_LOCATED_FOR_TRUST the verdict must be
    "cannot tell", never "bad"."""
    assert _trust(6, 0.50, 2) != ss.TRUST_LOW
    assert _trust(6, 0.67, 3) != ss.TRUST_LOW
    assert _trust(6, 0.50, 4) == ss.TRUST_LOW, "four located members is actionable"


def test_a_thin_score_on_a_big_cluster_is_unverified_not_ok():
    """Too few located members and nothing located at all are the same state — no independent read
    — so they get the same verdict rather than one silently passing as good."""
    assert _trust(200, 0.50, 2) == ss.TRUST_UNVERIFIED


def test_blindspot_is_withheld_from_an_incoherent_cluster():
    """The gate that matters most. A blindspot is a claim about PUBLISHER BEHAVIOUR; asserting one
    from a cluster whose members are located in four different countries states something false
    about the world, not merely something untidy about grouping."""
    st = store_mod.Store("sqlite://")
    _false_merge(st)
    story = ss.cluster_from_store(st)[0]
    assert story["clusterTrust"] == ss.TRUST_LOW
    assert story["geoCoherence"] == 0.2 and story["locatedMembers"] == 5
    assert story["blindspotSide"] is None, "no claim from a cluster we cannot stand behind"
    assert story["blindspotWithheld"] is True, "and the audit must be able to count it"


def test_a_trusted_cluster_still_reports_its_blindspot():
    """The gate must not simply delete the feature — a coherent, adequately-rated cluster keeps
    its claim."""
    st = store_mod.Store("sqlite://")
    _senate_and_wildfire(st)
    wild = next(s for s in ss.cluster_from_store(st) if "Wildfire" in s["title"])
    assert wild["clusterTrust"] == ss.TRUST_OK
    assert wild["blindspotSide"] in {"center", "right"} and wild["blindspotWithheld"] is False


def test_default_ranking_demotes_an_independently_suspect_cluster():
    """Ranking on publisherCount alone promotes exactly the defect it should bury: measured in
    production, a 106-publisher cluster at coherence 0.62 outsorts every correct story."""
    st = store_mod.Store("sqlite://")
    _false_merge(st)                                        # 4 publishers, coherence 0.25
    _senate_and_wildfire(st)                                # Senate: 3 publishers, coherent
    stories = ss.cluster_from_store(st)
    assert stories[0]["publisherCount"] == 3 and "Senate" in stories[0]["title"]
    assert stories[-1]["clusterTrust"] == ss.TRUST_LOW, "the bigger, suspect cluster sorts last"


def test_trust_ranking_can_be_switched_off(monkeypatch):
    """The kill switch restores pure size ordering without a deploy."""
    monkeypatch.setenv("RWE_STORY_TRUST_RANKING", "0")
    ss.clear_cache()
    st = store_mod.Store("sqlite://")
    _false_merge(st)
    _senate_and_wildfire(st)
    stories = ss.cluster_from_store(st)
    assert stories[0]["publisherCount"] == 5 and stories[0]["clusterTrust"] == ss.TRUST_LOW


def test_publishers_sort_gets_the_same_demotion():
    """Otherwise "sort by publishers" is a one-click route back to the card the default ordering
    exists to keep off the top."""
    st = store_mod.Store("sqlite://")
    _false_merge(st)
    _senate_and_wildfire(st)
    body = ss.list_stories(st, sort="publishers")
    assert body["stories"][0]["clusterTrust"] == ss.TRUST_OK
    assert body["stories"][-1]["clusterTrust"] == ss.TRUST_LOW


def test_time_sorts_are_untouched_by_trust():
    """A reader asking for newest wants newest — the demotion belongs to the "biggest" semantic."""
    st = store_mod.Store("sqlite://")
    _false_merge(st)
    _senate_and_wildfire(st)
    latest = ss.list_stories(st, sort="latest")["stories"]
    assert latest == sorted(latest, key=lambda s: (s["latest"] or "", s["id"]), reverse=True)


def test_diagnostics_report_trust_and_the_launch_monitors():
    """Both monitors are RATIOS. Raw counts stop being comparable the moment the corpus grows,
    which is the exact regime the mega-cluster was found in."""
    st = store_mod.Store("sqlite://")
    _false_merge(st)
    _senate_and_wildfire(st)
    d = ss.diagnostics(st)
    assert d["clusterTrust"][ss.TRUST_LOW] == 1
    assert d["blindspotsWithheld"] == 1
    assert d["largestOverP90"] > 0 and 0.0 < d["largestShareOfCovered"] <= 1.0


def test_link_quorum_is_off_by_default_and_tunable(monkeypatch):
    monkeypatch.delenv("RWE_CLUSTER_LINK_QUORUM", raising=False)
    assert ss.link_quorum() == 0.0
    monkeypatch.setenv("RWE_CLUSTER_LINK_QUORUM", "0.3")
    assert ss.link_quorum() == 0.3
    monkeypatch.setenv("RWE_CLUSTER_LINK_QUORUM", "nonsense")
    assert ss.link_quorum() == 0.0, "junk falls back rather than silently reshaping the catalog"
    monkeypatch.setenv("RWE_CLUSTER_LINK_QUORUM", "1.7")
    assert ss.link_quorum() == 0.0, "out of range is junk too"


# --------------------------------------------------------------------------- #
# Targeted repair — the stricter linkage rule applied ONLY where the independent signal objects.
#
# A GLOBAL quorum measured well on the mega-cluster (486 -> largest 45) and badly everywhere else:
# Berlin pride, 77 articles from 54 publishers at coherence 0.94, split into six pieces. Size
# cannot separate a good big cluster from a bad one; coherence can.
# --------------------------------------------------------------------------- #
def test_repair_leaves_trusted_clusters_byte_identical():
    """The whole point of targeting. A coherent story must not notice that repair is switched on."""
    st = store_mod.Store("sqlite://")
    _senate_and_wildfire(st)
    plain = ss.build_stories(ss._fetch(st))
    mended = ss.build_stories(ss._fetch(st), repair=0.5)
    assert plain == mended


def test_repair_splits_a_condemned_cluster():
    """Four members located in four different countries, merged on shared title tokens. The two
    that also share a distinctive phrase stay together; the rest separate or fall out."""
    st = store_mod.Store("sqlite://")
    _false_merge(st)
    before = ss.build_stories(ss._fetch(st))
    assert len(before) == 1 and before[0]["clusterTrust"] == ss.TRUST_LOW
    after = ss.build_stories(ss._fetch(st), repair=1.0)
    assert len(after) >= 1
    assert sum(s["totalCoverage"] for s in after) <= before[0]["totalCoverage"]


def test_repair_keeps_the_original_when_it_cannot_improve_it():
    """Two guards, both against silent destruction: a split into ONE piece separated nothing, and
    a split that loses most of the articles destroyed the cluster rather than resolving it.
    Dissolving a cluster improves every aggregate the audit prints, so this failure has to be
    caught in the code rather than noticed in a table."""
    members = [{"headline": f"unrelated headline number {i} alpha beta", "publishedAt": None,
                "publisher": f"P{i}"} for i in range(6)]
    kw = dict(sim=0.28, window_days=6.0, min_shared=3, min_tokens=3, idf=False,
              min_articles=2, min_publishers=2)
    # quorum 1.0 on members that share only boilerplate: everything falls below the gates.
    assert ss._repair(members, quorum=1.0, **kw) is None


def test_repair_quorum_is_off_by_default_and_tunable(monkeypatch):
    monkeypatch.delenv("RWE_STORY_REPAIR_QUORUM", raising=False)
    assert ss.repair_quorum() == 0.0
    monkeypatch.setenv("RWE_STORY_REPAIR_QUORUM", "0.3")
    assert ss.repair_quorum() == 0.3
    monkeypatch.setenv("RWE_STORY_REPAIR_QUORUM", "2")
    assert ss.repair_quorum() == 0.0, "out of range falls back rather than reshaping the catalog"


# --------------------------------------------------------------------------- #
# Wire sources — the one defect class no clustering signal can catch.
#
# A press-release template repeated 115 times clusters CORRECTLY: it really is about one template.
# geoCoherence rates it perfectly coherent, and articles-per-publisher was measured against the
# whole catalog and rejected at 0% precision / 0% recall. It is an identity fact about the source,
# so it is curated in the registry rather than guessed by a threshold.
# --------------------------------------------------------------------------- #
def _wire_template(st, n=4):
    for i in range(n):
        _add(st, f"https://lulegacy.com/{i}", "Lulegacy" if i < 3 else "MarketBeat", None,
             f"M D Sass LLC Makes New Investment in Gildan Activewear Inc holding {i}",
             category="Business", days=1)


def test_wire_outlets_never_form_a_story():
    st = store_mod.Store("sqlite://")
    _wire_template(st)
    _senate_and_wildfire(st)
    titles = [s["title"] for s in ss.build_stories(ss._fetch(st))]
    assert not any("Gildan" in t for t in titles)
    assert any("Senate" in t for t in titles), "real stories are untouched"


def test_wire_exclusion_can_be_switched_off(monkeypatch):
    """A kill switch, because a curation mistake should be reversible without a deploy."""
    monkeypatch.setenv("RWE_STORY_EXCLUDE_WIRE", "0")
    ss.clear_cache()
    st = store_mod.Store("sqlite://")
    _wire_template(st)
    assert any("Gildan" in s["title"] for s in ss.build_stories(ss._fetch(st)))


def test_an_unregistered_outlet_is_never_wire():
    """Absence of a registry row means unrated, not disqualified — otherwise the whole long tail
    of outlets we have not curated would silently stop producing stories."""
    st = store_mod.Store("sqlite://")
    for i in range(3):
        _add(st, f"https://smalltown{i}.example/x", f"Smalltown Gazette {i}", None,
             "council approves the new harbour development plan", days=1)
    assert len(ss.build_stories(ss._fetch(st))) == 1


# --------------------------------------------------------------------------- #
# Blindspot support floor — a gap claim needs a sample it could have been false in.
# --------------------------------------------------------------------------- #
def test_one_rated_publisher_cannot_assert_a_gap():
    """The defect this fixes, measured at 254 claims in production. One outlet covering something
    says nothing about who else did; the distribution is 1.0 in its bucket and two buckets are
    empty BY CONSTRUCTION, so the claim reports its own sample size."""
    st = store_mod.Store("sqlite://")
    _add(st, "https://npr.org/x1", "NPR", -1.0, "Ferry runs aground near the northern port", days=1)
    _add(st, "https://small.example/x2", "Small Gazette", None,
         "Ferry runs aground close to the northern port", days=1)
    story = ss.cluster_from_store(st)[0]
    assert story["distribution"] == {"left": 1.0, "center": 0.0, "right": 0.0}
    assert story["blindspotSide"] is None, "one rated publisher is not a coverage finding"


def test_two_rated_publishers_cannot_fill_three_buckets():
    """Also arithmetic: two outlets cannot cover three lean buckets, so an empty one is forced
    whatever they did. 206 production claims sat here."""
    st = store_mod.Store("sqlite://")
    _add(st, "https://npr.org/y1", "NPR", -1.0, "Ferry runs aground near the northern port", days=1)
    _add(st, "https://fox.com/y2", "Fox News", 1.5, "Ferry runs aground close to northern port", days=1)
    story = ss.cluster_from_store(st)[0]
    assert story["distribution"] == {"left": 0.5, "center": 0.0, "right": 0.5}
    assert story["blindspotSide"] is None


def test_three_rated_publishers_can_assert_a_gap():
    """Three is the floor because three is where covering every bucket becomes POSSIBLE — so an
    empty bucket is finally an observation rather than a consequence of the sample size."""
    st = store_mod.Store("sqlite://")
    for i, (pub, lean) in enumerate([("NPR", -1.0), ("The Guardian", -1.5), ("MSNBC", -1.4)]):
        _add(st, f"https://p{i}.example/z", pub, lean,
             "Ferry runs aground near the northern port again", days=1)
    story = ss.cluster_from_store(st)[0]
    assert story["distribution"]["left"] == 1.0
    assert story["blindspotSide"] in {"center", "right"}


def test_the_support_floor_is_tunable(monkeypatch):
    """Reversible without a deploy — 1 restores the pre-2026-07-28 behaviour of claiming on any
    sample, which is what 89.1% of production claims rested on."""
    monkeypatch.setenv("RWE_STORY_MIN_RATED", "1")
    ss.clear_cache()
    st = store_mod.Store("sqlite://")
    _add(st, "https://npr.org/w1", "NPR", -1.0, "Ferry runs aground near the northern port", days=1)
    _add(st, "https://small.example/w2", "Small Gazette", None,
         "Ferry runs aground close to the northern port", days=1)
    assert ss.cluster_from_store(st)[0]["blindspotSide"] == "center"


# --------------------------------------------------------------------------- #
# Duplicate merge — the RECALL fix, and the guards that stop it rebuilding chains.
#
# "Mass shooting reported at Seattle Center" and "…gunfire erupts near Seattle" share ONE token
# against MIN_SHARED_TOKENS = 3. No linkage rule reaches that; only richer text does.
# --------------------------------------------------------------------------- #
SEATTLE_TEXT = ("Police say a gunman opened fire at Seattle Center near the Space Needle on"
                " Friday, killing two people and wounding five before fleeing the plaza.")


def _dup_catalog(st):
    """One event, two clusters, disjoint headline vocabulary — the production shape."""
    for i, (pub, lean, title) in enumerate([
            ("NPR", -1.0, "At Least 2 Killed in Shooting at Food Festival in Seattle"),
            ("BBC News", 0.0, "Two killed in a shooting at the Seattle food festival"),
            ("Fox News", 1.5, "Two dead, five injured in gunfire near the Space Needle"),
            ("CNN", -1.2, "Five injured and two dead in gunfire close to Space Needle")]):
        _add(st, f"https://d{i}.example/s", pub, lean, title, desc=SEATTLE_TEXT, days=1)


def test_the_two_clusters_cannot_merge_on_headlines():
    """The premise. If these cleared the pairwise gate the merge pass would be unnecessary."""
    st = store_mod.Store("sqlite://")
    _dup_catalog(st)
    assert len(ss.build_stories(ss._fetch(st))) == 2


def test_descriptions_merge_what_headlines_cannot():
    st = store_mod.Store("sqlite://")
    _dup_catalog(st)
    merged = ss.build_stories(ss._fetch(st), merge=0.33)
    assert len(merged) == 1 and merged[0]["totalCoverage"] == 4


def test_a_merge_never_loses_an_article():
    """A merge adds coverage; losing any would be a bug, and the audit rejects on it."""
    st = store_mod.Store("sqlite://")
    _dup_catalog(st)
    _senate_and_wildfire(st)
    rows = ss._fetch(st)
    before = {c["url"] for s in ss.build_stories(rows) for c in s["coverage"]}
    after = {c["url"] for s in ss.build_stories(rows, merge=0.33) for c in s["coverage"]}
    assert before == after


def test_complete_linkage_stops_an_explainer_chaining_two_events():
    """The guard, taken from a real audit finding: a "Houthi attacks in the Red Sea: what to know"
    explainer paired with two SEPARATE Houthi events at 0.30 and 0.27. Single linkage would glue
    those two events together through it. Every pair inside a group must clear the bar."""
    # a~b = 0.44 and b~c = 0.44 both clear the bar; a~c = 0.097 does not.
    a = [{"headline": "alphaone alphatwo", "description": "alphathree bridgeword",
          "publishedAt": None}]
    b = [{"headline": "bridgeword alphaone", "description": "alphatwo betaone betatwo",
          "publishedAt": None}]
    c = [{"headline": "betaone betatwo", "description": "betathree bridgeword",
          "publishedAt": None}]
    out = ss._merge_duplicates([a, b, c], min_sim=0.3, max_gap_hours=48.0, max_size=100)
    assert len(out) == 2, "the two events must not end up in one group"
    assert sorted(len(g) for g in out) == [1, 2], "the explainer joins ONE of them, not both"


def test_the_size_cap_refuses_a_runaway():
    big = [[{"headline": "same story text here", "description": "shared vocabulary everywhere",
             "publishedAt": None}] * 60 for _ in range(2)]
    out = ss._merge_duplicates(big, min_sim=0.1, max_gap_hours=48.0, max_size=100)
    assert len(out) == 2, "120 articles exceeds the cap, so the merge is refused"


def test_a_wide_time_gap_is_a_recurring_topic_not_a_duplicate():
    """Coverage of one event arrives in a burst. Two clusters a week apart that read alike are a
    weekly fixture or a monthly filing, and pairing them would worsen with archive size."""
    st = store_mod.Store("sqlite://")
    _add(st, "https://e1.example/s", "NPR", -1.0,
         "At Least 2 Killed in Shooting at Food Festival in Seattle", desc=SEATTLE_TEXT, days=1)
    _add(st, "https://e2.example/s", "BBC News", 0.0,
         "Two killed in a shooting at the Seattle food festival", desc=SEATTLE_TEXT, days=1)
    _add(st, "https://e3.example/s", "Fox News", 1.5,
         "Two dead, five injured in gunfire near the Space Needle", desc=SEATTLE_TEXT, days=5)
    _add(st, "https://e4.example/s", "CNN", -1.2,
         "Five injured and two dead in gunfire close to Space Needle", desc=SEATTLE_TEXT, days=5)
    rows = ss._fetch(st)
    assert len(ss.build_stories(rows, merge=0.33)) == 2, "96h apart: not the same burst"
    # …and the window is the only reason: widen it and they join.
    assert len(ss.build_stories(rows, merge=0.33, merge_gap=200.0)) == 1


def test_merge_is_deterministic():
    st = store_mod.Store("sqlite://")
    _dup_catalog(st)
    _senate_and_wildfire(st)
    rows = ss._fetch(st)
    first = [(s["id"], s["totalCoverage"]) for s in ss.build_stories(rows, merge=0.33)]
    assert first == [(s["id"], s["totalCoverage"]) for s in ss.build_stories(rows, merge=0.33)]


def test_merge_is_off_by_default_and_tunable(monkeypatch):
    monkeypatch.delenv("RWE_STORY_MERGE_SIM", raising=False)
    assert ss.merge_similarity() == 0.0
    monkeypatch.setenv("RWE_STORY_MERGE_SIM", "0.33")
    assert ss.merge_similarity() == 0.33
    monkeypatch.setenv("RWE_STORY_MERGE_SIM", "nope")
    assert ss.merge_similarity() == 0.0
