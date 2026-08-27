"""sources.py — the pluggable multi-source ingestion layer (Commit 11).

Every ingestion source (RSS/Atom, NewsAPI, The Guardian, NewsData.io, GNews, MediaStack, Currents,
Google News RSS, GDELT, and future providers) is a :class:`SourceAdapter` that **normalizes its data
into the existing** :class:`rss_ingest.FeedEntry` and terminates at the existing
``rss_ingest.ingest_entries`` pipeline. After that boundary the whole platform — scoring,
canonical-URL dedup, media selection, persistence, search, clustering, Story Intelligence,
recommendations — behaves **exactly as it does for RSS today** and never learns where an article came
from.

    any provider  ->  SourceAdapter.fetch()  ->  normalize()  ->  SourceBatch(FeedEntry[])
                  ->  ingest_entries()       ->  FeedArticle  ->  everything else

Design:
  * Adapters reuse the ingestion pipeline; they never duplicate scoring/dedup/media/persistence.
  * RSS reuses ``rss_ingest.ingest_all`` verbatim (identical behaviour, per-feed health).
  * Keyed JSON providers (NewsAPI, Guardian, NewsData, GNews, MediaStack, Currents) share one chassis,
    :class:`KeyedJSONAdapter`: env-prefix config, combo rotation, daily budgets, 429 accounting, retry
    with backoff. A concrete adapter is ~40 lines: URL + payload mapping.
  * A :class:`SourceRegistry` holds the adapters; :class:`MultiSourcePoller` iterates the enabled ones,
    so the poller is provider-agnostic and future adapters need no poller change.
  * Health reuses ``store.record_feed_health`` under a stable per-source key (``rss://…`` per feed,
    ``newsapi://top-headlines``, ``guardian://search``, ``gdelt://doc``, …). ``feed_service.FeedPoller``
    is left untouched.
  * Publisher identity flows through ``FeedEntry.publisher_hint`` into the outlet registry — new
    providers resolve publishers/leans through the same registry; unresolved outlets stay honest
    (no lean) until curated.

No network is contacted unless an adapter is enabled; ``fetch`` is injectable so tests run offline.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import random
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

import rss_ingest      # reuse: FeedEntry, load_feeds/fetch_feed/parse_feed, ingest_entries, ingest_all
import feed_schedule   # per-feed cadence + conditional-GET policy (pure; off unless enabled)
import media           # reuse: pick_best_image (image SELECTION only — never modified, never downloads)
import corpus_health   # reuse: validation-aware retention (post-cycle, exactly as FeedPoller runs it)
import storage_lifecycle  # reuse: the ONE bounded cleanup pass (catalog + derived tables)
import story_service      # reuse: warm the clustered-story cache after ingest (off the request path)
import gdelt_gkg       # reuse: the Phase-2 event-geography enrichment logic (offline-testable)
import publisher_metadata  # reuse: the bounded Wikipedia/Wikidata publisher enrichment pass
import publisher_wiki      # reuse: the Wikimedia-compliant User-Agent (their policy requires one)

_USER_AGENT = "InformationHealth-Sources/0.1 (+https://code.claude.com)"
_TRUE = {"1", "true", "yes", "on"}
_logger = logging.getLogger("ih.sources")

# A sensible default GDELT DOC 2.0 query: real news topics. A bare ``sourcelang:english`` (no keyword)
# returns a degenerate, non-news result set (e.g. google.com help pages), so the default carries topic
# keywords. Override with ``RWE_GDELT_QUERY``.
DEFAULT_GDELT_QUERY = "(politics OR economy OR election OR climate OR world) sourcelang:english"


# --------------------------------------------------------------------------- #
# Config helpers
# --------------------------------------------------------------------------- #
def _bool_env(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in _TRUE


def _int_env(name: str, default: int) -> int:
    v = os.environ.get(name)
    return int(v) if v and v.lstrip("-").isdigit() else default


def _float_env(name: str, default: float) -> float:
    v = os.environ.get(name)
    try:
        return float(v) if v not in (None, "") else default
    except ValueError:
        return default


#: How long `_post_cycle_unlocked` waits to re-take the ingest lock for breaking-story detection
#: before giving up on this cycle and saying so.
#:
#: Its purpose is to make an IMPOSSIBLE wait visible, not to tune anything: a genuine deadlock never
#: resolves, so any finite value catches it, and the only cost of a large one is that thread sitting
#: idle — which now blocks nobody, because the warm ahead of it no longer holds the lock either.
#:
#: 120 s was the first guess and it was WRONG, on reasoning that was too narrow: it cleared a single
#: ~96 s maintenance pass but ignored QUEUEING behind one. Production fired the timeout once in an
#: hour at 16% occupancy. Sized now against the real shape — a maintenance pass plus several
#: adapters queued behind it — rather than against one pass in isolation.
_BREAKING_LOCK_TIMEOUT_S = 600.0


def _poll_workers() -> int:
    """Size of the bounded poll pool. **0 = one thread per adapter**, the pre-M6.3 model.

    Zero by default so deploying M6.3 changes nothing: at the 13 adapters production runs today a pool buys literally
    nothing, because thread-per-adapter and an 11-worker pool schedule identically. It starts
    mattering in the hundreds, which is exactly when an operator sets it — and until then the safest
    behaviour for a scheduler change on the ingest path is the behaviour that already runs.

    The cap it imposes is on CONCURRENT FETCHES, not on sources. That distinction is the whole
    milestone: source count and thread count were the same number until now, and 2,200 sources (the
    ceiling M6.2 measured) means 2,200 threads and 2,200 simultaneous outbound connections under the
    old model. Neither is a thing a 2-vCPU box should attempt, and the second is a politeness
    problem as much as a resource one.
    """
    return max(0, _int_env("RWE_POLL_WORKERS", 0))


def _maintenance_interval() -> float:
    """Minimum seconds between catalog-wide post-cycle passes (retention + hot refresh).

    600 s matches ``RWE_POLL_INTERVAL``, which is what "one pass per polling window" means: with
    13 adapters the old per-cycle behaviour ran that pass up to 13 times per window, each superseding
    the last, at a cost set by catalog size rather than by what any adapter brought.

    **0 restores the previous behaviour exactly** — every ingesting cycle runs its own pass — so
    the change has an off switch that does not require a rollback. Read per call rather than
    cached at construction, so an operator can retune it on a running process.
    """
    return _float_env("RWE_POST_CYCLE_MAINTENANCE_INTERVAL", 600.0)


def _int_or_none(name: str) -> Optional[int]:
    v = os.environ.get(name)
    return int(v) if v and v.lstrip("-").isdigit() else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _backoff_delay(attempt: int, base: float, cap: float = 60.0) -> float:
    """Exponential backoff with half-jitter: ``d/2 + rand(0, d/2)`` where ``d = base * 2^(n-1)``.

    Half rather than full jitter because the floor matters — a retry that can fire ~immediately is
    no retry at all against a server that just refused us."""
    d = min(base * (2 ** max(0, attempt - 1)), cap)
    return d / 2.0 + random.random() * (d / 2.0)


def _retry_after_seconds(e) -> Optional[float]:
    """``Retry-After`` in delta-seconds form, or ``None`` (absent, or the HTTP-date form we don't
    parse). The server telling us exactly when to return is worth more than any backoff guess."""
    try:
        v = e.headers.get("Retry-After") if getattr(e, "headers", None) else None
    except Exception:
        return None
    if not v:
        return None
    try:
        return max(0.0, float(str(v).strip()))
    except ValueError:
        return None


def _request(url: str, *, read, headers=None, timeout: float,
             retries: Optional[int] = None, backoff: Optional[float] = None,
             on_transient: Optional[Callable[[int], None]] = None):
    """One HTTP GET with the shared retry discipline. ``read(resp)`` turns the response into the
    caller's value (JSON, bytes, …). The retry policy differs by failure class, deliberately:

    * **429 Too Many Requests** — retried ONLY when the server sends a ``Retry-After`` we are
      willing to wait for; otherwise it raises immediately. Retrying a rate limit on a short ladder
      sends MORE traffic into a limit we are already over, and a background poller's next scheduled
      cycle is the right retry. Verified on GDELT (2026-07-27): every 429 arrives with **no**
      ``Retry-After``, so this path already makes exactly one request per refused cycle. An earlier
      version of this note claimed four requests and ~30 s of sleeping per cycle — that was inferred
      from a latency figure, never measured, and it was wrong: the time was going into read
      timeouts, not retries.
    * **5xx** — a genuine transient server fault; retried with exponential backoff + jitter.
    * **Connection-level failures** (SSL handshake, DNS, connect/read timeout, reset, truncated
      body) — retried the same way. These previously escaped the loop entirely: only ``HTTPError``
      was caught, and ``URLError`` is its PARENT, not its child, so an SSL or timeout failure was
      never retried however high ``RWE_SOURCE_RETRIES`` was set.

    ``on_transient`` is called with the HTTP code for every 429/5xx — including the one that gives
    up — so callers count rate-limit events instead of the loop absorbing them.

    **A single call has a total sleep budget** (``RWE_SOURCE_MAX_WAIT``, default 60 s). Retry counts
    and per-wait ceilings do not bound wall-clock time on their own: with three retries and a 120 s
    ``Retry-After`` ceiling, one polite server could hold a poller thread for six minutes. The
    budget makes the worst case a number you can state, rather than the product of three knobs
    nobody multiplies together."""
    retries = _int_env("RWE_SOURCE_RETRIES", 3) if retries is None else retries
    backoff = _float_env("RWE_SOURCE_BACKOFF", 5.0) if backoff is None else backoff
    retry_after_max = _float_env("RWE_SOURCE_RETRY_AFTER_MAX", 120.0)
    max_wait = _float_env("RWE_SOURCE_MAX_WAIT", 60.0)
    slept = 0.0
    req = urllib.request.Request(url, headers=headers or {})
    attempt = 0

    def _sleep(seconds: float) -> bool:
        """Sleep if the budget allows. False means the caller must give up instead of waiting."""
        nonlocal slept
        if seconds <= 0:
            return True
        if slept + seconds > max_wait:
            return False
        time.sleep(seconds)
        slept += seconds
        return True

    while True:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return read(resp)
        except urllib.error.HTTPError as e:                 # HTTPError first: it subclasses URLError
            attempt += 1
            if e.code == 429 or 500 <= e.code < 600:
                if on_transient is not None:
                    try:
                        on_transient(e.code)
                    except Exception:
                        pass                                # metrics must never break the fetch
            if e.code == 429:
                wait = _retry_after_seconds(e)
                if wait is None or wait > retry_after_max or attempt > retries:
                    raise
                if not _sleep(wait):
                    raise
                continue
            if not (500 <= e.code < 600) or attempt > retries:
                raise
            if not _sleep(_backoff_delay(attempt, backoff)):
                raise
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError):
            attempt += 1
            if attempt > retries:
                raise
            if not _sleep(_backoff_delay(attempt, backoff)):
                raise


def _get_json(url: str, *, headers=None, timeout: float = 15.0,
              retries: Optional[int] = None, backoff: Optional[float] = None,
              on_transient: Optional[Callable[[int], None]] = None) -> dict:
    """HTTP GET -> parsed JSON. See :func:`_request` for the retry policy."""
    return _request(url, read=lambda r: json.loads(r.read()), headers=headers, timeout=timeout,
                    retries=retries, backoff=backoff, on_transient=on_transient)


def _get_bytes(url: str, *, headers=None, timeout: float = 30.0,
               retries: Optional[int] = None, backoff: Optional[float] = None) -> bytes:
    """HTTP GET -> raw bytes, same retry policy as :func:`_get_json`. Used for the GKG zip +
    manifest, which are files, not JSON."""
    return _request(url, read=lambda r: r.read(), headers=headers or {"User-Agent": _USER_AGENT},
                    timeout=timeout, retries=retries, backoff=backoff)


def _default_log(level: int, event: str, **fields) -> None:
    _logger.log(level, json.dumps({"event": event, **fields}, default=str))


def _host_hint(value) -> Optional[str]:
    """A publisher hint from a URL-or-name: URL forms reduce to their bare host, www-stripped
    ("https://www.oricon.co.jp" -> "oricon.co.jp"). The registry resolves KNOWN outlets from either
    form — this is about honest naming for the UNKNOWN ones, whose hint is stored verbatim as the
    outlet name (and feeds the unrated-publishers worklist); a scheme-bearing URL there is noise
    (observed in production with GNews sources, 2026-07-26)."""
    v = (value or "").strip()
    if not v:
        return None
    if "://" in v:
        host = urllib.parse.urlsplit(v).netloc.lower()
        host = host[4:] if host.startswith("www.") else host
        return host or None
    return v


# --------------------------------------------------------------------------- #
# SourceBatch — lightweight metadata around one adapter fetch (metrics/diagnostics/logging/replay).
# It is NOT persisted and NO downstream consumer reads it — only its ``entries`` reach the pipeline.
# --------------------------------------------------------------------------- #
@dataclass
class SourceBatch:
    provider: str
    source_type: str
    fetched_at: str
    entries: list = field(default_factory=list)
    raw_count: int = 0
    error: Optional[str] = None

    def __len__(self) -> int:
        return len(self.entries)


# --------------------------------------------------------------------------- #
# SourceAdapter — the uniform contract every source implements.
# --------------------------------------------------------------------------- #
class SourceAdapter:
    """One ingestion source. Subclasses set ``provider`` / ``source_type`` and implement ``fetch`` +
    ``normalize`` (single-batch sources use the default ``poll_once``, which chains them through the
    quota into ``ingest_entries`` and records one health row). Every adapter exposes the same surface:
    ``provider``, ``source_type``, ``enabled()``, ``interval()``, ``max_articles()``, ``fetch()``,
    ``normalize()``, ``poll_once()`` — so the poller stays provider-agnostic."""

    provider: str = "?"
    source_type: str = "?"

    # -- config surface (env-driven) --
    def enabled(self) -> bool:
        raise NotImplementedError

    def interval(self) -> float:
        raise NotImplementedError

    def max_articles(self) -> Optional[int]:
        return None

    def config_warning(self) -> Optional[str]:
        """A human-readable reason this source looks *intended-on but disabled* (e.g. a flag set without
        its required key), or ``None``. Surfaced at startup so a silent ``enabled() == False`` never
        hides a typo."""
        return None

    @property
    def health_key(self) -> str:
        return f"{self.source_type}://{self.provider.lower()}"

    # -- data surface --
    def fetch(self):
        """Return the raw provider payload. Overridden per source; injectable for offline tests."""
        raise NotImplementedError

    def normalize(self, raw) -> SourceBatch:
        """Map a raw payload into a :class:`SourceBatch` of :class:`rss_ingest.FeedEntry`."""
        raise NotImplementedError

    # -- one poll cycle (default: single-batch sources) --
    #: Whether ``fetch``/``normalize`` touch the store at all. FALSE by default — an adapter must
    #: opt in, because the whole value of the flag is that the poller then runs those steps WITHOUT
    #: the ingest write lock, and a store write out there would race every other adapter's ingest.
    #: See :meth:`MultiSourcePoller.poll_adapter_once`.
    FETCH_IS_STORE_FREE = False

    def collect(self) -> "_Collected":
        """Fetch -> normalize -> apply the per-source quota. **No store access.**

        Split out of :meth:`poll_once` so the poller can run it off the ingest lock (M6.2). It is
        the expensive half and the half that needs nothing the lock protects: measured on production
        2026-08-26, a crawl adapter's ``pollMs`` was ~2.4 s, essentially all of it a network round
        trip to a publisher — held, until this split, under the lock that serialises every other
        adapter's ingest.

        Never raises: a fetch or parse error is carried in the result so one source's outage cannot
        affect another, exactly as before.
        """
        t0 = time.perf_counter()
        try:
            batch = self.normalize(self.fetch())
            entries = batch.entries
            cap = self.max_articles()
            if cap is not None and cap >= 0:
                entries = entries[:cap]                    # quota applies BEFORE ingest_entries
            return _Collected(batch=batch, entries=entries, error=None, started=t0)
        except Exception as e:                              # network / parse / provider error
            return _Collected(batch=None, entries=[], error=e, started=t0)

    def persist(self, collected: "_Collected", store_, scorer, *,
                on_feed: Optional[Callable] = None) -> dict:
        """``ingest_entries`` -> record health. **This is the half that needs the ingest lock.**

        ``latencyMs`` still spans collect+persist, so the health record and the aggregate mean what
        they always meant — the split is about which half holds the lock, not about redefining how
        long a source took.
        """
        error = collected.error
        stats: Optional[dict] = None
        if error is None:
            try:
                stats = rss_ingest.ingest_entries(
                    collected.entries, self.provider, self.health_key, scorer, store_,
                    source_type=self.source_type, source_provider=self.provider)
            except Exception as e:
                error = e
        latency_ms = (time.perf_counter() - collected.started) * 1000.0
        agg = _agg(self.provider, self.source_type, stats, collected.batch, latency_ms, error,
                   key=self.health_key)
        if on_feed is not None:
            try:
                on_feed(self.provider, self.health_key, stats, latency_ms, error)
            except Exception:                              # health recording must never break polling
                pass
        return agg

    def poll_once(self, store_, scorer, *, on_feed: Optional[Callable] = None) -> dict:
        """Fetch -> normalize -> apply the per-source quota -> ``ingest_entries`` -> record health.
        Never raises for a fetch/parse error — it records the failure and returns an aggregate with the
        error, so one source's outage can't affect another.

        Kept as the composition of :meth:`collect` and :meth:`persist` so every existing caller,
        subclass override and test sees exactly the contract it saw before M6.2.
        """
        return self.persist(self.collect(), store_, scorer, on_feed=on_feed)


@dataclass
class _Lease:
    """One source's slot in the due-time table (M6.3).

    ``leased`` is the mutual exclusion that replaces "one thread owns this adapter": exactly one
    worker may hold a source at a time, so a slow fetch can never overlap its own next cycle. Under
    thread-per-adapter that was free — the adapter's thread was the lease. With a shared pool it has
    to be stated.
    """
    adapter: "SourceAdapter"
    due: float                       # monotonic; when this source is next allowed to poll
    leased: bool = False


@dataclass
class _Collected:
    """What :meth:`SourceAdapter.collect` produced, waiting for the lock to be written.

    ``started`` is carried so ``latencyMs`` still spans the whole cycle rather than only the part
    that held the lock — the two are now different numbers and conflating them would misreport
    every source's health.
    """
    batch: Optional[SourceBatch]
    entries: list
    error: Optional[BaseException]
    started: float


def _agg(provider, source_type, stats, batch, latency_ms, error, *, key) -> dict:
    """A per-cycle aggregate shaped like ``rss_ingest.ingest_all``'s, plus source metadata."""
    s = stats or {}
    return {"provider": provider, "sourceType": source_type, "feeds": 1,
            "ok": 0 if error else 1, "failed": 1 if error else 0,
            "entries": s.get("entries", 0), "new": s.get("new", 0),
            "duplicates": s.get("duplicates", 0), "skipped": s.get("skipped", 0),
            # Articles refused by RWE_CATALOG_BLOCKED_OUTLETS. Carried through because it was
            # DROPPED here — `ingest_entries` counted it and this aggregate quietly discarded the
            # key, so on the poller path (every production ingest) the count was unobservable and
            # an operator had no way to tell a working block list from a typo'd one.
            "blocked": s.get("blocked", 0),
            "rawCount": (batch.raw_count if batch else 0), "latencyMs": round(latency_ms, 1),
            "errors": ([{"feed": key, "error": f"{type(error).__name__}: {error}"}] if error else [])}


# --------------------------------------------------------------------------- #
# RSS adapter — wraps the existing RSS ingestion; RSS behaviour is unchanged.
# --------------------------------------------------------------------------- #
class RSSAdapter(SourceAdapter):
    provider = "RSS"
    source_type = "rss"

    def __init__(self, feeds_spec: Optional[str] = None):
        self.feeds_spec = feeds_spec

    def enabled(self) -> bool:
        # Explicit opt-out/in via RWE_RSS_ENABLED; default keeps the existing RWE_FEED_POLL behaviour.
        v = os.environ.get("RWE_RSS_ENABLED")
        return v.strip().lower() in _TRUE if v is not None else _bool_env("RWE_FEED_POLL")

    def interval(self) -> float:
        return _float_env("RWE_POLL_INTERVAL", 600.0)      # reuse the existing RSS interval

    def max_articles(self) -> Optional[int]:
        return _int_or_none("RWE_RSS_MAX_ARTICLES")

    def fetch(self):
        return rss_ingest.load_feeds(self.feeds_spec)      # raw = the configured (name, url) feeds

    def normalize(self, feeds) -> SourceBatch:
        """Flat batch across all feeds (for diagnostics/replay). The real per-feed ingestion + per-feed
        health happens in ``poll_once`` via ``ingest_all``; this just reuses fetch_feed/parse_feed."""
        entries: list = []
        for _name, url in feeds or []:
            try:
                _title, es = rss_ingest.parse_feed(rss_ingest.fetch_feed(url))
                entries.extend(es)
            except Exception:
                continue
        return SourceBatch(self.provider, self.source_type, _now_iso(), entries, raw_count=len(entries))

    def poll_once(self, store_, scorer, *, on_feed: Optional[Callable] = None) -> dict:
        """RSS reuses ``rss_ingest.ingest_all`` verbatim (identical behaviour + per-feed health). When
        ``RWE_RSS_MAX_ARTICLES`` is set, it uses a per-feed capped path that still reuses
        fetch_feed/parse_feed/ingest_entries (only the entry list is truncated first).

        With ``RWE_FEED_SCHEDULER`` on, a third path asks each feed on its OWN cadence and with
        conditional-GET validators — see ``_ingest_scheduled``. Off, neither branch below is
        reachable and this method is exactly what it was."""
        feeds = rss_ingest.load_feeds(self.feeds_spec)
        cap = self.max_articles()
        if feed_schedule.enabled():
            agg = self._ingest_scheduled(feeds, scorer, store_, cap, on_feed)
        elif cap is None:
            agg = rss_ingest.ingest_all(feeds, scorer, store_, on_feed=on_feed)   # unchanged RSS path
        else:
            agg = self._ingest_capped(feeds, scorer, store_, cap, on_feed)
        agg["provider"] = self.provider
        agg["sourceType"] = self.source_type
        return agg

    def _ingest_scheduled(self, feeds, scorer, store_, cap, on_feed) -> dict:
        """Per-feed cadence + conditional GET (``feed_schedule``).

        Three outcomes per feed, and only one of them costs a full document:

        * **not due** — skipped entirely. No request, no health record: a feed we chose not to ask
          has no new evidence about its health, and writing one would reset the very timers the
          skip is based on.
        * **304 / unchanged** — the origin says nothing changed. Counted, health recorded as a
          successful poll with zero entries (it IS a successful poll — treating the cheapest
          possible answer as a failure is the classic conditional-GET bug), interval widened.
        * **200 with a body** — parsed and ingested exactly as the unscheduled paths do, through
          the same ``ingest_entries`` choke point. Nothing downstream can tell which path ran.

        ``changed`` is decided by the ingest result first and the body hash second: a 200 whose
        bytes differ but which yields no new articles is a regenerated document, not new
        journalism, and the slow branch is the right one for it."""
        agg = {"feeds": 0, "ok": 0, "failed": 0, "entries": 0, "new": 0, "duplicates": 0,
               "skipped": 0, "blocked": 0, "unknown_outlet": 0,
               "notDue": 0, "notModified": 0, "errors": []}
        for name, url in feeds:
            raw = store_.feed_schedule_state(url)
            state = feed_schedule.FeedState(**raw)
            if not feed_schedule.due(state):
                agg["notDue"] += 1
                continue
            agg["feeds"] += 1
            t0 = time.perf_counter()
            result, error, fetched = None, None, None
            try:
                fetched = rss_ingest.fetch_feed_conditional(
                    url, etag=state.etag, last_modified=state.last_modified)
                if not fetched.not_modified:
                    title, entries = rss_ingest.parse_feed(fetched.data)
                    if cap is not None:
                        entries = entries[: max(0, cap)]
                    result = rss_ingest.ingest_entries(entries, name or title or None, url,
                                                       scorer, store_, source_type="rss")
                    result["feed"] = url
            except Exception as e:
                error = e
            latency_ms = (time.perf_counter() - t0) * 1000.0

            if error is not None:
                agg["failed"] += 1
                agg["errors"].append({"feed": url, "error": f"{type(error).__name__}: {error}"})
                state = feed_schedule.advance(state, changed=False, failed=True)
            else:
                agg["ok"] += 1
                if fetched.not_modified:
                    agg["notModified"] += 1
                    changed = False
                    sha = state.content_sha
                else:
                    for k in ("entries", "new", "duplicates", "skipped", "blocked",
                              "unknown_outlet"):
                        agg[k] += (result or {}).get(k, 0)
                    sha = feed_schedule.content_hash(fetched.data)
                    changed = bool((result or {}).get("new", 0)) or sha != state.content_sha
                state = feed_schedule.advance(
                    state, changed=changed, etag=fetched.etag,
                    last_modified=fetched.last_modified, content_sha=sha)

            store_.record_feed_schedule(
                url, etag=state.etag, last_modified=state.last_modified,
                content_sha=state.content_sha, next_due_at=state.next_due_at,
                interval_s=state.interval_s)
            if on_feed is not None:
                try:
                    on_feed(name, url, result, latency_ms, error)
                except Exception:
                    pass
        return agg

    def _ingest_capped(self, feeds, scorer, store_, cap, on_feed) -> dict:
        agg = {"feeds": 0, "ok": 0, "failed": 0, "entries": 0, "new": 0, "duplicates": 0,
               "skipped": 0, "errors": []}
        for name, url in feeds:
            agg["feeds"] += 1
            t0 = time.perf_counter()
            result, error = None, None
            try:
                title, entries = rss_ingest.parse_feed(rss_ingest.fetch_feed(url))
                result = rss_ingest.ingest_entries(entries[: max(0, cap)], name or title or None, url,
                                                   scorer, store_, source_type="rss")
                result["feed"] = url
            except Exception as e:
                error = e
            latency_ms = (time.perf_counter() - t0) * 1000.0
            if error is None:
                agg["ok"] += 1
                for k in ("entries", "new", "duplicates", "skipped", "blocked"):
                    agg[k] += result.get(k, 0)
            else:
                agg["failed"] += 1
                agg["errors"].append({"feed": url, "error": f"{type(error).__name__}: {error}"})
            if on_feed is not None:
                try:
                    on_feed(name, url, result, latency_ms, error)
                except Exception:
                    pass
        return agg


# --------------------------------------------------------------------------- #
# KeyedJSONAdapter — the hardened chassis every keyed JSON news API rides.
# --------------------------------------------------------------------------- #
#: URL shapes that identify NON-ARTICLE pages a news API sometimes ships as "articles". Each
#: pattern is receipted from production (2026-08-16, Currents delivering Buffalo News): the
#: provider sent 11 BLOX-CMS photo asset pages (``/image_<uuid>.html`` — captions as titles,
#: batch-stamped dates, one an archive shot from 2012 "published" today) and a car-dealer
#: inventory page (``autos.buffalonews.com/inventory?model=cx-50``) in one window. The CMS names
#: the content type in the URL, so this is identification, not a title heuristic — the
#: caption-vs-headline judgement the slug gate deliberately refuses stays refused. BLOX/townnews
#: runs hundreds of US local papers, so the gate lives on the shared chassis, not in the
#: Currents adapter.
# BLOX CMS image asset page: /image_{uuid}.html (exact UUID shape, any host).
_BLOX_IMAGE_PAGE = re.compile(
    r"/image_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.html$", re.IGNORECASE)
# BLOX classifieds: a dealer-inventory browse page on an autos. subdomain.
_AUTOS_INVENTORY = re.compile(r"^https?://autos\.[^/]+/inventory(?:[/?#]|$)", re.IGNORECASE)


def non_article_url(url) -> bool:
    """Whether a provider item's URL identifies a non-article page. Judged on the URL the
    publisher's own CMS wrote — never on title or description."""
    s = str(url or "")
    if not s:
        return False
    path = s.split("?", 1)[0].split("#", 1)[0]
    return bool(_BLOX_IMAGE_PAGE.search(path) or _AUTOS_INVENTORY.match(s))


class KeyedJSONAdapter(SourceAdapter):
    """The shared chassis for keyed JSON news APIs (NewsAPI, Guardian, NewsData, GNews,
    MediaStack, Currents, …): env-prefixed configuration, enable-gating on flag+key with a
    startup config warning, comma-separated combo lists polled by ROTATION (one request per
    cycle — N combinations never multiply the request rate), a per-UTC-day request budget that
    short-circuits cycles BEFORE any request (no fetch, no health-row touch — a cycle that did
    nothing must not claim a lastSuccess), and HTTP-429 accounting (``rateLimited`` on every
    aggregate). A concrete adapter supplies only its identity, its URL builder, and its
    payload→``FeedEntry`` mapping — everything else is this chassis, once.

    Env surface (``RWE_<PREFIX>_*``): ENABLED, API_KEY, POLL_INTERVAL, PAGE_SIZE (1..page_cap,
    decoupled from the MAX_ARTICLES ingest quota), MAX_ARTICLES, DAILY_BUDGET (0 = unlimited),
    TIMEOUT, plus the adapter's combo axes (CATEGORY/COUNTRY/LANGUAGE by default)."""

    env_prefix = "?"
    default_interval = 900.0
    page_cap = 100                                          # the API's absolute per-request max
    signup_hint = ""                                        # appended to the missing-key warning
    endpoint_name = "?"                                     # the endpoint the health key names
    # (env suffix, request param) per rotation axis, outermost first — the cross-product order.
    combo_axes: tuple = (("COUNTRY", "country"), ("CATEGORY", "category"), ("LANGUAGE", "language"))

    @property
    def health_key(self) -> str:
        # Stable per-endpoint identity (e.g. ``guardian://search``) — rotation combos share ONE
        # health row per source, the same discipline as ``newsapi://top-headlines``.
        return f"{self.source_type}://{self.endpoint_name}"

    def __init__(self, fetch: Optional[Callable[[str], dict]] = None):
        self._fetch_fn = fetch                              # injectable (offline tests)
        self._combo_i = 0                                   # rotation cursor (per process)
        self._last_combo: Optional[dict] = None             # the combo the last fetch used
        self._rl_events = 0                                 # 429s seen in the CURRENT cycle
        self._req_day: Optional[str] = None                 # UTC day the request counter is for
        self._req_count = 0

    # -- env plumbing ------------------------------------------------------ #
    def _env(self, suffix: str, default: str = "") -> str:
        return os.environ.get(f"RWE_{self.env_prefix}_{suffix}", default)

    def api_key(self) -> str:
        return self._env("API_KEY").strip()

    def enabled(self) -> bool:
        return _bool_env(f"RWE_{self.env_prefix}_ENABLED") and bool(self.api_key())

    def config_warning(self) -> Optional[str]:
        if _bool_env(f"RWE_{self.env_prefix}_ENABLED") and not self.api_key():
            return (f"RWE_{self.env_prefix}_ENABLED is set but RWE_{self.env_prefix}_API_KEY is "
                    f"missing/empty — {self.provider} stays disabled. {self.signup_hint}".rstrip())
        return None

    def interval(self) -> float:
        return _float_env(f"RWE_{self.env_prefix}_POLL_INTERVAL", self.default_interval)

    def max_articles(self) -> Optional[int]:
        return _int_or_none(f"RWE_{self.env_prefix}_MAX_ARTICLES")

    def page_size(self) -> int:
        """Articles per request (capped at the API's max). Explicit PAGE_SIZE wins; otherwise
        don't fetch more than the ingest quota would keep."""
        explicit = _int_or_none(f"RWE_{self.env_prefix}_PAGE_SIZE")
        size = explicit if explicit is not None else min(self.max_articles() or self.page_cap,
                                                         self.page_cap)
        return max(1, min(size, self.page_cap))

    def daily_budget(self) -> int:
        return _int_env(f"RWE_{self.env_prefix}_DAILY_BUDGET", 0)   # 0 = unlimited

    # -- rotation + budget + 429 accounting -------------------------------- #
    def _split(self, suffix: str) -> list:
        return [v.strip() for v in self._env(suffix).split(",") if v.strip()]

    def _combos(self) -> list:
        """The cross-product of the configured combo-axis lists (each ``[None]`` when unset), as
        request-param dicts in axis order — single values yield exactly one combo."""
        axes = [(param, self._split(suffix) or [None]) for suffix, param in self.combo_axes]
        combos: list = [{}]
        for param, values in axes:
            combos = [{**c, **({param: v} if v else {})} for c in combos for v in values]
        return combos

    def _budget_left(self) -> Optional[int]:
        budget = self.daily_budget()
        if budget <= 0:
            return None
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if self._req_day != today:                          # UTC day rolled — fresh allowance
            self._req_day, self._req_count = today, 0
        return budget - self._req_count

    def _note_transient(self, code: int) -> None:
        if code == 429:
            self._rl_events += 1

    # -- the concrete adapter's surface ------------------------------------ #
    def _url(self, combo: dict) -> str:
        raise NotImplementedError

    def _headers(self) -> dict:
        return {"User-Agent": _USER_AGENT}

    def _articles(self, raw: dict) -> list:
        """The payload's article list (provider-specific envelope)."""
        raise NotImplementedError

    def _entry(self, a: dict, combo: dict) -> "Optional[rss_ingest.FeedEntry]":
        """One provider article -> FeedEntry (or None to drop). Provider-specific mapping ONLY —
        publisher/lean/country resolution happens downstream in the shared pipeline."""
        raise NotImplementedError

    # -- chassis fetch / cycle --------------------------------------------- #
    def fetch(self) -> dict:
        combos = self._combos()
        combo = combos[self._combo_i % len(combos)]
        self._combo_i += 1
        self._last_combo = combo
        url = self._url(combo)
        self._budget_left()                                 # roll the day before counting
        self._req_count += 1
        if self._fetch_fn is not None:
            return self._fetch_fn(url)
        return _get_json(url, headers=self._headers(),
                         timeout=_float_env(f"RWE_{self.env_prefix}_TIMEOUT", 15.0),
                         on_transient=self._note_transient)

    def poll_once(self, store_, scorer, *, on_feed: Optional[Callable] = None) -> dict:
        self._rl_events = 0
        left = self._budget_left()
        if left is not None and left <= 0:
            _default_log(logging.INFO, f"{self.source_type}_budget_exhausted",
                         budget=self.daily_budget(), day=self._req_day)
            agg = _agg(self.provider, self.source_type, None, None, 0.0, None, key=self.health_key)
            agg["budgetExhausted"] = True
            agg["rateLimited"] = 0
            return agg
        agg = super().poll_once(store_, scorer, on_feed=on_feed)
        agg["rateLimited"] = self._rl_events
        return agg

    def normalize(self, raw: dict) -> SourceBatch:
        # Stamp entries from the COMBO the fetch actually used (rotation makes env values
        # ambiguous); a direct normalize() without a prior fetch falls back to the first combo.
        combo = self._last_combo if self._last_combo is not None else self._combos()[0]
        arts = self._articles(raw or {})
        entries = [e for e in (self._entry(a, combo) for a in arts) if e is not None]
        # Non-article pages (photo assets, classifieds — see non_article_url) never enter the
        # catalog: once stored they are articles to every downstream surface, and no title
        # hygiene can undo that. Chassis-wide because the URL shapes are CMS properties, not
        # provider properties. Logged so a provider that turns into a photo firehose is visible.
        kept = [e for e in entries if not non_article_url(e.url)]
        if len(kept) != len(entries):
            _default_log(logging.INFO, f"{self.source_type}_non_article_dropped",
                         n=len(entries) - len(kept))
        return SourceBatch(self.provider, self.source_type, _now_iso(), kept, raw_count=len(arts))


# --------------------------------------------------------------------------- #
# NewsAPI adapter — https://newsapi.org/docs (the chassis' first rider; behaviour unchanged).
# --------------------------------------------------------------------------- #
class NewsAPIAdapter(KeyedJSONAdapter):
    """NewsAPI on the shared chassis. Free tier ≈ 100 requests/day (24 h article delay); the
    default 900 s interval spends 96 — set RWE_NEWSAPI_DAILY_BUDGET accordingly (compose: 90)."""

    provider = "NewsAPI"
    source_type = "newsapi"
    env_prefix = "NEWSAPI"
    page_cap = 100
    signup_hint = "Get a free key at https://newsapi.org and set RWE_NEWSAPI_API_KEY."

    @property
    def health_key(self) -> str:
        return f"newsapi://{os.environ.get('RWE_NEWSAPI_ENDPOINT', 'top-headlines')}"

    def _headers(self) -> dict:
        return {"X-Api-Key": self.api_key(), "User-Agent": _USER_AGENT}

    def _url(self, combo: dict) -> str:
        endpoint = os.environ.get("RWE_NEWSAPI_ENDPOINT", "top-headlines")
        params = {"pageSize": str(self.page_size())}
        q = os.environ.get("RWE_NEWSAPI_QUERY")
        if q:
            params["q"] = q
        params.update(combo)
        # top-headlines needs at least one of country/category/q/sources to be a valid request.
        if endpoint == "top-headlines" and not ({"q", "category", "country"} & set(params)):
            params["country"] = "us"
        return f"https://newsapi.org/v2/{endpoint}?{urllib.parse.urlencode(params)}"

    def _articles(self, raw: dict) -> list:
        return raw.get("articles") or []

    def _entry(self, a: dict, combo: dict):
        url = (a.get("url") or "").strip()
        if not url:
            return None
        img = media.pick_best_image([{"url": a.get("urlToImage"), "source": "newsapi"}]) or {}
        return rss_ingest.FeedEntry(
            url=url, title=a.get("title") or "", description=a.get("description") or "",
            body=a.get("content") or None, published_at=rss_ingest._to_iso(a.get("publishedAt") or ""),
            image=img.get("url"), image_width=img.get("width"), image_height=img.get("height"),
            image_mime=img.get("mime"), image_source=(img.get("source") if img else None),
            source_type="newsapi", source_provider="NewsAPI", category=combo.get("category"),
            language=combo.get("language"), country=combo.get("country"), external_id=url,
            publisher_hint=((a.get("source") or {}).get("name") or None))


# --------------------------------------------------------------------------- #
# Guardian Open Platform — https://open-platform.theguardian.com/documentation/
# --------------------------------------------------------------------------- #
class GuardianAdapter(KeyedJSONAdapter):
    """Single-outlet source: every article is The Guardian's own (publisher_hint fixed to its
    domain, so the registry resolves the canonical outlet + its verified lean/locality).
    Developer tier ≈ 500 calls/day. Rotation axis: SECTION (e.g. world,politics)."""

    provider = "Guardian"
    source_type = "guardian"
    env_prefix = "GUARDIAN"
    endpoint_name = "search"
    page_cap = 50
    signup_hint = "Get a free developer key at https://open-platform.theguardian.com/access/."
    combo_axes = (("SECTION", "section"),)

    def _url(self, combo: dict) -> str:
        params = {"api-key": self.api_key(), "order-by": "newest",
                  "show-fields": "trailText,thumbnail", "page-size": str(self.page_size())}
        q = self._env("QUERY")
        if q:
            params["q"] = q
        params.update(combo)
        return f"https://content.guardianapis.com/search?{urllib.parse.urlencode(params)}"

    def _articles(self, raw: dict) -> list:
        return (raw.get("response") or {}).get("results") or []

    def _entry(self, a: dict, combo: dict):
        url = (a.get("webUrl") or "").strip()
        if not url:
            return None
        fields = a.get("fields") or {}
        img = media.pick_best_image([{"url": fields.get("thumbnail"), "source": "guardian"}]) or {}
        return rss_ingest.FeedEntry(
            url=url, title=a.get("webTitle") or "", description=fields.get("trailText") or "",
            published_at=rss_ingest._to_iso(a.get("webPublicationDate") or ""),
            image=img.get("url"), image_width=img.get("width"), image_height=img.get("height"),
            image_mime=img.get("mime"), image_source=(img.get("source") if img else None),
            source_type="guardian", source_provider="Guardian",
            category=a.get("sectionName") or combo.get("section"), language="en",
            external_id=a.get("id") or url, publisher_hint="theguardian.com")


# --------------------------------------------------------------------------- #
# NewsData.io — https://newsdata.io/documentation
# --------------------------------------------------------------------------- #
class NewsDataAdapter(KeyedJSONAdapter):
    """NewsData.io ``latest``. Free tier ≈ 200 credits/day, ``size`` capped at 10 there (the
    compose default matches; paid tiers may raise PAGE_SIZE up to 50). Country/language values
    may arrive as English names — the shared Location Resolver normalizes both forms."""

    provider = "NewsData"
    source_type = "newsdata"
    env_prefix = "NEWSDATA"
    endpoint_name = "latest"
    page_cap = 50
    signup_hint = "Get a free key at https://newsdata.io and set RWE_NEWSDATA_API_KEY."

    def _url(self, combo: dict) -> str:
        params = {"apikey": self.api_key(), "size": str(self.page_size())}
        q = self._env("QUERY")
        if q:
            params["q"] = q
        params.update(combo)
        return f"https://newsdata.io/api/1/latest?{urllib.parse.urlencode(params)}"

    def _articles(self, raw: dict) -> list:
        return raw.get("results") or []

    @staticmethod
    def _iso(pub: str) -> Optional[str]:
        pub = (pub or "").strip()
        if not pub:
            return None
        # "2026-07-26 10:00:00" (their format) -> RFC3339-ish for the shared parser.
        return rss_ingest._to_iso(pub.replace(" ", "T", 1) if pub[:4].isdigit() else pub)

    def _entry(self, a: dict, combo: dict):
        url = (a.get("link") or "").strip()
        if not url:
            return None
        img = media.pick_best_image([{"url": a.get("image_url"), "source": "newsdata"}]) or {}
        cats = a.get("category") or []
        countries = a.get("country") or []
        return rss_ingest.FeedEntry(
            url=url, title=a.get("title") or "", description=a.get("description") or "",
            published_at=self._iso(a.get("pubDate") or ""),
            image=img.get("url"), image_width=img.get("width"), image_height=img.get("height"),
            image_mime=img.get("mime"), image_source=(img.get("source") if img else None),
            source_type="newsdata", source_provider="NewsData",
            category=(cats[0] if cats else combo.get("category")),
            language=a.get("language") or combo.get("language"),
            country=(countries[0] if countries else combo.get("country")),
            external_id=a.get("article_id") or url,
            publisher_hint=a.get("source_name") or a.get("source_id") or None)


# --------------------------------------------------------------------------- #
# GNews — https://gnews.io/docs/v4
# --------------------------------------------------------------------------- #
class GNewsAdapter(KeyedJSONAdapter):
    """GNews ``top-headlines``. Free tier ≈ 100 requests/day with ``max`` capped at 10 (compose
    default matches). The source URL (domain) is preferred as the publisher hint — the registry
    resolves domains exactly."""

    provider = "GNews"
    source_type = "gnews"
    env_prefix = "GNEWS"
    endpoint_name = "top-headlines"
    page_cap = 100
    signup_hint = "Get a free key at https://gnews.io and set RWE_GNEWS_API_KEY."
    combo_axes = (("COUNTRY", "country"), ("CATEGORY", "category"), ("LANGUAGE", "lang"))

    def _url(self, combo: dict) -> str:
        params = {"apikey": self.api_key(), "max": str(self.page_size())}
        q = self._env("QUERY")
        if q:
            params["q"] = q
        params.update(combo)
        return f"https://gnews.io/api/v4/top-headlines?{urllib.parse.urlencode(params)}"

    def _articles(self, raw: dict) -> list:
        return raw.get("articles") or []

    def _entry(self, a: dict, combo: dict):
        url = (a.get("url") or "").strip()
        if not url:
            return None
        src = a.get("source") or {}
        img = media.pick_best_image([{"url": a.get("image"), "source": "gnews"}]) or {}
        return rss_ingest.FeedEntry(
            url=url, title=a.get("title") or "", description=a.get("description") or "",
            body=a.get("content") or None,
            published_at=rss_ingest._to_iso(a.get("publishedAt") or ""),
            image=img.get("url"), image_width=img.get("width"), image_height=img.get("height"),
            image_mime=img.get("mime"), image_source=(img.get("source") if img else None),
            source_type="gnews", source_provider="GNews",
            category=combo.get("category"), language=combo.get("lang"),
            country=combo.get("country"), external_id=url,
            publisher_hint=_host_hint(src.get("url")) or src.get("name") or None)


# --------------------------------------------------------------------------- #
# MediaStack — https://mediastack.com/documentation
# --------------------------------------------------------------------------- #
class MediaStackAdapter(KeyedJSONAdapter):
    """MediaStack ``news``. NOTE the VERY tight free tier — 100 requests/MONTH (verified on the
    operator dashboard 2026-07-26; older docs said 500): the defaults pick an 8-hour interval +
    budget 3/day (~93/month). HTTPS is paid-only there: RWE_MEDIASTACK_HTTPS=0 switches to http
    for the free tier (documented trade-off)."""

    provider = "MediaStack"
    source_type = "mediastack"
    env_prefix = "MEDIASTACK"
    endpoint_name = "news"
    default_interval = 28800.0
    page_cap = 100
    signup_hint = "Get a key at https://mediastack.com and set RWE_MEDIASTACK_API_KEY."
    combo_axes = (("COUNTRY", "countries"), ("CATEGORY", "categories"), ("LANGUAGE", "languages"))

    def _url(self, combo: dict) -> str:
        scheme = "https" if _bool_env("RWE_MEDIASTACK_HTTPS", True) else "http"
        params = {"access_key": self.api_key(), "sort": "published_desc",
                  "limit": str(self.page_size())}
        q = self._env("QUERY")
        if q:
            params["keywords"] = q
        params.update(combo)
        return f"{scheme}://api.mediastack.com/v1/news?{urllib.parse.urlencode(params)}"

    def _articles(self, raw: dict) -> list:
        return raw.get("data") or []

    def _entry(self, a: dict, combo: dict):
        url = (a.get("url") or "").strip()
        if not url:
            return None
        img = media.pick_best_image([{"url": a.get("image"), "source": "mediastack"}]) or {}
        return rss_ingest.FeedEntry(
            url=url, title=a.get("title") or "", description=a.get("description") or "",
            published_at=rss_ingest._to_iso(a.get("published_at") or ""),
            image=img.get("url"), image_width=img.get("width"), image_height=img.get("height"),
            image_mime=img.get("mime"), image_source=(img.get("source") if img else None),
            source_type="mediastack", source_provider="MediaStack",
            category=a.get("category") or combo.get("categories"),
            language=a.get("language") or combo.get("languages"),
            country=a.get("country") or combo.get("countries"),
            external_id=url, publisher_hint=a.get("source") or None)


# --------------------------------------------------------------------------- #
# Currents — https://currentsapi.services/en/docs/
# --------------------------------------------------------------------------- #
class CurrentsAdapter(KeyedJSONAdapter):
    """Currents ``latest-news``. Free tier ≈ 600 requests/day with ``page_size`` capped at 20
    (verified in production 2026-07-26: 21+ is a hard 400 — the compose default matches). The
    payload carries no outlet field, so the publisher hint is the article URL's own domain (the
    registry resolves domains); ``image`` is sometimes the literal string "None" — dropped,
    never stored."""

    provider = "Currents"
    source_type = "currents"
    env_prefix = "CURRENTS"
    endpoint_name = "latest-news"
    page_cap = 100
    signup_hint = "Get a free key at https://currentsapi.services and set RWE_CURRENTS_API_KEY."

    def _url(self, combo: dict) -> str:
        params = {"apiKey": self.api_key(), "page_size": str(self.page_size())}
        q = self._env("QUERY")
        if q:
            params["keywords"] = q
        params.update(combo)
        return f"https://api.currentsapi.services/v1/latest-news?{urllib.parse.urlencode(params)}"

    def _articles(self, raw: dict) -> list:
        return raw.get("news") or []

    @staticmethod
    def _iso(pub: str) -> Optional[str]:
        pub = (pub or "").strip()
        if not pub:
            return None
        out = rss_ingest._to_iso(pub)
        if out:
            return out
        try:                                                # "2026-07-26 10:00:00 +0000"
            from datetime import datetime
            return datetime.strptime(pub, "%Y-%m-%d %H:%M:%S %z").isoformat()
        except ValueError:
            return None

    def _entry(self, a: dict, combo: dict):
        url = (a.get("url") or "").strip()
        if not url:
            return None
        host = _host_hint(url)
        image = a.get("image")
        image = None if (not image or str(image).strip().lower() == "none") else image
        img = media.pick_best_image([{"url": image, "source": "currents"}]) or {}
        cats = a.get("category") or []
        return rss_ingest.FeedEntry(
            url=url, title=a.get("title") or "", description=a.get("description") or "",
            published_at=self._iso(a.get("published") or ""),
            image=img.get("url"), image_width=img.get("width"), image_height=img.get("height"),
            image_mime=img.get("mime"), image_source=(img.get("source") if img else None),
            source_type="currents", source_provider="Currents",
            category=(cats[0] if cats else combo.get("category")),
            language=a.get("language") or combo.get("language"),
            country=combo.get("country"), external_id=a.get("id") or url,
            publisher_hint=host or None)


# --------------------------------------------------------------------------- #
# Google News RSS — https://news.google.com/rss (keyless; publisher comes from the <source> tag)
# --------------------------------------------------------------------------- #
class GoogleNewsAdapter(SourceAdapter):
    """Google News RSS feeds (keyless). Feeds are built from TOPICS (WORLD, NATION, BUSINESS,
    TECHNOLOGY, ENTERTAINMENT, SPORTS, SCIENCE, HEALTH) and/or free-text QUERIES — both
    comma-separated, ROTATED one feed per cycle. Each item's ``<source url=…>`` tag names the
    REAL outlet, which becomes the publisher hint (the registry resolves it: rated outlets
    arrive rated, unknown ones stay honestly Unknown).

    Honest limitation, by design: item links are Google redirect URLs (the encoded form is
    undocumented — decoding it would be guesswork), so the Read flow opens via Google's
    redirect, and canonical-URL dedup cannot merge a Google-delivered article with the SAME
    article from the publisher's own feed — story clustering still groups them by title."""

    provider = "GoogleNews"
    source_type = "googlenews"

    _TOPICS = ("WORLD", "NATION", "BUSINESS", "TECHNOLOGY", "ENTERTAINMENT",
               "SPORTS", "SCIENCE", "HEALTH")

    def __init__(self, fetch_bytes: Optional[Callable[[str], bytes]] = None):
        self._fetch_bytes = fetch_bytes                     # injectable (offline tests)
        self._feed_i = 0
        self._last_feed: Optional[tuple] = None             # (kind, value, url)

    def enabled(self) -> bool:
        return _bool_env("RWE_GOOGLENEWS_ENABLED")

    def interval(self) -> float:
        return _float_env("RWE_GOOGLENEWS_POLL_INTERVAL", 900.0)

    def max_articles(self) -> Optional[int]:
        return _int_or_none("RWE_GOOGLENEWS_MAX_ARTICLES")

    @property
    def health_key(self) -> str:
        return "googlenews://rss"

    def _locale(self) -> dict:
        hl = os.environ.get("RWE_GOOGLENEWS_LANGUAGE", "en-US").strip() or "en-US"
        gl = os.environ.get("RWE_GOOGLENEWS_COUNTRY", "US").strip() or "US"
        return {"hl": hl, "gl": gl, "ceid": f"{gl}:{hl.split('-')[0]}"}

    def _feeds(self) -> list:
        """(kind, value, url) per configured feed: topic sections, searches, or the front page."""
        loc = urllib.parse.urlencode(self._locale())
        topics = [t.strip().upper() for t in os.environ.get("RWE_GOOGLENEWS_TOPICS", "").split(",")
                  if t.strip() and t.strip().upper() in self._TOPICS]
        queries = [q.strip() for q in os.environ.get("RWE_GOOGLENEWS_QUERIES", "").split(",")
                   if q.strip()]
        feeds = [("topic", t, f"https://news.google.com/rss/headlines/section/topic/{t}?{loc}")
                 for t in topics]
        feeds += [("search", q,
                   f"https://news.google.com/rss/search?{urllib.parse.urlencode({'q': q})}&{loc}")
                  for q in queries]
        return feeds or [("top", "", f"https://news.google.com/rss?{loc}")]

    def fetch(self) -> bytes:
        feeds = self._feeds()
        kind, value, url = feeds[self._feed_i % len(feeds)]
        self._feed_i += 1
        self._last_feed = (kind, value, url)
        if self._fetch_bytes is not None:
            return self._fetch_bytes(url)
        return _get_bytes(url, timeout=_float_env("RWE_GOOGLENEWS_TIMEOUT", 20.0))

    def normalize(self, raw: bytes) -> SourceBatch:
        import xml.etree.ElementTree as ET
        kind, value, _url = self._last_feed or ("top", "", "")
        loc = self._locale()
        category = value.capitalize() if kind == "topic" else None
        entries: list = []
        raw_count = 0
        try:
            root = ET.fromstring(raw or b"")
            items = root.findall(".//item")
        except ET.ParseError:
            items = []
        for item in items:
            raw_count += 1
            link = (item.findtext("link") or "").strip()
            title = (item.findtext("title") or "").strip()
            if not link:
                continue
            src = item.find("source")
            src_name = (src.text or "").strip() if src is not None else ""
            src_url = (src.get("url") or "").strip() if src is not None else ""
            # Google appends " - Publisher" to titles; strip it when it matches the source tag.
            if src_name and title.endswith(f" - {src_name}"):
                title = title[: -(len(src_name) + 3)].rstrip()
            entries.append(rss_ingest.FeedEntry(
                url=link, title=title,
                # A Google News <description> is a related-coverage DIGEST — an <ol> of
                # "headline + outlet" rows, never this article's own dek. Flattened by
                # clean_html it served as 26.2% of story summaries (2026-08-16 baseline).
                # Stored blank: the field is display-only (clustering's description tokens are
                # 0, measured-and-not-adopted), and pick_story_summary rejects the stored
                # backlog by provider/structure the same way.
                description="",
                published_at=rss_ingest._to_iso(item.findtext("pubDate") or ""),
                source_type="googlenews", source_provider="GoogleNews",
                category=category, language=loc["hl"].split("-")[0], country=loc["gl"],
                external_id=link, publisher_hint=_host_hint(src_url) or src_name or None))
        return SourceBatch(self.provider, self.source_type, _now_iso(), entries, raw_count=raw_count)


# --------------------------------------------------------------------------- #
# GDELT adapter — https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
# --------------------------------------------------------------------------- #
def _gdelt_date(s) -> Optional[str]:
    """GDELT ``seendate`` is ``YYYYMMDDTHHMMSSZ``; normalize to ISO (or None)."""
    s = (s or "").strip()
    try:
        return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


class GDELTAdapter(SourceAdapter):
    provider = "GDELT"
    source_type = "gdelt"

    def __init__(self, fetch: Optional[Callable[[str], dict]] = None):
        self._fetch_fn = fetch                              # injectable (offline tests)

    def enabled(self) -> bool:
        return _bool_env("RWE_GDELT_ENABLED")               # keyless API

    def interval(self) -> float:
        return _float_env("RWE_GDELT_POLL_INTERVAL", 900.0)

    def max_articles(self) -> Optional[int]:
        return _int_or_none("RWE_GDELT_MAX_ARTICLES")

    @property
    def health_key(self) -> str:
        return "gdelt://doc"

    def _url(self) -> str:
        params = {"query": os.environ.get("RWE_GDELT_QUERY") or DEFAULT_GDELT_QUERY,
                  "mode": "artlist", "format": "json", "sort": "datedesc",
                  "maxrecords": str(min(self.max_articles() or 75, 250))}
        return f"https://api.gdeltproject.org/api/v2/doc/doc?{urllib.parse.urlencode(params)}"

    #: Read timeout, in seconds. NOT 15 — that was the default this adapter shipped with, and it sat
    #: exactly inside the endpoint's response distribution.
    #:
    #: Measured against the live API (2026-07-27, ten samples):
    #:     429 refusals : 9.4, 10.4, 10.8, 11.8, 11.9, 12.1, 12.7 s
    #:     200 successes: 13.5, 14.7, 15.3 s
    #:
    #: GDELT's DOC endpoint answers slowly whatever the outcome, and it **refuses faster than it
    #: succeeds**. A 15 s timeout therefore cut off successes while letting every refusal through —
    #: a filter biased against the outcome we want, which is why the success rate sat at 58% and
    #: every diagnosis kept landing on rate limits. Query complexity is NOT a factor: a single-term
    #: query measured 15.3 s and 429'd at the same rate as the five-term one.
    #:
    #: 45 s clears the observed distribution with headroom. The cost of being generous here is one
    #: background thread waiting longer on a poll that happens every 30 minutes; the cost of being
    #: tight is discarding responses we already paid for.
    DEFAULT_TIMEOUT = 45.0

    def fetch(self) -> dict:
        url = self._url()
        if self._fetch_fn is not None:
            return self._fetch_fn(url)
        return _get_json(url, headers={"User-Agent": _USER_AGENT},
                         timeout=_float_env("RWE_GDELT_TIMEOUT", self.DEFAULT_TIMEOUT))

    def normalize(self, raw: dict) -> SourceBatch:
        arts = (raw or {}).get("articles") or []
        entries = []
        for a in arts:
            url = (a.get("url") or "").strip()
            if not url:
                continue
            img = media.pick_best_image([{"url": a.get("socialimage"), "source": "gdelt"}]) or {}
            entries.append(rss_ingest.FeedEntry(
                url=url, title=a.get("title") or "", description="",
                published_at=_gdelt_date(a.get("seendate")),
                image=img.get("url"), image_width=img.get("width"), image_height=img.get("height"),
                image_mime=img.get("mime"), image_source=(img.get("source") if img else None),
                source_type="gdelt", source_provider="GDELT",
                language=a.get("language") or None, country=a.get("sourcecountry") or None,
                external_id=url, publisher_hint=(a.get("domain") or None)))
        return SourceBatch(self.provider, self.source_type, _now_iso(), entries, raw_count=len(arts))


# --------------------------------------------------------------------------- #
# GDELT GKG enricher — event geography for articles ALREADY in the catalog (Phase 2 supply).
# --------------------------------------------------------------------------- #
class GDELTGKGEnricher(SourceAdapter):
    """An ENRICHMENT source: produces no articles, so ``fetch``/``normalize`` are never used —
    ``poll_once`` is overridden to run one :func:`gdelt_gkg.enrich_from_latest` cycle on the
    poller's standard cadence/health machinery. Keyless, default OFF (``RWE_GDELT_GKG``);
    independent of the DOC artlist adapter — it locates ANY provider's articles that GDELT
    happens to monitor (RSS-ingested outlets included). Event countries land in the
    ``article_event_locations`` side table with ``gdelt-gkg`` provenance via the shared
    resolver; per-source replace means re-running a cycle is harmless."""

    provider = "GDELT-GKG"
    source_type = "gdelt-gkg"

    def __init__(self, fetch_bytes: Optional[Callable[[str], bytes]] = None):
        self._fetch_bytes = fetch_bytes                     # injectable (offline tests)
        self._first_cycle = True                            # auto-backfill: at most once per process

    def enabled(self) -> bool:
        return _bool_env("RWE_GDELT_GKG")

    def interval(self) -> float:
        return _float_env("RWE_GDELT_GKG_INTERVAL", 900.0)  # GKG publishes every 15 minutes

    @property
    def health_key(self) -> str:
        return "gdelt://gkg"

    def _warn_if_window_cost_is_high(self) -> None:
        """One loud line at startup when the steady-state lookback has been left at a backfill-sized
        value. Each window is a separate multi-megabyte download, so this is the single setting that
        can silently multiply our request rate against GDELT — and it is easy to leave behind after a
        one-off deep scan, because nothing else about the system changes when you do. It cost a 60%
        DOC success rate before anyone connected the two."""
        windows = gdelt_gkg.windows_per_cycle()
        if windows <= gdelt_gkg.DEFAULT_WINDOWS * 4:
            return
        per_cycle = windows + 1                              # + the lastupdate.txt manifest
        per_day = per_cycle * (86400.0 / max(1.0, self.interval()))
        self._log(logging.WARNING, "gkg_window_cost_high", windows=windows,
                  requestsPerCycle=per_cycle, requestsPerDay=round(per_day),
                  steadyStateDefault=gdelt_gkg.DEFAULT_WINDOWS,
                  hint="RWE_GDELT_GKG_WINDOWS is a ONE-TIME backfill depth; cold start is handled "
                       "automatically by RWE_GDELT_GKG_BACKFILL_WINDOWS. Leaving it raised polls "
                       "GDELT far harder than intended and can rate-limit the DOC adapter.")

    def poll_once(self, store_, scorer, *, on_feed: Optional[Callable] = None) -> dict:
        t0 = time.perf_counter()
        error = None
        stats: Optional[dict] = None
        if self._first_cycle:
            self._warn_if_window_cost_is_high()
        try:
            stats = gdelt_gkg.enrich_from_latest(
                store_, fetch_bytes=self._fetch_bytes or _get_bytes,
                allow_backfill=self._first_cycle)
        except Exception as e:                              # network / zip / parse error
            error = e
        finally:
            self._first_cycle = False
        latency_ms = (time.perf_counter() - t0) * 1000.0
        agg = _agg(self.provider, self.source_type, None, None, latency_ms, error,
                   key=self.health_key)
        # Enrichment counters (not ingest counters): windows processed/skipped, parsed records
        # with a dominant country, catalog matches, articles located — and whether this was the
        # automatic cold-start deep cycle.
        s = stats or {}
        agg.update({"windows": s.get("windows", 0), "windowErrors": s.get("windowErrors", 0),
                    "records": s.get("records", 0), "matched": s.get("matched", 0),
                    "located": s.get("located", 0), "images": s.get("images", 0)})
        if s.get("backfill"):
            agg["backfill"] = True
        if on_feed is not None:
            try:
                on_feed(self.provider, self.health_key, stats, latency_ms, error)
            except Exception:                               # health recording must never break polling
                pass
        return agg


class PublisherMetadataEnricher(SourceAdapter):
    """An ENRICHMENT source: fills the publisher metadata cache from Wikipedia/Wikidata, so the
    Publisher page reads facts that are already stored instead of blocking a request on a third-party
    API. Produces no articles, so ``fetch``/``normalize`` are never used.

    It is an ADAPTER rather than a post-cycle hook for three reasons, all learned from the passes
    that live in ``_post_cycle``: it needs its OWN cadence (a 30-day TTL does not want to be re-checked
    every ingest cycle), it needs the poller's per-source health and failure backoff (Wikimedia
    outages should throttle it, not the catalog), and ``fetch_json`` has to be injectable so the test
    suite never touches the network — which a lambda built inside ``_post_cycle`` cannot be.

    Idempotent by construction: :func:`publisher_metadata.pending` skips any publisher whose row is
    still fresh, so once the catalog is covered a cycle costs one query and zero requests."""

    provider = "Wikipedia"
    source_type = "publisher-wiki"

    def __init__(self, fetch_json: Optional[Callable[[str], dict]] = None):
        self._fetch_json = fetch_json                       # injectable (offline tests)

    def enabled(self) -> bool:
        return publisher_metadata.enabled()

    def interval(self) -> float:
        # Publisher facts are close to static; this cadence exists to spread a cold start over a few
        # hours, not to track change. Batch × cycles-per-hour is the whole request budget.
        return _float_env("RWE_PUBLISHER_WIKI_INTERVAL", 900.0)

    @property
    def health_key(self) -> str:
        return "wikipedia://publishers"

    def _fetch(self, url: str) -> dict:
        if self._fetch_json is not None:
            return self._fetch_json(url)
        # Wikimedia's User-Agent policy: requests without a descriptive agent are refused (403).
        return _get_json(url, headers={"User-Agent": publisher_wiki.USER_AGENT}, timeout=20.0)

    def poll_once(self, store_, scorer, *, on_feed: Optional[Callable] = None) -> dict:
        t0 = time.perf_counter()
        error = None
        stats: Optional[dict] = None
        try:
            # _default_log, not the poller's: the poller's per-adapter `source_poll` line
            # carries a fixed field set and drops the enrichment counters, so without this the
            # pass is invisible in logs however many publishers it resolved.
            stats = publisher_metadata.run_enrichment(store_, fetch_json=self._fetch,
                                                      log=_default_log)
        except Exception as e:                              # store / network / parse error
            error = e
        latency_ms = (time.perf_counter() - t0) * 1000.0
        agg = _agg(self.provider, self.source_type, None, None, latency_ms, error,
                   key=self.health_key)
        s = stats or {}
        by_status = s.get("byStatus") or {}
        # Enrichment counters, not ingest counters: how many outlets were due, and how the lookups
        # resolved. `ambiguous` is the one worth watching — it is the human-curation backlog.
        # NOT named `errors`: that key already exists in _agg as the CYCLE's error list, and an
        # int there would corrupt the aggregate every other adapter shares. A per-publisher lookup
        # failure is a counter; a failed cycle is a different thing entirely.
        agg.update({"considered": s.get("considered", 0),
                    "matched": by_status.get("ok", 0),
                    "noMatch": by_status.get("no_match", 0),
                    "ambiguous": by_status.get("ambiguous", 0),
                    "lookupErrors": by_status.get("error", 0)})
        if on_feed is not None:
            try:
                on_feed(self.provider, self.health_key, stats, latency_ms, error)
            except Exception:                               # health recording must never break polling
                pass
        return agg


# --------------------------------------------------------------------------- #
# SourceRegistry — the enabled adapters the poller iterates. Future providers register here only.
# --------------------------------------------------------------------------- #
class SourceRegistry:
    def __init__(self):
        self._adapters: list = []

    def register(self, adapter: SourceAdapter) -> SourceAdapter:
        self._adapters.append(adapter)
        return adapter

    def adapters(self) -> list:
        return list(self._adapters)

    def enabled(self) -> list:
        return [a for a in self._adapters if a.enabled()]


def default_registry(feeds_spec: Optional[str] = None) -> SourceRegistry:
    """The standard registry (RSS + NewsAPI + Guardian + NewsData + GNews + MediaStack + Currents +
    Google News RSS + GDELT articles, + the GKG event-geography and publisher-metadata enrichers).
    Future adapters (Reuters,
    AP, Reddit, Hacker News, …) register here without touching the poller."""
    reg = SourceRegistry()
    reg.register(RSSAdapter(feeds_spec=feeds_spec))
    reg.register(NewsAPIAdapter())
    reg.register(GuardianAdapter())
    reg.register(NewsDataAdapter())
    reg.register(GNewsAdapter())
    reg.register(MediaStackAdapter())
    reg.register(CurrentsAdapter())
    reg.register(GoogleNewsAdapter())
    reg.register(GDELTAdapter())
    for adapter in _crawl_adapters():
        reg.register(adapter)
    reg.register(GDELTGKGEnricher())   # enrichment last: it annotates articles the others ingested
    reg.register(PublisherMetadataEnricher())   # …and this annotates the publishers behind them
    return reg


def _crawl_adapters() -> list:
    """One :class:`crawler.CrawlAdapter` per configured publisher, or none.

    **Imported lazily on purpose**: ``crawler`` imports this module, so a top-level import here is a
    cycle. The same reason `store` reaches for `corpus` inside a method.

    Every failure mode returns an empty list rather than raising. A malformed or missing crawl
    config must not take the RSS poller down with it — the crawler is a supplement to ingestion, and
    a supplement that can break the thing it supplements is worse than one that is absent. Each
    adapter is still gated on ``RWE_CRAWL_ENABLED``, which defaults to OFF, so registering them
    changes nothing until an operator says so."""
    if not _bool_env("RWE_CRAWL_ENABLED"):
        return []
    try:
        import crawler
        return [crawler.CrawlAdapter(c) for c in crawler.load_config()]
    except Exception:
        return []


def config_warnings(registry: SourceRegistry) -> list:
    """Config warnings for every registered adapter that looks intended-on but is disabled (e.g. a flag
    set without its required key). Empty when nothing is misconfigured — surfaced at startup so a silent
    ``enabled() == False`` doesn't hide a typo."""
    return [w for a in registry.adapters() if (w := a.config_warning())]


# --------------------------------------------------------------------------- #
# MultiSourcePoller — one daemon thread per enabled adapter, each on its own interval, isolated.
# Reuses the ingestion pipeline, FeedHealth (record_feed_health), retention, and the hot-refresh seam.
# FeedPoller is left untouched.
# --------------------------------------------------------------------------- #
class MultiSourcePoller:
    def __init__(self, store_, scorer=None, *, registry: Optional[SourceRegistry] = None,
                 log: Optional[Callable] = None, on_cycle: Optional[Callable[[dict], None]] = None,
                 dirty_check: Optional[Callable[[], bool]] = None):
        self.store = store_
        self.scorer = scorer or rss_ingest.make_scorer()
        self.registry = registry or default_registry()
        self._log = log or _default_log
        self._on_cycle = on_cycle                           # hot-refresh seam (reused; never modified)
        self._dirty_check = dirty_check                     # Commit 18 D6: request-path catalog growth
        self.unhealthy_after = _int_env("RWE_FEED_UNHEALTHY_AFTER", 3)
        self._stop = threading.Event()
        self._threads: list = []
        # Serialize DB writes + the post-cycle hook across adapters so concurrent polls stay SQLite-safe.
        self._lock = threading.Lock()
        # Coalescing state for the catalog-wide post-cycle steps (see `_post_cycle`). None rather
        # than 0.0 so "never run" is distinguishable from "ran at process start" and the first pass
        # is never throttled.
        self._last_maintenance: Optional[float] = None
        self._maintenance_pending = False
        # M6.3 scheduler state. `_sched` guards the due-time table ONLY and is never held across a
        # poll — see the pool section below for why that ordering is the whole point.
        self._sched = threading.Condition()
        self._leases: "list[_Lease]" = []
        # health_key -> consecutive failures, for adaptive polling (see _effective_interval).
        self._consecutive: dict = {}

    # -- adaptive polling ---------------------------------------------------------------------- #
    def _effective_interval(self, adapter: SourceAdapter) -> float:
        """The adapter's own interval, widened while it is failing.

        A provider that is refusing us is not helped by being asked again on schedule — and when the
        refusal is a rate limit, polling on time is what sustains it. Doubling per consecutive
        failure backs off automatically and returns to the configured cadence the moment a cycle
        succeeds.

        **Backoff waits for SUSTAINED failure** (``RWE_SOURCE_BACKOFF_AFTER``, default 3), because
        the doubling assumes consecutive failures are evidence of an outage — which holds only when
        failures are correlated. GDELT's are not: it refuses roughly 40% of requests as load
        shedding, independently of anything we do, so runs of four or six arrive by chance.
        Measured in production: ``gdelt://doc`` reached 7 consecutive failures, which under the old
        rule multiplied its 30-minute interval by 16 and pinned it at the 6-hour ceiling. That took
        a source succeeding ~58% of the time from ~28 ingests a day to ~2 — and, because
        ``consecutive_failures`` is persisted, a restart did not clear it.

        The ceiling is a small multiple of the adapter's OWN interval rather than an absolute wall
        clock, so a source polled every 15 minutes and one polled hourly get proportional treatment.
        A genuinely dead source still ends up politely spaced; a flaky-but-working one keeps its
        cadence."""
        base = max(1.0, adapter.interval())
        fails = self._consecutive.get(adapter.health_key, 0)
        after = max(1, _int_env("RWE_SOURCE_BACKOFF_AFTER", 3))
        if fails < after:
            return base
        steps = _int_env("RWE_SOURCE_BACKOFF_STEPS", 2)          # 2x, 4x, then flat
        factor = 2 ** min(fails - after + 1, max(0, steps))
        return min(base * factor, _float_env("RWE_SOURCE_MAX_INTERVAL", 6 * 3600.0))

    # -- per-source health (reuses store.record_feed_health; mirrors FeedPoller's glue) --
    def _record_health(self, name, url, stats, latency_ms, error) -> None:
        rec = self.store.record_feed_health(
            url, ok=(error is None), name=name, latency_ms=latency_ms,
            error=(f"{type(error).__name__}: {error}" if error is not None else None),
            stats=stats or {}, unhealthy_after=self.unhealthy_after)
        self._consecutive[url] = int(rec.get("consecutiveFailures") or 0)
        if error is not None:
            self._log(logging.WARNING, "source_health", feed=url, healthy=rec["healthy"],
                      consecutiveFailures=rec["consecutiveFailures"], error=rec["lastError"])
        if rec.get("transition") == "unhealthy":
            self._log(logging.WARNING, "feed_unhealthy", feed=url)
        elif rec.get("transition") == "recovered":
            self._log(logging.INFO, "feed_recovered", feed=url)

    # -- reused post-cycle seams: validation-aware retention + the hot-refresh trigger. Fires on feed
    # growth OR when a request-path producer flagged the catalog dirty between cycles (Commit 18 D6:
    # an extension read creates an article the poller's own counters never see; without this check a
    # quiet feed stalls that article's graph entry indefinitely). Trigger condition only — ingestion,
    # retention, and the refresh machinery are untouched. --
    def _post_cycle(self, agg: dict) -> bool:
        grew = agg.get("new", 0) > 0
        dirty = self._dirty_check is not None and self._dirty_check()
        if grew or dirty:
            self._maintenance_pending = True
        # ── COALESCING THE CATALOG-WIDE STEPS ──────────────────────────────────────────────────
        # Retention and the hot refresh both cost a function of CATALOG SIZE, not of what this
        # adapter brought, and both run while holding `self._lock`. "One incremental pass per
        # cycle" was written for a single poller loop; there are now 13 adapter threads, so the
        # catalog paid for a full pass every time ANY of them found a single article.
        #
        # Measured on production, 6 h window against 12 h uptime, 150,000 rows:
        #   post-cycle 18,176.9 s + poll 797.6 s / 21,600 s = 87.8% LOCK OCCUPANCY
        #   per post-cycle: cleanup 38-50%, refresh 36-50%, warm 11-17%
        # kait8 paid 216 s of it for 2 new articles; GNews paid 90 s for 10. Every rebuild but the
        # last in a window is superseded before a reader can use it — the same waste the inline
        # `story_cache_warm` had, one step over.
        #
        # This is SCHEDULING ONLY. Nothing moves off the lock and no new thread appears: the two
        # expensive steps are coalesced to at most one pass per window, which is what "per cycle"
        # meant before the poller grew from one loop to eleven. Narrowing the lock itself is M6.
        #
        # THREE properties this must not break, each one a bug that already happened here:
        #  * A pending pass is never LOST. `_maintenance_pending` outlives the skip, and a due pass
        #    runs even on a cycle that ingested nothing — otherwise ingestion going quiet right
        #    after a skip would strand the last window's rows indefinitely, which is precisely the
        #    "a quiet feed stalls that article's graph entry" failure `dirty_check` exists for.
        #  * A dirty nudge BYPASSES the throttle. It is the request path explicitly asking, and
        #    D6's latency bound is a promise rather than a probability (corpus_refresh:327). Nudges
        #    come from reads, not from the eleven pollers, so they cannot reintroduce the cost.
        #  * The FIRST pass after start is never delayed: `_last_maintenance` starts at None.
        due = self._maintenance_pending and (
            dirty or self._last_maintenance is None
            or (time.monotonic() - self._last_maintenance) >= _maintenance_interval())
        if not (grew or dirty or due):
            return False
        # Storage lifecycle: catalog retention PLUS the bounded prunes for derived/operational
        # tables (orphaned event locations, score cache, analytics, rec-events, snapshots). One
        # incremental pass per WINDOW — never user data, never a long write lock.
        t0 = time.perf_counter()
        if due:
            try:
                storage_lifecycle.run_cleanup(self.store, log=self._log)
            except Exception as e:                          # cleanup must never break polling
                self._log(logging.WARNING, "storage_cleanup_failed",
                          error=f"{type(e).__name__}: {e}")
        t_cleanup = time.perf_counter()
        # THE WARM IS NO LONGER HERE. It ran on this thread, holding this lock, for a full
        # clustering build — see `_post_cycle_unlocked`, which now owns it and runs after the lock
        # is released. What stays locked is what actually writes the catalog: retention above and
        # the hot refresh below.
        if due and self._on_cycle is not None:
            try:
                self._on_cycle(agg)
            except Exception as e:                          # a downstream hook must never break polling
                self._log(logging.ERROR, "multi_source_on_cycle_error", error=repr(e))
        t_hook = time.perf_counter()
        if due:
            # Only after BOTH steps have actually run. Stamping the clock earlier would let a
            # cleanup that raised, or a refresh that never happened, reset the window anyway.
            self._last_maintenance = time.monotonic()
            self._maintenance_pending = False
        # Production measured postCycleMs at 83-122 s per cycle against a 5.7 s warm, so ~93% of the
        # most expensive loop in the process was inside a step nobody had timed. These three numbers
        # are what turn "the post-cycle is slow" into a named owner.
        # `warmMs` is deliberately GONE from this line rather than reported as 0.0: it is no longer
        # a segment of the locked phase, and a zero would read as "the warm was free" instead of
        # "the warm is measured elsewhere". `post_cycle_unlocked` carries it now.
        self._log(logging.INFO, "post_cycle",
                  cleanupMs=round((t_cleanup - t0) * 1000.0, 1),
                  refreshMs=round((t_hook - t_cleanup) * 1000.0, 1),
                  # Which of the two shapes this was. Without it a coalesced cycle and an expensive
                  # one differ only by their durations, and "why is cleanupMs 0" has no answer in
                  # the log — the same complaint that produced the three timings above.
                  maintenance=bool(due), coalesced=bool(self._maintenance_pending))
        return grew or dirty

    def _post_cycle_unlocked(self, agg: dict) -> float:
        """The post-cycle steps that do NOT need the ingest write lock. Returns milliseconds spent.

        ## Why this phase exists

        `request_warm` is non-blocking only when `warm_coalesce_window() > 0`, and that defaults to
        **0** — OFF by measured decision, not oversight (`story_service:2829`: production warms sit
        ~60 s apart so there is no burst to merge, and *delaying* a warm costs more than it saves).
        With the window at 0 it calls `warm_cache` inline on the caller's thread. That was fine; the
        bug was doing it while holding `self._lock`.

        Measured on production 2026-08-26, after the catalog-wide steps were coalesced: `warmMs` was
        14-20 s on **every** ingesting cycle against a 13,624 ms full clustering build — the same
        number — making it the largest single contributor to the 24.9% lock occupancy that remained.

        ## Why taking it off the lock is safe, and not the same as turning coalescing on

        Turning coalescing on would re-introduce the **delay** those measurements rejected. This
        keeps the warm synchronous and immediate and only stops it blocking other adapters, which is
        the part that was never justified: `warm_cache` reads the catalog and builds an in-process
        cache. It never needs the **write** lock the poller holds to serialise ingests.

        Two properties make the unlocked version strictly better rather than merely faster:

        * **`warm_cache` already single-flights** on its own `_WARM_LOCK` with a non-blocking
          acquire. `sources.py` records that this guard "was written for concurrent adapters ... so
          adapter warms are SERIALIZED and the guard has never fired for them". Off the lock it
          starts working as designed: concurrent adapters collapse to one build instead of queueing
          up N sequential ones. A stood-down warm is not a lost one — the winner's build covers the
          same catalog.
        * **Breaking detection keeps its ordering AND its write lock.** It reads the cache the warm
          just built, so it has to stay after it; it also WRITES event rows, so it re-enters
          `self._lock` for its own duration rather than racing an adapter's ingest. Brief and
          explicit beats moving a writer off the write lock to save a few seconds.
        """
        t0 = time.perf_counter()

        def _warm_log(event, **fields):
            self._log(logging.WARNING if event.endswith("_failed") else logging.INFO,
                      event, **fields)

        # Fail-soft throughout: a warm that cannot be built is a slow next request, never a broken
        # poll loop.
        try:
            story_service.request_warm(self.store, log=_warm_log)
        except Exception as e:
            self._log(logging.WARNING, "story_cache_warm_failed", error=f"{type(e).__name__}: {e}")

        # Breaking-story detection (OFF unless RWE_BREAKING_NOTIFICATIONS is set) — the same seam as
        # FeedPoller's copy in feed_service.py, and it must exist in BOTH: either chassis may be the
        # one polling, and a producer wired to only one of them would simply never fire in whichever
        # deployment ran the other. Idempotent and stateless; the event row's UNIQUE constraint is
        # what makes running it every cycle correct rather than merely harmless.
        #
        # It WRITES, so it takes the lock back for exactly its own duration. Cheap when the feature
        # is off (`detect_breaking_stories` returns 0 at its first line) and correct when it is on.
        #
        # `acquire(timeout=)` rather than `with self._lock:` — the difference is a hang versus a log
        # line. This method MUST NOT be called while already holding the lock, and if it ever is,
        # `threading.Lock` is not reentrant, so a bare `with` deadlocks the calling adapter thread
        # forever. That is not hypothetical: reverting the warm back inside the lock to check that
        # these tests actually flip hung the test run instead of failing it. A deadlock here
        # presents as "ingestion stopped", with no error anywhere — the worst shape a fault can
        # take in this loop. The timeout turns it into something an operator can find.
        try:
            import story_events                  # lazy: keeps story_intelligence out of this import graph
            # ASK BEFORE QUEUEING. `detect_breaking_stories` returns 0 at its first line when the
            # feature is off, which is the default — so taking a contended lock to call it buys
            # nothing and can cost the wait below. Production fired `breaking_detect_lock_timeout`
            # once in an hour doing exactly that: blocking on a lock in order to invoke a no-op.
            if not story_events.enabled():
                pass
            elif not self._lock.acquire(timeout=_BREAKING_LOCK_TIMEOUT_S):
                self._log(logging.ERROR, "breaking_detect_lock_timeout",
                          waitedSeconds=_BREAKING_LOCK_TIMEOUT_S,
                          detail="could not re-take the ingest lock; skipping breaking detection "
                                 "for this cycle. If this repeats, _post_cycle_unlocked is being "
                                 "called while the lock is already held.")
            else:
                try:
                    story_events.detect_breaking_stories(self.store, log=_warm_log)
                finally:
                    self._lock.release()
        except Exception as e:
            self._log(logging.WARNING, "breaking_detect_failed", error=f"{type(e).__name__}: {e}")

        # Push delivery (OFF unless RWE_PUSH_DELIVERY is set). Hangs off the same post-cycle seam as
        # breaking-story detection, immediately after it, so an event recorded above can be delivered
        # on this cycle rather than the next.
        #
        # `request_delivery` STARTS A THREAD and returns: the fan-out is network I/O against a third
        # party, and blocking ingestion on it would trade a delayed notification for a stale corpus.
        # One run at a time — a request during a run is dropped, not queued, so a slow push service
        # cannot turn every cycle into another overlapping fan-out.
        try:
            import push_delivery                 # lazy: keeps the push stack out of this import graph
            push_delivery.request_delivery(
                self.store, log=lambda lvl, ev, **f: self._log(lvl, ev, **f))
        except Exception as e:
            self._log(logging.WARNING, "push_delivery_request_failed",
                      error=f"{type(e).__name__}: {e}")
        ms = (time.perf_counter() - t0) * 1000.0
        self._log(logging.INFO, "post_cycle_unlocked", warmMs=round(ms, 1))
        return ms

    def poll_adapter_once(self, adapter: SourceAdapter) -> dict:
        t_start = time.perf_counter()
        # ── M6.2: THE FETCH COMES OFF THE LOCK ─────────────────────────────────────────────────
        # An adapter opts in by declaring FETCH_IS_STORE_FREE *and* leaving `poll_once` alone — the
        # split lives in the base implementation, so an override would simply not use it, and
        # honouring the flag anyway would run a store-touching override unlocked.
        #
        # Why this and not more workers first: at 16.0% occupancy the remaining lock-held cost that
        # SCALES with source count is the poll (120.2 s/h), because coalescing made the post-cycle
        # O(1) per window. A crawl source costs ~2.4 s of lock per poll x 4 polls/h = 9.6 s/h, so
        #     saturation  (3600 - 458) / 9.6 = 327 sources
        #     50% comfort (1800 - 458) / 9.6 = 140 sources
        # and essentially all of that 2.4 s is a network round trip to a publisher. Worker leases
        # and a bounded pool — the rest of M6 — buy NOTHING until this moves: N workers would each
        # take the same lock, fetch for 2.4 s, and release, serialising exactly as one worker does.
        # Concurrency behind a global lock is not concurrency, so this is the dependency.
        # `getattr` with a False default, not attribute access: the registry accepts DUCK-TYPED
        # adapters that never inherit SourceAdapter, and an opt-in split must default to "no" for
        # anything that has not said otherwise rather than raising at them.
        collected = None
        if (getattr(adapter, "FETCH_IS_STORE_FREE", False)
                and type(adapter).poll_once is SourceAdapter.poll_once):
            collected = adapter.collect()                   # network + parse, no lock, no store
        t_fetch = time.perf_counter()
        with self._lock:                                    # write-safe: one adapter ingests at a time
            t0 = time.perf_counter()
            if collected is None:
                agg = adapter.poll_once(self.store, self.scorer, on_feed=self._record_health)
            else:
                agg = adapter.persist(collected, self.store, self.scorer,
                                      on_feed=self._record_health)
            t1 = time.perf_counter()
            warm_wanted = self._post_cycle(agg)
            t2 = time.perf_counter()
        # ── OUTSIDE THE LOCK ───────────────────────────────────────────────────────────────────
        # M6's first piece. Everything above serialises adapters against each other because it
        # WRITES the catalog; the warm only reads it and builds an in-process cache, so holding the
        # lock across it bought nothing and cost 14-20 s of every other adapter's time.
        warm_ms = self._post_cycle_unlocked(agg) if warm_wanted else 0.0
        # pollMs / postCycleMs: fetch+parse+score+write against retention+warm+refresh. The split is
        # the point — they are different problems with different fixes, and without it a slow cycle
        # is just "slow". Measured after `story_cache_warm` turned out to be logged on a branch
        # production does not run: an expensive path with no duration in the log is a path nobody
        # can rank, and this loop is the one that owns the process's CPU.
        self._log(logging.WARNING if agg.get("failed") else logging.INFO, "source_poll",
                  provider=adapter.provider, sourceType=adapter.source_type, new=agg.get("new", 0),
                  duplicates=agg.get("duplicates", 0), failed=agg.get("failed", 0),
                  catalog=self.store.count_feed_articles(),
                  # pollMs + postCycleMs is still exactly LOCK-HELD time, so the occupancy sum
                  # that has tracked this all along keeps meaning the same thing. warmMs is the
                  # work that moved OUT of it — reported, not hidden, so the change shows up as
                  # occupancy falling rather than as time disappearing from the logs.
                  # pollMs REMAINS lock-held time and nothing else, so `sum(pollMs+postCycleMs)
                  # / wall` still measures lock occupancy exactly as it has through every
                  # measurement in this series. What changed is how much work is inside it: for a
                  # split adapter this is now the ingest alone, and `fetchMs` carries the network
                  # half that moved out. Reported, not hidden — the same rule as offLockWarmMs.
                  pollMs=round((t1 - t0) * 1000.0, 1), postCycleMs=round((t2 - t1) * 1000.0, 1),
                  fetchMs=round((t_fetch - t_start) * 1000.0, 1),
                  # NOT `warmMs`: that name is already on `post_cycle_unlocked`, and one field name
                  # across two events makes `grep -o '"warmMs"' | sum` silently double every warm.
                  # It did, on the first measurement of this change.
                  offLockWarmMs=round(warm_ms, 1))
        return agg

    # ── M6.3: the bounded worker pool ──────────────────────────────────────────────────────────
    #
    # Thread-per-adapter tied thread count to SOURCE count. That was invisible while the ingest lock
    # serialised everything — N threads all queued on it, so N did not matter. M6.2 removed the lock
    # from the fetch and moved the measured ceiling from ~327 crawl sources to ~2,200, at which point
    # the old model would mean 2,200 threads and 2,200 simultaneous outbound connections.
    #
    # A pool decouples the two: sources live in a due-time table, a fixed number of workers lease
    # them, and concurrency is capped by the pool rather than by how many publishers exist.
    #
    # TWO LOCKS, NEVER NESTED THE WRONG WAY. `self._lock` is the ingest write lock; `self._sched` is
    # the due-time table. A worker holds `_sched` only to claim or release a lease and never while
    # polling, so the ordering is always sched-then-release-then-ingest. Holding the scheduler lock
    # across a poll would recreate exactly the global serialisation this milestone exists to remove.

    def _claim(self) -> "Optional[_Lease]":
        """Block until a source is due, then lease it. ``None`` means the poller is stopping.

        **Earliest-due-first**, which is what makes starvation impossible: a source that has been
        waiting longest is always taken before one that just came due, so no source can be
        indefinitely overtaken by a busier neighbour.
        """
        with self._sched:
            while not self._stop.is_set():
                now = time.monotonic()
                ready = [l for l in self._leases if not l.leased and l.due <= now]
                if ready:
                    lease = min(ready, key=lambda l: l.due)
                    lease.leased = True
                    return lease
                pending = [l.due for l in self._leases if not l.leased]
                # Sleep exactly until the next source is due — not a fixed tick. A poll interval is
                # minutes; a busy-wait here would burn a core to save nothing.
                timeout = max(0.05, min(pending) - now) if pending else 1.0
                self._sched.wait(timeout)
            return None

    def _release(self, lease: "_Lease") -> None:
        """Schedule this source's next poll and hand the slot back.

        `_effective_interval` is REUSED verbatim rather than reimplemented: the adaptive backoff it
        encodes — including the sustained-failure rule that GDELT's ~40% load shedding forced — is
        per-source scheduling policy, and a pool that re-derived it would drift from the
        thread-per-adapter path it has to stay equivalent to.
        """
        wait = self._effective_interval(lease.adapter)
        if wait > lease.adapter.interval():
            self._log(logging.WARNING, "source_poll_backoff", provider=lease.adapter.provider,
                      consecutiveFailures=self._consecutive.get(lease.adapter.health_key, 0),
                      intervalSec=round(wait), baseIntervalSec=round(lease.adapter.interval()))
        with self._sched:
            lease.due = time.monotonic() + max(1.0, wait)
            lease.leased = False
            self._sched.notify()

    def _worker(self, index: int) -> None:
        """One pool worker: claim a due source, poll it, release it, repeat.

        Isolation is per LEASE, not per worker. An adapter that raises must not take the worker down
        with it — under thread-per-adapter that cost one source its polling; in a pool it would cost
        every source that worker would have served next.
        """
        while not self._stop.is_set():
            lease = self._claim()
            if lease is None:
                break
            try:
                self.poll_adapter_once(lease.adapter)
            except Exception as e:                          # isolation: one source never stops another
                self._log(logging.ERROR, "source_poll_cycle_failed",
                          provider=lease.adapter.provider, error=repr(e))
            finally:
                self._release(lease)                        # even on failure — a raised source must
                                                            # be rescheduled, not dropped forever
        self._log(logging.INFO, "source_worker_stopped", worker=index)

    def _run_adapter(self, adapter: SourceAdapter) -> None:
        self._log(logging.INFO, "source_poll_start", provider=adapter.provider, interval=adapter.interval())
        while not self._stop.is_set():
            try:
                self.poll_adapter_once(adapter)
            except Exception as e:                          # isolation: one adapter never stops another
                self._log(logging.ERROR, "source_poll_cycle_failed", provider=adapter.provider, error=repr(e))
            wait = self._effective_interval(adapter)
            if wait > adapter.interval():
                self._log(logging.WARNING, "source_poll_backoff", provider=adapter.provider,
                          consecutiveFailures=self._consecutive.get(adapter.health_key, 0),
                          intervalSec=round(wait), baseIntervalSec=round(adapter.interval()))
            self._stop.wait(max(1.0, wait))                 # interruptible per-adapter sleep
        self._log(logging.INFO, "source_poll_stopped", provider=adapter.provider)

    def start(self) -> None:
        """Start one daemon thread per enabled adapter (idempotent). Each polls immediately, then every
        its own interval; an exception in one thread never touches another."""
        if self.running:
            return
        adapters = self.registry.enabled()
        # Say why a REGISTERED adapter is not among them. `enabled()` returning False is silent by
        # construction, and for a crawl adapter the usual reason is a missing RWE_CORPUS_SHADOW
        # entry — a config the operator believes is live. `shadow_warning()` was written for exactly
        # this and had no caller: a diagnostic nothing invokes is the same defect as a gate that
        # cannot fire, and it made "turning on only the flag tells you why" an untrue sentence.
        for a in self.registry.adapters():
            warn = getattr(a, "shadow_warning", None)
            if warn is None:
                continue
            try:
                message = warn()
            except Exception:                     # a diagnostic must never break startup
                continue
            if message:
                self._log(logging.WARNING, "source_adapter_inert",
                          provider=getattr(a, "provider", "?"), reason=message)
        if not adapters:
            self._log(logging.INFO, "multi_source_no_adapters")
            return
        self._stop.clear()
        self._threads = []
        workers = min(_poll_workers(), len(adapters)) if _poll_workers() else 0
        if workers > 0:
            # Every source starts due NOW, matching thread-per-adapter's "polls immediately, then
            # every its own interval". The pool then meters them; it does not delay the first pass.
            now = time.monotonic()
            with self._sched:
                self._leases = [_Lease(adapter=a, due=now) for a in adapters]
            for a in adapters:                              # parity with _run_adapter's own line, so
                self._log(logging.INFO, "source_poll_start",   # an operator still sees each source
                          provider=a.provider, interval=a.interval())
            for i in range(workers):
                t = threading.Thread(target=self._worker, args=(i,), name=f"src-worker-{i}",
                                     daemon=True)
                t.start()
                self._threads.append(t)
        else:
            for a in adapters:
                t = threading.Thread(target=self._run_adapter, args=(a,), name=f"src-{a.source_type}", daemon=True)
                t.start()
                self._threads.append(t)
        self._log(logging.INFO, "multi_source_start", adapters=[a.provider for a in adapters],
                  # Which scheduler is running, and its concurrency cap. Without this the two models
                  # are indistinguishable in the log, and "why are only 4 sources polling at once"
                  # would have no answer anywhere.
                  mode=("pool" if workers else "thread-per-adapter"), workers=workers)

    def stop(self, join_timeout: float = 10.0) -> None:
        self._stop.set()
        # Wake every worker parked in `_claim`. Without this a pool with nothing due sleeps until
        # the next due time — minutes — and `stop()` would look like a hang rather than a shutdown.
        with self._sched:
            self._sched.notify_all()
        for t in self._threads:
            if t.is_alive():
                t.join(timeout=join_timeout)

    @property
    def running(self) -> bool:
        return any(t.is_alive() for t in self._threads)


# --------------------------------------------------------------------------- #
# One-shot CLI (complements rss_ingest.py) — ingest / inspect every enabled source once.
# --------------------------------------------------------------------------- #
def _cli_health_recorder(store_):
    def rec(name, url, stats, latency_ms, error):
        try:
            store_.record_feed_health(
                url, ok=(error is None), name=name, latency_ms=latency_ms,
                error=(f"{type(error).__name__}: {error}" if error is not None else None),
                stats=stats or {})
        except Exception:
            pass
    return rec


def main(argv=None) -> int:
    """One-shot multi-source ingest / status (complements ``rss_ingest.py``):

        python examples/sources.py poll     # poll every ENABLED adapter once into the catalog
        python examples/sources.py check    # per-adapter enabled/config status only (no ingest)

    Reuses the same pipeline, scorer, dedup, and health as the running engine — set the same env
    (RWE_NEWSAPI_ENABLED / RWE_NEWSAPI_API_KEY / RWE_GDELT_ENABLED / RWE_DB_URL) so it writes the
    catalog the engine reads."""
    import argparse
    from collections import Counter
    import store as store_mod
    ap = argparse.ArgumentParser(description="one-shot multi-source ingest / status",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", choices=("poll", "check"), default="poll")
    ap.add_argument("--db", default=None, help="RWE_DB_URL override")
    args = ap.parse_args(argv)

    reg = default_registry()
    for w in config_warnings(reg):
        print(f"  ! {w}")
    st = store_mod.Store(args.db)
    scorer = rss_ingest.make_scorer()
    rec = _cli_health_recorder(st)
    for a in reg.adapters():
        print(f"[{a.provider:<8}] {'enabled ' if a.enabled() else 'disabled'} "
              f"source_type={a.source_type} interval={a.interval():.0f}s key={a.health_key}")
        if args.command == "poll" and a.enabled():
            agg = a.poll_once(st, scorer, on_feed=rec)
            if "located" in agg:
                # Enrichment sources (GKG) create no articles — new/duplicates are always 0 for
                # them, so report what they DID do or the run looks like a silent no-op.
                print(f"           -> windows={agg.get('windows', 0)} "
                      f"records={agg.get('records', 0)} matched={agg.get('matched', 0)} "
                      f"located={agg.get('located', 0)} images={agg.get('images', 0)} "
                      f"windowErrors={agg.get('windowErrors', 0)} "
                      f"errors={agg.get('errors') or '-'}")
            else:
                extra = ""
                if "rateLimited" in agg:                    # NewsAPI accounting rides the aggregate
                    extra = f"rateLimited={agg['rateLimited']} "
                    if agg.get("budgetExhausted"):
                        extra += "budgetExhausted=True "
                print(f"           -> new={agg.get('new', 0)} duplicates={agg.get('duplicates', 0)} "
                      f"failed={agg.get('failed', 0)} raw={agg.get('rawCount', 0)} {extra}"
                      f"errors={agg.get('errors') or '-'}")
    rows = st.list_feed_articles(limit=1_000_000)
    print(f"catalog: {st.count_feed_articles()} articles  "
          f"by source: {dict(Counter(r.get('sourceType') for r in rows))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
