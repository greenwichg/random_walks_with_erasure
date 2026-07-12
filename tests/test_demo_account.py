"""The read-only exhibit account (RWE_DEMO_ACCOUNT) — Option E of the demo-architecture review.

Pins the whole contract: the serving seam (anonymous + below-threshold readers get the seeded
account's MEASURED report through the unchanged pipeline; ?user= and the Estimate rung keep
precedence; unseeded = the synthetic fallback), the one-way write lock (provisioning flows
through the public pipeline while the account is empty; once measured, every administrative
/api/me mutation is a typed 403 — one middleware site), the interaction no-ops (opened/shown
record NOTHING for the exhibit yet answer 200), and non-interference (anonymous traffic can
never move the exhibit's report). Flag-off behaviour is pinned by the entire existing suite,
which runs with RWE_DEMO_ACCOUNT unset.
"""
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

os.environ.setdefault("RWE_DB_URL", "sqlite://")

from fastapi.testclient import TestClient  # noqa: E402

import api_fastapi  # noqa: E402

IDENTITY = "dev:demo-exhibit@test"


def _strip(report: dict) -> dict:
    return {k: v for k, v in report.items() if k != "updatedAt"}


@pytest.fixture(scope="module")
def client():
    os.environ["RWE_DEMO_ACCOUNT"] = IDENTITY
    try:
        with TestClient(api_fastapi.app) as c:
            yield c
    finally:
        os.environ.pop("RWE_DEMO_ACCOUNT", None)


@pytest.fixture(scope="module")
def exhibit(client):
    """Seed the exhibit through the normal public pipeline — the pre-lock provisioning window."""
    uid = client.post("/api/internal/users",
                      json={"provider": "dev", "providerAccountId": "demo-exhibit@test",
                            "email": "demo-exhibit@test", "displayName": "Demo Reader"}
                      ).json()["userId"]
    assert uid == api_fastapi.state.demo_uid          # lifespan resolved the same account
    h = {"X-IH-User-Id": str(uid)}
    reads = [{"url": f"https://exhibit.example/politics/{k}",
              "title": f"exhibit read {k}", "outlet": "The Guardian"} for k in range(6)]
    r = client.post("/api/me/reads", json={"reads": reads}, headers=h)
    assert r.status_code == 200 and r.json()["sufficient"] is True   # seeding was writable
    return uid, h


def _user(client, account):
    uid = client.post("/api/internal/users",
                      json={"provider": "dev", "providerAccountId": account,
                            "email": f"{account}@x", "displayName": account}).json()["userId"]
    return uid, {"X-IH-User-Id": str(uid)}


# --------------------------------------------------------------------------- #
# The serving seam.
# --------------------------------------------------------------------------- #
def test_anonymous_report_is_the_exhibits_measured_report(client, exhibit):
    uid, h = exhibit
    anon = client.get("/api/report").json()
    own = client.get("/api/report", headers=h).json()
    assert anon["mode"] == "measured"
    assert _strip(anon) == _strip(own)


def test_explicit_user_param_still_wins(client, exhibit):
    # every report serializes mode="measured"; the ?user= exception shows in the CONTENT —
    # row 3's synthetic report, not the exhibit's (the row picker stays an exhibit browser)
    row = client.get("/api/report", params={"user": "3"}).json()
    exhibit_report = client.get("/api/report").json()
    assert _strip(row) != _strip(exhibit_report)
    assert row["coverage"]["reads"] != exhibit_report["coverage"]["reads"] or \
        row["overall"] != exhibit_report["overall"]


def test_below_threshold_reader_gets_the_exhibit(client, exhibit):
    _, h = _user(client, "fresh-below-threshold")
    body = client.get("/api/report", headers=h).json()
    assert body["mode"] == "measured"
    assert _strip(body) == _strip(client.get("/api/report").json())


def test_onboarded_reader_keeps_their_estimate(client, exhibit):
    _, h = _user(client, "onboarded-below-threshold")
    names = [o["id"] for o in client.get("/api/outlets").json()][:3]
    assert client.post("/api/me/onboarding", json={"outlets": names}, headers=h).status_code == 200
    assert client.get("/api/report", headers=h).json()["mode"] == "estimate"


def test_recommendations_and_coach_follow_the_seam(client, exhibit):
    uid, h = exhibit
    anon = client.get("/api/recommendations").json()
    own = client.get("/api/recommendations", headers=h).json()
    assert [r["article"]["id"] for r in anon] == [r["article"]["id"] for r in own]
    g_anon = client.get("/api/coach").json()[0]
    g_own = client.get("/api/coach", headers=h).json()[0]
    assert g_anon["content"] == g_own["content"]
    assert g_anon.get("citations") == g_own.get("citations")


# --------------------------------------------------------------------------- #
# The one-way write lock (administrative mutations -> typed 403).
# --------------------------------------------------------------------------- #
def test_admin_mutations_are_403_once_measured(client, exhibit):
    uid, h = exhibit
    calls = [
        ("POST", "/api/me/reads", {"reads": [{"url": "https://x.example/a", "title": "t",
                                              "outlet": "AP"}]}),
        ("POST", "/api/me/settings", {"readingGoalMinutes": 25}),
        ("POST", "/api/me/onboarding", {"outlets": ["AP"]}),
        ("POST", "/api/me/saved", {"article": {"id": "a1", "headline": "h", "publisher": "AP",
                                               "publisherLean": 0.0, "topic": "Politics",
                                               "lean": 0.0}}),
        ("DELETE", "/api/me/saved", {"article": {"id": "a1"}}),
        ("POST", "/api/me/tokens", {"name": "probe"}),
    ]
    for method, path, body in calls:
        r = client.request(method, path, json=body, headers=h)
        assert r.status_code == 403, f"{method} {path} -> {r.status_code}"
        assert r.json()["error"]["code"] == "demo_account_read_only"


def test_normal_users_are_untouched_by_the_guard(client, exhibit):
    _, h = _user(client, "normal-writer")
    r = client.post("/api/me/reads", json={"reads": [{"url": "https://n.example/1",
                                                      "title": "t", "outlet": "AP"}]}, headers=h)
    assert r.status_code == 200
    assert client.post("/api/me/settings", json={"readingGoalMinutes": 30},
                       headers=h).status_code == 200


# --------------------------------------------------------------------------- #
# Interaction telemetry: successful no-ops, zero rows.
# --------------------------------------------------------------------------- #
def test_interaction_endpoints_answer_200_and_write_nothing(client, exhibit):
    uid, h = exhibit
    st = api_fastapi.state.store
    before = len(st.list_rec_events(uid))
    recs = client.get("/api/recommendations", headers=h)          # shown-recorder must skip
    assert recs.status_code == 200 and recs.json()
    art = recs.json()[0]["article"]["id"]
    r = client.post("/api/me/recommendations/opened",
                    json={"articleId": art, "crossCutting": True}, headers=h)
    assert r.status_code == 200                                   # successful no-op
    assert set(r.json()) >= {"shownCross", "openedCross", "rate"}
    assert len(st.list_rec_events(uid)) == before == 0


# --------------------------------------------------------------------------- #
# Non-interference: anonymous traffic can never move the exhibit.
# --------------------------------------------------------------------------- #
def test_anonymous_traffic_cannot_move_the_exhibit(client, exhibit):
    before = _strip(client.get("/api/report").json())
    for _ in range(3):
        client.get("/api/report")
        client.get("/api/recommendations")
        client.post("/api/coach", json={"message": "am I balanced?"})
    assert _strip(client.get("/api/report").json()) == before
