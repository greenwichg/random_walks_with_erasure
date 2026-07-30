"""push_ratelimit.py — how fast the fan-out may hand messages to one push service (Phase B4).

A pure leaf: standard library, no store, no network, and no clock of its own beyond one injectable
``monotonic``. What it decides is how long a caller should wait before its next send.

**Per push service, not global.** Endpoints belong to a handful of hosts — ``fcm.googleapis.com``,
``updates.push.services.mozilla.com``, ``*.notify.windows.com`` — and each is an independent
operator with its own capacity. A global limit throttles Firefox because Chrome is slow, which
punishes the wrong readers for someone else's bad day and makes the fan-out slower than either
service asked for. Keying on the host is what makes the limit mean "be polite to *them*".

**A token bucket, not a fixed interval.** Fan-outs are bursty by nature: an event produces every send
it will ever produce within one cycle, then nothing for an hour. A fixed minimum gap would stretch a
burst a service could absorb comfortably; a bucket lets the idle time pay for it, which is both
faster and closer to what rate limits are actually written to mean. ``burst`` is what the idle time
may bank.

**This is a floor on politeness, not a ceiling on throughput.** ``Retry-After`` (``push_retry``) is
the reactive half — the service telling us it has had enough. This is the proactive half: not
arriving at that point in the first place. They are complementary and neither replaces the other.
"""

from __future__ import annotations

import threading
import time
from urllib.parse import urlsplit

#: Sends per second per push service when the limiter is on. Deliberately unhurried: a fan-out is
#: not latency-critical work, and a notification that lands two seconds later is indistinguishable to
#: a reader from one that did not.
DEFAULT_RATE = 10.0

#: How much idle time may be banked, in sends. One second's worth — enough that a small fan-out after
#: a quiet hour goes out at once, not so much that a large one arrives as a single spike.
DEFAULT_BURST = 10.0


def host_of(endpoint: str) -> str:
    """The push service an endpoint belongs to. Empty string when it cannot be determined, which
    groups all such endpoints together — the conservative answer, since anything unparseable is more
    likely one broken source than many independent ones.

    No ``.lower()``: ``urlsplit().hostname`` already case-folds (``netloc`` does not, which is the
    trap). Measured rather than assumed — a mutation run reported the call as having no effect, and
    the interpreter agreed. A redundant fold would be harmless but would also imply a normalisation
    step that is not there, and the next reader would keep it for that reason."""
    try:
        return urlsplit(str(endpoint or "")).hostname or ""
    except ValueError:
        return ""


class RateLimiter:
    """Token buckets keyed by push-service host.

    Thread-safe because the send phase is a pool: the whole point is that four workers share one
    budget per host, so the bookkeeping has to be shared too. The lock is held only for arithmetic —
    **never across the sleep** — or four workers would serialise behind each other's waits and the
    pool would be a pool in name only.
    """

    def __init__(self, rate: float = DEFAULT_RATE, burst: "float | None" = None,
                 monotonic=time.monotonic) -> None:
        self.rate = max(0.0, float(rate))
        self.burst = max(1.0, float(burst if burst is not None else max(rate, 1.0)))
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._buckets: "dict[str, tuple[float, float]]" = {}   # host -> (tokens, last refill)

    @property
    def enabled(self) -> bool:
        """A rate of zero means unlimited. Off is a real configuration — a deployment with a handful
        of readers cannot out-run a push service, and a limiter that never fires is one more thing
        between an event and a lock screen."""
        return self.rate > 0.0

    def acquire_delay(self, endpoint: str) -> float:
        """Seconds the caller must wait before sending to ``endpoint``. Consumes a token.

        Returns rather than sleeps, so the caller decides how to spend the wait — and so this stays
        testable without any real time passing, which a method that slept could not be."""
        if not self.enabled:
            return 0.0
        host = host_of(endpoint)
        now = self._monotonic()
        with self._lock:
            tokens, last = self._buckets.get(host, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens >= 1.0:
                self._buckets[host] = (tokens - 1.0, now)
                return 0.0
            # Not enough for a whole token: wait for the shortfall, and record the send as already
            # spent so a concurrent caller queues behind it rather than being told the same wait.
            wait = (1.0 - tokens) / self.rate
            self._buckets[host] = (tokens - 1.0, now)
            return wait

    def wait(self, endpoint: str, *, sleep=time.sleep) -> float:
        """Block until a send to ``endpoint`` is allowed. Returns how long that took."""
        delay = self.acquire_delay(endpoint)
        if delay > 0.0:
            sleep(delay)
        return delay


def from_env(env) -> "RateLimiter | None":
    """Build a limiter from ``RWE_PUSH_MAX_SENDS_PER_SECOND``, or ``None`` when it is off.

    Absent means the default rate — on by default, because the failure it prevents (a fan-out that
    trips a push service's own limit and turns every send into a retry) is silent, gradual, and only
    visible once it is already happening. ``0`` switches it off explicitly, which is the escape hatch
    for an operator who has decided the limit is the problem."""
    raw = (env.get("RWE_PUSH_MAX_SENDS_PER_SECOND") or "").strip()
    if not raw:
        return RateLimiter(DEFAULT_RATE, DEFAULT_BURST)
    try:
        rate = float(raw)
    except ValueError:
        return RateLimiter(DEFAULT_RATE, DEFAULT_BURST)   # unparseable is not a licence to flood
    return RateLimiter(rate, max(rate, 1.0)) if rate > 0 else None
