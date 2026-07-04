"""Reading-event ingestion — turn a raw read (a URL) into a scored read the engine can use.

Milestone C1: the **abstraction + the scored-article cache only**. Every ingestion source
(browser extension, pasted URL, RSS) hands in a :class:`RawRead`; this module scores it into
the :class:`ScoredRead` interface from Milestone B4, which the augmented-corpus seam then
places into the reference corpus. Nothing here is wired into an endpoint yet, and no research
algorithm is touched — the scorer only *reads* the bundled outlet-lean table from ``rwe.mind``.

Scoring strategy (approved): a **deterministic baseline** that needs no API key —

    outlet            = the URL's domain (rwe.mind._outlet_from_url)
    lean              = outlet -> AllSides lean via rwe.mind.DEFAULT_LEAN (NaN if unknown)
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
from rwe.mind import DEFAULT_LEAN, _norm, _outlet_from_url

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


@dataclass(frozen=True)
class RawRead:
    """A reading event as an ingestion source observes it — before scoring.

    ``url`` is required; the rest are hints a source may already know (the extension can read
    the outlet, section, and whether a page is political off the page itself). Anything left
    empty / ``None`` is derived by the scorer."""

    url: str
    title: str = ""
    outlet: str = ""
    category: str = ""
    political: Optional[bool] = None
    read_at: Optional[str] = None


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


class Scorer:
    """Baseline, deterministic scorer: a URL -> :class:`ScoredRead`, with an optional enricher.

    Uses the bundled AllSides outlet-lean table (``rwe.mind.DEFAULT_LEAN``) read-only; pass a
    different ``lean_table`` (normalised keys) to widen coverage. No API key, no network."""

    def __init__(self, lean_table: Optional[dict] = None, enricher: Optional[Enricher] = None):
        self.lean_table = DEFAULT_LEAN if lean_table is None else lean_table
        self.enricher = enricher

    def score(self, raw: RawRead) -> ScoredRead:
        outlet = raw.outlet or _outlet_from_url(raw.url)
        path = urlsplit(raw.url).path.lower()
        political = (raw.political if raw.political is not None
                     else any(h in path for h in _POLITICAL_HINTS))
        scored = ScoredRead(
            article_id=canonical_url(raw.url),
            outlet=outlet,
            category=raw.category or self._topic_from_path(path),
            title=raw.title or "",
            lean=self._lean_for(outlet),
            political=political,
            read_at=raw.read_at,
        )
        return self.enricher.enrich(scored, raw) if self.enricher is not None else scored

    def _lean_for(self, outlet: str) -> float:
        if not outlet:
            return float("nan")
        v = self.lean_table.get(_norm(outlet))
        return float(v) if v is not None else float("nan")

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
