"""Reading-event ingestion — turn a raw read (a URL) into a scored read the engine can use.

Every ingestion source (browser extension, pasted URL, RSS) hands in a :class:`RawRead`; this
module scores it into the :class:`ScoredRead` interface from Milestone B4, which the
augmented-corpus seam then places into the reference corpus. No research algorithm is touched —
outlet identity + lean come from the product-layer :mod:`outlet_registry`.

Scoring strategy (approved): a **deterministic baseline** that needs no API key —

    outlet + lean     = the canonical OutletRegistry (the product layer's single source of truth
                        for outlet identity): a captured URL, a source-supplied outlet name, and a
                        corpus label all resolve to the SAME canonical outlet + AllSides lean.
                        An outlet the registry doesn't know still ingests — the outlet is the bare
                        domain and the lean is NaN (excluded from lean metrics, as the engine
                        already handles missing data).
    political / topic = what the source supplies, else a light URL-path heuristic

plus an **optional, pluggable** :class:`Enricher` (e.g. an LLM emotion/register/topic
classifier, cached) that improves a read *only when configured*. Absent an enricher, scoring is
fully offline and deterministic, and unscored fields (emotion/register/confidence) stay ``n/a``
exactly as the engine already handles missing data.

    scorer = Scorer()                                          # baseline, no enricher
    read = score_with_cache(RawRead(url=...), scorer, store)   # scored once per canonical URL
"""

from __future__ import annotations

import dataclasses
from dataclasses import asdict, dataclass, replace
from typing import Optional, Protocol
from urllib.parse import urlsplit, urlunsplit

from augmented_corpus import ScoredRead
from outlet_registry import OutletRegistry, default_registry

# Coarse URL-path segment -> display topic. The browser extension can pass the real section
# (OpenGraph article:section) and the optional enricher can refine it; this is only a
# best-effort fallback so Topic Diversity has *some* signal for a pasted link.
_SECTIONS = {
    "politics": "Politics", "election": "Politics", "elections": "Politics",
    "business": "Business", "economy": "Business", "markets": "Business",
    "technology": "Technology", "tech": "Technology",
    "science": "Science", "health": "Health", "climate": "Climate", "environment": "Climate",
    "world": "World", "us": "U.S.", "opinion": "Opinion", "sports": "Sports",
    "entertainment": "Entertainment", "arts": "Arts", "culture": "Culture",
}
# Path substrings that flag a read as political when the source doesn't say.
_POLITICAL_HINTS = ("politic", "election", "/opinion")
# Category substrings that flag an article as political (case-insensitive).
_POLITICAL_CATEGORY_HINTS = ("politic", "election", "opinion")


def looks_political(url: str = "", category: str = "") -> bool:
    """The shared article-level political heuristic: URL path hints or a political category.

    ONE definition product-wide — the read scorer and the corpus loaders both use it, so the
    Information Health metrics, the cross-cutting gate, and the bridge explanations can never
    disagree about what "political" means. Deterministic; no network."""
    path = urlsplit(url).path.lower() if url else ""
    cat = (category or "").lower()
    return (any(h in path for h in _POLITICAL_HINTS)
            or any(h in cat for h in _POLITICAL_CATEGORY_HINTS))


@dataclass(frozen=True)
class RawRead:
    """A reading event as an ingestion source observes it — before scoring.

    ``url`` is required; the rest are hints a source may already know (the extension reads the
    outlet, section, headline, and the article's ``og:description`` off the page). ``subtitle`` /
    ``description`` give the enricher more text than the headline alone (register/emotion spread
    much better on an abstract); anything left empty / ``None`` is derived by the scorer or
    degrades gracefully to headline-only."""

    url: str
    title: str = ""
    outlet: str = ""
    category: str = ""
    political: Optional[bool] = None
    read_at: Optional[str] = None
    subtitle: str = ""          # optional richer text for enrichment (deck / kicker)
    description: str = ""        # optional richer text for enrichment (og:description / abstract)


class Enricher(Protocol):
    """Optional refinement of a baseline-scored read (e.g. an LLM emotion/register classifier).
    Implementations return a possibly-updated :class:`ScoredRead` and must be side-effect free."""

    def enrich(self, scored: ScoredRead, raw: RawRead) -> ScoredRead: ...


def canonical_url(url: str) -> str:
    """Canonical form used as the dedup / cache key: lower-cased host without ``www.``, no query
    or fragment, no trailing slash. Query params are dropped (usually tracking); a source that
    needs a query-identified article should pass an already-clean URL."""
    p = urlsplit(url.strip())
    scheme = (p.scheme or "https").lower()
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = p.path.rstrip("/") or "/"
    return urlunsplit((scheme, host, path, "", ""))


def normalize_url(url: str) -> str:
    """Best-effort tidy of a user-supplied URL: trim, and prepend ``https://`` when no scheme is
    present so a bare ``example.com/x`` parses with a host."""
    u = (url or "").strip()
    if u and "://" not in u:
        u = "https://" + u
    return u


def has_host(url: str) -> bool:
    """A cheap validity check: the URL has a plausible host (a dot, no spaces) — enough to
    reject pasted junk before it becomes a reading event."""
    host = urlsplit(url).netloc
    return bool(host) and "." in host and " " not in host


def _domain_of(url: str) -> str:
    """Bare host of a URL (lower-cased, ``www.`` removed) — the fallback outlet label when the
    registry doesn't know the outlet. Correct prefix handling: the research helper's
    ``lstrip("www.")`` ate leading ``w`` characters (``washingtonpost.com`` -> ``ashingtonpost``,
    ``wsj.com`` -> ``sj``); this strips the ``www.`` prefix only."""
    s = (url or "").strip()
    netloc = urlsplit(s).netloc if "://" in s else s.split("/", 1)[0]
    host = netloc.split("@")[-1].split(":", 1)[0].lower()
    return host[4:] if host.startswith("www.") else host


class Scorer:
    """Baseline, deterministic scorer: a URL -> :class:`ScoredRead`, with an optional enricher.

    Outlet identity + lean come from the canonical :class:`OutletRegistry` (the product layer's
    single source of truth), so a captured URL, a source-supplied outlet name, and a corpus label
    all collapse to the same canonical outlet. An outlet the registry doesn't know still ingests —
    the outlet is the bare domain and the lean is NaN. No API key, no network."""

    def __init__(self, registry: Optional[OutletRegistry] = None,
                 enricher: Optional[Enricher] = None):
        self.registry = registry if registry is not None else default_registry()
        self.enricher = enricher

    def score(self, raw: RawRead) -> ScoredRead:
        path = urlsplit(raw.url).path.lower()
        political = (raw.political if raw.political is not None
                     else looks_political(raw.url, raw.category))
        outlet, lean = self._resolve_outlet(raw)
        scored = ScoredRead(
            article_id=canonical_url(raw.url),
            outlet=outlet,
            category=raw.category or self._topic_from_path(path),
            title=raw.title or "",
            lean=lean,
            political=political,
            read_at=raw.read_at,
        )
        return self.enricher.enrich(scored, raw) if self.enricher is not None else scored

    def _resolve_outlet(self, raw: RawRead):
        """Canonical outlet + AllSides lean via the registry — resolving a source-supplied outlet
        name if present, else the URL. Unknown -> (the source's label or the bare domain, NaN), so
        the read still ingests, just without a lean."""
        out = self.registry.resolve(raw.outlet or raw.url)
        if out is not None:
            return out.canonical, out.lean
        return (raw.outlet or _domain_of(raw.url)), float("nan")

    @staticmethod
    def _topic_from_path(path: str) -> str:
        for seg in path.split("/"):
            hit = _SECTIONS.get(seg.lower())
            if hit:
                return hit
        return ""


# --------------------------------------------------------------------------- #
# Scored-article cache — score once per canonical URL, shared across users.
# --------------------------------------------------------------------------- #
def _to_cache(scored: ScoredRead) -> dict:
    d = asdict(scored)
    d["read_at"] = None          # the cache holds article scoring, not a per-read timestamp
    return d


def _from_cache(d: dict) -> ScoredRead:
    names = {f.name for f in dataclasses.fields(ScoredRead)}
    return ScoredRead(**{k: v for k, v in d.items() if k in names})


def score_with_cache(raw: RawRead, scorer: Scorer, store) -> ScoredRead:
    """Score ``raw``, reusing a previously-scored article for the same canonical URL so a
    popular link is scored once and shared. ``store`` is any object exposing
    ``get_scored_article(url)`` / ``save_scored_article(url, dict)`` (the SQLite store). The
    returned read carries *this* read's timestamp, while its scoring comes from the cache."""
    key = canonical_url(raw.url)
    cached = store.get_scored_article(key)
    if cached is not None:
        sr = _from_cache(cached)
        return replace(sr, read_at=raw.read_at, title=(sr.title or raw.title or ""))
    scored = scorer.score(raw)
    store.save_scored_article(key, _to_cache(scored))
    return scored
