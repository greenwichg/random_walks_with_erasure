"""media.py — the single home for all article/story media + publisher-logo SELECTION.

Additive and presentation-only. Every surface — RSS ingestion, Discover, Search, the Story hero, and
the recommendation enrichment — reuses these four functions, so image/logo selection is defined once:

    pick_best_image(candidates)          choose the best image among a feed item's media tags (ingest)
    pick_article_media(row)              the media dict for a stored FeedArticle row (serialization)
    pick_story_hero(articles, rep=…)     the optional hero image for a Story (legacy: representative →
                                         best → recent; ranked=True: evidence-ranked with branding/reuse
                                         rejection — docs/STORY_HERO_IMAGES.md)
    pick_best_logo(publisher, url=…)     a publisher logo URL (its own favicon, or a curated override)

It NEVER downloads an image, fetches Open Graph, or calls an AI — it only chooses among the metadata RSS
already provided and derives a logo URL from the publisher's own domain. Absent media → all-null, so
the caller falls back to the existing text-only layout. Image URLs stay canonical (never rewritten).
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse, urlsplit, urlunsplit

try:
    import outlet_registry            # reuse publisher identity for the curated-logo key (no duplication)
except Exception:                     # pragma: no cover - registry is optional
    outlet_registry = None

try:
    import store as _store            # SOURCE_PRIORITY + normalize_image_source only — pure contract
                                      # lookups, no database or network touch. The ranked hero consults
                                      # the ingestion source's existing media precedence, the same
                                      # ordering the dedup merge already trusts (store.upsert_feed_article).
except Exception:                     # pragma: no cover - store is optional (stdlib-only contexts)
    _store = None

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


# --------------------------------------------------------------------------- #
# Ranked story hero (RWE_STORY_HERO_GUARD) — measured 2026-08-16 on the production catalog by
# examples/audit_story_hero.py; record + receipts in docs/STORY_HERO_IMAGES.md.
# --------------------------------------------------------------------------- #

#: Reject a hero candidate whose image fronts MORE THAN this many distinct stories in the same
#: build — an image on many stories is by definition about none of them. The threshold is
#: measured, not guessed (production reuse table, 2026-08-16): every asset on >= 4 stories was
#: publisher furniture (``sr_placeholder.png`` on 20 stories across 14 publishers, an og-image
#: fallback on 10, social-media logos on 12 and 4), while the FIRST real photograph — The Hill's
#: AP file art legitimately reused across a related family — appears at exactly 3 stories, which
#: this value keeps. At this cut 25 branding heroes died and only 11 of 1,331 stories fell back
#: to the imageless card.
HERO_MAX_CLUSTER_REUSE = 3

#: URL-path tokens that mark an image as publisher furniture rather than story art. Each token is
#: receipted from the measured reuse table or this module's own conventions, never invented:
#: ``logo`` (TaipeiTimesLogo-1200X1200px, logo_1200x1200.png, logo_dgabc_facebook.jpg),
#: ``placeholder`` (sr_placeholder.png), ``og-image``/``og_image``/``ogimage`` (fb-og-image.png
#: and the og-fallback convention it instances), ``socmedia`` (newTsol_logo_socmedia.png),
#: ``masthead`` (the reported Spokesman-Review symptom's genre), ``favicon``/``apple-touch``
#: (this module's own ``_ICON_PATHS``). Deliberately conservative: a suspect verdict only demotes
#: in ranking — it costs a story its hero only when NO clean candidate exists, and that story's
#: card renders the coverage figure, a designed state.
_HERO_SUSPECT_TOKENS = ("logo", "placeholder", "og-image", "og_image", "ogimage",
                        "socmedia", "masthead", "favicon", "apple-touch")

#: Photo-shaped declared dimensions: a story photograph is large and landscape; a logo/avatar is
#: small and square-ish. Only ``media:`` tags carry dimensions, so shape can promote but its
#: absence never rejects.
_PHOTO_MIN_AREA = 90_000
_PHOTO_MIN_ASPECT = 1.2


def image_identity(url) -> str:
    """An image URL's IDENTITY for cross-story reuse counting: scheme+host lower-cased, path kept,
    query and fragment dropped. Query strings on a house asset are usually cache-busters or
    per-article resize parameters, so keeping them would let one placeholder wear a thousand
    identities and defeat the measurement. Path stays case-sensitive — some CDNs sign it."""
    s = _abs(url)
    if not s:
        return ""
    try:
        p = urlsplit(s)
    except ValueError:
        return ""
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path, "", ""))


def _photo_like(w, h) -> "tuple[bool, int]":
    """``(looks like a photo, area)`` from declared dimensions; ``(False, 0)`` when absent."""
    iw, ih = _int(w), _int(h)
    if not iw or not ih:
        return False, 0
    return (iw * ih >= _PHOTO_MIN_AREA and iw / ih >= _PHOTO_MIN_ASPECT), iw * ih


def hero_suspect(url, width=None, height=None) -> bool:
    """Whether an image looks like publisher branding rather than story art, from metadata alone:
    a furniture token in the URL path, or exactly-square declared dimensions (the 1200x1200
    social-logo shape — ``TaipeiTimesLogo-1200X1200px``, ``logo_1200x1200.png``). Never downloads,
    never inspects pixels."""
    s = _abs(url)
    if s:
        try:
            path = urlsplit(s).path.lower()
        except ValueError:
            path = ""
        if any(tok in path for tok in _HERO_SUSPECT_TOKENS):
            return True
    iw, ih = _int(width), _int(height)
    return bool(iw and ih and iw == ih)


def _source_priority(image_source) -> int:
    """Media precedence of the ingestion source that supplied the image (``store.SOURCE_PRIORITY``
    through ``store.normalize_image_source``); 0 when store is unavailable or the tag is unknown.
    RSS media tags outrank adapter payloads outrank GDELT's ``og:image`` — the og:image tier is
    exactly where site-wide fallback graphics arrive, so the ordering the dedup merge already
    trusts is evidence here too."""
    if _store is None:                # pragma: no cover - store import guarded above
        return 0
    return _store.SOURCE_PRIORITY.get(_store.normalize_image_source(image_source), 0)


def hero_rank(article, *, is_rep: bool = False, rejected=None) -> tuple:
    """The ranked-hero ordering for one candidate, highest first. Deterministic, metadata-only.
    Shared with ``examples/audit_story_hero.py`` so the instrument can never drift from the
    shipped rule.

    Order of evidence: not a known cross-story-reused asset → not branding-shaped
    (:func:`hero_suspect`) → photo-shaped dimensions → area → the ingestion source's media
    priority → the representative → recency. The representative survives as a TIEBREAK, which is
    all it was ever entitled to be — its unconditional override is the defect this replaces."""
    rej = rejected or ()
    photo, area = _photo_like(article.get("imageWidth"), article.get("imageHeight"))
    return (image_identity(article.get("image")) not in rej,
            not hero_suspect(article.get("image"), article.get("imageWidth"),
                             article.get("imageHeight")),
            photo, area, _source_priority(article.get("imageSource")),
            bool(is_rep), article.get("publishedAt") or "")


def pick_story_hero(articles, *, representative: Optional[dict] = None,
                    ranked: bool = False, rejected=None) -> Optional[dict]:
    """The optional hero image for a Story. Never fabricates.

    Legacy (``ranked=False`` — byte-identical to the pre-guard behaviour), by priority: the
    representative article's image → highest-quality (largest area) → most recent → ``None``.

    Ranked (the ``RWE_STORY_HERO_GUARD`` path): every member with an image competes under
    :func:`hero_rank`; ``rejected`` is the caller's per-build set of cross-story-reused image
    identities (:func:`image_identity`). If the BEST candidate is still a rejected or suspect
    asset, the answer is ``None`` — the imageless card renders the coverage-distribution figure,
    a designed state, and no hero is more honest than a masthead pretending to be news
    (docs/SIGNAL_INTEGRITY.md, applied to images)."""
    with_image = [a for a in (articles or []) if _abs(a.get("image"))]
    if not with_image:
        return None
    if not ranked:
        if representative is not None and _abs(representative.get("image")):
            return _hero_of(representative)
        best = max(with_image, key=lambda a: (_area(a.get("imageWidth"), a.get("imageHeight")),
                                              a.get("publishedAt") or ""))
        return _hero_of(best)
    best = max(with_image,
               key=lambda a: hero_rank(a, is_rep=a is representative, rejected=rejected))
    not_reused, not_suspect = hero_rank(best, is_rep=best is representative,
                                        rejected=rejected)[:2]
    if not (not_reused and not_suspect):
        return None
    return _hero_of(best)


def _host(url) -> str:
    try:
        return (urlparse(str(url or "")).hostname or "").strip()
    except ValueError:
        return ""


#: Site-root icon paths, LARGEST FIRST. `favicon.ico` is a 16-32px browser chrome icon; blown up
#: to a 48px avatar box on a 2x display it is a 4.5x upscale, which is the blurry mark the
#: publisher profile used to show for every outlet enrichment had not reached. The Apple touch
#: icons are conventionally 180x180 and are the highest-resolution asset a site reliably exposes at
#: a predictable path — no HTML parsing, no third-party icon service, still the publisher's own
#: asset. Each is only a CANDIDATE: any of them may 404, so the client walks the list.
_ICON_PATHS = ("apple-touch-icon.png", "apple-touch-icon-precomposed.png", "favicon.ico")


def _host_icons(host: str) -> list:
    return [f"https://{host}/{p}" for p in _ICON_PATHS]


def pick_best_logo(publisher, url=None, *, enriched=None) -> dict:
    """A publisher logo, best source first, plus the ordered alternates to try if it fails.

    1. a **curated** override (with an optional dark-mode variant), keyed by canonical name;
    2. an **enriched** logo from Wikimedia Commons / Wikipedia, passed in as ``(url, source)`` by
       the caller that owns the metadata cache — this module stays free of store and network;
    3. the publisher's own **site icons**, derived from the article URL's domain
       (privacy-preserving: the publisher's own asset, no third party, no download).

    Enrichment sits ABOVE the site icons rather than below because a Commons logo file is the
    outlet's actual mark at usable resolution, while a site icon is browser chrome. It stays BELOW
    curation because a hand-picked logo is a decision somebody made on purpose.

    ``publisherLogoFallbacks`` is what makes the tiers survive contact with reality: an enriched
    URL can 404 (a Commons file gets renamed), and an Apple touch icon is a convention rather than
    a guarantee. Returning one URL meant a single 404 dropped the outlet straight to a generic
    building glyph; returning the chain lets the client degrade one step at a time and only reach
    the glyph when the publisher genuinely exposes nothing. All-null when nothing is known."""
    name = (publisher or "").strip()
    canon = None
    if outlet_registry is not None:
        try:
            canon = outlet_registry.canonical(name)
        except Exception:
            canon = None
    host = _host(url)
    icons = _host_icons(host) if host else []

    curated = _CURATED_LOGOS.get((canon or name).lower())
    if curated:
        return {"publisherLogo": curated.get("logo"), "publisherLogoDark": curated.get("logoDark"),
                "publisherLogoSource": "registry", "publisherLogoFallbacks": icons or None}
    if enriched:
        logo, source = enriched
        if _abs(logo):
            return {"publisherLogo": logo, "publisherLogoDark": None,
                    "publisherLogoSource": source, "publisherLogoFallbacks": icons or None}
    if icons:
        # The highest-resolution site icon leads; the 16px favicon is the last thing tried, never
        # the first thing shown.
        return {"publisherLogo": icons[0], "publisherLogoDark": None,
                "publisherLogoSource": "site-icon", "publisherLogoFallbacks": icons[1:] or None}
    return {"publisherLogo": None, "publisherLogoDark": None, "publisherLogoSource": None,
            "publisherLogoFallbacks": None}
