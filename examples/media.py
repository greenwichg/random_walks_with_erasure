"""media.py — the single home for all article/story media + publisher-logo SELECTION.

Additive and presentation-only. Every surface — RSS ingestion, Discover, Search, the Story hero, and
the recommendation enrichment — reuses these four functions, so image/logo selection is defined once:

    pick_best_image(candidates)          choose the best image among a feed item's media tags (ingest)
    pick_article_media(row)              the media dict for a stored FeedArticle row (serialization)
    pick_story_hero(articles, rep=…)     the optional hero image for a Story (representative → best → recent)
    pick_best_logo(publisher, url=…)     a publisher logo URL (its own favicon, or a curated override)

It NEVER downloads an image, fetches Open Graph, or calls an AI — it only chooses among the metadata RSS
already provided and derives a logo URL from the publisher's own domain. Absent media → all-null, so
the caller falls back to the existing text-only layout. Image URLs stay canonical (never rewritten).
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

try:
    import outlet_registry            # reuse publisher identity for the curated-logo key (no duplication)
except Exception:                     # pragma: no cover - registry is optional
    outlet_registry = None

# Richness of each media tag, used only to break ties between equally-sized images.
_SOURCE_RANK = {"media:content": 3, "media:thumbnail": 2, "enclosure": 1, "atom:link": 1}
_IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".bmp", ".svg")

# Optional curated logo overrides (canonical publisher name, lower-cased) -> {logo, logoDark}. Empty by
# default; this is the extension point for hand-picked logos + dark-mode variants. Favicon is the
# fallback for everything else, so no binary assets are shipped.
_CURATED_LOGOS: "dict[str, dict]" = {}


def _abs(u) -> str:
    """An absolute http(s) URL, or "" — never a relative/hostless value (the Read-to-app-origin guard,
    applied to media URLs too)."""
    s = str(u or "").strip()
    return s if s[:7].lower() == "http://" or s[:8].lower() == "https://" else ""


def _int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _area(w, h) -> int:
    iw, ih = _int(w), _int(h)
    return iw * ih if iw and ih else 0


def _is_image_mime(mime) -> bool:
    return bool(mime) and str(mime).lower().startswith("image/")


def _looks_image(url: str, mime, source) -> bool:
    """Whether a media candidate is an image. A declared image MIME wins; a media:content/thumbnail is
    assumed an image; otherwise fall back to the URL extension. This keeps podcast/video ``enclosure``
    tags (audio/mpeg, video/mp4) from being treated as images."""
    if mime:
        return _is_image_mime(mime)
    if source in ("media:content", "media:thumbnail"):
        return True
    return urlparse(url).path.lower().endswith(_IMG_EXT)


def pick_best_image(candidates) -> Optional[dict]:
    """Choose the best image among a feed item's media candidates (each ``{url, width, height, mime,
    source}``). Prefers a declared image MIME, then the largest area, then the richest tag. Returns
    ``{url, width, height, mime, source}`` or ``None``. Metadata only — never downloads."""
    valid = []
    for c in candidates or []:
        url = _abs(c.get("url"))
        if not url or not _looks_image(url, c.get("mime"), c.get("source")):
            continue
        valid.append({"url": url, "width": _int(c.get("width")), "height": _int(c.get("height")),
                      "mime": c.get("mime") or None, "source": c.get("source") or "enclosure"})
    if not valid:
        return None
    valid.sort(key=lambda c: (_is_image_mime(c["mime"]), _area(c["width"], c["height"]),
                              _SOURCE_RANK.get(c["source"], 0)), reverse=True)
    return valid[0]


def pick_article_media(row) -> dict:
    """The article media dict from a stored FeedArticle row (already image-selected at ingestion).
    Absolute-URL guarded; all-null when the row carries no image."""
    image = _abs(row.get("image"))
    if not image:
        return {"image": None, "imageWidth": None, "imageHeight": None, "imageMimeType": None,
                "imageSource": None, "imageAttribution": None}
    return {"image": image, "imageWidth": _int(row.get("imageWidth")),
            "imageHeight": _int(row.get("imageHeight")), "imageMimeType": row.get("imageMimeType") or None,
            "imageSource": row.get("imageSource") or None,
            "imageAttribution": row.get("imageAttribution") or None}


def _hero_of(a: dict) -> dict:
    return {"image": _abs(a.get("image")) or None, "imageWidth": _int(a.get("imageWidth")),
            "imageHeight": _int(a.get("imageHeight")), "imageMimeType": a.get("imageMimeType") or None,
            "imageSource": a.get("imageSource") or None, "imageAttribution": a.get("imageAttribution") or None}


def pick_story_hero(articles, *, representative: Optional[dict] = None) -> Optional[dict]:
    """The optional hero image for a Story, by priority: the representative article's image →
    highest-quality (largest area) → most recent → ``None``. Never fabricates."""
    with_image = [a for a in (articles or []) if _abs(a.get("image"))]
    if not with_image:
        return None
    if representative is not None and _abs(representative.get("image")):
        return _hero_of(representative)
    best = max(with_image, key=lambda a: (_area(a.get("imageWidth"), a.get("imageHeight")),
                                          a.get("publishedAt") or ""))
    return _hero_of(best)


def _host(url) -> str:
    try:
        return (urlparse(str(url or "")).hostname or "").strip()
    except ValueError:
        return ""


def pick_best_logo(publisher, url=None, *, enriched=None) -> dict:
    """A publisher logo, best source first:

    1. a **curated** override (with an optional dark-mode variant), keyed by canonical name;
    2. an **enriched** logo from Wikimedia Commons / Wikipedia, passed in as ``(url, source)`` by
       the caller that owns the metadata cache — this module stays free of store and network;
    3. the publisher's own **favicon**, derived from the article URL's domain (privacy-preserving:
       the publisher's own asset, no third party, no download).

    Enrichment sits ABOVE the favicon rather than below it because a favicon is a 16px browser icon
    that frequently 404s, while a Commons logo file is the outlet's actual mark — the favicon is a
    last resort, not a preference. It stays BELOW curation because a hand-picked logo is a decision
    somebody made on purpose. All-null when nothing is known."""
    name = (publisher or "").strip()
    canon = None
    if outlet_registry is not None:
        try:
            canon = outlet_registry.canonical(name)
        except Exception:
            canon = None
    curated = _CURATED_LOGOS.get((canon or name).lower())
    if curated:
        return {"publisherLogo": curated.get("logo"), "publisherLogoDark": curated.get("logoDark"),
                "publisherLogoSource": "registry"}
    if enriched:
        logo, source = enriched
        if _abs(logo):
            return {"publisherLogo": logo, "publisherLogoDark": None,
                    "publisherLogoSource": source}
    host = _host(url)
    if host:
        return {"publisherLogo": f"https://{host}/favicon.ico", "publisherLogoDark": None,
                "publisherLogoSource": "favicon"}
    return {"publisherLogo": None, "publisherLogoDark": None, "publisherLogoSource": None}
