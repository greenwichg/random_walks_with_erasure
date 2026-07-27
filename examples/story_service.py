"""story_service.py — the single owner of Story construction.

Orchestrates: fetch FeedArticles → cluster them with the reusable ``clustering`` primitive → build
``Story`` objects → filter / sort / paginate → diagnostics. It **owns Story construction**; Discover
and Stories both consume it and never build a Story independently. It reuses
``discover.feed_article_to_article`` for article serialization (so a Story's coverage articles are the
exact Article shape, with the identical Read flow) and ``store.search_feed_articles`` for the
index-backed pre-filter. It does not implement clustering (that is ``clustering.py``) and never touches
the recommendation engine.

Story IDs are **stable across rebuilds as a cluster evolves**: the id is anchored to the cluster's
representative (earliest-published) article's canonical URL, so as more coverage of the same event
arrives the id does not change (unlike hashing all members, which would churn on every new article).

Future AI summarization + image enrichment build on this service: every Story already carries the
nullable image contract ``{image, imageSource, imageAttribution}``, so Commit 8 can populate it without
an API change.
"""

from __future__ import annotations

import hashlib
import os
import threading
import weakref
import time as _time
from typing import Optional

import clustering                 # the deterministic union-find Jaccard primitive (algorithm only)
import discover                   # feed_article_to_article — the shared Article serializer (Read flow)
import media                      # centralised hero-image selection (additive; no clustering change)
from pagination import OffsetPagination

SORTS = ("top", "latest", "oldest", "publishers")


# --------------------------------------------------------------------------- #
# Story construction (the single implementation).
# --------------------------------------------------------------------------- #
def _story_id(members: list) -> str:
    """A stable id anchored to the representative (earliest-published) article's canonical URL, so the
    id survives rebuilds as the cluster gains coverage of the same event."""
    rep = min(members, key=lambda a: (a.get("publishedAt") or "~", a.get("id") or ""))
    anchor = rep.get("id") or rep.get("url") or ""
    return "st_" + hashlib.sha1(anchor.encode("utf-8", "replace")).hexdigest()[:16]


def _distribution(members: list) -> dict:
    """L/C/R distribution over **distinct RATED publishers** (one vote per outlet), normalised to
    sum 1. An unrated outlet (leanBucket null — L2.2) is real coverage but casts no vote: counting
    it as centre would fabricate a lean nobody rated. All-unrated -> all-zero (blindspot: None)."""
    by_pub = {}
    for m in members:
        by_pub.setdefault(m["publisher"], m["leanBucket"])
    counts = {"left": 0, "center": 0, "right": 0}
    for bucket in by_pub.values():
        if bucket in counts:
            counts[bucket] += 1
    total = sum(counts.values()) or 1
    return {k: counts[k] / total for k in ("left", "center", "right")}


def _blindspot(dist: dict) -> Optional[str]:
    """The under-covered side of an event — a bucket with no publishers while another side is well
    covered. Deterministic (left < center < right). A coverage gap, not an opinion metric."""
    empties = [k for k in ("left", "center", "right") if dist[k] == 0.0]
    covered = [k for k in ("left", "center", "right") if dist[k] > 0.0]
    if empties and len(covered) >= 1 and max(dist.values()) >= 0.5:
        return empties[0]
    return None


def _coverage(members: list) -> list:
    """One coverage entry per article, newest first — the canonical article list (carries the URL)."""
    out = []
    for m in sorted(members, key=lambda m: (m["publishedAt"] or "", m["id"]), reverse=True):
        out.append({
            "publisher": m["publisher"], "headline": m["headline"], "lean": m["lean"],
            "leanBucket": m["leanBucket"], "register": m["register"], "emotion": m["emotion"],
            "url": m["url"], "publishedAt": m["publishedAt"],
        })
    return out


def _mode_topic(members: list) -> str:
    counts: dict = {}
    for m in members:
        counts[m["topic"]] = counts.get(m["topic"], 0) + 1
    return sorted(counts, key=lambda t: (-counts[t], t))[0] if counts else "General"


def _build_story(members: list) -> dict:
    """Build one Story object from a cluster of Article dicts. Coverage only — no opinion metrics."""
    times = [clustering.parse_time(m["publishedAt"]) for m in members]
    times = [t for t in times if t is not None]
    earliest = min(times).isoformat() if times else ""
    latest = max(times).isoformat() if times else ""
    span_hours = round((max(times) - min(times)).total_seconds() / 3600.0, 2) if len(times) >= 2 else 0.0
    # Representative = earliest-published article (deterministic); its headline titles the event.
    rep = min(members, key=lambda m: (m["publishedAt"] or "~", m["id"]))
    dist = _distribution(members)
    publishers = sorted({m["publisher"] for m in members})
    total = len(members)
    # Optional hero image (additive; centralised in media.py): representative → best → most recent →
    # None. This is the only media touch here — the clustering/filter/sort/pagination logic is unchanged.
    hero = media.pick_story_hero(members, representative=rep) or {}
    timeline = []
    if earliest:
        timeline.append({"date": earliest, "label": "First report"})
    if latest and latest != earliest:
        timeline.append({"date": latest, "label": "Latest"})
    votes = _country_votes(members)
    consensus = _event_consensus(members, votes)
    coherence, located_members = _geo_coherence(members, votes)
    return {
        "id": _story_id(members),
        "title": rep["headline"],
        # Fallback summary handles an EMPTY topic (uncategorized stays "" by design) — the
        # naive interpolation shipped "18 publishers covering ." with an orphaned period.
        "summary": rep["description"] or (
            f"{len(publishers)} publishers covering {rep['topic'].lower()}." if rep["topic"]
            else f"{len(publishers)} publishers covering this story."),
        # Hero image contract (nullable) — selected from the cluster's articles' RSS media.
        "image": hero.get("image"),
        "imageWidth": hero.get("imageWidth"),
        "imageHeight": hero.get("imageHeight"),
        "imageMimeType": hero.get("imageMimeType"),
        "imageSource": hero.get("imageSource"),
        "imageAttribution": hero.get("imageAttribution"),
        "topic": _mode_topic(members),
        "updatedAt": latest or rep["publishedAt"],
        "totalCoverage": total,                 # article count
        "publisherCount": len(publishers),      # distinct outlets
        "publishers": publishers,               # explicit publisher list
        "publisherDiversity": round(len(publishers) / total, 3) if total else 0.0,
        "earliest": earliest,
        "latest": latest,
        "firstPublished": earliest,
        "latestUpdate": latest,
        "newest": latest,
        "oldest": earliest,
        "timeSpanHours": span_hours,
        "distribution": dist,
        "coverage": _coverage(members),
        "timeline": timeline,
        "blindspotSide": _blindspot(dist),
        # Location Intelligence — the story's EVENT geography (counted facts, never guessed).
        # ``countries`` is what ?country= matches: the member-consensus leaders of the EVENT
        # dimension only — a story with no event-located members matches no country (it still
        # appears under "All"). Publisher homes are deliberately NOT a fallback here: they are a
        # PROVENANCE fact, preserved separately as ``publisherCountries`` for publisher
        # intelligence/analytics. All internal until a card consumes them (the response model
        # omits undeclared fields).
        "countries": consensus,
        "primaryCountry": consensus[0] if len(consensus) == 1 else None,
        "eventCountries": sorted({c for m in members for c in (m.get("eventCountries") or ())}),
        "publisherCountries": sorted({str(m["country"]).upper() for m in members
                                      if m.get("country")}),
        # Cluster-geography coherence (diagnostic; internal like the fields above until a surface
        # consumes it). Measured on the INCIDENT dimension only — see _geo_coherence.
        "geoCoherence": coherence,              # None = nothing located, NOT zero
        "locatedMembers": located_members,
        "countryVotes": dict(sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))),
    }


def _member_countries(m: dict) -> set:
    """One member's EVENT countries, upper-cased. The INCIDENT's location — never the publisher's
    home (``m["country"]``), which is provenance and lives separately as ``publisherCountries``. A
    US outlet reporting an incident in India votes IN here, which is the whole point."""
    return {str(c).upper() for c in (m.get("eventCountries") or ()) if c}


def _country_votes(members: list) -> dict:
    """country -> how many MEMBERS were located there. One vote per member per country, so a
    prolific outlet cannot outvote the rest by filing more copy. Unlocated members abstain — they
    are not evidence either way."""
    votes: dict = {}
    for m in members:
        for c in _member_countries(m):
            votes[c] = votes.get(c, 0) + 1
    return votes


def _event_consensus(members: list, votes: Optional[dict] = None) -> list:
    """The story's event countries by member consensus: each event-located member votes for its
    (already dominance-filtered) event countries; the plurality leader(s) win — ties are kept,
    because a genuinely two-country event IS in both places. No event-located members → no
    countries (fail-honest: publisher homes never substitute for where an event happened)."""
    votes = _country_votes(members) if votes is None else votes
    if not votes:
        return []
    top = max(votes.values())
    return sorted(c for c, n in votes.items() if n == top)


def _geo_coherence(members: list, votes: dict) -> "tuple[Optional[float], int]":
    """``(coherence, locatedMembers)`` — the share of LOCATED members backing the single
    strongest incident country. ``None`` when nothing is located: absence of evidence is not
    incoherence, and a story nobody located must not be scored as though it were.

    Deliberately measured against the TOP vote, not against the consensus set. ``_event_consensus``
    keeps ties, so a cluster whose members each name a *different* country produces an n-way tie in
    which every member "backs a winner" — scoring maximal disagreement as perfect agreement, the
    exact inverse of the truth. Against the top vote, four members in four countries score 0.25.

    The distinction that makes this useful: a member located in BOTH places of a genuine two-country
    event votes for both and lifts the top count, so real border/multi-site events stay coherent —
    while members that each name a different single place do not.

    This measures whether a cluster's members are *about the same place*, which turns out to be a
    sharp detector of FALSE MERGES rather than of geography errors. Measured in production: a
    105-publisher cluster titled "Thune on Trump's Canada tariffs" whose members were located
    across CN, CU, DJ, GB, IL, IR, OM, PH, SA, SG, US and YE — articles with nothing to do with
    each other, merged on shared title tokens. ``publisherDiversity`` rated that cluster healthy
    (0.53); this does not.

    Crucially it is a MEMBER-AGREEMENT measure, not a country count. A genuine multi-country story
    scores high: an explainer citing fires in AU/ES/FR/GB/SK/US is coherent when its members all
    mention the same lead country, however many others each adds. A false merge scores low because
    its members name *different* places."""
    located = sum(1 for m in members if _member_countries(m))
    if not located or not votes:
        return None, located
    return round(max(votes.values()) / located, 3), located


def min_shared_tokens() -> int:
    """Distinctive tokens two headlines must share to be considered the same event. Tunable without
    a deploy because the right value is an empirical question about the live headline mix — see
    ``examples/audit_clustering_change.py``, which measures a candidate against the real catalog."""
    return _env_int("RWE_CLUSTER_MIN_SHARED", clustering.MIN_SHARED_TOKENS)


def min_title_tokens() -> int:
    return _env_int("RWE_CLUSTER_MIN_TOKENS", clustering.MIN_TITLE_TOKENS)


def build_stories(rows: list, *, min_articles: int = 2, min_publishers: int = 2,
                  sim: float = clustering.DEFAULT_SIM,
                  window_days: float = clustering.DEFAULT_WINDOW_DAYS,
                  min_shared: Optional[int] = None,
                  min_tokens: Optional[int] = None) -> list:
    """Cluster FeedArticle rows into Story objects (the pure builder). Keeps clusters with
    ≥ ``min_articles`` from ≥ ``min_publishers`` distinct outlets; sorted biggest+freshest first.
    Deterministic: same rows → same stories, ids, and order."""
    arts = [discover.feed_article_to_article(r) for r in rows]
    groups = clustering.cluster(
        arts, tokens=lambda a: clustering.title_tokens(a["headline"]),
        time=lambda a: clustering.parse_time(a["publishedAt"]), sim=sim, window_days=window_days,
        min_shared=min_shared_tokens() if min_shared is None else min_shared,
        min_tokens=min_title_tokens() if min_tokens is None else min_tokens)
    stories = []
    for idxs in groups:
        members = [arts[i] for i in idxs]
        if len(members) < min_articles:
            continue
        if len({m["publisher"] for m in members}) < min_publishers:
            continue
        stories.append(_build_story(members))
    stories.sort(key=lambda s: (s["publisherCount"], s["totalCoverage"], s["latest"] or ""), reverse=True)
    return stories


# --------------------------------------------------------------------------- #
# Store-backed orchestration — the surface Discover + Stories consume.
# --------------------------------------------------------------------------- #
def _env_float(name: str, default: float) -> float:
    """A positive float from the environment, else the default. Junk never widens or narrows the
    window silently — it falls back."""
    try:
        v = float(os.environ.get(name, "").strip())
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name, "").strip())
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


def scan_days() -> float:
    """How many days back the clustering candidate set reaches. Defaults to the clustering window
    itself — a threshold that pairs articles up to ``window_days`` apart is meaningless if the
    candidate set spans less than that."""
    return _env_float("RWE_STORIES_SCAN_DAYS", clustering.DEFAULT_WINDOW_DAYS)


def max_scan_default() -> int:
    """Backstop on candidate-set SIZE. This is a memory guard, NOT the relevance rule — the window
    above decides what is in scope. It sits far above a normal window so it only ever engages if
    ingestion volume spikes far beyond projections."""
    return _env_int("RWE_STORIES_MAX_SCAN", 60000)


def _window_start(now=None) -> str:
    from datetime import datetime, timedelta, timezone
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=scan_days())).isoformat()


def _fetch(store_, *, topic=None, date_from=None, date_to=None, max_scan=None) -> list:
    """The clustering candidate set: a TIME-bounded, pre-filtered article slice (topic/date narrow
    it in SQL first). Each row is annotated with its EVENT countries (one batched side-table
    lookup) so story construction can locate members by best-known location.

    The bound is a **time window**, not a row count. It used to be ``max_scan=2000`` rows ordered
    newest-first, which made story yield a function of ingestion RATE: every provider added shrank
    the hours those 2000 rows covered, so integrating more sources produced FEWER stories (measured:
    a 12.5-hour effective window against a 6-day clustering threshold, 89 stories from a
    12,790-article catalog). A caller-supplied ``date_from`` still wins — an explicit request for a
    date range is never silently narrowed."""
    if date_from is None:
        date_from = _window_start()
    cap = max_scan or max_scan_default()
    rows, _total = store_.search_feed_articles(
        topic=topic, date_from=date_from, date_to=date_to, sort="newest",
        pagination=OffsetPagination.from_params(cap, 0, max_limit=cap))
    events = store_.event_countries_for_urls([r.get("canonicalUrl") for r in rows])
    for r in rows:
        r["eventCountries"] = events.get(r.get("canonicalUrl"), [])
    return rows


# --------------------------------------------------------------------------- #
# Clustered-result cache.
#
# Clustering is the expensive step and its input only changes when the poller ingests (every
# RWE_POLL_INTERVAL, default 600 s), so recomputing it per request is pure waste. Filters, sort and
# pagination stay OUTSIDE the cache — they are cheap list operations over the cached clusters, so
# every filter combination is served from one cached build.
# --------------------------------------------------------------------------- #
# Keyed by the STORE OBJECT itself, weakly. Identity cannot collide (an ``id()`` in the key would:
# CPython reuses addresses after collection, so a dead store's clusters could be served to a new one
# allocated at the same address), and a store's cache is collected with the store.
_CACHE: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 16


def cache_ttl() -> float:
    """Seconds a clustered build stays servable. 0 disables the cache entirely."""
    try:
        v = float(os.environ.get("RWE_STORIES_CACHE_TTL", "").strip())
        return v if v >= 0 else 120.0
    except (TypeError, ValueError):
        return 120.0


def clear_cache() -> None:
    """Drop every cached build (tests, and any caller that has just mutated the catalog)."""
    with _CACHE_LOCK:
        _CACHE.clear()


_WARM_LOCK = threading.Lock()


def warm_cache(store_) -> Optional[int]:
    """Build and cache the default (unfiltered) view; returns the story count, or ``None`` if
    another warm was already in flight and this one stood down.

    Called by the poller right after it ingests, on the poller's own thread. Without this the FIRST
    reader after every poll pays the whole clustering cost — measured at 5.4 s in production, once
    per poll cycle, which on low traffic is a large share of requests. The rebuild is unavoidable
    (the catalog genuinely changed); paying it on the thread that caused the change, rather than on
    a reader's request, is the whole point.

    **Single-flight.** ``MultiSourcePoller`` runs one thread PER ADAPTER — eight of them can finish
    a cycle at once, and without this guard that is eight concurrent multi-second clustering runs on
    a small instance. A skipped warm is not a lost one: the winner's build covers the same catalog,
    and the next adapter to finish warms again.

    Warms the exact key ``/api/stories`` uses with no filters — filters, sort and pagination are
    applied outside the cache, so this one build serves every filter combination too."""
    if not _WARM_LOCK.acquire(blocking=False):
        return None
    try:
        return len(_cached_build(store_, topic=None, date_from=None, date_to=None, max_scan=None,
                                 min_articles=2, min_publishers=2))
    finally:
        _WARM_LOCK.release()


def _cached_build(store_, *, topic, date_from, date_to, max_scan, min_articles, min_publishers) -> list:
    """``build_stories(_fetch(...))`` behind a cache with TWO independent invalidation conditions,
    because either alone is wrong:

    * **A catalog fingerprint** ``(row count, newest fetched_at)`` is part of the KEY, so any
      catalog write immediately invalidates. A pure TTL cache would keep serving pre-ingest
      clusters — a reader could open a story link the list had just rendered and get a stale member
      set. A bare row COUNT is not enough either: a retention prune plus an ingest in the same
      interval leaves the count identical while the content differs entirely. Between polls
      (``RWE_POLL_INTERVAL``, default 600 s) the fingerprint is stable, so this is a long-lived
      cache in practice, not a permanently-cold one.
    * **TTL** bounds staleness on the other axis. ``date_from`` defaults to a rolling ``now −
      scan_days``, so a quiet catalog would otherwise pin an ever-older window.

    The store's identity is in the key too: two stores must never share a build. One process serves
    one database in production, but tests and any future multi-tenant caller would silently read
    each other's clusters without it."""
    def _build():
        return build_stories(_fetch(store_, topic=topic, date_from=date_from, date_to=date_to,
                                    max_scan=max_scan),
                             min_articles=min_articles, min_publishers=min_publishers)

    ttl = cache_ttl()
    if ttl <= 0:
        return _build()
    try:
        fingerprint = store_.catalog_fingerprint()
    except Exception:                       # a store without the fingerprint is simply uncached
        return _build()

    key = (topic, date_from, date_to, max_scan, min_articles, min_publishers, fingerprint)
    now = _time.time()
    with _CACHE_LOCK:
        entries = _CACHE.get(store_)
        hit = entries.get(key) if entries else None
        if hit is not None and (now - hit[0]) < ttl:
            return hit[1]

    built = _build()
    with _CACHE_LOCK:
        entries = _CACHE.setdefault(store_, {})
        # Bounded per store: evict oldest first rather than grow without limit across topics/dates.
        if len(entries) >= _CACHE_MAX:
            for stale in sorted(entries, key=lambda k: entries[k][0])[: len(entries) - _CACHE_MAX + 1]:
                entries.pop(stale, None)
        entries[key] = (_time.time(), built)
    return built


def _sort_stories(stories: list, sort: str) -> list:
    if sort == "latest":
        return sorted(stories, key=lambda s: (s["latest"] or "", s["id"]), reverse=True)
    if sort == "oldest":
        return sorted(stories, key=lambda s: (s["earliest"] or "~", s["id"]))
    if sort == "publishers":
        return sorted(stories, key=lambda s: (s["publisherCount"], s["totalCoverage"], s["latest"] or ""),
                      reverse=True)
    return stories       # "top" — build_stories already ordered biggest+freshest first


def cluster_from_store(store_, *, min_articles: int = 2, min_publishers: int = 2,
                       sim: float = clustering.DEFAULT_SIM,
                       window_days: float = clustering.DEFAULT_WINDOW_DAYS, max_scan: int = None) -> list:
    """The bare Story list for the current window (what ``discover.cluster_stories`` delegates to).
    Uncached on purpose: it takes ``sim``/``window_days`` overrides the cache key does not carry."""
    return build_stories(_fetch(store_, max_scan=max_scan), min_articles=min_articles,
                         min_publishers=min_publishers, sim=sim, window_days=window_days)


def list_stories(store_, *, topic=None, publisher=None, lean=None, country=None, blindspot=None,
                 date_from=None, date_to=None,
                 sort: str = "top", limit: int = 30, offset: int = 0, min_articles: int = 2,
                 min_publishers: int = 2, max_scan: int = None, debug: bool = False) -> dict:
    """The paginated, filtered Story envelope Discover + Stories consume:
    ``{stories, total, page, pageSize, hasMore, remainingPages, sort, countryFacets,
    blindspotFacets}`` (+ ``clusterMs`` + ``diagnostics`` when ``debug``). topic/date are
    pre-filtered in SQL; publisher/lean/country/blindspot are coverage post-filters on the built
    stories. ``country`` matches EVENT location only — the story's member-consensus event
    countries (``_event_consensus``); publisher homes never substitute, so an unlocated story
    appears under "All" and under no country. ``blindspot`` is the coverage-gap lens:
    ``"any"`` matches stories with a DETECTED gap (``blindspotSide`` set), a side matches that
    thin side exactly; ``blindspotSide`` None means balanced-OR-unknown (an all-unrated story
    casts no votes) and never matches — a gap is a counted finding, not a default. Both facet
    dicts are STORY counts under the other active filters, computed BEFORE their own filters and
    pagination — each picker's source of truth, so an option is only offered when selecting it
    returns ≥1 story."""
    sort = sort if sort in SORTS else "top"
    pg = OffsetPagination.from_params(limit, offset)
    t0 = _time.perf_counter()
    stories = _cached_build(store_, topic=topic, date_from=date_from, date_to=date_to,
                            max_scan=max_scan, min_articles=min_articles,
                            min_publishers=min_publishers)
    cluster_ms = round((_time.perf_counter() - t0) * 1000.0, 2)

    if publisher and publisher.strip():
        want = publisher.strip().lower()
        stories = [s for s in stories if want in {p.lower() for p in s["publishers"]}]
    if lean in ("left", "center", "right"):
        stories = [s for s in stories if s["distribution"][lean] > 0.0]
    # Story-level country + blindspot facets: counted after topic/publisher/lean narrowed the
    # set, before their own filters (standard faceting — a picker must not collapse to the
    # current selection) and before pagination.
    country_facets: dict = {}
    blindspot_facets: dict = {}
    for s in stories:
        for c in s["countries"]:
            country_facets[c] = country_facets.get(c, 0) + 1
        if s["blindspotSide"]:
            blindspot_facets[s["blindspotSide"]] = blindspot_facets.get(s["blindspotSide"], 0) + 1
    if country and country.strip():
        want = country.strip().upper()
        stories = [s for s in stories if want in s["countries"]]
    if blindspot == "any":
        stories = [s for s in stories if s["blindspotSide"]]
    elif blindspot in ("left", "center", "right"):
        stories = [s for s in stories if s["blindspotSide"] == blindspot]

    stories = _sort_stories(stories, sort)
    total = len(stories)
    page = stories[pg.offset: pg.offset + pg.limit] if pg.limit > 0 else stories
    out = {"stories": page, "total": total, "sort": sort, "countryFacets": country_facets,
           "blindspotFacets": blindspot_facets,
           **pg.meta(total)}
    if debug:
        out["clusterMs"] = cluster_ms
        out["diagnostics"] = _diagnostics(stories, cluster_ms)
    return out


def get_story(store_, story_id: str, *, min_articles: int = 2, min_publishers: int = 2,
              max_scan: int = None, **kwargs) -> Optional[dict]:
    """One Story by id — re-derive the deterministic clusters and return the match (its stable,
    anchored id means the lookup survives new coverage of the same event). ``None`` if it no longer
    exists (the catalog changed enough that the event dissolved).

    Shares the list's cached build, which matters twice: a detail page costs no extra clustering,
    and the two surfaces cannot disagree about which stories exist — a narrower scan here than in
    ``list_stories`` would 404 links the list had just rendered."""
    for s in _cached_build(store_, topic=None, date_from=None, date_to=None, max_scan=max_scan,
                           min_articles=min_articles, min_publishers=min_publishers):
        if s["id"] == story_id:
            return s
    return None


def _diagnostics(stories: list, cluster_ms: float) -> dict:
    sizes = sorted((s["totalCoverage"] for s in stories), reverse=True)
    dist: dict = {}
    for sz in sizes:
        dist[sz] = dist.get(sz, 0) + 1
    return {
        "storyCount": len(stories),
        "avgArticlesPerStory": round(sum(sizes) / len(sizes), 2) if sizes else 0.0,
        "largestStory": sizes[0] if sizes else 0,
        "clusterBuildMs": cluster_ms,
        "sizeDistribution": {str(k): v for k, v in sorted(dist.items())},
    }


def diagnostics(store_, *, max_scan: int = None) -> dict:
    """Story-layer diagnostics for operators: counts, average + largest cluster, build time, and the
    cluster-size distribution."""
    t0 = _time.perf_counter()
    stories = cluster_from_store(store_, max_scan=max_scan)
    return _diagnostics(stories, round((_time.perf_counter() - t0) * 1000.0, 2))
