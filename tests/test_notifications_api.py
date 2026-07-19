"""Tests for the N2 notification endpoints — GET /api/me/notifications + POST .../{id}/seen.

Drives the real FastAPI app: auth, materialise-on-read, the NotificationModel wire shape, the
``unseenOnly`` / ``limit`` filters, idempotent + user-scoped mark-seen, per-user isolation, and the
invariant that fetching notifications generates neither a recommendation nor a report.
"""

import pathlib
import sys
import uuid
from datetime import datetime, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
from fastapi.testclient import TestClient   # noqa: E402
import api_fastapi                          # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(api_fastapi.app) as c:   # lifespan builds the backend + store
        yield c


_RUN = uuid.uuid4().hex[:8]   # unique per run: notifications persist in the file DB across runs, so
                             # fresh accounts keep exact-count assertions isolated from prior state


def _user(client, acct):
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": f"{acct}-{_RUN}"}).json()["userId"]
    return uid, {"X-IH-User-Id": str(uid)}


def _seed(uid, *, topics=("Economy",)):
    """Seed persisted producers for a user directly on the app's store (report + settings + a read)."""
    st = api_fastapi.state.store
    st.save_settings(uid, {"weeklyReport": True, "monthlyReport": True,
                           "notifications": {"recommendations": True, "weeklyDigest": True,
                                             "streakReminders": True, "blindSpotAlerts": True}})
    st.save_report(uid, {"mode": "measured", "overall": 70,
                         "blindSpots": [{"topic": t, "gap": 0.4, "note": "n"} for t in topics]})
    st.add_read(uid, "https://ex.com/a", {"article_id": "https://ex.com/a", "outlet": "AP",
                "category": "Politics", "lean": 0.0, "political": True, "title": "t",
                "read_at": datetime.now(timezone.utc).isoformat()})


LIVE = {"weekly_report", "monthly_deep_dive", "weekly_digest", "blind_spot_alert"}
# Not expected from the base _seed: recommendations_waiting needs unopened rec events (none seeded
# here); streak_reminder can't fire under the current streak predicate.
ABSENT = {"recommendations_waiting", "streak_reminder"}


def test_notifications_require_auth(client):
    assert client.get("/api/me/notifications").status_code == 401
    assert client.post("/api/me/notifications/1/seen").status_code == 401


def test_get_materialises_and_returns_model_shape(client):
    uid, hdr = _user(client, "napi-get")
    _seed(uid)
    r = client.get("/api/me/notifications", headers=hdr)
    assert r.status_code == 200
    data = r.json()
    kinds = {d["kind"] for d in data}
    assert LIVE <= kinds and kinds.isdisjoint(ABSENT)    # 4 live kinds; neither absent kind appears
    one = data[0]
    assert set(one) == {"id", "kind", "titleKey", "payload", "createdAt", "seenAt", "gatedBy"}
    assert one["seenAt"] is None and isinstance(one["payload"], dict)
    # idempotent materialise: a second GET does not duplicate rows
    assert len(client.get("/api/me/notifications", headers=hdr).json()) == len(data)


def test_recommendations_waiting_appears_with_unopened_recs(client):
    uid, hdr = _user(client, "napi-waiting")
    _seed(uid)
    # surface two recs (unopened) directly on the store — no recommender is run
    api_fastapi.state.store.record_recommendations_shown(uid, [("art-1", False), ("art-2", True)])
    data = client.get("/api/me/notifications", headers=hdr).json()
    waiting = [d for d in data if d["kind"] == "recommendations_waiting"]
    assert len(waiting) == 1 and waiting[0]["payload"]["count"] == 2


def test_unseen_only_and_limit(client):
    uid, hdr = _user(client, "napi-filter")
    _seed(uid)
    alln = client.get("/api/me/notifications", headers=hdr).json()
    assert len(alln) >= 4
    assert len(client.get("/api/me/notifications?limit=2", headers=hdr).json()) == 2
    nid = alln[0]["id"]
    assert client.post(f"/api/me/notifications/{nid}/seen", headers=hdr).json()["changed"] is True
    unseen = client.get("/api/me/notifications?unseenOnly=true", headers=hdr).json()
    assert nid not in {d["id"] for d in unseen} and len(unseen) == len(alln) - 1


def test_mark_seen_idempotent_and_user_scoped(client):
    uid, hdr = _user(client, "napi-seen")
    _seed(uid)
    nid = client.get("/api/me/notifications", headers=hdr).json()[0]["id"]
    assert client.post(f"/api/me/notifications/{nid}/seen", headers=hdr).json()["changed"] is True
    assert client.post(f"/api/me/notifications/{nid}/seen", headers=hdr).json()["changed"] is False  # idempotent
    # a different user cannot touch this notification
    _uid2, hdr2 = _user(client, "napi-other")
    assert client.post(f"/api/me/notifications/{nid}/seen", headers=hdr2).json()["changed"] is False
    # still seen for the owner
    assert nid not in {d["id"] for d in client.get("/api/me/notifications?unseenOnly=true", headers=hdr).json()}


def test_per_user_isolation(client):
    uidA, hdrA = _user(client, "napi-isoA")
    uidB, hdrB = _user(client, "napi-isoB")
    _seed(uidA); _seed(uidB)
    a = {d["id"] for d in client.get("/api/me/notifications", headers=hdrA).json()}
    b = {d["id"] for d in client.get("/api/me/notifications", headers=hdrB).json()}
    assert a and b and a.isdisjoint(b)


def test_fetching_notifications_generates_no_recs_or_reports(client):
    """The GET must not invoke the recommender or the report engine — no new RecEvents / snapshots."""
    uid, hdr = _user(client, "napi-nogen")
    _seed(uid)
    st = api_fastapi.state.store
    reports_before = len(st.list_report_snapshots(uid, limit=1000))
    recs_before = len(st.list_rec_events(uid))
    client.get("/api/me/notifications", headers=hdr)
    assert len(st.list_report_snapshots(uid, limit=1000)) == reports_before
    assert len(st.list_rec_events(uid)) == recs_before
