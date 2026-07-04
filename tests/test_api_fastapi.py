"""HTTP-layer tests for the FastAPI re-host (examples/api_fastapi.py).

Verifies the FastAPI serving layer preserves the stdlib server's behaviour: same endpoints,
same query params, and responses that carry the same engine output as the Backend
serialisers. Skips cleanly when the optional serving deps aren't installed.
"""

import importlib.util
import json
import os
import pathlib
import sys

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Small, fast synthetic corpus for the app's startup build.
os.environ.setdefault("RWE_N_USERS", "150")
os.environ.setdefault("RWE_MAX_ITEMS", "400")
os.environ.setdefault("RWE_SEED", "0")
os.environ.setdefault("RWE_DB_URL", "sqlite://")   # ephemeral in-memory store for the app's lifespan

METRIC_KEYS = {
    "topicDiversity", "sourceDiversity", "reportingRatio", "emotionalBalance",
    "echoChamber", "viewpointBalance", "openMindedness", "confidence",
}
STRATEGIES = {"rwe-b", "rwe-d", "adaptive"}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


api_fastapi = _load("api_fastapi", ROOT / "examples" / "api_fastapi.py")


@pytest.fixture(scope="module")
def client():
    with TestClient(api_fastapi.app) as c:   # entering triggers lifespan → builds the backend
        yield c


# --------------------------------------------------------------------------- #
def test_health_ok(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and "profile" in body and body["eligibleReaders"] > 0


def test_report_serves_engine_output(client):
    r = client.get("/api/report")
    assert r.status_code == 200
    body = r.json()
    assert body["band"] in {"Healthy", "Fair", "Needs work", "Unknown"}
    assert {m["key"] for m in body["metrics"]} == METRIC_KEYS
    assert abs(sum(body["viewpoint"].values()) - 1.0) < 1e-6


def test_report_matches_backend_serializer(client):
    """The re-host faithfully serves the Backend serialiser (modulo the request timestamp)."""
    be = api_fastapi.state.backend
    http = client.get("/api/report").json()
    direct = be.report(be.demo_user)
    assert http["overall"] == direct["overall"]
    assert http["band"] == direct["band"]
    assert http["viewpoint"] == direct["viewpoint"]
    assert [(m["key"], m["score"]) for m in http["metrics"]] == [(m["key"], m["score"]) for m in direct["metrics"]]


def test_report_user_override(client):
    assert client.get("/api/report", params={"user": "0"}).status_code == 200


def test_recommendations_blend_and_strategy(client):
    blend = client.get("/api/recommendations").json()
    assert isinstance(blend, list) and len(blend) > 0
    assert {r["strategy"] for r in blend} <= STRATEGIES
    only = client.get("/api/recommendations", params={"strategy": "rwe-d"}).json()
    assert {r["strategy"] for r in only} == {"rwe-d"}


def test_coach_get_and_post(client):
    greeting = client.get("/api/coach").json()
    assert isinstance(greeting, list) and greeting[0]["role"] == "assistant"
    reply = client.post("/api/coach", json={"message": "how one-sided am I?"}).json()
    assert reply["role"] == "assistant" and reply["content"]
    for c in reply["citations"]:
        assert c["metric"] in METRIC_KEYS and 0 <= c["value"] <= 100
    # keyless → deterministic grounded fallback that states the reader's real overall score
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")):
        overall = client.get("/api/report").json()["overall"]
        assert str(overall) in reply["content"]


def test_openapi_document_served(client):
    doc = client.get("/openapi.json")
    assert doc.status_code == 200
    paths = doc.json()["paths"]
    for p in ("/api/report", "/api/recommendations", "/api/coach", "/api/health"):
        assert p in paths


def test_errors_use_typed_envelope(client):
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "not_found" and err["message"]

    r2 = client.request("PUT", "/api/report")   # GET-only route
    assert r2.status_code == 405
    assert r2.json()["error"]["code"] == "method_not_allowed"


def _strip_volatile(obj):
    """Drop now()-derived fields so two serialisations are comparable."""
    volatile = {"updatedAt", "createdAt", "publishedAt"}
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items() if k not in volatile}
    if isinstance(obj, list):
        return [_strip_volatile(x) for x in obj]
    return obj


def test_report_response_model_preserves_every_field(client):
    """response_model must not drop or add any field vs the raw serialiser (else the
    contract silently changes). Timestamps are the only expected difference."""
    be = api_fastapi.state.backend
    http = client.get("/api/report").json()
    direct = json.loads(json.dumps(be.report(be.demo_user)))
    assert _strip_volatile(http) == _strip_volatile(direct)


def test_recommendations_response_model_preserves_every_field(client):
    be = api_fastapi.state.backend
    http = client.get("/api/recommendations").json()
    direct = json.loads(json.dumps(be.recommendations(be.demo_user)))
    assert _strip_volatile(http) == _strip_volatile(direct)


def test_coach_response_model_preserves_every_field(client):
    be = api_fastapi.state.backend
    msg = "explain my echo chamber"
    http = client.post("/api/coach", json={"message": msg}).json()
    direct = json.loads(json.dumps(be.coach_reply(be.demo_user, msg)))
    assert _strip_volatile(http) == _strip_volatile(direct)


def test_request_id_correlation(client):
    ok = client.get("/api/health")
    assert ok.headers.get("x-request-id")                      # every response is tagged
    err = client.get("/api/nope")
    assert err.json()["error"]["requestId"]                    # errors carry it too, for support
    # a caller-supplied id is echoed back (trace propagation)
    mine = client.get("/api/health", headers={"X-Request-ID": "trace-abc"})
    assert mine.headers.get("x-request-id") == "trace-abc"


# --------------------------------------------------------------------------- #
# Beta identity plumbing (Milestone A/2): user upsert + real-user resolution.
# --------------------------------------------------------------------------- #
def test_internal_user_upsert_is_idempotent(client):
    body = {"provider": "google", "providerAccountId": "acct-123", "displayName": "Ada"}
    first = client.post("/api/internal/users", json=body)
    assert first.status_code == 200
    uid = first.json()["userId"]
    # same identity, no profile fields -> the same engine user, not a second one
    again = client.post("/api/internal/users",
                        json={"provider": "google", "providerAccountId": "acct-123"})
    assert again.json()["userId"] == uid
    got = client.get(f"/api/internal/users/{uid}")
    assert got.status_code == 200 and got.json()["displayName"] == "Ada"


def test_internal_user_missing_is_typed_404(client):
    r = client.get("/api/internal/users/999999")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_real_user_header_resolves_and_falls_back(client):
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "hdr-1"}).json()["userId"]
    # a valid signed-in user resolves to a report (the reference reader until Milestone B)
    ok = client.get("/api/report", headers={"X-IH-User-Id": str(uid)})
    assert ok.status_code == 200 and "overall" in ok.json()
    # an unknown id simply falls back to the demo reader — no error
    fb = client.get("/api/report", headers={"X-IH-User-Id": "999999"})
    assert fb.status_code == 200 and "overall" in fb.json()


def test_report_is_labeled_measured(client):
    body = client.get("/api/report").json()
    assert body["mode"] == "measured"
    assert body["coverage"]["threshold"] == 5 and body["coverage"]["sufficient"] is True


def test_outlets_endpoint(client):
    outs = client.get("/api/outlets").json()
    assert isinstance(outs, list) and len(outs) > 0
    assert {"id", "name", "lean", "leanBucket", "articles"} <= set(outs[0])


def test_estimate_endpoint_is_labeled(client):
    names = [o["id"] for o in client.get("/api/outlets").json()[:6]]
    est = client.post("/api/estimate", json={"outlets": names}).json()
    assert est["mode"] == "estimate"
    assert est["coverage"]["sufficient"] is False
    assert "axisConfidence" not in est                      # omitted for an estimate
    assert {m["key"] for m in est["metrics"]} <= (METRIC_KEYS - {"confidence", "openMindedness"})
    assert 0 <= est["overall"] <= 100


def test_estimate_requires_outlets(client):
    r = client.post("/api/estimate", json={"outlets": ["nope-not-real"]})
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"


def test_me_requires_authentication(client):
    r = client.get("/api/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"


def test_reads_requires_authentication(client):
    r = client.post("/api/me/reads", json={"reads": [{"url": "https://x.com/a"}]})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"


def test_reads_ingestion_is_idempotent_and_reports_coverage(client):
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "reads-1"}).json()["userId"]
    hdr = {"X-IH-User-Id": str(uid)}
    reads = [
        {"url": "https://www.nytimes.com/2024/us/politics/a"},
        {"url": "nytimes.com/2024/us/politics/a"},          # same canonical -> duplicate
        {"url": "https://foxnews.com/politics/b"},
        {"url": "not a url"},                                # rejected (no host)
    ]
    r1 = client.post("/api/me/reads", json={"reads": reads}, headers=hdr).json()
    assert r1["accepted"] == 2 and r1["duplicates"] == 1 and r1["rejected"] == 1
    assert r1["totalReads"] == 2 and r1["threshold"] == 5 and r1["sufficient"] is False
    # re-submitting the same articles adds nothing (idempotent per user + canonical URL)
    r2 = client.post("/api/me/reads", json={"reads": reads[:3]}, headers=hdr).json()
    assert r2["accepted"] == 0 and r2["duplicates"] == 3 and r2["totalReads"] == 2


def test_save_onboarding_persists_and_me_returns_it(client):
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "me-1"}).json()["userId"]
    names = [o["id"] for o in client.get("/api/outlets").json()[:5]]
    saved = client.post("/api/me/onboarding", json={"outlets": names},
                        headers={"X-IH-User-Id": str(uid)})
    assert saved.status_code == 200 and saved.json()["mode"] == "estimate"
    me = client.get("/api/me", headers={"X-IH-User-Id": str(uid)}).json()
    assert me["onboarding"]["outlets"] == names
    assert me["report"]["mode"] == "estimate" and 0 <= me["report"]["overall"] <= 100


def test_internal_secret_gates_the_trust_boundary(client, monkeypatch):
    """With RWE_INTERNAL_SECRET set, internal calls need the X-IH-Auth header and the
    user-id header is honoured only when signed. Unset (the default) leaves dev untouched."""
    monkeypatch.setenv("RWE_INTERNAL_SECRET", "s3cret")
    # no secret -> typed 401
    denied = client.post("/api/internal/users",
                         json={"provider": "google", "providerAccountId": "sec-1"})
    assert denied.status_code == 401 and denied.json()["error"]["code"] == "unauthorized"
    # correct secret -> 200
    ok = client.post("/api/internal/users",
                     json={"provider": "google", "providerAccountId": "sec-1"},
                     headers={"X-IH-Auth": "s3cret"})
    assert ok.status_code == 200
    uid = ok.json()["userId"]
    # an unsigned user-id header is ignored -> falls back to the demo reader (still 200)
    unsigned = client.get("/api/report", headers={"X-IH-User-Id": str(uid)})
    assert unsigned.status_code == 200 and "overall" in unsigned.json()
    # a signed user-id header is honoured
    signed = client.get("/api/report",
                        headers={"X-IH-User-Id": str(uid), "X-IH-Auth": "s3cret"})
    assert signed.status_code == 200 and "overall" in signed.json()
