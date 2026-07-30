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
    registry_order = [k.kind for k in ns.NOTIFICATION_KINDS]
    fired = _kinds(ns.evaluate(_ctx()))
    assert fired == registry_order            # all fire here -> full registry order, deterministic


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
    """The boundary must be able to exclude them explicitly rather than by asking "is it an event?"."""
    assert hasattr(ns, "DISCRETE_KINDS")
    assert set(ns.DISCRETE_KINDS) & set(ns.EVENT_KINDS) == set(), "a kind is one lifecycle, not two"


def test_event_inputs_default_empty_and_carry_events():
    """The context grew a substructure; nothing that omits it may change behaviour."""
    assert ns.NotificationContext(now=FW_NOW, settings={}).events.events == ()
    ev = {"id": 1, "sourceType": "story_breaking", "sourceId": "st_a", "payload": {"title": "T"}}
    assert _fw_ctx(events=[ev]).events.events == (ev,)


def test_the_six_shipped_kinds_are_untouched_by_fan_out():
    """The regression that matters most: every existing kind takes the single-notification path, and
    none of them acquired a fanout, a cap, or a new mode."""
    for k in ns.NOTIFICATION_KINDS:
        assert k.fanout is None, f"{k.kind} must stay a single kind"
        assert k.max_per_day is None, f"{k.kind} must stay uncapped"
        assert k.mode in ("cadence", "event"), f"{k.kind} has an unexpected mode {k.mode!r}"
    assert ns.DISCRETE_KINDS == (), "A3a adds the capability, A3b adds the first user"
