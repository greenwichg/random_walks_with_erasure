"""Tests for examples/story_intelligence.py — deterministic intelligence computed ON TOP of Story
objects (Commit 10). Story Intelligence is a strict consumer of the Story Service: it reads a Story
(+ the reader's existing reads) and derives freshness, lifecycle, momentum, coverage statistics, an
expanded timeline (incl. Perspective Expansion), "new since last visit", and informational alerts.

The decomposed functions are tested directly with hand-built, time-anchored Story dicts (so ages are
deterministic against a fixed ``now``), plus one integration test through the real Story Service and
a guard that the dependency graph (Service → Intelligence, never the reverse) holds.
"""

import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import store as store_mod          # noqa: E402
import story_service as ss         # noqa: E402
import story_intelligence as si    # noqa: E402

NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)
TH = si.thresholds_from_env()      # breaking 3h, developing 12h, active 48h, cooling 168h, window 24h


def _iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat()


def _cov(pub, hours_ago, *, bucket="center", url=None, headline=None):
    """One coverage article, published ``hours_ago`` before the fixed NOW."""
    return {"publisher": pub, "leanBucket": bucket, "publishedAt": _iso(hours_ago),
            "url": url or f"https://{pub.split()[0].lower()}.example/{hours_ago}",
            "headline": headline or f"{pub} on the senate funding bill"}


def _story(cov, **over):
    pubs = {c["publisher"] for c in cov}
    times = sorted(c["publishedAt"] for c in cov)
    span = 0.0
    if len(times) >= 2:
        span = round((si._parse(times[-1]) - si._parse(times[0])).total_seconds() / 3600.0, 2)
    d = {"id": "st_test", "coverage": cov,
         "latest": times[-1] if times else None, "updatedAt": times[-1] if times else None,
         "totalCoverage": len(cov), "publisherCount": len(pubs),
         "publisherDiversity": round(len(pubs) / len(cov), 3) if cov else 0.0,
         "timeSpanHours": span, "distribution": {"left": 0.0, "center": 1.0, "right": 0.0}}
    d.update(over)
    return d


# --------------------------------------------------------------------------- #
# Freshness
# --------------------------------------------------------------------------- #
def test_freshness_breaking_needs_recent_burst():
    # 3 articles all within the 3h breaking window -> Breaking (age<=3 AND burst>=2)
    s = _story([_cov("NPR", 0.5), _cov("Fox News", 1.0), _cov("BBC News", 2.0)])
    f = si.compute_freshness(s, now=NOW, th=TH)
    assert f["band"] == "Breaking" and f["recentArticles"] == 3
    assert 0 <= f["score"] <= 100 and f["score"] > 80        # very fresh -> high score
    assert f["latestAgeHours"] == 0.5


def test_freshness_bands_by_age():
    assert si.compute_freshness(_story([_cov("A", 6)]), now=NOW, th=TH)["band"] == "Developing"
    assert si.compute_freshness(_story([_cov("A", 30)]), now=NOW, th=TH)["band"] == "Active"
    assert si.compute_freshness(_story([_cov("A", 100)]), now=NOW, th=TH)["band"] == "Cooling"
    assert si.compute_freshness(_story([_cov("A", 200)]), now=NOW, th=TH)["band"] == "Archived"


def test_freshness_no_age_is_archived():
    f = si.compute_freshness(_story([], latest=None, updatedAt=None), now=NOW, th=TH)
    assert f["band"] == "Archived" and f["score"] == 0 and f["latestAgeHours"] is None


def test_freshness_single_recent_article_is_not_breaking():
    # one article inside the breaking window is Developing, not Breaking (burst < 2)
    f = si.compute_freshness(_story([_cov("A", 1.0)]), now=NOW, th=TH)
    assert f["band"] == "Developing"


# --------------------------------------------------------------------------- #
# Momentum
# --------------------------------------------------------------------------- #
def test_momentum_growing_on_recent_surge():
    s = _story([_cov("NPR", 2), _cov("Fox News", 4), _cov("BBC News", 6)])   # all in the recent 24h window
    m = si.compute_momentum(s, now=NOW, th=TH)
    assert m["state"] == "Growing" and m["recentArticles"] == 3 and m["priorArticles"] == 0
    assert m["newPublishers"] == 3


def test_momentum_stable_when_flat_and_no_new_publishers():
    # 2 recent + 2 prior, both publishers first seen in the prior window (no new joiners) -> Stable
    s = _story([_cov("NPR", 30), _cov("Fox News", 36), _cov("NPR", 10), _cov("Fox News", 20)])
    m = si.compute_momentum(s, now=NOW, th=TH)
    assert m["recentArticles"] == 2 and m["priorArticles"] == 2 and m["newPublishers"] == 0
    assert m["state"] == "Stable"


def test_momentum_declining_when_quiet():
    s = _story([_cov("NPR", 40), _cov("Fox News", 44)])       # nothing in the recent window
    assert si.compute_momentum(s, now=NOW, th=TH)["state"] == "Declining"


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
def test_lifecycle_states():
    breaking = _story([_cov("NPR", 0.5), _cov("Fox News", 1.0), _cov("BBC News", 2.0)])
    f = si.compute_freshness(breaking, now=NOW, th=TH); m = si.compute_momentum(breaking, now=NOW, th=TH)
    assert si.compute_lifecycle(breaking, f, m, now=NOW, th=TH) == "Breaking"

    mature = _story([_cov("NPR", 30), _cov("Fox News", 34)])
    f = si.compute_freshness(mature, now=NOW, th=TH); m = si.compute_momentum(mature, now=NOW, th=TH)
    assert si.compute_lifecycle(mature, f, m, now=NOW, th=TH) == "Mature"

    archived = _story([_cov("NPR", 200), _cov("Fox News", 210)])
    f = si.compute_freshness(archived, now=NOW, th=TH); m = si.compute_momentum(archived, now=NOW, th=TH)
    assert si.compute_lifecycle(archived, f, m, now=NOW, th=TH) == "Archived"


# --------------------------------------------------------------------------- #
# Coverage statistics
# --------------------------------------------------------------------------- #
def test_coverage_statistics_distribution_and_growth():
    s = _story([_cov("NPR", 30), _cov("NPR", 10), _cov("Fox News", 5)],
               publisherDiversity=0.667, publisherCount=2, totalCoverage=3, timeSpanHours=25.0,
               distribution={"left": 0.33, "center": 0.34, "right": 0.33})
    stats = si.compute_coverage_statistics(s, now=NOW, th=TH)
    assert stats["articleCount"] == 3 and stats["publisherCount"] == 2
    assert stats["publisherDistribution"] == {"NPR": 2, "Fox News": 1}        # sorted desc by count
    assert stats["coverageGrowth"]["recent"] == 2 and stats["coverageGrowth"]["prior"] == 1
    assert stats["coverageGrowth"]["delta"] == 1
    assert stats["politicalDistribution"] == {"left": 0.33, "center": 0.34, "right": 0.33}
    assert stats["coverageVelocityPerDay"] > 0


# --------------------------------------------------------------------------- #
# Timeline — incl. the Perspective Expansion event (approved refinement)
# --------------------------------------------------------------------------- #
def test_timeline_events_and_perspective_expansion():
    # oldest->newest: center (first), left (join+expand+milestone#2), right (join+expand+latest)
    cov = [_cov("BBC News", 30, bucket="center"),
           _cov("NPR", 20, bucket="left"),
           _cov("Fox News", 5, bucket="right")]
    events = si.compute_timeline(_story(cov), th=TH)
    types = [e["type"] for e in events]
    assert types == ["first_report", "publisher_join", "perspective_expansion", "milestone",
                     "publisher_join", "perspective_expansion", "latest"]
    # chronological
    assert [e["date"] for e in events] == sorted(e["date"] for e in events)
    persp = [e for e in events if e["type"] == "perspective_expansion"]
    assert [e["perspective"] for e in persp] == ["left", "right"]
    assert next(e for e in events if e["type"] == "milestone")["count"] == 2


def test_timeline_single_article_is_just_first_report():
    events = si.compute_timeline(_story([_cov("NPR", 1)]), th=TH)
    assert [e["type"] for e in events] == ["first_report"]     # no "latest" for a lone article


# --------------------------------------------------------------------------- #
# New since last visit
# --------------------------------------------------------------------------- #
def _nsv_story():
    return _story([_cov("NPR", 48, bucket="center", url="https://npr.example/a"),
                   _cov("Fox News", 10, bucket="right", url="https://fox.example/b"),
                   _cov("BBC News", 2, bucket="left", url="https://bbc.example/c")])


def test_new_since_last_visit_from_reads():
    reads = [{"canonicalUrl": "https://npr.example/a", "observedAt": _iso(40)}]   # read A, 40h ago
    nsv = si.compute_new_since_last_visit(_nsv_story(), reads, now=NOW)
    assert nsv["lastVisited"] == _iso(40) and nsv["lastUpdated"] == _iso(2)
    assert nsv["count"] == 2                                   # B and C published after the baseline
    assert nsv["publishers"] == ["BBC News", "Fox News"]
    assert nsv["perspectives"] == ["left", "right"]            # ordered left, center, right
    assert {a["url"] for a in nsv["articles"]} == {"https://fox.example/b", "https://bbc.example/c"}


def test_new_since_last_visit_empty_without_reads():
    empty = si.compute_new_since_last_visit(_nsv_story(), None, now=NOW)
    assert empty["count"] == 0 and empty["lastVisited"] is None and empty["articles"] == []
    # a read that doesn't belong to this story is ignored (no baseline)
    other = si.compute_new_since_last_visit(
        _nsv_story(), [{"canonicalUrl": "https://elsewhere.example/z", "observedAt": _iso(1)}], now=NOW)
    assert other["count"] == 0 and other["lastVisited"] is None


# --------------------------------------------------------------------------- #
# Alerts
# --------------------------------------------------------------------------- #
def test_alerts_reflect_new_coverage_and_state():
    s = _nsv_story()
    reads = [{"canonicalUrl": "https://npr.example/a", "observedAt": _iso(40)}]
    f = si.compute_freshness(s, now=NOW, th=TH)
    m = si.compute_momentum(s, now=NOW, th=TH)
    lc = si.compute_lifecycle(s, f, m, now=NOW, th=TH)
    nsv = si.compute_new_since_last_visit(s, reads, now=NOW)
    alerts = si.compute_alerts(s, f, lc, m, nsv, th=TH)
    types = {a["type"] for a in alerts}
    assert "new_publisher" in types and "new_perspective" in types
    assert "coverage_doubled" in types                        # recent(2) >= 2*prior(1)


def test_alerts_archived_story():
    s = _story([_cov("NPR", 200), _cov("Fox News", 210)])
    f = si.compute_freshness(s, now=NOW, th=TH)
    m = si.compute_momentum(s, now=NOW, th=TH)
    lc = si.compute_lifecycle(s, f, m, now=NOW, th=TH)
    nsv = si.compute_new_since_last_visit(s, None, now=NOW)
    assert any(a["type"] == "became_archived" for a in si.compute_alerts(s, f, lc, m, nsv, th=TH))


# --------------------------------------------------------------------------- #
# Assembled outputs
# --------------------------------------------------------------------------- #
def test_compute_summary_is_lightweight():
    s = _story([_cov("NPR", 0.5), _cov("Fox News", 1.0), _cov("BBC News", 2.0)])
    summ = si.compute_summary(s, now=NOW)
    assert set(summ) == {"freshness", "lifecycle"}
    assert set(summ["freshness"]) == {"band", "score"}        # no coverage/timeline/reads in the badge
    assert summ["freshness"]["band"] == "Breaking"


def test_compute_intelligence_full_shape():
    s = _nsv_story()
    reads = [{"canonicalUrl": "https://npr.example/a", "observedAt": _iso(40)}]
    intel = si.compute_intelligence(s, reads=reads, now=NOW)
    for k in ("storyId", "freshness", "lifecycle", "momentum", "coverageStatistics", "timeline",
              "newSinceLastVisit", "alerts", "lastVisited", "lastUpdated", "diagnostics"):
        assert k in intel
    assert intel["storyId"] == "st_test"
    assert intel["lastVisited"] == intel["newSinceLastVisit"]["lastVisited"]
    assert intel["lastUpdated"] == intel["newSinceLastVisit"]["lastUpdated"]
    d = intel["diagnostics"]
    assert d["coverageCount"] == 3 and d["timelineEvents"] == len(intel["timeline"])
    assert isinstance(d["computeMs"], float)


# --------------------------------------------------------------------------- #
# Integration through the real Story Service + dependency-graph guard
# --------------------------------------------------------------------------- #
def _seed_store():
    st = store_mod.Store("sqlite://")

    def add(cu, pub, lean, title, days):
        st.upsert_feed_article(
            canonical_url=cu, url=cu, publisher=pub, source_publisher=pub, title=title,
            description="context", body=None, published_at=(NOW - timedelta(days=days)).isoformat(),
            source_feed="feed://x",
            scored={"article_id": cu, "outlet": pub, "category": "Politics", "lean": lean, "title": title})

    add("https://npr.org/s1", "NPR", -1.0, "Senate passes the funding bill after debate", 2)
    add("https://fox.com/s2", "Fox News", 1.5, "Senate passes funding bill averting shutdown", 1)
    add("https://bbc.com/s3", "BBC News", 0.0, "US Senate passes funding bill to avert shutdown", 0)
    return st


def test_intelligence_for_via_story_service():
    st = _seed_store()
    story = ss.cluster_from_store(st)[0]
    intel = si.intelligence_for(st, story["id"], now=NOW)
    assert intel is not None and intel["storyId"] == story["id"]
    assert intel["coverageStatistics"]["articleCount"] == story["totalCoverage"]
    assert si.intelligence_for(st, "st_does_not_exist", now=NOW) is None


def test_dependency_graph_service_does_not_import_intelligence():
    """Story Intelligence consumes Story Service, never the reverse."""
    src = (ROOT / "examples" / "story_service.py").read_text()
    assert "import story_intelligence" not in src
