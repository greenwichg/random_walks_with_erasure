"""Tests for examples/notification_service.py — the D0 notification foundation (pure leaf).

Pins the D0 contract: the module is a stdlib-only leaf; :func:`evaluate` is deterministic, uses only
the :class:`NotificationContext`, gates every kind through the reader's settings, dedupes through the
delivered-key ledger, and never touches a producer or a clock. Settings are built with the REAL
``settings_service`` normaliser so the registry's setting paths are cross-checked against the contract.
"""

import ast
import dataclasses
import json
import pathlib
import sys
from datetime import datetime, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import notification_service as ns   # noqa: E402
import settings_service as ss       # noqa: E402


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
ALL_KINDS = {"weekly_report", "monthly_deep_dive", "recommendations_waiting",
             "weekly_digest", "streak_reminder", "blind_spot_alert"}


def _settings(weekly=True, monthly=True, recs=True, digest=True, streak=True, blind=True):
    """Normalised settings with every notification toggle independently controllable."""
    return ss.normalize_settings({
        "weeklyReport": weekly, "monthlyReport": monthly,
        "notifications": {"recommendations": recs, "weeklyDigest": digest,
                          "streakReminders": streak, "blindSpotAlerts": blind}})


def _ctx(settings=None, delivered=(), now=NOW, *, has_report=True, overall=72,
         blind_spots=("topic:economy",), unopened_count=4, streak_days=5, read_today=False,
         reads_this_week=6, streak_through_yesterday=4):
    """A context in which — with all toggles on and nothing delivered — every kind fires."""
    return ns.NotificationContext(
        now=now,
        settings=settings if settings is not None else _settings(),
        delivery=ns.DeliveryState(delivered_keys=frozenset(delivered)),
        report=ns.ReportInputs(has_report=has_report, overall=overall, blind_spots=blind_spots),
        recommendations=ns.RecommendationInputs(unopened_count=unopened_count),
        reading=ns.ReadingInputs(streak_days=streak_days, read_today=read_today,
                                 reads_this_week=reads_this_week,
                                 streak_through_yesterday=streak_through_yesterday))


def _kinds(notifications):
    return [n.kind for n in notifications]


# --------------------------------------------------------------------------- #
# Leaf-module import constraint — the whole reason this module exists as it does.
# --------------------------------------------------------------------------- #
def test_module_is_a_stdlib_only_leaf():
    src = (ROOT / "examples" / "notification_service.py").read_text()
    tops = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            tops.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                tops.add(f"<relative level {node.level}>")
            elif node.module:
                tops.add(node.module.split(".")[0])
    forbidden = {"api_server", "coach_service", "store", "rwe", "health_report", "personalize",
                 "settings_service", "api_fastapi", "narrate_report", "evidence_resolver", "numpy"}
    assert tops.isdisjoint(forbidden), f"leaf imported a non-stdlib/project module: {tops & forbidden}"
    allowed = set(sys.stdlib_module_names) | {"__future__"}
    assert tops <= allowed, f"non-stdlib imports found: {sorted(tops - allowed)}"


# --------------------------------------------------------------------------- #
# Every kind fires when due + enabled.
# --------------------------------------------------------------------------- #
def test_all_kinds_fire_when_due_and_enabled():
    assert set(_kinds(ns.evaluate(_ctx()))) == ALL_KINDS


def test_output_is_in_registry_order():
    """Registry order is the output order. `_ctx()` fires every kind whose trigger is the reader's
    own state; `breaking_story` needs a global event, so it is added here to exercise the full
    registry rather than being excused from the property."""
    registry_order = [k.kind for k in ns.NOTIFICATION_KINDS]
    fired = _kinds(ns.evaluate(_ctx()))
    assert fired == [k for k in registry_order if k != "breaking_story"]

    with_event = dataclasses.replace(_ctx(), events=ns.EventInputs(events=(_breaking_event(1),)))
    assert _kinds(ns.evaluate(with_event)) == registry_order, "the new kind slots into its own place"


@pytest.mark.parametrize("kind,setting_path", [
    ("weekly_report", "weeklyReport"),
    ("monthly_deep_dive", "monthlyReport"),
    ("recommendations_waiting", "notifications.recommendations"),
    ("weekly_digest", "notifications.weeklyDigest"),
    ("streak_reminder", "notifications.streakReminders"),
    ("blind_spot_alert", "notifications.blindSpotAlerts"),
])
def test_each_kind_records_its_gate_and_a_json_safe_payload(kind, setting_path):
    n = next(n for n in ns.evaluate(_ctx()) if n.kind == kind)
    assert n.gated_by == setting_path
    assert n.created_at == NOW.isoformat()
    json.dumps(n.payload)                      # payload JSON-safe


# --------------------------------------------------------------------------- #
# Settings gating — an off toggle suppresses exactly its own kind; all-off => nothing.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("flag,kind", [
    ("weekly", "weekly_report"),
    ("monthly", "monthly_deep_dive"),
    ("recs", "recommendations_waiting"),
    ("digest", "weekly_digest"),
    ("streak", "streak_reminder"),
    ("blind", "blind_spot_alert"),
])
def test_disabling_one_setting_suppresses_only_that_kind(flag, kind):
    fired = set(_kinds(ns.evaluate(_ctx(settings=_settings(**{flag: False})))))
    assert kind not in fired
    assert fired == ALL_KINDS - {kind}         # every OTHER kind still fires


def test_all_settings_off_emits_nothing():
    off = _settings(weekly=False, monthly=False, recs=False, digest=False, streak=False, blind=False)
    assert ns.evaluate(_ctx(settings=off)) == []


# --------------------------------------------------------------------------- #
# Dedupe — a delivered key suppresses re-emission (idempotent on every fetch).
# --------------------------------------------------------------------------- #
def test_delivered_keys_suppress_re_emission():
    first = ns.evaluate(_ctx())
    assert first                                        # sanity: something fired
    delivered = {n.dedupe_key for n in first}
    assert ns.evaluate(_ctx(delivered=delivered)) == []  # nothing re-fires once delivered


def test_partial_dedupe_leaves_the_rest():
    first = ns.evaluate(_ctx())
    one = next(n for n in first if n.kind == "streak_reminder")
    remaining = set(_kinds(ns.evaluate(_ctx(delivered={one.dedupe_key}))))
    assert "streak_reminder" not in remaining and remaining == ALL_KINDS - {"streak_reminder"}


def test_blind_spot_dedupe_is_keyed_on_the_set():
    # same set already delivered -> suppressed
    n = next(n for n in ns.evaluate(_ctx()) if n.kind == "blind_spot_alert")
    assert "blind_spot_alert" not in _kinds(ns.evaluate(_ctx(delivered={n.dedupe_key})))
    # a DIFFERENT blind-spot set -> different key -> fires again (a genuinely new alert)
    changed = ns.evaluate(_ctx(delivered={n.dedupe_key},
                               blind_spots=("topic:economy", "topic:climate")))
    assert "blind_spot_alert" in _kinds(changed)


# --------------------------------------------------------------------------- #
# Predicate negatives — nothing fires when the underlying producer fact is absent.
# --------------------------------------------------------------------------- #
def test_predicate_negatives():
    assert "weekly_report" not in _kinds(ns.evaluate(_ctx(has_report=False)))
    assert "monthly_deep_dive" not in _kinds(ns.evaluate(_ctx(has_report=False)))
    assert "recommendations_waiting" not in _kinds(ns.evaluate(_ctx(unopened_count=0)))
    assert "weekly_digest" not in _kinds(ns.evaluate(_ctx(reads_this_week=0)))
    assert "blind_spot_alert" not in _kinds(ns.evaluate(_ctx(blind_spots=())))
    # streak reminder needs an active streak THROUGH YESTERDAY AND nothing read today
    assert "streak_reminder" not in _kinds(ns.evaluate(_ctx(streak_through_yesterday=0)))
    assert "streak_reminder" not in _kinds(ns.evaluate(_ctx(read_today=True)))
    assert "streak_reminder" in _kinds(ns.evaluate(_ctx(streak_through_yesterday=1, read_today=False)))


# --------------------------------------------------------------------------- #
# Determinism + JSON-safe serialization of the whole batch.
# --------------------------------------------------------------------------- #
def test_evaluate_is_deterministic():
    a = ns.evaluate(_ctx())
    b = ns.evaluate(_ctx())
    assert a == b                                       # frozen dataclasses -> value equality
    assert [n.dedupe_key for n in a] == [n.dedupe_key for n in b]


def test_batch_is_json_safe_and_channel_renders():
    batch = ns.evaluate(_ctx())
    json.dumps([dataclasses.asdict(n) for n in batch])  # every notification serialises
    chan = ns.InAppChannel()
    assert isinstance(chan, ns.Channel) and chan.name == "in_app"
    for n in batch:
        json.dumps(chan.render(n))                       # channel output serialises too


def test_dedupe_keys_have_expected_periods():
    by = {n.kind: n.dedupe_key for n in ns.evaluate(_ctx())}
    wk = f"{NOW.isocalendar()[0]}-W{NOW.isocalendar()[1]:02d}"
    assert by["weekly_report"] == f"weekly_report:{wk}"
    assert by["weekly_digest"] == f"weekly_digest:{wk}"
    assert by["monthly_deep_dive"] == "monthly_deep_dive:2026-07"
    assert by["recommendations_waiting"] == "recommendations_waiting:2026-07-15"
    assert by["streak_reminder"] == "streak_reminder:2026-07-15"
    assert by["blind_spot_alert"].startswith("blind_spot_alert:")


# --------------------------------------------------------------------------- #
# The framework: fan-out, mode="discrete", and the daily cap (A3a).
#
# Tested against a SYNTHETIC kind monkeypatched into the registry, deliberately: this commit adds a
# capability and no consumer, and a framework that can only be exercised through its first consumer
# is not isolated from it. The real kind arrives in A3b and needs none of these tests changed.
# --------------------------------------------------------------------------- #
FW_NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def _fw_settings(on=True):
    """Settings whose only relevant gate is a category path (the shape a fan-out kind will use)."""
    return ss.normalize_settings({"notifications": {"categories": {"breaking": {"inApp": on}}}})


def _fan_kind(items, *, max_per_day=None, kind="synthetic_fanout"):
    """A kind that fans out to whatever `items` says — `[(dedupe_key, payload), ...]`."""
    return ns.NotificationKind(
        kind=kind, setting_path="notifications.categories.breaking.inApp", mode="discrete",
        title_key="notifications.synthetic.title",
        fanout=lambda c: list(items), max_per_day=max_per_day)


def _fw_ctx(delivered=(), counts=None, events=()):
    return ns.NotificationContext(
        now=FW_NOW, settings=_fw_settings(),
        delivery=ns.DeliveryState(delivered_keys=frozenset(delivered), counts_today=dict(counts or {})),
        events=ns.EventInputs(events=tuple(events)))


def _only(monkeypatch, *kinds):
    """Evaluate with ONLY these kinds registered, so a framework assertion is not diluted by the six
    real ones. `DISCRETE_KINDS` is recomputed to match, as it is derived at import time."""
    monkeypatch.setattr(ns, "NOTIFICATION_KINDS", tuple(kinds))
    monkeypatch.setattr(ns, "DISCRETE_KINDS", tuple(k.kind for k in kinds if k.mode == "discrete"))
    monkeypatch.setattr(ns, "EVENT_KINDS", tuple(k.kind for k in kinds if k.mode == "event"))


def test_a_fanout_kind_yields_one_notification_per_item(monkeypatch):
    """THE capability. Every kind before this yielded at most one notification, because its trigger
    was a state. A fan-out kind's trigger is a collection, so N in means N out — in order."""
    items = [("ev:1", {"storyId": "a"}), ("ev:2", {"storyId": "b"}), ("ev:3", {"storyId": "c"})]
    _only(monkeypatch, _fan_kind(items))
    out = ns.evaluate(_fw_ctx())
    assert [n.dedupe_key for n in out] == ["ev:1", "ev:2", "ev:3"], "one each, delivery order kept"
    assert [n.payload["storyId"] for n in out] == ["a", "b", "c"]
    assert {n.kind for n in out} == {"synthetic_fanout"}
    assert {n.gated_by for n in out} == {"notifications.categories.breaking.inApp"}


def test_a_fanout_kind_is_still_deterministic(monkeypatch):
    """The D0 contract does not bend for fan-out: same context in, same notifications out."""
    _only(monkeypatch, _fan_kind([("ev:1", {"n": 1}), ("ev:2", {"n": 2})]))
    ctx = _fw_ctx()
    assert [dataclasses.asdict(n) for n in ns.evaluate(ctx)] == \
           [dataclasses.asdict(n) for n in ns.evaluate(ctx)]


def test_fanout_items_already_delivered_are_suppressed(monkeypatch):
    _only(monkeypatch, _fan_kind([("ev:1", {}), ("ev:2", {}), ("ev:3", {})]))
    out = ns.evaluate(_fw_ctx(delivered=("ev:2",)))
    assert [n.dedupe_key for n in out] == ["ev:1", "ev:3"]


def test_a_duplicate_key_within_one_fanout_is_collapsed(monkeypatch):
    """A producer that emits the same key twice must not get two rows — and, more importantly, must
    not spend the daily cap twice on one notification."""
    _only(monkeypatch, _fan_kind([("ev:1", {"v": 1}), ("ev:1", {"v": 2}), ("ev:2", {})]))
    out = ns.evaluate(_fw_ctx())
    assert [n.dedupe_key for n in out] == ["ev:1", "ev:2"]
    assert out[0].payload == {"v": 1}, "the first occurrence wins"


def test_max_per_day_truncates_the_front_of_the_list(monkeypatch):
    """A fan-out's volume is decided by the world, so the cap is what stops a burst becoming a flood.
    It keeps the FRONT: a fan-out returns items in delivery order, so the reader gets the first
    stories rather than an arbitrary slice of them."""
    items = [(f"ev:{i}", {"i": i}) for i in range(10)]
    _only(monkeypatch, _fan_kind(items, max_per_day=3))
    assert [n.dedupe_key for n in ns.evaluate(_fw_ctx())] == ["ev:0", "ev:1", "ev:2"]


def test_max_per_day_counts_what_the_reader_already_received_today(monkeypatch):
    """The cap is per DAY, not per evaluation — otherwise a reader refreshing the page ten times gets
    ten times the cap."""
    items = [(f"ev:{i}", {}) for i in range(10)]
    _only(monkeypatch, _fan_kind(items, max_per_day=5))
    assert len(ns.evaluate(_fw_ctx(counts={"synthetic_fanout": 3}))) == 2, "5 - 3 already sent"
    assert ns.evaluate(_fw_ctx(counts={"synthetic_fanout": 5})) == [], "budget exhausted"
    assert ns.evaluate(_fw_ctx(counts={"synthetic_fanout": 99})) == [], "over budget is not negative"


def test_no_max_per_day_means_unbounded(monkeypatch):
    _only(monkeypatch, _fan_kind([(f"ev:{i}", {}) for i in range(50)]))
    assert len(ns.evaluate(_fw_ctx(counts={"synthetic_fanout": 1000}))) == 50


def test_a_fanout_kind_is_gated_like_any_other(monkeypatch):
    """Fail-closed on the dotted path, exactly as for a single kind — the gate is not bypassed by
    the new shape."""
    _only(monkeypatch, _fan_kind([("ev:1", {})]))
    off = ns.NotificationContext(now=FW_NOW, settings=_fw_settings(on=False))
    assert ns.evaluate(off) == []
    assert ns.evaluate(ns.NotificationContext(now=FW_NOW, settings={})) == [], "missing path -> closed"


def test_an_empty_fanout_emits_nothing(monkeypatch):
    _only(monkeypatch, _fan_kind([]))
    assert ns.evaluate(_fw_ctx()) == []


def test_discrete_kinds_are_never_returned_as_inactive(monkeypatch):
    """F2, the failure this mode exists to prevent. `notification_delivery` auto-resolves every kind
    this function names. A discrete kind must never appear, even when its gate is OFF and its
    predicate is false — a story that stopped breaking still broke."""
    discrete = _fan_kind([("ev:1", {})])
    state = ns.NotificationKind(
        kind="synthetic_state", setting_path="notifications.categories.breaking.inApp", mode="event",
        title_key="notifications.synthetic.title", predicate=lambda c: False,
        dedupe_key=lambda c: "s:1", payload=lambda c: {})
    _only(monkeypatch, discrete, state)

    inactive = ns.inactive_event_kinds(_fw_ctx())
    assert "synthetic_state" in inactive, "a state alert whose condition is false IS inactive"
    assert "synthetic_fanout" not in inactive, "a discrete occurrence is never inactive"

    off = ns.NotificationContext(now=FW_NOW, settings=_fw_settings(on=False))
    assert "synthetic_fanout" not in ns.inactive_event_kinds(off), "not even when the gate is off"


def test_discrete_kinds_are_exported_separately_from_event_kinds():
    """`EVENT_KINDS` is an ALLOWLIST, so a discrete kind is excluded by not being in it — and so is
    whatever mode is invented next. This tuple is the assertion surface that pins the two apart."""
    assert hasattr(ns, "DISCRETE_KINDS")
    assert set(ns.DISCRETE_KINDS) & set(ns.EVENT_KINDS) == set(), "a kind is one lifecycle, not two"


def test_event_inputs_default_empty_and_carry_events():
    """The context grew a substructure; nothing that omits it may change behaviour."""
    assert ns.NotificationContext(now=FW_NOW, settings={}).events.events == ()
    ev = {"id": 1, "sourceType": "story_breaking", "sourceId": "st_a", "payload": {"title": "T"}}
    assert _fw_ctx(events=[ev]).events.events == (ev,)


def test_the_six_shipped_kinds_are_untouched_by_fan_out():
    """The regression that matters most: the kinds that predate fan-out still take the
    single-notification path, and none of them acquired a fanout, a cap, or a new mode. Adding a
    fan-out kind must change nothing about them."""
    for k in ns.NOTIFICATION_KINDS:
        if k.kind in ALL_KINDS:
            assert k.fanout is None, f"{k.kind} must stay a single kind"
            assert k.max_per_day is None, f"{k.kind} must stay uncapped"
            assert k.mode in ("cadence", "event"), f"{k.kind} has an unexpected mode {k.mode!r}"
    assert set(ns.DISCRETE_KINDS).isdisjoint(ALL_KINDS), "no shipped kind became discrete"


# --------------------------------------------------------------------------- #
# breaking_story — the first fan-out kind (A3b).
#
# A3a's tests pin the FRAMEWORK against a synthetic kind. These pin this kind's own policy: what it
# selects out of the context, what it puts in the payload, and the cap that keeps a chaotic news day
# from filling the inbox.
# --------------------------------------------------------------------------- #
def _breaking_event(i, *, title="Court issues major ruling", category="breaking",
                    expires_at=None, story_id=None, publishers=4):
    """An event shaped exactly as `store.recent_notification_events` returns one."""
    return {"id": i, "sourceType": "story_breaking", "sourceId": story_id or f"st_{i}",
            "category": category,
            "payload": {"storyId": story_id or f"st_{i}", "title": title,
                        "publisherCount": publishers, "band": "Breaking"},
            "occurredAt": "2026-07-15T11:00:00+00:00", "expiresAt": expires_at}


def _breaking_ctx(events=(), *, on=True, delivered=(), counts=None, now=NOW):
    return ns.NotificationContext(
        now=now,
        settings=ss.normalize_settings({"notifications": {"categories": {"breaking": {"inApp": on}}}}),
        delivery=ns.DeliveryState(delivered_keys=frozenset(delivered), counts_today=dict(counts or {})),
        events=ns.EventInputs(events=tuple(events)))


def _breaking(out):
    return [n for n in out if n.kind == "breaking_story"]


def test_each_breaking_event_becomes_one_notification():
    """The requirement in one line: one notification per STORY, not per article and not per update.
    The event table already guarantees one event per story; this guarantees one notification per
    event."""
    out = _breaking(ns.evaluate(_breaking_ctx([_breaking_event(1), _breaking_event(2)])))
    assert [n.dedupe_key for n in out] == ["ev:1", "ev:2"]
    assert [n.payload["storyId"] for n in out] == ["st_1", "st_2"]
    assert {n.title_key for n in out} == {"notifications.breaking_story.title"}


def test_the_payload_carries_what_the_inbox_needs_to_deep_link():
    out = _breaking(ns.evaluate(_breaking_ctx([_breaking_event(7, story_id="st_xyz")])))
    assert out[0].payload == {"storyId": "st_xyz", "title": "Court issues major ruling",
                              "publisherCount": 4, "occurredAt": "2026-07-15T11:00:00+00:00"}


def test_the_dedupe_key_is_the_event_not_the_story():
    """A story id is derived from its earliest-published member, so a backfill that discovers an
    earlier article can move it. Keying on the immutable event row is what stops that re-announcing
    a story the reader already saw."""
    ev = _breaking_event(3, story_id="st_original")
    assert _breaking(ns.evaluate(_breaking_ctx([ev])))[0].dedupe_key == "ev:3"
    moved = {**ev, "sourceId": "st_moved", "payload": {**ev["payload"], "storyId": "st_moved"}}
    assert _breaking(ns.evaluate(_breaking_ctx([moved])))[0].dedupe_key == "ev:3", "same event, same key"


def test_only_breaking_category_events_are_selected():
    """The context will carry other categories once product updates exist; a kind selects its own.
    Matched on CATEGORY, which is what the reader's preference is expressed in."""
    events = [_breaking_event(1), _breaking_event(2, category="product"),
              _breaking_event(3, category="digests")]
    assert [n.dedupe_key for n in _breaking(ns.evaluate(_breaking_ctx(events)))] == ["ev:1"]


def test_expired_events_do_not_fire():
    """A three-day-old "breaking" is not breaking. `ctx.now` is the authority, so this holds even if
    the caller handed us a stale list."""
    events = [_breaking_event(1, expires_at="2026-07-15T09:00:00+00:00"),   # before NOW
              _breaking_event(2, expires_at="2026-07-15T17:00:00+00:00"),   # after NOW
              _breaking_event(3, expires_at=None)]                          # never expires
    assert [n.dedupe_key for n in _breaking(ns.evaluate(_breaking_ctx(events)))] == ["ev:2", "ev:3"]


def test_an_event_without_a_usable_title_is_skipped():
    """An empty row is worse for the reader than no row."""
    events = [_breaking_event(1, title=""), _breaking_event(2, title="   "), _breaking_event(3)]
    assert [n.dedupe_key for n in _breaking(ns.evaluate(_breaking_ctx(events)))] == ["ev:3"]


def test_a_malformed_event_cannot_break_evaluation():
    """Events are JSON from the database. A row that lost its shape must be skipped, never raise —
    one bad event may not cost the reader their whole inbox."""
    events = ["not-a-dict", None, {"id": 9}, {"id": 10, "category": "breaking", "payload": None},
              _breaking_event(11)]
    out = _breaking(ns.evaluate(_breaking_ctx(events)))
    assert [n.dedupe_key for n in out] == ["ev:11"]


def test_breaking_is_capped_per_day_at_five():
    """A fan-out's volume is set by the news. The cap is a platform guarantee in a product about
    reading more calmly — a quiet day never reaches it, a chaotic one is bounded."""
    assert ns.BREAKING_MAX_PER_DAY == 5
    events = [_breaking_event(i) for i in range(1, 11)]
    out = _breaking(ns.evaluate(_breaking_ctx(events)))
    assert len(out) == 5
    assert [n.dedupe_key for n in out] == ["ev:1", "ev:2", "ev:3", "ev:4", "ev:5"]

    partial = _breaking(ns.evaluate(_breaking_ctx(events, counts={"breaking_story": 3})))
    assert len(partial) == 2, "the cap counts what the reader already received today"


def test_breaking_is_gated_by_its_category_and_is_fail_closed():
    events = [_breaking_event(1)]
    assert _breaking(ns.evaluate(_breaking_ctx(events, on=False))) == []
    bare = ns.NotificationContext(now=NOW, settings={}, events=ns.EventInputs(events=tuple(events)))
    assert _breaking(ns.evaluate(bare)) == [], "a missing preference path never delivers"


def test_a_delivered_breaking_notification_is_not_repeated():
    events = [_breaking_event(1), _breaking_event(2)]
    out = _breaking(ns.evaluate(_breaking_ctx(events, delivered=("ev:1",))))
    assert [n.dedupe_key for n in out] == ["ev:2"]


def test_breaking_is_discrete_and_therefore_never_auto_resolves():
    """F2, at the kind level. `notification_delivery` resolves every kind `inactive_event_kinds`
    names; a story that has stopped breaking still broke, and the reader should still see that."""
    assert "breaking_story" in ns.DISCRETE_KINDS
    assert "breaking_story" not in ns.EVENT_KINDS
    assert "breaking_story" not in ns.inactive_event_kinds(_breaking_ctx([]))
    assert "breaking_story" not in ns.inactive_event_kinds(_breaking_ctx([], on=False))


def test_breaking_is_registered_last_so_it_leads_the_inbox():
    """`store.list_notifications` orders by `id DESC`, so within one materialisation the kind
    registered LAST is inserted last and shows first. That is where a breaking story belongs."""
    assert ns.NOTIFICATION_KINDS[-1].kind == "breaking_story"


def test_no_events_means_no_breaking_notifications():
    assert _breaking(ns.evaluate(_breaking_ctx([]))) == []


# --------------------------------------------------------------------------- #
# Channel-aware gating (pre-Phase-B). A reader's answer to "tell me about breaking news" and their
# answer to "interrupt my phone about it" are different answers, so the gate — and only the gate —
# depends on the channel being evaluated for.
# --------------------------------------------------------------------------- #
def _channel_ctx(events=(), *, in_app=True, push=False):
    return ns.NotificationContext(
        now=NOW,
        settings=ss.normalize_settings(
            {"notifications": {"categories": {"breaking": {"inApp": in_app, "push": push}}}}),
        events=ns.EventInputs(events=tuple(events)))


def test_a_category_kind_resolves_a_different_gate_per_channel():
    k = next(k for k in ns.NOTIFICATION_KINDS if k.kind == "breaking_story")
    assert ns.gate_path(k) == "notifications.categories.breaking.inApp", "in-app is the default"
    assert ns.gate_path(k, "in_app") == "notifications.categories.breaking.inApp"
    assert ns.gate_path(k, "push") == "notifications.categories.breaking.push"


def test_a_legacy_kind_keeps_one_gate_on_every_channel():
    """A preference that predates channels means the same thing on all of them: there is no separate
    "email me my weekly digest" toggle to consult, so inventing a path per channel would gate on a
    preference the reader was never offered."""
    for kind in ("weekly_report", "weekly_digest", "streak_reminder"):
        k = next(k for k in ns.NOTIFICATION_KINDS if k.kind == kind)
        assert ns.gate_path(k) == k.setting_path
        assert ns.gate_path(k, "push") == k.setting_path
        assert ns.gate_path(k, "carrier_pigeon") == k.setting_path


def test_an_unknown_channel_is_fail_closed_for_a_category_kind():
    """Consent is per channel, so a channel nobody has written a preference for must not inherit the
    consent a reader gave for a different one."""
    k = next(k for k in ns.NOTIFICATION_KINDS if k.kind == "breaking_story")
    assert ns.gate_path(k, "sms") == ""
    ctx = _channel_ctx([_breaking_event(1)], in_app=True, push=True)
    assert _breaking(ns.evaluate(ctx, "sms")) == [], "everything on, but not on a channel we know"


def test_the_two_channels_gate_independently():
    """The point of the whole refactor: with the channel baked into `setting_path`, a reader who
    wanted push but not in-app produced NO notification at all, so there was nothing for a push
    channel to send."""
    events = [_breaking_event(1)]

    both_off = _channel_ctx(events, in_app=False, push=False)
    assert _breaking(ns.evaluate(both_off, "in_app")) == []
    assert _breaking(ns.evaluate(both_off, "push")) == []

    in_app_only = _channel_ctx(events, in_app=True, push=False)
    assert len(_breaking(ns.evaluate(in_app_only, "in_app"))) == 1
    assert _breaking(ns.evaluate(in_app_only, "push")) == [], "in-app consent is not push consent"

    push_only = _channel_ctx(events, in_app=False, push=True)
    assert _breaking(ns.evaluate(push_only, "in_app")) == []
    assert len(_breaking(ns.evaluate(push_only, "push"))) == 1, "the case that used to be impossible"


def test_gated_by_records_the_channel_it_was_evaluated_for():
    """`gated_by` is the consent a row rests on, so it must name the channel's own preference — a
    later consent view that showed `.inApp` for something delivered by push would be lying."""
    ctx = _channel_ctx([_breaking_event(1)], in_app=True, push=True)
    assert _breaking(ns.evaluate(ctx, "in_app"))[0].gated_by == "notifications.categories.breaking.inApp"
    assert _breaking(ns.evaluate(ctx, "push"))[0].gated_by == "notifications.categories.breaking.push"


def test_the_default_channel_leaves_every_shipped_kind_byte_identical():
    """The refactor's acceptance condition: no caller passes a channel yet, so `evaluate(ctx)` must
    produce exactly what it did before channels existed — same kinds, same keys, same gates."""
    ctx = dataclasses.replace(_ctx(), events=ns.EventInputs(events=(_breaking_event(1),)))
    assert ns.evaluate(ctx) == ns.evaluate(ctx, "in_app")
    assert [n.gated_by for n in ns.evaluate(ctx)] == [
        "weeklyReport", "monthlyReport", "notifications.recommendations",
        "notifications.weeklyDigest", "notifications.streakReminders",
        "notifications.blindSpotAlerts", "notifications.categories.breaking.inApp"]


def test_inactive_event_kinds_is_channel_aware_too():
    """Resolution follows the same gate as delivery: an alert the reader turned off for a channel is
    not actionable on that channel. No shipped event kind has a category yet, so today every channel
    gives the same answer — the parameter exists so that stays true when one does."""
    ctx = _ctx()
    assert ns.inactive_event_kinds(ctx) == ns.inactive_event_kinds(ctx, "in_app")
    assert ns.inactive_event_kinds(ctx) == ns.inactive_event_kinds(ctx, "push")


def test_every_kind_declares_exactly_one_gating_shape():
    """A registry invariant, not a style rule: a kind with both would have two answers to "may I
    deliver this", and a kind with neither resolves to "" and can never deliver at all."""
    for k in ns.NOTIFICATION_KINDS:
        assert bool(k.setting_path) != bool(k.category), f"{k.kind} must have exactly one"


def test_a_category_kind_never_hard_codes_a_channel_in_its_setting_path():
    """The specific mistake this refactor removes: a `setting_path` ending in a channel leaf reads as
    a preference but behaves as a decision, and makes the kind undeliverable anywhere else."""
    leaves = set(ns.CHANNEL_SETTING_KEYS.values())
    for k in ns.NOTIFICATION_KINDS:
        assert k.setting_path.split(".")[-1] not in leaves, f"{k.kind} should declare a category"


def test_inactive_event_kinds_follows_the_channel_gate(monkeypatch):
    """The channel-aware half of resolution, provable only with a synthetic kind: no shipped event
    kind has a category yet, so the real registry cannot distinguish `gate_path(k, channel)` from
    `k.setting_path` here. A state alert the reader turned off for a channel is not actionable on
    that channel, and must be resolved there — while staying live on a channel they left on."""
    state = ns.NotificationKind(
        kind="synthetic_category_state", category="breaking", mode="event",
        title_key="notifications.synthetic.title", predicate=lambda c: True,
        dedupe_key=lambda c: "s:1", payload=lambda c: {})
    _only(monkeypatch, state)
    ctx = ns.NotificationContext(
        now=FW_NOW,
        settings=ss.normalize_settings(
            {"notifications": {"categories": {"breaking": {"inApp": True, "push": False}}}}))

    assert "synthetic_category_state" not in ns.inactive_event_kinds(ctx, "in_app"), "on, so live"
    assert "synthetic_category_state" in ns.inactive_event_kinds(ctx, "push"), "off, so resolved"


# --------------------------------------------------------------------------------------------- #
# The settings copy may not promise a channel the platform does not have.
# --------------------------------------------------------------------------------------------- #
def test_no_notification_setting_promises_a_channel_that_does_not_exist():
    """`settings.notif.digestDesc` read "A short email rounding up your week." in all five catalogs.

    No email channel exists, and none ever has: `CHANNEL_SETTING_KEYS` is `{in_app, push}`, there is
    no `Channel` implementation besides `InAppChannel` and the push sender, no provider or SMTP
    dependency in the tree, no mail credentials in deploy, and no scheduler other than the opt-in
    DB-backup one. So the toggle was honest about WHETHER it fires — the gate and the dedupe work,
    and the reader does get "Your week in review" in the app — and dishonest about WHERE, promising
    a delivery the system cannot make. Reported by a reader who turned it on and waited.

    Keyed on the live channel registry rather than on a hardcoded verdict: the day an email channel
    is actually built and registered, this stops objecting on its own instead of having to be
    remembered and deleted."""
    import re
    channels = set(ns.CHANNEL_SETTING_KEYS)
    if {"email", "mail"} & channels:
        pytest.skip(f"an email channel exists now ({sorted(channels)}) — the copy may promise it")

    # e-mail / email / correo cover the five shipped catalogs.
    promises_mail = re.compile(r"\be-?mails?\b|\bcorreos?\b", re.IGNORECASE)
    offenders = []
    for lang in ("en", "es", "fr", "de", "pt"):
        cat = json.loads((ROOT / "web" / "messages" / f"{lang}.json").read_text(encoding="utf-8"))
        for key, value in cat.items():
            if key.startswith("settings.notif.") and promises_mail.search(value):
                offenders.append(f"{lang}:{key} = {value!r}")
    assert not offenders, (
        "notification settings copy promises email, but the delivery boundary has no email "
        f"channel (only {sorted(channels)}):\n  " + "\n  ".join(offenders))
