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

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

import rss_ingest      # reuse: FeedEntry, load_feeds/fetch_feed/parse_feed, ingest_entries, ingest_all
import media           # reuse: pick_best_image (image SELECTION only — never modified, never downloads)
import corpus_health   # reuse: validation-aware retention (post-cycle, exactly as FeedPoller runs it)
import storage_lifecycle  # reuse: the ONE bounded cleanup pass (catalog + derived tables)
import gdelt_gkg       # reuse: the Phase-2 event-geography enrichment logic (offline-testable)

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


def _get_json(url: str, *, headers=None, timeout: float = 15.0,
              retries: Optional[int] = None, backoff: Optional[float] = None,
              on_transient: Optional[Callable[[int], None]] = None) -> dict:
    """HTTP GET -> parsed JSON, retrying **transient** failures (HTTP 429 + 5xx) with linear backoff.
    429 is common on shared IPs (e.g. GDELT from Colab), so a one-shot poll shouldn't fail on it; a
    non-transient error (e.g. 401 Unauthorized) raises immediately. Tunable via RWE_SOURCE_RETRIES /
    RWE_SOURCE_BACKOFF. ``on_transient`` (if given) is called with the HTTP code for EVERY transient
    response — including the one that exhausts the retries — so callers can count rate-limit events
    instead of the retry loop silently absorbing them."""
    retries = _int_env("RWE_SOURCE_RETRIES", 3) if retries is None else retries
    backoff = _float_env("RWE_SOURCE_BACKOFF", 5.0) if backoff is None else backoff
    req = urllib.request.Request(url, headers=headers or {})
    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            attempt += 1
            transient = e.code == 429 or 500 <= e.code < 600
            if transient and on_transient is not None:
                try:
                    on_transient(e.code)
                except Exception:
                    pass                                  # metrics must never break the fetch
            if not transient or attempt > retries:
                raise
            time.sleep(min(backoff * attempt, 60.0))     # 5s, 10s, 15s … (capped)


def _get_bytes(url: str, *, headers=None, timeout: float = 30.0,
               retries: Optional[int] = None, backoff: Optional[float] = None) -> bytes:
    """HTTP GET -> raw bytes, with the SAME transient-retry discipline as :func:`_get_json`
    (429 + 5xx retried with linear backoff; anything else raises immediately). Used for the GKG
    zip + manifest, which are files, not JSON."""
    retries = _int_env("RWE_SOURCE_RETRIES", 3) if retries is None else retries
    backoff = _float_env("RWE_SOURCE_BACKOFF", 5.0) if backoff is None else backoff
    req = urllib.request.Request(url, headers=headers or {"User-Agent": _USER_AGENT})
    attempt = 0
    while True:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            attempt += 1
            transient = e.code == 429 or 500 <= e.code < 600
            if not transient or attempt > retries:
                raise
            time.sleep(min(backoff * attempt, 60.0))


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
# KeyedJSONAdapter — the hardened chassis every keyed JSON news API rides.
# --------------------------------------------------------------------------- #
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
        return SourceBatch(self.provider, self.source_type, _now_iso(), entries, raw_count=len(arts))


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
                url=link, title=title, description=item.findtext("description") or "",
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

    def fetch(self) -> dict:
        url = self._url()
        if self._fetch_fn is not None:
            return self._fetch_fn(url)
        return _get_json(url, headers={"User-Agent": _USER_AGENT},
                         timeout=_float_env("RWE_GDELT_TIMEOUT", 15.0))

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

    def poll_once(self, store_, scorer, *, on_feed: Optional[Callable] = None) -> dict:
        t0 = time.perf_counter()
        error = None
        stats: Optional[dict] = None
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
    Google News RSS + GDELT articles, + the GKG event-geography enricher). Future adapters (Reuters,
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
    reg.register(GDELTGKGEnricher())   # enrichment last: it annotates articles the others ingested
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

    # -- reused post-cycle seams: validation-aware retention + the hot-refresh trigger. Fires on feed
    # growth OR when a request-path producer flagged the catalog dirty between cycles (Commit 18 D6:
    # an extension read creates an article the poller's own counters never see; without this check a
    # quiet feed stalls that article's graph entry indefinitely). Trigger condition only — ingestion,
    # retention, and the refresh machinery are untouched. --
    def _post_cycle(self, agg: dict) -> None:
        if agg.get("new", 0) <= 0 and not (self._dirty_check is not None and self._dirty_check()):
            return
        # Storage lifecycle: catalog retention PLUS the bounded prunes for derived/operational
        # tables (orphaned event locations, score cache, analytics, rec-events, snapshots). One
        # incremental pass per cycle — never user data, never a long write lock.
        try:
            storage_lifecycle.run_cleanup(self.store, log=self._log)
        except Exception as e:                              # cleanup must never break polling
            self._log(logging.WARNING, "storage_cleanup_failed", error=f"{type(e).__name__}: {e}")
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
