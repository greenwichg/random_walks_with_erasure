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

import math
from typing import Optional

import api_server as engine   # reuse the serializer helpers _prettify / _lean_bucket (no algorithm)
import media                  # centralised image + publisher-logo selection (additive, presentation-only)

# EmotionShare shape the web `Article` expects; a neutral default when the feed carries no emotion.
_NEUTRAL_EMOTION = {"fear": 0.0, "outrage": 0.0, "analysis": 0.0, "positive": 0.0, "neutral": 1.0}
_REGISTERS = {"reporting", "opinion", "mixed"}


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
    art = {
        "id": row.get("canonicalUrl") or url,
        "headline": row.get("title") or scored.get("title") or "(untitled)",
        "publisher": engine._prettify(outlet),
        "publisherLean": lean,
        # Commit R2: uncategorized stays "" (the UI hides the segment) — no synthesized "General"
        # that History renders blank while Discover/explanations present it as a real topic.
        "topic": engine._prettify(topic) if topic else "",
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
        # Location Intelligence Phase 0 — canonical publisher-level location (None omitted on the
        # wire via response_model_exclude_none; never fabricated).
        "country": row.get("country"),
        "language": row.get("language"),
    }
    # Additive media + publisher logo (centralised in media.py; all-null when the feed carried no image).
    art.update(media.pick_article_media(row))
    art.update(media.pick_best_logo(art["publisher"], url))
    return art


# --------------------------------------------------------------------------- #
# Discover — the latest catalog articles, with topic / publisher / lean filters + facets.
# --------------------------------------------------------------------------- #
_LEANS = {"left", "center", "right"}


def list_discover(store_, *, topic: Optional[str] = None, publisher: Optional[str] = None,
                  lean: Optional[str] = None, country: Optional[str] = None,
                  limit: int = 60, max_scan: int = 2000) -> dict:
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

    # Discover is the one surface that hides *provisional* (extension-created, not yet corroborated)
    # articles — Stories/Search/the corpus include them (Commit 18 lifecycle). Same shared SQL path.
    rows, _total = store_.search_feed_articles(
        publisher=_f(publisher), topic=_f(topic), lean=_f(lean), country=_f(country),
        sort="newest",
        pagination=OffsetPagination.from_params(limit, 0), include_provisional=False)
    articles = [feed_article_to_article(r) for r in rows]
    facets = store_.feed_article_facets(include_provisional=False)
    topics = sorted({engine._prettify(t) for t in facets["topics"] if t})
    publishers = sorted({engine._prettify(p) for p in facets["publishers"] if p})
    return {"articles": articles, "topics": topics, "publishers": publishers}


# --------------------------------------------------------------------------- #
# Stories — delegated to the Story Service (the single owner of Story construction).
# The clustering algorithm lives in ``clustering`` and Story construction in ``story_service``; these
# stay as thin backward-compatible wrappers. ``story_service`` is imported lazily because it imports
# THIS module for ``feed_article_to_article`` — the lazy import breaks the cycle.
# --------------------------------------------------------------------------- #
def cluster_stories(store_, **kwargs) -> list:
    """Backward-compatible bare Story list — delegates to ``story_service.cluster_from_store``. No
    clustering or Story construction lives here anymore (one implementation, owned by the service)."""
    import story_service
    return story_service.cluster_from_store(store_, **kwargs)


def story_detail(store_, story_id: str, **kwargs) -> Optional[dict]:
    """Backward-compatible single-Story lookup — delegates to ``story_service.get_story``."""
    import story_service
    return story_service.get_story(store_, story_id, **kwargs)
