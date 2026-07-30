"""Tests for the B3 retry ladder — the backoff policy, the persisted schedule, and the worker's
second-and-later attempts.

Split from `test_push_delivery.py` because the question is different. That file asks "does a
notification reach a device?"; this one asks "what happens when it does not?" — which is a question
about *time*, and time is the thing a scheduler must be testable in without waiting for it. Every test
here supplies its own `now`, and the jitter source is injected wherever a number is asserted.

The contract pinned here is `docs/BROWSER_PUSH_ARCHITECTURE.md` §7 ("Retries"), which fixed the
classification in B2 and left the ladder for B3.
"""

import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import push_delivery                # noqa: E402
import push_retry                   # noqa: E402
import push_sender                  # noqa: E402
import settings_service as ss       # noqa: E402
import store as store_mod           # noqa: E402

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------------------------- #
# The policy — pure, no store, no clock of its own.
# --------------------------------------------------------------------------------------------- #
@pytest.mark.parametrize("status,retry", [
    (push_sender.TIMEOUT, True), (push_sender.TRANSIENT, True),
    (push_sender.SUCCESS, False), (push_sender.PERMANENT, False), (push_sender.EXPIRED, False),
])
def test_only_the_failures_that_could_succeed_later_are_retried(status, retry):
    """`expired` is a failure that is never retried: the address is gone, so another attempt is
    guaranteed to fail. `permanent` likewise — an unchanged request rejected for being wrong can only
    be rejected again."""
    assert push_retry.is_retryable(status) is retry


@pytest.mark.parametrize("raw,seconds", [
    ("120", 120.0), (30, 30.0), (7.5, 7.5), ("0", 0.0),
    ("-10", 0.0),                        # a negative delta is floored, never turned into a rewind
    (-5, 0.0),
    (None, None), ("", None), ("soon", None), ("   ", None),
])
def test_retry_after_reads_the_delta_form(raw, seconds):
    """The numeric form, and the floor. Everything unusable answers `None` rather than `0`, so the
    caller falls back to its own backoff instead of to no wait at all — the difference between
    "the service said nothing" and "the service said go now"."""
    assert push_retry.parse_retry_after(raw, now=NOW) == seconds


def test_retry_after_reads_the_http_date_form():
    """RFC 9110 allows both, and push services use both. A date is only meaningful relative to a
    clock, which is why parsing it lives here rather than in the transport."""
    future = push_retry.parse_retry_after("Thu, 30 Jul 2026 12:05:00 GMT", now=NOW)
    assert future == pytest.approx(300.0)


def test_a_retry_after_date_already_past_means_now_not_the_past():
    """The service is saying the window has opened, not that we should travel backwards. A negative
    delay would schedule a retry before the failure that caused it."""
    assert push_retry.parse_retry_after("Thu, 30 Jul 2026 11:00:00 GMT", now=NOW) == 0.0


def test_a_retry_after_without_a_timezone_is_read_as_utc():
    """Everything this system writes and reads is UTC. Interpreting a bare date as local time would
    shift the whole ladder by the host's offset — silently, and differently per deployment."""
    assert push_retry.parse_retry_after("Thu, 30 Jul 2026 12:10:00", now=NOW) == pytest.approx(600.0)


def test_the_backoff_grows_and_then_stops_growing():
    """Growth is what backs off a service that is already failing. The cap is what stops the exponent
    scheduling an attempt days out, against a payload the push service dropped hours earlier."""
    highest = push_retry.backoff_seconds(1, rng=lambda: 1.0)
    for attempts in range(2, 8):
        nxt = push_retry.backoff_seconds(attempts, rng=lambda: 1.0)
        assert nxt >= highest
        highest = nxt
    assert push_retry.backoff_seconds(1, rng=lambda: 1.0) == push_retry.BASE_SECONDS
    assert push_retry.backoff_seconds(2, rng=lambda: 1.0) == push_retry.BASE_SECONDS * 2
    assert push_retry.backoff_seconds(20, rng=lambda: 1.0) == push_retry.MAX_BACKOFF_SECONDS


@pytest.mark.parametrize("draw", [0.0, 0.25, 0.5, 0.99, 1.0])
def test_equal_jitter_stays_inside_its_half_of_the_window(draw):
    """Half fixed, half random. Full jitter can draw a near-zero delay, which defeats the backing-off;
    the fixed half is what guarantees a floor."""
    raw = push_retry.BASE_SECONDS * 2         # attempt 2, before jitter
    got = push_retry.backoff_seconds(2, rng=lambda: draw)
    assert raw / 2 <= got <= raw


def test_the_delay_actually_varies_with_the_draw():
    """The bounds test above passes for a delay that ignores the jitter entirely. Without variation
    every device in a failed fan-out retries at the same instant — the thundering herd the jitter
    exists to prevent, aimed at a service that is already unwell."""
    low = push_retry.backoff_seconds(3, rng=lambda: 0.0)
    high = push_retry.backoff_seconds(3, rng=lambda: 1.0)
    assert low < high
    assert len({push_retry.backoff_seconds(3, rng=lambda: d) for d in (0.1, 0.4, 0.9)}) == 3


def test_the_whole_ladder_fits_inside_the_age_bound():
    """The three bounds are not independent knobs: an attempt budget whose worst case runs past the
    age bound means the last attempts can never happen, and the ladder is shorter than it claims.
    Tuning any of the three has to keep this true."""
    assert push_retry.MAX_ATTEMPTS >= 3, "two sends is a repeat, not a ladder"
    worst = sum(push_retry.backoff_seconds(n, rng=lambda: 1.0)
                for n in range(1, push_retry.MAX_ATTEMPTS))
    assert worst < push_retry.MAX_DELIVERY_AGE_SECONDS


def test_retry_after_is_a_floor_and_never_a_ceiling():
    """A push service asking for MORE time gets it — asking again sooner is the definition of
    hammering. One asking for less does not get to shorten our own backoff."""
    longer = push_retry.backoff_seconds(1, retry_after=600.0, rng=lambda: 0.0)
    assert longer == 600.0
    shorter = push_retry.backoff_seconds(3, retry_after=1.0, rng=lambda: 1.0)
    assert shorter == push_retry.BASE_SECONDS * 4, "our backoff still applies"


def test_a_failure_schedules_the_next_attempt_in_the_future():
    when = push_retry.next_attempt_at(now=NOW, attempts=1, first_attempted_at=NOW, rng=lambda: 0.5)
    assert when is not None and when > NOW


def test_the_ladder_ends_when_the_attempt_budget_is_spent():
    """Without this, a permanently-unreachable service turns one notification into an unbounded
    stream of requests."""
    assert push_retry.next_attempt_at(now=NOW, attempts=push_retry.MAX_ATTEMPTS - 1,
                                      first_attempted_at=NOW) is not None
    assert push_retry.next_attempt_at(now=NOW, attempts=push_retry.MAX_ATTEMPTS,
                                      first_attempted_at=NOW) is None
    assert push_retry.give_up_reason(now=NOW, attempts=push_retry.MAX_ATTEMPTS,
                                     first_attempted_at=NOW) == "attempts"


def test_the_ladder_ends_when_the_delivery_is_simply_too_old():
    """The bound that matters most, and it is not about performance: a notification that arrives late
    enough is not a late notification, it is a wrong one."""
    old = NOW - timedelta(seconds=push_retry.MAX_DELIVERY_AGE_SECONDS + 1)
    assert push_retry.next_attempt_at(now=NOW, attempts=1, first_attempted_at=old) is None
    assert push_retry.give_up_reason(now=NOW, attempts=1, first_attempted_at=old) == "age"
    assert push_retry.expired(now=NOW, first_attempted_at=old) is True


def test_an_attempt_that_would_land_past_the_age_bound_is_not_scheduled():
    """Waiting for a deadline we already know we will miss is strictly worse than admitting it: the
    row sits in a retryable state meanwhile, and every operator reading the ledger has to work out
    for themselves that it is already doomed."""
    nearly = NOW - timedelta(seconds=push_retry.MAX_DELIVERY_AGE_SECONDS - 5)
    assert push_retry.expired(now=NOW, first_attempted_at=nearly) is False, "not yet expired"
    assert push_retry.next_attempt_at(now=NOW, attempts=1, first_attempted_at=nearly,
                                      rng=lambda: 1.0) is None, "but the next attempt would be"


def test_a_retry_after_beyond_the_age_bound_ends_the_ladder_rather_than_parking_it():
    assert push_retry.next_attempt_at(now=NOW, attempts=1, first_attempted_at=NOW,
                                      retry_after=push_retry.MAX_DELIVERY_AGE_SECONDS * 2) is None


def test_an_unknown_start_time_is_not_an_expired_one():
    """Abandoning a delivery for a fact not in evidence is the worse error. A legacy row that predates
    the column reads as `None`, and it must still get its attempts."""
    assert push_retry.expired(now=NOW, first_attempted_at=None) is False
    assert push_retry.next_attempt_at(now=NOW, attempts=1, first_attempted_at=None) is not None


def test_a_naive_timestamp_is_read_as_utc_not_as_local_time():
    """SQLite hands back naive datetimes. Reading them as local time would shift the age bound by the
    host's offset — a bug that only appears on a machine not set to UTC, which is every laptop."""
    naive = (NOW - timedelta(seconds=10)).replace(tzinfo=None)
    assert push_retry.expired(now=NOW, first_attempted_at=naive) is False
    long_ago = (NOW - timedelta(seconds=push_retry.MAX_DELIVERY_AGE_SECONDS + 60)).replace(tzinfo=None)
    assert push_retry.expired(now=NOW, first_attempted_at=long_ago) is True


@pytest.fixture()
def tz(monkeypatch):
    """Run the body under a deliberately non-UTC timezone.

    Without this, every "read as UTC" assertion in this file passes on a host that is already UTC —
    which CI is, which is exactly why the bug would ship. Ten hours ahead is chosen so a mistake is
    larger than the values under test rather than a rounding difference."""
    import time
    if not hasattr(time, "tzset"):
        pytest.skip("tzset is POSIX-only")
    monkeypatch.setenv("TZ", "Australia/Sydney")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def test_a_naive_timestamp_is_utc_even_on_a_host_that_is_not(tz):
    """The same claim as above, made where it can actually fail. `astimezone` on a naive datetime
    interprets it as LOCAL time; `replace` states that it was always UTC. On a UTC host the two are
    identical, so only this test can tell them apart."""
    ten_minutes_old = (NOW - timedelta(minutes=10)).replace(tzinfo=None)
    assert push_retry.expired(now=NOW, first_attempted_at=ten_minutes_old) is False, \
        "a ten-minute-old delivery is not four hours old, whatever the host clock says"


def test_a_bare_http_date_is_utc_even_on_a_host_that_is_not(tz):
    """RFC 9110 dates without a zone are UTC. Reading one as local time on a host ten hours ahead
    turns a five-minute wait into a ten-hour one — past the age bound, so the delivery is abandoned
    rather than retried, and nothing in the logs points at a timezone."""
    assert push_retry.parse_retry_after("Thu, 30 Jul 2026 12:05:00", now=NOW) == pytest.approx(300.0)


def test_the_age_bound_matches_the_transports_ttl():
    """They are the same number for a reason: the push service drops the message at its TTL, so an
    attempt past that point could not succeed even if it were made."""
    source = (ROOT / "examples" / "push_sender.py").read_text(encoding="utf-8")
    assert f"ttl={int(push_retry.MAX_DELIVERY_AGE_SECONDS)}," in source


# --------------------------------------------------------------------------------------------- #
# The persisted schedule. This is what makes the ladder survive a deploy.
# --------------------------------------------------------------------------------------------- #
@pytest.fixture()
def st():
    return store_mod.Store("sqlite://")


def _reader(st, account="retry-b3", *, push=True):
    uid = st.upsert_user_by_identity("dev", account).id
    ss.update(st, uid, {"notifications": {"categories": {"breaking": {"push": push,
                                                                     "inApp": True}}}})
    return uid


def _subscribe(st, uid, endpoint="https://push.example/dev-1"):
    cats = ss.get(st, uid)["notifications"]["categories"]
    return st.upsert_push_subscription(uid, endpoint, p256dh="BPub", auth="Auth",
                                       categories=cats)["id"]


def _notification(st, uid, key="ev:1"):
    st.record_notifications(uid, [{"kind": "breaking_story", "dedupe_key": key,
                                   "created_at": NOW.isoformat(),
                                   "title_key": "notifications.breaking_story.title",
                                   "payload": {"storyId": "st_1", "title": "Something broke"},
                                   "gated_by": "notifications.categories.breaking.push"}])
    return st.notification_ids_by_dedupe_key(uid, [key])[key]


def test_a_scheduled_delivery_is_invisible_until_it_is_due(st):
    uid = _reader(st)
    sid, nid = _subscribe(st, uid), _notification(st, uid)
    claim = st.claim_delivery(nid, sid, user_id=uid)
    st.record_delivery_result(claim, "transient", status_code=503,
                              next_attempt_at=NOW + timedelta(minutes=5))

    assert st.due_deliveries(now=NOW) == [], "not yet"
    assert [r["id"] for r in st.due_deliveries(now=NOW + timedelta(minutes=6))] == [claim]


def test_an_open_delivery_has_no_completion_time(st):
    """`completed_at` means "this is over". Stamping it on a row that will be tried again would make
    it mean "the last time we gave up", which no reader of the ledger expects."""
    uid = _reader(st)
    sid, nid = _subscribe(st, uid), _notification(st, uid)
    claim = st.claim_delivery(nid, sid, user_id=uid)
    st.record_delivery_result(claim, "transient", next_attempt_at=NOW + timedelta(minutes=5))
    assert st.delivery_attempts(notification_id=nid)[0]["completedAt"] is None

    st.record_delivery_result(claim, "permanent", next_attempt_at=None)
    assert st.delivery_attempts(notification_id=nid)[0]["completedAt"] is not None


def test_a_row_a_dead_process_left_claimed_is_recoverable_after_the_lease(st):
    """The B2 behaviour was to abandon these, on the reasoning that we cannot know whether the reader
    saw it. The lease is what makes recovery safe: nothing takes fifteen minutes to send, so a row
    this old means the process died — and the `tag` derived from `dedupeKey` collapses a duplicate at
    the OS level if the first attempt did in fact land."""
    uid = _reader(st)
    sid, nid = _subscribe(st, uid), _notification(st, uid)
    claim = st.claim_delivery(nid, sid, user_id=uid, now=NOW)   # claimed, never resolved

    fresh = st.due_deliveries(now=NOW, lease_seconds=push_retry.LEASE_SECONDS)
    assert fresh == [], "a send that started a moment ago is not abandoned"

    later = NOW + timedelta(seconds=push_retry.LEASE_SECONDS + 60)
    assert [r["id"] for r in st.due_deliveries(now=later)] == [claim]


def test_a_settled_delivery_is_never_due_again(st):
    uid = _reader(st)
    sid, nid = _subscribe(st, uid), _notification(st, uid)
    claim = st.claim_delivery(nid, sid, user_id=uid)
    st.record_delivery_result(claim, "success", status_code=201)
    assert st.due_deliveries(now=NOW + timedelta(days=7)) == []


def test_two_workers_reading_one_due_row_produce_one_attempt(st):
    """Compare-and-set on the attempts counter — the same shape as the UNIQUE claim one level up,
    applied to a row that already exists. The loser backs off rather than sending a duplicate."""
    uid = _reader(st)
    sid, nid = _subscribe(st, uid), _notification(st, uid)
    claim = st.claim_delivery(nid, sid, user_id=uid)
    st.record_delivery_result(claim, "transient", next_attempt_at=NOW)
    row = st.due_deliveries(now=NOW)[0]

    assert st.lease_delivery(claim, attempts=row["attempts"], now=NOW) is True
    assert st.lease_delivery(claim, attempts=row["attempts"], now=NOW) is False, "stale counter"
    assert st.delivery_attempts(notification_id=nid)[0]["attempts"] == row["attempts"] + 1


def test_leasing_a_row_takes_it_out_of_the_scan_immediately(st):
    """The lease has to clear the schedule as well as set the status, or a row claimed for a send in
    flight stays visible as "due" — and a process that dies mid-send leaves it recoverable at once
    instead of after the lease window, which is the whole protection."""
    uid = _reader(st)
    sid, nid = _subscribe(st, uid), _notification(st, uid)
    claim = st.claim_delivery(nid, sid, user_id=uid, now=NOW)
    st.record_delivery_result(claim, "transient", next_attempt_at=NOW - timedelta(minutes=1))

    row = st.due_deliveries(now=NOW)[0]
    assert st.lease_delivery(claim, attempts=row["attempts"], now=NOW) is True
    assert st.due_deliveries(now=NOW) == [], "in flight, not due"
    assert st.due_deliveries(now=NOW + timedelta(seconds=push_retry.LEASE_SECONDS + 60)) != [], \
        "and recoverable once the lease has run out"


def test_a_notification_body_can_never_shadow_its_own_row_id(st):
    """The body is stored JSON, and the delivery ledger keys on the ROW id. A body that happens to
    carry an `id` would otherwise be claimed and recorded against whatever integer it contained."""
    uid = _reader(st)
    st.record_notifications(uid, [{"kind": "breaking_story", "dedupe_key": "ev:shadow",
                                   "id": 999999, "created_at": NOW.isoformat(),
                                   "title_key": "notifications.breaking_story.title",
                                   "payload": {"storyId": "st_1", "title": "x"}}])
    nid = st.notification_ids_by_dedupe_key(uid, ["ev:shadow"])["ev:shadow"]
    assert st.notification_by_id(nid)["id"] == nid


def test_the_scan_is_scoped_to_its_own_channel(st):
    """`channel` is the axis a future transport extends along. A web-push worker must not lease an
    email delivery out from under whatever will send those."""
    uid = _reader(st)
    sid, nid = _subscribe(st, uid), _notification(st, uid)
    claim = st.claim_delivery(nid, sid, user_id=uid, channel="email")
    st.record_delivery_result(claim, "transient", next_attempt_at=NOW)
    assert st.due_deliveries(now=NOW + timedelta(minutes=1), channel="web_push") == []
    assert len(st.due_deliveries(now=NOW + timedelta(minutes=1), channel="email")) == 1


def test_a_backlog_drains_oldest_first(st):
    """A queue that serves its newest entries starves its own head — and the head is the work closest
    to the age bound, which is the work that will be lost if it waits."""
    uid = _reader(st)
    sid = _subscribe(st, uid)
    claims = []
    for i in range(3):
        nid = _notification(st, uid, key=f"ev:{i}")
        claim = st.claim_delivery(nid, sid, user_id=uid)
        st.record_delivery_result(claim, "transient", next_attempt_at=NOW - timedelta(minutes=3 - i))
        claims.append(claim)
    assert [r["id"] for r in st.due_deliveries(now=NOW)] == claims


def test_the_notification_body_is_re_read_rather_than_carried(st):
    """A retry belongs to a run that has ended, possibly in another process. Re-reading is what makes
    it independent of the run that scheduled it."""
    uid = _reader(st)
    nid = _notification(st, uid)
    got = st.notification_by_id(nid)
    assert got["kind"] == "breaking_story" and got["id"] == nid
    assert got["payload"]["storyId"] == "st_1"
    assert st.notification_by_id(nid + 999) is None


def test_a_device_looked_up_by_id_carries_what_the_sender_needs(st):
    uid = _reader(st)
    sid = _subscribe(st, uid)
    got = st.push_subscription_by_id(sid)
    assert got["endpoint"] == "https://push.example/dev-1" and got["p256dh"] == "BPub"
    assert st.push_subscription_by_id(sid + 999) is None, "pruned between attempts is normal"


def test_a_b2_database_upgrades_in_place_without_rescheduling_its_history(tmp_path):
    """`create_all` creates NEW tables only, so a table that shipped in B2 keeps its B2 schema — and
    every ledger read would then fail with `no such column`, on the delivery path, taking push down
    entirely on exactly the databases that have rows in them.

    Built as a real pre-B3 table rather than simulated, because the thing under test is the ALTER."""
    import sqlalchemy as sa
    db = tmp_path / "legacy.sqlite"
    legacy = sa.create_engine(f"sqlite:///{db}")
    with legacy.begin() as c:
        c.execute(sa.text(
            "CREATE TABLE notification_deliveries ("
            " id INTEGER PRIMARY KEY, notification_id INTEGER, channel VARCHAR(32),"
            " subscription_id INTEGER, user_id INTEGER, status VARCHAR(16), status_code INTEGER,"
            " detail VARCHAR(255), attempted_at DATETIME, completed_at DATETIME)"))
        c.execute(sa.text(
            "INSERT INTO notification_deliveries"
            " (notification_id, channel, subscription_id, user_id, status, attempted_at,"
            "  completed_at)"
            " VALUES (1,'web_push',1,1,'transient','2026-07-30 11:00:00.000000',"
            "         '2026-07-30 11:00:01.000000')"))
    legacy.dispose()

    upgraded = store_mod.Store(f"sqlite:///{db}")     # the migration runs in the constructor
    row = upgraded.delivery_attempts(notification_id=1)[0]
    assert row["attempts"] == 1, "a B2 row was claimed exactly once"
    assert row["nextAttemptAt"] is None
    assert upgraded.due_deliveries(now=NOW + timedelta(days=365)) == [], "settled stays settled"
    assert store_mod.Store(f"sqlite:///{db}") is not None, "and the migration is idempotent"


# --------------------------------------------------------------------------------------------- #
# The worker's ladder, end to end.
# --------------------------------------------------------------------------------------------- #
class _Scripted:
    """Answers with one scripted result per call, then repeats the last."""

    def __init__(self, *results):
        self.calls = []
        self._results = list(results)

    def send(self, subscription, data):
        self.calls.append(subscription["endpoint"])
        return self._results.pop(0) if len(self._results) > 1 else self._results[0]


def _event(st, source_id="st_1", *, hours=6):
    st.record_notification_event(
        "story_breaking", source_id, category="breaking",
        payload={"storyId": source_id, "title": "Something broke", "publisherCount": 4},
        occurred_at=(NOW - timedelta(minutes=5)).isoformat(),
        expires_at=(NOW + timedelta(hours=hours)).isoformat())


def _logs():
    seen = []
    return seen, (lambda level, event, **fields: seen.append((event, fields)))


TRANSIENT = push_sender.SendResult(push_sender.TRANSIENT, 503, "http_503")
SUCCESS = push_sender.SendResult(push_sender.SUCCESS, 201)
PERMANENT = push_sender.SendResult(push_sender.PERMANENT, 400, "http_400")
GONE = push_sender.SendResult(push_sender.EXPIRED, 410, "http_410")
TIMEOUT = push_sender.SendResult(push_sender.TIMEOUT, None, "timeout")


def test_a_transient_failure_schedules_a_second_attempt(st):
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st)
    seen, log = _logs()

    stats = push_delivery.run_once(st, now=NOW, sender=_Scripted(TRANSIENT), log=log)
    assert stats.failed == 1 and stats.scheduled == 1 and stats.exhausted == 0
    row = st.delivery_attempts(user_id=uid)[0]
    assert row["status"] == "transient" and row["attempts"] == 1
    assert row["nextAttemptAt"] is not None and row["completedAt"] is None
    assert any(e == "push_retry_scheduled" for e, _ in seen)


def test_the_scheduled_attempt_is_made_and_can_succeed(st):
    """The whole point of the phase: a delivery that failed once reaches the device on the next
    cycle, with no queue in memory and nothing carried between runs but the ledger row."""
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st)
    sender = _Scripted(TRANSIENT, SUCCESS)

    first = push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None)
    assert first.sent == 0 and first.scheduled == 1

    later = NOW + timedelta(minutes=10)
    second = push_delivery.run_once(st, now=later, sender=sender, log=lambda *a, **k: None)
    assert second.sent == 1 and second.retried == 1
    assert len(sender.calls) == 2
    row = st.delivery_attempts(user_id=uid)[0]
    assert row["status"] == "success" and row["attempts"] == 2 and row["nextAttemptAt"] is None


def test_a_permanent_failure_is_never_retried(st):
    """Retrying an unchanged request that was rejected for being wrong can only produce the same
    answer, more often — and each one is a defect on our side that a retry would obscure."""
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st)
    sender = _Scripted(PERMANENT)

    stats = push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None)
    assert stats.failed == 1 and stats.scheduled == 0
    assert st.delivery_attempts(user_id=uid)[0]["nextAttemptAt"] is None
    push_delivery.run_once(st, now=NOW + timedelta(hours=1), sender=sender,
                           log=lambda *a, **k: None)
    assert len(sender.calls) == 1


def test_the_ladder_stops_after_the_attempt_budget_and_says_why(st):
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st, hours=24)
    sender = _Scripted(TIMEOUT)
    seen, log = _logs()

    moment = NOW
    for _ in range(push_retry.MAX_ATTEMPTS + 2):
        push_delivery.run_once(st, now=moment, sender=sender, log=log)
        moment += timedelta(minutes=20)

    assert len(sender.calls) == push_retry.MAX_ATTEMPTS, "bounded, and bounded at the documented value"
    row = st.delivery_attempts(user_id=uid)[0]
    assert row["attempts"] == push_retry.MAX_ATTEMPTS and row["nextAttemptAt"] is None
    exhausted = [f for e, f in seen if e == "push_retry_exhausted"]
    assert exhausted and exhausted[0]["reason"] == "attempts"


def test_a_410_on_a_retry_still_prunes_the_device(st):
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st)
    sender = _Scripted(TRANSIENT, GONE)

    push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None)
    stats = push_delivery.run_once(st, now=NOW + timedelta(minutes=10), sender=sender,
                                   log=lambda *a, **k: None)
    assert stats.pruned == 1
    assert st.list_push_subscriptions(uid) == []


def test_a_retry_after_header_is_honoured_over_our_own_backoff(st):
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st, hours=24)
    throttled = push_sender.SendResult(push_sender.TRANSIENT, 429, "http_429", retry_after="1800")

    push_delivery.run_once(st, now=NOW, sender=_Scripted(throttled), log=lambda *a, **k: None)
    scheduled = st.delivery_attempts(user_id=uid)[0]["nextAttemptAt"]
    assert scheduled >= (NOW + timedelta(seconds=1800)).replace(tzinfo=None)


def test_a_device_unregistered_between_attempts_ends_the_ladder(st):
    """Not an error: a reader turning push off on that device is the system working."""
    uid = _reader(st)
    sid = _subscribe(st, uid)
    _event(st)
    sender = _Scripted(TRANSIENT, SUCCESS)
    push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None)

    st.delete_push_subscription(uid, "https://push.example/dev-1")
    seen, log = _logs()
    stats = push_delivery.run_once(st, now=NOW + timedelta(minutes=10), sender=sender, log=log)
    assert stats.abandoned == 1 and len(sender.calls) == 1
    reasons = [f["reason"] for e, f in seen if e == "push_retry_abandoned"]
    assert reasons == ["subscription_gone"]


def test_consent_withdrawn_between_attempts_ends_the_ladder(st):
    """The one place "we already decided this" is not good enough: the decision is the reader's, and
    they have since changed it."""
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st)
    sender = _Scripted(TRANSIENT, SUCCESS)
    push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None)

    ss.update(st, uid, {"notifications": {"categories": {"breaking": {"push": False}}}})
    seen, log = _logs()
    stats = push_delivery.run_once(st, now=NOW + timedelta(minutes=10), sender=sender, log=log)
    assert stats.abandoned == 1 and len(sender.calls) == 1
    assert [f["reason"] for e, f in seen if e == "push_retry_abandoned"] == ["consent_withdrawn"]


def test_a_delivery_that_outlives_its_usefulness_is_abandoned_unsent(st):
    """Four hours late, "breaking news" describes something that has stopped being true — and the
    reader cannot tell that from the lock screen."""
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st, hours=48)
    sender = _Scripted(TRANSIENT, SUCCESS)
    push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None)

    much_later = NOW + timedelta(seconds=push_retry.MAX_DELIVERY_AGE_SECONDS + 600)
    seen, log = _logs()
    stats = push_delivery.run_once(st, now=much_later, sender=sender, log=log)
    assert stats.abandoned == 1 and len(sender.calls) == 1, "never sent"
    assert [f["reason"] for e, f in seen if e == "push_retry_abandoned"] == ["age"]


def test_a_run_interrupted_mid_send_is_recovered_by_a_later_one(st):
    """Restart safety, from the outside: a row claimed and never resolved is exactly what a container
    restart during a fan-out leaves behind, and B2 lost those silently."""
    uid = _reader(st)
    sid, nid = _subscribe(st, uid), _notification(st, uid)
    _event(st, hours=24)
    st.claim_delivery(nid, sid, user_id=uid, now=NOW)   # the "crashed" run, claimed at NOW

    sender = _Scripted(SUCCESS)
    seen, log = _logs()
    later = NOW + timedelta(seconds=push_retry.LEASE_SECONDS + 60)
    stats = push_delivery.run_once(st, now=later, sender=sender, log=log)

    assert stats.recovered == 1 and stats.sent >= 1
    assert any(e == "push_delivery_recovered" for e, _ in seen)


def test_a_fresh_run_does_not_re_send_a_notification_the_ladder_still_owns(st):
    """The claim is the arbiter for both paths. Without that, the same event would be planned fresh on
    every cycle while its retry was still pending, and the reader would get it twice."""
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st, hours=24)
    sender = _Scripted(TRANSIENT, SUCCESS)
    push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None)

    # Ten seconds later the backoff has not elapsed (it is at least half of BASE_SECONDS), so the
    # ladder still owns the row and the fresh planner must not step around it.
    stats = push_delivery.run_once(st, now=NOW + timedelta(seconds=10), sender=sender,
                                   log=lambda *a, **k: None)
    assert stats.considered == 0 and stats.skipped == 1
    assert len(sender.calls) == 1


def test_the_ladder_survives_a_process_restart(st):
    """The strongest form of the claim, and the reason `next_attempt_at` is a column: a brand-new
    module state — no timers, no queue, nothing carried over — finds the work waiting in the ledger."""
    import importlib
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st, hours=24)
    push_delivery.run_once(st, now=NOW, sender=_Scripted(TRANSIENT), log=lambda *a, **k: None)

    reloaded = importlib.reload(push_delivery)
    sender = _Scripted(SUCCESS)
    stats = reloaded.run_once(st, now=NOW + timedelta(minutes=10), sender=sender,
                              log=lambda *a, **k: None)
    assert stats.sent == 1 and stats.retried == 1
    importlib.reload(push_delivery)          # leave the module as the rest of the suite expects it


def test_a_ledger_the_scan_cannot_read_does_not_end_the_run(st, monkeypatch):
    """Fail-open on a read, matching the platform's rule elsewhere. The fresh fan-out is independent
    of the retry scan and must still happen."""
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st)
    monkeypatch.setattr(st, "due_deliveries",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("db hiccup")))
    seen, log = _logs()
    stats = push_delivery.run_once(st, now=NOW, sender=_Scripted(SUCCESS), log=log)
    assert stats.sent == 1
    assert any(e == "push_retry_scan_failed" for e, _ in seen)


def test_a_failure_near_the_age_bound_is_not_rescheduled_past_it(st):
    """The scheduling half of the age bound, distinct from the abandon-before-sending half: this
    delivery IS attempted, fails, and must then be closed rather than parked for a deadline it cannot
    make."""
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st, hours=48)
    sender = _Scripted(TRANSIENT)
    push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None)

    # Just inside the bound: the retry is planned and sent, and only the RESCHEDULE hits the wall.
    nearly = NOW + timedelta(seconds=push_retry.MAX_DELIVERY_AGE_SECONDS - 5)
    seen, log = _logs()
    stats = push_delivery.run_once(st, now=nearly, sender=sender, log=log)
    assert stats.retried == 1 and len(sender.calls) == 2, "it was attempted"
    assert stats.exhausted == 1 and stats.scheduled == 0
    assert [f["reason"] for e, f in seen if e == "push_retry_exhausted"] == ["age"]
    assert st.delivery_attempts(user_id=uid)[0]["nextAttemptAt"] is None


def test_unreadable_settings_end_the_ladder_rather_than_delivering_anyway(st, monkeypatch):
    """Fail-closed on consent, on the retry path as much as the first. Settings we cannot read are
    not consent — the alternative is delivering to a reader whose preference we could not check."""
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st, hours=24)
    sender = _Scripted(TRANSIENT, SUCCESS)
    push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None)

    monkeypatch.setattr(push_delivery.settings_service, "get",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("settings unreadable")))
    seen, log = _logs()
    stats = push_delivery.run_once(st, now=NOW + timedelta(minutes=10), sender=sender, log=log)
    assert stats.abandoned == 1 and len(sender.calls) == 1
    assert [f["reason"] for e, f in seen if e == "push_retry_abandoned"] == ["consent_withdrawn"]


def test_a_retry_whose_notification_has_vanished_ends_the_ladder(st, monkeypatch):
    """A retry with nothing to render has nothing to send.

    Driven by making the lookup answer `None` rather than by deleting the row, because on SQLite with
    `foreign_keys=ON` the row **cannot** be deleted while a delivery names it — the guard this test
    covers is therefore defensive rather than reachable from the ORM. It is kept because the FK is a
    property of the schema and not of the code: a backend without enforcement, or a restore from a
    dump taken with it off, reaches this branch, and the alternative is a `None` dereference on a
    background thread."""
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st, hours=24)
    sender = _Scripted(TRANSIENT, SUCCESS)
    push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None)

    monkeypatch.setattr(st, "notification_by_id", lambda _nid: None)
    seen, log = _logs()
    stats = push_delivery.run_once(st, now=NOW + timedelta(minutes=10), sender=sender, log=log)
    assert stats.abandoned == 1 and len(sender.calls) == 1
    assert [f["reason"] for e, f in seen if e == "push_retry_abandoned"] == ["notification_gone"]


def test_pruning_a_readers_history_does_not_break_on_a_pushed_notification(st):
    """A real defect B2 shipped: `notification_deliveries.notification_id` is a foreign key with
    `PRAGMA foreign_keys=ON`, so deleting a notification a delivery names RAISES — and the caller is
    `prune_notifications`, which runs on the delivery boundary for every reader on every fetch. Once a
    reader passed the history cap with one pushed notification among the settled rows, their inbox
    would have started failing outright."""
    uid = _reader(st)
    sid = _subscribe(st, uid)
    for i in range(12):
        nid = _notification(st, uid, key=f"ev:hist-{i}")
        st.mark_notification_seen(uid, nid)
        if i < 3:
            st.claim_delivery(nid, sid, user_id=uid, now=NOW)

    assert st.prune_notifications(uid, keep=5) == 7, "the settled overflow is dropped"
    assert len(st.delivery_attempts(user_id=uid)) == 0, "and its ledger goes with it"
    assert len(st.list_notifications(uid)) == 5


def test_losing_the_lease_race_means_backing_off_not_sending(st, monkeypatch):
    """Two workers can read the same due row. The compare-and-set decides which one sends; a caller
    that ignored its answer would turn every race into a duplicate notification."""
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st, hours=24)
    sender = _Scripted(TRANSIENT, SUCCESS)
    push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None)

    monkeypatch.setattr(st, "lease_delivery", lambda *a, **k: False)   # the other worker won
    stats = push_delivery.run_once(st, now=NOW + timedelta(minutes=10), sender=sender,
                                   log=lambda *a, **k: None)
    assert stats.retried == 0 and stats.considered == 0
    assert len(sender.calls) == 1, "no second send"


def test_one_unrecordable_result_does_not_lose_the_others(st, monkeypatch):
    """The sends have already happened by the time recording starts. Losing the rest of the batch to
    one failed write would leave devices that were reached with no ledger row saying so — and the
    lease would then send to them all over again."""
    uid = _reader(st)
    _subscribe(st, uid, "https://push.example/a")
    _subscribe(st, uid, "https://push.example/b")
    _event(st)
    real = st.record_delivery_result
    calls = {"n": 0}

    def flaky(delivery_id, status, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("write failed")
        return real(delivery_id, status, **kw)
    monkeypatch.setattr(st, "record_delivery_result", flaky)

    seen, log = _logs()
    stats = push_delivery.run_once(st, now=NOW, sender=_Scripted(SUCCESS), log=log)
    assert stats.considered == 2 and stats.sent == 1, "the second was still recorded"
    assert any(e == "push_record_failed" for e, _ in seen)


def test_retries_are_planned_before_fresh_sends(st):
    """A run that runs out of time should have spent it on the work that expires soonest."""
    uid = _reader(st)
    _subscribe(st, uid, "https://push.example/dev-1")
    _event(st, "st_old", hours=24)
    push_delivery.run_once(st, now=NOW, sender=_Scripted(TRANSIENT), log=lambda *a, **k: None)

    later = NOW + timedelta(minutes=10)
    st.record_notification_event(
        "story_breaking", "st_new", category="breaking",
        payload={"storyId": "st_new", "title": "Newer thing", "publisherCount": 4},
        occurred_at=(later - timedelta(minutes=1)).isoformat(),
        expires_at=(later + timedelta(hours=6)).isoformat())

    seen, log = _logs()
    push_delivery.run_once(st, now=later, sender=_Scripted(SUCCESS), log=log)
    started = [f["notificationId"] for e, f in seen if e == "push_send_started"]
    assert len(started) == 2 and started[0] < started[1], "the older delivery goes first"
