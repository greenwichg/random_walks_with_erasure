"""RC2.3 — improvement-recommendation lifecycle ledger.

Three layers:
  * the pure, deterministic reconciler (state machine + completion rule),
  * the store round-trip (persistence, idempotent upsert, set-once timestamps),
  * the API end-to-end (report annotation, stable IDs across regeneration, accept/dismiss, GET ledger).
"""
import importlib.util
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "examples" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


il = _load("improvement_ledger")
store = _load("store")
api_fastapi = _load("api_fastapi")


# --------------------------------------------------------------------------- #
# 1) pure reconciler — deterministic state machine
# --------------------------------------------------------------------------- #
_CUR = [{"recKey": "imp_topicDiversity", "metric": "topicDiversity"},
        {"recKey": "imp_sourceDiversity", "metric": "sourceDiversity"}]


def test_completion_rule_is_deterministic_and_metric_based():
    # reached benchmark AND improved by >= margin → complete
    assert il.is_completed(20, 55) is True
    # reached benchmark but did not improve enough → not complete
    assert il.is_completed(48, 51) is False        # +3 < margin 5
    # improved a lot but still below benchmark → not complete
    assert il.is_completed(10, 40) is False
    # unknown scores never complete
    assert il.is_completed(None, 80) is False and il.is_completed(30, None) is False


def test_generated_shown_accepted_in_progress_completed():
    ann, upd = il.reconcile(_CUR, {}, {"topicDiversity": 20, "sourceDiversity": 30}, "T0")
    assert ann["imp_topicDiversity"]["state"] == "shown"
    assert ann["imp_topicDiversity"]["generatedAt"] == "T0" and ann["imp_topicDiversity"]["firstScore"] == 20

    led = {k: dict(v) for k, v in upd.items()}
    led["imp_topicDiversity"]["acceptedAt"] = "T1"                 # reader accepts
    ann2, _ = il.reconcile(_CUR, led, {"topicDiversity": 20, "sourceDiversity": 30}, "T2")
    assert ann2["imp_topicDiversity"]["state"] == "accepted"      # accepted, no progress yet
    ann3, upd3 = il.reconcile(_CUR, led, {"topicDiversity": 24, "sourceDiversity": 30}, "T3")
    assert ann3["imp_topicDiversity"]["state"] == "in_progress"   # score ticked up

    led2 = {k: dict(v) for k, v in upd3.items()}
    ann4, _ = il.reconcile(_CUR, led2, {"topicDiversity": 55, "sourceDiversity": 30}, "T4")
    assert ann4["imp_topicDiversity"]["state"] == "completed"     # >=50 and +>=5 from first 20
    assert ann4["imp_topicDiversity"]["completedScore"] == 55


def test_dismissed_path():
    ann, upd = il.reconcile(_CUR, {}, {"topicDiversity": 20, "sourceDiversity": 30}, "T0")
    led = {k: dict(v) for k, v in upd.items()}
    led["imp_topicDiversity"]["dismissedAt"] = "T1"
    ann2, _ = il.reconcile(_CUR, led, {"topicDiversity": 20, "sourceDiversity": 30}, "T2")
    assert ann2["imp_topicDiversity"]["state"] == "dismissed"


def test_expired_when_dropped_with_no_replacement():
    _, upd = il.reconcile(_CUR, {}, {"topicDiversity": 20, "sourceDiversity": 30}, "T0")
    led = {k: dict(v) for k, v in upd.items()}
    # next report generates only sourceDiversity (topic dropped out, nothing new entered)
    cur2 = [{"recKey": "imp_sourceDiversity", "metric": "sourceDiversity"}]
    _, upd2 = il.reconcile(cur2, led, {"topicDiversity": 20, "sourceDiversity": 30}, "T1")
    assert upd2["imp_topicDiversity"]["state"] == "expired"
    assert upd2["imp_topicDiversity"]["expiredAt"] == "T1"


def test_superseded_when_a_new_rec_takes_the_slot():
    _, upd = il.reconcile(_CUR, {}, {"topicDiversity": 20, "sourceDiversity": 30}, "T0")
    led = {k: dict(v) for k, v in upd.items()}
    # topic leaves, a brand-new echoChamber rec enters
    cur2 = [{"recKey": "imp_sourceDiversity", "metric": "sourceDiversity"},
            {"recKey": "imp_echoChamber", "metric": "echoChamber"}]
    _, upd2 = il.reconcile(cur2, led, {"topicDiversity": 20, "sourceDiversity": 30, "echoChamber": 25}, "T1")
    assert upd2["imp_topicDiversity"]["state"] == "superseded"
    assert upd2["imp_topicDiversity"]["supersededBy"] == "imp_echoChamber"


def test_departed_rec_completes_over_supersession():
    # a rec that leaves the set but whose metric crossed the completion bar is COMPLETED, not superseded
    _, upd = il.reconcile(_CUR, {}, {"topicDiversity": 20, "sourceDiversity": 30}, "T0")
    led = {k: dict(v) for k, v in upd.items()}
    cur2 = [{"recKey": "imp_sourceDiversity", "metric": "sourceDiversity"},
            {"recKey": "imp_echoChamber", "metric": "echoChamber"}]
    _, upd2 = il.reconcile(cur2, led, {"topicDiversity": 60, "sourceDiversity": 30, "echoChamber": 25}, "T1")
    assert upd2["imp_topicDiversity"]["state"] == "completed"


def test_reconcile_is_deterministic():
    a = il.reconcile(_CUR, {}, {"topicDiversity": 20, "sourceDiversity": 30}, "T0")
    b = il.reconcile(_CUR, {}, {"topicDiversity": 20, "sourceDiversity": 30}, "T0")
    assert a == b


def test_terminal_rows_are_not_revived():
    completed = {"imp_topicDiversity": {"recKey": "imp_topicDiversity", "metric": "topicDiversity",
                                        "state": "completed", "firstScore": 20, "completedAt": "T0"}}
    # a later report that no longer generates it must leave the terminal row untouched
    _, upd = il.reconcile([], completed, {"topicDiversity": 10}, "T9")
    assert "imp_topicDiversity" not in upd            # terminal → skipped


# --------------------------------------------------------------------------- #
# 2) store round-trip — persistence
# --------------------------------------------------------------------------- #
def test_store_persists_and_upserts(tmp_path):
    s = store.Store(f"sqlite:///{tmp_path / 'ih.db'}")
    uid = s.upsert_user_by_identity("google", "led-1").id

    # a report reconcile creates the row (generated + shown) at T0
    s.save_improvement_lifecycle(uid, [{"recKey": "imp_sourceDiversity", "metric": "sourceDiversity",
                                        "state": "shown", "firstScore": 30, "currentScore": 30,
                                        "generatedAt": "T0", "shownAt": "T0"}])
    # the reader accepts at T1 (row already exists → not newly created)
    assert s.record_improvement_lifecycle_event(uid, "imp_sourceDiversity", "sourceDiversity",
                                                 "accepted", at="T1") is False
    row = {r["recKey"]: r for r in s.list_improvement_lifecycle(uid)}["imp_sourceDiversity"]
    assert row["state"] == "accepted" and row["acceptedAt"] == "T1"
    assert row["generatedAt"] == "T0" and row["firstScore"] == 30

    # set-once: a later reconcile must NOT overwrite generatedAt/firstScore/acceptedAt, but shownAt
    # legitimately refreshes each serve.
    s.save_improvement_lifecycle(uid, [{"recKey": "imp_sourceDiversity", "metric": "sourceDiversity",
                                        "state": "in_progress", "firstScore": 99, "generatedAt": "TX",
                                        "shownAt": "T5"}])
    row = {r["recKey"]: r for r in s.list_improvement_lifecycle(uid)}["imp_sourceDiversity"]
    assert row["firstScore"] == 30 and row["generatedAt"] == "T0" and row["shownAt"] == "T5"
    assert row["acceptedAt"] == "T1"


# --------------------------------------------------------------------------- #
# 3) API end-to-end — annotation, stable IDs, regeneration, accept/dismiss, GET
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def client():
    with TestClient(api_fastapi.app) as c:
        yield c


def _measured_uid(client, tag):
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": tag}).json()["userId"]
    reads = [{"url": f"https://ex-{tag}-{i}.com/politics/story-{i}"} for i in range(6)]
    client.post("/api/me/reads", json={"reads": reads}, headers={"X-IH-User-Id": str(uid)})
    return uid


def test_report_annotates_lifecycle_and_ids_are_stable(client):
    uid = _measured_uid(client, "rc23-a")
    hdr = {"X-IH-User-Id": str(uid)}
    r1 = client.get("/api/report", headers=hdr).json()
    assert r1["mode"] == "measured" and r1["improvements"]
    for imp in r1["improvements"]:
        lc = imp["lifecycle"]
        assert lc["recKey"] == imp["id"] == f"imp_{imp['metric']}"     # stable, metric-based id
        assert lc["state"] in {"shown", "accepted", "in_progress", "completed", "dismissed"}
        assert lc["generatedAt"] and lc["firstScore"] is not None

    # regeneration: same recKeys, generatedAt stable, persisted to the ledger
    gen1 = {imp["id"]: imp["lifecycle"]["generatedAt"] for imp in r1["improvements"]}
    r2 = client.get("/api/report", headers=hdr).json()
    gen2 = {imp["id"]: imp["lifecycle"]["generatedAt"] for imp in r2["improvements"]}
    assert set(gen1) == set(gen2) and gen1 == gen2                     # generatedAt is set-once
    ledger = {row["recKey"] for row in client.get("/api/me/recommendations/improvements",
                                                  headers=hdr).json()}
    assert set(gen1) <= ledger                                         # persisted


def test_accept_and_dismiss_are_recorded_and_reflected(client):
    uid = _measured_uid(client, "rc23-b")
    hdr = {"X-IH-User-Id": str(uid)}
    rep = client.get("/api/report", headers=hdr).json()
    key = rep["improvements"][0]["id"]

    ack = client.post(f"/api/me/recommendations/improvements/{key}/accept", headers=hdr).json()
    assert ack["ok"] and ack["event"] == "accepted"
    rep2 = client.get("/api/report", headers=hdr).json()
    lc = {imp["id"]: imp["lifecycle"] for imp in rep2["improvements"]}[key]
    assert lc["state"] in {"accepted", "in_progress", "completed"} and lc["acceptedAt"]

    key2 = rep["improvements"][-1]["id"]
    client.post(f"/api/me/recommendations/improvements/{key2}/dismiss", headers=hdr)
    rep3 = client.get("/api/report", headers=hdr).json()
    lc2 = {imp["id"]: imp["lifecycle"] for imp in rep3["improvements"]}[key2]
    assert lc2["state"] in {"dismissed", "completed"} and lc2["dismissedAt"]


def test_lifecycle_endpoints_require_auth(client):
    assert client.get("/api/me/recommendations/improvements").status_code == 401
    assert client.post("/api/me/recommendations/improvements/imp_x/accept").status_code == 401
    # unknown event → 422 (validated at the edge)
    uid = _measured_uid(client, "rc23-c")
    bad = client.post("/api/me/recommendations/improvements/imp_x/frobnicate",
                      headers={"X-IH-User-Id": str(uid)})
    assert bad.status_code == 422


def test_anonymous_report_has_no_lifecycle(client):
    """A demo/anonymous report never carries lifecycle (no real user to track)."""
    rep = client.get("/api/report").json()
    assert all("lifecycle" not in imp for imp in rep["improvements"])
