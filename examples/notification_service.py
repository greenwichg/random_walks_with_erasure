"""notification_service.py — the notification foundation (D0): a pure, dependency-free leaf.

This module is the **delivery boundary's decision core**. It answers one question, deterministically:
*given everything a reader's producers have already computed, which notifications are due right now?*
It computes NOTHING new — no report, no recommendation, no metric — and it reads NO producer directly.
Every fact it needs is handed to it inside a :class:`NotificationContext`, so this file imports only the
standard library and can never introduce an import cycle (mirroring ``settings_service``).

Two orthogonal axes keep the design small:

* **WHAT** is delivered — a :class:`Notification`, one per :data:`NOTIFICATION_KINDS` entry. A report
  becoming available, a digest, a streak nudge, a blind-spot alert: all are notifications.
* **HOW** it is delivered — a :class:`Channel`. In-app now (:class:`InAppChannel`); email / push later
  add a Channel each, never a new kind. Telemetry is a *different* bounded context (system-facing egress,
  privacy-consent-gated) and deliberately lives nowhere near here — it shares only ``settings_service``.

Determinism (D0 contract): :func:`evaluate` is a pure function of the context. Same context in → same
notifications out, in registry order. ``now`` is injected, gating is read from the (already-normalised)
settings carried in the context, and idempotency is enforced against the delivered-key ledger — so no
scheduler, no clock, and no store is involved. This module is imported by no production code (D0).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import typing
from datetime import datetime


# --------------------------------------------------------------------------- #
# Context — grouped producer outputs (never a flat bag). The caller reads the real producers and
# packs their facts into these immutable substructures; this module only decides over them.
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class DeliveryState:
    """What has already been delivered — the idempotency ledger. A kind whose dedupe key is in
    ``delivered_keys`` is suppressed, so re-evaluating on every fetch never re-emits the same item.

    ``counts_today`` is how many notifications of each kind this reader has already received today,
    and it exists for a different reason than ``delivered_keys``: dedupe stops the SAME item arriving
    twice, a daily cap stops a HUNDRED DIFFERENT items arriving at once. Only kinds that fan out (see
    :attr:`NotificationKind.max_per_day`) need it — one event upstream can mean many notifications,
    and nothing else in the ledger bounds that."""
    delivered_keys: frozenset = frozenset()
    counts_today: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass(frozen=True)
class ReportInputs:
    """Facts from the health report / snapshots (read upstream — the REPORT CONTRACT is never touched
    here). ``blind_spots`` are stable identifiers of the report's current blind spots; a *changed* set
    yields a new dedupe key, which is how "a new blind spot appeared" is detected without diffing."""
    has_report: bool = False
    overall: "int | None" = None
    blind_spots: tuple = ()


@dataclasses.dataclass(frozen=True)
class RecommendationInputs:
    """Facts about recommendations the engine has ALREADY produced (never re-ranked here).
    ``unopened_count`` is how many recs the reader was surfaced but has not opened yet."""
    unopened_count: int = 0


@dataclasses.dataclass(frozen=True)
class ReadingInputs:
    """Facts about the reader's reading activity (streak / recency / weekly volume).

    ``streak_days`` is the current streak **ending today** (matches the dashboard). It is >= 1 only if
    the reader read today — which is why the streak-at-risk reminder needs a separate signal:
    ``streak_through_yesterday`` is the run of consecutive days ending **yesterday**, so it is > 0
    exactly when there is an active streak that today's silence would break."""
    streak_days: int = 0
    read_today: bool = False
    reads_this_week: int = 0
    streak_through_yesterday: int = 0


@dataclasses.dataclass(frozen=True)
class EventInputs:
    """**Global** occurrences — the first facts in this context that are not about the reader.

    Every other substructure here answers "what is true of *this* reader": their report, their
    recommendations, their reading. Those are what let the delivery boundary evaluate on the reader's
    own fetch. An event is different in kind: it happened once, to nobody in particular, and many
    readers may be told about it (a breaking story is the first). The boundary reads them from
    ``store.recent_notification_events`` and packs them here; this module still decides nothing but
    what the context contains.

    Each entry is a plain dict as that accessor returns it — ``id``, ``sourceType``, ``sourceId``,
    ``category``, ``payload``, ``occurredAt`` — newest first. Kept as dicts, not a dataclass, for the
    same reason the store persists dicts: this leaf must not grow a schema that a producer has to
    import."""
    events: tuple = ()


@dataclasses.dataclass(frozen=True)
class NotificationContext:
    """The complete, immutable input to :func:`evaluate`. ``settings`` is the reader's *normalised*
    preferences (produced upstream by ``settings_service``); everything else is grouped producer facts."""
    now: datetime
    settings: dict
    delivery: DeliveryState = DeliveryState()
    report: ReportInputs = ReportInputs()
    recommendations: RecommendationInputs = RecommendationInputs()
    reading: ReadingInputs = ReadingInputs()
    events: EventInputs = EventInputs()


# --------------------------------------------------------------------------- #
# Output object + the channel seam.
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class Notification:
    """One due notification. JSON-safe and self-describing: ``kind`` names it, ``dedupe_key`` makes
    delivery idempotent, ``title_key`` is an i18n key (rendering is a channel's job), ``payload`` is
    structured content sourced only from context facts, and ``gated_by`` records the setting that
    enabled it (transparency for a later telemetry/consent view)."""
    kind: str
    dedupe_key: str
    created_at: str
    title_key: str
    payload: dict
    gated_by: str


@typing.runtime_checkable
class Channel(typing.Protocol):
    """A delivery transport for a :class:`Notification`. The WHAT (Notification) is orthogonal to the
    HOW (Channel): in-app now; email / push later implement this same protocol with provider-specific
    rendering — no notification kind changes when a channel is added."""
    name: str

    def render(self, notification: "Notification") -> dict: ...


class InAppChannel:
    """The reference Channel: an in-app notification renders to its own JSON payload, verbatim.
    (Email / push channels are deferred; they implement :class:`Channel` the same way.)"""
    name = "in_app"

    def render(self, notification: "Notification") -> dict:
        return {"kind": notification.kind, "title": notification.title_key,
                "payload": dict(notification.payload), "createdAt": notification.created_at}


# --------------------------------------------------------------------------- #
# Deterministic helpers (pure).
# --------------------------------------------------------------------------- #
def _iso_week(now: datetime) -> str:
    """``YYYY-Www`` for the ISO week containing ``now`` — the weekly-cadence dedupe period."""
    y, w, _ = now.isocalendar()
    return f"{y}-W{w:02d}"


def _blind_spot_sig(blind_spots) -> str:
    """A stable (process-independent) 12-hex digest of the blind-spot SET — so an unchanged set maps to
    the same key (suppressed once delivered) and any change maps to a new one (a fresh alert)."""
    canon = json.dumps(sorted(str(b) for b in blind_spots), separators=(",", ":"))
    return hashlib.sha1(canon.encode("utf-8")).hexdigest()[:12]


def _gated(settings: dict, path: str) -> bool:
    """Read a boolean preference at a dotted ``path`` (e.g. ``notifications.streakReminders``) from the
    normalised settings. Fail-closed: a missing path never delivers."""
    node = settings
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return bool(node)


# --------------------------------------------------------------------------- #
# The registry — one declarative entry per notification kind. Each names the setting that gates it,
# a pure predicate over the context, a dedupe key, and a payload builder. Adding a kind is one row.
# --------------------------------------------------------------------------- #
@dataclasses.dataclass(frozen=True)
class NotificationKind:
    """One declarative kind.

    Two shapes, and a kind is exactly one of them:

    * **single** — ``predicate`` / ``dedupe_key`` / ``payload``. Answers "is this true of the reader
      right now?" and yields at most ONE notification. Every kind shipped before this field existed.
    * **fan-out** — ``fanout`` alone. Yields 0..N notifications from one evaluation, because the
      trigger is a *collection* (several breaking stories) rather than a state. It returns
      ``[(dedupe_key, payload), ...]`` in the order they should be delivered; the three callables
      above are unused and must be left at their defaults.

    ``mode`` documents the LIFECYCLE, which is a different axis from the shape and is consumed by
    ``notification_delivery.materialize_notifications``:

    * ``"cadence"`` — a periodic ARTIFACT ("your weekly report"). Week 30's report stays real after
      week 31, so these accumulate, one per period.
    * ``"event"`` — a STATE alert ("you have recommendations waiting"), true only while its condition
      holds. At most one may be outstanding, and it auto-resolves when the condition clears.
    * ``"discrete"`` — a one-time OCCURRENCE ("this story broke"). It accumulates like a cadence kind
      and is triggered like an event, and it is neither collapsed to one-per-kind nor auto-resolved:
      a story that has stopped breaking still broke, and the reader should still see that it did.
      Getting this wrong is silent — ``"event"`` would erase the alert the moment the condition
      passed, and keep only one row for every story ever.

    ``max_per_day`` bounds how many of this kind one reader may receive in a day. ``None`` means
    unbounded, which is right for anything that can only fire once per period; a fan-out kind should
    always set it, because the number of notifications is decided by the world rather than by us."""

    kind: str
    setting_path: str
    mode: str                                    # "cadence" | "event" | "discrete" (lifecycle)
    title_key: str
    predicate: "typing.Callable[[NotificationContext], bool]" = lambda c: False
    dedupe_key: "typing.Callable[[NotificationContext], str]" = lambda c: ""
    payload: "typing.Callable[[NotificationContext], dict]" = lambda c: {}
    fanout: "typing.Optional[typing.Callable[[NotificationContext], list]]" = None
    max_per_day: "int | None" = None


NOTIFICATION_KINDS = (
    NotificationKind(
        kind="weekly_report", setting_path="weeklyReport", mode="cadence",
        title_key="notifications.weekly_report.title",
        predicate=lambda c: c.report.has_report,
        dedupe_key=lambda c: f"weekly_report:{_iso_week(c.now)}",
        payload=lambda c: {"overall": c.report.overall, "period": _iso_week(c.now)}),
    NotificationKind(
        kind="monthly_deep_dive", setting_path="monthlyReport", mode="cadence",
        title_key="notifications.monthly_deep_dive.title",
        predicate=lambda c: c.report.has_report,
        dedupe_key=lambda c: f"monthly_deep_dive:{c.now:%Y-%m}",
        payload=lambda c: {"overall": c.report.overall, "period": f"{c.now:%Y-%m}"}),
    NotificationKind(
        kind="recommendations_waiting", setting_path="notifications.recommendations", mode="event",
        title_key="notifications.recommendations_waiting.title",
        predicate=lambda c: c.recommendations.unopened_count > 0,
        dedupe_key=lambda c: f"recommendations_waiting:{c.now:%Y-%m-%d}",
        payload=lambda c: {"count": c.recommendations.unopened_count}),
    NotificationKind(
        kind="weekly_digest", setting_path="notifications.weeklyDigest", mode="cadence",
        title_key="notifications.weekly_digest.title",
        predicate=lambda c: c.reading.reads_this_week > 0,
        dedupe_key=lambda c: f"weekly_digest:{_iso_week(c.now)}",
        payload=lambda c: {"reads": c.reading.reads_this_week, "streakDays": c.reading.streak_days,
                           "overall": c.report.overall}),
    NotificationKind(
        kind="streak_reminder", setting_path="notifications.streakReminders", mode="event",
        title_key="notifications.streak_reminder.title",
        predicate=lambda c: c.reading.streak_through_yesterday >= 1 and not c.reading.read_today,
        dedupe_key=lambda c: f"streak_reminder:{c.now:%Y-%m-%d}",
        payload=lambda c: {"streakDays": c.reading.streak_through_yesterday}),
    NotificationKind(
        kind="blind_spot_alert", setting_path="notifications.blindSpotAlerts", mode="event",
        title_key="notifications.blind_spot_alert.title",
        predicate=lambda c: len(c.report.blind_spots) > 0,
        dedupe_key=lambda c: f"blind_spot_alert:{_blind_spot_sig(c.report.blind_spots)}",
        payload=lambda c: {"blindSpots": list(c.report.blind_spots),
                           "count": len(c.report.blind_spots)}),
)


#: Kinds whose ``mode`` is ``"event"`` — STATE alerts ("you have recommendations waiting"), as
#: opposed to ``"cadence"`` kinds, which are periodic ARTIFACTS ("your weekly report is ready").
#: The distinction drives lifecycle: an artifact for week 30 stays true forever, so those rows
#: accumulate legitimately; a state alert is only true while its condition holds, so at most one
#: may be outstanding and it must auto-resolve when the condition clears (see
#: ``notification_delivery.materialize_notifications``).
EVENT_KINDS: tuple = tuple(k.kind for k in NOTIFICATION_KINDS if k.mode == "event")

#: Kinds whose ``mode`` is ``"discrete"`` — one-time OCCURRENCES. They accumulate like ``cadence``
#: kinds and are triggered like ``event`` kinds, and the delivery boundary must apply NEITHER of the
#: two treatments it gives state alerts: no auto-resolve (a story that stopped breaking still broke)
#: and no one-outstanding-per-kind collapse (one row per story is the entire point). Exported so
#: ``notification_delivery`` can exclude them explicitly rather than by asking "is it an event?".
DISCRETE_KINDS: tuple = tuple(k.kind for k in NOTIFICATION_KINDS if k.mode == "discrete")


def inactive_event_kinds(ctx: NotificationContext) -> tuple:
    """Event-mode kinds whose triggering condition is **not** currently true for this reader (the
    predicate is false, or the reader disabled the setting). Pure, like :func:`evaluate` — the
    delivery boundary uses it to resolve alerts that no longer describe reality, so the inbox and
    its unread badge track *actionable* state instead of history."""
    return tuple(k.kind for k in NOTIFICATION_KINDS
                 if k.mode == "event"                 # NOT "discrete": an occurrence never resolves
                 and (not _gated(ctx.settings, k.setting_path) or not k.predicate(ctx)))


def _due_pairs(k: NotificationKind, ctx: NotificationContext) -> "list[tuple]":
    """The ``(dedupe_key, payload)`` pairs a kind wants to deliver, before dedupe and the cap.

    The single/fan-out split lives here and nowhere else, so :func:`evaluate` reads the same for both
    shapes and the six kinds that predate fan-out take a byte-identical path."""
    if k.fanout is not None:
        return [(str(key), dict(payload)) for key, payload in k.fanout(ctx)]
    if not k.predicate(ctx):
        return []
    return [(k.dedupe_key(ctx), k.payload(ctx))]


def evaluate(ctx: NotificationContext) -> "list[Notification]":
    """The due notifications for this context, in registry order. Pure and deterministic: for each
    kind, gate on the reader's setting, ask the kind what it wants to deliver, and skip anything whose
    dedupe key was already delivered. Reads no producer and no clock — everything comes from ``ctx``.

    A kind may yield more than one notification (see :attr:`NotificationKind.fanout`), so two further
    rules apply, both of which exist because a fan-out kind's volume is decided by the world:

    * ``max_per_day`` bounds the total this reader receives of that kind today, counting what the
      ledger says they already got. Truncation keeps the FRONT of the list — a fan-out returns its
      items in delivery order, so the reader keeps the first ones rather than an arbitrary slice.
    * dedupe is applied per item, and duplicates *within one fan-out* are collapsed too, so a producer
      that emits the same key twice cannot spend the cap on one notification."""
    out: "list[Notification]" = []
    for k in NOTIFICATION_KINDS:
        if not _gated(ctx.settings, k.setting_path):
            continue
        budget = None
        if k.max_per_day is not None:
            budget = max(0, k.max_per_day - int(ctx.delivery.counts_today.get(k.kind, 0)))
            if budget == 0:
                continue
        emitted: set = set()
        for key, payload in _due_pairs(k, ctx):
            if key in ctx.delivery.delivered_keys or key in emitted:
                continue
            emitted.add(key)
            out.append(Notification(kind=k.kind, dedupe_key=key, created_at=ctx.now.isoformat(),
                                    title_key=k.title_key, payload=payload, gated_by=k.setting_path))
            if budget is not None and len(emitted) >= budget:
                break
    return out
