"""M0 — characterization tests pinning the CURRENT coach (v1) contract.

These tests are the baseline every later "flag off = byte-identical" claim diffs against
(Coach v2 plan, docs/COACH_REDESIGN.md). They pin v1 exactly as it behaves today, including
its documented limitation: the reply is computed WITHOUT reading the question (the narrator
behavior the redesign exists to fix). Retire this file at M8b when the v1 path is deleted.

No LLM key is set in CI, so replies come from the deterministic `_grounded_fallback` path —
which is precisely what makes the message-blindness pinnable byte-for-byte.
"""
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

os.environ.setdefault("RWE_DB_URL", "sqlite://")     # ephemeral store for the app lifespan

from fastapi.testclient import TestClient  # noqa: E402

import api_fastapi  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(api_fastapi.app) as c:
        yield c


@pytest.fixture()
def no_llm(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def _measured_headers(client):
    """A real (measured) user: internal upsert + enough reads for the personal path."""
    uid = client.post("/api/internal/users",
                      json={"provider": "dev", "providerAccountId": "coach-contract",
                            "email": "coach-contract@x", "displayName": "Coach Contract"}
                      ).json()["userId"]
    h = {"X-IH-User-Id": str(uid)}
    reads = [{"url": f"https://coach-contract.example/politics/{k}",
              "title": f"coach contract read {k}", "outlet": "The Guardian"} for k in range(6)]
    r = client.post("/api/me/reads", json={"reads": reads}, headers=h)
    assert r.status_code == 200 and r.json()["sufficient"] is True
    return h


# --------------------------------------------------------------------------- #
# Greeting (GET /api/coach)
# --------------------------------------------------------------------------- #
def test_greeting_shape_demo_path(client, no_llm):
    body = client.get("/api/coach").json()
    assert isinstance(body, list) and len(body) == 1
    msg = body[0]
    assert msg["role"] == "assistant"
    assert msg["content"].startswith("Hi — I'm your Information Health guide.")
    assert isinstance(msg["citations"], list) and len(msg["citations"]) <= 2
    for c in msg["citations"]:
        assert set(c) >= {"metric", "value"}


# --------------------------------------------------------------------------- #
# Reply (POST /api/coach) — demo path
# --------------------------------------------------------------------------- #
def test_reply_shape_demo_path(client, no_llm):
    msg = client.post("/api/coach", json={"message": "hello coach"}).json()
    assert msg["role"] == "assistant" and msg["content"]
    assert isinstance(msg["citations"], list) and len(msg["citations"]) <= 2
    assert isinstance(msg["suggestions"], list) and len(msg["suggestions"]) <= 2
    for art in msg["suggestions"]:
        assert set(art) >= {"id", "headline", "publisher", "lean"}   # real Article payloads


def test_v1_is_message_blind_demo_path(client, no_llm):
    """THE narrator characterization: two very different questions produce the same content,
    citations, and suggestions (only id/createdAt differ). This is the defect Coach v2 fixes;
    v1 must keep behaving this way until the flag flips."""
    a = client.post("/api/coach", json={"message": "why is my echo chamber score low?"}).json()
    b = client.post("/api/coach", json={"message": "suggest something to read"}).json()
    assert a["content"] == b["content"]
    assert a["citations"] == b["citations"]
    assert [s["id"] for s in a["suggestions"]] == [s["id"] for s in b["suggestions"]]
    assert a["id"] != b["id"]                        # ids derive from the message text


# --------------------------------------------------------------------------- #
# Reply — measured (personal) path
# --------------------------------------------------------------------------- #
def test_reply_shape_measured_path(client, no_llm):
    h = _measured_headers(client)
    msg = client.post("/api/coach", json={"message": "how am I doing?"}, headers=h).json()
    assert msg["role"] == "assistant" and msg["content"]
    assert len(msg["citations"]) <= 2 and len(msg["suggestions"]) <= 2


def test_v1_is_message_blind_measured_path(client, no_llm):
    h = _measured_headers(client)
    a = client.post("/api/coach", json={"message": "what are my blind spots?"}, headers=h).json()
    b = client.post("/api/coach", json={"message": "compare my reading over time"}, headers=h).json()
    assert a["content"] == b["content"] and a["citations"] == b["citations"]
