"""HTTP-layer tests for the FastAPI re-host (examples/api_fastapi.py).

Verifies the FastAPI serving layer preserves the stdlib server's behaviour: same endpoints,
same query params, and responses that carry the same engine output as the Backend
serialisers. Skips cleanly when the optional serving deps aren't installed.
"""

import importlib.util
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


def test_request_id_correlation(client):
    ok = client.get("/api/health")
    assert ok.headers.get("x-request-id")                      # every response is tagged
    err = client.get("/api/nope")
    assert err.json()["error"]["requestId"]                    # errors carry it too, for support
    # a caller-supplied id is echoed back (trace propagation)
    mine = client.get("/api/health", headers={"X-Request-ID": "trace-abc"})
    assert mine.headers.get("x-request-id") == "trace-abc"
