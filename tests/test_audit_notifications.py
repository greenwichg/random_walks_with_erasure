"""The notifications pipeline audit — examples/audit_notifications.py.

The probe exists because "no notification arrived" has at least six causes that are
indistinguishable from a browser, and three of them are switches that default to off. Its whole
value is naming WHICH stage stopped, so the properties worth pinning are that each verdict is
reachable and that it names the right one — a probe that always blames the last thing it checked
would send an operator to the wrong layer with confidence.
"""
import io
import contextlib
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import audit_notifications as an     # noqa: E402
import notification_service as ns    # noqa: E402
import store as store_mod            # noqa: E402


@pytest.fixture()
def st(tmp_path):
    return store_mod.Store(f"sqlite:///{tmp_path / 'notif.db'}")


@pytest.fixture()
def uid(st):
    return st.upsert_user_by_identity("google", "probe", email="probe@example.com").id


def _verdict(cfg_over=None, tables_over=None) -> str:
    cfg = {"breaking": True, "registration": True, "delivery": True,
           "vapid_public": True, "vapid_private": True, "vapid_subject": True}
    tables = {"notifications": 1, "subscriptions": 1, "deliveries": 1}
    cfg.update(cfg_over or {})
    tables.update(tables_over or {})
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        an.verdict(cfg, tables)
    return buf.getvalue()


def test_registration_off_is_blamed_before_anything_downstream():
    """With the switch off no subscription CAN exist, so reporting a sending problem would send the
    operator to VAPID keys for a pipeline that stops two stages earlier."""
    out = _verdict({"registration": False}, {"subscriptions": 0, "deliveries": 0})
    assert "STOPS AT REGISTRATION" in out
    assert "STOPS AT SENDING" not in out


def test_no_subscription_is_blamed_on_the_browser_not_the_keys():
    out = _verdict({}, {"subscriptions": 0, "deliveries": 0})
    assert "no subscription exists" in out
    assert "STOPS AT SENDING" not in out


def test_missing_send_credentials_are_named_individually():
    """`_sender()` returns None if ANY of the three is missing, and it does so silently on a
    background thread. Naming which one is the entire difference between a fix and a search."""
    out = _verdict({"vapid_subject": False}, {"deliveries": 0})
    assert "STOPS AT SENDING" in out and "RWE_VAPID_SUBJECT" in out
    assert "RWE_VAPID_PRIVATE_KEY" not in out.split("STOPS AT SENDING")[1].split("\n")[0]


def test_everything_on_and_still_silent_points_at_the_poller():
    """The fan-out has no scheduler of its own — it rides the poller's post-cycle seam. With every
    switch on and no delivery row, the poller is the only remaining suspect."""
    out = _verdict({}, {"deliveries": 0})
    assert "CONFIGURED AND SILENT" in out and "RWE_FEED_POLL" in out


def test_in_app_rows_existing_moves_the_blame_to_the_browser():
    """In-app has no flag and no worker, so rows existing means the engine did its job. The badge
    query has no polling and no focus refetch, which is where a reader's 'nothing appeared' lives."""
    out = _verdict({}, {"notifications": 5})
    assert "in-app  PRODUCING" in out and "no polling" in out.lower()


def test_in_app_with_no_rows_sends_the_operator_to_the_per_reader_gates():
    out = _verdict({}, {"notifications": 0})
    assert "NOTHING MATERIALISED" in out and "--email" in out


def test_breaking_off_is_reported_even_when_everything_else_works():
    """It is orthogonal: the other six kinds fire without it, so a healthy in-app verdict must not
    hide the fact that no breaking story will ever notify anyone."""
    out = _verdict({"breaking": False}, {"notifications": 5})
    assert "in-app  PRODUCING" in out
    assert "RWE_BREAKING_NOTIFICATIONS is not set" in out


def test_evaluation_names_the_gate_for_every_shipped_kind(st, uid, capsys):
    """The stage no table can answer. A preference that was never written and one written `false`
    are the same silence to `_gated`, so the probe has to print the resolved path and the verdict
    for BOTH channels of every kind."""
    an.report_reader(st, uid)
    out = capsys.readouterr().out
    for k in ns.NOTIFICATION_KINDS:
        assert k.kind in out, f"{k.kind} missing from the evaluation table"
        assert ns.gate_path(k, ns.IN_APP) in out
    # The push channel of a category kind resolves to a DIFFERENT path than its in-app channel —
    # the whole reason `gate_path` takes a channel. Pinning both stops the probe from quietly
    # reporting one channel's answer for the other.
    assert "notifications.categories.breaking.push" in out
    assert "notifications.categories.breaking.inApp" in out


def test_push_defaults_to_denied_for_a_fresh_reader(st, uid, capsys):
    """Documented, and the fourth independent gate: every category ships `push: false`, so a reader
    who subscribes a device still receives nothing until they turn a category on. No deploy config
    changes this, which is why the probe reports it per reader rather than only per process."""
    an.report_reader(st, uid)
    out = capsys.readouterr().out
    push_line = next(ln for ln in out.splitlines()
                     if "notifications.categories.breaking.push" in ln)
    assert "DENY" in push_line


def test_the_probe_writes_nothing(st, uid):
    """It runs against production. Every table it touches must have the same count afterwards."""
    from sqlalchemy import func, select
    def counts():
        with st.session() as s:
            return tuple(int(s.scalar(select(func.count()).select_from(m)) or 0)
                         for m in (store_mod.Notification, store_mod.NotificationEvent,
                                   store_mod.PushSubscription, store_mod.NotificationDelivery))
    before = counts()
    with contextlib.redirect_stdout(io.StringIO()):
        an.report_tables(st, stage="head")
        an.report_reader(st, uid)
        an.report_tables(st, stage="tail")
    assert counts() == before


def test_secrets_are_never_printed(monkeypatch, capsys):
    monkeypatch.setenv("RWE_VAPID_PRIVATE_KEY", "super-secret-private-key")
    monkeypatch.setenv("RWE_VAPID_PUBLIC_KEY", "a-public-key-value")
    an.report_config()
    out = capsys.readouterr().out
    assert "super-secret-private-key" not in out and "a-public-key-value" not in out
    assert "set" in out


def test_breaking_off_explains_an_empty_ledger_instead_of_blaming_the_poller():
    """Push can only carry a kind with a `fanout`, and `breaking_story` is the only one — so with
    breaking detection off an empty delivery ledger is the CORRECT state, not a fault.

    Before this branch existed the ladder fell through to "check RWE_FEED_POLL", which is a healthy
    subsystem here, while stages 1 and 2 of the same report had already named the real cause.
    Observed misdirecting on production 2026-08-09: registration on, keys set, 2 subscriptions,
    0 deliveries, breaking OFF."""
    out = _verdict({"breaking": False}, {"subscriptions": 2, "deliveries": 0})
    assert "EXPECTEDLY SILENT" in out
    assert "RWE_BREAKING_NOTIFICATIONS" in out
    assert "RWE_FEED_POLL" not in out, "the poller is not the suspect when there is nothing to carry"
    assert "CONFIGURED AND SILENT" not in out


def test_the_poller_verdict_survives_when_breaking_is_on():
    """The new branch must not swallow the case it was inserted in front of."""
    out = _verdict({"breaking": True}, {"subscriptions": 2, "deliveries": 0})
    assert "CONFIGURED AND SILENT" in out and "RWE_FEED_POLL" in out
    assert "EXPECTEDLY SILENT" not in out
