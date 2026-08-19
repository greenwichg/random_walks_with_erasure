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
import os
from datetime import datetime, timedelta, timezone

import notification_service as ns
import settings_service


def _recs_window_days() -> int:
    """How far back a still-unopened recommendation counts as *waiting* (default 7 days). Bounds the
    alert to the live feed; 0/junk falls back to the default rather than silently going unbounded."""
    raw = os.environ.get("RWE_NOTIFY_RECS_WINDOW_DAYS", "")
    return int(raw) if raw.strip().lstrip("-").isdigit() and int(raw) > 0 else 7


def _events_window_hours() -> int:
    """How far back to look for global events (default 24h). Each event also carries its own
    ``expires_at`` — the real staleness cutoff, set per category by whoever produced it — so this is
    only a bound on the query, not the policy. 0/junk falls back rather than going unbounded."""
    raw = os.environ.get("RWE_NOTIFY_EVENTS_WINDOW_HOURS", "")
    return int(raw) if raw.strip().lstrip("-").isdigit() and int(raw) > 0 else 24


def _recent_events(store, now) -> tuple:
    """Global events for the context, newest first — or ``()`` if they cannot be read.

    Fail-soft on purpose, and this is the one read here that is allowed to fail quietly: events feed
    a *supplementary* kind, while the rest of the context feeds the reader's own report, streak and
    recommendations. A broken event read must cost the reader their breaking notifications, never
    their whole inbox."""
    try:
        since = (now - timedelta(hours=_events_window_hours())).isoformat()
        return tuple(store.recent_notification_events(since=since, now=now.isoformat(), limit=50))
    except Exception:                    # noqa: BLE001 — see the docstring; degradation is the point
        return ()


def _capped_kinds() -> tuple:
    """Kinds that declare a per-day cap — the only ones whose counts need reading."""
    return tuple(k.kind for k in ns.NOTIFICATION_KINDS if k.max_per_day is not None)


def _counts_today(store, uid: int, now) -> dict:
    """Today's per-kind delivery counts for the capped kinds. Same fail-soft posture as
    :func:`_recent_events`: without counts a cap would read as "nothing sent yet", so a failure here
    must not silently *raise* the ceiling — it returns the ceiling itself, closing the cap."""
    kinds = _capped_kinds()
    if not kinds:
        return {}
    try:
        return store.notification_counts_today(uid, list(kinds), day=now.date().isoformat())
    except Exception:                    # noqa: BLE001 — fail CLOSED: assume the cap is spent
        return {k.kind: k.max_per_day for k in ns.NOTIFICATION_KINDS if k.max_per_day is not None}


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


def _streak_through_yesterday(read_ats, now, time_zone=None) -> int:
    """The run of consecutive days with >= 1 read ending **yesterday** — an "active streak at risk"
    signal that, unlike ``api_server._reading_streak`` (which ends *today* and so drops to 0 the
    moment today has no read), survives today's silence. Same day bucketing as the streak
    definition (``api_server._local_days`` — the reader's zone when known, UTC otherwise), but
    anchored at yesterday and driven by the injected ``now`` (so it is deterministic).
    ``_reading_streak`` is neither called nor modified.

    "Yesterday" is yesterday WHERE THE READER IS: anchoring at a UTC yesterday while counting local
    days would mis-time the one notification this signal exists to send."""
    import api_server as engine
    days = engine._local_days(read_ats, time_zone)
    if not days:
        return 0
    local_now = now.astimezone(engine._zone(time_zone)) if time_zone else now
    streak, d = 0, local_now.date() - timedelta(days=1)        # anchor at yesterday, count backwards
    while d.isoformat() in days:
        streak += 1
        d = d - timedelta(days=1)
    return streak


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
    # Read once and reused: the same settings object the context carries also names the reader's
    # zone, so a streak reminder is timed against THEIR midnight rather than UTC's.
    settings = settings_service.get(store, uid)
    tz = settings.get("timeZone")
    today = (now.astimezone(engine._zone(tz)) if tz else now).date().isoformat()
    week_ago = now - timedelta(days=7)

    def _within_week(ra) -> bool:
        dt = _parse_iso(ra)
        return dt is not None and dt >= week_ago

    reading = ns.ReadingInputs(
        streak_days=engine._reading_streak(read_ats, tz),      # current streak ending today, local
        read_today=any(engine._local_day(ra, tz) == today for ra in read_ats),
        reads_this_week=sum(1 for ra in read_ats if _within_week(ra)),
        streak_through_yesterday=_streak_through_yesterday(read_ats, now, tz))

    return ns.NotificationContext(
        now=now,
        settings=settings,
        delivery=ns.DeliveryState(
            delivered_keys=frozenset(store.delivered_notification_keys(uid)),
            counts_today=_counts_today(store, uid, now)),
        report=ns.ReportInputs(
            has_report=report is not None,
            overall=(_opt_int(report.get("overall")) if report else None),
            blind_spots=_blind_spot_topics(report)),
        # Recommendations: "recommendations waiting" = recs the reader was SURFACED but hasn't opened
        # yet (``RecEvent.opened_at IS NULL``). A pure count over already-recorded reception events —
        # no recommender is invoked, nothing is ranked, and no feed is generated on this path.
        # WINDOWED to the recent feed (RWE_NOTIFY_RECS_WINDOW_DAYS, default 7): an unopened card from
        # months ago is not something the reader can act on today — the current feed no longer offers
        # it — so counting it would inflate the alert with history that has no live counterpart.
        recommendations=ns.RecommendationInputs(
            unopened_count=store.count_unopened_recommendations(
                uid, since=(now - timedelta(days=_recs_window_days())).isoformat())),
        reading=reading,
        # GLOBAL occurrences — the only part of this context that is not about this reader. Read
        # rather than pushed: the same evaluate-on-fetch shape as everything else here, so a breaking
        # story reaches a reader on their next request with no scheduler and no queue.
        events=ns.EventInputs(events=_recent_events(store, now)))


def materialize_notifications(store, uid: int, now: "datetime | None" = None) -> int:
    """``build_context`` → ``evaluate`` → **reconcile state alerts** → ``record_notifications``.
    Persists the due notifications for a user and returns how many were **newly** materialised
    (idempotent: re-running with unchanged producer state and settings records 0, because the
    dedupe ledger suppresses re-delivery).

    The reconcile step is what makes the inbox — and the unread badge over it — describe what is
    ACTIONABLE NOW rather than everything that was ever true. Two kinds of notification behave
    differently, exactly as ``NotificationKind.mode`` already documents:

    * ``cadence`` (weekly report, monthly deep dive, weekly digest) — periodic ARTIFACTS. Week 30's
      report stays a real thing after week 31 arrives, so these accumulate, one per period.
    * ``event`` (recommendations waiting, streak reminder, blind-spot alert) — STATE alerts, true
      only while their condition holds. Here we (a) auto-resolve unseen alerts whose condition has
      cleared, and (b) keep at most ONE outstanding alert per kind, refreshing its payload in place
      instead of minting a new row on each evaluation period.
    * ``discrete`` (breaking stories) — one-time OCCURRENCES. They get **neither** treatment, and
      that is load-bearing rather than an omission: (a) would erase a breaking alert the moment the
      story stopped breaking, when the reader should still see that it broke, and (b) would keep one
      row for every story ever, when one row per story is the entire point.

    Both exclusions hold by construction rather than by a check here — ``inactive_event_kinds``
    filters on ``mode == "event"`` and (b) tests membership of ``EVENT_KINDS``, and a discrete kind
    is in neither. Adding ``DISCRETE_KINDS`` to either branch is the mistake to avoid; the tests in
    ``test_notification_delivery.py`` assert both directions.

    Without (a) the badge kept describing a state the reader had already resolved; without (b) an
    inactive reader accumulated one row per day per kind forever.
    """
    ctx = build_context(store, uid, now)
    stamp = ctx.now.isoformat()

    # (a) Conditions that no longer hold: resolve their outstanding alerts.
    for kind in ns.inactive_event_kinds(ctx):
        store.resolve_notifications(uid, kind, at=stamp)

    # (b) Still-true alerts: refresh the outstanding row instead of adding another.
    due = []
    for n in ns.evaluate(ctx):
        body = dataclasses.asdict(n)
        if n.kind in ns.EVENT_KINDS:
            outstanding = store.unseen_notification(uid, n.kind)
            if outstanding is not None:
                store.refresh_notification(uid, outstanding["id"], body,
                                           dedupe_key=n.dedupe_key)
                continue
        due.append(body)

    created = store.record_notifications(uid, due)
    store.prune_notifications(uid)          # bound settled history (unseen rows are never pruned)
    return created
