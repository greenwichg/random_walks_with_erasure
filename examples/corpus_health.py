"""Corpus health — shared metrics, thresholds, and **validation-aware retention**.

Pure functions over lists of ``FeedArticle``-shaped dicts (as ``store.list_feed_articles`` returns);
no recommendation code, no protected module. This is the single definition of "what a healthy corpus
needs", used now by retention and later by the corpus-validation gate — so retention respects exactly
what validation will require.

Retention guarantee (the point of this module): pruning is **monotonic** — it computes the raw
age/count prune set, then *retains additional older articles* until the kept catalog still meets the
configured floors (min total / publishers / per-political-bucket / fresh). It can therefore only ever
prune LESS than the raw policy — never below the floors — so it can never strand the catalog in a
state from which no healthy replacement corpus could ever be built. If the catalog simply *cannot*
meet a floor (the feeds never supplied that diversity), retention keeps everything relevant (best
effort) and never makes it worse; the later validation gate then rejects the candidate and the
current corpus keeps serving.

Config (env, all optional):
    RWE_FEED_MAX_AGE_DAYS        recommendation-candidate age window in days (default 60; 0 = off).
                                 Composition only — stale articles stay stored and visible to
                                 Search / Stories / History, they just stop being rec candidates.
    RWE_FEED_REQUIRE_DATED       when truthy (and the age window is active), candidacy requires a
                                 parseable ``publishedAt`` — the ``fetchedAt`` fallback is not
                                 trusted, so a legacy feed re-serving undated cached items can't
                                 keep them "fresh" forever (re-polls refresh ``fetchedAt``).
                                 Default off. Composition only; read articles stay exempt.
    RWE_RETENTION_MAX_AGE_DAYS   prune articles older than this           (0/unset = no age prune)
    RWE_RETENTION_MAX_COUNT      keep at most this many, newest-first      (0/unset = no count prune)
    RWE_CORPUS_MIN_ARTICLES      floor: min total (default RWE_FEED_MIN_ARTICLES, 50)
    RWE_CORPUS_MIN_PUBLISHERS    floor: min distinct publishers            (default 0 = off)
    RWE_CORPUS_MIN_PER_BUCKET    floor: min articles per left/center/right (default 0 = off)
    RWE_CORPUS_MIN_FRESH         floor: min fresh articles                 (default 0 = off)
    RWE_CORPUS_FRESH_MAX_AGE_DAYS   what counts as "fresh"                 (default 3)

Validation ceilings (read only by the corpus-validation gate; retention ignores them):
    RWE_CORPUS_MAX_PER_PUBLISHER      ceiling: max articles from one publisher   (default 0 = off)
    RWE_CORPUS_MAX_BUCKET_PERCENT     ceiling: max share of any political bucket (0–100, 0 = off)
    RWE_CORPUS_MAX_ARTICLE_AGE_DAYS   ceiling: newest article must be within this (default 0 = off)
    RWE_CORPUS_MAX_DUPLICATE_PERCENT  ceiling: max duplicate share               (0–100, 0 = off)
    RWE_CORPUS_MAX_MISSING_METADATA_PERCENT  ceiling: max missing-metadata share (0–100, 0 = off)
    RWE_CORPUS_REQUIRE_HEALTHY_FEEDS  require zero unhealthy feeds to activate   (default off)
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional

_BUCKETS = ("left", "center", "right")
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)   # undated articles sort oldest (pruned first)
_logger = logging.getLogger("ih.corpus")


def _int_env(name: str, default: int) -> int:
    v = os.environ.get(name)
    return int(v) if v and v.lstrip("-").isdigit() else default


def _float_env(name: str, default: float) -> float:
    v = os.environ.get(name)
    try:
        return float(v) if v not in (None, "") else default
    except ValueError:
        return default


def _bool_env(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _default_log(level: int, event: str, **fields) -> None:
    _logger.log(level, json.dumps({"event": event, **fields}, default=str))


# --------------------------------------------------------------------------- #
# Field extractors (tolerant of the store's FeedArticle-row dict shape).
# --------------------------------------------------------------------------- #
def _lean_bucket(lean, center: float = 0.5) -> Optional[str]:
    try:
        v = float(lean)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    if v <= -center:
        return "left"
    if v >= center:
        return "right"
    return "center"


def _outlet(a: dict) -> str:
    scored = a.get("scored") or {}
    return (a.get("publisher") or scored.get("outlet") or "").strip()


def _bucket(a: dict) -> Optional[str]:
    return _lean_bucket((a.get("scored") or {}).get("lean"))


#: Candidate-age fallback order (C4.1): after the article's own ``publishedAt``, prefer ``createdAt``
#: — the row's STABLE first-seen time, stamped once at ingest — over ``fetchedAt``, which every
#: re-poll refreshes. Anchoring to the refreshed ``fetchedAt`` let an undated article reset its age
#: on each poll and stay a candidate forever; ``createdAt`` ages it out ``feed_max_age_days`` after
#: first discovery instead. ONLY candidate freshness uses this order; the health metrics keep
#: :func:`_published`'s default (``publishedAt`` then ``fetchedAt``), so no reported metric shifts.
_CANDIDACY_TIME_KEYS = ("publishedAt", "createdAt", "fetchedAt")


def _published(a: dict, keys: "tuple[str, ...]" = ("publishedAt", "fetchedAt")) -> Optional[datetime]:
    """First parseable timestamp among ``keys`` (as tz-aware UTC), or ``None``. Default order is the
    observed time (``publishedAt`` else ``fetchedAt``); candidacy passes :data:`_CANDIDACY_TIME_KEYS`
    to anchor an undated article's age to its stable first-seen ``createdAt`` (see that constant)."""
    for key in keys:
        s = (a.get(key) or "").strip()
        if s:
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def _canonical(a: dict) -> str:
    return a.get("canonicalUrl") or a.get("url") or ""


def _has_publication_date(a: dict) -> bool:
    """Whether the article carries a parseable *publication* date (``publishedAt`` only — ``fetchedAt``
    is set on every row and would mask a genuinely missing date)."""
    s = (a.get("publishedAt") or "").strip()
    if not s:
        return False
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _missing_metadata(a: dict) -> bool:
    """Missing metadata = no title or no publication date — the same rule ``rss_ingest`` uses for its
    per-feed quality metric, so a corpus-level count and a per-feed count mean the same thing."""
    return (not (a.get("title") or "").strip()) or (not _has_publication_date(a))


# --------------------------------------------------------------------------- #
# Recommendation-candidate freshness (Commit C4) — composition only, storage untouched.
# --------------------------------------------------------------------------- #
def feed_max_age_days() -> Optional[float]:
    """The recommendation-candidate age window in days (``RWE_FEED_MAX_AGE_DAYS``), or ``None``
    when the gate is disabled. Default **60**; ``0`` (or negative, or a non-number) disables.

    Distinct from ``RWE_RETENTION_MAX_AGE_DAYS`` (which *deletes* catalog rows): this window only
    keeps stale articles out of the recommendation corpus — they stay stored and remain visible
    to Search, Stories, and Reading History."""
    v = _float_env("RWE_FEED_MAX_AGE_DAYS", 60.0)
    return v if v > 0 else None


def feed_require_dated() -> bool:
    """Whether recommendation candidacy requires a parseable ``publishedAt``
    (``RWE_FEED_REQUIRE_DATED``, default off).

    Defends against stale/legacy feeds that re-serve old items without dates: every re-poll
    refreshes ``fetchedAt`` (``store.upsert_feed_article``), so such an item's fallback age never
    grows and it would pass the age window forever. Only consulted while the
    :func:`feed_max_age_days` window is active — disabling the window disables this gate too."""
    return _bool_env("RWE_FEED_REQUIRE_DATED", False)


def fresh_articles(articles: list, *, now: Optional[datetime] = None,
                   max_age_days: Optional[float] = None,
                   exempt: "frozenset[str] | set[str]" = frozenset(),
                   require_dated: Optional[bool] = None) -> list:
    """The subset of ``articles`` fresh enough to be recommendation candidates — a filter, never a
    mutation (the same row objects are returned).

    An article's age comes from :func:`_published` with :data:`_CANDIDACY_TIME_KEYS` (``publishedAt``,
    else the stable first-seen ``createdAt``, else ``fetchedAt``) — so an undated article is as old as
    its FIRST discovery and genuinely ages out, instead of resetting to fresh every time a re-poll
    refreshes ``fetchedAt`` (C4.1); a row with no parseable time at all is kept (staleness can't be
    proven). With ``require_dated`` (default: the
    ``RWE_FEED_REQUIRE_DATED`` env flag) an article with no parseable ``publishedAt`` is excluded
    instead — the ``fetchedAt`` fallback is not trusted for candidacy. Canonical URLs in
    ``exempt`` are always kept — the read-demand articles whose removal would disconnect a reader
    from the recommendation graph. ``max_age_days`` defaults to :func:`feed_max_age_days`;
    ``None``/``<=0`` disables the gate entirely (returns ``articles`` unchanged), including
    ``require_dated``."""
    window = feed_max_age_days() if max_age_days is None else (max_age_days if max_age_days > 0 else None)
    if window is None:
        return list(articles)
    need_dated = feed_require_dated() if require_dated is None else bool(require_dated)
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=float(window))
    kept = []
    for a in articles:
        if _canonical(a) in exempt:
            kept.append(a)
            continue
        if need_dated and not _has_publication_date(a):
            continue
        dt = _published(a, _CANDIDACY_TIME_KEYS)
        if dt is None or dt >= cutoff:
            kept.append(a)
    return kept


# --------------------------------------------------------------------------- #
# Metrics + thresholds.
# --------------------------------------------------------------------------- #
def thresholds_from_env() -> dict:
    """The single definition of "what a healthy corpus needs" — floors (used by retention *and* the
    validation gate) plus ceilings (read only by the validation gate). Retention consults just the
    floor keys, so adding the ceiling keys leaves ``plan_retention``/``run_retention`` unchanged.
    Every threshold is optional; ``0`` / unset means the corresponding check is off."""
    return {
        # Floors — shared with retention.
        "minArticles": _int_env("RWE_CORPUS_MIN_ARTICLES", _int_env("RWE_FEED_MIN_ARTICLES", 50)),
        "minPublishers": _int_env("RWE_CORPUS_MIN_PUBLISHERS", 0),
        "minPerBucket": _int_env("RWE_CORPUS_MIN_PER_BUCKET", 0),
        "minFresh": _int_env("RWE_CORPUS_MIN_FRESH", 0),
        "freshMaxAgeDays": _int_env("RWE_CORPUS_FRESH_MAX_AGE_DAYS", 3),
        # Ceilings — read only by the corpus-validation gate (examples/corpus_validation.py).
        # Retention never consults these; percentages are on a 0–100 scale (like duplicatePct).
        "maxPerPublisher": _int_env("RWE_CORPUS_MAX_PER_PUBLISHER", 0),
        "maxBucketPercent": _float_env("RWE_CORPUS_MAX_BUCKET_PERCENT", 0.0),
        "maxArticleAgeDays": _int_env("RWE_CORPUS_MAX_ARTICLE_AGE_DAYS", 0),
        "maxDuplicatePct": _float_env("RWE_CORPUS_MAX_DUPLICATE_PERCENT", 0.0),
        "maxMissingMetadataPct": _float_env("RWE_CORPUS_MAX_MISSING_METADATA_PERCENT", 0.0),
        "requireHealthyFeeds": _bool_env("RWE_CORPUS_REQUIRE_HEALTHY_FEEDS", False),
    }


def corpus_metrics(articles: list, *, now: Optional[datetime] = None,
                   fresh_max_age_days: Optional[int] = None) -> dict:
    """Health snapshot of a catalog/corpus: totals, diversity, duplicates, freshness, age span."""
    now = now or datetime.now(timezone.utc)
    fresh_days = fresh_max_age_days if fresh_max_age_days is not None \
        else _int_env("RWE_CORPUS_FRESH_MAX_AGE_DAYS", 3)
    total = len(articles)
    pub: Counter = Counter()
    bkt = {b: 0 for b in _BUCKETS}
    times = []
    fresh = 0
    seen = set()
    dups = 0
    missing = 0
    for a in articles:
        o = _outlet(a)
        if o:
            pub[o] += 1
        b = _bucket(a)
        if b:
            bkt[b] += 1
        dt = _published(a)
        if dt:
            times.append(dt)
            if (now - dt).total_seconds() <= fresh_days * 86400:
                fresh += 1
        cu = _canonical(a)
        if cu in seen:
            dups += 1
        elif cu:
            seen.add(cu)
        if _missing_metadata(a):
            missing += 1
    return {
        "total": total,
        "publishers": len(pub),
        "perPublisher": dict(pub),
        "perBucket": bkt,
        "duplicatePct": round(100.0 * dups / total, 2) if total else 0.0,
        "fresh": fresh,
        "freshMaxAgeDays": fresh_days,
        "missingMetadata": missing,
        "missingMetadataPct": round(100.0 * missing / total, 2) if total else 0.0,
        "oldest": min(times).isoformat() if times else None,
        "newest": max(times).isoformat() if times else None,
    }


# --------------------------------------------------------------------------- #
# Validation-aware, monotonic retention planner.
# --------------------------------------------------------------------------- #
def plan_retention(articles: list, *, max_age_days: Optional[float] = None,
                   max_count: Optional[int] = None, thresholds: Optional[dict] = None,
                   now: Optional[datetime] = None) -> dict:
    """Decide which articles to prune. Returns ``keep`` / ``prune`` canonical-URL lists plus stats.

    Steps: (1) flag the raw age/count prune candidates (newest-first order), then (2) a repair pass
    pulls the *newest* pruned articles back until each floor is met — per-bucket, then publishers,
    then fresh, then total. Every pull-back only moves an article prune->keep, so the final prune set
    is always a subset of the raw policy set: retention cannot breach a floor."""
    now = now or datetime.now(timezone.utc)
    th = thresholds or thresholds_from_env()
    fresh_days = th["freshMaxAgeDays"]

    def is_fresh(a):
        dt = _published(a)
        return dt is not None and (now - dt).total_seconds() <= fresh_days * 86400

    ordered = sorted(articles, key=lambda a: _published(a) or _EPOCH, reverse=True)  # newest first
    n = len(ordered)

    # (1) raw policy: keep[i] True/False
    keep = [True] * n
    for i, a in enumerate(ordered):
        if max_count and i >= max_count:
            keep[i] = False
        if max_age_days:
            dt = _published(a)
            if dt is None or (now - dt).total_seconds() > max_age_days * 86400:
                keep[i] = False
    raw_pruned = sum(1 for k in keep if not k)

    # running tallies of the KEPT set (kept up front so the repair pass is O(n) per floor)
    pub: Counter = Counter()
    bkt: Counter = Counter()
    total = 0
    fresh = 0
    for i, a in enumerate(ordered):
        if keep[i]:
            total += 1
            o = _outlet(a)
            if o:
                pub[o] += 1
            b = _bucket(a)
            if b:
                bkt[b] += 1
            if is_fresh(a):
                fresh += 1

    def pull_back(i):
        nonlocal total, fresh
        keep[i] = True
        total += 1
        o = _outlet(ordered[i])
        if o:
            pub[o] += 1
        b = _bucket(ordered[i])
        if b:
            bkt[b] += 1
        if is_fresh(ordered[i]):
            fresh += 1

    # (2) repair — retain older articles until floors hold (newest pruned first; best-effort if scarce)
    for b in _BUCKETS:
        if th["minPerBucket"] > 0:
            for i, a in enumerate(ordered):
                if bkt[b] >= th["minPerBucket"]:
                    break
                if not keep[i] and _bucket(a) == b:
                    pull_back(i)
    if th["minPublishers"] > 0:
        for i, a in enumerate(ordered):
            if len(pub) >= th["minPublishers"]:
                break
            o = _outlet(a)
            if not keep[i] and o and pub[o] == 0:   # a publisher not yet represented in keep
                pull_back(i)
    if th["minFresh"] > 0:
        for i, a in enumerate(ordered):
            if fresh >= th["minFresh"]:
                break
            if not keep[i] and is_fresh(a):
                pull_back(i)
    if th["minArticles"] > 0:
        for i in range(n):
            if total >= th["minArticles"]:
                break
            if not keep[i]:
                pull_back(i)

    keep_urls = [_canonical(ordered[i]) for i in range(n) if keep[i]]
    prune_urls = [_canonical(ordered[i]) for i in range(n) if not keep[i] and _canonical(ordered[i])]
    return {
        "keep": keep_urls,
        "prune": prune_urls,
        "kept": len(keep_urls),
        "pruned": len(prune_urls),
        "rawPruned": raw_pruned,                         # what a naive policy would have removed
        "retainedForFloor": raw_pruned - len(prune_urls),  # articles kept to satisfy the floors
        "thresholds": th,
    }


def retention_enabled() -> bool:
    """Retention runs only when an age or count policy is configured."""
    return bool(_int_env("RWE_RETENTION_MAX_AGE_DAYS", 0) or _int_env("RWE_RETENTION_MAX_COUNT", 0))


def run_retention(store_, *, max_age_days: Optional[float] = None, max_count: Optional[int] = None,
                  thresholds: Optional[dict] = None, log=None, now: Optional[datetime] = None) -> dict:
    """Load the catalog, plan a validation-aware prune, delete the (floor-respecting) prune set from
    ``feed_articles`` only, and log it. Returns the plan stats + post-prune metrics."""
    log = log or _default_log
    if max_age_days is None:
        max_age_days = _int_env("RWE_RETENTION_MAX_AGE_DAYS", 0) or None
    if max_count is None:
        max_count = _int_env("RWE_RETENTION_MAX_COUNT", 0) or None
    if not max_age_days and not max_count:
        return {"pruned": 0, "kept": store_.count_feed_articles(), "skipped": "no_policy"}

    articles = store_.list_feed_articles(limit=10_000_000)
    plan = plan_retention(articles, max_age_days=max_age_days, max_count=max_count,
                          thresholds=thresholds, now=now)
    deleted = store_.delete_feed_articles(plan["prune"]) if plan["prune"] else 0
    metrics = corpus_metrics([a for a in articles if _canonical(a) in set(plan["keep"])],
                             now=now, fresh_max_age_days=(thresholds or plan["thresholds"])["freshMaxAgeDays"])
    log(logging.INFO, "feed_retention", pruned=deleted, kept=plan["kept"],
        retainedForFloor=plan["retainedForFloor"], catalog=store_.count_feed_articles(),
        publishers=metrics["publishers"], perBucket=metrics["perBucket"], fresh=metrics["fresh"])
    return {"pruned": deleted, "kept": plan["kept"], "retainedForFloor": plan["retainedForFloor"],
            "rawPruned": plan["rawPruned"], "metrics": metrics}


def main() -> int:
    """Run retention once against the default DB (manual/cron use)."""
    import store
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    res = run_retention(store.Store())
    print(json.dumps(res, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
