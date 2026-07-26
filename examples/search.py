"""search.py — the live catalog Search Service over ``FeedArticle``.

Additive and read-only. It reuses ``store.search_feed_articles`` for the index-backed SQL and
``discover.feed_article_to_article`` for serialization, so a search result is the **exact** Article
shape recommendations/Discover render — and inherits the identical Read flow (canonical URL → browser
extension → Dashboard/History/Analytics/Health). It **never** queries the recommendation engine,
touches the ``Backend``, or changes ranking/scoring/selection/serialization.

Pagination is delegated to a :class:`pagination.Pagination` strategy (offset today; a keyset cursor
can be added later with no change here). Query timing is measured and surfaced in debug mode.
"""

from __future__ import annotations

import time
from typing import Optional

import discover                    # reuse feed_article_to_article (identical Article shape + Read flow)
from pagination import OffsetPagination

_SORTS = ("newest", "oldest", "publisher", "relevance")
_LEANS = ("left", "center", "right")


def normalize_sort(sort: Optional[str]) -> str:
    return sort if sort in _SORTS else "newest"


def normalize_lean(lean: Optional[str]) -> Optional[str]:
    return lean if lean in _LEANS else None


def search(store_, *, query: Optional[str] = None, publisher: Optional[str] = None,
           lean: Optional[str] = None, topic: Optional[str] = None,
           date_from: Optional[str] = None, date_to: Optional[str] = None,
           source: Optional[str] = None, country: Optional[str] = None, sort: str = "newest",
           limit: int = 30, offset: int = 0, debug: bool = False) -> dict:
    """Run a catalog search and return the paginated envelope:
    ``{results, total, page, pageSize, hasMore, remainingPages, sort}`` (plus ``queryMs`` +
    ``ftsAvailable`` when ``debug``). Every result carries the canonical URL, publisher, title,
    description, publication timestamp, political lean, and category."""
    sort = normalize_sort(sort)
    pagination = OffsetPagination.from_params(limit, offset)

    t0 = time.perf_counter()
    rows, total = store_.search_feed_articles(
        q=query, publisher=publisher, lean=normalize_lean(lean), topic=topic, country=country,
        date_from=date_from, date_to=date_to, source=source, sort=sort, pagination=pagination)
    results = [discover.feed_article_to_article(r) for r in rows]
    query_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    out = {"results": results, "total": total, "sort": sort, **pagination.meta(total)}
    if debug:
        out["queryMs"] = query_ms
        out["ftsAvailable"] = store_.fts5_available()   # diagnostics only — FTS is not implemented yet
    return out
