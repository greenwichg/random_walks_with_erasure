"""In-process application metrics (OBS1) — a tiny, dependency-free, thread-safe collector.

No external monitoring dependency yet: counters and latency histograms live in memory and are read back
via :func:`snapshot` (which the internal-only ``/api/metrics`` endpoint serves). **Bounded by
construction** — fixed latency buckets and a cap on distinct series — so it can never grow without limit.
**Purely observational**: recording a metric never raises into the caller (every path is guarded), so it
cannot change request behavior. A later phase can drain :func:`snapshot` into Prometheus / OpenTelemetry
without touching call sites."""
from __future__ import annotations

import threading
import time
from typing import Dict

# Latency histogram bucket upper bounds (milliseconds); an implicit +Inf bucket follows the last.
_BUCKETS_MS = (1, 5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000)
# Safety cap on distinct metric series: beyond this, new series are dropped rather than grow unbounded.
_MAX_SERIES = 1024


class _Stat:
    """Running latency stats + a fixed-bucket histogram for approximate percentiles."""
    __slots__ = ("count", "total", "min", "max", "buckets")

    def __init__(self) -> None:
        self.count = 0
        self.total = 0.0
        self.min = float("inf")
        self.max = 0.0
        self.buckets = [0] * (len(_BUCKETS_MS) + 1)

    def observe(self, v: float) -> None:
        self.count += 1
        self.total += v
        if v < self.min:
            self.min = v
        if v > self.max:
            self.max = v
        for i, edge in enumerate(_BUCKETS_MS):
            if v <= edge:
                self.buckets[i] += 1
                return
        self.buckets[-1] += 1

    def _percentile(self, p: float):
        if self.count == 0:
            return None
        target = p / 100.0 * self.count
        edges = list(_BUCKETS_MS) + [float("inf")]
        cum = 0
        for i, c in enumerate(self.buckets):
            cum += c
            if cum >= target:
                e = edges[i]
                return None if e == float("inf") else e
        return None

    def to_dict(self) -> dict:
        return {"count": self.count, "sumMs": round(self.total, 1),
                "minMs": round(self.min, 1) if self.count else 0.0,
                "maxMs": round(self.max, 1),
                "avgMs": round(self.total / self.count, 1) if self.count else 0.0,
                "p50Ms": self._percentile(50), "p95Ms": self._percentile(95),
                "p99Ms": self._percentile(99)}


class Metrics:
    """Thread-safe counters + latency timers. All entry points are guarded and never raise."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Dict[str, int] = {}
        self._timers: Dict[str, _Stat] = {}
        self._started = time.time()

    def incr(self, name: str, n: int = 1) -> None:
        try:
            with self._lock:
                if name in self._counters or len(self._counters) < _MAX_SERIES:
                    self._counters[name] = self._counters.get(name, 0) + n
        except Exception:
            pass

    def observe(self, name: str, value_ms: float) -> None:
        try:
            with self._lock:
                st = self._timers.get(name)
                if st is None:
                    if len(self._timers) >= _MAX_SERIES:
                        return
                    st = self._timers[name] = _Stat()
                st.observe(float(value_ms))
        except Exception:
            pass

    def record_request(self, method: str, route: str, status: int, duration_ms: float) -> None:
        """One request → a counter (by method·route·status-class) and a latency timer (by method·route).
        ``route`` should be the matched **route template** (not the raw path) so cardinality stays bounded."""
        key = f"{method} {route}"
        self.incr(f"requests_total|{key}|{status // 100}xx")
        self.observe(f"request_ms|{key}", duration_ms)

    def snapshot(self) -> dict:
        with self._lock:
            return {"uptimeSeconds": round(time.time() - self._started, 1),
                    "series": {"counters": len(self._counters), "timers": len(self._timers)},
                    "counters": dict(self._counters),
                    "timers": {k: v.to_dict() for k, v in self._timers.items()}}

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._timers.clear()


_METRICS = Metrics()


def metrics() -> Metrics:
    return _METRICS


def incr(name: str, n: int = 1) -> None:
    _METRICS.incr(name, n)


def observe(name: str, value_ms: float) -> None:
    _METRICS.observe(name, value_ms)


def record_request(method: str, route: str, status: int, duration_ms: float) -> None:
    _METRICS.record_request(method, route, status, duration_ms)


def snapshot() -> dict:
    return _METRICS.snapshot()


class timer:
    """Context manager that times its block and records the elapsed milliseconds under ``name``.
    Never raises — a metrics failure can never break the timed code."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._t0 = 0.0

    def __enter__(self) -> "timer":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc) -> bool:
        try:
            observe(self.name, (time.perf_counter() - self._t0) * 1000.0)
        except Exception:
            pass
        return False
