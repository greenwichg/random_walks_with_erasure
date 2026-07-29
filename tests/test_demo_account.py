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
def _content(report: dict) -> dict:
    """The report minus its timestamp and its provenance marker — what the reader actually sees."""
    return {k: v for k, v in report.items() if k not in ("updatedAt", "sample")}


def test_anonymous_report_is_the_exhibits_measured_report(client, exhibit):
    """Same CONTENT, different PROVENANCE. The exhibit's report is served to anonymous visitors
    unchanged — that is the showcase — but the payload now says whose it is, so nothing downstream
    can present it as the viewer's own measurement."""
    uid, h = exhibit
    anon = client.get("/api/report").json()
    own = client.get("/api/report", headers=h).json()
    assert anon["mode"] == "measured"
    assert _content(anon) == _content(own)
    assert anon.get("sample") is True, "served to someone who is not the exhibit"
    assert "sample" not in own, "the exhibit viewing its own report is not viewing a sample"


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


# --------------------------------------------------------------------------- #
# Provenance — the exhibit's numbers must never be presented as somebody else's measurement.
# --------------------------------------------------------------------------- #
def test_a_signed_in_reader_with_no_history_is_told_the_report_is_not_theirs(client, exhibit):
    """The bug this marker exists for.

    A brand-new beta tester signed in, opened the Health Report, and saw "Measured · based on 30
    reads" above a political distribution they had never produced. The report was real — it was the
    exhibit account's, which is genuinely measured over 30 reads — and nothing in the payload said
    so, so the UI rendered it as theirs.

    The fallback itself is deliberate (a cold Health Report page is worse than an example one). What
    was not deliberate was asserting a measurement about someone who had read nothing."""
    _, h = _user(client, "brand-new-no-history")
    body = client.get("/api/report", headers=h).json()

    exhibit_report = client.get("/api/report").json()
    assert body["mode"] == "measured", "this is still the exhibit's genuinely measured report"
    assert body["coverage"]["reads"] == exhibit_report["coverage"]["reads"] > 0, (
        "and it still carries the EXHIBIT's read count, not this reader's zero — which is precisely "
        "why the payload has to say whose it is")
    assert body.get("sample") is True, (
        "so the payload MUST say it is not this reader's — without this the UI claims "
        "'Measured, based on 30 reads' for a reader with zero reads")


def test_the_exhibit_itself_is_never_marked_a_sample(client, exhibit):
    """The flag is keyed on the VIEWER, not on the report. The exhibit account looking at its own
    report is looking at its own measurement."""
    _, h = exhibit
    assert "sample" not in client.get("/api/report", headers=h).json()


def test_an_onboarded_reader_estimate_is_not_marked_a_sample(client, exhibit):
    """An Estimate is the reader's own — computed from outlets THEY chose. It is not a measurement
    and does not claim to be, but it is also not somebody else's data."""
    _, h = _user(client, "onboarded-not-a-sample")
    names = [o["id"] for o in client.get("/api/outlets").json()][:3]
    client.post("/api/me/onboarding", json={"outlets": names}, headers=h)
    body = client.get("/api/report", headers=h).json()
    assert body["mode"] == "estimate"
    assert "sample" not in body


def test_the_dashboard_carries_the_same_marker(client, exhibit):
    """The dashboard renders the same Measured chip from the same routing, so it would make the
    same false claim. `build_dashboard` rebuilds the payload, so the marker has to be carried
    explicitly — a test rather than a comment, because that copy is easy to lose."""
    _, h = _user(client, "brand-new-dashboard")
    assert client.get("/api/dashboard", headers=h).json().get("sample") is True
    _, eh = exhibit
    assert "sample" not in client.get("/api/dashboard", headers=eh).json()
