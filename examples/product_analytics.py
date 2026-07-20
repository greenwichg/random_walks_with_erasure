"""PA1 — product-analytics computation (pure, deterministic, dependency-free leaf).

This module owns the **event taxonomy** and the **funnel / product-metric / retention maths**. It
imports nothing from the app: give it a list of event rows (plain dicts) and it returns the funnel,
the metrics, retention, and event counts — the same rows always produce the same numbers. The API
layer does the I/O (read rows from the store, gate the endpoint); the arithmetic lives here, the way
``obs_metrics`` and ``recommendation_eval`` keep their computation out of the request path.

An event row (as produced by :meth:`store.Store.list_analytics_events`) looks like::

    {"event": "health_report_viewed", "userId": 7, "anonId": "a1b2…", "sessionId": "s9…",
     "props": {"mode": "measured", "coverage": 0.8}, "clientTs": "…", "serverTs": "2026-07-20T…"}

Identity model (see docs/PA1_PRODUCT_ANALYTICS.md): a signed-in event is attributed to ``u:<userId>``;
an anonymous event to ``a:<anonId>``. ``login_success`` / ``account_created`` events that carry *both*
an anonId and a resolved userId stitch the two, so a person's pre-auth and post-auth events fold into
one identity for the funnel.
"""
from __future__ import annotations

import statistics
from datetime import datetime
from typing import Iterable

# --------------------------------------------------------------------------- #
# Taxonomy — the allow-list. The sink drops any event whose name is not here, so the table's
# cardinality stays bounded and every stored row is a known, documented event.
# --------------------------------------------------------------------------- #
EVENTS: "frozenset[str]" = frozenset({
    "app_opened", "page_viewed",
    "onboarding_started", "onboarding_step_completed", "source_connected",
    "signin_started", "account_created", "login_success",
    "article_read",
    "health_report_viewed",
    "recommendations_viewed", "recommendation_opened", "recommendation_feedback",
})

# Per-event allow-listed properties (scalars only; everything else is dropped). Keeping this explicit
# is what makes the store pseudonymous by construction — no free-form blob, no PII can leak in.
PROPS: "dict[str, tuple[str, ...]]" = {
    "app_opened": ("path", "referrer"),
    "page_viewed": ("path",),
    "onboarding_started": ("step",),
    "onboarding_step_completed": ("step", "stepIndex"),
    "source_connected": ("outletCount",),
    "signin_started": ("method",),
    "account_created": ("method",),
    "login_success": ("method",),
    "article_read": ("source", "isFirst"),
    "health_report_viewed": ("mode", "coverage"),
    "recommendations_viewed": ("count",),
    "recommendation_opened": ("strategy", "crossCutting"),
    "recommendation_feedback": ("action",),
}

_MAX_STR = 200          # property string values are truncated to this
_POSITIVE_FEEDBACK = frozenset({"like", "read_later"})


# --------------------------------------------------------------------------- #
# Normalization — used by the /api/events sink to clean a single inbound event.
# --------------------------------------------------------------------------- #
def _scalar(v: object) -> object:
    """Keep only JSON scalars; truncate strings. Anything else (dict/list/None-of-note) is dropped."""
    if isinstance(v, bool) or isinstance(v, int) or isinstance(v, float):
        return v
    if isinstance(v, str):
        return v[:_MAX_STR]
    return None


def sanitize_props(event: str, raw: object) -> dict:
    """Return the allow-listed, scalar-only properties for ``event`` (empty if none/invalid)."""
    if not isinstance(raw, dict):
        return {}
    allowed = PROPS.get(event, ())
    out: dict = {}
    for key in allowed:
        if key in raw:
            val = _scalar(raw[key])
            if val is not None:
                out[key] = val
    return out


def normalize(raw: object) -> "dict | None":
    """Validate & clean one inbound client event into the fields the store persists, or ``None`` if the
    event name is unknown/invalid (the sink drops it). Identity + timestamps that must be authoritative
    (``user_id``, ``server_ts``, ``request_id``) are stamped by the caller, not taken from the client."""
    if not isinstance(raw, dict):
        return None
    event = raw.get("event")
    if not isinstance(event, str) or event not in EVENTS:
        return None

    def _short(v: object, n: int = 128) -> "str | None":
        return v[:n] if isinstance(v, str) and v else None

    return {
        "event": event,
        "anon_id": _short(raw.get("anonId"), 64),
        "session_id": _short(raw.get("sessionId"), 64),
        "client_ts": _short(raw.get("clientTs"), 64),
        "props": sanitize_props(event, raw.get("props")),
    }


# --------------------------------------------------------------------------- #
# Identity + timestamp helpers
# --------------------------------------------------------------------------- #
def build_stitch(rows: Iterable[dict]) -> "dict[str, int]":
    """Map ``anonId -> userId`` from stitch events (login / account-created rows carrying both). Earliest
    binding wins so the mapping is stable and order-independent."""
    stitch: dict[str, int] = {}
    best_ts: dict[str, str] = {}
    for r in rows:
        if r.get("event") not in ("login_success", "account_created"):
            continue
        anon, uid, ts = r.get("anonId"), r.get("userId"), r.get("serverTs") or ""
        if anon and isinstance(uid, int):
            if anon not in best_ts or ts < best_ts[anon]:
                stitch[anon] = uid
                best_ts[anon] = ts
    return stitch


def identity(row: dict, stitch: "dict[str, int]") -> "str | None":
    """The stable identity a row belongs to: a real user (``u:<id>``) when signed in or stitched from the
    row's anonId, else the anonymous browser (``a:<anonId>``); ``None`` when a row has neither."""
    uid = row.get("userId")
    if isinstance(uid, int):
        return f"u:{uid}"
    anon = row.get("anonId")
    if anon:
        mapped = stitch.get(anon)
        return f"u:{mapped}" if mapped is not None else f"a:{anon}"
    return None


def _parse(ts: object) -> "datetime | None":
    if not isinstance(ts, str) or not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _day(ts: object) -> "str | None":
    """The calendar day (YYYY-MM-DD) of an ISO timestamp — ISO strings sort/slice correctly."""
    return ts[:10] if isinstance(ts, str) and len(ts) >= 10 else None


# --------------------------------------------------------------------------- #
# Per-identity aggregation — one pass the funnel, metrics, and retention all read from.
# --------------------------------------------------------------------------- #
def _aggregate(rows: Iterable[dict], stitch: "dict[str, int]") -> "dict[str, dict]":
    agg: dict[str, dict] = {}
    for r in rows:
        who = identity(r, stitch)
        if who is None:
            continue
        ev = r.get("event")
        ts = r.get("serverTs") or r.get("clientTs") or ""
        a = agg.get(who)
        if a is None:
            a = agg[who] = {"events": set(), "first": {}, "app_days": set(),
                            "measured": False, "recs_viewed": False,
                            "rec_engaged": False, "rec_accepted": False}
        a["events"].add(ev)
        # earliest timestamp per event (ISO sorts lexicographically)
        cur = a["first"].get(ev)
        if cur is None or (ts and ts < cur):
            a["first"][ev] = ts
        props = r.get("props") or {}
        if ev == "app_opened":
            d = _day(ts)
            if d:
                a["app_days"].add(d)
        elif ev == "health_report_viewed" and props.get("mode") == "measured":
            a["measured"] = True
            fm = a["first"].get("_measured")
            if fm is None or (ts and ts < fm):
                a["first"]["_measured"] = ts
        elif ev == "recommendations_viewed":
            a["recs_viewed"] = True
        elif ev == "recommendation_opened":
            a["rec_engaged"] = True
            a["rec_accepted"] = True
        elif ev == "recommendation_feedback":
            a["rec_engaged"] = True
            if props.get("action") in _POSITIVE_FEEDBACK:
                a["rec_accepted"] = True
    return agg


# --------------------------------------------------------------------------- #
# Funnel (Phase 4)
# --------------------------------------------------------------------------- #
#: (key, human label, predicate over a per-identity aggregate) — the ten activation stages.
_STAGES: "list[tuple[str, str]]" = [
    ("app_opened", "App Opened"),
    ("account_created", "Account Created"),
    ("login_success", "Login Success"),
    ("source_connected", "Source Connected"),
    ("article_read", "First Article Read"),
    ("health_report_viewed", "Health Report Generated"),
    ("measured_report", "Measured Report"),
    ("recommendations_viewed", "Recommendation Viewed"),
    ("recommendation_accepted", "Recommendation Accepted"),
    ("returned_next_day", "Returned Next Day"),
]


def _reached(stage: str, a: dict) -> bool:
    if stage == "measured_report":
        return a["measured"]
    if stage == "recommendation_accepted":
        return a["rec_accepted"]
    if stage == "returned_next_day":
        return len(a["app_days"]) >= 2
    return stage in a["events"]


def _round(x: "float | None", n: int = 4) -> "float | None":
    return None if x is None else round(x, n)


def funnel(rows: Iterable[dict]) -> dict:
    """The ten-stage activation funnel: per-stage reachers, stage-to-stage conversion, conversion from
    the top, and the largest single drop (the top drop-off point). Deterministic."""
    rows = list(rows)
    stitch = build_stitch(rows)
    agg = _aggregate(rows, stitch)

    counts = {key: sum(1 for a in agg.values() if _reached(key, a)) for key, _ in _STAGES}
    top = counts[_STAGES[0][0]]
    stages = []
    prev = None
    for key, label in _STAGES:
        c = counts[key]
        conv_prev = None if prev in (None, 0) else _round(c / prev)
        conv_top = None if top == 0 else _round(c / top)
        stages.append({"key": key, "label": label, "reachers": c,
                       "conversionFromPrev": conv_prev, "conversionFromStart": conv_top})
        prev = c
    return {"stages": stages, "totalIdentities": len(agg), "topDropOff": _topdropoff(stages)}


def _topdropoff(stages: "list[dict]") -> "dict | None":
    """The consecutive transition with the largest relative loss."""
    worst = None
    for i in range(1, len(stages)):
        prev, cur = stages[i - 1], stages[i]
        if not prev["reachers"]:
            continue
        drop = 1.0 - cur["reachers"] / prev["reachers"]
        if worst is None or drop > worst["dropPct"]:
            worst = {"fromStage": prev["key"], "toStage": cur["key"],
                     "fromReachers": prev["reachers"], "toReachers": cur["reachers"],
                     "dropPct": _round(drop)}
    return worst


# --------------------------------------------------------------------------- #
# Product metrics (Phase 5)
# --------------------------------------------------------------------------- #
def _median_seconds(agg: "dict[str, dict]", start_key: str, end_key: str) -> "float | None":
    deltas = []
    for a in agg.values():
        t0, t1 = _parse(a["first"].get(start_key)), _parse(a["first"].get(end_key))
        if t0 and t1 and t1 >= t0:
            deltas.append((t1 - t0).total_seconds())
    return _round(statistics.median(deltas), 1) if deltas else None


def _rate(num: int, den: int) -> "float | None":
    return None if den == 0 else _round(num / den)


def product_metrics(rows: Iterable[dict]) -> dict:
    """Activation, time-to-value, and recommendation-engagement metrics — all derived from the same
    stitched identities the funnel uses."""
    rows = list(rows)
    stitch = build_stitch(rows)
    agg = _aggregate(rows, stitch)

    accounts = sum(1 for a in agg.values() if "account_created" in a["events"])
    reports = sum(1 for a in agg.values() if "health_report_viewed" in a["events"])
    measured = sum(1 for a in agg.values() if a["measured"])
    recs_viewed = sum(1 for a in agg.values() if a["recs_viewed"])
    engaged = sum(1 for a in agg.values() if a["rec_engaged"])
    accepted = sum(1 for a in agg.values() if a["rec_accepted"])

    ret = retention(rows)
    return {
        "identities": len(agg),
        "accountsCreated": accounts,
        "activationRate": _rate(reports, accounts),
        "measuredActivationRate": _rate(measured, accounts),
        "timeToFirstReportSeconds": _median_seconds(agg, "account_created", "health_report_viewed"),
        "timeToMeasuredModeSeconds": _median_seconds(agg, "account_created", "_measured"),
        "recommendationEngagementRate": _rate(engaged, recs_viewed),
        "recommendationAcceptanceRate": _rate(accepted, recs_viewed),
        "day1Retention": ret["day1"]["rate"],
        "day7Retention": ret["day7"]["rate"],
    }


# --------------------------------------------------------------------------- #
# Retention (Phase 5) — cohort by first-seen day; D1 / D7 (D7 is future-ready).
# --------------------------------------------------------------------------- #
def _retained_after(app_days: "set[str]", offset_days: int) -> bool:
    if not app_days:
        return False
    first = min(app_days)
    try:
        base = datetime.fromisoformat(first)
    except ValueError:
        return False
    from datetime import timedelta
    target = (base + timedelta(days=offset_days)).date().isoformat()
    return target in app_days


def retention(rows: Iterable[dict]) -> dict:
    """Day-1 and Day-7 retention: of the identities that ever opened the app, the share that opened it
    again exactly N days after their first-seen day. D7 is present and future-ready (≈0 in a short beta)."""
    rows = list(rows)
    stitch = build_stitch(rows)
    agg = _aggregate(rows, stitch)
    cohort = [a for a in agg.values() if a["app_days"]]
    n = len(cohort)
    d1 = sum(1 for a in cohort if _retained_after(a["app_days"], 1))
    d7 = sum(1 for a in cohort if _retained_after(a["app_days"], 7))
    return {
        "cohort": n,
        "day1": {"retained": d1, "rate": _rate(d1, n)},
        "day7": {"retained": d7, "rate": _rate(d7, n)},
    }


# --------------------------------------------------------------------------- #
# Event counts (Phase 6 dashboard)
# --------------------------------------------------------------------------- #
def event_counts(rows: Iterable[dict]) -> dict:
    """Total events and a per-event-name breakdown (taxonomy order, only names that occurred)."""
    counts: dict[str, int] = {}
    total = 0
    for r in rows:
        ev = r.get("event")
        if ev in EVENTS:
            counts[ev] = counts.get(ev, 0) + 1
            total += 1
    return {"total": total, "byEvent": counts}
