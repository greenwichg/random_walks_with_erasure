"""Tests for the B2 push delivery pipeline — payload, sender classification, ledger, worker.

Nothing here touches a network or the `pywebpush` library: the transport is injected
(`push_sender.WebPushSender(transport=...)`), which is what lets every branch of the classification —
including the ones a real push service produces once a year — be driven deterministically. What that
does NOT cover is the pywebpush call itself; that is stated in the commit and in
`docs/BROWSER_PUSH_OPERATIONS.md` rather than implied away.

The contracts pinned here are `docs/BROWSER_PUSH_ARCHITECTURE.md` §5 (the payload) and §7 (the worker).
"""

import json
import pathlib
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import notification_service as ns   # noqa: E402
import push_delivery                # noqa: E402
import push_payload                 # noqa: E402
import push_sender                  # noqa: E402
import settings_service as ss       # noqa: E402
import store as store_mod           # noqa: E402


NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------------------------- #
# §5 — the payload. Frozen, so these are contract assertions rather than implementation details.
# --------------------------------------------------------------------------------------------- #
def _notification(**kw):
    base = {"id": 7, "kind": "breaking_story", "dedupe_key": "ev:42",
            "created_at": "2026-07-30T11:00:00+00:00",
            "payload": {"storyId": "st_9", "title": "Court issues ruling", "publisherCount": 5}}
    base.update(kw)
    return base


def test_the_payload_carries_exactly_the_frozen_fields():
    got = push_payload.build(_notification(), lang="es", sent_at="2026-07-30T12:00:00+00:00")
    assert set(got) == {"v", "notificationId", "kind", "payload", "dedupeKey",
                        "lang", "createdAt", "sentAt", "href"}
    assert got["v"] == push_payload.PAYLOAD_VERSION
    assert got["notificationId"] == 7 and got["kind"] == "breaking_story"
    assert got["dedupeKey"] == "ev:42" and got["lang"] == "es"
    assert got["createdAt"] == "2026-07-30T11:00:00+00:00"
    assert got["sentAt"] == "2026-07-30T12:00:00+00:00"


def test_the_payload_never_carries_title_or_body_keys():
    """§5, explicitly: they are METADATA the worker already holds, and sending them would create a
    second source of truth for the same mapping."""
    got = push_payload.build(_notification(), lang="en", sent_at="x")
    assert "titleKey" not in got and "bodyKey" not in got
    assert "titleKey" not in json.dumps(got)


def test_the_kinds_own_payload_travels_verbatim():
    got = push_payload.build(_notification(), lang="en", sent_at="x")
    assert got["payload"] == {"storyId": "st_9", "title": "Court issues ruling", "publisherCount": 5}


def test_href_deep_links_when_the_payload_names_a_story():
    got = push_payload.build(_notification(), lang="en", sent_at="x")
    assert got["href"] == "/stories/st_9"


def test_href_falls_back_to_the_kinds_page_then_the_root():
    """§6: every payload must be tappable, including for a worker that has never heard of the kind."""
    assert push_payload.href_for("breaking_story", {}) == "/stories"
    assert push_payload.href_for("breaking_story", {"storyId": "   "}) == "/stories"
    assert push_payload.href_for("breaking_story", {"storyId": 42}) == "/stories"
    assert push_payload.href_for("weekly_report", None) == "/report"
    assert push_payload.href_for("a_kind_from_the_future", {"storyId": "x"}) == "/"


def test_a_story_id_is_escaped_because_a_payload_is_data_not_a_path():
    assert push_payload.href_for("breaking_story", {"storyId": "a/b?c#d"}) == "/stories/a%2Fb%3Fc%23d"


def test_a_long_title_is_truncated_before_it_can_cost_the_send():
    """Exceeding the push service's size limit rejects the WHOLE send rather than truncating it, so
    the one field whose length we do not control is bounded here."""
    got = push_payload.build(_notification(payload={"storyId": "s", "title": "T" * 500}),
                             lang="en", sent_at="x")
    assert len(got["payload"]["title"]) == push_payload.MAX_TITLE_CHARS
    assert got["payload"]["title"].endswith("…")


def test_a_realistic_payload_is_far_inside_the_budget():
    size = push_payload.size_bytes(push_payload.build(_notification(), lang="en", sent_at="x"))
    assert size < push_payload.SOFT_LIMIT_BYTES


def test_the_schema_version_is_pinned_at_one():
    """§6 makes ``v`` a contract, not a build number: an old worker reads it to decide whether it can
    trust the shape. Bumping it silently is a breaking change to every device already in the field, so
    the constant is asserted literally rather than compared to itself."""
    assert push_payload.PAYLOAD_VERSION == 1
    assert push_payload.build(_notification(), lang="en", sent_at="x")["v"] == 1


def test_a_title_at_exactly_the_limit_is_left_alone():
    """The boundary, not just the far side of it: truncating at the limit rather than past it would
    mangle a title that fits, and every test with a 500-character title passes either way."""
    exact = "T" * push_payload.MAX_TITLE_CHARS
    got = push_payload.build(_notification(payload={"storyId": "s", "title": exact}),
                             lang="en", sent_at="x")
    assert got["payload"]["title"] == exact, "no ellipsis on a title that fits"
    over = push_payload.build(_notification(payload={"storyId": "s", "title": exact + "X"}),
                              lang="en", sent_at="x")
    assert over["payload"]["title"] != exact + "X" and over["payload"]["title"].endswith("…")


@pytest.mark.parametrize("payload", [None, "not-a-dict", 42, ["storyId", "st_1"]])
def test_a_deep_linked_kind_survives_a_payload_that_is_not_a_dict(payload):
    """Stored JSON may predate any given shape, and a `.get` on a string is an AttributeError raised
    inside a pool thread — the one place a crash is least visible."""
    assert push_payload.href_for("breaking_story", payload) == "/stories"


def test_the_wire_form_spends_no_bytes_on_whitespace():
    """Every byte counts against the push service's 4 KB limit, and JSON's default separators add two
    characters per field for nothing a machine reads."""
    encoded = push_payload.encode(push_payload.build(_notification(), lang="en", sent_at="x"))
    assert ", " not in encoded and '": ' not in encoded
    assert json.loads(encoded)["kind"] == "breaking_story", "still valid JSON"


def test_non_ascii_travels_as_itself_rather_than_as_escapes():
    """`\\u00e9` costs six bytes where `é` costs two. Escaping would inflate exactly the headlines the
    non-English catalogs exist for — the ones nearest the size limit."""
    got = push_payload.build(_notification(payload={"storyId": "s", "title": "Élection à Genève"}),
                             lang="fr", sent_at="x")
    encoded = push_payload.encode(got)
    assert "Élection à Genève" in encoded and "\\u" not in encoded


def test_the_size_check_counts_bytes_not_characters():
    """The limit a push service enforces is in bytes. A CJK headline is three bytes per character, so
    measuring in characters would under-report it by a factor of three."""
    got = push_payload.build(_notification(payload={"storyId": "s", "title": "速報" * 50}),
                             lang="en", sent_at="x")
    assert push_payload.size_bytes(got) > len(push_payload.encode(got))


def test_the_engines_deep_link_matches_the_web_metadata_table():
    """The two must agree or a push sends readers somewhere the inbox would not. Read from the web
    module rather than restated, so a change on either side fails here."""
    source = (ROOT / "web" / "lib" / "notification-kinds.ts").read_text(encoding="utf-8")
    assert 'deepLinkField: "storyId"' in source
    assert '"/stories/"' in source or "/stories/${" in source


# --------------------------------------------------------------------------------------------- #
# Failure classification — the judgement that makes the platform operable.
# --------------------------------------------------------------------------------------------- #
@pytest.mark.parametrize("code,expected", [
    (200, "success"), (201, "success"), (202, "success"),
    (404, "expired"), (410, "expired"),
    (429, "transient"), (500, "transient"), (502, "transient"), (503, "transient"), (504, "transient"),
    (400, "permanent"), (401, "permanent"), (403, "permanent"), (413, "permanent"),
])
def test_status_codes_classify_by_what_they_mean_operationally(code, expected):
    assert push_sender.classify_status(code) == expected


def test_an_unrecognised_status_is_permanent_not_transient():
    """Without retries the two cost the same delivery; WITH retries (a later phase) treating an unknown
    answer as retryable is how a sender starts hammering a service that is telling it no."""
    assert push_sender.classify_status(418) == "permanent"
    assert push_sender.classify_status(451) == "permanent"


def test_the_success_band_ends_before_the_redirects():
    """299 is the last success; 300 is a redirect, which means the push service did NOT accept the
    message. Counting it as delivered is a silent loss — the ledger would say `success` for something
    no device ever received."""
    assert push_sender.classify_status(299) == "success"
    assert push_sender.classify_status(300) == "permanent"
    assert push_sender.classify_status(199) == "permanent"


class _Timeout(Exception):
    pass


class _ConnectionError(Exception):
    pass


class _WebPushException(Exception):
    def __init__(self, response):
        super().__init__("boom")
        self.response = response


def test_a_timeout_is_its_own_outcome_not_a_failure():
    """Nothing is known to be wrong with the subscription — only that no answer arrived in time."""
    got = push_sender.classify_exception(_Timeout("read timed out"))
    assert got.status == "timeout" and got.detail == "timeout"


def test_an_unreachable_service_is_transient():
    assert push_sender.classify_exception(_ConnectionError("no route")).status == "transient"


def test_a_web_push_exception_is_classified_by_the_status_it_carries():
    gone = push_sender.classify_exception(_WebPushException(type("R", (), {"status_code": 410})()))
    assert gone.status == "expired" and gone.status_code == 410 and gone.subscription_gone
    too_big = push_sender.classify_exception(_WebPushException(type("R", (), {"status_code": 413})()))
    assert too_big.status == "permanent" and too_big.status_code == 413


def test_a_response_without_a_usable_status_falls_through_to_the_type_name():
    """pywebpush releases differ in what they attach: some carry a `requests` response, some a bare
    string, some nothing. A non-integer status must not be handed to `classify_status`, which would
    raise inside the one `except` clause meant to be the last word."""
    stringly = _WebPushException(type("R", (), {"status_code": "410"})())
    assert push_sender.classify_exception(stringly).status == "permanent"
    assert push_sender.classify_exception(stringly).status_code is None
    assert push_sender.classify_exception(_WebPushException(None)).status == "permanent"


def test_an_unknown_exception_is_permanent_and_names_only_its_type():
    got = push_sender.classify_exception(ValueError("secret-looking detail"))
    assert got.status == "permanent"
    assert "secret-looking" not in got.detail, "a detail lands in a log; it carries a type, not a body"


def test_the_sender_never_raises_whatever_the_transport_does():
    """It runs on a pool thread: an exception would be swallowed by the executor and the ledger row
    would stay `pending` with nobody the wiser."""
    def boom(*a, **k):
        raise _Timeout("nope")
    sender = push_sender.WebPushSender(private_key="k", subject="mailto:x", transport=boom)
    assert sender.send({"endpoint": "https://x", "p256dh": "a", "auth": "b"}, "{}").status == "timeout"


def test_the_sender_hands_the_transport_the_subscription_and_the_deadline():
    seen = {}

    def spy(info, data, private_key, subject, timeout):
        seen.update(info=info, data=data, key=private_key, subject=subject, timeout=timeout)
        return 201

    sender = push_sender.WebPushSender(private_key="PRIV", subject="mailto:ops", timeout=3.5,
                                       transport=spy)
    result = sender.send({"endpoint": "https://push.example/x", "p256dh": "PUB", "auth": "AUTH"},
                         '{"v":1}')
    assert result.ok and result.status_code == 201
    assert seen["info"] == {"endpoint": "https://push.example/x",
                            "keys": {"p256dh": "PUB", "auth": "AUTH"}}
    assert seen["timeout"] == 3.5 and seen["key"] == "PRIV" and seen["subject"] == "mailto:ops"


# --------------------------------------------------------------------------------------------- #
# The ledger — the third of the platform's four idempotency levels.
# --------------------------------------------------------------------------------------------- #
@pytest.fixture()
def st():
    return store_mod.Store("sqlite://")


def _reader(st, account="push-b2", *, push=True):
    uid = st.upsert_user_by_identity("dev", account).id
    ss.update(st, uid, {"notifications": {"categories": {"breaking": {"push": push,
                                                                     "inApp": True}}}})
    return uid


def _subscribe(st, uid, endpoint="https://push.example/dev-1"):
    cats = ss.get(st, uid)["notifications"]["categories"]
    return st.upsert_push_subscription(uid, endpoint, p256dh="BPub", auth="Auth",
                                       categories=cats)["id"]


def _record_notification(st, uid, key="ev:1"):
    st.record_notifications(uid, [{"kind": "breaking_story", "dedupe_key": key,
                                   "created_at": NOW.isoformat(),
                                   "title_key": "notifications.breaking_story.title",
                                   "payload": {"storyId": "st_1", "title": "Something broke"},
                                   "gated_by": "notifications.categories.breaking.push"}])
    return st.notification_ids_by_dedupe_key(uid, [key])[key]


def test_a_delivery_is_claimed_once_and_only_once(st):
    """The claim is what stops every poll cycle re-sending every unexpired notification to every
    device — the worker runs on a cycle, not on a request."""
    uid = _reader(st)
    sid, nid = _subscribe(st, uid), _record_notification(st, uid)
    first = st.claim_delivery(nid, sid, user_id=uid)
    assert first is not None
    assert st.claim_delivery(nid, sid, user_id=uid) is None, "the second cycle finds it taken"


def test_the_same_notification_may_be_claimed_for_each_device(st):
    """A subscription is a device, so two devices are two deliveries of one notification."""
    uid = _reader(st)
    a = _subscribe(st, uid, "https://push.example/laptop")
    b = _subscribe(st, uid, "https://push.example/phone")
    nid = _record_notification(st, uid)
    assert st.claim_delivery(nid, a, user_id=uid) is not None
    assert st.claim_delivery(nid, b, user_id=uid) is not None


def test_a_result_resolves_the_claim_and_is_readable_back(st):
    uid = _reader(st)
    sid, nid = _subscribe(st, uid), _record_notification(st, uid)
    claim = st.claim_delivery(nid, sid, user_id=uid)
    assert st.record_delivery_result(claim, "expired", status_code=410, detail="http_410") is True

    row = st.delivery_attempts(notification_id=nid)[0]
    assert row["status"] == "expired" and row["statusCode"] == 410
    assert row["notificationId"] == nid and row["subscriptionId"] == sid, "correlatable both ways"
    assert row["completedAt"] is not None


def test_a_claim_is_never_released_by_a_failure(st):
    """Deliberate: an unresolved or failed claim means "we do not know whether the reader saw this",
    and re-sending on that basis is how a platform starts duplicating itself. Without retry
    scheduling, a transient failure is therefore a lost delivery — recorded, not repeated."""
    uid = _reader(st)
    sid, nid = _subscribe(st, uid), _record_notification(st, uid)
    claim = st.claim_delivery(nid, sid, user_id=uid)
    st.record_delivery_result(claim, "transient", status_code=503)
    assert st.claim_delivery(nid, sid, user_id=uid) is None


def test_the_ledger_outlives_the_subscription_it_names(st):
    """`subscription_id` is not a foreign key: a pruned device must not erase the record that we tried
    to reach it."""
    uid = _reader(st)
    sid, nid = _subscribe(st, uid), _record_notification(st, uid)
    claim = st.claim_delivery(nid, sid, user_id=uid)
    st.record_delivery_result(claim, "expired", status_code=410)
    assert st.delete_push_subscription_by_id(sid) == "https://push.example/dev-1"
    assert st.delivery_attempts(notification_id=nid)[0]["subscriptionId"] == sid


def test_the_candidate_query_selects_by_category_flag(st):
    on, off = _reader(st, "wants-push", push=True), _reader(st, "no-push", push=False)
    _subscribe(st, on, "https://push.example/on")
    _subscribe(st, off, "https://push.example/off")
    found = st.push_subscriptions_for_category("breaking")
    assert [s["userId"] for s in found] == [on]
    assert found[0]["p256dh"] == "BPub", "the sender needs the device's address"
    assert st.push_subscriptions_for_category("not_a_category") == []


# --------------------------------------------------------------------------------------------- #
# The worker.
# --------------------------------------------------------------------------------------------- #
class _FakeSender:
    """Records what it was asked to send and answers with a scripted result."""

    def __init__(self, result=None, results=None):
        self.calls = []
        self._result = result or push_sender.SendResult(push_sender.SUCCESS, 201)
        self._results = list(results or [])

    def send(self, subscription, data):
        self.calls.append((subscription["endpoint"], json.loads(data)))
        return self._results.pop(0) if self._results else self._result


def _event(st, source_id="st_1", *, title="Something broke", hours=1):
    st.record_notification_event(
        "story_breaking", source_id, category="breaking",
        payload={"storyId": source_id, "title": title, "publisherCount": 4},
        occurred_at=(NOW - timedelta(minutes=5)).isoformat(),
        expires_at=(NOW + timedelta(hours=hours)).isoformat())


def _logs():
    seen = []
    return seen, (lambda level, event, **fields: seen.append((event, fields)))


def test_an_event_reaches_a_subscribed_readers_device(st):
    """The whole pipeline, end to end: an event, a reader who opted in on the push channel, and one
    encrypted payload handed to the transport."""
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st)
    sender = _FakeSender()

    stats = push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None)
    assert stats.sent == 1 and stats.failed == 0
    endpoint, payload = sender.calls[0]
    assert endpoint == "https://push.example/dev-1"
    assert payload["kind"] == "breaking_story" and payload["href"] == "/stories/st_1"
    assert payload["dedupeKey"].startswith("ev:")


def test_a_reader_who_did_not_opt_in_on_the_push_channel_gets_nothing(st):
    """Consent is per channel. The denormalised flag narrows the candidate set; `gate_path` decides."""
    uid = _reader(st, push=False)
    _subscribe(st, uid)
    _event(st)
    sender = _FakeSender()
    assert push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None).sent == 0
    assert sender.calls == []


def test_a_stale_mirror_can_cost_a_candidate_but_never_an_unconsented_send(st):
    """The accelerator is not the authority (§7). Flip the flag on directly, leaving settings saying
    no, and the send must still not happen."""
    uid = _reader(st, push=False)
    sid = _subscribe(st, uid)
    with st.session() as s:
        s.get(store_mod.PushSubscription, sid).push_breaking = True
    _event(st)
    sender = _FakeSender()
    push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None)
    assert sender.calls == [], "settings decide, not the mirror"


def test_a_second_run_does_not_re_send(st):
    """The worker runs every poll cycle over the same unexpired events. Without the claim, every cycle
    would re-send."""
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st)
    sender = _FakeSender()
    first = push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None)
    second = push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None)
    assert first.sent == 1 and second.sent == 0 and second.skipped == 1
    assert len(sender.calls) == 1


def test_a_410_prunes_the_device_immediately(st):
    """A dead endpoint left in place is attempted on every future fan-out, and its failures crowd out
    the ones that mean something."""
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st)
    sender = _FakeSender(result=push_sender.SendResult(push_sender.EXPIRED, 410, "http_410"))
    seen, log = _logs()

    stats = push_delivery.run_once(st, now=NOW, sender=sender, log=log)
    assert stats.pruned == 1 and stats.failed == 1
    assert st.list_push_subscriptions(uid) == [], "the device is gone"
    assert any(e == "push_subscription_pruned" for e, _ in seen)


def test_a_transient_failure_leaves_the_device_alone(st):
    """The subscription is fine; the service is unwell. Pruning here would punish the reader for the
    push service's bad day."""
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st)
    sender = _FakeSender(result=push_sender.SendResult(push_sender.TRANSIENT, 503, "http_503"))
    stats = push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None)
    assert stats.failed == 1 and stats.pruned == 0
    assert len(st.list_push_subscriptions(uid)) == 1
    assert st.delivery_attempts(user_id=uid)[0]["status"] == "transient"


def test_a_timeout_is_recorded_as_a_timeout(st):
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st)
    sender = _FakeSender(result=push_sender.SendResult(push_sender.TIMEOUT, None, "timeout"))
    seen, log = _logs()
    push_delivery.run_once(st, now=NOW, sender=sender, log=log)
    assert st.delivery_attempts(user_id=uid)[0]["status"] == "timeout"
    assert any(e == "push_send_timeout" for e, _ in seen)


def test_every_send_is_logged_from_start_to_outcome(st):
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st)
    seen, log = _logs()
    push_delivery.run_once(st, now=NOW, sender=_FakeSender(), log=log)
    events = [e for e, _ in seen]
    assert "push_send_started" in events and "push_send_succeeded" in events
    assert "push_run_complete" in events
    started = dict(next(f for e, f in seen if e == "push_send_started"))
    assert started["notificationId"] and started["subscriptionId"]


def test_one_readers_failure_does_not_end_the_fan_out(st):
    """A pool of sends over many readers: the second must be attempted even if the first blows up."""
    a, b = _reader(st, "reader-a"), _reader(st, "reader-b")
    _subscribe(st, a, "https://push.example/a")
    _subscribe(st, b, "https://push.example/b")
    _event(st)

    class _Flaky(_FakeSender):
        def send(self, subscription, data):
            if subscription["endpoint"].endswith("/a"):
                raise RuntimeError("kaboom")
            return super().send(subscription, data)

    sender = _Flaky()
    stats = push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None)
    assert stats.sent == 1 and stats.failed == 1
    assert [e for e, _ in sender.calls] == ["https://push.example/b"]


def test_multiple_devices_of_one_reader_each_receive_it(st):
    uid = _reader(st)
    _subscribe(st, uid, "https://push.example/laptop")
    _subscribe(st, uid, "https://push.example/phone")
    _event(st)
    sender = _FakeSender()
    assert push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None).sent == 2
    assert {e for e, _ in sender.calls} == {"https://push.example/laptop",
                                            "https://push.example/phone"}


def test_an_expired_event_is_not_delivered(st):
    """A three-day-old "breaking" is not breaking, and the per-event TTL is what says so."""
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st, hours=-1)                     # already expired at NOW
    sender = _FakeSender()
    assert push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None).sent == 0


def test_no_events_means_no_work_at_all(st):
    uid = _reader(st)
    _subscribe(st, uid)
    sender = _FakeSender()
    stats = push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None)
    assert stats.considered == 0 and sender.calls == []


def test_only_event_driven_kinds_are_pushed(st):
    """B2 delivers what `notification_events` produced. The six kinds that predate channels answer the
    same on every channel, so without this filter enabling delivery would start pushing a reader's
    weekly report — which nobody asked for."""
    uid = _reader(st)
    ss.update(st, uid, {"weeklyReport": True, "monthlyReport": True,
                        "notifications": {"recommendations": True, "weeklyDigest": True,
                                          "streakReminders": True, "blindSpotAlerts": True}})
    st.save_report(uid, {"overall": 72, "blindSpots": [{"topic": "Economy"}]})
    _subscribe(st, uid)
    _event(st)
    sender = _FakeSender()

    push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None)
    assert {p["kind"] for _, p in sender.calls} == {"breaking_story"}


def test_delivery_is_off_unless_the_switch_and_the_keys_are_both_set(monkeypatch):
    monkeypatch.delenv("RWE_PUSH_DELIVERY", raising=False)
    assert push_delivery._sender() is None
    monkeypatch.setenv("RWE_PUSH_DELIVERY", "1")
    assert push_delivery._sender() is None, "a switch without keys sends nothing"
    monkeypatch.setenv("RWE_VAPID_PRIVATE_KEY", "priv")
    assert push_delivery._sender() is None, "still no subject"
    monkeypatch.setenv("RWE_VAPID_SUBJECT", "mailto:ops@example.com")
    assert push_delivery._sender() is not None


@pytest.mark.parametrize("missing", ["RWE_PUSH_DELIVERY", "RWE_VAPID_PRIVATE_KEY",
                                     "RWE_VAPID_SUBJECT"])
def test_each_of_the_three_preconditions_alone_disables_sending(monkeypatch, missing):
    """Asserted one at a time rather than as a build-up: a chain that only ever adds settings never
    exercises "keys present, switch off", which is precisely the rollback state an operator reaches
    for when push is misbehaving."""
    monkeypatch.setenv("RWE_PUSH_DELIVERY", "1")
    monkeypatch.setenv("RWE_VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setenv("RWE_VAPID_SUBJECT", "mailto:ops@example.com")
    assert push_delivery._sender() is not None, "all three present"
    monkeypatch.delenv(missing)
    assert push_delivery._sender() is None


@pytest.mark.parametrize("raw,on", [("1", True), ("true", True), ("TRUE", True), ("yes", True),
                                    ("on", True), (" on ", True),
                                    ("0", False), ("false", False), ("off", False), ("", False),
                                    ("2", False), ("enabled", False)])
def test_the_delivery_switch_reads_the_same_vocabulary_as_the_rest_of_the_deployment(
        monkeypatch, raw, on):
    """An operator who writes `RWE_PUSH_DELIVERY=true` because every other flag accepts it, and gets
    silence, has no way to tell that from a broken pipeline."""
    monkeypatch.setenv("RWE_PUSH_DELIVERY", raw)
    assert push_delivery.enabled() is on


@pytest.mark.parametrize("raw,seconds", [("2500", 2.5), ("500", 0.5), ("30000", 30.0),
                                         ("0", 10.0), ("", 10.0), ("abc", 10.0), ("-5", 10.0)])
def test_the_send_timeout_is_configured_in_milliseconds(monkeypatch, raw, seconds):
    """Named `_MS` and parsed as ms. Reading it as seconds turns a 2500 ms deadline into 42 minutes —
    which is not a timeout at all, and would let one wedged service hold a pool thread for the whole
    run. A zero or unparseable value falls back rather than disabling the deadline."""
    monkeypatch.setenv("RWE_PUSH_SEND_TIMEOUT_MS", raw)
    assert push_delivery._timeout_seconds() == seconds


def test_the_daily_cap_still_applies_on_the_push_channel(st):
    """`_due_for_reader` deliberately clears `delivered_keys` (channel-agnostic) but KEEPS
    `counts_today`. A cap bounds how often a reader is interrupted, and that is more true of a lock
    screen than of an inbox, not less."""
    uid = _reader(st)
    _subscribe(st, uid)
    for i in range(ns.BREAKING_MAX_PER_DAY):
        st.record_notifications(uid, [{"kind": "breaking_story", "dedupe_key": f"ev:cap-{i}",
                                       "created_at": NOW.isoformat(),
                                       "title_key": "notifications.breaking_story.title",
                                       "payload": {"storyId": f"st_{i}"},
                                       "gated_by": "notifications.categories.breaking.push"}])
    _event(st, "st_new")
    sender = _FakeSender()
    stats = push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None)
    assert stats.considered == 0 and sender.calls == [], "today's budget is spent"


def test_an_event_older_than_the_lookback_window_is_not_delivered(st):
    """The window bounds how far back a fan-out reaches. Widening it means a reader who subscribes
    today receives yesterday's breaking news on their lock screen as if it had just happened."""
    uid = _reader(st)
    _subscribe(st, uid)
    st.record_notification_event(
        "story_breaking", "st_old", category="breaking",
        payload={"storyId": "st_old", "title": "Two days ago", "publisherCount": 4},
        occurred_at=(NOW - timedelta(hours=48)).isoformat(),
        expires_at=(NOW + timedelta(days=5)).isoformat())   # still unexpired, but stale
    sender = _FakeSender()
    assert push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None).events == 0
    assert sender.calls == []


def test_unreadable_events_mean_no_fan_out_rather_than_a_crash(st, monkeypatch):
    """Fail-open on a READ, matching the platform's rule elsewhere: an unreadable event list yields
    nothing to send. Raising here would kill the daemon thread and take the next cycle with it."""
    def boom(*a, **k):
        raise RuntimeError("db is having a moment")
    monkeypatch.setattr(st, "recent_notification_events", boom)
    stats = push_delivery.run_once(st, now=NOW, sender=_FakeSender(), log=lambda *a, **k: None)
    assert stats.events == 0 and stats.considered == 0


def test_an_unreadable_language_falls_back_rather_than_failing_the_send(st, monkeypatch):
    """The payload's `lang` is a FALLBACK the device usually overrides anyway (§4). Losing a
    notification over it would trade something that matters for something that does not."""
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st)
    monkeypatch.setattr(push_delivery.settings_service, "language",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no settings")))
    sender = _FakeSender()
    assert push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None).sent == 1
    assert sender.calls[0][1]["lang"] == "en"


def test_an_oversize_payload_is_logged_and_still_attempted(st):
    """Not truncated further: a payload over the budget is a defect in whatever produced it, and
    silently mangling it would hide the defect while still risking the rejection.

    `title` is bounded, so the field that can actually blow the budget is `storyId` — unbounded, and
    carried twice (in the payload and again, percent-encoded, in `href`). A cluster id that long is a
    bug upstream, which is exactly what this log line is for."""
    uid = _reader(st)
    _subscribe(st, uid)
    huge_id = "st_" + "z" * push_payload.SOFT_LIMIT_BYTES
    st.record_notification_event(
        "story_breaking", huge_id, category="breaking",
        payload={"storyId": huge_id, "title": "Big", "publisherCount": 4},
        occurred_at=(NOW - timedelta(minutes=5)).isoformat(),
        expires_at=(NOW + timedelta(hours=1)).isoformat())
    seen, log = _logs()

    stats = push_delivery.run_once(st, now=NOW, sender=_FakeSender(), log=log)
    oversize = [f for e, f in seen if e == "push_payload_oversize"]
    assert oversize and oversize[0]["bytes"] > push_payload.SOFT_LIMIT_BYTES
    assert stats.sent == 1, "logged, not dropped"


def test_a_run_that_runs_out_of_time_stops_cleanly(st, monkeypatch):
    """Unbounded work on a background thread is how a "background" job becomes an outage. What the
    deadline cuts short is picked up by the next cycle — nothing is lost, only deferred."""
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st)
    monkeypatch.setattr(push_delivery, "MAX_RUN_SECONDS", -1.0)
    seen, log = _logs()

    stats = push_delivery.run_once(st, now=NOW, sender=_FakeSender(), log=log)
    assert stats.considered == 0
    assert any(e == "push_run_deadline" for e, _ in seen)


def test_a_notification_whose_id_cannot_be_resolved_is_dropped_not_sent(st, monkeypatch):
    """`record_notifications` is idempotent and may legitimately decline to write a row (already
    resolved, already collapsed). The id lookup is what confirms one exists, and a notification
    without an id cannot be claimed — sending it would put something on a lock screen that the ledger
    has no row for, which is the one state no later phase could reconcile."""
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st)
    monkeypatch.setattr(st, "notification_ids_by_dedupe_key", lambda *a, **k: {})
    sender = _FakeSender()
    stats = push_delivery.run_once(st, now=NOW, sender=sender, log=lambda *a, **k: None)
    assert stats.considered == 0 and sender.calls == []


def test_the_fan_out_really_is_concurrent(st):
    """§7 requires a bounded POOL, and "bounded" is only interesting because it is a bound on
    something. Serialised, a 200-device fan-out at a 10-second timeout each would take half an hour
    and `MAX_RUN_SECONDS` would cut it off long before the last device.

    Driven by a barrier rather than by timing: the sends must overlap or this deadlocks, and the
    barrier's own timeout turns that into a failure instead of a hung suite.

    The barrier is sized from `MAX_WORKERS` so retuning the pool does not break the test — which means
    the assertion below has to pin the one thing that is not a tuning decision: that it is a pool at
    all. A pool of one is a serial loop wearing an executor."""
    assert push_delivery.MAX_WORKERS > 1
    uid = _reader(st)
    for i in range(push_delivery.MAX_WORKERS):
        _subscribe(st, uid, f"https://push.example/dev-{i}")
    _event(st)
    barrier = threading.Barrier(push_delivery.MAX_WORKERS, timeout=10)

    class _Rendezvous(_FakeSender):
        def send(self, subscription, data):
            barrier.wait()                   # BrokenBarrierError if the pool is serial
            return super().send(subscription, data)

    stats = push_delivery.run_once(st, now=NOW, sender=_Rendezvous(), log=lambda *a, **k: None)
    assert stats.sent == push_delivery.MAX_WORKERS


def test_a_fan_out_never_keeps_the_process_alive(st, monkeypatch):
    """A daemon thread, asserted on the flag because the property it encodes — a container that is
    shutting down does not wait on a push service — is only otherwise observable at interpreter exit,
    which a test inside that interpreter cannot reach."""
    monkeypatch.setenv("RWE_PUSH_DELIVERY", "1")
    monkeypatch.setenv("RWE_VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setenv("RWE_VAPID_SUBJECT", "mailto:ops@example.com")
    monkeypatch.setattr(push_delivery, "run_once", lambda *a, **k: None)
    made = {}
    real_thread = threading.Thread

    def spy(*args, **kwargs):
        made.update(kwargs)
        return real_thread(*args, **kwargs)
    monkeypatch.setattr(push_delivery.threading, "Thread", spy)

    assert push_delivery.request_delivery(st) is True
    assert made["daemon"] is True and made["name"] == "push-delivery"


def test_a_reader_whose_evaluation_blows_up_does_not_end_the_fan_out(st, monkeypatch):
    """Distinct from a failing SEND, which `_deliver_one` catches: this is the PLANNING phase, where an
    unhandled exception would abandon every reader who happened to sort after the broken one."""
    a, b = _reader(st, "reader-a"), _reader(st, "reader-b")
    _subscribe(st, a, "https://push.example/a")
    _subscribe(st, b, "https://push.example/b")
    _event(st)
    real = push_delivery._due_for_reader

    def selective(store_, uid, now):
        if uid == a:
            raise RuntimeError("this reader's settings are corrupt")
        return real(store_, uid, now)
    monkeypatch.setattr(push_delivery, "_due_for_reader", selective)
    seen, log = _logs()

    sender = _FakeSender()
    stats = push_delivery.run_once(st, now=NOW, sender=sender, log=log)
    assert stats.sent == 1
    assert [e for e, _ in sender.calls] == ["https://push.example/b"]
    assert any(e == "push_reader_failed" for e, _ in seen)


def test_run_once_is_a_no_op_when_delivery_is_not_configured(st, monkeypatch):
    monkeypatch.delenv("RWE_PUSH_DELIVERY", raising=False)
    uid = _reader(st)
    _subscribe(st, uid)
    _event(st)
    assert push_delivery.run_once(st, now=NOW, log=lambda *a, **k: None).considered == 0


# --------------------------------------------------------------------------------------------- #
# The poller seam — the requirement that ingestion never waits on a push service.
# --------------------------------------------------------------------------------------------- #
def test_request_delivery_returns_immediately_and_works_on_another_thread(st, monkeypatch):
    """§7: the poller's thread ingests articles. Blocking it on a third party's network would trade a
    delayed notification for a stale corpus — the worse of the two."""
    monkeypatch.setenv("RWE_PUSH_DELIVERY", "1")
    monkeypatch.setenv("RWE_VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setenv("RWE_VAPID_SUBJECT", "mailto:ops@example.com")
    started, released = threading.Event(), threading.Event()
    worker_thread = {}

    def slow_run(*a, **kw):
        worker_thread["name"] = threading.current_thread().name
        started.set()
        released.wait(5)
    monkeypatch.setattr(push_delivery, "run_once", slow_run)

    caller = threading.current_thread().name
    t0 = time.perf_counter()
    assert push_delivery.request_delivery(st) is True
    elapsed = time.perf_counter() - t0
    assert started.wait(5), "the run really did start"
    assert elapsed < 0.5, "the caller was not blocked by it"

    assert push_delivery.request_delivery(st) is False, "one run at a time — dropped, not queued"
    released.set()
    time.sleep(0.2)
    assert worker_thread["name"] != caller

    assert push_delivery.request_delivery(st) is True, "and the next cycle may run again"
    released.set()


def test_request_delivery_does_nothing_when_delivery_is_off(st, monkeypatch):
    monkeypatch.delenv("RWE_PUSH_DELIVERY", raising=False)
    assert push_delivery.request_delivery(st) is False
