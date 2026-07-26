"""M4 — the flag-gated API wiring for Coach v2 (POST /api/coach + RWE_COACH_V2).

DoD: with the flag OFF the wire reply is byte-identical to v1 — none of the additive fields
leak (the M0 characterization suite, tests/test_coach_v1_contract.py, stays green untouched
and remains the primary proof). With the flag ON, only the MEASURED (personal) path routes
through coach_service.coach_turn; the demo path and below-threshold readers stay v1. Every
v2 turn emits ONE structured observability record (event=coach_turn on logger "ih.api")
carrying intent / resolution / tools / failures / fallback / ms — read-only telemetry that
never changes the reply.
"""
import json
import logging
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

os.environ.setdefault("RWE_DB_URL", "sqlite://")     # ephemeral store for the app lifespan

from fastapi.testclient import TestClient  # noqa: E402

import api_fastapi  # noqa: E402

V2_FIELDS = {"intent", "resolution", "followUps", "cards", "echo"}


@pytest.fixture(scope="module")
def client():
    with TestClient(api_fastapi.app) as c:
        yield c


@pytest.fixture(scope="module")
def measured(client):
    """A real (measured) reader: internal upsert + enough reads to cross the personal threshold."""
    uid = client.post("/api/internal/users",
                      json={"provider": "dev", "providerAccountId": "coach-api",
                            "email": "coach-api@x", "displayName": "Coach Api"}).json()["userId"]
    h = {"X-IH-User-Id": str(uid)}
    reads = [{"url": f"https://coach-api.example/politics/{k}",
              "title": f"coach api read {k}", "outlet": "The Guardian"} for k in range(6)]
    r = client.post("/api/me/reads", json={"reads": reads}, headers=h)
    assert r.status_code == 200 and r.json()["sufficient"] is True
    return h


@pytest.fixture()
def no_llm(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


@pytest.fixture()
def v2_off(monkeypatch):
    monkeypatch.delenv("RWE_COACH_V2", raising=False)


@pytest.fixture()
def v2_on(monkeypatch):
    monkeypatch.setenv("RWE_COACH_V2", "1")


class _JsonLogCapture(logging.Handler):
    """logger "ih.api" has propagate=False, so caplog never sees it — attach directly."""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.events = []

    def emit(self, record):
        try:
            self.events.append(json.loads(record.getMessage()))
        except (ValueError, TypeError):
            pass


@pytest.fixture()
def api_log():
    h = _JsonLogCapture()
    logging.getLogger("ih.api").addHandler(h)
    yield h.events
    logging.getLogger("ih.api").removeHandler(h)


# --------------------------------------------------------------------------- #
# Flag OFF: nothing additive leaks onto the v1 wire (M0 remains the full proof).
# --------------------------------------------------------------------------- #
def test_flag_off_reply_carries_no_v2_fields(client, measured, no_llm, v2_off):
    msg = client.post("/api/coach", json={"message": "how am I doing?"}, headers=measured).json()
    assert msg["role"] == "assistant" and msg["content"]
    assert not (V2_FIELDS & set(msg)), f"v2 fields leaked with the flag off: {V2_FIELDS & set(msg)}"
    for c in msg.get("citations") or []:
        assert "source" not in c                     # CitationModel.source stays v2-only

    greeting = client.get("/api/coach", headers=measured).json()
    assert not (V2_FIELDS & set(greeting[0]))


def test_flag_off_emits_no_coach_turn_telemetry(client, measured, no_llm, v2_off, api_log):
    client.post("/api/coach", json={"message": "am I improving?"}, headers=measured)
    client.get("/api/coach", headers=measured)
    assert not [e for e in api_log
                if e.get("event") in ("coach_turn", "coach_greeting")]


# --------------------------------------------------------------------------- #
# Flag ON, measured path: the intent-routed reply.
# --------------------------------------------------------------------------- #
def test_flag_on_measured_reply_is_intentful(client, measured, no_llm, v2_on):
    r = client.post("/api/coach", json={"message": "why is my source diversity low?"},
                    headers=measured)
    assert r.status_code == 200
    msg = r.json()
    assert msg["role"] == "assistant" and msg["content"].strip()
    assert msg["intent"] == "EXPLAIN.metric" and msg["resolution"] == "rule"
    assert isinstance(msg["followUps"], list) and msg["followUps"]
    assert msg["echo"]["v"] == 1
    assert msg["echo"]["turns"][-1]["intent"] == "EXPLAIN.metric"
    assert msg["citations"], "EXPLAIN.metric must cite engine numbers"
    for c in msg["citations"]:
        assert set(c) >= {"metric", "value", "source"} and c["source"]

    again = client.post("/api/coach", json={"message": "why is my source diversity low?"},
                        headers=measured).json()
    assert again["id"] == msg["id"]                  # ids stay hash-stable, like v1


def test_flag_on_demo_path_stays_v1(client, no_llm, v2_on):
    msg = client.post("/api/coach", json={"message": "why is my source diversity low?"}).json()
    assert msg["role"] == "assistant" and msg["content"]
    assert not (V2_FIELDS & set(msg))
    assert len(msg.get("citations") or []) <= 2      # the v1 demo narrator shape


def test_flag_on_below_threshold_reader_stays_v1(client, no_llm, v2_on):
    uid = client.post("/api/internal/users",
                      json={"provider": "dev", "providerAccountId": "coach-api-thin",
                            "email": "coach-api-thin@x", "displayName": "Thin"}).json()["userId"]
    msg = client.post("/api/coach", json={"message": "am I balanced?"},
                      headers={"X-IH-User-Id": str(uid)}).json()
    assert not (V2_FIELDS & set(msg))                # no reads -> row path -> v1


# --------------------------------------------------------------------------- #
# The echo round-trip over the wire (D6: binding-only).
# --------------------------------------------------------------------------- #
def test_echo_round_trip_binds_the_offer(client, measured, no_llm, v2_on):
    a = client.post("/api/coach", json={"message": "why is my source diversity low?"},
                    headers=measured).json()
    b = client.post("/api/coach", json={"message": "yes, show me", "echo": a["echo"]},
                    headers=measured).json()
    assert b["intent"] == "ACT.suggest"
    assert b["echo"]["turns"][-1]["intent"] == "ACT.suggest"
    assert b["cards"], "the live feed serves recs here, so the offer must attach cards"
    for card in b["cards"]:                          # resolver-explained, RecommendationModel-valid
        assert card["explanation"]["type"] and card["strategy"]
    assert [s["id"] for s in b["suggestions"]] == [c["article"]["id"] for c in b["cards"]][:3]


# --------------------------------------------------------------------------- #
# M6 — the proactive greeting over the wire.
# --------------------------------------------------------------------------- #
def test_flag_on_default_greeting_keeps_v1_body_and_adds_chips(client, measured, no_llm,
                                                               v2_on, api_log):
    """No settings row -> the ladder falls through: today's greeting verbatim + chips, and one
    coach_greeting telemetry record carrying the shadow-trigger evidence."""
    body = client.get("/api/coach", headers=measured).json()
    assert isinstance(body, list) and len(body) == 1
    msg = body[0]
    assert msg["content"].startswith("Hi — I'm your Information Health guide.")
    assert msg["followUps"], "the default greeting must offer weakest-metric chips"
    assert "intent" not in msg and "echo" not in msg    # no proactive turn fired
    for c in msg.get("citations") or []:
        assert "source" not in c                        # v1 greeting citations, untouched
    events = [e for e in api_log if e.get("event") == "coach_greeting"]
    assert len(events) == 1
    rec = events[0]
    assert rec["trigger"] is None and rec["intent"] is None
    assert set(rec["shadow"]) == {"metricChange", "storyUpdate"}
    assert isinstance(rec["ms"], (int, float))


def test_flag_on_greeting_fires_weekly_review_after_settings(client, measured, no_llm,
                                                             v2_on, api_log):
    """A stored settings row + this week's reads -> the greeting IS a weekly-review coach turn.
    (Runs last against this measured user: the settings write persists in the module store.)"""
    r = client.post("/api/me/settings", json={"readingGoalMinutes": 25}, headers=measured)
    assert r.status_code == 200
    body = client.get("/api/coach", headers=measured).json()
    msg = body[0]
    assert msg["intent"] == "COMPARE.weekly_review"
    assert msg["citations"] and all(c.get("source") for c in msg["citations"])
    assert msg["echo"]["turns"][-1]["intent"] == "COMPARE.weekly_review"
    assert "25" in msg["content"]
    events = [e for e in api_log if e.get("event") == "coach_greeting"]
    assert events and events[-1]["trigger"] == "weekly_review_recap"
    assert events[-1]["tools"] == ["goals", "history", "trend"]


# --------------------------------------------------------------------------- #
# Observability: one structured record per v2 turn, read-only.
# --------------------------------------------------------------------------- #
def test_v2_turn_emits_structured_observability(client, measured, no_llm, v2_on, api_log):
    msg = client.post("/api/coach", json={"message": "am I improving?"},
                      headers=measured).json()
    events = [e for e in api_log if e.get("event") == "coach_turn"]
    assert len(events) == 1, f"expected exactly one coach_turn record, got {len(events)}"
    rec = events[0]
    assert set(rec) >= {"event", "requestId", "intent", "resolution",
                        "tools", "failures", "fallback", "ms"}
    assert rec["intent"] == msg["intent"] == "COMPARE.over_time"
    assert rec["resolution"] == msg["resolution"]
    assert isinstance(rec["tools"], list) and all(isinstance(t, str) for t in rec["tools"])
    assert rec["failures"] == []                     # no tool failed on this turn
    assert rec["fallback"] in (None, "missing_evidence", "gate")
    assert isinstance(rec["ms"], (int, float)) and rec["ms"] >= 0


# --------------------------------------------------------------------------- #
# M8a beta-walk regressions (2026-07-13): two defects found through the public
# API against the beta-replica corpus, fixed minimally, pinned here.
# --------------------------------------------------------------------------- #
def test_malformed_echo_turns_degrades_cold_never_500(client, measured, no_llm, v2_on):
    """M8a defect 1: the echo is untrusted client input, but only its version was
    validated — {"v": 1, "turns": "garbage"} reached the turn-append and crashed the
    request (str + list TypeError -> HTTP 500). A malformed ``turns`` must degrade
    exactly like a missing echo: cold turn, fresh echo rebuilt from scratch."""
    for bad in ("garbage", {"role": "coach"}, 42):
        r = client.post("/api/coach", json={"message": "why is it low?",
                                            "echo": {"v": 1, "turns": bad}},
                        headers=measured)
        assert r.status_code == 200, f"turns={bad!r} -> {r.status_code}"
        body = r.json()
        assert body["resolution"] == "unresolved"        # cold: the pronoun has no binding
        assert len(body["echo"]["turns"]) == 1           # rebuilt, not concatenated onto junk
        assert body["echo"]["turns"][-1]["role"] == "coach"


def test_source_diversity_cause_names_real_outlets(client, measured, no_llm, v2_on):
    """M8a defect 2: the report's ``sources`` rows key the outlet under "source", but the
    metric tool's citation labels and the drivers line both read .get("name") — the reply
    rendered "Your most-read outlets: None 25%, ..." and citations were labeled
    sourceShare.None. Pin the fix end-to-end over the wire."""
    body = client.post("/api/coach", json={"message": "why is my source diversity low?"},
                       headers=measured).json()
    assert body["intent"] == "EXPLAIN.metric"
    assert "None" not in body["content"]
    assert "The Guardian" in body["content"]             # the fixture reader's real outlet
    # wire citations are the top-8 curation, so sourceShare.* may not surface here — but any
    # citation label that does surface must never carry a None-keyed name again
    assert all(not str(c["metric"]).endswith(".None") for c in body["citations"] or [])
