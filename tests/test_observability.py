"""OBS1 — observability foundation: metrics, error reporting, health split, tracing, endpoints."""
import importlib.util
import json
import logging
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "examples" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


obs_metrics = _load("obs_metrics")
error_reporting = _load("error_reporting")
api_fastapi = _load("api_fastapi")


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #
def test_metrics_record_and_snapshot():
    m = obs_metrics.Metrics()
    for ms in (5, 10, 200, 1000):
        m.observe("t", ms)
    m.record_request("GET", "/api/report", 200, 12.0)
    m.record_request("GET", "/api/report", 503, 8.0)
    snap = m.snapshot()
    assert snap["counters"]["requests_total|GET /api/report|2xx"] == 1
    assert snap["counters"]["requests_total|GET /api/report|5xx"] == 1
    t = snap["timers"]["t"]
    assert t["count"] == 4 and t["minMs"] == 5.0 and t["maxMs"] == 1000.0
    assert t["p50Ms"] is not None and t["p95Ms"] is not None
    assert "uptimeSeconds" in snap


def test_metrics_is_bounded_and_never_raises():
    m = obs_metrics.Metrics()
    # exceed the series cap — new series are dropped, not grown unbounded
    for i in range(obs_metrics._MAX_SERIES + 50):
        m.observe(f"series_{i}", 1.0)
    assert len(m.snapshot()["timers"]) <= obs_metrics._MAX_SERIES
    m.observe("x", float("nan"))          # pathological input must not raise


def test_timer_context_manager_records():
    m = obs_metrics._METRICS
    m.reset()
    with obs_metrics.timer("block_ms"):
        pass
    assert "block_ms" in m.snapshot()["timers"]


# --------------------------------------------------------------------------- #
# error reporting
# --------------------------------------------------------------------------- #
def test_error_reporter_is_swappable_and_receives_context():
    captured = {}

    class _Fake:
        def report(self, exc, *, context):
            captured["exc"] = exc
            captured["context"] = context

    prev = error_reporting.get_reporter()
    try:
        error_reporting.set_reporter(_Fake())
        error_reporting.report_exception(ValueError("boom"), path="/x", requestId="rid1")
        assert isinstance(captured["exc"], ValueError)
        assert captured["context"]["path"] == "/x" and captured["context"]["requestId"] == "rid1"
    finally:
        error_reporting.set_reporter(prev)


def test_reporting_never_raises_even_if_reporter_fails():
    class _Broken:
        def report(self, exc, *, context):
            raise RuntimeError("reporter down")

    prev = error_reporting.get_reporter()
    try:
        error_reporting.set_reporter(_Broken())
        error_reporting.report_exception(ValueError("x"))     # must swallow the reporter's failure
    finally:
        error_reporting.set_reporter(prev)


def test_logging_reporter_emits_structured_json(caplog):
    with caplog.at_level(logging.ERROR, logger="ih.errors"):
        error_reporting.LoggingReporter().report(
            ValueError("kaboom"), context={"path": "/api/report", "requestId": "rid2"})
    line = caplog.records[-1].getMessage()
    payload = json.loads(line)
    assert payload["event"] == "exception" and payload["error"] == "ValueError"
    assert payload["message"] == "kaboom" and payload["path"] == "/api/report"
    assert payload["requestId"] == "rid2" and "traceback" in payload


# --------------------------------------------------------------------------- #
# API — health split, metrics, tracing, client errors
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def client():
    with TestClient(api_fastapi.app) as c:
        yield c


def test_liveness_and_readiness(client):
    live = client.get("/api/health/live")
    assert live.status_code == 200 and live.json() == {"status": "alive"}
    ready = client.get("/api/health/ready")
    assert ready.status_code == 200
    body = ready.json()
    assert body["status"] == "ready" and body["store"] is True and body["backend"] is True
    # the original /api/health is unchanged (backward compatible)
    assert client.get("/api/health").json()["ok"] is True


def test_metrics_endpoint_records_request_and_report_timings(client):
    client.get("/api/report")                     # generate traffic
    snap = client.get("/api/metrics").json()       # dev: _trusted is open, so 200
    assert "report_generate_ms" in snap["timers"]  # report generation was timed
    assert "db_query_ms" in snap["timers"]         # DB queries were timed
    assert any(k.startswith("request_ms|") for k in snap["timers"])          # per-route latency
    assert any(k.startswith("requests_total|") for k in snap["counters"])    # per-route counts


def test_metrics_endpoint_is_internal_only_in_production(client, monkeypatch):
    # with a secret configured, _trusted requires the X-IH-Auth header → an un-headered call is 404
    monkeypatch.setattr(api_fastapi, "_internal_secret", lambda: "s3cret")
    assert client.get("/api/metrics").status_code == 404
    assert client.get("/api/metrics", headers={"X-IH-Auth": "s3cret"}).status_code == 200


def test_request_carries_correlation_id(client):
    r = client.get("/api/health/live", headers={"X-Request-ID": "trace-xyz"})
    assert r.headers.get("X-Request-ID") == "trace-xyz"     # echoed for client↔log correlation


def test_client_error_sink_accepts_and_logs(client, caplog):
    with caplog.at_level(logging.WARNING, logger="ih.api"):
        r = client.post("/api/client-errors",
                        json={"message": "TypeError: undefined is not a function",
                              "name": "TypeError", "url": "/report"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert any("client_error" in rec.getMessage() for rec in caplog.records)
