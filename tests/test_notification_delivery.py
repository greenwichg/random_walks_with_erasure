"""Tests for examples/notification_delivery.py — the N2 orchestration seam (deterministic).

Covers building a NotificationContext from persisted producer state, blind-spot **topic** stability
(the dedupe key must not depend on volatile gap/note fields), idempotent materialisation, settings
gating, and — critically — that materialisation generates NOTHING (no report snapshot, no rec event).
Also pins the two kinds that are intentionally inert in production (see the N2 report): the
recommendation kind (no canonical "new" definition) and the streak kind (predicate vs `_reading_streak`).
"""

import pathlib
import sys
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import notification_delivery as nd   # noqa: E402
import store as store_mod            # noqa: E402


def _store_user(acct="nd-1"):
    st = store_mod.Store("sqlite://")
    return st, st.upsert_user_by_identity("dev", acct).id


def _all_on(st, uid):
    st.save_settings(uid, {"weeklyReport": True, "monthlyReport": True,
                           "notifications": {"recommendations": True, "weeklyDigest": True,
                                             "streakReminders": True, "blindSpotAlerts": True}})


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def _read(st, uid, url, days_ago):
    st.add_read(uid, url, {"article_id": url, "outlet": "AP", "category": "Politics", "lean": 0.0,
                           "political": True, "title": "t", "read_at": _iso(days_ago)})


def _report(st, uid, overall=72, topics=("Economy",)):
    st.save_report(uid, {"mode": "measured", "overall": overall,
                         "blindSpots": [{"topic": t, "gap": 0.4, "note": f"{t} note"} for t in topics]})


def _kinds(st, uid):
    return {x["kind"] for x in st.list_notifications(uid, limit=100)}


# --------------------------------------------------------------------------- #
# build_context maps persisted producers (and nothing else).
# --------------------------------------------------------------------------- #
def test_build_context_empty_user():
    st, uid = _store_user()
    ctx = nd.build_context(st, uid)
    assert ctx.report.has_report is False and ctx.report.overall is None and ctx.report.blind_spots == ()
    assert ctx.reading.streak_days == 0 and ctx.reading.read_today is False
    assert ctx.reading.reads_this_week == 0 and ctx.reading.streak_through_yesterday == 0
    assert ctx.recommendations.unopened_count == 0
    assert isinstance(ctx.settings, dict) and ctx.delivery.delivered_keys == frozenset()


def test_build_context_uses_stable_blind_spot_topics_only():
    st, uid = _store_user()
    _report(st, uid, overall=72, topics=("Economy", "Science"))
    ctx = nd.build_context(st, uid)
    assert ctx.report.has_report and ctx.report.overall == 72
    assert set(ctx.report.blind_spots) == {"Economy", "Science"}
    assert all(isinstance(b, str) for b in ctx.report.blind_spots)   # topics, never serialized dicts


def test_build_context_reading_signals():
    st, uid = _store_user()
    _read(st, uid, "u-today", 0)
    _read(st, uid, "u-3d", 3)
    _read(st, uid, "u-10d", 10)
    ctx = nd.build_context(st, uid)
    assert ctx.reading.read_today is True
    assert ctx.reading.streak_days >= 1                 # a read today -> current streak >= 1
    assert ctx.reading.reads_this_week == 2             # today + 3d within the window; 10d excluded
    assert ctx.reading.streak_through_yesterday == 0    # nothing read yesterday -> not at risk


# --------------------------------------------------------------------------- #
# Blind-spot dedupe stability — the key correctness property behind N1's persisted key.
# --------------------------------------------------------------------------- #
def test_blind_spot_dedupe_is_topic_stable():
    st, uid = _store_user(); _all_on(st, uid)
    _report(st, uid, topics=("Economy",))
    assert nd.materialize_notifications(st, uid) >= 1
    # same TOPIC, different gap/note -> same dedupe key -> nothing new re-fires
    st.save_report(uid, {"mode": "measured", "overall": 50,
                         "blindSpots": [{"topic": "Economy", "gap": 0.99, "note": "totally different"}]})
    assert nd.materialize_notifications(st, uid) == 0
    # A genuinely NEW topic set is a new dedupe key — but blind_spot_alert is an EVENT (state)
    # kind, so the reader's ONE outstanding alert is refreshed in place rather than a second one
    # being stacked beside it. "You have blind spots" is one actionable thing, not two.
    st.save_report(uid, {"mode": "measured", "overall": 50,
                         "blindSpots": [{"topic": "Economy"}, {"topic": "Climate"}]})
    assert nd.materialize_notifications(st, uid) == 0    # refreshed, not accumulated
    alerts = [x for x in st.list_notifications(uid, limit=100) if x["kind"] == "blind_spot_alert"]
    assert len(alerts) == 1                              # exactly ONE outstanding alert
    assert set(alerts[0]["payload"]["blindSpots"]) == {"Economy", "Climate"}   # payload is current
    # Once dismissed, the SAME state must not immediately re-fire (the refreshed dedupe key was
    # recorded), but a later, different gap set legitimately raises a fresh alert.
    st.mark_notification_seen(uid, alerts[0]["id"])
    assert nd.materialize_notifications(st, uid) == 0
    st.save_report(uid, {"mode": "measured", "overall": 50, "blindSpots": [{"topic": "Health"}]})
    assert nd.materialize_notifications(st, uid) == 1


# --------------------------------------------------------------------------- #
# Materialisation: idempotent, gated, and side-effect-free.
# --------------------------------------------------------------------------- #
def test_materialize_is_idempotent():
    st, uid = _store_user(); _all_on(st, uid)
    _report(st, uid); _read(st, uid, "u", 0)
    assert nd.materialize_notifications(st, uid) >= 1
    assert nd.materialize_notifications(st, uid) == 0


def test_settings_gate_suppresses_only_that_kind():
    st, uid = _store_user()
    st.save_settings(uid, {"weeklyReport": False, "monthlyReport": True,
                           "notifications": {"recommendations": True, "weeklyDigest": True,
                                             "streakReminders": True, "blindSpotAlerts": True}})
    _report(st, uid); _read(st, uid, "u", 0)
    nd.materialize_notifications(st, uid)
    kinds = _kinds(st, uid)
    assert "weekly_report" not in kinds and "monthly_deep_dive" in kinds


def test_materialize_generates_no_reports_or_rec_events():
    """The invariant that matters: delivery reads persisted state and writes ONLY notifications."""
    st, uid = _store_user(); _all_on(st, uid)
    _report(st, uid); _read(st, uid, "u", 0)
    reports_before = len(st.list_report_snapshots(uid, limit=1000))
    recs_before = len(st.list_rec_events(uid))
    nd.materialize_notifications(st, uid)
    assert len(st.list_report_snapshots(uid, limit=1000)) == reports_before   # no new report snapshot
    assert len(st.list_rec_events(uid)) == recs_before                         # no new rec events


# --------------------------------------------------------------------------- #
# Intentionally-inert kinds (pinned + reported, not silently broken).
# --------------------------------------------------------------------------- #
def test_recommendations_waiting_fires_on_unopened_recs():
    """recommendations_waiting = recs surfaced but not opened (``RecEvent.opened_at IS NULL``) — a
    pure reception count, no recommender. It fires when unopened recs exist and clears when opened."""
    st, uid = _store_user(); _all_on(st, uid)
    # no rec events yet -> unopened_count 0 -> kind does not fire
    nd.materialize_notifications(st, uid)
    assert nd.build_context(st, uid).recommendations.unopened_count == 0
    assert "recommendations_waiting" not in _kinds(st, uid)
    # surface two recs (unopened) -> the kind now fires, with a truthful count
    st.record_recommendations_shown(uid, [("a1", False), ("a2", True)])
    assert nd.build_context(st, uid).recommendations.unopened_count == 2
    assert nd.materialize_notifications(st, uid) >= 1
    waiting = next(x for x in st.list_notifications(uid, limit=100)
                   if x["kind"] == "recommendations_waiting")
    assert waiting["payload"]["count"] == 2
    # opening them drops the unopened count back to 0 (self-quiescing)
    st.record_recommendation_open(uid, "a1")
    st.record_recommendation_open(uid, "a2")
    assert nd.build_context(st, uid).recommendations.unopened_count == 0


def test_build_context_populates_both_streak_signals():
    """streak_days = the current streak ending today (unchanged); streak_through_yesterday = the run
    ending yesterday (the at-risk signal). They differ exactly when the reader read yesterday but not
    today."""
    st, uid = _store_user()
    _read(st, uid, "u-y", 1)                             # yesterday
    _read(st, uid, "u-2d", 2)                            # the day before
    ctx = nd.build_context(st, uid)
    assert ctx.reading.read_today is False
    assert ctx.reading.streak_days == 0                 # ending-today streak: nothing read today
    assert ctx.reading.streak_through_yesterday == 2    # yesterday + the day before


def test_streak_reminder_fires_when_streak_at_risk():
    """Active streak through yesterday + nothing read today -> the reminder fires; the payload carries
    the at-risk length."""
    st, uid = _store_user(); _all_on(st, uid)
    _read(st, uid, "u-y", 1)                             # read yesterday, not today
    nd.materialize_notifications(st, uid)
    assert "streak_reminder" in _kinds(st, uid)
    sr = next(x for x in st.list_notifications(uid, limit=100) if x["kind"] == "streak_reminder")
    assert sr["payload"]["streakDays"] == 1


def test_streak_reminder_suppressed_when_read_today():
    """Read yesterday AND today -> the streak is safe -> no reminder."""
    st, uid = _store_user(); _all_on(st, uid)
    _read(st, uid, "u-y", 1); _read(st, uid, "u-today", 0)
    nd.materialize_notifications(st, uid)
    assert "streak_reminder" not in _kinds(st, uid)


def test_streak_reminder_no_prior_streak():
    """A read only 3 days ago -> nothing read yesterday -> no at-risk streak -> no reminder."""
    st, uid = _store_user(); _all_on(st, uid)
    _read(st, uid, "u-3d", 3)
    nd.materialize_notifications(st, uid)
    assert "streak_reminder" not in _kinds(st, uid)


def test_weekly_digest_streak_days_unchanged():
    """The digest keeps reporting streak_days (ending today) — NOT streak_through_yesterday."""
    st, uid = _store_user(); _all_on(st, uid)
    _read(st, uid, "u-today", 0); _read(st, uid, "u-y", 1)   # read today + yesterday -> streak_days == 2
    nd.materialize_notifications(st, uid)
    digest = next(x for x in st.list_notifications(uid, limit=100) if x["kind"] == "weekly_digest")
    assert digest["payload"]["streakDays"] == 2
    assert digest["payload"]["streakDays"] == nd.build_context(st, uid).reading.streak_days


# --------------------------------------------------------------------------- #
# Badge semantics (N-badge): the unread count must describe what is ACTIONABLE NOW, never the
# cumulative history of everything that was ever true, and it must not grow without bound.
# --------------------------------------------------------------------------- #
def _unseen(st, uid, kind=None):
    rows = st.list_notifications(uid, unseen_only=True, limit=200)
    return [r for r in rows if kind is None or r["kind"] == kind]


def _surface_recs(st, uid, n, days_ago=0, tag="a"):
    st.record_recommendations_shown(uid, [(f"https://x.example/{tag}-{days_ago}-{i}", False)
                                          for i in range(n)], shown_at=_iso(days_ago))


def test_recommendations_waiting_does_not_accumulate_daily():
    """The bug this fixes: an event-mode alert minted one row PER DAY the condition held, so an
    inactive reader's badge grew forever and permanently read '9+'. Now at most ONE is outstanding,
    with a payload that tracks the current count."""
    st, uid = _store_user(); _all_on(st, uid)
    base = datetime.now(timezone.utc)
    for day in range(10):                                  # ten days of a live feed, never opened
        st.record_recommendations_shown(uid, [(f"https://x.example/d{day}", False)],
                                        shown_at=(base + timedelta(days=day)).isoformat())
        nd.materialize_notifications(st, uid, now=base + timedelta(days=day))
    waiting = _unseen(st, uid, "recommendations_waiting")
    assert len(waiting) == 1                               # NOT 10 — one outstanding alert
    assert waiting[0]["payload"]["count"] == 8             # windowed to the live feed, not all 10


def test_alert_auto_resolves_when_its_condition_clears():
    """Opening every waiting recommendation makes the alert untrue — it must stop counting toward
    the badge on the next evaluation, without the reader having to dismiss a stale row by hand."""
    st, uid = _store_user(); _all_on(st, uid)
    _surface_recs(st, uid, 2)
    nd.materialize_notifications(st, uid)
    assert len(_unseen(st, uid, "recommendations_waiting")) == 1
    for i in range(2):                                     # the reader opens them
        st.record_recommendation_open(uid, f"https://x.example/a-0-{i}", cross_cutting=False)
    nd.materialize_notifications(st, uid)
    assert _unseen(st, uid, "recommendations_waiting") == []   # auto-resolved
    assert len(st.list_notifications(uid, limit=50)) >= 1       # kept as history, just not active
    # …and it re-arms: a NEW surfaced-but-unopened recommendation raises a fresh alert.
    _surface_recs(st, uid, 1, days_ago=0, tag="fresh")
    nd.materialize_notifications(st, uid, now=datetime.now(timezone.utc) + timedelta(days=1))
    assert len(_unseen(st, uid, "recommendations_waiting")) == 1


def test_waiting_count_is_windowed_to_the_live_feed():
    """A card surfaced months ago and never opened is not actionable today — the current feed no
    longer offers it — so it must not inflate the alert."""
    st, uid = _store_user(); _all_on(st, uid)
    _surface_recs(st, uid, 5, days_ago=90)                 # ancient, unopened
    nd.materialize_notifications(st, uid)
    assert _unseen(st, uid, "recommendations_waiting") == []    # nothing waiting *now*
    _surface_recs(st, uid, 2, days_ago=1)                  # fresh, unopened
    nd.materialize_notifications(st, uid)
    waiting = _unseen(st, uid, "recommendations_waiting")
    assert len(waiting) == 1 and waiting[0]["payload"]["count"] == 2   # 2, not 7


def test_notification_history_is_bounded_and_never_prunes_unseen():
    """Cadence kinds legitimately accumulate one row per period forever; pruning bounds the table
    while leaving every UNSEEN (still actionable) row alone."""
    st, uid = _store_user(); _all_on(st, uid)
    for i in range(30):
        st.record_notifications(uid, [{"kind": "weekly_report", "dedupe_key": f"w:{i}",
                                       "created_at": _iso(i), "title_key": "t",
                                       "payload": {}, "gated_by": "weeklyReport"}])
    seen_ids = [r["id"] for r in st.list_notifications(uid, limit=100)][:20]
    for nid in seen_ids:
        st.mark_notification_seen(uid, nid)
    assert st.prune_notifications(uid, keep=10) > 0
    remaining = st.list_notifications(uid, limit=100)
    assert len(remaining) >= 10
    assert len(_unseen(st, uid)) == 10                     # all 10 unseen rows survived pruning
    assert st.prune_notifications(uid, keep=1000) == 0     # nothing to do -> no-op
