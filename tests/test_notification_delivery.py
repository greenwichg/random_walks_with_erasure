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
    assert ctx.reading.reads_this_week == 0
    assert ctx.recommendations.new_count == 0
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
    # a genuinely NEW topic set -> a fresh blind_spot_alert
    st.save_report(uid, {"mode": "measured", "overall": 50,
                         "blindSpots": [{"topic": "Economy"}, {"topic": "Climate"}]})
    assert nd.materialize_notifications(st, uid) == 1
    alerts = [x for x in st.list_notifications(uid, limit=100) if x["kind"] == "blind_spot_alert"]
    assert len(alerts) == 2                             # {Economy} and {Economy,Climate} are distinct


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
def test_new_recommendations_is_inert_no_canonical_definition():
    st, uid = _store_user(); _all_on(st, uid)
    _report(st, uid); _read(st, uid, "u", 0)
    nd.materialize_notifications(st, uid)
    assert nd.build_context(st, uid).recommendations.new_count == 0
    assert "new_recommendations" not in _kinds(st, uid)


def test_streak_reminder_is_inert_under_reading_streak():
    """`_reading_streak` counts days ENDING TODAY, so streak_days >= 1 iff the user read today — but
    the D0 predicate also requires `not read_today`. The two are contradictory, so the kind never
    materialises in production. Pinned in both states so the interaction stays visible."""
    st, uid = _store_user(); _all_on(st, uid)
    _read(st, uid, "u-today", 0)                        # read today -> streak>=1 but read_today True
    nd.materialize_notifications(st, uid)
    assert "streak_reminder" not in _kinds(st, uid)

    st2, uid2 = _store_user("nd-streak2"); _all_on(st2, uid2)
    _read(st2, uid2, "u-yest", 1)                       # read only yesterday -> read_today False, streak 0
    nd.materialize_notifications(st2, uid2)
    assert "streak_reminder" not in _kinds(st2, uid2)
