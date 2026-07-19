"""Tests for the N1 notification persistence layer — the `notifications` table + store methods.

Covers the persistence contract only (no endpoints, no evaluation wiring): idempotency (per batch,
across re-evaluation, and at the DB level), per-user isolation, round-trip serialization, ledger
reconstruction (record -> delivered keys -> suppress re-delivery), newest-first ordering, the
``unseen_only`` filter, and verbatim ``created_at``. Notifications are produced by the real
``notification_service`` leaf and serialised with ``dataclasses.asdict`` — the exact dict the store
persists — proving the store stays decoupled from that module (it only ever writes dicts).
"""

import dataclasses
import pathlib
import sys
from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import notification_service as ns   # noqa: E402
import settings_service as ss       # noqa: E402
import store as store_mod           # noqa: E402


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)   # a later ISO week + same month


def _store_user(account="notif-1"):
    st = store_mod.Store("sqlite://")                       # in-memory
    uid = st.upsert_user_by_identity("dev", account).id
    return st, uid


def _settings():
    return ss.normalize_settings({
        "weeklyReport": True, "monthlyReport": True,
        "notifications": {"recommendations": True, "weeklyDigest": True,
                          "streakReminders": True, "blindSpotAlerts": True}})


def _ctx(now=NOW, delivered=(), blind_spots=("topic:economy",)):
    """A context in which all six kinds fire (unless suppressed by ``delivered``)."""
    return ns.NotificationContext(
        now=now, settings=_settings(),
        delivery=ns.DeliveryState(delivered_keys=frozenset(delivered)),
        report=ns.ReportInputs(has_report=True, overall=72, blind_spots=blind_spots),
        recommendations=ns.RecommendationInputs(unopened_count=4),
        reading=ns.ReadingInputs(streak_days=5, read_today=False, reads_this_week=6))


def _dicts(ctx):
    """Exactly what the store is handed: the evaluated notifications as JSON-safe dicts."""
    return [dataclasses.asdict(n) for n in ns.evaluate(ctx)]


# --------------------------------------------------------------------------- #
# Round-trip serialization.
# --------------------------------------------------------------------------- #
def test_record_and_roundtrip():
    st, uid = _store_user()
    dicts = _dicts(_ctx())
    assert len(dicts) == 6
    assert st.record_notifications(uid, dicts) == 6

    listed = st.list_notifications(uid, limit=100)
    assert len(listed) == 6
    by_kind = {d["kind"]: d for d in listed}
    assert set(by_kind) == {n["kind"] for n in dicts}

    src = next(d for d in dicts if d["kind"] == "weekly_report")
    got = by_kind["weekly_report"]
    assert got["dedupe_key"] == src["dedupe_key"]
    assert got["payload"] == src["payload"]           # nested dict survives verbatim
    assert got["title_key"] == src["title_key"]
    assert got["gated_by"] == src["gated_by"]
    assert got["created_at"] == NOW.isoformat()       # verbatim injected timestamp
    assert got["seenAt"] is None
    assert isinstance(got["id"], int)


# --------------------------------------------------------------------------- #
# Idempotency — batch, re-evaluation, in-batch, and DB level.
# --------------------------------------------------------------------------- #
def test_idempotent_across_batch_and_reevaluation():
    st, uid = _store_user()
    assert st.record_notifications(uid, _dicts(_ctx())) == 6
    assert st.record_notifications(uid, _dicts(_ctx())) == 0     # same keys again -> nothing new
    assert len(st.list_notifications(uid, limit=100)) == 6       # still exactly six rows


def test_in_batch_duplicate_recorded_once():
    st, uid = _store_user()
    dicts = _dicts(_ctx())
    assert st.record_notifications(uid, dicts + [dicts[0]]) == 6   # duplicate not double-counted
    assert len(st.list_notifications(uid, limit=100)) == 6


def test_db_level_uniqueness_enforced():
    st, uid = _store_user()
    with pytest.raises(IntegrityError):
        with st.session() as s:                          # bypass the method's pre-check on purpose
            s.add(store_mod.Notification(user_id=uid, kind="weekly_report",
                                         dedupe_key="weekly_report:2026-W29",
                                         body="{}", created_at=NOW.isoformat()))
            s.add(store_mod.Notification(user_id=uid, kind="weekly_report",
                                         dedupe_key="weekly_report:2026-W29",
                                         body="{}", created_at=NOW.isoformat()))


# --------------------------------------------------------------------------- #
# Per-user isolation.
# --------------------------------------------------------------------------- #
def test_per_user_isolation():
    st, uid = _store_user("notif-a")
    uid2 = st.upsert_user_by_identity("dev", "notif-b").id
    dicts = _dicts(_ctx())
    st.record_notifications(uid, dicts)
    st.record_notifications(uid2, dicts)                 # identical dedupe keys, different user

    assert len(st.list_notifications(uid, limit=100)) == 6
    assert len(st.list_notifications(uid2, limit=100)) == 6
    assert st.delivered_notification_keys(uid) == st.delivered_notification_keys(uid2)
    a_ids = {d["id"] for d in st.list_notifications(uid, limit=100)}
    b_ids = {d["id"] for d in st.list_notifications(uid2, limit=100)}
    assert a_ids.isdisjoint(b_ids)                       # distinct rows per user


# --------------------------------------------------------------------------- #
# Ledger reconstruction — the record -> delivered_keys -> suppress loop.
# --------------------------------------------------------------------------- #
def test_ledger_reconstruction_suppresses_redelivery():
    st, uid = _store_user()
    st.record_notifications(uid, _dicts(_ctx()))

    delivered = st.delivered_notification_keys(uid)
    assert delivered == {n["dedupe_key"] for n in _dicts(_ctx())}

    # feed the persisted ledger back into evaluate -> nothing re-fires
    assert ns.evaluate(_ctx(delivered=delivered)) == []
    assert st.record_notifications(uid, _dicts(_ctx(delivered=delivered))) == 0


# --------------------------------------------------------------------------- #
# Ordering (newest-first) + limit.
# --------------------------------------------------------------------------- #
def test_list_is_newest_first():
    st, uid = _store_user()
    st.record_notifications(uid, _dicts(_ctx()))
    ids = [d["id"] for d in st.list_notifications(uid, limit=100)]
    assert ids == sorted(ids, reverse=True)              # strictly newest-first by id


def test_ordering_across_batches():
    st, uid = _store_user()
    st.record_notifications(uid, _dicts(_ctx(now=NOW)))
    st.record_notifications(uid, _dicts(_ctx(now=LATER)))   # a later week -> some fresh keys
    top = st.list_notifications(uid, limit=1)[0]
    assert top["created_at"] == LATER.isoformat()        # most-recent batch surfaces first


def test_limit_caps_results():
    st, uid = _store_user()
    st.record_notifications(uid, _dicts(_ctx()))
    assert len(st.list_notifications(uid, limit=3)) == 3


# --------------------------------------------------------------------------- #
# unseen_only filter (the seen_at column exists now; mark-seen is a later milestone).
# --------------------------------------------------------------------------- #
def test_unseen_only_filter():
    st, uid = _store_user()
    st.record_notifications(uid, _dicts(_ctx()))
    all_ids = [d["id"] for d in st.list_notifications(uid, limit=100)]
    assert len(all_ids) == 6
    assert len(st.list_notifications(uid, unseen_only=True, limit=100)) == 6   # all unseen initially

    with st.session() as s:                              # set read-state directly (no N2 method yet)
        s.get(store_mod.Notification, all_ids[0]).seen_at = NOW.isoformat()

    unseen = st.list_notifications(uid, unseen_only=True, limit=100)
    assert len(unseen) == 5 and all_ids[0] not in {d["id"] for d in unseen}
    assert len(st.list_notifications(uid, limit=100)) == 6    # unfiltered still returns all six
