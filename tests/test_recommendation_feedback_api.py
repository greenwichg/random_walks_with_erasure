"""Tests for the B1 recommendation-feedback endpoints — POST + GET /api/me/recommendations/feedback.

Drives the real FastAPI app: auth (401 anonymous on both verbs), recording every feedback type, the
ack + list wire shapes, idempotency, an invalid type rejected (422), per-user isolation, a real
backend row written, and the invariant that recording feedback changes neither the recommendation
feed nor its order — feedback is recorded, never consumed (B1).
"""

import pathlib
import sys
import uuid

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
from fastapi.testclient import TestClient   # noqa: E402
import api_fastapi                          # noqa: E402

FB = "/api/me/recommendations/feedback"
TYPES = ("like", "dislike", "ignore", "read_later")


@pytest.fixture(scope="module")
def client():
    with TestClient(api_fastapi.app) as c:   # lifespan builds the backend + store
        yield c


_RUN = uuid.uuid4().hex[:8]   # unique per run: rows persist in the file DB across runs, so fresh
                             # accounts keep the exact-count assertions isolated from prior state


def _user(client, acct):
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": f"{acct}-{_RUN}"}).json()["userId"]
    return uid, {"X-IH-User-Id": str(uid)}


def test_feedback_requires_auth(client):
    assert client.post(FB, json={"articleId": "x", "feedback": "like"}).status_code == 401
    assert client.get(FB).status_code == 401


def test_records_every_feedback_type_and_is_idempotent(client):
    uid, hdr = _user(client, "fb-types")
    for t in TYPES:
        r = client.post(FB, json={"articleId": "https://a/x", "feedback": t}, headers=hdr)
        assert r.status_code == 200
        assert r.json() == {"ok": True, "feedback": t, "changed": True}     # each distinct type → a row
    # a repeat of the same signal is idempotent: still 200, but changed=False (no new row)
    assert client.post(FB, json={"articleId": "https://a/x", "feedback": "like"},
                       headers=hdr).json() == {"ok": True, "feedback": "like", "changed": False}

    rows = client.get(FB, headers=hdr).json()
    assert [x["feedback"] for x in rows] == list(TYPES)                       # oldest-first, one per type
    assert set(rows[0]) == {"articleId", "feedback", "createdAt", "updatedAt"}
    # the row really exists in the store, attributed to this user (the "backend row is created" proof)
    assert len(api_fastapi.state.store.list_recommendation_feedback(uid)) == 4


def test_invalid_feedback_type_is_rejected(client):
    _uid, hdr = _user(client, "fb-bad")
    assert client.post(FB, json={"articleId": "x", "feedback": "love"}, headers=hdr).status_code == 422
    assert client.post(FB, json={"articleId": "x"}, headers=hdr).status_code == 422   # missing feedback


def test_feedback_is_user_scoped(client):
    _uid_a, hdr_a = _user(client, "fb-iso-a")
    _uid_b, hdr_b = _user(client, "fb-iso-b")
    client.post(FB, json={"articleId": "https://a/only-a", "feedback": "dislike"}, headers=hdr_a)
    assert [x["articleId"] for x in client.get(FB, headers=hdr_a).json()] == ["https://a/only-a"]
    assert client.get(FB, headers=hdr_b).json() == []                        # B sees nothing of A's


def test_feedback_does_not_change_the_recommendation_feed(client):
    """Recording feedback is inert to serving: the recommendation feed and its order are identical
    before and after every feedback type — feedback is recorded, never consumed (B1)."""
    _uid, hdr = _user(client, "fb-ranking")
    before = client.get("/api/recommendations", headers=hdr).json()
    ids_before = [r["article"]["id"] for r in before]
    for t in TYPES:
        aid = ids_before[0] if ids_before else "https://a/none"
        assert client.post(FB, json={"articleId": aid, "feedback": t}, headers=hdr).status_code == 200
    after = client.get("/api/recommendations", headers=hdr).json()
    assert [r["article"]["id"] for r in after] == ids_before                 # same recs, same order
    # Everything the recommender controls (strategy, crossCutting, reason, article content) is
    # identical. The ONLY field that moves is the synthetic demo article's `publishedAt` — a wall-clock
    # `_iso_recent` estimate that varies between ANY two calls, feedback or not — so normalise it out
    # before the deep compare, proving feedback changed nothing about the feed itself.
    def _norm(feed):
        return [{**r, "article": {k: v for k, v in r["article"].items() if k != "publishedAt"}} for r in feed]
    assert _norm(after) == _norm(before)                                     # feed identical apart from demo ts
