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
# real publisher URL from the same CSV by row order. ``political`` (Commit R1) carries the scored
# article-level flag ("1"/"0"; "" = unknown → the loader derives from tags+url) so the corpus mask
# is real per-article classification, never assumed.
_COLUMNS = ["title", "source", "bias_rating", "tags", "url", "political"]


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


def _qbias_record(a: dict, center: float) -> list:
    """One qbias-format CSV row ``[title, source, bias_rating, tags, url]`` from a FeedArticle-shaped
    dict. The single definition of the row shape, shared by both exporters below so the CSV format
    (and the row order the ``Q{i}`` -> URL map depends on) lives in exactly one place."""
    scored = a.get("scored") or {}
    outlet = a.get("publisher") or scored.get("outlet") or ""
    political = scored.get("political")
    return [
        a.get("title") or scored.get("title") or "",
        outlet,
        _bias_label(scored.get("lean"), center),
        scored.get("category") or "",
        a.get("url") or a.get("canonicalUrl") or "",
        "" if political is None else ("1" if political else "0"),
    ]


def export_candidate_csv(rows, path: str, *, center: float = 0.5) -> int:
    """Write an explicit, already-composed list of FeedArticle-shaped dicts to a qbias-format CSV at
    ``path`` in the given order; returns the row count. This is **the** qbias serializer — the catalog
    exporter (below) and the hot-refresh Backend builder (``corpus_refresh``) both go through it, so
    the CSV format and the ``Q{i}`` row indexing that :func:`load_url_map` relies on are defined once.

    It writes every row verbatim and applies no filtering or cap: composition is the caller's job
    (``corpus_validation.build_candidate`` already balanced + capped the candidate). No article is
    modified — only projected onto the five qbias columns."""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    n = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_COLUMNS)
        for a in rows:
            w.writerow(_qbias_record(a, center))
            n += 1
    return n


def export_catalog_csv(store_, path: str, *, max_items: Optional[int] = None,
                       center: float = 0.5, max_per_outlet: Optional[int] = None) -> int:
    """Write the FeedArticle catalog to a qbias-format CSV at ``path``. Returns the row count.

    ``max_per_outlet`` (optional) keeps at most that many articles per outlet — the most-recently
    fetched, since :meth:`Store.list_feed_articles` is ordered newest-first — so a single high-volume
    feed (e.g. a "world news" firehose) can't dominate the recommendations. It is corpus *composition*
    only (like ``max_items``); it does not touch ranking, scoring, diversity, or selection. Serializes
    through :func:`export_candidate_csv` so the CSV format is single-sourced.

    Read-demand exemption (Commit 18): an article a user actually **read** is never trimmed out —
    neither by the ``max_items`` recency window nor by the per-outlet cap — because trimming a read
    article would disconnect that reader from the recommendation graph. Composition only, bounded by
    the distinct-read-URL set."""
    rows = store_.list_feed_articles(limit=max_items or 1_000_000)
    read_urls = store_.distinct_read_urls()
    if read_urls:                            # union in read articles the recency window missed
        have = {a.get("canonicalUrl") for a in rows}
        missing = [u for u in read_urls if u not in have]
        if missing:
            rows = rows + store_.feed_articles_by_urls(missing)
    if max_per_outlet:
        kept = []
        per_outlet: dict = {}
        for a in rows:
            scored = a.get("scored") or {}
            if a.get("canonicalUrl") in read_urls:
                kept.append(a)               # read-demand exemption: never capped out
                continue
            key = (a.get("publisher") or scored.get("outlet") or "").strip().lower()
            per_outlet[key] = per_outlet.get(key, 0) + 1
            if per_outlet[key] > max_per_outlet:
                continue                     # this outlet already hit its cap — keep the corpus balanced
            kept.append(a)
        rows = kept
    return export_candidate_csv(rows, path, center=center)


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
    # Optional per-outlet cap (RWE_FEED_MAX_PER_OUTLET, 0/unset = no cap) so one firehose feed can't
    # dominate the recommendation corpus. Applied to the corpus export only; the full catalog is kept.
    export_catalog_csv(store_, out, max_items=max_items,
                       max_per_outlet=_int_env("RWE_FEED_MAX_PER_OUTLET", 0) or None)
    return out


def load_url_map(csv_path: str) -> dict:
    """The corpus item-id -> publisher URL map implied by the exported catalog.

    ``catalog_from_qbias`` labels the i-th CSV *data* row ``Q{i}`` (``for i, row in enumerate(...)``)
    and drops rows with no resolvable lean without renumbering, so mapping each data-row index ``i``
    to that row's ``url`` reproduces the exact ids the recommender emits. Rows the builder later
    drops simply never appear as a recommendation id — their entries here are harmless. Reads the
    same CSV ``export_catalog_csv`` wrote (no duplicate storage)."""
    out: dict = {}
    try:
        with open(csv_path, newline="", encoding="utf-8") as f:
            for i, row in enumerate(csv.DictReader(f)):
                url = (row.get("url") or "").strip()
                if url:
                    out[f"Q{i}"] = url
    except OSError:
        pass
    return out


def _int_env(name: str, default: int) -> int:
    v = os.environ.get(name)
    return int(v) if v and v.lstrip("-").isdigit() else default
