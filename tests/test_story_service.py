"""Tests for examples/story_service.py — the single owner of Story construction (Commit 7).

Proves clustering into Stories, full Story construction (incl. the nullable image contract), timeline
ordering, coverage calculation, stable IDs that survive new coverage, pagination, sorting, filters,
diagnostics, that Discover + Stories reuse this one service, and that it never touches the recommender.
"""

import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import store as store_mod        # noqa: E402
import story_service as ss       # noqa: E402
import discover                  # noqa: E402
import location                  # noqa: E402
import outlet_registry           # noqa: E402

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


def test_coverage_rows_carry_the_outlets_ownership_and_unknown_stays_absent():
    """Ownership is resolved live from the registry at serve time (like the credibility gate),
    one value per row, and an outlet the registry doesn't classify carries None — never "other"
    (L2.2). NPR/Fox News here are real tranche rows, so this also pins the wire contract to the
    bundled data. Mutation check: resolving from m["url"] instead of m["publisher"] leaves the
    values intact (hosts resolve too) — the caught break is dropping the field or hardcoding it.
    """
    st = store_mod.Store("sqlite://")
    _add(st, "https://npr.org/a1", "NPR", -1.0, "Senate passes the funding bill after debate", days=1)
    _add(st, "https://fox.com/a2", "Fox News", 1.5, "Senate passes funding bill averting shutdown", days=1)
    _add(st, "https://obscure.example/a3", "Obscure Tribune", None,
         "Senate passes funding bill to avert shutdown")
    story = next(s for s in ss.cluster_from_store(st) if "Senate" in s["title"])
    by_pub = {c["publisher"]: c["ownership"] for c in story["coverage"]}
    assert by_pub == {"NPR": "independent", "Fox News": "conglomerate", "Obscure Tribune": None}


def test_factuality_rides_the_coverage_rows_only_when_the_deployment_publishes_it(monkeypatch):
    """A story's rows carry the RATER's verdict with its provenance, resolved live like ownership
    — and the whole field is behind ``RWE_PUBLIC_FACTUALITY`` (default OFF).

    Two absences that must stay distinguishable: with the gate off NO row carries a verdict and
    the story omits ``factualityPublished`` entirely (a disabled deployment transmits nothing);
    with it on, the flag says "we publish" and an unrated outlet still carries None. Collapsing
    those would label the 130 outlets we hold verdicts for as unrated.

    Mutation check: publishing the bare level instead of the object drops source/asOf/ratingUrl,
    and this asserts all four — a verdict shown without its attribution reads as ours."""
    st = store_mod.Store("sqlite://")
    _add(st, "https://npr.org/a1", "NPR", -1.0, "Senate passes the funding bill after debate", days=1)
    _add(st, "https://fox.com/a2", "Fox News", 1.5, "Senate passes funding bill averting shutdown", days=1)
    _add(st, "https://obscure.example/a3", "Obscure Tribune", None,
         "Senate passes funding bill to avert shutdown")

    monkeypatch.delenv("RWE_PUBLIC_FACTUALITY", raising=False)
    story = next(s for s in ss.cluster_from_store(st) if "Senate" in s["title"])
    assert story["factualityPublished"] is None
    assert all(c["factuality"] is None for c in story["coverage"])

    monkeypatch.setenv("RWE_PUBLIC_FACTUALITY", "1")
    story = next(s for s in ss.cluster_from_store(st) if "Senate" in s["title"])
    assert story["factualityPublished"] is True
    by_pub = {c["publisher"]: c["factuality"] for c in story["coverage"]}
    assert by_pub["Obscure Tribune"] is None          # unrated stays unrated, never a middle level
    verdict = by_pub["Fox News"]
    assert set(verdict) == {"value", "source", "asOf", "ratingUrl"}
    assert verdict["value"] in outlet_registry.FACTUALITY
    assert verdict["source"] == "mbfc" and verdict["asOf"] and verdict["ratingUrl"]


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
def test_stable_id_survives_new_coverage(monkeypatch):
    _refresh_inline(monkeypatch)
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    sid = next(s for s in ss.cluster_from_store(st) if "Senate" in s["title"])["id"]
    assert ss.get_story(st, sid) is not None
    # a new outlet covers the SAME event -> the cluster grows but the anchored id is unchanged.
    # The id must hold on BOTH sides of the refresh: the stale serve right after the write, and
    # the rebuilt view after it — a link that 404s for the duration of a rebuild is churn too.
    _add(st, "https://ap.org/a4", "AP", 0.1, "Senate passes funding bill in late vote", days=1)
    stale = ss.get_story(st, sid)
    assert stale is not None and stale["id"] == sid
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


def test_an_uncategorized_member_does_not_outvote_a_categorized_one():
    """The production defect: `_mode_topic` counted "" alongside real topics, so a story with more
    uncategorized members than any single category resolved to "" and the card showed no chip.

    Measured on the default 30-story front page (2026-08-30): 7 stories had no category and ALL 7
    carried at least one categorized member — none was blank for want of evidence. 66.8% of
    catalogue articles carry a category, so the uncategorized third was winning pluralities."""
    st = store_mod.Store("sqlite://")
    # Three uncategorized members against two that agree on World — the empties are the PLURALITY,
    # exactly the shape that used to lose. Plus one Science member, which is alphabetically BEFORE
    # World: without it, "ignore the counts and sort alphabetically" returns World too and the test
    # cannot see that mutation.
    for i, cat in enumerate(("", "", "", "World", "World", "Science")):
        _add(st, f"https://p{i}.example/1", f"Outlet{i}", 0.0,
             "Avalanche triggers deadly flash floods in Nepal valley",
             category=cat, desc="", days=1)
    s = next(iter(ss.cluster_from_store(st)))
    assert s["totalCoverage"] == 6, "the fixture no longer forms one cluster"
    assert s["topic"] == "World", \
        "an uncategorized majority outvoted the categorized members — absence is not a category"


def test_a_story_with_no_categorized_member_stays_blank():
    """The other half of the contract, and the reason this is not a fallback. `classify_topic`
    returns "" on purpose — this system does not invent metadata — so a cluster nobody classified
    still renders no chip. `discover.feed_article_to_article` and the card's `{story.topic && ...}`
    already agree; this keeps the aggregation agreeing too."""
    st = store_mod.Store("sqlite://")
    for i in range(3):
        _add(st, f"https://q{i}.example/1", f"Outlet{i}", 0.0,
             "Samsung introduces new foldable phone line", category="", desc="", days=1)
    s = next(iter(ss.cluster_from_store(st)))
    assert s["topic"] == "", "a topic was invented for a cluster with no categorized member"
    assert s["topic"] != "General", "the unreachable General default is back"


def test_the_topic_tiebreak_stays_deterministic():
    """Two topics with equal support resolve alphabetically, so two builds over one catalogue agree
    — the story id is stable and the chip must not flicker between them.

    Asserted on `_mode_topic` DIRECTLY, and under both member orderings, because that is the only
    way to see the property. Driven through a cluster build instead, `sorted` is stable, so a
    version with no tiebreak key at all returns whatever member order supplies — which in a build
    is deterministic and can coincidentally match the alphabetical answer. Two orderings, one
    result, is the claim.

    Tech/World deliberately: the alphabetically-first of the pair is also the SHORTER one, so a
    tiebreak keyed on length resolves differently. A Science/World pair ranks identically under
    both rules and could not see its own mutation."""
    a = [{"topic": "World"}, {"topic": "Tech"}, {"topic": ""}]
    b = [{"topic": "Tech"}, {"topic": "World"}, {"topic": ""}]
    assert ss._mode_topic(a) == ss._mode_topic(b) == "Tech", \
        "the tiebreak depends on member order — two builds could show different chips"


def test_filters_topic_publisher_lean():
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    assert ss.list_stories(st, topic="Climate")["total"] == 1        # only the wildfire event
    assert ss.list_stories(st, publisher="NPR")["total"] == 1        # only stories including NPR
    assert ss.list_stories(st, lean="right")["total"] == 1           # only the Senate event has right coverage


def _news_research_community(st):
    """Three events, each covered by a different curated source type.

    Real registry names on purpose — the filter's entire basis is the curated `kind` column, so a
    fixture of invented outlets would resolve to nothing and the test would pass against a filter
    that returned everything. Each event pairs its typed source with a plain news outlet, because a
    story needs two publishers to exist at all; that also makes the "has coverage from" reading
    testable, since the research and community events are equally News events.
    """
    _add(st, "https://nature.com/r1", "Nature", 0.0,
         "Fusion reactor sustains plasma for a record duration", category="Science", days=1)
    _add(st, "https://bbc.com/r2", "BBC News", 0.0,
         "Fusion reactor sustains plasma for record duration", category="Science", days=1)

    _add(st, "https://reddit.com/c1", "Reddit", 0.0,
         "Coastal ferry terminal refurbishment sparks debate", category="Business", days=2)
    _add(st, "https://npr.org/c2", "NPR", -1.0,
         "Coastal ferry terminal refurbishment sparks local debate", category="Business", days=2)

    _add(st, "https://foxnews.com/n1", "Fox News", 1.5,
         "Senate confirms the new transport secretary", days=3)
    _add(st, "https://cnn.com/n2", "CNN", -1.2,
         "Senate confirms new transport secretary", days=3)


def test_filter_type_selects_by_curated_source_type():
    st = store_mod.Store("sqlite://"); _news_research_community(st)
    assert ss.list_stories(st)["total"] == 3, "fixture: three events before any type filter"

    def titles(story_type):
        return {s["title"] for s in ss.list_stories(st, story_type=story_type)["stories"]}

    research = titles("research")
    community = titles("community")
    assert len(research) == 1 and "Fusion" in next(iter(research))
    assert len(community) == 1 and "ferry" in next(iter(community)).lower()
    # News is the majority, and — the point of "has coverage from" — it includes the research and
    # community events too, since each is also covered by a curated news outlet. A story is many
    # publishers; it has no single type of its own.
    assert len(titles("news")) == 3
    assert research | community <= titles("news")


def test_type_facets_count_what_selecting_each_lens_returns():
    """The badge's number must BE the result, or it is worse than no number."""
    st = store_mod.Store("sqlite://"); _news_research_community(st)
    facets = ss.list_stories(st)["typeFacets"]

    assert set(facets) == {"news", "research", "community"}, "every lens reports, including empties"
    for kind, n in facets.items():
        assert ss.list_stories(st, story_type=kind)["total"] == n, kind
    assert facets == {"news": 3, "research": 1, "community": 1}

    # They do NOT partition the feed: the research and community events are each also covered by a
    # news outlet, so they count twice. A badge that summed to `total` would be describing a
    # different filter from the one it sits on.
    assert sum(facets.values()) > ss.list_stories(st)["total"]


def test_type_facets_do_not_collapse_to_the_current_selection():
    """A picker counted after its own filter can only ever show the lens already chosen.

    This is the ordering the country and coverage-gap facets are computed under, and the reason
    the type filter is applied below the facet pass rather than beside `lean`: with it above,
    selecting Research would report research=1, news=0, community=0 and the reader could never
    find their way back out.
    """
    st = store_mod.Store("sqlite://"); _news_research_community(st)
    unfiltered = ss.list_stories(st)["typeFacets"]
    for kind in ("news", "research", "community"):
        assert ss.list_stories(st, story_type=kind)["typeFacets"] == unfiltered, kind
    # The same must hold under a sibling filter, which is the case that proves the counts are
    # scoped to the OTHER active filters rather than simply frozen.
    climate = ss.list_stories(st, topic="Science")["typeFacets"]
    assert climate != unfiltered and climate["research"] == 1 and climate["community"] == 0


def test_an_empty_lens_reports_zero_rather_than_going_missing():
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    facets = ss.list_stories(st)["typeFacets"]
    assert facets["research"] == 0 and facets["community"] == 0
    assert "research" in facets and "community" in facets, \
        "a missing key is indistinguishable from 'not computed'; the badge needs a real 0"


def test_filter_type_never_invents_a_classification():
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    # Not one of these outlets is curated research or forum, so both lenses must come back empty
    # rather than falling back to "everything" or bucketing strangers.
    assert ss.list_stories(st)["total"] == 2
    assert ss.list_stories(st, story_type="research")["total"] == 0
    assert ss.list_stories(st, story_type="community")["total"] == 0
    # An unrecognised value is not a filter — it must not silently narrow or empty the feed.
    assert ss.list_stories(st, story_type="wire")["total"] == 2
    assert ss.list_stories(st, story_type="nonsense")["total"] == 2
    assert ss.list_stories(st, story_type=None)["total"] == 2       # "All" keeps the whole feed


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


def _refresh_inline(monkeypatch):
    """Run background stale-refreshes synchronously on the requesting thread, so a test can assert
    the serve-stale sequence (stale first, fresh after one refresh) without real threads."""
    monkeypatch.setattr(ss, "_spawn_refresh", lambda store_, logical: ss._run_refresh(store_, logical))


def test_an_ingest_is_detected_immediately_and_visible_after_one_refresh(monkeypatch):
    """A new article must never be hidden by a TTL — the entry goes stale the moment the write
    lands. What changed with serve-stale: the reader who FINDS the stale entry is handed the
    previous build instead of the rebuild bill, and visibility follows one background refresh.
    List and detail stay mutually consistent throughout — both read the same cached build, so they
    flip to the new coverage together, never disagreeing about a link between them."""
    _refresh_inline(monkeypatch)
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    before = next(s for s in ss.list_stories(st)["stories"] if "Senate" in s["title"])
    _add(st, "https://ap.org/a9", "AP", 0.1, "Senate passes funding bill in late vote", days=1)
    # The finder is served the pre-ingest build — that is the point of the policy — and their
    # request triggers the (here: inline) refresh that ends the staleness.
    stale = next(s for s in ss.list_stories(st)["stories"] if "Senate" in s["title"])
    assert stale["totalCoverage"] == before["totalCoverage"]
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


def test_cache_invalidates_when_content_changes_at_constant_row_count(monkeypatch):
    """Regression: staleness must be judged by a content fingerprint, not a row COUNT.

    Deleting N articles and inserting N others leaves the count identical while the catalog is
    entirely different — which is exactly what a retention prune plus an ingest in the same interval
    does. Fingerprinted on count alone, the change is never DETECTED: no refresh is ever requested
    and the old clusters are served forever, not merely for one serve-stale interval. The read
    after the refresh is where the two designs diverge, so that is the read this asserts on."""
    _refresh_inline(monkeypatch)
    st = store_mod.Store("sqlite://")
    first = ["https://one.example/a", "https://two.example/a"]
    for cu, pub in zip(first, ("NPR", "BBC News")):
        _add(st, cu, pub, 0.0, "Harbour pilots ratify their contract", days=1)
    assert any("Harbour" in s["title"] for s in ss.list_stories(st)["stories"])

    st.delete_feed_articles(first)
    second = ["https://three.example/a", "https://four.example/a"]
    for cu, pub in zip(second, ("CNN", "Fox News")):
        _add(st, cu, pub, 0.0, "Rail operators publish the winter timetable", days=1)

    ss.list_stories(st)                     # the finder: served stale, triggers the (inline) refresh
    titles = {s["title"] for s in ss.list_stories(st)["stories"]}     # same row count as before
    assert any("Rail operators" in t for t in titles), "change at constant row count never detected"
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


def test_warm_cache_is_invalidated_by_the_next_ingest(monkeypatch):
    """A warm build must not outlive the catalog it was built from — it serves for at most one
    refresh after the write, then the rebuilt view takes over."""
    _refresh_inline(monkeypatch)
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    ss.clear_cache()
    assert ss.warm_cache(st) == 2
    _add(st, "https://ap.org/new", "AP", 0.1, "Harbour pilots ratify their contract", days=1)
    _add(st, "https://re.example/new", "Reuters", 0.0, "Harbour pilots ratify contract", days=1)
    assert ss.list_stories(st)["total"] == 2      # the finder still reads the warm's build…
    assert ss.list_stories(st)["total"] == 3      # …and the refresh it triggered replaces it


# --------------------------------------------------------------------------- #
# Serve-stale-while-revalidate (P0-1). The measured failure this closes: an 11.5 s rebuild in
# front of a 6 s web deadline, handed to whichever reader arrived first after every ingest.
# --------------------------------------------------------------------------- #
def test_a_stale_build_is_served_and_the_rebuild_leaves_the_reader_thread(monkeypatch):
    """The core of the fix, asserted from both sides: the reader gets the previous build with ZERO
    build work on their thread, and exactly one background refresh is requested for the key."""
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    assert ss.warm_cache(st) == 2
    _add(st, "https://ap.org/new", "AP", 0.1, "Harbour pilots ratify their contract", days=1)
    _add(st, "https://re.example/new", "Reuters", 0.0, "Harbour pilots ratify contract", days=1)

    spawned = []
    monkeypatch.setattr(ss, "_spawn_refresh", lambda store_, logical: spawned.append(logical))
    calls = {"n": 0}
    real = ss.build_stories
    monkeypatch.setattr(ss, "build_stories",
                        lambda *a, **kw: (calls.__setitem__("n", calls["n"] + 1), real(*a, **kw))[1])

    body = ss.list_stories(st)
    assert body["total"] == 2, "the reader was made to wait for the new catalog"
    assert calls["n"] == 0, "the rebuild ran on the reader's thread"
    assert len(spawned) == 1

    ss._run_refresh(st, spawned[0])               # the background thread's body, run inline
    assert calls["n"] == 1
    assert ss.list_stories(st)["total"] == 3
    assert calls["n"] == 1, "the refreshed build was not served from cache"


def test_stale_refresh_requests_are_single_flight_per_key(monkeypatch):
    """Every reader landing on the same stale entry during the rebuild coalesces into ONE refresh —
    otherwise the ~12 s window would spawn one identical rebuild per request, recreating in the
    background the duplicate-build convoy the build lock exists to prevent inline."""
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    assert ss.warm_cache(st) == 2
    _add(st, "https://ap.org/new", "AP", 0.1, "Harbour pilots ratify their contract", days=1)
    _add(st, "https://re.example/new", "Reuters", 0.0, "Harbour pilots ratify contract", days=1)

    spawned = []
    monkeypatch.setattr(ss, "_spawn_refresh", lambda store_, logical: spawned.append(logical))
    ss.list_stories(st); ss.list_stories(st); ss.list_stories(st)
    assert len(spawned) == 1, "concurrent stale hits each spawned their own rebuild"

    ss._run_refresh(st, spawned[0])
    assert ss.list_stories(st)["total"] == 3      # fresh now…
    assert len(spawned) == 1, "…and a fresh hit must request nothing"


def test_serve_stale_kill_switch_restores_the_reader_paid_rebuild(monkeypatch):
    """RWE_STORIES_SERVE_STALE=0 must be a true kill switch: the old behaviour back, byte for byte
    — the reader rebuilds inline, sees the new catalog at once, and nothing is spawned."""
    monkeypatch.setenv("RWE_STORIES_SERVE_STALE", "0")
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    assert ss.warm_cache(st) == 2
    _add(st, "https://ap.org/new", "AP", 0.1, "Harbour pilots ratify their contract", days=1)
    _add(st, "https://re.example/new", "Reuters", 0.0, "Harbour pilots ratify contract", days=1)

    spawned = []
    monkeypatch.setattr(ss, "_spawn_refresh", lambda store_, logical: spawned.append(logical))
    calls = {"n": 0}
    real = ss.build_stories
    monkeypatch.setattr(ss, "build_stories",
                        lambda *a, **kw: (calls.__setitem__("n", calls["n"] + 1), real(*a, **kw))[1])

    body = ss.list_stories(st)
    assert body["total"] == 3 and calls["n"] == 1 and spawned == []


def test_an_entry_past_its_ttl_is_never_served_stale(monkeypatch):
    """The TTL bounds rolling-window drift, and serve-stale must not stretch a bound it did not
    set: past the TTL the reader rebuilds inline, exactly as before the policy existed."""
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    assert ss.warm_cache(st) == 2
    _add(st, "https://ap.org/new", "AP", 0.1, "Harbour pilots ratify their contract", days=1)
    _add(st, "https://re.example/new", "Reuters", 0.0, "Harbour pilots ratify contract", days=1)
    with ss._CACHE_LOCK:                          # age the entry past the TTL, internals on purpose
        entries = ss._CACHE[st]
        for k, (built_at, fp, stories) in list(entries.items()):
            entries[k] = (built_at - ss.cache_ttl() - 1.0, fp, stories)

    spawned = []
    monkeypatch.setattr(ss, "_spawn_refresh", lambda store_, logical: spawned.append(logical))
    body = ss.list_stories(st)
    assert body["total"] == 3, "an expired entry was served stale"
    assert spawned == [], "an expired entry is a cold miss, not a stale serve"


def test_warm_cache_builds_fresh_even_over_a_stale_entry():
    """The warm's allow_stale=False: its one purpose is a fresh build. Served its own stale entry
    it would 'warm' with the build it exists to replace and queue a refresh forever."""
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    assert ss.warm_cache(st) == 2
    _add(st, "https://ap.org/new", "AP", 0.1, "Harbour pilots ratify their contract", days=1)
    _add(st, "https://re.example/new", "Reuters", 0.0, "Harbour pilots ratify contract", days=1)
    assert ss.warm_cache(st) == 3
    assert ss.list_stories(st)["total"] == 3


def test_a_refresh_that_loses_the_race_to_the_warm_stands_down(monkeypatch):
    """The poller's warm and a reader-triggered refresh can chase the same key. The build lock
    serialises them and the loser's under-lock re-check finds the winner's build fresh — one
    rebuild total, not two."""
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    assert ss.warm_cache(st) == 2
    _add(st, "https://ap.org/new", "AP", 0.1, "Harbour pilots ratify their contract", days=1)
    _add(st, "https://re.example/new", "Reuters", 0.0, "Harbour pilots ratify contract", days=1)

    spawned = []
    monkeypatch.setattr(ss, "_spawn_refresh", lambda store_, logical: spawned.append(logical))
    ss.list_stories(st)                           # stale serve; refresh queued but not yet run
    assert ss.warm_cache(st) == 3                 # the warm wins the rebuild

    calls = {"n": 0}
    real = ss.build_stories
    monkeypatch.setattr(ss, "build_stories",
                        lambda *a, **kw: (calls.__setitem__("n", calls["n"] + 1), real(*a, **kw))[1])
    ss._run_refresh(st, spawned[0])               # the loser arrives second…
    assert calls["n"] == 0, "the losing refresh rebuilt an answer it was already holding"
    assert ss.list_stories(st)["total"] == 3


def test_a_completed_refresh_releases_its_key_for_the_next_cycle(monkeypatch):
    """Found by mutation: dropping the ``finally`` discard left every test above green. One stale
    cycle worked; the SECOND ingest's stale hit then coalesced into a refresh that no longer
    existed, and staleness became permanent until the poller's next warm."""
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    assert ss.warm_cache(st) == 2
    spawned = []
    monkeypatch.setattr(ss, "_spawn_refresh", lambda store_, logical: spawned.append(logical))

    _add(st, "https://ap.org/new", "AP", 0.1, "Harbour pilots ratify their contract", days=1)
    _add(st, "https://re.example/new", "Reuters", 0.0, "Harbour pilots ratify contract", days=1)
    assert ss.list_stories(st)["total"] == 2 and len(spawned) == 1
    ss._run_refresh(st, spawned[0])
    assert ss.list_stories(st)["total"] == 3

    _add(st, "https://cnn.com/w2", "CNN", -1.0, "Rail operators publish the winter timetable", days=1)
    _add(st, "https://fox.com/w2", "Fox News", 1.4, "Rail operators publish winter timetable", days=1)
    assert ss.list_stories(st)["total"] == 3, "second cycle should serve stale first"
    assert len(spawned) == 2, "the released key must accept the second cycle's refresh"
    ss._run_refresh(st, spawned[1])
    assert ss.list_stories(st)["total"] == 4


def test_a_waiter_adopts_the_winners_fingerprint_instead_of_rebuilding(monkeypatch):
    """Found by mutation: without the under-lock fingerprint re-read, a caller whose entry-time
    fingerprint predates the winner's build judges that build 'stale' and rebuilds the answer it is
    already holding. Deterministic construction: the first fingerprint read lies (an old value),
    every later read tells the truth — the re-read under the lock is what must save the caller."""
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    assert ss.warm_cache(st) == 2                 # entry stored under the REAL fingerprint

    real_fp = st.catalog_fingerprint
    reads = {"n": 0}

    def lying_first_read():
        reads["n"] += 1
        return ("bogus", "old") if reads["n"] == 1 else real_fp()

    monkeypatch.setattr(st, "catalog_fingerprint", lying_first_read)
    calls = {"n": 0}
    real_build = ss.build_stories
    monkeypatch.setattr(ss, "build_stories",
                        lambda *a, **kw: (calls.__setitem__("n", calls["n"] + 1), real_build(*a, **kw))[1])

    # allow_stale=False routes past the stale-serve branch straight to the lock — the path a real
    # waiter takes. Its entry read got the bogus old fingerprint; the re-read gets the truth.
    assert ss.warm_cache(st) == 2
    assert calls["n"] == 0, "the waiter rebuilt an answer the cache already held fresh"
    assert reads["n"] >= 2, "the under-lock re-read never happened"


def test_the_default_refresh_path_really_runs_on_a_thread():
    """One real-thread smoke over the unpatched wiring: a stale serve spawns the daemon thread,
    the thread builds, the key is released, and the next read is fresh — no monkeypatch."""
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    assert ss.warm_cache(st) == 2
    _add(st, "https://ap.org/new", "AP", 0.1, "Harbour pilots ratify their contract", days=1)
    _add(st, "https://re.example/new", "Reuters", 0.0, "Harbour pilots ratify contract", days=1)

    assert ss.list_stories(st)["total"] == 2      # stale serve; real refresh thread started
    deadline = datetime.now(timezone.utc) + timedelta(seconds=10)
    while datetime.now(timezone.utc) < deadline:
        with ss._CACHE_LOCK:
            done = not ss._REFRESH_PENDING
        if done and ss.list_stories(st)["total"] == 3:
            break
    assert ss.list_stories(st)["total"] == 3, "the background refresh never landed"


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


# --------------------------------------------------------------------------- #
# The dek as clustering signal — built, measured, and NOT adopted.
#
# The premise was sound: the clusterer sees 8-12 title tokens, so "Fed holds rates steady" and
# "Central bank leaves borrowing costs unchanged" share ZERO tokens and can never meet min_shared
# however the thresholds move. Four in five catalog articles are in no story at all.
#
# The measurement killed it. Over four realistic paraphrase pairs and four realistic TEMPLATE pairs
# (same wire boilerplate, different event — election results by state, earnings by ticker, scores by
# game, weather by county), template pairs score STRICTLY HIGHER than paraphrase pairs on both
# shared-token count and Jaccard, at every cap. Not overlapping distributions — fully inverted ones.
# So no floor separates them, which is what `desc_min_shared` was supposed to do.
#
# These tests pin the negative result so the idea is not re-proposed on its premise alone.
# --------------------------------------------------------------------------- #
#: (headline, dek) pairs about ONE event, written differently — what the dek signal is meant to join.
PARAPHRASE_PAIRS = [
    (("Fed holds rates steady",
      "The Federal Reserve left its benchmark interest rate unchanged on Wednesday, citing "
      "stubborn inflation."),
     ("Central bank leaves borrowing costs unchanged",
      "Policymakers at the Federal Reserve held the benchmark interest rate steady Wednesday as "
      "inflation stayed elevated.")),
    (("Strike ends at Detroit plant",
      "Union members ratified a four-year contract on Friday, ending a six-week walkout at the "
      "assembly plant in Detroit."),
     ("Autoworkers ratify deal after six weeks",
      "The union said its members approved a four-year contract Friday, concluding a walkout that "
      "idled the Detroit assembly plant.")),
    (("Quake kills dozens in western Turkey",
      "A magnitude 6.1 earthquake struck western Turkey before dawn, killing at least 40 people "
      "and collapsing buildings."),
     ("At least 40 dead after tremor hits Izmir",
      "Rescuers searched collapsed buildings in western Turkey after a magnitude 6.1 earthquake "
      "before dawn killed dozens.")),
    (("EU agrees migration overhaul",
      "European Union governments reached a deal in Brussels on Thursday to overhaul the bloc's "
      "asylum and migration rules."),
     ("Brussels deal rewrites asylum rules",
      "After all-night talks, European Union governments agreed Thursday to rewrite the bloc's "
      "asylum and migration system.")),
]

#: Pairs sharing a TEMPLATE but covering different events — what the dek signal must not join.
TEMPLATE_PAIRS = [
    (("Trump wins Ohio",
      "The Republican candidate took Ohio with 54 percent of the vote, election officials said "
      "Tuesday night."),
     ("Trump wins Iowa",
      "The Republican candidate took Iowa with 51 percent of the vote, election officials said "
      "Tuesday night.")),
    (("Apple beats earnings expectations",
      "The company reported quarterly revenue above analyst estimates, sending shares higher in "
      "after-hours trading Thursday."),
     ("Microsoft beats earnings expectations",
      "The company reported quarterly revenue above analyst estimates, sending shares higher in "
      "after-hours trading Tuesday.")),
    (("Lakers beat Suns 112-104",
      "The visitors pulled away in the fourth quarter Saturday night, improving to 21-14 with "
      "their third straight road win."),
     ("Celtics beat Magic 118-109",
      "The visitors pulled away in the fourth quarter Saturday night, improving to 24-11 with "
      "their fourth straight road win.")),
    (("Storm warning issued for Norfolk",
      "Forecasters warned of gusts up to 70 mph and coastal flooding overnight, urging residents "
      "to avoid unnecessary travel."),
     ("Storm warning issued for Suffolk",
      "Forecasters warned of gusts up to 65 mph and coastal flooding overnight, urging residents "
      "to avoid unnecessary travel.")),
]


def _pair_scores(pairs, cap):
    import clustering as cl
    out = []
    for (h1, d1), (h2, d2) in pairs:
        a = ss.article_tokens({"headline": h1, "description": d1}, cap)
        b = ss.article_tokens({"headline": h2, "description": d2}, cap)
        out.append((len(a & b), cl.jaccard(a, b)))
    return out


@pytest.mark.parametrize("cap", [6, 12])
def test_no_shared_token_floor_can_separate_paraphrase_from_template(cap):
    """**The finding that stops this change from being adopted.**

    ``desc_min_shared`` exists on the theory that three-of-eight is signal and three-of-twenty is
    noise, so a higher floor buys back the precision the deks cost. It cannot: the WORST paraphrase
    pair scores below the BEST template pair, so any floor admitting all four paraphrases admits all
    four templates, and any floor rejecting the templates rejects the feature's whole purpose.

    The reason is structural rather than a fixture accident. A template pair shares its prose BY
    CONSTRUCTION — it is the same sentence with one entity substituted — while a paraphrase pair
    shares only the entities two desks independently chose to lead with. Lengthening the token set
    therefore helps the template more than the paraphrase, monotonically. Separating these needs
    information bag-of-words does not carry (that *Ohio* and *Iowa* are alternatives, not variants),
    which is a different phase, not a tuned threshold."""
    para, tmpl = _pair_scores(PARAPHRASE_PAIRS, cap), _pair_scores(TEMPLATE_PAIRS, cap)
    assert min(s for s, _ in para) <= max(s for s, _ in tmpl), "shared-count floor cannot separate"
    assert min(j for _, j in para) <= max(j for _, j in tmpl), "a similarity bar cannot either"
    # Stronger, and the reason the first two assertions are not a near-miss: the orders are fully
    # INVERTED. Every template pair outscores every paraphrase pair.
    assert min(s for s, _ in tmpl) >= max(s for s, _ in para)
    assert min(j for _, j in tmpl) >= max(j for _, j in para)


def test_dek_tokens_are_off_by_default_and_the_signal_degenerates_exactly(monkeypatch):
    """Off must mean *byte-identical to before this existed*, not "nearly". ``article_tokens`` is
    now the single owner of the clustering signal for the build, the repair and the audit, so it has
    to reduce to the plain ``title_tokens`` call it replaced at every one of them."""
    import clustering as cl
    monkeypatch.delenv("RWE_CLUSTER_DESC_TOKENS", raising=False)
    assert ss.desc_tokens() == 0
    for (h, d), _ in PARAPHRASE_PAIRS + TEMPLATE_PAIRS:
        art = {"headline": h, "description": d}
        assert ss.article_tokens(art, 0) == cl.title_tokens(h)
        assert ss.article_tokens(art, ss.desc_tokens()) == cl.title_tokens(h)
    monkeypatch.setenv("RWE_CLUSTER_DESC_TOKENS", "12")
    assert ss.desc_tokens() == 12
    monkeypatch.setenv("RWE_CLUSTER_DESC_TOKENS", "nonsense")
    assert ss.desc_tokens() == 0, "junk falls back rather than silently reshaping the catalog"


def test_dek_min_shared_is_tunable(monkeypatch):
    monkeypatch.delenv("RWE_CLUSTER_DESC_MIN_SHARED", raising=False)
    assert ss.desc_min_shared() == 5
    monkeypatch.setenv("RWE_CLUSTER_DESC_MIN_SHARED", "8")
    assert ss.desc_min_shared() == 8
    monkeypatch.setenv("RWE_CLUSTER_DESC_MIN_SHARED", "")
    assert ss.desc_min_shared() == 5


def _pair_catalog(st, pairs):
    for i, ((h1, d1), (h2, d2)) in enumerate(pairs):
        _add(st, f"https://npr.org/p{i}", "NPR", -1.0, h1, desc=d1, days=0)
        _add(st, f"https://fox.com/p{i}", "Fox News", 1.5, h2, desc=d2, days=0)


def test_the_catalog_is_untouched_while_the_dek_signal_is_off():
    """The production path. Same rows, same stories, same ids, same order — the change is inert."""
    st = store_mod.Store("sqlite://")
    _pair_catalog(st, PARAPHRASE_PAIRS + TEMPLATE_PAIRS)
    _senate_and_wildfire(st)
    rows = ss._fetch(st)
    off = [(s["id"], s["title"], s["totalCoverage"]) for s in ss.build_stories(rows, desc=0)]
    assert off == [(s["id"], s["title"], s["totalCoverage"]) for s in ss.build_stories(rows)]
    # The baseline both directions are measured from, and it is not a clean one. On headlines alone
    # NONE of the four paraphrase pairs forms a story (0 shared title tokens, as designed) while TWO
    # of the four template pairs ALREADY DO — "Apple/Microsoft beats earnings expectations" and
    # "Storm warning issued for Norfolk/Suffolk" each share 3 title tokens at Jaccard 0.60. So the
    # template collision is a live defect today, before any dek is involved; what the dek signal
    # does is take it from two-of-four to four-of-four while buying the paraphrases.
    assert len(off) == 4                                   # 2 real events + 2 template false merges
    assert not [s for s in off if "Fed" in s[1] or "Central bank" in s[1] or "Trump" in s[1]]


def test_dek_signal_buys_paraphrase_recall_and_pays_for_it_in_template_merges():
    """What turning it on actually does, both halves of it, on one catalog.

    The recall is real — four paraphrase pairs that could never cluster now do. The cost is equally
    real and arrives at the same threshold: four unrelated template pairs cluster too. Recorded
    together deliberately, because the first half alone is the number that would get this shipped."""
    st = store_mod.Store("sqlite://")
    _pair_catalog(st, PARAPHRASE_PAIRS + TEMPLATE_PAIRS)
    rows = ss._fetch(st)
    on = ss.build_stories(rows, desc=12, min_shared=5)
    joined = {s["title"] for s in on if s["totalCoverage"] == 2}
    assert len(on) == 8, f"all eight pairs clustered, not just the four wanted: {len(on)}"
    assert any("Fed" in t or "Central bank" in t for t in joined), "the paraphrase recall is real"
    assert any("Trump wins" in t for t in joined), "and so is the false merge it comes with"


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


# --------------------------------------------------------------------------- #
# Stable story ids — the fix for a measured 5.1%/day churn.
#
# _story_id anchors to the earliest member, and the failure is that anchor LEAVING: the rolling
# window drops it, or a backfilled article displaces it. No member-derived anchor survives that,
# so ids are given BACK rather than recomputed.
# --------------------------------------------------------------------------- #
def _cov(*urls):
    return {"coverage": [{"url": u} for u in urls]}


def test_an_id_survives_its_representative_ageing_out():
    """The dominant production case — 72 of 81 measured churn events."""
    prior = {"a": "st_old", "b": "st_old", "c": "st_old"}
    assert ss.reassign_ids(prior, [_cov("b", "c", "d")]) == {0: "st_old"}


def test_an_id_survives_an_earlier_article_arriving():
    """Ingestion is not ordered by publication time; GDELT backfill moves the anchor backwards."""
    prior = {"b": "st_old", "c": "st_old"}
    assert ss.reassign_ids(prior, [_cov("older", "b", "c")]) == {0: "st_old"}


def test_a_merge_keeps_the_larger_contributors_id():
    """One story may claim only one prior id, so the smaller half's id retires rather than both
    surviving on one story."""
    prior = {"a": "st_big", "b": "st_big", "c": "st_big", "x": "st_small", "y": "st_small"}
    assert ss.reassign_ids(prior, [_cov("a", "b", "c", "x", "y")]) == {0: "st_big"}


def test_a_split_gives_the_id_to_the_piece_holding_most_of_the_coverage():
    """One prior id may go to only one story. The other piece is a NEW story, which is what it is."""
    prior = {u: "st_one" for u in "abcd"}
    out = ss.reassign_ids(prior, [_cov("a", "b", "c"), _cov("d")])
    assert out == {0: "st_one"}, "the 3-article piece keeps it; the 1-article piece is new"


def test_a_minority_overlap_does_not_inherit():
    """Below a majority two clusters could each have a claim, and which won would depend on
    ordering rather than on the data."""
    prior = {"a": "st_old"}
    assert ss.reassign_ids(prior, [_cov("a", "x", "y", "z")]) == {}


def test_a_brand_new_story_keeps_its_derived_id():
    assert ss.reassign_ids({}, [_cov("a", "b")]) == {}


def test_reassignment_is_deterministic():
    prior = {"a": "st_1", "b": "st_1", "c": "st_2", "d": "st_2"}
    stories = [_cov("a", "b"), _cov("c", "d")]
    assert ss.reassign_ids(prior, stories) == ss.reassign_ids(prior, stories)


def test_ids_persist_across_rebuilds_when_the_oldest_article_ages_out():
    """End to end through the store, which is where the churn actually bites."""
    st = store_mod.Store("sqlite://")
    for i, (pub, d) in enumerate([("NPR", 3), ("BBC News", 2), ("CNN", 1)]):
        _add(st, f"https://age{i}.example/x", pub, 0.0,
             "Ferry runs aground near the northern port", days=d)
    first = ss.stabilize_ids(st, ss.build_stories(ss._fetch(st)))
    assert len(first) == 1
    # The window rolls: the earliest member drops out. Derived ids would change here.
    rows = [r for r in ss._fetch(st) if "age0" not in (r.get("canonicalUrl") or "")]
    rebuilt = ss.build_stories(rows)
    assert rebuilt[0]["id"] != first[0]["id"], "the derived id really does move"
    assert ss.stabilize_ids(st, rebuilt)[0]["id"] == first[0]["id"], "…and is given back"


def test_identity_failure_never_breaks_the_page():
    """A churned id is a broken link; a 500 is a broken page. Fail soft in both directions."""
    class Broken:
        def story_member_ids(self):
            raise RuntimeError("no table")

    stories = [dict(_cov("a", "b"), id="st_x")]
    assert ss.stabilize_ids(Broken(), stories)[0]["id"] == "st_x"


def test_stable_ids_are_on_by_default_and_reversible(monkeypatch):
    monkeypatch.delenv("RWE_STORY_STABLE_IDS", raising=False)
    assert ss.stable_ids() is True
    monkeypatch.setenv("RWE_STORY_STABLE_IDS", "0")
    assert ss.stable_ids() is False


# --------------------------------------------------------------------------- #
# Publisher identity — publisherCount counts OUTLETS, not name forms.
#
# Measured: 181 of 1,367 names were duplicates, and 35 stories cleared min_publishers only because
# one outlet was counted twice — the largest being 17 articles from 17 *.iheart.com hostnames.
# --------------------------------------------------------------------------- #
def _syndicated(st, hosts, title="Accused murderer arrested boarding a cruise ship"):
    for i, h in enumerate(hosts):
        _add(st, f"https://{h}/a{i}", h, 0.0, title, days=1)


def test_a_syndication_network_is_not_a_multi_publisher_story():
    """The production case. Seventeen station hostnames under one domain are one outlet, and this
    cluster only ever cleared min_publishers because they were counted separately."""
    st = store_mod.Store("sqlite://")
    _syndicated(st, ["Kfbk.Iheart.Com", "Wjjs.Iheart.Com", "Kogo.Iheart.Com"])
    assert ss.build_stories(ss._fetch(st)) == []


def test_the_same_cluster_survives_with_identity_off(monkeypatch):
    """Proof the removal is the identity rule and not something else about the fixture."""
    monkeypatch.setenv("RWE_STORY_PUBLISHER_IDENTITY", "0")
    ss.clear_cache()
    st = store_mod.Store("sqlite://")
    _syndicated(st, ["Kfbk.Iheart.Com", "Wjjs.Iheart.Com", "Kogo.Iheart.Com"])
    stories = ss.build_stories(ss._fetch(st))
    assert len(stories) == 1 and stories[0]["publisherCount"] == 3


def test_a_genuine_story_keeps_every_outlet():
    st = store_mod.Store("sqlite://")
    _senate_and_wildfire(st)
    senate = next(s for s in ss.build_stories(ss._fetch(st)) if "Senate" in s["title"])
    assert senate["publisherCount"] == 3


def test_publisher_count_collapses_but_the_story_survives():
    """Three name forms, two outlets: the count was wrong, the story is real."""
    st = store_mod.Store("sqlite://")
    for i, pub in enumerate(["Sportskeeda.Com", "Sportskeeda", "BBC News"]):
        _add(st, f"https://s{i}.example/x", pub, 0.0,
             "Djokovic and Sinner reach the semi finals", days=1)
    story = ss.build_stories(ss._fetch(st))[0]
    assert story["totalCoverage"] == 3 and story["publisherCount"] == 2


def test_the_publisher_list_names_each_outlet_once_by_its_commonest_form():
    """Listing seventeen hostnames would overstate the coverage and read as noise."""
    members = [{"publisher": "Sportskeeda.Com", "publisherKey": "k"},
               {"publisher": "Sportskeeda.Com", "publisherKey": "k"},
               {"publisher": "Sportskeeda", "publisherKey": "k"},
               {"publisher": "BBC News", "publisherKey": "b"}]
    assert ss._display_publishers(members) == ["BBC News", "Sportskeeda.Com"]


def test_one_outlet_casts_one_lean_vote():
    """_distribution votes per outlet. Without identity a fragmented outlet votes many times, which
    skews the distribution the bias claim rests on."""
    members = [{"publisher": "Kfbk.Iheart.Com", "publisherKey": "ih", "leanBucket": "right"},
               {"publisher": "Wjjs.Iheart.Com", "publisherKey": "ih", "leanBucket": "right"},
               {"publisher": "NPR", "publisherKey": "npr", "leanBucket": "left"}]
    assert ss._distribution(members) == {"left": 0.5, "center": 0.0, "right": 0.5}
    assert ss._rated_publishers(members) == 2


def test_publisher_identity_is_on_by_default_and_reversible(monkeypatch):
    monkeypatch.delenv("RWE_STORY_PUBLISHER_IDENTITY", raising=False)
    assert ss.publisher_identity_enabled() is True
    monkeypatch.setenv("RWE_STORY_PUBLISHER_IDENTITY", "0")
    assert ss.publisher_identity_enabled() is False


# --------------------------------------------------------------------------- #
# Registry credibility — a rated outlet whose rater also called it Questionable.
#
# The registry gained a `credibility` column so a lean and a credibility verdict could be recorded
# as the separate facts they are. Before it, eight outlets with a published MBFC lean AND an MBFC
# Questionable / Low Credibility verdict had to have the lean withheld entirely — throwing away a
# true fact to avoid a misleading one. These tests pin what the column bought and what it must not
# cost: the outlet is still COVERAGE, it just does not VOTE.
# --------------------------------------------------------------------------- #
def _gap_with_a_questionable_outlet(st):
    """Two left outlets plus TASS, whose MBFC lean is right-of-centre and whose MBFC credibility is
    Low. Ungated, TASS is the third rated publisher and its vote fills the right bucket."""
    _add(st, "https://cnn.com/g1", "CNN", -1.2, "Border talks collapse after a long night", days=1)
    _add(st, "https://guardian.com/g2", "The Guardian", -1.5, "Border talks collapse after long night",
         days=1)
    _add(st, "https://tass.com/g3", "TASS", 1.0, "Border talks collapse after the long night", days=1)


def test_a_low_credibility_outlet_is_coverage_but_not_a_vote():
    st = store_mod.Store("sqlite://"); _gap_with_a_questionable_outlet(st)
    story = ss.cluster_from_store(st)[0]
    # Still fully counted as coverage — the story really was covered by three outlets.
    assert story["totalCoverage"] == 3 and story["publisherCount"] == 3
    assert "TASS" in story["publishers"]
    assert story["lowCredibilityPublishers"] == ["TASS"]
    # But its lean does not vote: the distribution is the two left outlets only.
    assert story["distribution"] == {"left": 1.0, "center": 0.0, "right": 0.0}


def test_a_low_credibility_outlet_cannot_complete_the_claim_floor():
    """The failure the column exists to prevent. Ungated, TASS is the third rated publisher and the
    story clears MIN_RATED_FOR_BLINDSPOT — so a coverage-gap claim would rest on a state wire the
    rater itself calls Questionable, with nothing in the product showing it."""
    st = store_mod.Store("sqlite://"); _gap_with_a_questionable_outlet(st)
    story = ss.cluster_from_store(st)[0]
    assert story["blindspotSide"] is None            # only two voting publishers, below the floor
    assert story["blindspotWithheld"] is False       # never valid, so nothing was withheld


def test_the_lean_is_still_recorded_on_the_article(monkeypatch):
    """What the column BOUGHT. The old fix was to leave the registry lean blank, which lost the
    rating everywhere. Now the article carries it and only the vote is withheld."""
    st = store_mod.Store("sqlite://"); _gap_with_a_questionable_outlet(st)
    story = ss.cluster_from_store(st)[0]
    row = next(c for c in story["coverage"] if c["publisher"] == "TASS")
    assert row["lean"] is not None and row["leanBucket"] == "right"


def test_the_credibility_gate_is_reversible(monkeypatch):
    """One env var back to the pre-column behaviour, with the leans left in the file. If a verdict
    turns out to be wrong, the fix is a flag and not a re-curation."""
    monkeypatch.setenv("RWE_STORY_CREDIBILITY_GATE", "0")
    st = store_mod.Store("sqlite://"); _gap_with_a_questionable_outlet(st)
    story = ss.cluster_from_store(st)[0]
    assert story["lowCredibilityPublishers"] == []
    assert story["distribution"]["right"] > 0.0


def test_an_ordinary_rated_outlet_is_untouched():
    """The gate must fire on the registry verdict and nothing else. A story of ordinary outlets has
    to behave exactly as it did before the column existed."""
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    for story in ss.cluster_from_store(st):
        assert story["lowCredibilityPublishers"] == []
    wildfire = next(s for s in ss.cluster_from_store(st) if "Wildfire" in s["title"])
    assert wildfire["blindspotSide"] == "center"


def test_an_aggregator_never_enters_clustering():
    """An aggregator republishes other outlets' articles, so counting it as a publisher is a second
    copy of coverage the cluster already holds. Measured in production: Zazoom alone contributed 815
    articles to a six-day window."""
    st = store_mod.Store("sqlite://")
    _add(st, "https://cnn.com/z1", "CNN", -1.2, "Dockworkers strike closes the main port", days=1)
    _add(st, "https://fox.com/z2", "Fox News", 1.5, "Dockworkers strike closes main port", days=1)
    _add(st, "https://zazoom.it/z3", "Zazoom", None, "Dockworkers strike closes the main port now",
         days=1)
    story = ss.cluster_from_store(st)[0]
    assert "Zazoom" not in story["publishers"]
    assert story["publisherCount"] == 2


def test_the_aggregator_gate_is_its_own_switch(monkeypatch):
    """Separate from the wire gate on purpose: they exclude for different reasons — a wire has no
    editorial stance, an aggregator has someone else's — and an operator who wants one back should
    not have to take the other with it."""
    monkeypatch.setenv("RWE_STORY_EXCLUDE_AGGREGATOR", "0")
    st = store_mod.Store("sqlite://")
    _add(st, "https://cnn.com/z1", "CNN", -1.2, "Dockworkers strike closes the main port", days=1)
    _add(st, "https://zazoom.it/z3", "Zazoom", None, "Dockworkers strike closes the main port now",
         days=1)
    story = ss.cluster_from_store(st)[0]
    assert "Zazoom" in story["publishers"]
    assert ss.exclude_wire() is True, "the wire gate is untouched by the aggregator switch"


def test_curated_obituary_feeds_leave_clustering_and_their_paper_does_not():
    """Source curation, end to end — the change the clustering audits actually recommended.

    Five different deceased people cannot form one story if the feed never enters clustering. That
    is the whole mechanism: no threshold moved, no similarity redefined, one identity fact recorded
    about a syndication feed. Contrast every measured alternative — a/p rejected twice at 0%/0%,
    dek tokens rejected at 34.5% dropped coverage.

    The second half is the guard. `obits.oregonlive.com` used to resolve to The Oregonian, so a
    row written one character too wide would take a rated regional newspaper out of the catalog
    with it."""
    st = store_mod.Store("sqlite://")
    # Two obituaries for DIFFERENT people, from the two curated feeds — the exact shape that fused
    # into one story under the rejected dek experiment.
    _add(st, "https://obits.oregonlive.com/us/obituaries/oregonian/name/a-1", "Obits.Oregonlive",
         None, "Janet Peek Obituary 2026 Ellensburg funeral services", days=1)
    _add(st, "https://obits.lehighvalleylive.com/us/obituaries/lehighvalley/name/b-2",
         "Obits.Lehighvalleylive", None,
         "Emil Benz Obituary 2026 Wilkes-Barre funeral services", days=1)
    # The newspapers' own reporting, on one real event.
    _add(st, "https://www.oregonlive.com/politics/p1", "The Oregonian", 0.0,
         "Portland council approves the transit funding plan", days=1)
    _add(st, "https://www.lehighvalleylive.com/news/p2", "The Express-Times", -1.0,
         "Portland council approves transit funding plan after debate", days=1)

    stories = ss.cluster_from_store(st)
    pubs = {p for s in stories for p in s["publishers"]}
    assert not [s for s in stories if "Obituary" in s["title"]], "no obituary reaches a story"
    assert "Obits.Oregonlive" not in pubs and "Obits.Lehighvalleylive" not in pubs
    # …and the mastheads are still clustering, together, on their own reporting.
    assert {"The Oregonian", "The Express-Times"} <= pubs


def test_an_obituary_stored_under_its_masthead_is_still_kept_out_of_clustering():
    """**The case the registry rows alone could not reach, measured in production 2026-08-08.**

    `publisher` is the canonical registry name resolved at INGEST, so an article ingested before
    its feed was curated keeps the old name for ever. 499 of 671 obituary articles are stored as
    `The Oregonian` / `The Express-Times` with an `obits.*` URL — and curating the feeds removed
    172 articles and ZERO of the 14 obituary stories, because those clusters are built entirely
    from the masthead-labelled half.

    So the gate asks the URL too. The second half of this test is what stops that from being a
    catastrophe: the same masthead's real reporting, on its own domain, must still cluster."""
    st = store_mod.Store("sqlite://")
    # Two obituaries for DIFFERENT people, stored the way production stores them: masthead as
    # publisher, obituary feed in the URL.
    _add(st, "https://obits.oregonlive.com/us/obituaries/oregonian/name/a-1", "The Oregonian",
         0.0, "Janet Peek Obituary 2026 Ellensburg funeral services", days=1)
    _add(st, "https://obits.lehighvalleylive.com/us/obituaries/lehighvalley/name/b-2",
         "The Express-Times", -1.0,
         "Emil Benz Obituary 2026 Wilkes-Barre funeral services", days=1)
    # The same two mastheads reporting a real event, on their OWN domains.
    _add(st, "https://www.oregonlive.com/politics/p1", "The Oregonian", 0.0,
         "Portland council approves the transit funding plan", days=1)
    _add(st, "https://www.lehighvalleylive.com/news/p2", "The Express-Times", -1.0,
         "Portland council approves transit funding plan after debate", days=1)

    stories = ss.cluster_from_store(st)
    assert not [s for s in stories if "Obituary" in s["title"]], \
        "an obits.* URL is excluded even when its publisher name is a rated newspaper"
    assert {"The Oregonian", "The Express-Times"} <= {p for s in stories for p in s["publishers"]}, \
        "and the same mastheads still cluster on their own domains — the gate is host-scoped"


def test_the_url_gate_is_off_when_the_wire_switch_is(monkeypatch):
    """One switch, both halves. An operator turning the wire gate off to inspect the raw catalog
    must not be left with a second, invisible filter still running."""
    monkeypatch.setenv("RWE_STORY_EXCLUDE_WIRE", "0")
    st = store_mod.Store("sqlite://")
    _add(st, "https://obits.oregonlive.com/us/obituaries/oregonian/name/a-1", "The Oregonian",
         0.0, "Janet Peek Obituary 2026 Ellensburg funeral services", days=1)
    _add(st, "https://obits.lehighvalleylive.com/us/obituaries/lehighvalley/name/a-2",
         "The Express-Times", -1.0,
         "Janet Peek Obituary 2026 Ellensburg funeral services reported", days=1)
    assert [s for s in ss.cluster_from_store(st) if "Obituary" in s["title"]], \
        "with the gate off the obituaries cluster again — the URL check honours the same switch"
def test_concurrent_cold_readers_build_once_not_once_each(monkeypatch):
    """The defect the profile exposed. `warm_cache` has always been single-flight, so the POLLER's
    eight adapter threads could not stampede each other — but the READER path had no such guard,
    and every request arriving during a rebuild started a rebuild of its own.

    Measured cost of one build at the live catalog size: ~10 s of CPU (examples/perf_profile.py,
    20k articles). Three simultaneous readers therefore cost thirty seconds of work to produce
    three copies of one identical answer, on a box with fewer cores than that has readers — they do
    not merely wait for each other, they compete, and each makes the others slower.

    Asserts the count, not the timing: a timing test would be flaky on a loaded machine and would
    not say WHY it passed."""
    import threading
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    ss.clear_cache()

    started = threading.Event()
    calls = {"n": 0}
    real = ss.build_stories

    def slow(*a, **kw):
        calls["n"] += 1
        started.set()
        # Long enough that every other thread is inside _cached_build before this one returns —
        # which is precisely the window the old code rebuilt in.
        threading.Event().wait(0.35)
        return real(*a, **kw)

    monkeypatch.setattr(ss, "build_stories", slow)
    results, errors = [], []

    def read():
        try:
            results.append(ss.list_stories(st)["total"])
        except Exception as e:                                # pragma: no cover - surfaced below
            errors.append(e)

    threads = [threading.Thread(target=read) for _ in range(5)]
    threads[0].start()
    started.wait(5)                       # let the winner get inside the build
    for t in threads[1:]:
        t.start()
    for t in threads:
        t.join(30)

    assert not errors, errors
    assert len(results) == 5 and len(set(results)) == 1, "all readers must see the same answer"
    assert calls["n"] == 1, f"one cold build for five concurrent readers, got {calls['n']}"


def test_a_failed_build_is_not_cached_and_does_not_wedge_the_key(monkeypatch):
    """The risk the single-flight lock introduces, closed. If a build raises while holding the
    build lock, the lock must release and the next caller must be free to try again — a cached
    exception or a permanently-held lock would turn one transient failure into a dead endpoint."""
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    ss.clear_cache()
    real = ss.build_stories
    state = {"fail": True}

    def flaky(*a, **kw):
        if state["fail"]:
            state["fail"] = False
            raise RuntimeError("transient")
        return real(*a, **kw)

    monkeypatch.setattr(ss, "build_stories", flaky)
    with pytest.raises(RuntimeError):
        ss.list_stories(st)
    assert ss.list_stories(st)["total"] > 0, "the next caller must get a real answer"


def test_the_cache_ttl_default_matches_the_poll_interval(monkeypatch):
    """Not a style assertion — a measured one. A cold build is quadratic in catalog size (0.4 s at
    5k articles, 7.4 s at 20k, 32 s at 40k) and a warm hit is 0.65 ms, so what decides felt speed
    is how often a READER pays the cold path.

    The fingerprint already invalidates on every write and the poller already re-warms after every
    ingest, which left the old 120 s TTL expiring still-correct builds four extra times per 600 s
    poll cycle — four reader-visible multi-second stalls per cycle, buying nothing."""
    monkeypatch.delenv("RWE_STORIES_CACHE_TTL", raising=False)
    assert ss.cache_ttl() == 600.0
    monkeypatch.setenv("RWE_STORIES_CACHE_TTL", "45")
    assert ss.cache_ttl() == 45.0, "an explicit setting still wins"
    monkeypatch.setenv("RWE_STORIES_CACHE_TTL", "0")
    assert ss.cache_ttl() == 0.0, "0 must still disable the cache entirely"


# --------------------------------------------------------------------------- #
# Coalesced warming — the cache-invalidation architecture review
# --------------------------------------------------------------------------- #
def test_a_burst_of_provider_writes_produces_one_rebuild(monkeypatch):
    """The finding this exists for. `MultiSourcePoller` runs a thread per adapter but holds a
    global lock across poll+post-cycle, so adapter warms are SERIALIZED — which means warm_cache's
    single-flight guard, written for the concurrent case, never fired for them. Every provider that
    ingested anything ran its own full rebuild: measured live at 5.6 s each, several per polling
    window, on a two-core box.

    Six providers writing inside one quiet window must now cost ONE build, not six."""
    monkeypatch.setenv("RWE_STORY_WARM_COALESCE", "0.4")
    monkeypatch.setenv("RWE_STORY_WARM_MAX_DELAY", "30")
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    ss.clear_cache(); ss.shutdown_warmer()
    calls = {"n": 0}
    real = ss.build_stories

    def counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)
    monkeypatch.setattr(ss, "build_stories", counting)
    try:
        for i in range(6):                       # six providers finishing in quick succession
            _add(st, f"https://p{i}.example.com/a", f"P{i}", 0.0,
                 f"Senate passes funding bill number {i}", days=1)
            ss.request_warm(st)
        _wait_for(lambda: calls["n"] >= 1, 10)
        import time as _t
        _t.sleep(1.0)                            # let any straggler warm land
        assert calls["n"] == 1, f"one rebuild for six writes, got {calls['n']}"
    finally:
        ss.shutdown_warmer()


def test_continuous_ingestion_cannot_starve_the_warm(monkeypatch):
    """The failure mode quiescence-debouncing introduces, closed. A catalog written to more often
    than the quiet window never goes quiet, so a pure debounce would never warm at all and EVERY
    reader would pay a cold build — strictly worse than the behaviour being replaced. The max-delay
    cap is therefore not a refinement; it is what makes the design admissible."""
    monkeypatch.setenv("RWE_STORY_WARM_COALESCE", "30")     # never reached
    monkeypatch.setenv("RWE_STORY_WARM_MAX_DELAY", "0.5")   # the cap must fire instead
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    ss.clear_cache(); ss.shutdown_warmer()
    calls = {"n": 0}
    real = ss.build_stories

    def counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)
    monkeypatch.setattr(ss, "build_stories", counting)
    stop = False
    try:
        import threading, time as _t
        def hammer():
            i = 0
            while not stop and i < 200:
                ss.request_warm(st)
                _t.sleep(0.02)
                i += 1
        t = threading.Thread(target=hammer, daemon=True); t.start()
        assert _wait_for(lambda: calls["n"] >= 1, 10), "the cap must force a warm under load"
    finally:
        stop = True
        ss.shutdown_warmer()


def test_a_suppressed_warm_cannot_extend_staleness_past_one_refresh(monkeypatch):
    """The safety argument the whole design rests on, made executable — updated for serve-stale.

    The cached entry carries the catalog fingerprint, so the lookup always KNOWS the entry is
    stale; a warm only decides who pays for a build and how soon the staleness ends, never whether
    the bound holds. The failure this pins: a suppressed warm must not leave readers on the old
    build indefinitely — the reader's own stale hit requests the refresh that ends it, so
    visibility never depends on the warmer being alive at all."""
    monkeypatch.setenv("RWE_STORY_WARM_COALESCE", "3600")   # a warm will not fire during this test
    monkeypatch.setenv("RWE_STORY_WARM_MAX_DELAY", "3600")
    _refresh_inline(monkeypatch)
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    ss.clear_cache(); ss.shutdown_warmer()
    try:
        before = next(s for s in ss.list_stories(st)["stories"] if "Senate" in s["title"])
        _add(st, "https://ap.org/late", "AP", 0.1, "Senate passes funding bill in late vote", days=1)
        ss.request_warm(st)                       # queued, and deliberately never serviced
        ss.list_stories(st)                       # the finder: stale serve + the refresh request
        after = next(s for s in ss.list_stories(st)["stories"] if "Senate" in s["title"])
        assert after["totalCoverage"] == before["totalCoverage"] + 1, \
            "a reader must see the write whether or not a warm ever ran"
    finally:
        ss.shutdown_warmer()


def test_coalescing_off_restores_the_inline_warm(monkeypatch):
    """The kill switch has to be a true one — the same code path as before, not a quieter version
    of the new one. At 0 the warm happens on the CALLER's thread and is finished when it returns."""
    monkeypatch.setenv("RWE_STORY_WARM_COALESCE", "0")
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    ss.clear_cache(); ss.shutdown_warmer()
    calls = {"n": 0}
    real = ss.build_stories

    def counting(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)
    monkeypatch.setattr(ss, "build_stories", counting)
    assert ss.request_warm(st) is False, "0 must warm inline, not queue"
    assert calls["n"] == 1, "the build must be done by the time request_warm returns"


def test_the_warmer_survives_a_failing_build(monkeypatch):
    """A warm that cannot be built is a slow next request. It must never be a dead warmer thread,
    because a dead warmer degrades silently into "every reader pays" — the exact failure the warm
    exists to prevent."""
    monkeypatch.setenv("RWE_STORY_WARM_COALESCE", "0.3")
    monkeypatch.setenv("RWE_STORY_WARM_MAX_DELAY", "5")
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    ss.clear_cache(); ss.shutdown_warmer()
    state = {"fail": True, "n": 0}
    real = ss.build_stories

    def flaky(*a, **kw):
        state["n"] += 1
        if state["fail"]:
            state["fail"] = False
            raise RuntimeError("transient")
        return real(*a, **kw)
    monkeypatch.setattr(ss, "build_stories", flaky)
    try:
        ss.request_warm(st)
        assert _wait_for(lambda: state["n"] >= 1, 10)
        ss.clear_cache()
        ss.request_warm(st)
        assert _wait_for(lambda: state["n"] >= 2, 10), "the warmer thread must still be alive"
    finally:
        ss.shutdown_warmer()


def _wait_for(pred, timeout: float) -> bool:
    import time as _t
    deadline = _t.time() + timeout
    while _t.time() < deadline:
        if pred():
            return True
        _t.sleep(0.05)
    return False


# --------------------------------------------------------------------------- #
# _merge_duplicates size bound — 247,718 scores to keep 17 pairs
# --------------------------------------------------------------------------- #
def test_the_size_bound_never_rejects_a_pair_that_could_pass():
    """The property that makes the bound admissible, checked as arithmetic rather than as a
    clustering outcome.

    ``score = w / (Ti + Tj - w)`` is increasing in ``w``, so ``score >= s`` requires
    ``w >= s(Ti+Tj)/(1+s)``; and the intersection is a subset of both profiles, so
    ``w <= min(Ti, Tj)``. When ``min(Ti,Tj)*(1+s) < s*(Ti+Tj)`` no intersection can lift the pair
    over the threshold. Skipping it is therefore EXACT, not a heuristic — which is the only reason
    it is allowed to run inside a recall-sensitive merge pass."""
    import random
    rng = random.Random(5)
    false_skips = 0
    for _ in range(4000):
        A = frozenset(rng.sample(range(60), rng.randint(1, 40)))
        B = frozenset(rng.sample(range(60), rng.randint(1, 40)))
        W = {t: rng.uniform(0.05, 4.0) for t in range(60)}
        ti, tj = sum(W[t] for t in A), sum(W[t] for t in B)
        inter = A & B
        w = sum(W[t] for t in inter)
        den = ti + tj - w
        real = (w / den) if (inter and den) else 0.0
        for s in (0.1, 0.28, 0.33, 0.5, 0.75):
            skipped = (ti if ti < tj else tj) * (1.0 + s) < s * (ti + tj)
            if skipped and real >= s:
                false_skips += 1
    assert false_skips == 0, f"{false_skips} pairs wrongly rejected — the bound is not exact"


def test_merging_still_joins_the_same_event_in_different_words(monkeypatch):
    """The behaviour the merge pass exists for, pinned so the size bound cannot quietly break it.

    "Mass shooting reported at Seattle Center" and "…gunfire erupts near Seattle" share ONE headline
    token against MIN_SHARED_TOKENS=3, so the clusterer can never pair them at any threshold. The
    second pass over headline+description is the only route. These two profiles weigh 9.25 and 8.15,
    well inside the ~3x ratio the size bound admits, and score 0.387 against a 0.33 threshold.

    The merge similarity is set EXPLICITLY here: conftest leaves it at 0, which disables the merge
    pass outright. A first version of this test asserted a merge without setting it, got two stories
    instead of one, and would have looked exactly like the size bound breaking recall — it was the
    fixture running a code path that was switched off."""
    monkeypatch.setenv("RWE_STORY_MERGE_SIM", "0.33")
    monkeypatch.setenv("RWE_STORY_MERGE_MAX_GAP", "48")
    monkeypatch.setenv("RWE_STORY_MERGE_MAX_SIZE", "130")
    st = store_mod.Store("sqlite://")
    ss.clear_cache()
    base = NOW - timedelta(hours=6)
    for url, pub, title, desc in [
        ("https://a.com/1", "AP", "Mass shooting reported at Seattle Center",
         "Police responded to gunfire at Seattle Center on Saturday evening, several wounded."),
        ("https://b.com/1", "Reuters", "Mass shooting reported at Seattle Center",
         "Police responded to gunfire at Seattle Center on Saturday evening, several wounded."),
        ("https://c.com/1", "NPR", "Gunfire erupts near Seattle venue leaving several wounded",
         "Police responded to gunfire at Seattle Center on Saturday evening, several wounded."),
        ("https://d.com/1", "CNN", "Gunfire erupts near Seattle venue leaving several wounded",
         "Police responded to gunfire at Seattle Center on Saturday evening, several wounded."),
    ]:
        st.upsert_feed_article(canonical_url=url, url=url, publisher=pub, source_publisher=None,
                               title=title, description=desc, body=None,
                               published_at=base.isoformat(), source_feed="t",
                               scored={"lean": 0.0, "category": "world"})
    stories = ss.list_stories(st)["stories"]
    seattle = [s for s in stories if "Seattle" in s["title"] or "Gunfire" in s["title"]]
    assert seattle, stories
    assert max(s["totalCoverage"] for s in seattle) == 4, \
        "the two wordings must merge into one story of four articles"


def test_inline_warm_logs_story_cache_warm(monkeypatch):
    """The kill switch must not also kill the instrumentation.

    ``story_cache_warm`` is emitted by ``_Warmer._run``. Production runs with
    ``RWE_STORY_WARM_COALESCE=0``, which takes the inline branch of ``request_warm`` — and that
    branch used to log nothing at all. The single most expensive recurring operation in the process
    was therefore invisible in production for as long as the switch was off, and finding its cost
    again took a full pass of host-level forensics (iostat, PSI, /proc thread sampling, CPU credits)
    to re-derive a number this one line prints.

    The event is the contract. Both branches emit it or neither is trustworthy."""
    monkeypatch.setenv("RWE_STORY_WARM_COALESCE", "0")       # the production setting
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    ss.clear_cache(); ss.shutdown_warmer()
    events = []
    try:
        queued = ss.request_warm(st, log=lambda event, **f: events.append((event, f)))
    finally:
        ss.shutdown_warmer()

    assert queued is False, "coalescing off must warm inline, not queue"
    warms = [f for event, f in events if event == "story_cache_warm"]
    assert len(warms) == 1, f"the inline warm must log exactly one story_cache_warm, got {events}"
    assert warms[0]["stories"] >= 1
    assert warms[0]["durationMs"] >= 0.0, "duration is the field the cost was measured with"
    assert warms[0]["coalesced"] == 1, "inline absorbs exactly one write notification"


# --------------------------------------------------------------------------- #
# The build subprocess (P0-2′): clustering runs off the serving process's GIL.
# Real forkserver spawns below — slow-ish (~2 s once per test) and worth it: the equality and
# stable-id claims are exactly the ones a fake would assume away.
# --------------------------------------------------------------------------- #
def _file_store(tmp_path):
    st = store_mod.Store(f"sqlite:///{tmp_path}/stories.db")
    _senate_and_wildfire(st)
    return st


def _counter(name):
    import obs_metrics
    return obs_metrics.snapshot().get("counters", {}).get(name, 0)


def test_the_offloaded_build_matches_the_inline_build_exactly(tmp_path):
    """The child is the same `_fetch` + `build_stories` over the same file — same stories, same
    order, and (because identity is applied in the parent either way) the same stable ids."""
    st = _file_store(tmp_path)
    before = _counter("story_build_subprocess_total")
    assert ss.warm_cache(st) == 2                      # eligible: file-backed + enabled by default
    offloaded = ss.list_stories(st)["stories"]
    assert _counter("story_build_subprocess_total") == before + 1, "the build never left the process"

    os.environ["RWE_STORY_BUILD_SUBPROCESS"] = "0"
    try:
        ss.clear_cache()
        inline = ss.list_stories(st)["stories"]
    finally:
        os.environ.pop("RWE_STORY_BUILD_SUBPROCESS", None)
    assert [s["id"] for s in offloaded] == [s["id"] for s in inline]
    assert [s["title"] for s in offloaded] == [s["title"] for s in inline]
    assert [s["totalCoverage"] for s in offloaded] == [s["totalCoverage"] for s in inline]


def test_stable_ids_survive_the_offloaded_path(tmp_path):
    """`stabilize_ids` runs in the PARENT over the child's output; a story must keep its id across
    an offloaded rebuild that grew its coverage — the same promise the inline path makes."""
    st = _file_store(tmp_path)
    assert ss.warm_cache(st) == 2
    sid = next(s for s in ss.list_stories(st)["stories"] if "Senate" in s["title"])["id"]
    # An EARLIER member on purpose (days=3): it becomes the cluster's earliest article, so the RAW
    # derived id changes — only the parent's stabilize pass can keep the served id constant. A
    # later member would leave the raw id equal to the stable one and prove nothing (a mutant that
    # skipped identity on the offloaded path survived exactly that version of this test).
    _add(st, "https://ap.org/sub1", "AP", 0.1, "Senate passes funding bill in late vote", days=3)
    assert ss.warm_cache(st) == 2                      # rebuilt (offloaded), same two stories
    grown = next(s for s in ss.list_stories(st)["stories"] if "Senate" in s["title"])
    assert grown["id"] == sid and grown["totalCoverage"] == 4


@pytest.mark.parametrize("url", ["sqlite://", "sqlite:///:memory:"])
def test_an_in_memory_store_never_reaches_the_pool(monkeypatch, url):
    """A child cannot see the parent's in-memory database; offloading would silently build from an
    EMPTY catalog. Eligibility must refuse before the pool is ever touched."""
    st = store_mod.Store(url); _senate_and_wildfire(st)
    monkeypatch.setattr(ss, "_offloaded_build",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("pool touched")))
    assert ss.list_stories(st)["total"] == 2


def test_the_subprocess_kill_switch_keeps_the_build_inline(tmp_path, monkeypatch):
    monkeypatch.setenv("RWE_STORY_BUILD_SUBPROCESS", "0")
    st = _file_store(tmp_path)
    monkeypatch.setattr(ss, "_offloaded_build",
                        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("pool touched")))
    assert ss.list_stories(st)["total"] == 2


def test_a_broken_pool_falls_back_inline_and_resets_for_the_next_build(tmp_path, monkeypatch):
    """A worker OOM-killed mid-build must cost the caller nothing but the inline build they would
    have run anyway — and must tear the pool down so the NEXT build gets a fresh one."""
    st = _file_store(tmp_path)

    class _BrokenPool:
        def submit(self, *a, **kw):
            raise RuntimeError("worker died")

        def shutdown(self, *a, **kw):
            pass
    # Install the broken pool as the REAL global, not behind a monkeypatched `_build_pool`: the
    # mutant this kills leaves the installed pool in place after a failure, and a stub accessor
    # would hide exactly that state from the assertion.
    with ss._BUILD_POOL_LOCK:
        ss._BUILD_POOL = _BrokenPool()

    failed_before = _counter("story_build_subprocess_failed_total")
    assert ss.list_stories(st)["total"] == 2, "the fallback did not serve"
    assert _counter("story_build_subprocess_failed_total") == failed_before + 1
    assert ss._BUILD_POOL is None, "a broken pool must not be left installed"


def test_shutdown_build_pool_is_idempotent(tmp_path):
    st = _file_store(tmp_path)
    assert ss.warm_cache(st) == 2                      # boots the real pool
    ss.shutdown_build_pool()
    assert ss._BUILD_POOL is None
    ss.shutdown_build_pool()                           # second call: nothing to stop, no error
    assert ss._BUILD_POOL is None


# --------------------------------------------------------------------------- #
# default_story_view (publisher-page outage P0): cache when servable, read-only build when not.
# --------------------------------------------------------------------------- #
def test_default_view_serves_the_warmed_build_without_building(monkeypatch):
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    assert ss.warm_cache(st) == 2
    calls = {"n": 0}
    real = ss.build_stories
    monkeypatch.setattr(ss, "build_stories",
                        lambda *a, **kw: (calls.__setitem__("n", calls["n"] + 1), real(*a, **kw))[1])
    assert len(ss.default_story_view(st)) == 2
    assert calls["n"] == 0


def test_default_view_expired_entry_serves_empty_and_kicks_not_builds(monkeypatch):
    """Past the TTL the peek answers None — and the request path must NOT rebuild inline (the
    pre-2026-08-02 contract this test used to pin: measured at ~24 s per repetition on request
    threads at 51.8k articles). It serves ``[]``, kicks ONE refresh, and heals on the next call.
    The TTL's bound is still honoured: nothing older than the TTL is ever served."""
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    assert ss.warm_cache(st) == 2
    with ss._CACHE_LOCK:
        entries = ss._CACHE[st]
        for k, (t, fp, stories) in list(entries.items()):
            entries[k] = (t - ss.cache_ttl() - 1.0, fp, stories)
    spawned = []
    monkeypatch.setattr(ss, "_spawn_refresh", lambda s, k: spawned.append(k))
    calls = {"n": 0}
    real_build = ss.build_stories
    monkeypatch.setattr(ss, "build_stories",
                        lambda *a, **kw: (calls.__setitem__("n", calls["n"] + 1), real_build(*a, **kw))[1])
    assert ss.default_story_view(st) == [], "an expired entry must not be served"
    assert calls["n"] == 0, "a request thread rebuilt an expired entry inline"
    assert spawned == [ss._DEFAULT_LOGICAL]
    ss._run_refresh(st, spawned[0])
    assert len(ss.default_story_view(st)) == 2      # healed, within the TTL bound


def test_default_view_respects_the_serve_stale_kill_switch(monkeypatch):
    """Switch off, stale entry: the peek must answer None (not serve stale against the operator's
    setting). The request path then serves ``[]`` and kicks the refresh — the switch forbids
    serving STALE data, and neither an empty list nor a background rebuild is that."""
    monkeypatch.setenv("RWE_STORIES_SERVE_STALE", "0")
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    assert ss.warm_cache(st) == 2
    _add(st, "https://ap.org/kv", "AP", 0.1, "Harbour pilots ratify their contract", days=1)
    _add(st, "https://re.example/kv", "Reuters", 0.0, "Harbour pilots ratify contract", days=1)
    calls = {"n": 0}
    real = ss.build_stories
    monkeypatch.setattr(ss, "build_stories",
                        lambda *a, **kw: (calls.__setitem__("n", calls["n"] + 1), real(*a, **kw))[1])
    spawned = []
    monkeypatch.setattr(ss, "_spawn_refresh", lambda s, k: spawned.append(k))
    assert ss.default_story_view(st) == []          # stale data withheld, nothing built inline
    assert calls["n"] == 0 and spawned == [ss._DEFAULT_LOGICAL]
    ss._run_refresh(st, spawned[0])
    assert len(ss.default_story_view(st)) == 3      # fresh build, new story included


# --------------------------------------------------------------------------- #
# Filtered views render SERVED identity (P0, 2026-08-02). `get_story` resolves ids against the
# stabilized default view only, so a filtered list serving raw `_story_id` output is a list of
# dead links for every cluster whose anchor ever churned — measured in production at 110 of 1,257
# rendered topic-filtered links (8.8%), with one 93-member cluster's urls voting 93/93 for a
# ledger id that served 200 while its rendered raw id served 404. Filtered builds therefore READ
# the ledger (stabilize_ids_readonly) and still never WRITE it.
# --------------------------------------------------------------------------- #


def _disjoint_cluster(st, k, topic, publishers=("NPR", "Fox News", "BBC News")):
    """A three-publisher cluster whose vocabulary is shared with no other cluster — any common
    filler and the duplicate-merge pass glues unrelated clusters into one mega-story."""
    title = f"Alphax{k} bravox{k} charliex{k} deltax{k} echox{k} foxtrotx{k}"
    desc = f"Golfx{k} hotelx{k} indiax{k} julietx{k} kilox{k} limax{k} mikex{k}."
    for j, pub in enumerate(publishers):
        _add(st, f"https://p{j}.example/{k}", pub, 0.0, title, category=topic, days=1 + j, desc=desc)
    return title, desc


def _churn_anchor(st, k, topic, title, desc):
    """Backfill an OLDER article into cluster k — the documented anchor displacement that changes
    the raw `_story_id` (the 5.1%/day mechanism the ledger exists to absorb)."""
    _add(st, f"https://backfill.example/{k}", "CNN", 0.0, title, category=topic, days=6, desc=desc)


def test_topic_filtered_lists_render_ids_the_detail_endpoint_resolves():
    """The production failure as an assertion: churn an anchor, then every id a topic-filtered
    list renders must still resolve through get_story — and must BE the default view's id."""
    st = store_mod.Store("sqlite://")
    seeds = {k: _disjoint_cluster(st, k, ["Politics", "Climate"][k % 2]) for k in range(4)}
    before = {s["id"] for s in ss.list_stories(st, limit=50)["stories"]}   # ledger written
    assert len(before) == 4
    for k in (0, 1):
        _churn_anchor(st, k, ["Politics", "Climate"][k % 2], *seeds[k])
    ss.clear_cache()
    default_ids = {s["id"] for s in ss.list_stories(st, limit=50)["stories"]}
    assert default_ids == before, "control: the ledger absorbs the churn on the default view"
    for topic in ("Politics", "Climate"):
        for s in ss.list_stories(st, topic=topic, limit=50)["stories"]:
            assert s["id"] in default_ids, \
                f"topic list rendered {s['id']}, which the default view does not serve"
            assert ss.get_story(st, s["id"]) is not None, \
                f"topic list rendered a dead link: {s['id']}"


def test_date_range_builds_never_write_the_identity_ledger():
    """The write ban stands on the one remaining per-key path: an explicit date range (the debug
    surface — no UI sends one) builds its own clustering with READ-ONLY stabilization. The ledger
    must be byte-identical across such a build even when its raw ids disagree with it. (Topic
    queries no longer have a per-key build to test: they post-filter the default universe, whose
    build rightly owns the write.)"""
    st = store_mod.Store("sqlite://")
    title, desc = _disjoint_cluster(st, 0, "Politics")
    ss.list_stories(st, limit=50)                       # ledger written by the default build
    _churn_anchor(st, 0, "Politics", title, desc)       # raw id now diverges from the ledger
    ss.clear_cache()
    ledger_before = st.story_member_ids()
    wide = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    got = ss.list_stories(st, topic="Politics", date_from=wide, limit=50)["stories"]
    assert got, "the date build must still produce the cluster"
    assert st.story_member_ids() == ledger_before, \
        "a date-range build wrote the identity ledger — partial views must never own identity"


def test_topic_queries_share_the_default_universe_not_a_second_clustering():
    """The residual dead-link class (2026-08-02): clustering is corpus-relative, so a topic-only
    build could compose stories the default build splits differently — a production specimen's
    ledger votes fractured 4/4/2 (best share 0.33 < 0.5), unresolvable by any id mapping. Topic
    queries therefore consume the ONE default build: no second cache entry, and every rendered id
    is a default-view id by construction."""
    st = store_mod.Store("sqlite://")
    for k in range(4):
        _disjoint_cluster(st, k, ["Politics", "Climate"][k % 2])
    default_ids = {s["id"] for s in ss.list_stories(st, limit=50)["stories"]}
    for topic in ("Politics", "Climate"):
        listed = ss.list_stories(st, topic=topic, limit=50)["stories"]
        assert listed and all(s["id"] in default_ids for s in listed)
        assert all(ss.get_story(st, s["id"]) is not None for s in listed)
    with ss._CACHE_LOCK:
        assert len(ss._CACHE.get(st, {})) == 1, \
            "a topic query built a second clustering instead of filtering the default universe"


def test_topic_filter_matches_the_dominant_topic_case_insensitively():
    st = store_mod.Store("sqlite://")
    _disjoint_cluster(st, 0, "Politics")
    _disjoint_cluster(st, 1, "Climate")
    politics = ss.list_stories(st, topic="politics", limit=50)["stories"]
    assert [s["topic"] for s in politics] == ["Politics"]
    assert ss.list_stories(st, topic="Sports", limit=50)["stories"] == []


def test_explicit_date_ranges_still_get_their_own_build():
    """The one filter whose rows can lie outside the default window keeps its per-key build —
    narrowing it to a post-filter would silently empty historical queries."""
    st = store_mod.Store("sqlite://")
    _disjoint_cluster(st, 0, "Politics")
    ss.list_stories(st, limit=50)
    wide = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    ss.list_stories(st, date_from=wide, limit=50)
    with ss._CACHE_LOCK:
        assert len(ss._CACHE.get(st, {})) == 2, "a date range must key its own build"


def test_empty_string_topic_is_the_default_view_not_an_unstabilized_twin():
    """`?topic=` reached the row fetch as "no filter" but the cache key and identity gate as
    "filtered" — building a full-catalog twin of the default view under ('', …) with raw ids
    (the production probe's 93-member case came from exactly this key). Normalized: '' and None
    are one build, one cache entry, one set of served ids."""
    st = store_mod.Store("sqlite://")
    title, desc = _disjoint_cluster(st, 0, "Politics")
    ss.list_stories(st, limit=50)
    _churn_anchor(st, 0, "Politics", title, desc)
    ss.clear_cache()
    default_ids = {s["id"] for s in ss.list_stories(st, limit=50)["stories"]}
    twin_ids = {s["id"] for s in ss.list_stories(st, topic="", date_from="", date_to="",
                                                 limit=50)["stories"]}
    assert twin_ids == default_ids
    assert all(ss.get_story(st, i) is not None for i in twin_ids)
    with ss._CACHE_LOCK:
        assert len(ss._CACHE.get(st, {})) == 1, \
            "'' must share the default view's cache entry, not build a twin under ('', …)"


# --------------------------------------------------------------------------- #
# Boot window: a request-path peek miss must never cluster inline.
# The post-deploy probe of 2026-08-02 measured the alternative: four inline clusterings on
# request threads at ~24 s each in the first two minutes after a restart, repeated per consumer
# because the inline build is uncached, two at once starving the whole box.
# --------------------------------------------------------------------------- #
def test_request_path_peek_miss_serves_empty_and_kicks_one_refresh(monkeypatch):
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    spawned = []
    monkeypatch.setattr(ss, "_spawn_refresh", lambda store_, logical: spawned.append(logical))
    calls = {"n": 0}
    real = ss.build_stories
    monkeypatch.setattr(ss, "build_stories",
                        lambda *a, **kw: (calls.__setitem__("n", calls["n"] + 1), real(*a, **kw))[1])

    assert ss.default_story_view(st) == []          # cold: nothing to serve…
    assert ss.default_story_view(st) == []          # …and a second consumer coalesces
    assert calls["n"] == 0, "a request thread clustered inline"
    assert spawned == [ss._DEFAULT_LOGICAL], "exactly one background refresh must be kicked"

    ss._run_refresh(st, spawned[0])                 # the kicked refresh heals the window
    assert len(ss.default_story_view(st)) == 2      # …for every consumer after it
    assert spawned == [ss._DEFAULT_LOGICAL], "a peek hit must request nothing"


def test_the_analyzer_inline_path_still_builds_read_only(monkeypatch):
    """/api/analyze's zero-write contract: data on a cold cache, no cache entry left behind, and
    no background spawn (a kick would eventually write the cache + ledger from an analyze
    request — test_analysis_writes_nothing_anywhere hashes the whole DB file to forbid that)."""
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    spawned = []
    monkeypatch.setattr(ss, "_spawn_refresh", lambda store_, logical: spawned.append(logical))

    assert len(ss.default_story_view(st, build_inline=True)) == 2
    assert spawned == [], "the zero-write path spawned a cache-writing refresh"
    with ss._CACHE_LOCK:
        assert not ss._CACHE.get(st), "the inline build must stay uncached"


def test_cache_disabled_keeps_the_inline_build_for_everyone(monkeypatch):
    """RWE_STORIES_CACHE_TTL=0 opts out of the caching layer entirely: there is no cache to kick
    a refresh into, so the pre-existing uncached inline behaviour is the only correct one."""
    monkeypatch.setenv("RWE_STORIES_CACHE_TTL", "0")
    st = store_mod.Store("sqlite://"); _senate_and_wildfire(st)
    spawned = []
    monkeypatch.setattr(ss, "_spawn_refresh", lambda store_, logical: spawned.append(logical))
    assert len(ss.default_story_view(st)) == 2
    assert spawned == []


# --------------------------------------------------------------------------- #
# X4 geo-veto (docs/STORY_ENTITY_EVIDENCE_PLAN.md) — entity evidence at edge time. OFF by
# default and NOT a production setting; these tests pin the semantics the audit measures:
# fail-open on missing data, pair mode severs located disagreement, growth mode spares
# formation and two-country events, and the default path stays byte-identical.
# --------------------------------------------------------------------------- #
def _quake(st, n_us=2, n_co=2, located=True):
    """Same headline everywhere — lexically ONE cluster — with the located split carrying the
    only evidence that it is two events. Reuses `_locate` (defined above, ISO codes)."""
    title = "Massive earthquake strikes the region overnight rescue"
    for i in range(n_us):
        cu = f"https://us{i}.example.com/quake"
        _add(st, cu, f"US Outlet {i}", 0.0, title)
        if located:
            _locate(st, cu, "US")
    for i in range(n_co):
        cu = f"https://co{i}.example.com/quake"
        _add(st, cu, f"CO Outlet {i}", 0.0, title)
        if located:
            _locate(st, cu, "CO")


def test_geo_veto_env_parsing(monkeypatch):
    for raw, want in [("", ""), ("pair", "pair"), (" GROWTH ", "growth"), ("garbage", ""),
                      ("1", ""), ("off", "")]:
        monkeypatch.setenv("RWE_CLUSTER_GEO_VETO", raw)
        assert ss.geo_veto() == want, f"{raw!r} must resolve to {want!r}, never a guess"
    monkeypatch.delenv("RWE_CLUSTER_GEO_VETO", raising=False)
    assert ss.geo_veto() == ""


def test_pair_veto_severs_located_disagreement():
    st = store_mod.Store("sqlite://")
    _quake(st)
    rows = ss._fetch(st)
    assert len(ss.build_stories(rows)) == 1, "lexically this is one cluster"
    split = ss.build_stories(rows, veto="pair")
    assert len(split) == 2, "both-located disjoint pairs are severed"
    assert sorted(s["totalCoverage"] for s in split) == [2, 2]


def test_pair_veto_fails_open_on_unlocated_pairs():
    """Absence of evidence is never disagreement — and that has a known consequence, stated
    rather than hidden: an UNLOCATED bridge still chains located disagreement together. The
    aggregate cost of that limitation is exactly what audit runs C/D measure."""
    st = store_mod.Store("sqlite://")
    _quake(st, located=False)
    assert len(ss.build_stories(ss._fetch(st), veto="pair")) == 1

    st2 = store_mod.Store("sqlite://")
    _quake(st2, n_us=1, n_co=1)                        # one located each side...
    title = "Massive earthquake strikes the region overnight rescue"
    _add(st2, "https://bridge.example.com/quake", "Bridge Outlet", 0.0, title)   # ...unlocated
    stories = ss.build_stories(ss._fetch(st2), veto="pair")
    assert len(stories) == 1 and stories[0]["totalCoverage"] == 3, \
        "US–CO edge vetoed, but both chain through the unlocated bridge — the documented limit"


def test_growth_veto_spares_formation():
    st = store_mod.Store("sqlite://")
    _quake(st, n_us=1, n_co=1)
    stories = ss.build_stories(ss._fetch(st), veto="growth")
    assert len(stories) == 1 and stories[0]["totalCoverage"] == 2, \
        "two singletons always form — a singleton cannot carry two located members, so the " \
        "evidence floor leaves formation ungated with no size rule at all"


def test_growth_veto_fires_on_one_sided_corroboration():
    """The V-growth-2 rule: disjoint consensuses plus EITHER side's winning vote corroborated.
    3 US-located vs 2 CO-located: both corroborated, vetoed. 3 US-located vs ONE CO-located: the
    corroborated receiver rejects the thinly-located dissenter too — a false merge is the
    catastrophic direction, a rejected dissenter is one article and the 1% coverage bar measures
    the aggregate."""
    st = store_mod.Store("sqlite://")
    _quake(st, n_us=3, n_co=2)
    stories = ss.build_stories(ss._fetch(st), veto="growth")
    assert len(stories) == 2 and sorted(s["totalCoverage"] for s in stories) == [2, 3]
    assert len(ss.build_stories(ss._fetch(st))) == 1, "off: one 5-article story"

    st2 = store_mod.Store("sqlite://")
    _quake(st2, n_us=3, n_co=1)
    stories = ss.build_stories(ss._fetch(st2), veto="growth")
    assert len(stories) == 1 and stories[0]["totalCoverage"] == 3, \
        "the corroborated side (3 US votes) refuses the located disagreeing singleton"


def test_two_samples_of_one_never_veto_each_other():
    """The Ronaldo protection: a story whose located testimony is one member per side must not be
    split by it. Eight same-title articles, ONE located US, ONE located CO, six unlocated — no
    winning vote ever reaches GEO_MIN_CONSENSUS, so every merge fails open and the story holds."""
    st = store_mod.Store("sqlite://")
    title = "Massive earthquake strikes the region overnight rescue"
    _add(st, "https://us0.example.com/quake", "US Outlet", 0.0, title)
    _locate(st, "https://us0.example.com/quake", "US")
    _add(st, "https://co0.example.com/quake", "CO Outlet", 0.0, title)
    _locate(st, "https://co0.example.com/quake", "CO")
    for i in range(6):
        _add(st, f"https://n{i}.example.com/quake", f"Neutral {i}", 0.0, title)
    stories = ss.build_stories(ss._fetch(st), veto="growth")
    assert len(stories) == 1 and stories[0]["totalCoverage"] == 8


def test_growth_veto_blocks_the_seed_fusion_hole():
    """The run-C Colombia+Indonesia receipt: under the old size exemption two 2-member seeds of
    DIFFERENT events fused ungated on template vocabulary, and the poisoned {CO, ID} tie then
    overlapped everything. With the evidence floor a 2v2 merge carrying 2+2 located disagreement
    is gated and refused."""
    st = store_mod.Store("sqlite://")
    _quake(st, n_us=2, n_co=2)
    stories = ss.build_stories(ss._fetch(st), veto="growth")
    assert len(stories) == 2 and sorted(s["totalCoverage"] for s in stories) == [2, 2]


def test_growth_veto_admits_overlapping_consensus():
    """The two-country guard: a member located in BOTH countries of a genuine cross-border event
    overlaps the cluster consensus and is admitted — the _geo_coherence mechanism at edge time."""
    st = store_mod.Store("sqlite://")
    title = "Grand prix chaos as race stewards review the crash"
    for i in range(3):
        cu = f"https://hu{i}.example.com/gp"
        _add(st, cu, f"HU Outlet {i}", 0.0, title)
        _locate(st, cu, "HU")
    cu = "https://both.example.com/gp"
    _add(st, cu, "Cross Outlet", 0.0, title)
    _locate(st, cu, "HU", "GB")
    stories = ss.build_stories(ss._fetch(st), veto="growth")
    assert len(stories) == 1 and stories[0]["totalCoverage"] == 4, \
        "{HU,GB} overlaps consensus {HU} — a real two-country member is not a disagreement"


def test_veto_off_and_default_are_byte_identical(monkeypatch):
    monkeypatch.delenv("RWE_CLUSTER_GEO_VETO", raising=False)
    st = store_mod.Store("sqlite://")
    _quake(st)
    rows = ss._fetch(st)
    a = ss.build_stories(rows)
    b = ss.build_stories(rows, veto="")
    c = ss.build_stories(rows, veto="nonsense")        # junk narrows to off, never to a guess
    assert [s["id"] for s in a] == [s["id"] for s in b] == [s["id"] for s in c]
    assert [len(s["coverage"]) for s in a] == [len(s["coverage"]) for s in b]


def test_veto_stats_count_the_decisions():
    st = store_mod.Store("sqlite://")
    _quake(st)
    stats: dict = {}
    ss.build_stories(ss._fetch(st), veto="pair", veto_stats=stats)
    assert stats.get("pairChecked", 0) >= stats.get("pairBothLocated", 0) >= stats.get("pairVetoed", 0)
    assert stats.get("pairVetoed", 0) >= 1, "the US-CO edges were vetoed and must be counted"
    ss.build_stories(ss._fetch(st), veto="pair")       # stats=None: no counting, no error


def test_geo_closures_off_is_none_none():
    assert ss._geo_closures([], "") == (None, None)
    assert ss._geo_closures([{"eventCountries": ["US"]}], "") == (None, None)


def test_located_consensus_over_member_dicts():
    """The dup-merge pass's counterpart of the closure consensus — same vote semantics."""
    mk = lambda *cs: {"eventCountries": list(cs)}   # noqa: E731
    assert ss._located_consensus([]) == (frozenset(), 0)
    assert ss._located_consensus([mk(), mk()]) == (frozenset(), 0)
    assert ss._located_consensus([mk("US"), mk("US"), mk("CO")]) == (frozenset({"US"}), 2)
    assert ss._located_consensus([mk("US"), mk("CO")]) == (frozenset({"US", "CO"}), 1), \
        "a tie keeps both, and its winning vote is 1 — uncorroborated, so it cannot veto"


def test_dup_merge_pass_respects_the_veto():
    """The 3-pooled-located crack: one corroborated side (2 located) plus one sample of one is 3
    pooled — under MIN_LOCATED_FOR_TRUST, where the coherence guard is silent — and the
    profile-similar pair the veto severed must not be quietly rejoined there."""
    h = "Powerful magnitude earthquake strikes coastal region tsunami warning issued"
    groups = [
        [{"eventCountries": ["US"], "publisher": "A", "headline": h},
         {"eventCountries": ["US"], "publisher": "B", "headline": h}],
        [{"eventCountries": ["CO"], "publisher": "C", "headline": h},
         {"eventCountries": [], "publisher": "D", "headline": h}],
    ]
    kept = ss._merge_duplicates([list(g) for g in groups], min_sim=0.0001, max_gap_hours=1e9,
                                max_size=99, veto="growth")
    assert len(kept) == 2, "corroborated US vs located CO: the join is refused"
    joined = ss._merge_duplicates([list(g) for g in groups], min_sim=0.0001, max_gap_hours=1e9,
                                  max_size=99)
    assert len(joined) == 1, "without the veto the same pair joins — the gate is the difference"


# --------------------------------------------------------------------------- #
# X5b entity-corroborated merge recall (docs/STORY_ENTITY_EVIDENCE_PLAN.md) — dormant twice
# over (env default 0 AND the entity mapping must be injected). These pin the rule the phase-0
# measurements designed: two shared corroborated non-noise names propose, one does not (the
# USGS lesson), the X4 geo-consensus veto outranks entities (the Colombia+Indonesia
# protection), complete linkage prevents chains, and the default path is byte-identical.
# --------------------------------------------------------------------------- #
def _ents_for(st_rows_urls, mapping):
    """Entity mapping keyed the way member dicts are (id = canonical url)."""
    return {u: v for u, v in mapping.items()}


def _seattle(st):
    """The measured recall case: lexically unreachable (ONE shared token), entity-identical."""
    for pub in ["A", "B"]:
        _add(st, f"https://{pub.lower()}.example.com/shooting", pub, 0.0,
             "Mass shooting reported downtown at Seattle Center venue")
    for pub in ["C", "D"]:
        _add(st, f"https://{pub.lower()}.example.com/gunfire", pub, 0.0,
             "Gunfire erupts near busy plaza as police respond quickly")


def test_entity_merge_joins_the_lexically_unreachable_pair():
    st = store_mod.Store("sqlite://")
    _seattle(st)
    ents = {}
    for pub in ["a", "b", "c", "d"]:
        ents[f"https://{pub}.example.com/" + ("shooting" if pub in "ab" else "gunfire")] = \
            {"person": ["jane suspect"], "org": ["seattle center"]}
    rows = ss._fetch(st)
    plain = ss.build_stories(rows)
    assert len(plain) == 2, "lexically these never merge at any threshold"
    merged = ss.build_stories(rows, entity_merge=2, entities=ents)
    assert len(merged) == 1 and merged[0]["totalCoverage"] == 4, \
        "two corroborated shared names join what no lexical profile can reach"


def test_one_shared_name_proposes_nothing():
    """The USGS lesson: a single shared corroborated name can be a type-level responder agency,
    so it must not even generate a candidate."""
    st = store_mod.Store("sqlite://")
    _seattle(st)
    ents = {}
    for pub in ["a", "b", "c", "d"]:
        key = f"https://{pub}.example.com/" + ("shooting" if pub in "ab" else "gunfire")
        ents[key] = {"org": ["u s geological"],
                     "person": [f"local person {pub in 'ab' and 'x' or 'y'}"]}
    rows = ss._fetch(st)
    stats: dict = {}
    merged = ss.build_stories(rows, entity_merge=2, entities=ents, veto_stats=stats)
    assert len(merged) == 2
    assert stats.get("entityMergeCandidates", 0) == 0, "one shared name is not a candidate"


def test_geo_consensus_outranks_entities():
    """The Colombia+Indonesia protection: identical corroborated entities, disjoint corroborated
    located consensuses — the join is refused whatever the entities say."""
    st = store_mod.Store("sqlite://")
    _seattle(st)
    for pub in ["a", "b"]:
        _locate(st, f"https://{pub}.example.com/shooting", "CO")
    for pub in ["c", "d"]:
        _locate(st, f"https://{pub}.example.com/gunfire", "ID")
    ents = {}
    for pub in ["a", "b", "c", "d"]:
        key = f"https://{pub}.example.com/" + ("shooting" if pub in "ab" else "gunfire")
        ents[key] = {"person": ["shared name one"], "org": ["shared org two"]}
    rows = ss._fetch(st)
    stats: dict = {}
    merged = ss.build_stories(rows, entity_merge=2, entities=ents, veto_stats=stats)
    assert len(merged) == 2, "geography outranks entities"
    assert stats.get("entityMergeGeoVetoed", 0) >= 1


def test_entity_consensus_requires_corroboration_and_filters_noise():
    members = [{"id": "u1"}, {"id": "u2"}, {"id": "u3"}]
    ents = {"u1": {"person": ["jane doe"], "org": ["reuters", "acme corp"]},
            "u2": {"person": ["jane doe"], "org": ["reuters", "acme corp"]},
            "u3": {"person": ["someone else"], "org": []}}
    cons = ss._story_entity_consensus(members, ents)
    assert cons == frozenset({"jane doe", "acme corp"}), \
        "reuters is identity-noise however many members carry it; singletons are not consensus"


def test_entity_merge_default_is_byte_identical(monkeypatch):
    monkeypatch.delenv("RWE_STORY_ENTITY_MERGE", raising=False)
    st = store_mod.Store("sqlite://")
    _seattle(st)
    rows = ss._fetch(st)
    a = ss.build_stories(rows)
    b = ss.build_stories(rows, entity_merge=0, entities={"x": {"person": ["y"]}})
    c = ss.build_stories(rows, entity_merge=2, entities=None)   # env off + no data: both gates
    assert [s["id"] for s in a] == [s["id"] for s in b] == [s["id"] for s in c]
    assert ss.entity_merge_min() == 0
    monkeypatch.setenv("RWE_STORY_ENTITY_MERGE", "garbage")
    assert ss.entity_merge_min() == 0, "junk falls back to off, never to a guess"
    monkeypatch.setenv("RWE_STORY_ENTITY_MERGE", "2")
    assert ss.entity_merge_min() == 2


def test_ubiquitous_names_cannot_propose_merges():
    """Rule v2, from run 1's 130-article receipt: a name in more story consensuses than
    ENTITY_MERGE_MAX_STORY_DF is type-level attendance (the political USGS), and joins proposed
    through such names rebuilt the blob through complete linkage — every pair really did share
    {donald trump, white house}. Eight distinct stories all sharing the same two big names must
    produce ZERO candidates; a pair sharing two names that live in only their two consensuses
    still joins."""
    st = store_mod.Store("sqlite://")
    ents = {}
    # Eight token-disjoint 2-publisher stories, all "sharing" the same two ubiquitous names.
    themes = ["harbor bridge inquiry", "ferry terminal review", "museum funding vote",
              "stadium roof collapse", "airport runway closure", "hospital merger ruling",
              "library archive flood", "railway signal failure"]
    for k, theme in enumerate(themes):
        for pub in ("A", "B"):
            cu = f"https://{pub.lower()}{k}.example.com/s{k}"
            _add(st, cu, f"{pub} Outlet {k}", 0.0, f"{theme} update{k} report{k}")
            ents[cu] = {"person": ["big name"], "org": ["big office"]}
    rows = ss._fetch(st)
    assert len(ss.build_stories(rows)) == 8
    stats: dict = {}
    merged = ss.build_stories(rows, entity_merge=2, entities=ents, veto_stats=stats)
    assert len(merged) == 8, "type-level names must not join eight different events"
    assert stats.get("entityMergeCandidates", 0) == 0
    assert stats.get("entityMergeUbiquitous", 0) == 2, "both big names were excluded"

    # The discriminative pair still joins: names living in exactly two consensuses.
    for pub in ("C", "D"):
        cu = f"https://{pub.lower()}x.example.com/dup"
        _add(st, cu, f"{pub} Dup", 0.0, "Completely different wording about the same incident")
    for pub in ("E", "F"):
        cu = f"https://{pub.lower()}y.example.com/dup2"
        _add(st, cu, f"{pub} Dup", 0.0, "Another phrasing entirely for that very same incident")
    for cu in ("https://cx.example.com/dup", "https://dx.example.com/dup",
               "https://ey.example.com/dup2", "https://fy.example.com/dup2"):
        ents[cu] = {"person": ["jane specific"], "org": ["specific org"]}
    rows = ss._fetch(st)
    merged = ss.build_stories(rows, entity_merge=2, entities=ents)
    joined = [s for s in merged if s["totalCoverage"] == 4]
    assert len(joined) == 1, "two names in exactly two consensuses still carry the join"


def test_unanchored_joins_are_refused():
    """Rule v3, from run 2's hand-read: Leavitt's resignation joined the visa purge on two
    PERIPHERAL shared names while each story's top entity appeared nowhere in the other. A join
    must be anchored by BOTH tops; peripheral overlap alone proposes nothing."""
    st = store_mod.Store("sqlite://")
    ents = {}
    # Story A: top entity "karoline leavitt" (3 votes), peripheral {marco rubio, state department}.
    for k, pub in enumerate(["A", "B", "C"]):
        cu = f"https://{pub.lower()}.example.com/resign"
        _add(st, cu, f"{pub} Out", 0.0, "Press secretary resignation shakes the briefing room")
        ents[cu] = {"person": ["karoline leavitt"] + (["marco rubio"] if k < 2 else []),
                    "org": ["state department"] if k < 2 else []}
    # Story B: top entity "visa program" side — shares rubio + state department, never leavitt.
    for k, pub in enumerate(["D", "E", "F"]):
        cu = f"https://{pub.lower()}.example.com/visas"
        _add(st, cu, f"{pub} Out", 0.0, "Visa revocations accelerate under sweeping directive")
        ents[cu] = {"person": ["marco rubio"] if k < 2 else [],
                    "org": ["visa fraud unit"] + (["state department"] if k < 2 else [])}
    rows = ss._fetch(st)
    assert len(ss.build_stories(rows)) == 2
    stats: dict = {}
    merged = ss.build_stories(rows, entity_merge=2, entities=ents, veto_stats=stats)
    assert len(merged) == 2, "two peripheral shared names must not join two different events"
    assert stats.get("entityMergeUnanchored", 0) >= 1
    assert stats.get("entityMergeCandidates", 0) == 0

    # The anchored counterpart still joins: the same two stories, but the shared names ARE the
    # tops on both sides (the Mangione shape).
    ents2 = {}
    for pub in ["a", "b", "c"]:
        ents2[f"https://{pub}.example.com/resign"] = \
            {"person": ["shared top person"], "org": ["shared top org"]}
    for pub in ["d", "e", "f"]:
        ents2[f"https://{pub}.example.com/visas"] = \
            {"person": ["shared top person"], "org": ["shared top org"]}
    merged = ss.build_stories(rows, entity_merge=2, entities=ents2)
    assert len(merged) == 1 and merged[0]["totalCoverage"] == 6


def test_serving_path_supplies_entities_when_adopted(monkeypatch):
    """The adoption wiring: every serving call site fetches the entity mapping through
    _entities_for when the env is on — cluster_from_store here as the representative — and an
    environment without the flag pays no query and builds lexically."""
    monkeypatch.setenv("RWE_STORY_ENTITY_MERGE", "2")
    st = store_mod.Store("sqlite://")
    _seattle(st)
    for pub in ("a", "b"):
        st.replace_article_entities(f"https://{pub}.example.com/shooting",
                                    {"person": ["jane suspect"], "org": ["seattle center"]})
    for pub in ("c", "d"):
        st.replace_article_entities(f"https://{pub}.example.com/gunfire",
                                    {"person": ["jane suspect"], "org": ["seattle center"]})
    stories = ss.cluster_from_store(st)
    assert len(stories) == 1 and stories[0]["totalCoverage"] == 4, \
        "the serving path joined the lexically-unreachable pair on its own"
    monkeypatch.delenv("RWE_STORY_ENTITY_MERGE", raising=False)
    assert len(ss.cluster_from_store(st)) == 2, "off: lexical build, no entity query"


# --------------------------------------------------------------------------- #
# Story-hero guard (RWE_STORY_HERO_GUARD) — ranked hero + cross-story reuse rejection.
# Measured 2026-08-16 on the production catalog (docs/STORY_HERO_IMAGES.md); presentation only.
# --------------------------------------------------------------------------- #
def _add_with_image(st, cu, pub, title, *, image=None, w=None, h=None, days=0.0, lean=0.0):
    st.upsert_feed_article(
        canonical_url=cu, url=cu, publisher=pub, source_publisher=pub, title=title,
        description="context", body=None,
        published_at=(NOW - timedelta(days=days)).isoformat(), source_feed="feed://x",
        scored={"article_id": cu, "outlet": pub, "category": "Politics", "lean": lean,
                "title": title},
        image=image, image_width=w, image_height=h,
        image_source="media:content" if image else None)


def _four_stories_one_banner(st, *, families=4):
    """`families` lexically-disjoint two-publisher stories whose every member carries the SAME
    house asset (under differing cache-buster queries — identity, not string equality), plus one
    member of the first story with a real photo."""
    banner = "https://cdn.example/promo/site-banner.png"
    titles = [
        ("Volcano erupts near Reykjavik spewing ash", "Volcano erupts near Reykjavik airport shut"),
        ("Panda cub born at Madrid zoo delights", "Panda cub born at Madrid zoo thrives"),
        ("Cyclone lashes Queensland coast evacuations", "Cyclone lashes Queensland coast flooding"),
        ("Referee strike halts Serie A weekend", "Referee strike halts Serie A matches"),
    ][:families]
    for i, (t1, t2) in enumerate(titles):
        _add_with_image(st, f"https://one.example/{i}", "Outlet One", t1,
                        image=f"{banner}?cb={i}a", days=1.0 + i / 100.0)
        _add_with_image(st, f"https://two.example/{i}", "Outlet Two", t2,
                        image=f"{banner}?cb={i}b", days=0.5 + i / 100.0)
    _add_with_image(st, "https://three.example/0", "Outlet Three",
                    "Volcano erupts near Reykjavik ash cloud",
                    image="https://three.example/2026/08/eruption-scene.jpg",
                    w=1600, h=900, days=0.25)
    return banner


def test_hero_guard_off_is_the_legacy_representative_hero():
    """Code default OFF: an environment without the deploy's variables ships the old behaviour —
    the earliest filer's asset fronts the card even when a later member has a real photo."""
    st = store_mod.Store("sqlite://")
    _four_stories_one_banner(st, families=1)
    (story,) = ss.build_stories(ss._fetch(st))
    assert story["image"].startswith("https://cdn.example/promo/site-banner.png"), \
        "legacy: representative-first, no judgement — the pre-guard contract, byte-identical"


def test_hero_guard_rejects_an_asset_fronting_four_stories(monkeypatch):
    """The reuse tier: one image on > HERO_MAX_CLUSTER_REUSE distinct clusters in the same build
    is publisher furniture by definition (measured: sr_placeholder.png fronted 20 stories). The
    story with a real photo re-heroes to it; the rest fall back to NO hero — the imageless card's
    coverage figure is a designed state, and no hero is more honest than a banner."""
    st = store_mod.Store("sqlite://")
    _four_stories_one_banner(st, families=4)
    rows = ss._fetch(st)
    off = ss.build_stories(rows)
    monkeypatch.setenv("RWE_STORY_HERO_GUARD", "1")
    on = ss.build_stories(rows)
    assert len(off) == len(on) == 4
    assert [(s["id"], s["totalCoverage"], s["title"]) for s in on] \
        == [(s["id"], s["totalCoverage"], s["title"]) for s in off], \
        "presentation only: membership, ids and order must be untouched by the guard"
    heroes = {s["title"]: s["image"] for s in on}
    volcano = next(t for t in heroes if "Volcano" in t)
    assert heroes.pop(volcano) == "https://three.example/2026/08/eruption-scene.jpg"
    assert set(heroes.values()) == {None}, \
        "every banner-only story shows the coverage figure, not the reused asset"
    assert all(s["image"] for s in off), "and OFF still serves the banner everywhere (control)"


def test_hero_guard_keeps_an_asset_at_exactly_three_stories(monkeypatch):
    """The threshold's other half — the receipt that set it: at exactly 3 stories the production
    table held The Hill's real AP file art, legitimately shared across a related family. df=3
    must survive, or the guard rejects real photography."""
    st = store_mod.Store("sqlite://")
    banner = _four_stories_one_banner(st, families=3)
    monkeypatch.setenv("RWE_STORY_HERO_GUARD", "1")
    stories = ss.build_stories(ss._fetch(st))
    assert len(stories) == 3
    shared = [s for s in stories if (s["image"] or "").startswith(banner)]
    assert len(shared) == 2, \
        "3 clusters <= threshold: the shared asset still heroes the stories without a better member"


def test_hero_guard_reader_is_honest_about_junk(monkeypatch):
    monkeypatch.delenv("RWE_STORY_HERO_GUARD", raising=False)
    assert ss.hero_guard() is False, "UNSET is off — library fallback changes nothing"
    monkeypatch.setenv("RWE_STORY_HERO_GUARD", "1")
    assert ss.hero_guard() is True
    monkeypatch.setenv("RWE_STORY_HERO_GUARD", "definitely")
    assert ss.hero_guard() is False, "junk falls back to off, never to a guess"
    monkeypatch.setenv("RWE_STORY_HERO_GUARD", "0")
    assert ss.hero_guard() is False


# --------------------------------------------------------------------------- #
# Story summary selection (pick_story_summary) — adopted 2026-08-16 against the
# audit_story_summary production baseline (26.2% GN digests, echo 31.5%, 212 url-leaks).
# Fixtures below are ABBREVIATED PRODUCTION EXHIBITS from that baseline run.
# --------------------------------------------------------------------------- #
def _sm(pub, headline, desc, *, stype=None, at="2026-08-16T10:00:00+00:00"):
    return {"publisher": pub, "headline": headline, "description": desc, "sourceType": stype,
            "publishedAt": at, "id": f"https://{pub.lower().replace(' ', '')}.example/a"}


GN_DIGEST = ("A judge expands a block on U.S. Postal Service work on Trump's mail-in voting "
             "order NPR\nJudge again bars Trump administration from implementing order that "
             "sought to limit mail voting The Guardian")


def test_summary_gn_digest_rejected_by_provider_evidence():
    """The mail-in-voting exhibit: the earliest filer is a Google News digest. Provider evidence
    alone must reject it — the related outlets are mostly NOT cluster members (measured: the
    member-name backstop caught 13 of the ~399-story class)."""
    rep = _sm("NPR", "Judge expands block on Postal Service election work", GN_DIGEST,
              stype="googlenews", at="2026-08-16T08:00:00+00:00")
    dek = _sm("The Guardian", "Judge bars mail voting order",
              "A federal judge widened an injunction against the administration's order on Friday.")
    got = ss.pick_story_summary([rep, dek], rep)
    assert got == "A federal judge widened an injunction against the administration's order on Friday."
    assert "NPR" not in got and "\n" not in got


def test_summary_structural_digest_rejected_without_provider_tag():
    """The same digest arriving WITHOUT the googlenews tag: >=2 newline-separated headline rows
    (rows end in an outlet name, not sentence punctuation) is the structural signature."""
    rep = _sm("NPR", "Judge expands block", GN_DIGEST, stype="rss",
              at="2026-08-16T08:00:00+00:00")
    dek = _sm("The Guardian", "Judge bars order",
              "A federal judge widened an injunction against the order on Friday.")
    assert ss.pick_story_summary([rep, dek], rep).startswith("A federal judge widened")


def test_summary_member_name_backstop_still_fires():
    """A single-line description naming two OTHER cluster members (no newlines, no provider
    tag) — the registered backstop tier."""
    rep = _sm("KWTX", "Helicopter crash near Fort Cavazos",
              "Two dead after crash, CBS News and Fox News report from the scene",
              at="2026-08-16T08:00:00+00:00")
    others = [_sm("CBS News", "Two dead in crash", "Officials confirmed two fatalities at the site."),
              _sm("Fox News", "Crash kills two", "The cause of the crash remains under investigation.")]
    got = ss.pick_story_summary([rep, *others], rep)
    assert got == "Officials confirmed two fatalities at the site."


def test_summary_standfirst_kept_and_clamped_to_two_sentences():
    """The Guardian 740-char exhibit: a real multi-paragraph dek has ONE unpunctuated
    headline-ish line, so the structural test must keep it — then the clamp takes at most two
    sentences, single-line, <=320 chars, on a word boundary."""
    body = ("There's no evidence that vaccines cause autism, or that we should separate MMR "
            "shots. This order is dangerous\n\n" + "The president's order rests on feeling, "
            "not evidence, and pediatricians warned it will cost lives. " * 6)
    rep = _sm("The Guardian", "Trump's vaccine order is about feelings, not facts", body,
              at="2026-08-16T08:00:00+00:00")
    got = ss.pick_story_summary([rep], rep)
    assert got and "\n" not in got and len(got) <= 321
    assert got.startswith("There's no evidence that vaccines cause autism")


def test_summary_echo_rejected_against_story_and_own_headline():
    """The WaPo exhibit — the dek IS the headline plus a masthead. Rejected as echo; a sole
    candidate rejected means the counted fallback serves (the designed empty state)."""
    rep = _sm("The Washington Post", "Contradicting public statements, Trump took secret flight from Turkey",
              "Contradicting public statements, Trump took secret flight from Turkey The Washington Post",
              at="2026-08-16T08:00:00+00:00")
    assert ss.pick_story_summary([rep], rep) == ""
    # own-headline echo on a NON-representative member is judged the same way
    other = _sm("Big Island Now", "Big Island could see severe weather starting Friday",
                "Big Island could see severe weather starting Friday, forecasters said early today.")
    rep2 = _sm("KHON", "Storm nears the islands", "", at="2026-08-16T07:00:00+00:00")
    assert ss.pick_story_summary([rep2, other], rep2) == ""


def test_summary_url_junk_rejected():
    rep = _sm("ABC News", "Darline Graham faces primary battle",
              "Darline Graham faces battle reuters.com officials met late on Friday.",
              at="2026-08-16T08:00:00+00:00")
    assert ss.pick_story_summary([rep], rep) == ""


def test_summary_masthead_suffix_stripped_from_winner():
    rep = _sm("Buffalo News", "Sabres sign veteran goalie",
              "The Sabres signed a veteran goaltender to a one-year deal on Saturday - Buffalo News",
              at="2026-08-16T08:00:00+00:00")
    got = ss.pick_story_summary([rep], rep)
    assert got == "The Sabres signed a veteran goaltender to a one-year deal on Saturday"


def test_summary_ranking_sentence_beats_fragment_rep_breaks_ties():
    frag = _sm("Alpha", "Quake shakes the province", "Rescue teams en route",
               at="2026-08-16T07:00:00+00:00")                       # fragment: short, no punct
    full = _sm("Beta", "Dozens hurt in quake",
               "Rescue teams reached the hardest-hit district early on Sunday, officials said.")
    assert ss.pick_story_summary([frag, full], frag).startswith("Rescue teams reached")
    a = _sm("Alpha", "Quake shakes the province",
            "The tremor damaged more than a thousand homes across the province, officials said.",
            at="2026-08-16T07:00:00+00:00")
    b = _sm("Beta", "Dozens hurt in quake",
            "Rescue crews say the damage spans a wide area and repairs will take many months.")
    assert ss.pick_story_summary([a, b], a).startswith("The tremor damaged"), \
        "equal-quality deks: the representative is the tiebreak"


def test_summary_build_level_wiring_fallback_and_id_stability():
    """End-to-end through the store: a GN-digest representative loses to a clean member dek
    (sourceType passthrough proven); a GN-only-dek story serves the counted fallback; ids
    anchor on the representative URL and never move with summaries; two builds are identical."""
    st = store_mod.Store("sqlite://")
    def put(cu, pub, title, desc, days, stype=None):
        st.upsert_feed_article(canonical_url=cu, url=cu, publisher=pub, source_publisher=pub,
                               title=title, description=desc, body=None,
                               published_at=(NOW - timedelta(days=days)).isoformat(),
                               source_feed="feed://x", source_type=stype,
                               scored={"article_id": cu, "outlet": pub, "category": "Politics",
                                       "lean": 0.0, "title": title})
    put("https://gn.example/a", "Wire Desk", "Volcano erupts near Reykjavik spewing ash",
        "Volcano erupts near Reykjavik spewing ash RUV\nAsh cloud grounds flights Iceland Monitor",
        1.0, "googlenews")
    put("https://gaz.example/a", "Reykjavik Gazette", "Volcano erupts near Reykjavik airport shut",
        "Aviation authorities shut the airport as the eruption intensified overnight.", 0.5)
    put("https://gn2.example/a", "Agency Desk", "Panda cub born at Madrid zoo delights",
        "Panda cub born at Madrid zoo delights Zoo Daily\nCub thrives keepers say Madrid Herald",
        1.0, "googlenews")
    put("https://zoo.example/a", "Zoo Daily", "Panda cub born at Madrid zoo thrives", "", 0.5)

    stories = ss.build_stories(ss._fetch(st))
    assert len(stories) == 2
    volcano = next(s for s in stories if "Volcano" in s["title"])
    panda = next(s for s in stories if "Panda" in s["title"])
    assert volcano["summary"] == \
        "Aviation authorities shut the airport as the eruption intensified overnight."
    assert panda["summary"] == "2 publishers covering politics.", \
        "a story whose only dek is a digest serves the counted fallback"
    import hashlib as _h
    assert volcano["id"] == "st_" + _h.sha1(b"https://gn.example/a").hexdigest()[:16], \
        "the id still anchors on the representative — summaries can never move ids"
    assert [s["summary"] for s in ss.build_stories(ss._fetch(st))] == \
        [s["summary"] for s in stories], "deterministic"


# --------------------------------------------------------------------------- #
# Sole-template-evidence gate (Phase B; RWE_CLUSTER_TEMPLATE_GATE, off by default)
# --------------------------------------------------------------------------- #
def _template_weld(st):
    """The production-confirmed anchor exhibit (2026-08-17): five announcement-template
    headlines welded into one story by edges whose shared tokens are all template vocabulary,
    plus a genuine two-publisher control story that must never be touched."""
    _add(st, "https://e.example/1", "Radio Pacific Inc", 0.0,
         "'X-Men' cast, release date revealed at D23", category="Entertainment", days=1)
    _add(st, "https://e.example/2", "Forbes", 0.0,
         "Here Are The MCU X-Men Cast Members Revealed At D23 - Forbes",
         category="Entertainment", days=1)
    _add(st, "https://e.example/3", "Techgenyz.Com", 0.0,
         "DJI Osmo 360 II Release Date and Specs: Everything We Know So Far",
         category="Entertainment", days=2)
    _add(st, "https://e.example/4", "Womansworld.Com", 0.0,
         "The Paper Season 2 Cast , Release Date and Trailer Revealed",
         category="Entertainment", days=3)
    _add(st, "https://e.example/5", "News18", 0.0,
         "Mirzapur The Movie: Trailer, Cast, Release Date And Everything You Must Know",
         category="Entertainment", days=4)
    _add(st, "https://q.example/1", "AP News", 0.0,
         "Earthquake strikes central Colombia injuring dozens", days=0)
    _add(st, "https://q.example/2", "BBC News", 0.0,
         "Earthquake strikes central Colombia injuring dozens of residents", days=0)


def test_template_gate_off_is_byte_identical_and_keeps_the_weld():
    """OFF (the default) is provably the pre-gate behaviour: template=None and template=False
    build identical stories, and the anchor weld still forms — pinning the CURRENT defect so
    the gate's effect is measured against it, never assumed."""
    import json
    st = store_mod.Store("sqlite://")
    _template_weld(st)
    rows = ss._fetch(st)
    off = ss.build_stories(rows)
    explicit = ss.build_stories(rows, template=False)
    assert json.dumps(off, sort_keys=True) == json.dumps(explicit, sort_keys=True)
    assert any(s["totalCoverage"] == 5 for s in off)          # the weld is present without the gate


def test_template_gate_resolves_the_anchor_exhibit():
    """ON: the weld resolves exactly as Phase A's fragmentation predicted — the X-Men pair
    survives as a story, the three unrelated articles detach below admission, the control
    story is untouched, the veto is counted, and the build is deterministic."""
    import json
    st = store_mod.Store("sqlite://")
    _template_weld(st)
    rows = ss._fetch(st)
    stats = {}
    on = ss.build_stories(rows, template=True, veto_stats=stats)
    assert json.dumps(on, sort_keys=True) == \
        json.dumps(ss.build_stories(rows, template=True), sort_keys=True)   # deterministic
    assert sorted(s["totalCoverage"] for s in on) == [2, 2]   # X-Men pair + control; weld gone
    covered = {c["url"] for s in on for c in s["coverage"]}
    assert {"https://e.example/1", "https://e.example/2"} <= covered        # pair stays clustered
    assert not ({"https://e.example/3", "https://e.example/4",
                 "https://e.example/5"} & covered)             # the three detach
    assert stats.get("templateEdgeVetoed", 0) >= 3            # the three false edges, counted


def test_template_gate_env_resolution(monkeypatch):
    """Env flag semantics: unset/junk = off (never a guess), '1' = on, and the env build is
    byte-identical to the explicit-parameter build."""
    import json
    monkeypatch.delenv("RWE_CLUSTER_TEMPLATE_GATE", raising=False)
    assert ss.template_gate() is False
    monkeypatch.setenv("RWE_CLUSTER_TEMPLATE_GATE", "loud")
    assert ss.template_gate() is False
    monkeypatch.setenv("RWE_CLUSTER_TEMPLATE_GATE", "1")
    assert ss.template_gate() is True
    st = store_mod.Store("sqlite://")
    _template_weld(st)
    rows = ss._fetch(st)
    via_env = ss.build_stories(rows)
    monkeypatch.delenv("RWE_CLUSTER_TEMPLATE_GATE")
    via_param = ss.build_stories(rows, template=True)
    assert json.dumps(via_env, sort_keys=True) == json.dumps(via_param, sort_keys=True)


def test_template_closure_requires_a_distinctive_shared_token():
    """The closure itself: a pair sharing only template tokens fails; one distinctive shared
    token passes; and the veto counter increments only on the failure path."""
    arts = [{"headline": "The Paper Season 2 Cast , Release Date and Trailer Revealed"},
            {"headline": "Mirzapur The Movie: Trailer, Cast, Release Date And Everything You Must Know"},
            {"headline": "'X-Men' cast, release date revealed at D23"},
            {"headline": "Here Are The MCU X-Men Cast Members Revealed At D23 - Forbes"}]
    stats = {}
    ok = ss._template_closure(arts, 0, stats)
    assert ok(0, 1) is False                                  # sole-template -> vetoed
    assert ok(2, 3) is True                                   # shares {men, d23} -> passes
    assert stats == {"templateEdgeVetoed": 1}


# --------------------------------------------------------------------------- #
# Similar Stories — the rail on the story page.
#
# WHAT THESE TESTS DELIBERATELY DO NOT DO: assert that the shipped default constants produce a
# particular number of cards on this catalog. That is the mistake that shipped twice. The measure
# is IDF-weighted Jaccard `w / (Ta + Tb - w)`, and BOTH halves of it move with catalog size — the
# weights are `log(1 + N/df)` and the totals grow with how many coverage headlines a story carries.
# A twelve-article fixture and a 2,852-story production catalog therefore live on different scales:
# the first default was calibrated against a nine-story demo and emptied the rail everywhere.
#
# So the assertions here are about the RULE, with the thresholds passed in explicitly, and about
# the shape of the distribution the rule has to survive. The constants themselves are calibrated
# against production and the evidence for them is recorded at `SIMILAR_REL_RATIO`.
# --------------------------------------------------------------------------- #
_KYIV_DESC = "Ukrainian air defence units engaged dozens of Shahed drones over Kyiv oblast overnight."
_ORINOCO_DESC = "Officials described terms covering Orinoco belt crude output."


def _kyiv_and_caracas(st):
    """Four events with the two distributions production actually produces.

    The Kyiv pair is a strong match (0.43); the Caracas pair is a weak but genuine one (0.21); the
    Senate story shares Kyiv's names and place but is a different event (0.10); the wildfire shares
    nothing. The load-bearing property is that Caracas's TRUE match scores below the Kyiv story's
    cut — so one fixed number cannot serve both rails.
    """
    # A drone strike, and the air-defence response to it: one event family, two clusters.
    _add(st, "https://npr.org/t1", "NPR", -1.0, "Russian drone barrage kills 27 near Kyiv", desc=_KYIV_DESC)
    _add(st, "https://bbc.com/t2", "BBC News", 0.0, "Drone barrage kills 27 Ukrainians near Kyiv", desc=_KYIV_DESC)
    _add(st, "https://reuters.com/r1", "Reuters", 0.0,
         "Air defence units intercept Shahed drones over Kyiv oblast", desc=_KYIV_DESC)
    _add(st, "https://apnews.com/r2", "AP", 0.0,
         "Ukrainian gunners down Shahed drones above Kyiv oblast", desc=_KYIV_DESC)
    # The reported defect, in fixture form: shares "Ukrainian", "Kyiv", "drone" and "defence" with
    # the strike, and is a budget argument in Washington.
    _add(st, "https://wsj.com/w1", "WSJ", 0.8, "Senate committee debates the Ukraine aid package",
         desc="Ukrainian officials in Kyiv welcomed the drone defence funding.")
    _add(st, "https://cnn.com/w2", "CNN", -1.2, "Senators clash over Ukraine aid package size",
         desc="Ukrainian officials in Kyiv welcomed the drone defence funding.")
    # A flat distribution: one real relative, scoring far below what the Kyiv story's best reaches.
    _add(st, "https://foxnews.com/v1", "Fox News", 1.5,
         "Washington and Caracas reach an oil reserves accord", desc=_ORINOCO_DESC)
    _add(st, "https://ft.com/v2", "Financial Times", 0.2,
         "Caracas and Washington reach accord on oil reserves", desc=_ORINOCO_DESC)
    _add(st, "https://news.sky.com/v3", "Sky News", 0.0, "Orinoco crude output climbs after the accord",
         desc="Analysts tracked shipments from the belt.")
    _add(st, "https://aljazeera.com/v4", "Al Jazeera", -0.3,
         "Crude output from Orinoco climbs following the accord",
         desc="Analysts tracked shipments from the belt.")
    # Nothing in common with anything.
    _add(st, "https://guardian.com/n1", "The Guardian", -1.5,
         "Wildfires spread along the western coast", category="Climate")
    _add(st, "https://msnbc.com/n2", "MSNBC", -1.4,
         "Wildfires spread fast along the western coast", category="Climate")


def _find(st, needle):
    for s in ss.cluster_from_store(st):
        if needle.lower() in s["title"].lower():
            return s
    raise AssertionError(f"no story matching {needle!r}")


def test_similar_keeps_the_same_event_and_cuts_a_shared_name():
    """The reported defect. A story sharing the target's names, place and topic — four profile
    tokens, a real overlap — is not a similar story, and the rail must not carry it."""
    st = store_mod.Store("sqlite://"); _kyiv_and_caracas(st)
    strike = _find(st, "Drone barrage kills 27")
    got = [s["title"] for s in ss.similar_stories(st, strike["id"], min_score=0.01, rel_ratio=0.5)]
    assert got == ["Ukrainian gunners down Shahed drones above Kyiv oblast"]
    # Not because it was unscored — it scores, and is cut.
    diag = ss.similar_diagnostics(st, strike["id"])
    senate = next(r for r in diag["top"] if "Senate" in r["title"] or "Senators" in r["title"])
    assert senate["shared"] >= 3 and senate["score"] > 0
    assert senate["score"] < diag["cutInEffect"]


def test_similar_selection_is_relative_not_absolute():
    """WHY THE CUT IS A RATIO. Production's per-story best varies nearly 4x (0.246 for a Kyiv
    strike, 0.068 for the Venezuela oil deal) while the median pair scores 0, and this fixture
    reproduces that shape. A fixed floor tuned to the strong story empties the weak one's rail —
    which is what shipped, twice — and a floor tuned to the weak one fills the strong one's rail
    with noise. Judging each story against its OWN best is the only rule that serves both."""
    st = store_mod.Store("sqlite://"); _kyiv_and_caracas(st)
    strike = _find(st, "Drone barrage kills 27")
    accord = _find(st, "Caracas reach an oil reserves accord")

    strong = ss.similar_diagnostics(st, strike["id"])
    weak = ss.similar_diagnostics(st, accord["id"])
    # The two distributions differ in scale, not only in content.
    assert weak["scoreQuantiles"]["max"] < strong["cutInEffect"] < strong["scoreQuantiles"]["max"]

    # The relative rule keeps each story's own best match.
    assert [s["title"] for s in ss.similar_stories(st, strike["id"], min_score=0.01, rel_ratio=0.5)] == \
        ["Ukrainian gunners down Shahed drones above Kyiv oblast"]
    assert [s["title"] for s in ss.similar_stories(st, accord["id"], min_score=0.01, rel_ratio=0.5)] == \
        ["Crude output from Orinoco climbs following the accord"]

    # A FIXED floor at the strong story's cut — the best a single absolute number can do for it —
    # deletes the weak story's genuine match. This is the bug, reproduced.
    fixed = strong["cutInEffect"]
    assert ss.similar_stories(st, accord["id"], min_score=fixed, rel_ratio=0.0) == []
    # And a fixed floor low enough to keep it admits the Senate story into the strike's rail.
    loose = [s["title"] for s in ss.similar_stories(st, strike["id"], min_score=0.05, rel_ratio=0.0)]
    assert any("Senat" in t for t in loose)


def test_similar_floor_is_the_backstop_for_a_story_with_nothing_related():
    """The one case a ratio cannot handle: a story whose best candidate is noise. A ratio would
    keep the top few of nothing, so an absolute floor still has to say there is nothing."""
    st = store_mod.Store("sqlite://"); _kyiv_and_caracas(st)
    fire = _find(st, "Wildfires spread")
    assert ss.similar_stories(st, fire["id"], min_score=0.035, rel_ratio=0.5) == []
    # Fewer results, not padded ones: the rail is allowed to be empty.
    assert ss.similar_diagnostics(st, fire["id"])["scoreQuantiles"]["max"] == 0.0


def test_similar_never_returns_the_story_itself_and_honours_limit():
    st = store_mod.Store("sqlite://"); _kyiv_and_caracas(st)
    strike = _find(st, "Drone barrage kills 27")
    everything = ss.similar_stories(st, strike["id"], limit=25, min_score=0.0, rel_ratio=0.0)
    ids = [s["id"] for s in everything]
    assert strike["id"] not in ids
    assert len(ids) == len(set(ids))
    assert len(ss.similar_stories(st, strike["id"], limit=1, min_score=0.0, rel_ratio=0.0)) == 1
    # Best first, and stable: the same build gives the same order.
    assert ids == [s["id"] for s in ss.similar_stories(st, strike["id"], limit=25, min_score=0.0,
                                                       rel_ratio=0.0)]


def test_similar_is_none_for_an_id_the_catalog_no_longer_holds():
    """Distinguishable from "nothing is similar" — the route turns this into a 404 and the empty
    list into an empty rail, which are different facts about the page."""
    st = store_mod.Store("sqlite://"); _kyiv_and_caracas(st)
    assert ss.similar_stories(st, "st_nosuchstory") is None
    assert ss.similar_diagnostics(st, "st_nosuchstory") is None


def test_similar_thresholds_resolve_from_env(monkeypatch):
    """Both knobs: unset is the calibrated default, junk is the default (never a guess), and a
    value is clamped into [0, 1]. RWE_STORY_SIMILAR_MIN spent a release missing from the compose
    allowlist, so every probe of it was inert — the resolution itself is worth pinning."""
    for name, fn, default in (("RWE_STORY_SIMILAR_MIN", ss.similar_min_score, ss.SIMILAR_NOISE_FLOOR),
                              ("RWE_STORY_SIMILAR_RATIO", ss.similar_rel_ratio, ss.SIMILAR_REL_RATIO)):
        monkeypatch.delenv(name, raising=False)
        assert fn() == default
        monkeypatch.setenv(name, "0.4")
        assert fn() == 0.4
        monkeypatch.setenv(name, "nope")
        assert fn() == default
        monkeypatch.setenv(name, "9")
        assert fn() == 1.0
        monkeypatch.setenv(name, "-1")
        assert fn() == 0.0
        monkeypatch.delenv(name)


def test_similar_profile_memo_refreshes_when_coverage_grows():
    """The memo exists because the rail tokenizes every coverage headline in the catalog on every
    request. It is keyed on a fingerprint that MOVES with the story's coverage, so a story whose
    coverage grew is re-read rather than served from a profile that predates it."""
    ss._SIMILAR_PROFILES.clear()
    story = {"id": "st_x", "totalCoverage": 1, "updatedAt": "2026-09-01T00:00:00Z",
             "title": "Senate passes the funding bill", "summary": "",
             "coverage": [{"headline": "Senate passes the funding bill"}]}
    first = ss._similar_profile(story)
    assert ss._similar_profile(story) is first                      # warm: the same object
    grew = dict(story, totalCoverage=2, updatedAt="2026-09-02T00:00:00Z",
                coverage=story["coverage"] + [{"headline": "Shutdown averted as senators vote"}])
    second = ss._similar_profile(grew)
    assert "shutdown" in second and "shutdown" not in first
    assert ss._SIMILAR_PROFILE_MAX > 0                              # bounded, so it cannot grow forever


def test_similar_diagnostics_reports_the_distribution_with_no_cut_applied():
    """The instrument for choosing the thresholds: it must report what the rail WOULD cut, not the
    already-cut result — an operator sweeping a real catalog needs the scores the floor rejects."""
    st = store_mod.Store("sqlite://"); _kyiv_and_caracas(st)
    strike = _find(st, "Drone barrage kills 27")
    d = ss.similar_diagnostics(st, strike["id"])
    assert d["candidates"] == 5                                     # every other story, unfiltered
    assert len(d["top"]) == 5
    assert [r["score"] for r in d["top"]] == sorted((r["score"] for r in d["top"]), reverse=True)
    assert any(r["score"] < d["cutInEffect"] for r in d["top"])     # below the cut, still reported
    assert d["cutInEffect"] == round(max(d["floorInEffect"],
                                         d["scoreQuantiles"]["max"] * d["ratioInEffect"]), 4)
    assert d["minSharedInEffect"] == ss.clustering.MIN_SHARED_TOKENS
