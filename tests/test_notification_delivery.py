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
import notification_service as ns    # noqa: E402
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
    # surface two recs (unopened) -> the kind now fires
    st.record_recommendations_shown(uid, [("a1", False), ("a2", True)])
    assert nd.build_context(st, uid).recommendations.unopened_count == 2
    assert nd.materialize_notifications(st, uid) >= 1
    waiting = next(x for x in st.list_notifications(uid, limit=100)
                   if x["kind"] == "recommendations_waiting")
    # The count TRIGGERS the alert (asserted on the context above) and is deliberately NOT carried
    # into the payload: it counts cards scrolled past, not a queue, and rendering it told a reader
    # "3,023 recommendations are waiting for you" about a feed that is rebuilt on every request.
    assert waiting["payload"] == {}, "the payload must not re-introduce a countable backlog"
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
    inactive reader's badge grew forever and permanently read '9+'. Now at most ONE is outstanding."""
    st, uid = _store_user(); _all_on(st, uid)
    base = datetime.now(timezone.utc)
    for day in range(10):                                  # ten days of a live feed, never opened
        st.record_recommendations_shown(uid, [(f"https://x.example/d{day}", False)],
                                        shown_at=(base + timedelta(days=day)).isoformat())
        nd.materialize_notifications(st, uid, now=base + timedelta(days=day))
    waiting = _unseen(st, uid, "recommendations_waiting")
    assert len(waiting) == 1                               # NOT 10 — one outstanding alert
    # The trigger stays windowed to the live feed (8 of the 10 days, not all 10). Asserted on the
    # context because the payload no longer carries the number — see the kind's comment.
    assert nd.build_context(st, uid, now=base + timedelta(days=9)) \
             .recommendations.unopened_count == 8


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
    assert len(waiting) == 1
    # 2, not 7 — the five ancient cards are outside the window. On the context, not the payload:
    # the alert no longer states a number, but the number still decides whether it fires.
    assert nd.build_context(st, uid).recommendations.unopened_count == 2


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


# --------------------------------------------------------------------------- #
# Global events through the boundary (A4).
#
# `build_context` now reads one thing that is not about the reader, and `materialize_notifications`
# must give a one-time occurrence NEITHER of the two treatments it gives state alerts. These assert
# both directions, end to end against a real store.
# --------------------------------------------------------------------------- #
EV_NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def _breaking_on(st, uid, on=True):
    st.save_settings(uid, {"notifications": {"categories": {"breaking": {"inApp": on}}}})


def _emit(st, i, *, title="Court issues major ruling", occurred=None, expires=None):
    return st.record_notification_event(
        "story_breaking", f"st_{i}", category="breaking",
        payload={"storyId": f"st_{i}", "title": title, "publisherCount": 4},
        occurred_at=occurred or (EV_NOW - timedelta(hours=1)).isoformat(), expires_at=expires)


def _breaking_rows(st, uid):
    return [n for n in st.list_notifications(uid, limit=100) if n["kind"] == "breaking_story"]


def test_events_become_notifications_and_re_evaluation_adds_nothing():
    """The pipeline in one test: an event exists, the reader fetches, one row appears — and fetching
    again adds nothing, because the dedupe ledger already holds its key."""
    st, uid = _store_user("nd-ev-1")
    _breaking_on(st, uid)
    _emit(st, 1)
    _emit(st, 2)

    assert nd.materialize_notifications(st, uid, now=EV_NOW) >= 2
    assert {r["dedupe_key"] for r in _breaking_rows(st, uid)} == {"ev:1", "ev:2"}

    assert nd.materialize_notifications(st, uid, now=EV_NOW) == 0, "idempotent on re-evaluation"
    assert len(_breaking_rows(st, uid)) == 2


def test_a_breaking_notification_survives_the_story_no_longer_breaking():
    """F2, end to end and against a real store. State alerts auto-resolve when their condition
    clears; an occurrence must not. The story stopping breaking does not un-break it — and here the
    event itself has EXPIRED, which is the strongest form of "the condition is gone"."""
    st, uid = _store_user("nd-ev-2")
    _breaking_on(st, uid)
    _emit(st, 1, expires=(EV_NOW + timedelta(hours=1)).isoformat())
    nd.materialize_notifications(st, uid, now=EV_NOW)
    assert len(_breaking_rows(st, uid)) == 1

    later = EV_NOW + timedelta(hours=6)          # the event has expired; no event is in the context
    nd.materialize_notifications(st, uid, now=later)
    rows = _breaking_rows(st, uid)
    assert len(rows) == 1, "the row is still there"
    assert rows[0]["seenAt"] is None, "and still UNREAD — it was never auto-resolved"


def test_many_breaking_stories_accumulate_rather_than_collapsing_to_one():
    """The other half of F2: state alerts keep at most one outstanding row per kind. One row per
    story is the entire point of this kind, so the collapse must not apply."""
    st, uid = _store_user("nd-ev-3")
    _breaking_on(st, uid)
    for i in range(1, 4):
        _emit(st, i)
    nd.materialize_notifications(st, uid, now=EV_NOW)
    assert len(_breaking_rows(st, uid)) == 3, "three stories, three rows"


def test_the_daily_cap_holds_across_separate_evaluations():
    """The cap is per DAY, not per evaluation — a reader refreshing the page must not multiply it.
    This only works because the boundary reads today's counts back out of the store."""
    st, uid = _store_user("nd-ev-4")
    _breaking_on(st, uid)
    for i in range(1, 5):
        _emit(st, i)
    nd.materialize_notifications(st, uid, now=EV_NOW)
    assert len(_breaking_rows(st, uid)) == 4

    for i in range(5, 9):                        # four more stories break later the same day
        _emit(st, i, occurred=(EV_NOW + timedelta(minutes=30)).isoformat())
    nd.materialize_notifications(st, uid, now=EV_NOW + timedelta(hours=1))
    assert len(_breaking_rows(st, uid)) == ns.BREAKING_MAX_PER_DAY, "capped at the daily ceiling"

    # A new day restores the budget — and the three stories the cap held back yesterday are still
    # live (they were given no TTL here), so they arrive too. The cap DEFERS; expiry is what drops.
    tomorrow = EV_NOW + timedelta(days=1)
    _emit(st, 9, occurred=tomorrow.isoformat())
    nd.materialize_notifications(st, uid, now=tomorrow)
    assert len(_breaking_rows(st, uid)) == 9, "yesterday's held-back stories were deferred, not lost"


def test_the_cap_plus_a_ttl_drops_rather_than_defers():
    """The production shape: A5 gives every breaking event a TTL, so a story the cap held back is
    genuinely gone once it goes stale rather than resurfacing a day later as news."""
    st, uid = _store_user("nd-ev-4b")
    _breaking_on(st, uid)
    for i in range(1, 9):
        _emit(st, i, expires=(EV_NOW + timedelta(hours=6)).isoformat())
    nd.materialize_notifications(st, uid, now=EV_NOW)
    assert len(_breaking_rows(st, uid)) == ns.BREAKING_MAX_PER_DAY

    tomorrow = EV_NOW + timedelta(days=1)
    nd.materialize_notifications(st, uid, now=tomorrow)
    assert len(_breaking_rows(st, uid)) == ns.BREAKING_MAX_PER_DAY, "the held-back ones expired"


def test_the_category_preference_gates_delivery():
    st, uid = _store_user("nd-ev-5")
    _breaking_on(st, uid, on=False)
    _emit(st, 1)
    nd.materialize_notifications(st, uid, now=EV_NOW)
    assert _breaking_rows(st, uid) == []


def test_an_unreadable_event_store_costs_only_the_breaking_notifications(monkeypatch):
    """Fail-soft, and specifically WHICH way. Events feed a supplementary kind; the rest of the
    context feeds the reader's own report and streak. A broken event read must not take the inbox
    down with it."""
    st, uid = _store_user("nd-ev-6")
    _all_on(st, uid)
    _breaking_on(st, uid)
    _report(st, uid)                             # so a NON-breaking kind has something to deliver
    _emit(st, 1)

    def boom(*a, **k):
        raise RuntimeError("events table is unavailable")

    monkeypatch.setattr(st, "recent_notification_events", boom)
    created = nd.materialize_notifications(st, uid, now=EV_NOW)   # must not raise
    assert _breaking_rows(st, uid) == [], "no breaking notifications"
    assert created > 0 and "weekly_report" in _kinds(st, uid), "the rest of the inbox still arrived"


def test_an_unreadable_count_fails_CLOSED_not_open(monkeypatch):
    """The opposite posture from the events read, deliberately. If today's counts cannot be read, a
    cap that defaulted to zero-sent would read as "budget untouched" and could deliver the ceiling
    again on every evaluation. Assume it is spent instead."""
    st, uid = _store_user("nd-ev-7")
    _breaking_on(st, uid)
    _emit(st, 1)

    def boom(*a, **k):
        raise RuntimeError("cannot count")

    monkeypatch.setattr(st, "notification_counts_today", boom)
    nd.materialize_notifications(st, uid, now=EV_NOW)
    assert _breaking_rows(st, uid) == [], "no counts -> assume the cap is spent"


def test_the_events_window_bounds_the_query_but_expiry_is_the_policy():
    """`RWE_NOTIFY_EVENTS_WINDOW_HOURS` only bounds how far back we look; an event outside it is not
    delivered even though it never expires."""
    st, uid = _store_user("nd-ev-8")
    _breaking_on(st, uid)
    _emit(st, 1, occurred=(EV_NOW - timedelta(days=3)).isoformat(), expires=None)
    nd.materialize_notifications(st, uid, now=EV_NOW)
    assert _breaking_rows(st, uid) == [], "older than the 24h window"


def test_existing_state_alerts_still_reconcile_exactly_as_before():
    """The regression that matters: adding a discrete kind must change nothing about how the three
    event kinds are resolved and collapsed."""
    st, uid = _store_user("nd-ev-9")
    _all_on(st, uid)
    _breaking_on(st, uid)
    _emit(st, 1)
    nd.materialize_notifications(st, uid, now=EV_NOW)

    ctx = nd.build_context(st, uid, EV_NOW)
    inactive = ns.inactive_event_kinds(ctx)
    assert "breaking_story" not in inactive
    assert set(inactive) <= set(ns.EVENT_KINDS), "only state alerts are ever resolved"
    assert "breaking_story" not in ns.EVENT_KINDS, "and it is not one"


def test_build_context_carries_events_and_counts():
    st, uid = _store_user("nd-ev-10")
    _breaking_on(st, uid)
    _emit(st, 1)
    ctx = nd.build_context(st, uid, EV_NOW)
    assert [e["sourceId"] for e in ctx.events.events] == ["st_1"]
    assert ctx.delivery.counts_today.get("breaking_story", 0) == 0

    nd.materialize_notifications(st, uid, now=EV_NOW)
    assert nd.build_context(st, uid, EV_NOW).delivery.counts_today["breaking_story"] == 1


def test_within_one_batch_breaking_rows_are_ordered_by_insertion_not_recency():
    """A known property, pinned so it is not mistaken for a guarantee.

    `_breaking_fanout` returns events NEWEST-FIRST, because `evaluate` truncates the front and the
    cap must keep the most recent stories. `list_notifications` then orders by `id DESC`, so the
    first-emitted (newest) event gets the lowest id and appears LAST within that batch.

    This is not specific to breaking stories — every kind materialised in one batch shares
    `created_at`, so the inbox's intra-batch order has always been insertion order. It matters here
    only because this is the first kind that can produce several rows at once. Presentation owns the
    fix if one is wanted: the payload carries `occurredAt` precisely so A6 can sort by it."""
    st, uid = _store_user("nd-ev-order")
    _breaking_on(st, uid)
    _emit(st, 1, occurred=(EV_NOW - timedelta(hours=3)).isoformat())    # older
    _emit(st, 2, occurred=(EV_NOW - timedelta(hours=1)).isoformat())    # newer
    nd.materialize_notifications(st, uid, now=EV_NOW)

    rows = _breaking_rows(st, uid)
    assert [r["dedupe_key"] for r in rows] == ["ev:1", "ev:2"], "insertion order, reversed by id DESC"
    assert [r["payload"]["occurredAt"] for r in rows] == sorted(
        r["payload"]["occurredAt"] for r in rows), "…so occurredAt is what presentation must sort on"


def test_the_cap_keeps_the_NEWEST_stories_not_an_arbitrary_five():
    """The reason the fanout is newest-first: when more stories break than the cap allows, the
    reader should get the most recent ones."""
    st, uid = _store_user("nd-ev-newest")
    _breaking_on(st, uid)
    for i in range(1, 9):                        # 8 stories, one per hour, ev:8 the most recent
        _emit(st, i, occurred=(EV_NOW - timedelta(hours=9 - i)).isoformat())
    nd.materialize_notifications(st, uid, now=EV_NOW)

    delivered = {r["dedupe_key"] for r in _breaking_rows(st, uid)}
    assert delivered == {"ev:8", "ev:7", "ev:6", "ev:5", "ev:4"}, "the five most recent"
