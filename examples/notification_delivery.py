"""notification_delivery.py — the orchestration seam between the pure notification leaf and the app.

It builds a :class:`notification_service.NotificationContext` from **persisted producer state only**,
runs the pure :func:`notification_service.evaluate`, and materialises the due notifications via
:meth:`store.record_notifications`. It is deliberately thin and does exactly one dangerous-sounding
thing — read-then-persist — and nothing else:

* it **reads** the latest *saved* report snapshot, the reader's *stored* reads, the delivery ledger,
  and normalised settings; and
* it **generates nothing** — no recommendation, no report, no explanation, no coach turn. Nothing on
  this path invokes the recommender, ``health_report``, ``Personalizer.explain``, or any coach logic,
  so recommendation determinism, the REPORT CONTRACT, explain==served parity, and coach behaviour are
  untouched by construction.

``store`` is passed in (dependency injection, like ``settings_service`` / the coach tools), so this
module never imports the storage layer. ``api_server`` and ``feed_service`` are imported *lazily*
inside the functions (the established coach pattern) purely to reuse two shared, side-effect-free
helpers — the single ``_reading_streak`` / ``_read_at`` day-bucketing definitions — so notification
recency never diverges from the dashboard's.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import notification_service as ns
import settings_service


def _opt_int(value) -> "int | None":
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _blind_spot_topics(report: "dict | None") -> tuple:
    """STABLE blind-spot identifiers only — the report's blind-spot **topics**. The serialized
    blind-spot objects also carry ``gap`` / ``note`` fields whose share percentages shift as the
    reader reads; hashing those would thrash the dedupe key, so only the stable ``topic`` is used."""
    spots = (report or {}).get("blindSpots") or []
    return tuple(str(s["topic"]) for s in spots
                 if isinstance(s, dict) and s.get("topic"))


def build_context(store, uid: int, now: "datetime | None" = None) -> "ns.NotificationContext":
    """Assemble a NotificationContext from PERSISTED producer state only. Reads: the latest *saved*
    report snapshot (``latest_report``), the reader's stored reads (``list_reads`` + the shared
    ``_read_at`` timestamp), the delivery ledger (``delivered_notification_keys``), and normalised
    settings (``settings_service.get``). Builds and generates nothing."""
    now = now or datetime.now(timezone.utc)
    import api_server as engine          # lazy: the ONE _reading_streak / _read_at definition
    from feed_service import _parse_iso  # lazy: the shared robust ISO parser

    report = store.latest_report(uid)                          # the SAVED snapshot, never a fresh compute
    read_ats = [engine._read_at(row) for row in (store.list_reads(uid) or [])]
    today = now.date().isoformat()
    week_ago = now - timedelta(days=7)

    def _within_week(ra) -> bool:
        dt = _parse_iso(ra)
        return dt is not None and dt >= week_ago

    reading = ns.ReadingInputs(
        streak_days=engine._reading_streak(read_ats),          # reuse the existing streak logic verbatim
        read_today=any(isinstance(ra, str) and ra[:10] == today for ra in read_ats),
        reads_this_week=sum(1 for ra in read_ats if _within_week(ra)))

    return ns.NotificationContext(
        now=now,
        settings=settings_service.get(store, uid),
        delivery=ns.DeliveryState(
            delivered_keys=frozenset(store.delivered_notification_keys(uid))),
        report=ns.ReportInputs(
            has_report=report is not None,
            overall=(_opt_int(report.get("overall")) if report else None),
            blind_spots=_blind_spot_topics(report)),
        # Recommendations: "recommendations waiting" = recs the reader was SURFACED but hasn't opened
        # yet (``RecEvent.opened_at IS NULL``). A pure count over already-recorded reception events —
        # no recommender is invoked, nothing is ranked, and no feed is generated on this path.
        recommendations=ns.RecommendationInputs(
            unopened_count=store.count_unopened_recommendations(uid)),
        reading=reading)


def materialize_notifications(store, uid: int, now: "datetime | None" = None) -> int:
    """``build_context`` → ``evaluate`` → ``record_notifications``. Persists the due notifications for
    a user and returns how many were **newly** materialised (idempotent: re-running with unchanged
    producer state and settings records 0, because the dedupe ledger suppresses re-delivery)."""
    ctx = build_context(store, uid, now)
    due = [dataclasses.asdict(n) for n in ns.evaluate(ctx)]
    return store.record_notifications(uid, due)
