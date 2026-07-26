"""Publisher Intelligence — the counted, computed profile of ONE publisher (MVP scope).

The profile is COMPOSITION, never invention:

  identity + curated facts   :mod:`outlet_registry` — canonical name, AllSides-style lean, home
                             locality. Honest absence when the registry doesn't know the outlet.
  counted catalog facts      :meth:`store.Store.publisher_catalog_stats` — volume, observed
                             window, topics, languages, hosts, event countries, tone; every
                             signal carries its own ``n`` and absent signals are absent.
  recent articles            the SAME search path + Article serializer Discover/Search use
                             (``store.search_feed_articles`` + ``discover.feed_article_to_article``).

Fail-honest rules, inherited platform-wide:
  * An unrated outlet is ``{"rated": false, "lean": null}`` — rendered "Not rated", never a
    fabricated Center (L2.2). The observed catalog lean is NOT shown as an independent signal:
    feed article lean IS the registry house lean by construction (``ingest`` joins the registry),
    so "curated vs observed" would compare a number with itself.
  * Tone modules (registers / emotion) appear only when at least ``MIN_SIGNAL`` rows carry the
    signal — omit, don't thin-render. Counted lists (topics/countries/languages) are honest at
    any n and always shown.
  * No factuality / ownership / transparency fields exist because no data source backs them yet
    (the registry grows by curation; see docs/LOCATION_PLATFORM.md's registry discipline).

Read-only and additive: no ranking, no recommender, no protected module is touched.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

import api_server as engine     # _prettify / _lean_bucket — the one serializer vocabulary
import discover                 # feed_article_to_article — the canonical Article shape
import media                    # publisher logo selection (curated override -> favicon)
import outlet_registry
from pagination import OffsetPagination

# A tone split over fewer rows than this is noise presented as a fact — the module is omitted
# until the signal exists (the same "empty beats wrong" rule as the country pickers).
MIN_SIGNAL = 5

# Counted-list caps: enough for a profile page, small enough to stay a summary.
TOP_TOPICS = 8
TOP_COUNTRIES = 8
TOP_LANGUAGES = 4
RECENT_LIMIT = 8


def _parse_iso(ts: "str | None") -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")) if ts else None
    except ValueError:
        return None


def _per_day(total: int, first: "str | None", last: "str | None") -> Optional[float]:
    """Articles/day over the OBSERVED window — only when a real window exists (two distinct
    timestamps); a single-day sample yields no cadence claim."""
    a, b = _parse_iso(first), _parse_iso(last)
    if not a or not b or b <= a:
        return None
    days = (b - a).total_seconds() / 86400.0
    if days < 1.0:      # sub-day window: a rate would extrapolate, not count
        return None
    return round(total / days, 1)


def _prettified_counts(items: list, cap: int) -> list:
    """Prettify labels the way the facet dropdowns do, merging any labels that collide after
    prettification, and keep the top ``cap`` by count (then label, deterministic)."""
    merged: dict = {}
    for it in items:
        label = engine._prettify(it["label"])
        merged[label] = merged.get(label, 0) + it["count"]
    ranked = sorted(merged.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"label": k, "count": v} for k, v in ranked[:cap]]


def get_publisher(store_, name: str, *, recent_limit: int = RECENT_LIMIT) -> Optional[dict]:
    """The full profile for ``name`` (display name, stored catalog name, or a registry alias /
    domain — the registry resolver accepts them all), or ``None`` when neither the registry nor
    the catalog knows the outlet. A registry outlet with zero catalog rows still profiles (its
    curated facts stand alone, with an honest zero volume)."""
    query = (name or "").strip()
    if not query:
        return None
    outlet = outlet_registry.resolve(query)
    stats = store_.publisher_catalog_stats(query)
    if stats is None and outlet is not None and outlet.canonical.lower() != query.lower():
        # The request came as an alias/domain; the catalog stores the canonical name.
        stats = store_.publisher_catalog_stats(outlet.canonical)
    if stats is None and outlet is None:
        return None

    stored_name = stats["publisher"] if stats else outlet.canonical
    display = outlet.canonical if outlet else engine._prettify(stored_name)
    lean = outlet.lean if outlet and math.isfinite(outlet.lean) else None

    hosts = (stats or {}).get("hosts") or []
    site = f"https://{hosts[0]['label']}" if hosts else None

    total = stats["total"] if stats else 0
    profile: dict = {
        "name": display,
        "rated": lean is not None,
        "lean": lean,
        "leanBucket": engine._lean_bucket(lean) if lean is not None else None,
        "site": site,
        "articles": {
            "total": total,
            "firstSeen": stats["firstSeen"] if stats else None,
            "lastSeen": stats["lastSeen"] if stats else None,
            "perDay": _per_day(total, stats["firstSeen"], stats["lastSeen"]) if stats else None,
        },
        "topics": _prettified_counts(stats["topics"], TOP_TOPICS) if stats else [],
        "languages": (stats["languages"] or [])[:TOP_LANGUAGES] if stats else [],
        "eventCountries": (stats["eventCountries"] or [])[:TOP_COUNTRIES] if stats else [],
    }
    if outlet is not None:
        # Curated locality facts — present only for registry outlets; None fields drop on the wire.
        profile["registry"] = {"country": outlet.country, "region": outlet.region,
                               "city": outlet.city, "scope": outlet.scope}
    if stats:
        if stats["registers"] and stats["registers"]["n"] >= MIN_SIGNAL:
            profile["registers"] = stats["registers"]
        if stats["emotion"] and stats["emotion"]["n"] >= MIN_SIGNAL:
            profile["emotion"] = stats["emotion"]
    if total:
        rows, _ = store_.search_feed_articles(
            publisher=stored_name, sort="newest",
            pagination=OffsetPagination.from_params(recent_limit, 0), include_provisional=False)
        profile["recent"] = [discover.feed_article_to_article(r) for r in rows]
    else:
        profile["recent"] = []
    profile.update(media.pick_best_logo(display, site))
    return profile
