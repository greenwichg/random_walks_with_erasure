"""Discover & Stories — a product-layer exploration surface over the RSS ``FeedArticle`` catalog.

Read-only and **additive**: it reshapes ``FeedArticle`` rows into the *existing* Article / Story JSON
contracts (the same shapes the web app already renders) and clusters them into news events with a
deterministic, dependency-free algorithm (no LLM). It does **not** touch the recommender, its
ranking / scoring / selection, the health report, personalization, or any protected module — Discover
and Stories are a separate browse surface that happens to read the same catalog.

Reuse: the Article serialization reuses the engine's own ``_prettify`` / ``_lean_bucket`` helpers so a
Discover/Story article is bucketed exactly like a recommendation. The article ``id`` is the canonical
publisher URL, so the existing "record opened → open the real article" Read flow works unchanged.
"""

from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone
from typing import Optional

import api_server as engine   # reuse the serializer helpers _prettify / _lean_bucket (no algorithm)

# EmotionShare shape the web `Article` expects; a neutral default when the feed carries no emotion.
_NEUTRAL_EMOTION = {"fear": 0.0, "outrage": 0.0, "analysis": 0.0, "positive": 0.0, "neutral": 1.0}
_REGISTERS = {"reporting", "opinion", "mixed"}

# Small English stop-list for title-similarity clustering — enough to keep function words from
# inflating overlap without a stemming dependency.
_STOPWORDS = frozenset("""
a an and the of to in on for with from by at as is are was were be been being this that these those
it its his her their our your my we you they he she who what when where why how than then so but or
not no nor into over under after before amid amto about says say said new latest live update updates
""".split())


# --------------------------------------------------------------------------- #
# FeedArticle -> the canonical Article JSON (same shape recommendations serialise to).
# --------------------------------------------------------------------------- #
def _num(x, default: float = 0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _absolute_url(u: "str | None") -> str:
    """Only a real, absolute publisher URL is ever emitted — never a relative/hostless value that a
    browser would resolve against the app's own origin (that was the Read-opens-the-app-origin bug)."""
    s = (u or "").strip()
    return s if s[:7].lower() == "http://" or s[:8].lower() == "https://" else ""


def _emotion(scored: dict) -> dict:
    e = scored.get("emotion")
    if isinstance(e, dict) and e:
        return {k: _num(e.get(k), 0.0) for k in _NEUTRAL_EMOTION}
    return dict(_NEUTRAL_EMOTION)


def _register(scored: dict) -> str:
    r = str(scored.get("register") or "").strip().lower()
    return r if r in _REGISTERS else "reporting"


def _reading_minutes(row: dict) -> int:
    text = row.get("body") or row.get("description") or ""
    return max(1, min(20, round(len(text.split()) / 220) or 2))     # ~220 wpm, clamped


def feed_article_to_article(row: dict) -> dict:
    """One ``FeedArticle`` row -> the canonical Article dict, carrying the **real** publisher URL and
    publication time. ``id`` == the canonical URL so the existing Read flow opens the article."""
    scored = row.get("scored") or {}
    lean = _num(scored.get("lean"), 0.0)
    outlet = row.get("publisher") or scored.get("outlet") or "Unknown"
    emo = _emotion(scored)
    topic = scored.get("category") or ""
    url = _absolute_url(row.get("url") or row.get("canonicalUrl"))
    return {
        "id": row.get("canonicalUrl") or url,
        "headline": row.get("title") or scored.get("title") or "(untitled)",
        "publisher": engine._prettify(outlet),
        "publisherLean": lean,
        "topic": engine._prettify(topic) if topic else "General",
        "url": url,
        "lean": lean,
        "leanBucket": engine._lean_bucket(lean),
        "confidence": _num(scored.get("selective"), 0.7),
        "emotion": emo,
        "dominantEmotion": max(emo, key=emo.get),
        "register": _register(scored),
        "description": row.get("description") or "",
        "publishedAt": row.get("publishedAt") or row.get("fetchedAt") or "",
        "readingMinutes": _reading_minutes(row),
    }


# --------------------------------------------------------------------------- #
# Discover — the latest catalog articles, with topic / publisher / lean filters + facets.
# --------------------------------------------------------------------------- #
_LEANS = {"left", "center", "right"}


def list_discover(store_, *, topic: Optional[str] = None, publisher: Optional[str] = None,
                  lean: Optional[str] = None, limit: int = 60, max_scan: int = 2000) -> dict:
    """Latest FeedArticles as Article dicts, newest first, with optional facet filters. Returns
    ``{"articles": [...], "topics": [...], "publishers": [...]}`` — facets computed over the whole
    catalog so the filter dropdowns stay stable as filters are applied.

    Backed by the **shared** ``store.search_feed_articles`` path (one filtering implementation for
    Discover and Search — no duplicated filter/sort code). Facets come from the store's distinct
    publisher/category values, prettified exactly as before. ``max_scan`` is retained for backward
    compatibility (the SQL path needs no scan bound)."""
    from pagination import OffsetPagination

    def _f(v):     # drop the sentinel "all" and empties
        return v if v and v != "all" else None

    rows, _total = store_.search_feed_articles(
        publisher=_f(publisher), topic=_f(topic), lean=_f(lean), sort="newest",
        pagination=OffsetPagination.from_params(limit, 0))
    articles = [feed_article_to_article(r) for r in rows]
    facets = store_.feed_article_facets()
    topics = sorted({engine._prettify(t) for t in facets["topics"] if t})
    publishers = sorted({engine._prettify(p) for p in facets["publishers"] if p})
    return {"articles": articles, "topics": topics, "publishers": publishers}


# --------------------------------------------------------------------------- #
# Stories — deterministic event clustering (topic + title similarity + publisher + time).
# --------------------------------------------------------------------------- #
def _title_tokens(title: str) -> frozenset:
    toks = re.findall(r"[a-z0-9]+", (title or "").lower())
    return frozenset(t for t in toks if len(t) > 2 and t not in _STOPWORDS)


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b) if inter else 0.0


def _parse_time(iso: str) -> Optional[datetime]:
    s = (iso or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _within_window(a: Optional[datetime], b: Optional[datetime], days: float) -> bool:
    if a is None or b is None:
        return True                          # missing timestamps don't block a title match
    return abs((a - b).total_seconds()) <= days * 86400.0


class _DSU:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)   # attach to the lower index -> deterministic roots


def _distribution(members: list) -> dict:
    """L/C/R distribution over **distinct publishers** (one vote per outlet), normalised to sum 1."""
    by_pub = {}
    for m in members:
        by_pub.setdefault(m["art"]["publisher"], m["art"]["leanBucket"])
    counts = {"left": 0, "center": 0, "right": 0}
    for bucket in by_pub.values():
        counts[bucket] = counts.get(bucket, 0) + 1
    total = sum(counts.values()) or 1
    return {k: counts[k] / total for k in ("left", "center", "right")}


def _blindspot(dist: dict) -> Optional[str]:
    """The under-covered side of an event: a spectrum bucket with no publishers while another side
    has real coverage. ``None`` when coverage is broad. Deterministic (left < center < right)."""
    empties = [k for k in ("left", "center", "right") if dist[k] == 0.0]
    covered = [k for k in ("left", "center", "right") if dist[k] > 0.0]
    if empties and len(covered) >= 1 and max(dist.values()) >= 0.5:
        return empties[0]
    return None


def _coverage(members: list) -> list:
    """One StoryCoverage entry per article, newest first."""
    out = []
    for m in sorted(members, key=lambda m: (m["art"]["publishedAt"] or "", m["art"]["id"]), reverse=True):
        a = m["art"]
        out.append({
            "publisher": a["publisher"], "headline": a["headline"], "lean": a["lean"],
            "leanBucket": a["leanBucket"], "register": a["register"], "emotion": a["emotion"],
            "url": a["url"], "publishedAt": a["publishedAt"],
        })
    return out


def _story_id(members: list) -> str:
    key = "|".join(sorted(m["art"]["id"] for m in members))
    return "st_" + hashlib.md5(key.encode("utf-8")).hexdigest()[:16]


def _mode_topic(members: list) -> str:
    counts: dict = {}
    for m in members:
        t = m["art"]["topic"]
        counts[t] = counts.get(t, 0) + 1
    # most frequent topic; alphabetical tiebreak for determinism
    return sorted(counts, key=lambda t: (-counts[t], t))[0] if counts else "General"


def _build_story(members: list) -> dict:
    times = [m["time"] for m in members if m["time"] is not None]
    earliest = min(times).isoformat() if times else ""
    latest = max(times).isoformat() if times else ""
    # Representative = earliest-published article (deterministic); its headline titles the event.
    rep = min(members, key=lambda m: (m["art"]["publishedAt"] or "~", m["art"]["id"]))["art"]
    dist = _distribution(members)
    publishers = sorted({m["art"]["publisher"] for m in members})
    timeline = []
    if earliest:
        timeline.append({"date": earliest, "label": "First report"})
    if latest and latest != earliest:
        timeline.append({"date": latest, "label": "Latest"})
    return {
        "id": _story_id(members),
        "title": rep["headline"],
        "summary": rep["description"] or f"{len(publishers)} publishers covering {rep['topic'].lower()}.",
        "topic": _mode_topic(members),
        "updatedAt": latest or rep["publishedAt"],
        "totalCoverage": len(members),          # articles
        "publisherCount": len(publishers),      # distinct outlets (additive; UI also derives it)
        "earliest": earliest,
        "latest": latest,
        "distribution": dist,
        "coverage": _coverage(members),
        "timeline": timeline,
        "blindspotSide": _blindspot(dist),
    }


def cluster_stories(store_, *, min_articles: int = 2, min_publishers: int = 2, sim: float = 0.28,
                    window_days: float = 6.0, max_scan: int = 2000) -> list:
    """Cluster the catalog into news events. Two articles join the same event when their titles are
    similar enough (token Jaccard ≥ ``sim``) and published within ``window_days`` of each other; a
    story needs ≥ ``min_articles`` from ≥ ``min_publishers`` distinct outlets. Fully deterministic:
    same catalog -> same clusters, ids, and order. No LLM, no external deps."""
    rows = store_.list_feed_articles(limit=max_scan)
    items = []
    for r in rows:
        a = feed_article_to_article(r)
        items.append({"art": a, "tokens": _title_tokens(a["headline"]), "time": _parse_time(a["publishedAt"])})

    n = len(items)
    dsu = _DSU(n)
    for i in range(n):
        ti = items[i]
        if not ti["tokens"]:
            continue
        for j in range(i + 1, n):
            tj = items[j]
            if tj["tokens"] and _jaccard(ti["tokens"], tj["tokens"]) >= sim \
                    and _within_window(ti["time"], tj["time"], window_days):
                dsu.union(i, j)

    groups: dict = {}
    for i in range(n):
        groups.setdefault(dsu.find(i), []).append(items[i])

    stories = []
    for members in groups.values():
        if len(members) < min_articles:
            continue
        if len({m["art"]["publisher"] for m in members}) < min_publishers:
            continue
        stories.append(_build_story(members))

    # Biggest, freshest events first: more publishers, then more articles, then most recent.
    stories.sort(key=lambda s: (s["publisherCount"], s["totalCoverage"], s["latest"]), reverse=True)
    return stories


def story_detail(store_, story_id: str, **kwargs) -> Optional[dict]:
    """Re-derive the clusters and return the one whose id matches (clustering is deterministic, so the
    id is stable for a given catalog). ``None`` when it no longer exists (the catalog changed)."""
    for s in cluster_stories(store_, **kwargs):
        if s["id"] == story_id:
            return s
    return None
