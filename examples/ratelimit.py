"""Lightweight, dependency-free rate limiting for the Information Health engine.

A token-bucket limiter with **no Redis and no external services** — state is per process, in
memory. For the Private Alpha (a single engine process) this is sufficient; a multi-process or
multi-host deployment would move the buckets behind a shared store, and :class:`RateLimiter` is
the seam where that swap happens. Nothing here touches the recommendation, health-report, or
serialisation code: the limiter only decides whether a request is allowed, *before* the existing
handler runs, so every API contract is preserved.

The serving layer (``api_fastapi.py``) classifies each request into a *scope* and keys the bucket
by the authenticated user (when known) or the client IP, then calls :meth:`RateLimiter.check`.

Scopes and their PRODUCTION sustained rates (requests / minute):

    auth    brute-force-sensitive: token resolution + identity upsert
    ai      expensive LLM: the coach narrative (POST /api/coach)
    ingest  reading-event ingestion (POST /api/me/reads)
    write   other state-changing calls (settings, onboarding, tokens, ...)
    read    read-only GETs
    default anything unmatched

Development multiplies every rate by :data:`RATE_DEV_FACTOR`, so local work and the Colab demo
are never throttled under normal use; any rate can be overridden per scope with an environment
variable (``RWE_RATELIMIT_<SCOPE>_PER_MIN``), and the whole limiter disabled with
``RWE_RATELIMIT_ENABLED=0``.
"""

from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

# Per-scope sustained rate (requests/minute) in PRODUCTION. Burst capacity == one minute's worth.
RATE_DEFAULTS = {"auth": 30, "ai": 15, "ingest": 60, "write": 60, "read": 240, "default": 120}
# Development is deliberately relaxed: real rates are these defaults times this factor.
RATE_DEV_FACTOR = 50
# Bound memory: never track more than this many distinct keys (evict least-recently-seen beyond).
MAX_BUCKETS = 50_000

# Requests that must never be throttled: health/readiness probes and the API docs. The root path
# and CORS pre-flight are handled by the caller (method check) but listed here for clarity.
_EXEMPT_PATHS = frozenset({"/api/health", "/openapi.json", "/docs", "/redoc",
                           "/docs/oauth2-redirect", "/favicon.ico"})


def enabled() -> bool:
    """Whether rate limiting is active (default on). Set ``RWE_RATELIMIT_ENABLED=0`` to disable."""
    return os.environ.get("RWE_RATELIMIT_ENABLED", "1").strip().lower() not in {
        "0", "false", "no", "off"}


def _int_env(name: str) -> Optional[int]:
    v = os.environ.get(name)
    if v is not None and v.strip().lstrip("-").isdigit():
        return int(v.strip())
    return None


def rate_for(scope: str, production: bool) -> int:
    """The sustained requests/minute for ``scope``.

    An explicit ``RWE_RATELIMIT_<SCOPE>_PER_MIN`` env var wins verbatim; otherwise the production
    default is used, multiplied by :data:`RATE_DEV_FACTOR` outside production."""
    override = _int_env(f"RWE_RATELIMIT_{scope.upper()}_PER_MIN")
    if override is not None:
        return max(1, override)
    base = RATE_DEFAULTS.get(scope, RATE_DEFAULTS["default"])
    return base if production else base * RATE_DEV_FACTOR


def scope_for(method: str, path: str) -> Optional[str]:
    """Classify a request into a rate-limit scope, or ``None`` to exempt it.

    Deterministic on (method, path) only — no body inspection — so it is cheap and stable."""
    if method == "OPTIONS" or path in _EXEMPT_PATHS:
        return None
    # Authentication surface — token resolution and identity upsert are the brute-force vectors.
    if path in {"/api/internal/resolve-token", "/api/internal/users"}:
        return "auth"
    # Expensive AI — the coach's grounded narrative (LLM when a key is configured).
    if path == "/api/coach" and method == "POST":
        return "ai"
    # Ingestion — scoring + persistence of reading events (also the browser-extension path).
    if path == "/api/me/reads" and method == "POST":
        return "ingest"
    if method in {"POST", "PUT", "PATCH", "DELETE"}:
        return "write"
    if method in {"GET", "HEAD"}:
        return "read"
    return "default"


@dataclass
class _Bucket:
    tokens: float
    updated: float


class RateLimiter:
    """A token-bucket limiter keyed by an arbitrary string. Thread-safe.

    Each key gets a bucket of ``rate_per_min`` capacity that refills at ``rate_per_min/60`` tokens
    per second; a request costs one token. ``check`` returns ``(allowed, retry_after_seconds)`` —
    ``retry_after`` is 0 when allowed, else the whole seconds until a token is available."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._buckets: "dict[str, _Bucket]" = {}
        self._lock = threading.Lock()
        self._clock = clock

    def check(self, key: str, rate_per_min: int) -> "tuple[bool, int]":
        capacity = float(max(1, rate_per_min))
        refill = capacity / 60.0                     # tokens per second
        now = self._clock()
        with self._lock:
            b = self._buckets.get(key)
            if b is None:
                if len(self._buckets) >= MAX_BUCKETS:
                    self._evict_locked()
                b = _Bucket(tokens=capacity, updated=now)
                self._buckets[key] = b
            else:
                b.tokens = min(capacity, b.tokens + (now - b.updated) * refill)
                b.updated = now
            if b.tokens >= 1.0:
                b.tokens -= 1.0
                return True, 0
            retry = max(1, math.ceil((1.0 - b.tokens) / refill))
            return False, retry

    def _evict_locked(self) -> None:
        """Drop the least-recently-seen ~10% of keys to bound memory (rare; only when full)."""
        victims = sorted(self._buckets.items(), key=lambda kv: kv[1].updated)[: MAX_BUCKETS // 10]
        for k, _ in victims:
            self._buckets.pop(k, None)

    def reset(self) -> None:
        """Clear all buckets (used by tests for deterministic isolation)."""
        with self._lock:
            self._buckets.clear()
