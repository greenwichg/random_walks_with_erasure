"""Corpus validation — the eligibility gate between the ``FeedArticle`` catalog and the
recommendation corpus.

This module answers exactly one question: *"is a candidate corpus healthy enough to activate?"*
It builds a **candidate** (a publisher-capped subset of ``FeedArticle``), measures it, and returns a
``ValidationResult`` — nothing more. It NEVER activates a corpus, rebuilds ``Backend``, or touches a
single recommendation algorithm, ranking, score, or serialisation. Activation is a separate milestone
(the atomic hot swap); this module only decides *eligibility*.

Design guarantees (why this is safe to run anywhere, including on the request-free diagnostics path):

* **Read-only + pure.** ``validate_corpus`` reads article dicts and a feed-health snapshot and returns
  a result; it mutates nothing. ``build_candidate`` returns a subset of the *same* row objects,
  unmodified — no article is copied, duplicated, synthesised, re-titled, re-dated, or re-URL'd, so the
  candidate is always a strict subset of ``FeedArticle``.
* **Never throws.** Every entry point returns a ``ValidationResult``. ``evaluate`` wraps store access
  so an unexpected error yields an *ineligible* result (fail-closed: a corpus you couldn't validate is
  not eligible to activate) rather than raising into the caller.
* **Zero recommendation coupling.** It imports only ``corpus_health`` + stdlib — never ``api_server``,
  ``personalize``, ``simulate_users``, ``rwe``, or ``health_report`` — so validation cannot change how
  recommendations are built, ranked, scored, selected, or serialised.

Thresholds come from :func:`corpus_health.thresholds_from_env` (floors shared with retention, plus the
validation ceilings). Every check is independent and optional (``0`` / unset / ``False`` = off).
"""

from __future__ import annotations

import dataclasses
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import corpus_health as ch   # shared metrics + thresholds ONLY — no recommendation code

_logger = logging.getLogger("ih.corpus_validation")


# --------------------------------------------------------------------------- #
# Candidate construction — a publisher-capped, newest-first SUBSET of FeedArticle.
# --------------------------------------------------------------------------- #
def _sort_key(a: dict):
    """Newest publication first, canonical URL as a deterministic tiebreak. Undated rows sort last."""
    dt = ch._published(a)
    ts = dt.timestamp() if dt is not None else float("-inf")
    return (ts, ch._canonical(a))


def build_candidate(articles: list, *, max_per_publisher: Optional[int] = None,
                    now: Optional[datetime] = None,
                    max_age_days: Optional[float] = None) -> list:
    """A candidate corpus: the **fresh**, newest-first subset of ``articles`` keeping at most
    ``max_per_publisher`` per publisher (0 / ``None`` = no cap).

    Freshness (Commit C4) gates candidate *selection*: articles older than ``max_age_days``
    (default ``RWE_FEED_MAX_AGE_DAYS``, 60 days; ``0`` disables) never enter the candidate, so a
    stale article can never be recommended. The gate runs before the publisher cap, so caps are
    spent on recommendable articles only. Stale articles are untouched in storage — Search,
    Stories, and Reading History still see them.

    The result is a **subset of the same row objects** — never mutated, copied, duplicated, or
    synthesised — so URLs, titles, descriptions, and timestamps are exactly those of ``FeedArticle``.
    Articles with no resolvable publisher are always kept (they count toward no publisher's cap)."""
    articles = ch.fresh_articles(articles, now=now, max_age_days=max_age_days)
    ordered = sorted(articles, key=_sort_key, reverse=True)
    if not max_per_publisher or max_per_publisher <= 0:
        return list(ordered)
    kept: list = []
    per: dict = {}
    for a in ordered:
        outlet = ch._outlet(a).lower()
        if outlet:
            per[outlet] = per.get(outlet, 0) + 1
            if per[outlet] > max_per_publisher:
                continue        # this publisher already hit its cap — keep the candidate balanced
        kept.append(a)
    return kept


# --------------------------------------------------------------------------- #
# The validation result — a reusable, serialisable object. No activation, ever.
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class ValidationResult:
    eligible: bool
    status: str                 # "pass" | "fail" | "error"
    metrics: dict
    failures: list              # [{code, message, ...context}]
    warnings: list              # [{code, message, ...context}]
    thresholds: dict
    candidateSize: int
    generatedAt: str            # ISO-8601 validation timestamp

    def to_dict(self) -> dict:
        """Operator-facing shape for the diagnostics endpoint. Surfaces the distributions the spec
        calls out (publisher / political / freshness / feed health) top-level, alongside the full
        metrics block, the pass/fail verdict, and every failure + warning reason."""
        m = self.metrics
        return {
            "eligible": self.eligible,
            "status": self.status,
            "generatedAt": self.generatedAt,
            "candidateSize": self.candidateSize,
            "metrics": m,
            "publisherDistribution": m.get("perPublisher", {}),
            "politicalDistribution": m.get("perBucket", {}),
            "freshness": {
                "fresh": m.get("fresh"),
                "freshMaxAgeDays": m.get("freshMaxAgeDays"),
                "oldest": m.get("oldest"),
                "newest": m.get("newest"),
            },
            "healthyFeeds": m.get("healthyFeeds", 0),
            "unhealthyFeeds": m.get("unhealthyFeeds", 0),
            "failures": self.failures,
            "warnings": self.warnings,
            "thresholds": self.thresholds,
        }


def _newest_age_days(newest_iso: Optional[str], now: datetime) -> Optional[float]:
    if not newest_iso:
        return None
    try:
        dt = datetime.fromisoformat(newest_iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 86400.0


# --------------------------------------------------------------------------- #
# The gate — pure, never throws.
# --------------------------------------------------------------------------- #
def validate_corpus(articles: list, feed_health: Optional[list] = None, *,
                    thresholds: Optional[dict] = None, now: Optional[datetime] = None) -> ValidationResult:
    """Validate a candidate corpus against the configured floors + ceilings. Pure and total — it
    reads ``articles`` (already a candidate subset) and an optional ``feed_health`` snapshot and always
    returns a ``ValidationResult``; it never mutates its inputs and never raises. ``eligible`` is true
    iff there are no failures (warnings never block)."""
    now = now or datetime.now(timezone.utc)
    th = thresholds or ch.thresholds_from_env()
    feed_health = feed_health or []

    metrics = ch.corpus_metrics(articles, now=now, fresh_max_age_days=th["freshMaxAgeDays"])
    healthy = sum(1 for f in feed_health if f.get("healthy"))
    unhealthy = sum(1 for f in feed_health if not f.get("healthy"))
    metrics["healthyFeeds"] = healthy
    metrics["unhealthyFeeds"] = unhealthy

    failures: list = []
    warnings: list = []

    def fail(code: str, message: str, **ctx) -> None:
        failures.append({"code": code, "message": message, **ctx})

    def warn(code: str, message: str, **ctx) -> None:
        warnings.append({"code": code, "message": message, **ctx})

    total = metrics["total"]

    # -- floors ------------------------------------------------------------- #
    if th["minArticles"] > 0 and total < th["minArticles"]:
        fail("min_articles", "Too few articles to build a healthy corpus.",
             observed=total, required=th["minArticles"])
    if th["minPublishers"] > 0 and metrics["publishers"] < th["minPublishers"]:
        fail("min_publishers", "Too few distinct publishers.",
             observed=metrics["publishers"], required=th["minPublishers"])
    if th["minPerBucket"] > 0:
        deficient = {b: c for b, c in metrics["perBucket"].items() if c < th["minPerBucket"]}
        if deficient:
            fail("min_per_bucket", "A political bucket is under the minimum (viewpoint imbalance).",
                 buckets=deficient, required=th["minPerBucket"])
    if th["minFresh"] > 0 and metrics["fresh"] < th["minFresh"]:
        fail("min_fresh", "Too few fresh articles.",
             observed=metrics["fresh"], required=th["minFresh"])

    # -- ceilings ----------------------------------------------------------- #
    if th["maxPerPublisher"] > 0:
        over = {p: c for p, c in metrics["perPublisher"].items() if c > th["maxPerPublisher"]}
        if over:
            fail("max_per_publisher", "A publisher exceeds its cap (publisher imbalance).",
                 publishers=over, limit=th["maxPerPublisher"])
    if th["maxBucketPercent"] > 0:
        bucketed = sum(metrics["perBucket"].values())
        if bucketed > 0:
            shares = {b: round(100.0 * c / bucketed, 2) for b, c in metrics["perBucket"].items()}
            over = {b: s for b, s in shares.items() if s > th["maxBucketPercent"]}
            if over:
                fail("max_bucket_percent", "A political bucket dominates the corpus.",
                     shares=over, limit=th["maxBucketPercent"])
    if th["maxArticleAgeDays"] > 0:
        age = _newest_age_days(metrics["newest"], now)
        if age is None or age > th["maxArticleAgeDays"]:
            fail("max_article_age", "The corpus is stale — even the newest article is too old.",
                 newest=metrics["newest"], limitDays=th["maxArticleAgeDays"])
    if th["maxDuplicatePct"] > 0 and metrics["duplicatePct"] > th["maxDuplicatePct"]:
        fail("max_duplicate_pct", "Too many duplicate articles.",
             observed=metrics["duplicatePct"], limit=th["maxDuplicatePct"])
    if th["maxMissingMetadataPct"] > 0 and metrics["missingMetadataPct"] > th["maxMissingMetadataPct"]:
        fail("max_missing_metadata_pct", "Too many articles are missing title or publication date.",
             observed=metrics["missingMetadataPct"], limit=th["maxMissingMetadataPct"])

    # -- feed-health requirement (fail-closed) ------------------------------ #
    if th["requireHealthyFeeds"]:
        if unhealthy > 0:
            fail("unhealthy_feeds", "Unhealthy feeds present while healthy feeds are required.",
                 unhealthyFeeds=unhealthy, healthyFeeds=healthy)
        elif healthy == 0:
            fail("unhealthy_feeds", "No healthy feeds available to confirm feed health.",
                 unhealthyFeeds=unhealthy, healthyFeeds=healthy)
    elif unhealthy > 0:
        warn("unhealthy_feeds_present", "Some feeds are unhealthy (not required, so advisory only).",
             unhealthyFeeds=unhealthy)

    # -- advisory blind-spot warning when no per-bucket floor is set -------- #
    if th["minPerBucket"] == 0 and total > 0:
        empty = [b for b, c in metrics["perBucket"].items() if c == 0]
        if empty:
            warn("empty_bucket", "A political bucket has no coverage (potential blind spot).",
                 buckets=empty)

    eligible = not failures
    return ValidationResult(
        eligible=eligible, status="pass" if eligible else "fail", metrics=metrics,
        failures=failures, warnings=warnings, thresholds=th, candidateSize=total,
        generatedAt=now.isoformat())


def _error_metrics(th: dict) -> dict:
    return {"total": 0, "publishers": 0, "perPublisher": {}, "perBucket": {b: 0 for b in ch._BUCKETS},
            "duplicatePct": 0.0, "fresh": 0, "freshMaxAgeDays": th["freshMaxAgeDays"],
            "missingMetadata": 0, "missingMetadataPct": 0.0, "oldest": None, "newest": None,
            "healthyFeeds": 0, "unhealthyFeeds": 0}


def evaluate(store_, *, thresholds: Optional[dict] = None,
             now: Optional[datetime] = None) -> ValidationResult:
    """Load the catalog + feed-health snapshot, build a publisher-capped candidate, and validate it.
    The single entry point the diagnostics endpoint uses. Read-only over ``FeedArticle`` +
    ``feed_health``; it builds no ``Backend`` and activates nothing. Never throws — any unexpected
    error is logged and returned as an *ineligible* ``error`` result (fail-closed)."""
    now = now or datetime.now(timezone.utc)
    th = thresholds or ch.thresholds_from_env()
    try:
        articles = store_.list_feed_articles(limit=10_000_000)
        feed_health = store_.list_feed_health()
        candidate = build_candidate(articles, max_per_publisher=th["maxPerPublisher"] or None, now=now)
        return validate_corpus(candidate, feed_health, thresholds=th, now=now)
    except Exception as e:   # never let a diagnostics read raise into the request path
        _logger.error(json.dumps({"event": "corpus_validation_error", "error": repr(e)}))
        return ValidationResult(
            eligible=False, status="error", metrics=_error_metrics(th),
            failures=[{"code": "validation_error", "message": "Corpus validation could not run.",
                       "error": type(e).__name__}],
            warnings=[], thresholds=th, candidateSize=0, generatedAt=now.isoformat())


def main() -> int:
    """Validate the default DB's catalog once and print the result (manual/ops use)."""
    import store
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    print(json.dumps(evaluate(store.Store()).to_dict(), default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
