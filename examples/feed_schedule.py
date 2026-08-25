"""feed_schedule.py — per-feed polling policy: WHEN to ask a feed, and WHAT to send.

The production poller sweeps every configured feed on one global interval (``RWE_POLL_INTERVAL``,
600 s) and downloads each one in full, every time. Three consequences follow from that one design,
and they are the systemic weaknesses this module exists to fix:

* **Freshness is priced in bandwidth.** Halving the interval doubles the bytes, so the cadence is
  set by what the slowest-changing feed costs rather than by what the fastest-moving one needs.
* **Publish rate is ignored.** A wire filing 200 stories a day and a weekly column are asked at
  exactly the same rate, so one is under-served and the other is pestered.
* **Per-feed failure is recorded and then dropped on the floor.** ``store.record_feed_health``
  has tracked ``consecutive_failures`` per feed since it was written, but the scheduler's backoff
  (``sources.MultiSourcePoller._effective_interval``) keys on the ADAPTER, so a permanently-404
  feed is re-asked at full rate forever while its own failure count sits in the database, read by
  nothing.

This module is the policy, kept pure and separate from transport and storage so it can be tested
without either. It decides three things and performs none of them:

    due(state, now)              -> may this feed be asked yet?
    validators(state)            -> which conditional-GET headers to send
    advance(state, outcome, ...) -> the next interval and next due time

**Everything here is off by default** (``RWE_FEED_SCHEDULER``). Off, the poller sweeps exactly as
it does today, byte for byte, and no state is read or written.

## The adaptive law

One rule, no per-site configuration, no publisher names anywhere:

* the feed **changed** (new articles, or a 200 with different content) -> move the interval DOWN
  toward the floor, multiplicatively;
* the feed was **unchanged** (a 304, or a 200 that produced nothing new) -> move it UP toward the
  ceiling, multiplicatively;
* the feed **failed** -> back off from the CURRENT interval, bounded by the same ceiling, and let
  the persisted consecutive-failure count decide how hard.

Multiplicative on both sides because publish rates vary by orders of magnitude between a wire and
a personal column, and an additive step tuned for one is wrong for the other. The floor and
ceiling are the only numbers an operator sets, and both are wall-clock bounds an outsider could
verify from our request log — which is the honest form for a politeness knob to take.

**Conditional GET is what makes the floor affordable.** A feed that has not changed answers a
validated request with 304 and no body, so asking a quiet feed often costs a few hundred bytes
rather than a full document. Roughly 89% of feeds carry ``ETag`` and 73% ``Last-Modified``
(Mozilla Observatory's published survey), and the two are sent together because a feed that
supports neither must still be correct — it simply answers 200 every time and lands on the
unchanged branch through content comparison instead.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Optional

#: Fastest we will ever ask ONE feed, in seconds. A floor, not a target: the adaptive law only
#: reaches it for a feed that changes on nearly every poll. Two minutes is deliberately not
#: aggressive — with conditional GET an unchanged poll is a 304 with no body, and the point of the
#: floor is to serve breaking news on a wire, not to win a latency benchmark.
DEFAULT_MIN_INTERVAL = 120.0

#: Slowest we will let a healthy feed drift to. A feed that never changes still gets asked twice a
#: day, because "it stopped publishing" and "it broke silently" look identical from here and only
#: a request distinguishes them.
DEFAULT_MAX_INTERVAL = 6 * 3600.0

#: Multiplier applied when a feed changed (< 1 shortens) and when it did not (> 1 lengthens).
#: Deliberately asymmetric: news arrives in bursts, so reacting fast to a change and drifting back
#: slowly serves freshness better than a symmetric pair. The ratio also means a feed that alternates
#: change/no-change settles near the fast end rather than oscillating around the middle.
DEFAULT_SPEEDUP = 0.5
DEFAULT_SLOWDOWN = 1.5


def _float_env(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, "").strip())
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def enabled() -> bool:
    """Whether per-feed scheduling is on. ``RWE_FEED_SCHEDULER=1`` enables it; unset/0 is off and
    byte-identical to the uniform sweep — no state read, no state written, no header sent."""
    return (os.environ.get("RWE_FEED_SCHEDULER", "").strip().lower()
            in {"1", "true", "yes", "on"})


def min_interval() -> float:
    return _float_env("RWE_FEED_MIN_INTERVAL", DEFAULT_MIN_INTERVAL)


def max_interval() -> float:
    return _float_env("RWE_FEED_MAX_INTERVAL", DEFAULT_MAX_INTERVAL)


def speedup() -> float:
    return _float_env("RWE_FEED_SPEEDUP", DEFAULT_SPEEDUP)


def slowdown() -> float:
    return _float_env("RWE_FEED_SLOWDOWN", DEFAULT_SLOWDOWN)


def content_hash(data: bytes) -> str:
    """A cheap fingerprint of a feed body, for the ``changed`` decision when a publisher offers no
    validators. Hashing the BYTES rather than the parsed entries is deliberate: parsing to compare
    would spend the CPU that conditional GET exists to save, and a body that differs only in a
    build timestamp is still evidence the publisher regenerated the document — the adaptive law
    treats that as a weak change signal, which is the safe direction (it polls a little more)."""
    return hashlib.sha1(data or b"").hexdigest()


@dataclass(frozen=True)
class FeedState:
    """One feed's scheduling state. Persisted on ``feed_health``; ``None`` everywhere means a feed
    the scheduler has not met yet, which is always due."""

    etag: Optional[str] = None
    last_modified: Optional[str] = None
    next_due_at: Optional[str] = None
    interval_s: Optional[float] = None
    content_sha: Optional[str] = None
    consecutive_failures: int = 0


def _parse(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def due(state: FeedState, *, now: Optional[datetime] = None) -> bool:
    """Whether this feed may be asked now.

    Unknown state is DUE — a feed the scheduler has never seen, or whose stored timestamp is
    unparseable, is polled rather than skipped. Fail-open is the only safe direction here: the
    failure mode of the other choice is a feed that silently stops being collected, which looks
    exactly like a publisher going quiet and would be found by nobody."""
    when = _parse(state.next_due_at)
    if when is None:
        return True
    return (now or datetime.now(timezone.utc)) >= when


def validators(state: FeedState) -> dict:
    """The conditional-GET headers for this feed — ``{}`` when we hold nothing to validate against.

    Both are sent when both are held. They are not redundant: a server may honour one and ignore
    the other, and RFC 9110 has the server evaluate ``If-None-Match`` first when both arrive, so
    sending the pair costs two short headers and takes whichever the origin actually implements."""
    out = {}
    if state.etag:
        out["If-None-Match"] = state.etag
    if state.last_modified:
        out["If-Modified-Since"] = state.last_modified
    return out


def advance(state: FeedState, *, changed: bool, failed: bool = False,
            etag: Optional[str] = None, last_modified: Optional[str] = None,
            content_sha: Optional[str] = None,
            now: Optional[datetime] = None) -> FeedState:
    """The next state after one poll outcome. Pure: no clock unless you leave ``now`` unset, no I/O.

    Failure is handled first and separately. A failing feed's interval is grown from its CURRENT
    value by the persisted consecutive-failure count, so a feed that 404s permanently walks itself
    out to the ceiling instead of being asked every cycle forever — the gap this module's docstring
    names. Validators are NOT overwritten on failure: a transient 500 must not discard the ETag
    that will make the next successful poll cheap.

    On success the law is the two multiplicative branches, clamped to [floor, ceiling]. A feed
    with no prior interval starts at the floor when it changed and at the geometric middle
    otherwise, so a newly-added feed converges from a defensible place rather than from whatever
    the global sweep happened to be."""
    lo, hi = min_interval(), max_interval()
    base = state.interval_s
    at = now or datetime.now(timezone.utc)

    if failed:
        fails = state.consecutive_failures + 1
        start = base if base and base > 0 else lo
        nxt = min(start * (2 ** min(fails, 6)), hi)
        return replace(state, interval_s=nxt, consecutive_failures=fails,
                       next_due_at=(at + timedelta(seconds=nxt)).isoformat())

    if base and base > 0:
        nxt = base * (speedup() if changed else slowdown())
    else:
        nxt = lo if changed else (lo * hi) ** 0.5
    nxt = max(lo, min(nxt, hi))
    return FeedState(
        etag=etag if etag is not None else state.etag,
        last_modified=last_modified if last_modified is not None else state.last_modified,
        next_due_at=(at + timedelta(seconds=nxt)).isoformat(),
        interval_s=nxt,
        content_sha=content_sha if content_sha is not None else state.content_sha,
        consecutive_failures=0)
