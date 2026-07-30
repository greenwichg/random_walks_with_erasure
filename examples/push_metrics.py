"""push_metrics.py — the push pipeline's counters (Phase B4).

``docs/BROWSER_PUSH_ARCHITECTURE.md`` §7 asks for a specific list: notifications considered, sends
attempted, sends succeeded, sends failed **by classification**, subscriptions pruned, and fan-out
duration. Not a general facility — that list, because the send path is the first part of this system a
reader cannot see failing, and the reason §7 asks for it is that *interpretation* matters more than
collection. A rising prune rate is ordinary attrition; a rising `permanent` rate is a credential
defect we shipped. A single "failures" number cannot tell an operator which of those is happening,
which is the whole argument for splitting them.

**Built on ``obs_metrics``, not beside it.** That module already exists, is already thread-safe and
bounded, and is already served by the internal-only ``/api/metrics``. A second registry would mean a
second endpoint, a second cap on series, and two places to look during an incident. This file is
therefore a naming convention plus the guarantee that every classification gets a counter — including
the ones that are zero, because "no `permanent` failures today" and "we are not counting `permanent`
failures" look identical when the series is simply absent.

Everything here is best-effort. A metrics failure must never change what the pipeline does, so every
call is guarded, exactly as ``obs_metrics`` guards its own.
"""

from __future__ import annotations

import obs_metrics
import push_sender

PREFIX = "push_"

#: Every outcome gets a counter, pre-registered at zero. A missing series and a zero series are
#: indistinguishable to whoever is reading the snapshot at 3am, and they mean opposite things.
STATUSES = (push_sender.SUCCESS, push_sender.EXPIRED, push_sender.TIMEOUT,
            push_sender.TRANSIENT, push_sender.PERMANENT)

#: Counters this module owns. Named here rather than only at the call sites so the snapshot's shape
#: is a declared contract — a dashboard or an alert can be written against it before the first send.
COUNTERS = (
    "push_runs_total",
    "push_considered_total",
    "push_attempted_total",
    "push_succeeded_total",
    "push_failed_total",
    "push_pruned_total",
    "push_retries_scheduled_total",
    "push_retries_exhausted_total",
    "push_retries_abandoned_total",
    "push_deliveries_recovered_total",
    "push_rate_limited_total",
    *(f"push_failed_{status}_total" for status in STATUSES if status != push_sender.SUCCESS),
)


def initialize() -> None:
    """Register every counter at zero. Called once at startup.

    Without it the snapshot only ever shows outcomes that have happened, so the first `permanent`
    failure looks like a new metric rather than a change in an existing one — and nobody can write an
    alert on a series that does not exist yet."""
    try:
        for name in COUNTERS:
            obs_metrics.incr(name, 0)
    except Exception:                         # noqa: BLE001 — observational, never behavioural
        pass


def _incr(name: str, n: int = 1) -> None:
    if n <= 0:
        return
    try:
        obs_metrics.incr(name, n)
    except Exception:                         # noqa: BLE001 — see the module docstring
        pass


def record_attempt(status: str) -> None:
    """One send, classified. The `by classification` half of §7's list, and the half that carries the
    information: `expired` is weather, `permanent` is a bug we shipped."""
    _incr("push_attempted_total")
    if status == push_sender.SUCCESS:
        _incr("push_succeeded_total")
        return
    _incr("push_failed_total")
    _incr(f"push_failed_{status}_total")


def record_rate_limited(seconds: float) -> None:
    """A send that waited on the rate limiter. Counted, and its wait observed, because a fan-out that
    is slow because we are throttling ourselves looks exactly like one that is slow because the push
    service is — and the fixes are opposite."""
    _incr("push_rate_limited_total")
    try:
        obs_metrics.observe("push_rate_limit_wait_ms", max(0.0, seconds) * 1000.0)
    except Exception:                         # noqa: BLE001
        pass


def record_run(stats, duration_ms: float) -> None:
    """One completed fan-out. ``stats`` is a :class:`push_delivery.RunStats`."""
    _incr("push_runs_total")
    _incr("push_considered_total", int(getattr(stats, "considered", 0) or 0))
    _incr("push_pruned_total", int(getattr(stats, "pruned", 0) or 0))
    _incr("push_retries_scheduled_total", int(getattr(stats, "scheduled", 0) or 0))
    _incr("push_retries_exhausted_total", int(getattr(stats, "exhausted", 0) or 0))
    _incr("push_retries_abandoned_total", int(getattr(stats, "abandoned", 0) or 0))
    _incr("push_deliveries_recovered_total", int(getattr(stats, "recovered", 0) or 0))
    try:
        # §7 asks for fan-out duration by name. It is the number that says whether the pipeline is
        # keeping up: a run that takes longer than the poll interval means every cycle now starts
        # behind, and the dropped-not-queued rule turns that into fewer fan-outs rather than more.
        obs_metrics.observe("push_run_ms", max(0.0, float(duration_ms)))
    except Exception:                         # noqa: BLE001
        pass


def snapshot() -> dict:
    """Just this pipeline's counters, for an operator who wants the push picture without reading the
    whole application snapshot. Derived from the same registry, so the two can never disagree."""
    try:
        counters = obs_metrics.snapshot().get("counters", {}) or {}
    except Exception:                         # noqa: BLE001
        return {}
    return {k: v for k, v in counters.items() if k.startswith(PREFIX)}
