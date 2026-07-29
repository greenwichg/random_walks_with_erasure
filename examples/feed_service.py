"""Automatic RSS polling — a background service that keeps the ``FeedArticle`` catalog fresh by
periodically running the **existing** ``rss_ingest`` pipeline.

Additive and opt-in. It adds only a loop, a thread, config, and structured logging *around*
``rss_ingest.ingest_all`` — it does **not** duplicate ingestion, and it touches no recommendation
algorithm, ranking, scoring, selection, personalization, report calculation, or protected module.
Ingestion stays incremental and deduplicated by canonical URL (``upsert_feed_article``), so a poll
only ever imports genuinely new articles; a repeat of a seen article is a no-op.

This commit fills the catalog only. Turning a refreshed catalog into a live recommendation corpus
(retention, feed health, validation, and the atomic hot swap) is deliberately *out of scope here* —
so ``on_cycle`` is provided as the seam a later commit wires the hot refresh into, and by itself the
poller never rebuilds the corpus. The browser extension remains the single source of truth for reads.

Disabled unless ``RWE_FEED_POLL`` is truthy (and, in the serving layer, also ``RWE_RECS_SOURCE=feed``).

Config (all optional, env-overridable):
    RWE_FEED_POLL         enable the poller (1/true/yes/on)         default off
    RWE_POLL_INTERVAL     seconds between cycles                    default 600 (10 min)
    RWE_POLL_TIMEOUT      per-feed fetch timeout (seconds)          default 15
    RWE_POLL_RETRIES      per-feed fetch retries on failure         default 2
    RWE_POLL_BACKOFF      base seconds for exponential retry backoff default 2
    RWE_RSS_FEEDS         feeds file path or comma-list (Name|url)  (shared with rss_ingest)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

import rss_ingest      # reuse: load_feeds, make_scorer, fetch_feed, ingest_all — NO duplicate ingestion
import corpus_health
import storage_lifecycle   # validation-aware FeedArticle retention (runs after each ingest cycle)
import story_service       # warm the clustered-story cache after ingest (off the request path)

DEFAULT_INTERVAL = 600.0    # 10 minutes
DEFAULT_TIMEOUT = 15.0
DEFAULT_RETRIES = 2
DEFAULT_BACKOFF = 2.0
_MAX_BACKOFF = 30.0

_logger = logging.getLogger("ih.feed_poll")


def enabled() -> bool:
    """Whether the automatic polling service should run (``RWE_FEED_POLL``)."""
    return os.environ.get("RWE_FEED_POLL", "").strip().lower() in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    v = os.environ.get(name)
    try:
        return float(v) if v not in (None, "") else default
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    v = os.environ.get(name)
    return int(v) if v and v.lstrip("-").isdigit() else default


# --------------------------------------------------------------------------- #
# Feed staleness — a freshness axis over the (already-tracked) newest article date.
#
# Distinct from availability: a feed can poll SUCCESSFULLY (healthy) yet only serve old content — a
# retired/frozen feed that still responds. This is observational, exactly like the rest of Feed
# Health: it never stops polling a stale feed (so it can recover automatically) and never excludes it
# from the recommendation corpus (that decision stays with Corpus Validation).
# --------------------------------------------------------------------------- #
DEFAULT_STALE_DAYS = 30


def stale_days() -> int:
    """Freshness threshold in days: a feed whose newest published article is older than this reads as
    **stale**. Configurable via ``RWE_FEED_STALE_DAYS`` (default 30); ``0`` or negative disables
    staleness flagging entirely."""
    return _int_env("RWE_FEED_STALE_DAYS", DEFAULT_STALE_DAYS)


def _parse_iso(value) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp (as stored in ``FeedHealth.newestPublished``) to an aware datetime,
    or ``None`` if absent/unparseable. A naive timestamp is assumed UTC."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def newest_age_days(record: dict, *, now: Optional[datetime] = None) -> Optional[float]:
    """Age in days of a feed's newest published article (from its ``newestPublished``), relative to
    ``now``. ``None`` when the feed has no dated article yet (unknown age, not old)."""
    dt = _parse_iso(record.get("newestPublished"))
    if dt is None:
        return None
    now = now or datetime.now(timezone.utc)
    return (now - dt).total_seconds() / 86400.0


def annotate_staleness(records, *, threshold_days: Optional[int] = None,
                       now: Optional[datetime] = None) -> list:
    """Additively annotate FeedHealth records with **staleness**, computed at read time from each
    feed's newest-article age vs the ``RWE_FEED_STALE_DAYS`` threshold. Adds three fields per record:

        newestAgeDays       float | None   age (days) of the newest published article; None if undated
        staleThresholdDays  int            the threshold in effect
        stale               bool           the newest article is older than the threshold

    Staleness is a **separate axis from availability**: a feed can poll successfully (``healthy``) yet
    be ``stale`` because it only serves old content (a frozen/retired feed that still responds). A feed
    with no dated article is not stale (unknown, not old); a threshold ``<= 0`` disables flagging. Pure
    and read-time — it stores nothing, stops no polling, and touches no corpus."""
    threshold = stale_days() if threshold_days is None else int(threshold_days)
    now = now or datetime.now(timezone.utc)
    annotated = []
    for r in records:
        age = newest_age_days(r, now=now)
        stale = threshold > 0 and age is not None and age > threshold
        annotated.append({**r, "newestAgeDays": (round(age, 2) if age is not None else None),
                          "staleThresholdDays": threshold, "stale": stale})
    return annotated


def _default_log(level: int, event: str, **fields) -> None:
    """Structured (JSON) log line — mirrors the engine's format for standalone/CLI use. The serving
    layer injects its own ``_log`` so lines share one format across the process."""
    _logger.log(level, json.dumps({"event": event, **fields}, default=str))


class FeedPoller:
    """A background RSS poller. Reuses the whole ``rss_ingest`` pipeline; adds the loop + resilience.

    Resilience is layered: ``ingest_all`` already isolates a single feed's failure (the rest still
    ingest); fetch failures are retried with capped exponential backoff; and a whole-cycle exception
    is caught so a bug can never kill the loop. Shutdown is graceful — the between-cycle sleep and the
    retry backoff both wait on a stop event, so ``stop()`` interrupts them promptly.
    """

    def __init__(self, store_, *, interval: Optional[float] = None, timeout: Optional[float] = None,
                 retries: Optional[int] = None, backoff: Optional[float] = None,
                 feeds_spec: Optional[str] = None,
                 log: Optional[Callable[..., None]] = None,
                 on_cycle: Optional[Callable[[dict], None]] = None):
        self.store = store_
        self.interval = interval if interval is not None else _float_env("RWE_POLL_INTERVAL", DEFAULT_INTERVAL)
        self.timeout = timeout if timeout is not None else _float_env("RWE_POLL_TIMEOUT", DEFAULT_TIMEOUT)
        self.retries = retries if retries is not None else _int_env("RWE_POLL_RETRIES", DEFAULT_RETRIES)
        self.backoff = backoff if backoff is not None else _float_env("RWE_POLL_BACKOFF", DEFAULT_BACKOFF)
        self.feeds_spec = feeds_spec
        self.unhealthy_after = _int_env("RWE_FEED_UNHEALTHY_AFTER", 3)   # consecutive failures -> unhealthy
        self._log = log or _default_log
        self._on_cycle = on_cycle                 # future hot-refresh seam; unused by the poller itself
        self._scorer = rss_ingest.make_scorer()   # the SAME scorer the reading pipeline uses
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- per-feed health recording (observational; never affects articles/corpus/recs) ----------
    def _record_health(self, name, url, stats, latency_ms, error) -> None:
        """``ingest_all`` on_feed callback: persist this feed's poll result to FeedHealth. Failures
        increment the consecutive-failure counter (unhealthy at the threshold); a success resets it.
        Purely observational — it writes only the feed_health table."""
        rec = self.store.record_feed_health(
            url, ok=(error is None), name=name, latency_ms=latency_ms,
            error=(f"{type(error).__name__}: {error}" if error is not None else None),
            stats=stats or {}, unhealthy_after=self.unhealthy_after)
        if error is not None:
            self._log(logging.WARNING, "feed_health", feed=url, healthy=rec["healthy"],
                      consecutiveFailures=rec["consecutiveFailures"],
                      latencyMs=round(latency_ms, 1), error=rec["lastError"])
        if rec.get("transition") == "unhealthy":
            self._log(logging.WARNING, "feed_unhealthy", feed=url,
                      consecutiveFailures=rec["consecutiveFailures"])
        elif rec.get("transition") == "recovered":
            self._log(logging.INFO, "feed_recovered", feed=url)

    # -- fetch with interruptible, capped exponential retry --------------------------------------
    def _make_fetch(self) -> Callable[[str], bytes]:
        def fetch(url: str) -> bytes:
            attempt = 0
            while True:
                try:
                    return rss_ingest.fetch_feed(url, timeout=self.timeout)
                except Exception:
                    attempt += 1
                    if attempt > self.retries or self._stop.is_set():
                        raise
                    delay = min(self.backoff * (2 ** (attempt - 1)), _MAX_BACKOFF)
                    if self._stop.wait(delay):     # interrupted by shutdown -> give up this feed
                        raise
        return fetch

    # -- one cycle ------------------------------------------------------------------------------
    def poll_once(self) -> dict:
        """Run one ingest cycle over the configured feeds. Never raises for a feed failure; returns
        the aggregate stats (incl. ``new`` = genuinely new articles this cycle)."""
        feeds = rss_ingest.load_feeds(self.feeds_spec)
        if not feeds:
            self._log(logging.WARNING, "feed_poll_no_feeds",
                      hint="set RWE_RSS_FEEDS or pass a feeds spec")
            return {"feeds": 0, "ok": 0, "failed": 0, "entries": 0, "new": 0,
                    "duplicates": 0, "skipped": 0, "errors": [], "catalog": self.store.count_feed_articles()}

        t0 = time.perf_counter()
        agg = rss_ingest.ingest_all(feeds, self._scorer, self.store,
                                    fetch=self._make_fetch(), on_feed=self._record_health)
        agg["durationMs"] = round((time.perf_counter() - t0) * 1000.0, 1)
        agg["catalog"] = self.store.count_feed_articles()

        self._log(logging.WARNING if agg["failed"] else logging.INFO, "feed_poll",
                  feeds=agg["feeds"], ok=agg["ok"], failed=agg["failed"], new=agg["new"],
                  duplicates=agg["duplicates"], skipped=agg["skipped"],
                  catalog=agg["catalog"], durationMs=agg["durationMs"])
        for err in agg.get("errors", []):
            self._log(logging.WARNING, "feed_poll_feed_error", feed=err["feed"], error=err["error"])

        # Validation-aware retention: prune old/excess FeedArticles, but never below the floors that a
        # healthy replacement corpus needs (corpus_health guarantees the prune set respects them).
        # FeedArticle only — reads / reports / analytics / rec-events are never touched.
        try:
            ret = storage_lifecycle.run_cleanup(self.store, log=self._log)
            agg["retention"] = ret
            agg["catalog"] = self.store.count_feed_articles()   # reflect the post-retention size
        except Exception as e:                                  # cleanup must never break polling
            self._log(logging.WARNING, "storage_cleanup_failed", error=f"{type(e).__name__}: {e}")

        # REQUEST a story rebuild rather than performing one here — same reasoning as the
        # MultiSourcePoller's copy of this seam (see sources.py::_post_cycle). The rebuild the
        # ingest forces still happens off the request path; it just happens on the warmer thread,
        # coalesced with any other provider that wrote at about the same time, and without holding
        # this poller's thread for the 5.6 s the build takes.
        # Fail-soft: a warm that cannot be built is a slow next request, never a broken poll loop.
        try:
            def _warm_log(event, **fields):
                self._log(logging.WARNING if event.endswith("_failed") else logging.INFO,
                          event, **fields)
            story_service.request_warm(self.store, log=_warm_log)
        except Exception as e:
            self._log(logging.WARNING, "story_cache_warm_failed", error=f"{type(e).__name__}: {e}")

        # Optional seam: a later commit hangs the (validated) hot corpus refresh off this — and only
        # when the catalog actually changed (agg["new"] > 0). The poller itself never refreshes it.
        if self._on_cycle is not None:
            try:
                self._on_cycle(agg)
            except Exception as e:      # a downstream hook must never break the poll loop
                self._log(logging.ERROR, "feed_poll_on_cycle_error", error=repr(e))
        return agg

    # -- loop + lifecycle -----------------------------------------------------------------------
    def _run(self) -> None:
        self._log(logging.INFO, "feed_poll_start", interval=self.interval, timeout=self.timeout,
                  retries=self.retries)
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception as e:      # never let one bad cycle kill the service
                self._log(logging.ERROR, "feed_poll_cycle_failed", error=repr(e))
            self._stop.wait(self.interval)     # interruptible between-cycle sleep (graceful shutdown)
        self._log(logging.INFO, "feed_poll_stopped")

    def start(self) -> None:
        """Start the background thread (idempotent). Polls once immediately, then every interval."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="feed-poller", daemon=True)
        self._thread.start()

    def stop(self, join_timeout: float = 10.0) -> None:
        """Signal shutdown and wait (bounded) for the thread to finish the current cycle."""
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=join_timeout)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


def main() -> int:
    """Run the poller standalone in the foreground (e.g. a dedicated sidecar). Ctrl-C stops it
    gracefully. Uses the default DB unless ``RWE_DB_URL`` overrides it."""
    import store
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    poller = FeedPoller(store.Store())
    poller.start()
    try:
        while poller.running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        poller.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
