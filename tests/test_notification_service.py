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
         reads_this_week=6):
    """A context in which — with all toggles on and nothing delivered — every kind fires."""
    return ns.NotificationContext(
        now=now,
        settings=settings if settings is not None else _settings(),
        delivery=ns.DeliveryState(delivered_keys=frozenset(delivered)),
        report=ns.ReportInputs(has_report=has_report, overall=overall, blind_spots=blind_spots),
        recommendations=ns.RecommendationInputs(unopened_count=unopened_count),
        reading=ns.ReadingInputs(streak_days=streak_days, read_today=read_today,
                                 reads_this_week=reads_this_week))


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
    # streak reminder needs an active streak AND nothing read today
    assert "streak_reminder" not in _kinds(ns.evaluate(_ctx(streak_days=0)))
    assert "streak_reminder" not in _kinds(ns.evaluate(_ctx(read_today=True)))
    assert "streak_reminder" in _kinds(ns.evaluate(_ctx(streak_days=1, read_today=False)))


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
