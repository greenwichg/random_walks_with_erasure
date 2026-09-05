"""metering.py — the meter and the two limits: per-key rate, per-tenant monthly quota.

Rate is the engine's own token-bucket (``ratelimit.RateLimiter``) on its own instance, keyed by
key id, so a customer's burst budget is the plan's and not the anonymous-IP scope the engine
applies to everything else (that outer limit still stands — defence in depth). Quota is read from
the durable daily rollup (``platform_usage_daily``), so it survives restarts and counts every key
a tenant holds. Recording is FAIL-SOFT: a metering fault is counted, never surfaced as a failed
request — the customer's answer was correct, and the audit row is what is lost.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import obs_metrics
import ratelimit

_LIMITER = ratelimit.RateLimiter()


def reset() -> None:
    """Fresh buckets (tests)."""
    global _LIMITER
    _LIMITER = ratelimit.RateLimiter()


def month_of(now: "datetime | None" = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def check_rate(principal) -> "tuple[bool, int]":
    """``(allowed, retry_after_seconds)`` for one request under the key's per-minute rate."""
    if not principal.rate_per_min:
        return True, 0
    return _LIMITER.check(f"key:{principal.key_id}", int(principal.rate_per_min))


def check_quota(store_, principal, *, now: "datetime | None" = None) -> dict:
    """``{used, limit, exceeded}`` for the tenant's current month. ``limit`` 0 = unlimited."""
    limit = int(principal.quota_month or 0)
    used = store_.platform_usage_month(principal.tenant_id, month_of(now))["units"]
    return {"used": int(used), "limit": limit, "exceeded": bool(limit and used >= limit)}


def record(store_, principal, *, endpoint: str, status: int, latency_ms: Optional[float],
           request_id: Optional[str], units: int = 1) -> None:
    try:
        store_.platform_record_usage(tenant_id=principal.tenant_id, key_id=principal.key_id,
                                     endpoint=endpoint, units=units, status=status,
                                     request_id=request_id, latency_ms=latency_ms)
    except Exception:                       # noqa: BLE001 — the answer stands; the row is lost
        obs_metrics.incr("platform_metering_errors_total")


__all__ = ["reset", "month_of", "check_rate", "check_quota", "record"]
