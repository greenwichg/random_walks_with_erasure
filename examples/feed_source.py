"""Live recommendation SOURCE — build the recommender's article catalog from the RSS ``FeedArticle``
catalog instead of the static qbias CSV / synthetic generator.

The smallest additive seam that swaps the *article source* without touching a single recommendation
algorithm: it exports ``FeedArticle`` rows to a **qbias-format CSV** that the EXISTING corpus builder
(``simulate_users.run(qbias=<csv>)``) reads unchanged. The recommender, health metrics, diversity,
and personalization then operate over live articles **exactly** as they do over the static qbias
catalog — same simulated population, same ``recommender_inputs``, same RWE models. Nothing here (or
in the engine) changes ranking, scoring, diversity, health, or personalization; the protected
simulator is reused as-is.

Enable with ``RWE_RECS_SOURCE=feed``. If the catalog is too small to simulate a population, the
caller falls back to the existing corpus (so enabling it before any RSS ingest is safe).
"""

from __future__ import annotations

import csv
import math
import os
from pathlib import Path
from typing import Optional

# Enough of a catalog to sample a population + build a non-degenerate click matrix. Configurable.
DEFAULT_MIN_ARTICLES = 50
# Column names chosen to match what ``catalog_from_qbias`` fuzzy-picks (headline / bias / outlet /
# tags). ``url`` is written too (the builder ignores it) so a future URL pass-through can recover the
# real publisher URL from the same CSV by row order.
_COLUMNS = ["title", "source", "bias_rating", "tags", "url"]


def enabled() -> bool:
    """Whether the recommender should source its catalog from the RSS FeedArticle store."""
    return os.environ.get("RWE_RECS_SOURCE", "").strip().lower() in {"feed", "rss", "catalog"}


def _data_dir() -> str:
    return str(Path(__file__).resolve().parent.parent / "data")


def _bias_label(lean, center: float = 0.5) -> str:
    """Map a numeric outlet lean in ``[-2, 2]`` to the AllSides-style label ``catalog_from_qbias``
    parses (``left`` / ``center`` / ``right``), matching how the qbias corpus is positioned. An
    unknown lean yields ``""`` — the builder then drops the row, exactly as it does for a qbias row
    with no resolvable bias."""
    if lean is None:
        return ""
    try:
        v = float(lean)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(v):
        return ""
    if v <= -center:
        return "left"
    if v >= center:
        return "right"
    return "center"


def export_catalog_csv(store_, path: str, *, max_items: Optional[int] = None,
                       center: float = 0.5) -> int:
    """Write the FeedArticle catalog to a qbias-format CSV at ``path``. Returns the row count."""
    rows = store_.list_feed_articles(limit=max_items or 1_000_000)
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    n = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_COLUMNS)
        for a in rows:
            scored = a.get("scored") or {}
            w.writerow([
                a.get("title") or scored.get("title") or "",
                a.get("publisher") or scored.get("outlet") or "",
                _bias_label(scored.get("lean"), center),
                scored.get("category") or "",
                a.get("url") or a.get("canonicalUrl") or "",
            ])
            n += 1
    return n


def prepare(store_, path: Optional[str] = None, *, min_articles: Optional[int] = None,
            max_items: Optional[int] = None) -> Optional[str]:
    """Export the FeedArticle catalog to a qbias-format CSV and return its path — or ``None`` when
    the catalog is too small (fewer than ``min_articles``), so the caller keeps the existing corpus.

    Path resolution: explicit ``path`` > ``RWE_FEED_CORPUS_CSV`` > ``<repo>/data/feed_corpus.csv``.
    Threshold: ``min_articles`` > ``RWE_FEED_MIN_ARTICLES`` > :data:`DEFAULT_MIN_ARTICLES`."""
    total = store_.count_feed_articles()
    threshold = (min_articles if min_articles is not None
                 else _int_env("RWE_FEED_MIN_ARTICLES", DEFAULT_MIN_ARTICLES))
    if total < threshold:
        return None
    out = path or os.environ.get("RWE_FEED_CORPUS_CSV") or os.path.join(_data_dir(), "feed_corpus.csv")
    export_catalog_csv(store_, out, max_items=max_items)
    return out


def _int_env(name: str, default: int) -> int:
    v = os.environ.get(name)
    return int(v) if v and v.lstrip("-").isdigit() else default
