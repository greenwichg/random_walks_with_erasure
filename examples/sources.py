"""sources.py — the pluggable multi-source ingestion layer (Commit 11).

Every ingestion source (RSS/Atom, NewsAPI, GDELT, and future providers) is a :class:`SourceAdapter`
that **normalizes its data into the existing** :class:`rss_ingest.FeedEntry` and terminates at the
existing ``rss_ingest.ingest_entries`` pipeline. After that boundary the whole platform — scoring,
canonical-URL dedup, media selection, persistence, search, clustering, Story Intelligence,
recommendations — behaves **exactly as it does for RSS today** and never learns where an article came
from.

    NewsAPI / GDELT / RSS  ->  SourceAdapter.fetch()  ->  normalize()  ->  SourceBatch(FeedEntry[])
                            ->  ingest_entries()       ->  FeedArticle  ->  everything else

Design:
  * Adapters reuse the ingestion pipeline; they never duplicate scoring/dedup/media/persistence.
  * RSS reuses ``rss_ingest.ingest_all`` verbatim (identical behaviour, per-feed health).
  * A :class:`SourceRegistry` holds the adapters; :class:`MultiSourcePoller` iterates the enabled ones,
    so the poller is provider-agnostic and future adapters need no poller change.
  * Health reuses ``store.record_feed_health`` under a stable per-source key (``rss://…`` per feed,
    ``newsapi://everything``, ``gdelt://doc``). ``feed_service.FeedPoller`` is left untouched.

No network is contacted unless an adapter is enabled; ``fetch`` is injectable so tests run offline.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

import rss_ingest      # reuse: FeedEntry, load_feeds/fetch_feed/parse_feed, ingest_entries, ingest_all
import media           # reuse: pick_best_image (image SELECTION only — never modified, never downloads)
import corpus_health   # reuse: validation-aware retention (post-cycle, exactly as FeedPoller runs it)

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


def _int_or_none(name: str) -> Optional[int]:
    v = os.environ.get(name)
    return int(v) if v and v.lstrip("-").isdigit() else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_log(level: int, event: str, **fields) -> None:
    _logger.log(level, json.dumps({"event": event, **fields}, default=str))


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
    def poll_once(self, store_, scorer, *, on_feed: Optional[Callable] = None) -> dict:
        """Fetch -> normalize -> apply the per-source quota -> ``ingest_entries`` -> record health.
        Never raises for a fetch/parse error — it records the failure and returns an aggregate with the
        error, so one source's outage can't affect another."""
        t0 = time.perf_counter()
        error = None
        batch: Optional[SourceBatch] = None
        stats: Optional[dict] = None
        try:
            batch = self.normalize(self.fetch())
            entries = batch.entries
            cap = self.max_articles()
            if cap is not None and cap >= 0:
                entries = entries[:cap]                    # quota applies BEFORE ingest_entries
            stats = rss_ingest.ingest_entries(
                entries, self.provider, self.health_key, scorer, store_,
                source_type=self.source_type, source_provider=self.provider)
        except Exception as e:                              # network / parse / provider error
            error = e
        latency_ms = (time.perf_counter() - t0) * 1000.0
        agg = _agg(self.provider, self.source_type, stats, batch, latency_ms, error, key=self.health_key)
        if on_feed is not None:
            try:
                on_feed(self.provider, self.health_key, stats, latency_ms, error)
            except Exception:                              # health recording must never break polling
                pass
        return agg


def _agg(provider, source_type, stats, batch, latency_ms, error, *, key) -> dict:
    """A per-cycle aggregate shaped like ``rss_ingest.ingest_all``'s, plus source metadata."""
    s = stats or {}
    return {"provider": provider, "sourceType": source_type, "feeds": 1,
            "ok": 0 if error else 1, "failed": 1 if error else 0,
            "entries": s.get("entries", 0), "new": s.get("new", 0),
            "duplicates": s.get("duplicates", 0), "skipped": s.get("skipped", 0),
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
        fetch_feed/parse_feed/ingest_entries (only the entry list is truncated first)."""
        feeds = rss_ingest.load_feeds(self.feeds_spec)
        cap = self.max_articles()
        if cap is None:
            agg = rss_ingest.ingest_all(feeds, scorer, store_, on_feed=on_feed)   # unchanged RSS path
        else:
            agg = self._ingest_capped(feeds, scorer, store_, cap, on_feed)
        agg["provider"] = self.provider
        agg["sourceType"] = self.source_type
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
                for k in ("entries", "new", "duplicates", "skipped"):
                    agg[k] += result[k]
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
# NewsAPI adapter — https://newsapi.org/docs
# --------------------------------------------------------------------------- #
class NewsAPIAdapter(SourceAdapter):
    provider = "NewsAPI"
    source_type = "newsapi"

    def __init__(self, fetch: Optional[Callable[[str], dict]] = None):
        self._fetch_fn = fetch                              # injectable (offline tests)

    def api_key(self) -> str:
        return os.environ.get("RWE_NEWSAPI_API_KEY", "").strip()

    def enabled(self) -> bool:
        return _bool_env("RWE_NEWSAPI_ENABLED") and bool(self.api_key())

    def config_warning(self) -> Optional[str]:
        if _bool_env("RWE_NEWSAPI_ENABLED") and not self.api_key():
            return ("RWE_NEWSAPI_ENABLED is set but RWE_NEWSAPI_API_KEY is missing/empty — NewsAPI "
                    "stays disabled. Get a free key at https://newsapi.org and set RWE_NEWSAPI_API_KEY.")
        return None

    def interval(self) -> float:
        return _float_env("RWE_NEWSAPI_POLL_INTERVAL", 900.0)

    def max_articles(self) -> Optional[int]:
        return _int_or_none("RWE_NEWSAPI_MAX_ARTICLES")

    @property
    def health_key(self) -> str:
        return f"newsapi://{os.environ.get('RWE_NEWSAPI_ENDPOINT', 'top-headlines')}"

    def _url(self) -> str:
        endpoint = os.environ.get("RWE_NEWSAPI_ENDPOINT", "top-headlines")
        params = {"pageSize": str(min(self.max_articles() or 100, 100))}
        for env, key in (("RWE_NEWSAPI_QUERY", "q"), ("RWE_NEWSAPI_CATEGORY", "category"),
                         ("RWE_NEWSAPI_COUNTRY", "country"), ("RWE_NEWSAPI_LANGUAGE", "language")):
            v = os.environ.get(env)
            if v:
                params[key] = v
        # top-headlines needs at least one of country/category/q/sources to be a valid request.
        if endpoint == "top-headlines" and not ({"q", "category", "country"} & set(params)):
            params["country"] = "us"
        return f"https://newsapi.org/v2/{endpoint}?{urllib.parse.urlencode(params)}"

    def fetch(self) -> dict:
        url = self._url()
        if self._fetch_fn is not None:
            return self._fetch_fn(url)
        req = urllib.request.Request(url, headers={"X-Api-Key": self.api_key(), "User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=_float_env("RWE_NEWSAPI_TIMEOUT", 15.0)) as resp:
            return json.loads(resp.read())

    def normalize(self, raw: dict) -> SourceBatch:
        arts = (raw or {}).get("articles") or []
        cat = os.environ.get("RWE_NEWSAPI_CATEGORY") or None
        lang = os.environ.get("RWE_NEWSAPI_LANGUAGE") or None
        country = os.environ.get("RWE_NEWSAPI_COUNTRY") or None
        entries = []
        for a in arts:
            url = (a.get("url") or "").strip()
            if not url:
                continue
            img = media.pick_best_image([{"url": a.get("urlToImage"), "source": "newsapi"}]) or {}
            entries.append(rss_ingest.FeedEntry(
                url=url, title=a.get("title") or "", description=a.get("description") or "",
                body=a.get("content") or None, published_at=rss_ingest._to_iso(a.get("publishedAt") or ""),
                image=img.get("url"), image_width=img.get("width"), image_height=img.get("height"),
                image_mime=img.get("mime"), image_source=(img.get("source") if img else None),
                source_type="newsapi", source_provider="NewsAPI", category=cat, language=lang,
                country=country, external_id=url,
                publisher_hint=((a.get("source") or {}).get("name") or None)))
        return SourceBatch(self.provider, self.source_type, _now_iso(), entries, raw_count=len(arts))


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

    def fetch(self) -> dict:
        url = self._url()
        if self._fetch_fn is not None:
            return self._fetch_fn(url)
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=_float_env("RWE_GDELT_TIMEOUT", 15.0)) as resp:
            return json.loads(resp.read())

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
    """The standard three-source registry (RSS + NewsAPI + GDELT). Future adapters (Guardian API,
    Reuters, AP, Reddit, Hacker News, …) register here without touching the poller."""
    reg = SourceRegistry()
    reg.register(RSSAdapter(feeds_spec=feeds_spec))
    reg.register(NewsAPIAdapter())
    reg.register(GDELTAdapter())
    return reg


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
                 log: Optional[Callable] = None, on_cycle: Optional[Callable[[dict], None]] = None):
        self.store = store_
        self.scorer = scorer or rss_ingest.make_scorer()
        self.registry = registry or default_registry()
        self._log = log or _default_log
        self._on_cycle = on_cycle                           # hot-refresh seam (reused; never modified)
        self.unhealthy_after = _int_env("RWE_FEED_UNHEALTHY_AFTER", 3)
        self._stop = threading.Event()
        self._threads: list = []
        # Serialize DB writes + the post-cycle hook across adapters so concurrent polls stay SQLite-safe.
        self._lock = threading.Lock()

    # -- per-source health (reuses store.record_feed_health; mirrors FeedPoller's glue) --
    def _record_health(self, name, url, stats, latency_ms, error) -> None:
        rec = self.store.record_feed_health(
            url, ok=(error is None), name=name, latency_ms=latency_ms,
            error=(f"{type(error).__name__}: {error}" if error is not None else None),
            stats=stats or {}, unhealthy_after=self.unhealthy_after)
        if error is not None:
            self._log(logging.WARNING, "source_health", feed=url, healthy=rec["healthy"],
                      consecutiveFailures=rec["consecutiveFailures"], error=rec["lastError"])
        if rec.get("transition") == "unhealthy":
            self._log(logging.WARNING, "feed_unhealthy", feed=url)
        elif rec.get("transition") == "recovered":
            self._log(logging.INFO, "feed_recovered", feed=url)

    # -- reused post-cycle seams: validation-aware retention + the hot-refresh trigger (only on growth) --
    def _post_cycle(self, agg: dict) -> None:
        if agg.get("new", 0) <= 0:
            return
        if corpus_health.retention_enabled():
            corpus_health.run_retention(self.store, log=self._log)
        if self._on_cycle is not None:
            try:
                self._on_cycle(agg)
            except Exception as e:                          # a downstream hook must never break polling
                self._log(logging.ERROR, "multi_source_on_cycle_error", error=repr(e))

    def poll_adapter_once(self, adapter: SourceAdapter) -> dict:
        with self._lock:                                    # write-safe: one adapter ingests at a time
            agg = adapter.poll_once(self.store, self.scorer, on_feed=self._record_health)
            self._post_cycle(agg)
        self._log(logging.WARNING if agg.get("failed") else logging.INFO, "source_poll",
                  provider=adapter.provider, sourceType=adapter.source_type, new=agg.get("new", 0),
                  duplicates=agg.get("duplicates", 0), failed=agg.get("failed", 0),
                  catalog=self.store.count_feed_articles())
        return agg

    def _run_adapter(self, adapter: SourceAdapter) -> None:
        self._log(logging.INFO, "source_poll_start", provider=adapter.provider, interval=adapter.interval())
        while not self._stop.is_set():
            try:
                self.poll_adapter_once(adapter)
            except Exception as e:                          # isolation: one adapter never stops another
                self._log(logging.ERROR, "source_poll_cycle_failed", provider=adapter.provider, error=repr(e))
            self._stop.wait(max(1.0, adapter.interval()))   # interruptible per-adapter sleep
        self._log(logging.INFO, "source_poll_stopped", provider=adapter.provider)

    def start(self) -> None:
        """Start one daemon thread per enabled adapter (idempotent). Each polls immediately, then every
        its own interval; an exception in one thread never touches another."""
        if self.running:
            return
        adapters = self.registry.enabled()
        if not adapters:
            self._log(logging.INFO, "multi_source_no_adapters")
            return
        self._stop.clear()
        self._threads = []
        for a in adapters:
            t = threading.Thread(target=self._run_adapter, args=(a,), name=f"src-{a.source_type}", daemon=True)
            t.start()
            self._threads.append(t)
        self._log(logging.INFO, "multi_source_start", adapters=[a.provider for a in adapters])

    def stop(self, join_timeout: float = 10.0) -> None:
        self._stop.set()
        for t in self._threads:
            if t.is_alive():
                t.join(timeout=join_timeout)

    @property
    def running(self) -> bool:
        return any(t.is_alive() for t in self._threads)
