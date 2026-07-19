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
    ``delivered_keys`` is suppressed, so re-evaluating on every fetch never re-emits the same item."""
    delivered_keys: frozenset = frozenset()


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
    """Facts about the reader's reading activity (streak / recency / weekly volume)."""
    streak_days: int = 0
    read_today: bool = False
    reads_this_week: int = 0


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
    kind: str
    setting_path: str
    mode: str                                    # "cadence" | "event" (documentation of the trigger)
    title_key: str
    predicate: "typing.Callable[[NotificationContext], bool]"
    dedupe_key: "typing.Callable[[NotificationContext], str]"
    payload: "typing.Callable[[NotificationContext], dict]"


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
        predicate=lambda c: c.reading.streak_days >= 1 and not c.reading.read_today,
        dedupe_key=lambda c: f"streak_reminder:{c.now:%Y-%m-%d}",
        payload=lambda c: {"streakDays": c.reading.streak_days}),
    NotificationKind(
        kind="blind_spot_alert", setting_path="notifications.blindSpotAlerts", mode="event",
        title_key="notifications.blind_spot_alert.title",
        predicate=lambda c: len(c.report.blind_spots) > 0,
        dedupe_key=lambda c: f"blind_spot_alert:{_blind_spot_sig(c.report.blind_spots)}",
        payload=lambda c: {"blindSpots": list(c.report.blind_spots),
                           "count": len(c.report.blind_spots)}),
)


def evaluate(ctx: NotificationContext) -> "list[Notification]":
    """The due notifications for this context, in registry order. Pure and deterministic: for each
    kind, gate on the reader's setting, run the predicate over the context, and skip anything whose
    dedupe key was already delivered. Reads no producer and no clock — everything comes from ``ctx``."""
    out: "list[Notification]" = []
    for k in NOTIFICATION_KINDS:
        if not _gated(ctx.settings, k.setting_path):
            continue
        if not k.predicate(ctx):
            continue
        key = k.dedupe_key(ctx)
        if key in ctx.delivery.delivered_keys:
            continue
        out.append(Notification(kind=k.kind, dedupe_key=key, created_at=ctx.now.isoformat(),
                                title_key=k.title_key, payload=k.payload(ctx), gated_by=k.setting_path))
    return out
