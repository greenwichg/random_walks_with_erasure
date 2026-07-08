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
    """L/C/R distribution over **distinct publishers** (one vote per outlet), normalised to sum 1."""
    by_pub = {}
    for m in members:
        by_pub.setdefault(m["publisher"], m["leanBucket"])
    counts = {"left": 0, "center": 0, "right": 0}
    for bucket in by_pub.values():
        counts[bucket] = counts.get(bucket, 0) + 1
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
    return {
        "id": _story_id(members),
        "title": rep["headline"],
        "summary": rep["description"] or f"{len(publishers)} publishers covering {rep['topic'].lower()}.",
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
    }


def build_stories(rows: list, *, min_articles: int = 2, min_publishers: int = 2,
                  sim: float = clustering.DEFAULT_SIM,
                  window_days: float = clustering.DEFAULT_WINDOW_DAYS) -> list:
    """Cluster FeedArticle rows into Story objects (the pure builder). Keeps clusters with
    ≥ ``min_articles`` from ≥ ``min_publishers`` distinct outlets; sorted biggest+freshest first.
    Deterministic: same rows → same stories, ids, and order."""
    arts = [discover.feed_article_to_article(r) for r in rows]
    groups = clustering.cluster(
        arts, tokens=lambda a: clustering.title_tokens(a["headline"]),
        time=lambda a: clustering.parse_time(a["publishedAt"]), sim=sim, window_days=window_days)
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
def _fetch(store_, *, topic=None, date_from=None, date_to=None, max_scan=2000) -> list:
    """A bounded, pre-filtered article set to cluster (topic/date narrow it in SQL first)."""
    rows, _total = store_.search_feed_articles(
        topic=topic, date_from=date_from, date_to=date_to, sort="newest",
        pagination=OffsetPagination.from_params(max_scan, 0, max_limit=max_scan))
    return rows


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
                       window_days: float = clustering.DEFAULT_WINDOW_DAYS, max_scan: int = 2000) -> list:
    """The bare Story list for the whole catalog (what ``discover.cluster_stories`` delegates to)."""
    return build_stories(_fetch(store_, max_scan=max_scan), min_articles=min_articles,
                         min_publishers=min_publishers, sim=sim, window_days=window_days)


def list_stories(store_, *, topic=None, publisher=None, lean=None, date_from=None, date_to=None,
                 sort: str = "top", limit: int = 30, offset: int = 0, min_articles: int = 2,
                 min_publishers: int = 2, max_scan: int = 2000, debug: bool = False) -> dict:
    """The paginated, filtered Story envelope Discover + Stories consume:
    ``{stories, total, page, pageSize, hasMore, remainingPages, sort}`` (+ ``clusterMs`` +
    ``diagnostics`` when ``debug``). topic/date are pre-filtered in SQL; publisher/lean are coverage
    post-filters on the built stories."""
    sort = sort if sort in SORTS else "top"
    pg = OffsetPagination.from_params(limit, offset)
    t0 = _time.perf_counter()
    stories = build_stories(_fetch(store_, topic=topic, date_from=date_from, date_to=date_to,
                                   max_scan=max_scan), min_articles=min_articles,
                            min_publishers=min_publishers)
    cluster_ms = round((_time.perf_counter() - t0) * 1000.0, 2)

    if publisher and publisher.strip():
        want = publisher.strip().lower()
        stories = [s for s in stories if want in {p.lower() for p in s["publishers"]}]
    if lean in ("left", "center", "right"):
        stories = [s for s in stories if s["distribution"][lean] > 0.0]

    stories = _sort_stories(stories, sort)
    total = len(stories)
    page = stories[pg.offset: pg.offset + pg.limit] if pg.limit > 0 else stories
    out = {"stories": page, "total": total, "sort": sort, **pg.meta(total)}
    if debug:
        out["clusterMs"] = cluster_ms
        out["diagnostics"] = _diagnostics(stories, cluster_ms)
    return out


def get_story(store_, story_id: str, *, min_articles: int = 2, min_publishers: int = 2,
              max_scan: int = 2000, **kwargs) -> Optional[dict]:
    """One Story by id — re-derive the deterministic clusters and return the match (its stable,
    anchored id means the lookup survives new coverage of the same event). ``None`` if it no longer
    exists (the catalog changed enough that the event dissolved)."""
    for s in cluster_from_store(store_, min_articles=min_articles, min_publishers=min_publishers,
                                max_scan=max_scan):
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


def diagnostics(store_, *, max_scan: int = 2000) -> dict:
    """Story-layer diagnostics for operators: counts, average + largest cluster, build time, and the
    cluster-size distribution."""
    t0 = _time.perf_counter()
    stories = cluster_from_store(store_, max_scan=max_scan)
    return _diagnostics(stories, round((_time.perf_counter() - t0) * 1000.0, 2))
