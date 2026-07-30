"""Tests for the B4 operational surface — rate limiting, metrics, graceful shutdown, and what a
restart leaves behind.

Nothing here is about whether a notification is correct; B2 and B3 own that. These are the properties
an operator relies on when something is already going wrong: that the fan-out cannot flood a push
service, that a failure is countable by its classification rather than as one undifferentiated
"failures" number, that stopping the container does not lose more than it has to, and that what a
dead process left behind is visible at the moment it is caused rather than fifteen minutes later.
"""

import pathlib
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import obs_metrics                  # noqa: E402
import push_delivery                # noqa: E402
import push_metrics                 # noqa: E402
import push_ratelimit               # noqa: E402
import push_retry                   # noqa: E402
import push_sender                  # noqa: E402
import settings_service as ss       # noqa: E402
import store as store_mod           # noqa: E402

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------------------------- #
# Rate limiting — the proactive half. `Retry-After` is the reactive half (B3).
# --------------------------------------------------------------------------------------------- #
class _Clock:
    """A monotonic clock the test advances by hand, so a rate limit can be exercised without any
    real time passing."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


def test_the_budget_is_per_push_service_not_global():
    """Endpoints belong to a handful of independent operators. A global limit throttles Firefox
    because Chrome is slow, which punishes the wrong readers for someone else's bad day."""
    clock = _Clock()
    limiter = push_ratelimit.RateLimiter(rate=1.0, burst=1.0, monotonic=clock)
    assert limiter.acquire_delay("https://fcm.googleapis.com/fcm/send/abc") == 0.0
    assert limiter.acquire_delay("https://updates.push.services.mozilla.com/wpush/v2/xyz") == 0.0, \
        "a different service has its own budget"
    assert limiter.acquire_delay("https://fcm.googleapis.com/fcm/send/def") > 0.0, \
        "the first service's is spent"


def test_idle_time_pays_for_a_burst():
    """Fan-outs are bursty by nature: an event produces every send it will ever produce in one cycle,
    then nothing for an hour. A fixed minimum gap would stretch a burst the service could absorb."""
    clock = _Clock()
    limiter = push_ratelimit.RateLimiter(rate=10.0, burst=5.0, monotonic=clock)
    for _ in range(5):
        assert limiter.acquire_delay("https://fcm.googleapis.com/x") == 0.0
    assert limiter.acquire_delay("https://fcm.googleapis.com/x") > 0.0, "the bucket is empty"
    clock.advance(1.0)                       # ten tokens' worth, capped at the burst
    for _ in range(5):
        assert limiter.acquire_delay("https://fcm.googleapis.com/x") == 0.0


def test_the_bucket_never_banks_more_than_its_burst():
    """A day of silence must not buy a day's worth of sends in one second — which is the flood the
    limiter exists to prevent, arriving by a different route.

    The bucket has to be TOUCHED before the clock is advanced: it is created lazily with `last = now`,
    so advancing first would leave nothing for the refill to measure and the test would pass on a
    limiter with no cap at all."""
    clock = _Clock()
    limiter = push_ratelimit.RateLimiter(rate=10.0, burst=3.0, monotonic=clock)
    limiter.acquire_delay("https://fcm.googleapis.com/x")        # create the bucket at t0
    clock.advance(86400)
    allowed = sum(1 for _ in range(50) if limiter.acquire_delay("https://fcm.googleapis.com/x") == 0)
    assert allowed == 3


def test_a_rate_of_zero_is_no_limiter_at_all():
    """The documented meaning of `0`. Constructed directly rather than through `from_env`, which
    never builds one — so without this the contract holds only by never being exercised, and the
    first caller to build one would divide by it."""
    limiter = push_ratelimit.RateLimiter(rate=0.0)
    assert limiter.enabled is False
    assert limiter.acquire_delay("https://fcm.googleapis.com/x") == 0.0
    for _ in range(100):
        assert limiter.acquire_delay("https://fcm.googleapis.com/x") == 0.0


def test_concurrent_callers_queue_rather_than_all_being_told_the_same_wait():
    """The send phase is a pool sharing one budget per host. If each waiter were told the same delay
    they would all wake together and send together, which reproduces the burst."""
    clock = _Clock()
    limiter = push_ratelimit.RateLimiter(rate=1.0, burst=1.0, monotonic=clock)
    limiter.acquire_delay("https://fcm.googleapis.com/x")          # spend the only token
    first = limiter.acquire_delay("https://fcm.googleapis.com/x")
    second = limiter.acquire_delay("https://fcm.googleapis.com/x")
    assert second > first, "each caller waits behind the one before it"


@pytest.mark.parametrize("endpoint,host", [
    ("https://fcm.googleapis.com/fcm/send/abc", "fcm.googleapis.com"),
    ("https://FCM.GoogleAPIs.com/x", "fcm.googleapis.com"),
    ("not a url", ""),
    ("", ""),
    (None, ""),
])
def test_the_service_is_identified_by_host_alone(endpoint, host):
    assert push_ratelimit.host_of(endpoint) == host


def test_the_limiter_is_on_by_default_and_switchable_off():
    """On by default because the failure it prevents is silent and gradual — a fan-out that trips a
    push service's own limit turns every send into a retry, and nothing says so. `0` is the escape
    hatch for an operator who has decided the limit is the problem."""
    assert push_ratelimit.from_env({}).enabled is True
    assert push_ratelimit.from_env({"RWE_PUSH_MAX_SENDS_PER_SECOND": "25"}).rate == 25.0
    assert push_ratelimit.from_env({"RWE_PUSH_MAX_SENDS_PER_SECOND": "0"}) is None
    assert push_ratelimit.from_env({"RWE_PUSH_MAX_SENDS_PER_SECOND": "  "}).enabled is True


def test_an_unparseable_rate_falls_back_rather_than_flooding():
    """A typo in a limit must not read as "no limit". Fail towards the safe side, which here is the
    side that does not hammer somebody else's service."""
    limiter = push_ratelimit.from_env({"RWE_PUSH_MAX_SENDS_PER_SECOND": "ten"})
    assert limiter is not None and limiter.rate == push_ratelimit.DEFAULT_RATE


def test_waiting_does_not_hold_the_lock():
    """The lock guards arithmetic only. Held across the sleep, four workers would serialise behind
    each other's waits and the pool would be a pool in name only."""
    limiter = push_ratelimit.RateLimiter(rate=1.0, burst=1.0)
    limiter.acquire_delay("https://a.example/x")
    entered = threading.Event()

    def slow_sleep(_seconds):
        entered.set()
        time.sleep(0.3)

    waiter = threading.Thread(target=lambda: limiter.wait("https://a.example/x", sleep=slow_sleep))
    waiter.start()
    assert entered.wait(2), "the waiter got as far as sleeping"
    t0 = time.perf_counter()
    limiter.acquire_delay("https://b.example/x")      # a different host, mid-wait
    assert time.perf_counter() - t0 < 0.2, "not blocked behind the sleeping thread"
    waiter.join(2)


# --------------------------------------------------------------------------------------------- #
# Metrics.
# --------------------------------------------------------------------------------------------- #
@pytest.fixture()
def metrics():
    obs_metrics.metrics().reset()
    push_metrics.initialize()
    yield push_metrics
    obs_metrics.metrics().reset()


def test_every_classification_has_a_counter_before_anything_has_failed(metrics):
    """A missing series and a zero series look identical at 3am and mean opposite things. Nobody can
    alert on `push_failed_permanent_total` rising if it does not exist until it rises."""
    snap = push_metrics.snapshot()
    for status in push_metrics.STATUSES:
        if status != push_sender.SUCCESS:
            assert f"push_failed_{status}_total" in snap
    assert snap["push_failed_permanent_total"] == 0


def test_failures_are_counted_by_classification_and_not_only_in_total(metrics):
    """§7's whole argument: a rising prune rate is ordinary attrition and a rising `permanent` rate is
    a credential defect we shipped. One "failures" number cannot tell them apart."""
    push_metrics.record_attempt(push_sender.SUCCESS)
    push_metrics.record_attempt(push_sender.EXPIRED)
    push_metrics.record_attempt(push_sender.PERMANENT)
    push_metrics.record_attempt(push_sender.PERMANENT)

    snap = push_metrics.snapshot()
    assert snap["push_attempted_total"] == 4
    assert snap["push_succeeded_total"] == 1
    assert snap["push_failed_total"] == 3
    assert snap["push_failed_permanent_total"] == 2
    assert snap["push_failed_expired_total"] == 1
    assert snap["push_failed_timeout_total"] == 0


def test_a_run_records_the_shape_of_the_fan_out_and_its_duration(metrics):
    stats = push_delivery.RunStats(considered=7, pruned=2, scheduled=3, exhausted=1,
                                   abandoned=4, recovered=5)
    push_metrics.record_run(stats, 1234.0)
    snap = push_metrics.snapshot()
    assert snap["push_runs_total"] == 1 and snap["push_considered_total"] == 7
    assert snap["push_pruned_total"] == 2 and snap["push_retries_scheduled_total"] == 3
    assert snap["push_retries_exhausted_total"] == 1
    assert snap["push_retries_abandoned_total"] == 4
    assert snap["push_deliveries_recovered_total"] == 5
    assert obs_metrics.snapshot()["timers"]["push_run_ms"]["count"] == 1


def test_metrics_are_never_worth_failing_a_send_over(metrics, monkeypatch):
    """Purely observational, matching `obs_metrics`' own rule. A collector that can break the thing
    it observes is worse than no collector."""
    monkeypatch.setattr(obs_metrics, "incr",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("collector down")))
    monkeypatch.setattr(obs_metrics, "observe",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("collector down")))
    push_metrics.record_attempt(push_sender.SUCCESS)          # must not raise
    push_metrics.record_run(push_delivery.RunStats(considered=1), 5.0)
    push_metrics.record_rate_limited(0.5)
    push_metrics.initialize()


def test_the_push_snapshot_is_a_view_of_the_application_one(metrics):
    """Derived from the same registry rather than kept alongside it, so the two can never disagree —
    and so an incident has one place to look, not two."""
    push_metrics.record_attempt(push_sender.SUCCESS)
    obs_metrics.incr("http_requests_total", 3)     # a neighbour in the same registry
    assert push_metrics.snapshot()["push_succeeded_total"] == \
        obs_metrics.snapshot()["counters"]["push_succeeded_total"]
    assert "http_requests_total" not in push_metrics.snapshot(), "a view, not the whole registry"
    assert all(k.startswith("push_") for k in push_metrics.snapshot())


def test_an_unreadable_registry_yields_an_empty_snapshot_rather_than_raising(metrics, monkeypatch):
    """The snapshot is read by an operator during an incident — the moment when a diagnostic that
    raises is worst. Empty says "nothing to report"; an exception says nothing at all."""
    monkeypatch.setattr(obs_metrics, "snapshot",
                        lambda: (_ for _ in ()).throw(RuntimeError("registry down")))
    assert push_metrics.snapshot() == {}


# --------------------------------------------------------------------------------------------- #
# The worker's operational behaviour.
# --------------------------------------------------------------------------------------------- #
@pytest.fixture()
def st():
    return store_mod.Store("sqlite://")


@pytest.fixture(autouse=True)
def _not_stopping():
    """Every test starts with a process that is running. `shutdown` sets a module-level flag and a
    test that left it set would silently disable every test after it."""
    push_delivery._stop.clear()
    yield
    push_delivery._stop.clear()


def _reader(st, account="ops-b4", *, push=True):
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
                                   "payload": {"storyId": "st_1", "title": "Something broke"}}])
    return st.notification_ids_by_dedupe_key(uid, [key])[key]


def _event(st, source_id="st_1", *, hours=6):
    st.record_notification_event(
        "story_breaking", source_id, category="breaking",
        payload={"storyId": source_id, "title": "Something broke", "publisherCount": 4},
        occurred_at=(NOW - timedelta(minutes=5)).isoformat(),
        expires_at=(NOW + timedelta(hours=hours)).isoformat())


def _logs():
    seen = []
    return seen, (lambda level, event, **fields: seen.append((event, fields)))


class _Sender:
    def __init__(self, result=None):
        self.calls = []
        self._result = result or push_sender.SendResult(push_sender.SUCCESS, 201)

    def send(self, subscription, data):
        self.calls.append(subscription["endpoint"])
        return self._result


def test_a_fan_out_respects_the_configured_rate(st, monkeypatch):
    """End to end: the limit is applied to real sends, and the wait is visible rather than just
    slow — a fan-out throttled by us looks exactly like one throttled by the push service, and the
    fixes are opposite."""
    uid = _reader(st)
    for i in range(6):
        _subscribe(st, uid, f"https://push.example/dev-{i}")
    _event(st)
    monkeypatch.setenv("RWE_PUSH_MAX_SENDS_PER_SECOND", "1000")   # fast, but on
    seen, log = _logs()
    sender = _Sender()

    stats = push_delivery.run_once(st, now=NOW, sender=sender, log=log)
    assert stats.sent == 6, "throttling delays sends, it never drops them"
    assert len(sender.calls) == 6


def test_the_rate_limit_can_be_switched_off_entirely(st, monkeypatch):
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st)
    monkeypatch.setenv("RWE_PUSH_MAX_SENDS_PER_SECOND", "0")
    seen, log = _logs()
    assert push_delivery.run_once(st, now=NOW, sender=_Sender(), log=log).sent == 1
    assert not any(e == "push_rate_limited" for e, _ in seen)


def test_a_throttled_send_is_logged_and_counted(st, monkeypatch):
    uid = _reader(st)
    for i in range(3):
        _subscribe(st, uid, f"https://push.example/dev-{i}")
    _event(st)
    monkeypatch.setenv("RWE_PUSH_MAX_SENDS_PER_SECOND", "1")      # one per second, burst of one
    obs_metrics.metrics().reset()
    push_metrics.initialize()
    seen, log = _logs()

    started = time.perf_counter()
    stats = push_delivery.run_once(st, now=NOW, sender=_Sender(), log=log)
    elapsed = time.perf_counter() - started

    assert stats.sent == 3
    assert elapsed >= 1.0, "the limit actually delayed the fan-out"
    throttled = [f for e, f in seen if e == "push_rate_limited"]
    assert throttled and throttled[0]["host"] == "push.example"
    assert push_metrics.snapshot()["push_rate_limited_total"] >= 1
    obs_metrics.metrics().reset()


# --------------------------------------------------------------------------------------------- #
# Startup and shutdown.
# --------------------------------------------------------------------------------------------- #
def test_a_real_fan_out_reaches_the_counters(st):
    """The unit tests above prove the recorders work; this proves they are actually called. A metric
    that is correct and never invoked reads as a permanent zero, which is the most misleading value a
    counter can have."""
    obs_metrics.metrics().reset()
    push_metrics.initialize()
    uid = _reader(st)
    _subscribe(st, uid, "https://push.example/a")
    _subscribe(st, uid, "https://push.example/b")
    _event(st)

    push_delivery.run_once(st, now=NOW, sender=_Sender(), log=lambda *a, **k: None)
    gone = _Sender(push_sender.SendResult(push_sender.EXPIRED, 410, "http_410"))
    st.record_notification_event(
        "story_breaking", "st_x", category="breaking",
        payload={"storyId": "st_x", "title": "Another", "publisherCount": 4},
        occurred_at=(NOW - timedelta(minutes=1)).isoformat(),
        expires_at=(NOW + timedelta(hours=6)).isoformat())
    push_delivery.run_once(st, now=NOW, sender=gone, log=lambda *a, **k: None)

    snap = push_metrics.snapshot()
    assert snap["push_runs_total"] == 2
    assert snap["push_succeeded_total"] == 2
    assert snap["push_failed_expired_total"] == 2 and snap["push_pruned_total"] == 2
    assert snap["push_considered_total"] == 4
    assert obs_metrics.snapshot()["timers"]["push_run_ms"]["count"] == 2, "fan-out duration, by name"
    obs_metrics.metrics().reset()


def test_startup_reports_what_the_last_process_left_behind(st):
    """A restart mid-fan-out leaves claimed-but-unresolved rows, and the lease recovers them silently
    fifteen minutes later. Counting them at startup is what turns "notifications were late after that
    deploy" from a mystery into a number visible at the moment it is caused."""
    uid = _reader(st)
    sid = _subscribe(st, uid)
    st.claim_delivery(_notification(st, uid, "ev:a"), sid, user_id=uid, now=NOW)
    open_claim = st.claim_delivery(_notification(st, uid, "ev:b"), sid, user_id=uid, now=NOW)
    # Scheduled beyond any wall clock the test could run under: `startup` reports against real time,
    # which is right for a startup report and means a simulated `now` would not agree with it.
    st.record_delivery_result(open_claim, "transient", next_attempt_at=NOW + timedelta(days=365))

    seen, log = _logs()
    summary = push_delivery.startup(st, log=log)
    assert summary == {"pending": 1, "scheduled": 1, "due": 0}
    assert any(e == "push_startup_backlog" for e, _ in seen)

    # `due` is the third count, and it is the one that says whether the backlog is work waiting or
    # work overdue. Asserted against an explicit clock, since that is the only way to pin it.
    assert st.delivery_backlog(now=NOW + timedelta(days=366))["due"] == 1


def test_startup_registers_the_metric_series(st):
    obs_metrics.metrics().reset()
    push_delivery.startup(st, log=lambda *a, **k: None)
    assert "push_failed_permanent_total" in push_metrics.snapshot()
    obs_metrics.metrics().reset()


def test_a_quiet_ledger_produces_no_startup_noise(st):
    seen, log = _logs()
    assert push_delivery.startup(st, log=log) == {"pending": 0, "scheduled": 0, "due": 0}
    assert not any(e == "push_startup_backlog" for e, _ in seen)


def test_startup_never_keeps_the_process_from_coming_up(st, monkeypatch):
    """A report is diagnostic. An engine that will not boot because it could not count its backlog is
    a worse outcome than one that boots without the count."""
    monkeypatch.setattr(st, "delivery_backlog",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("db not ready")))
    seen, log = _logs()
    assert push_delivery.startup(st, log=log) == {"pending": 0, "scheduled": 0}
    assert any(e == "push_startup_scan_failed" for e, _ in seen)


def test_shutdown_stops_a_run_between_waves_rather_than_mid_request(st):
    """A request already handed to a push service is allowed to finish and be recorded. Abandoning it
    would mean an outcome the ledger never learns, which is strictly worse than one more send."""
    uid = _reader(st)
    for i in range(12):
        _subscribe(st, uid, f"https://push.example/dev-{i}")
    _event(st)
    first_wave = threading.Event()

    class _Blocking(_Sender):
        def send(self, subscription, data):
            out = super().send(subscription, data)
            if len(self.calls) == push_delivery.MAX_WORKERS:
                first_wave.set()
                time.sleep(0.3)              # give shutdown time to land between waves
            return out

    sender = _Blocking()
    seen, log = _logs()
    runner = threading.Thread(target=lambda: push_delivery.run_once(st, now=NOW, sender=sender,
                                                                    log=log))
    runner.start()
    assert first_wave.wait(5)
    push_delivery._stop.set()
    runner.join(10)

    assert 0 < len(sender.calls) < 12, "stopped early, but the wave in flight completed"
    assert len(sender.calls) % push_delivery.MAX_WORKERS == 0, "a whole wave, never a partial one"
    assert any(e == "push_run_stopped" for e, _ in seen)


def test_a_stopping_process_starts_no_new_work(st, monkeypatch):
    """With a real reader and a real device, so "nothing was sent" means the stop flag stopped it and
    not that there was nothing to send — the two are indistinguishable on an empty store, which is
    how a test like this passes while proving nothing."""
    monkeypatch.setenv("RWE_PUSH_DELIVERY", "1")
    monkeypatch.setenv("RWE_VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setenv("RWE_VAPID_SUBJECT", "mailto:ops@example.com")
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st)
    assert push_delivery.run_once(st, now=NOW, sender=_Sender(),
                                  log=lambda *a, **k: None).sent == 1, "there is real work here"

    st.record_notification_event(
        "story_breaking", "st_2", category="breaking",
        payload={"storyId": "st_2", "title": "Another", "publisherCount": 4},
        occurred_at=(NOW - timedelta(minutes=1)).isoformat(),
        expires_at=(NOW + timedelta(hours=6)).isoformat())
    push_delivery._stop.set()

    assert push_delivery.request_delivery(st) is False
    sender = _Sender()
    assert push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None).considered == 0
    assert sender.calls == []


def test_shutdown_returns_promptly_when_nothing_is_running(st):
    assert push_delivery.shutdown(log=lambda *a, **k: None) is True


def test_shutdown_waits_for_a_run_but_not_forever(st, monkeypatch):
    """A container being stopped has its own clock — SIGKILL follows SIGTERM by ten seconds under
    Docker's defaults — so a shutdown that outruns it is not graceful, only late. What is left is an
    unresolved claim, which the lease already knows how to recover."""
    monkeypatch.setenv("RWE_PUSH_DELIVERY", "1")
    monkeypatch.setenv("RWE_VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setenv("RWE_VAPID_SUBJECT", "mailto:ops@example.com")
    release = threading.Event()
    monkeypatch.setattr(push_delivery, "run_once", lambda *a, **k: release.wait(5))

    assert push_delivery.request_delivery(st) is True
    seen, log = _logs()
    t0 = time.perf_counter()
    finished = push_delivery.shutdown(log=log, timeout=0.3)
    elapsed = time.perf_counter() - t0

    assert finished is False and 0.2 < elapsed < 2.0
    assert any(e == "push_shutdown_incomplete" for e, _ in seen)
    release.set()


def test_startup_clears_a_stop_left_by_a_previous_lifespan(st):
    """The flag is module state and the test suite — like a reloaded app — reuses the module. A new
    process that inherited a set flag would never deliver anything, and nothing would say why."""
    push_delivery._stop.set()
    push_delivery.startup(st, log=lambda *a, **k: None)
    assert push_delivery._stop.is_set() is False


def test_an_unfinished_run_leaves_work_the_lease_recovers(st):
    """The claim that makes an ungraceful stop survivable: what is left is late, not lost."""
    uid = _reader(st)
    sid = _subscribe(st, uid)
    st.claim_delivery(_notification(st, uid), sid, user_id=uid, now=NOW)   # "killed" mid-send

    later = NOW + timedelta(seconds=push_retry.LEASE_SECONDS + 60)
    assert len(st.due_deliveries(now=later)) == 1
    _event(st, hours=24)
    stats = push_delivery.run_once(st, now=later, sender=_Sender(), log=lambda *a, **k: None)
    assert stats.recovered == 1 and stats.sent >= 1
