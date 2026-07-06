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
    RWE_RETENTION_MAX_AGE_DAYS   prune articles older than this           (0/unset = no age prune)
    RWE_RETENTION_MAX_COUNT      keep at most this many, newest-first      (0/unset = no count prune)
    RWE_CORPUS_MIN_ARTICLES      floor: min total (default RWE_FEED_MIN_ARTICLES, 50)
    RWE_CORPUS_MIN_PUBLISHERS    floor: min distinct publishers            (default 0 = off)
    RWE_CORPUS_MIN_PER_BUCKET    floor: min articles per left/center/right (default 0 = off)
    RWE_CORPUS_MIN_FRESH         floor: min fresh articles                 (default 0 = off)
    RWE_CORPUS_FRESH_MAX_AGE_DAYS   what counts as "fresh"                 (default 3)
"""

from __future__ import annotations

import json
import logging
import math
import os
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

_BUCKETS = ("left", "center", "right")
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)   # undated articles sort oldest (pruned first)
_logger = logging.getLogger("ih.corpus")


def _int_env(name: str, default: int) -> int:
    v = os.environ.get(name)
    return int(v) if v and v.lstrip("-").isdigit() else default


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


def _published(a: dict) -> Optional[datetime]:
    for key in ("publishedAt", "fetchedAt"):
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


# --------------------------------------------------------------------------- #
# Metrics + thresholds.
# --------------------------------------------------------------------------- #
def thresholds_from_env() -> dict:
    return {
        "minArticles": _int_env("RWE_CORPUS_MIN_ARTICLES", _int_env("RWE_FEED_MIN_ARTICLES", 50)),
        "minPublishers": _int_env("RWE_CORPUS_MIN_PUBLISHERS", 0),
        "minPerBucket": _int_env("RWE_CORPUS_MIN_PER_BUCKET", 0),
        "minFresh": _int_env("RWE_CORPUS_MIN_FRESH", 0),
        "freshMaxAgeDays": _int_env("RWE_CORPUS_FRESH_MAX_AGE_DAYS", 3),
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
    return {
        "total": total,
        "publishers": len(pub),
        "perPublisher": dict(pub),
        "perBucket": bkt,
        "duplicatePct": round(100.0 * dups / total, 2) if total else 0.0,
        "fresh": fresh,
        "freshMaxAgeDays": fresh_days,
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
