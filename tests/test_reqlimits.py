"""Tests for request-body size limits (examples/reqlimits.py) and their HTTP enforcement.

Unit tests cover the per-scope byte caps, env overrides, dev factor, and the ingestion batch-shape
checks; integration tests drive the FastAPI app and assert real 413s (oversized Content-Length,
over-count / over-long reads batch, oversized coach body) while valid requests are unaffected.
"""

import importlib.util
import os
import pathlib
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parent.parent

os.environ.setdefault("RWE_N_USERS", "120")
os.environ.setdefault("RWE_MAX_ITEMS", "300")
os.environ.setdefault("RWE_SEED", "0")
os.environ.setdefault("RWE_DB_URL", "sqlite://")

sys.path.insert(0, str(ROOT / "examples"))
import reqlimits  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


api_fastapi = _load("api_fastapi_bl", ROOT / "examples" / "api_fastapi.py")


@pytest.fixture(scope="module")
def client():
    with TestClient(api_fastapi.app) as c:
        yield c


# --------------------------------------------------------------------------- #
# Unit: byte caps + batch-shape.
# --------------------------------------------------------------------------- #
def test_max_body_bytes_classifies_by_endpoint():
    M = reqlimits.max_body_bytes
    assert M("POST", "/api/me/reads", True) == reqlimits.BODY_LIMITS["ingest"]
    assert M("POST", "/api/coach", True) == reqlimits.BODY_LIMITS["ai"]
    assert M("POST", "/api/internal/resolve-token", True) == reqlimits.BODY_LIMITS["auth"]
    assert M("POST", "/api/me/settings", True) == reqlimits.BODY_LIMITS["write"]
    assert M("GET", "/api/report", True) is None                 # read-only: no body cap
    assert M("GET", "/api/health", True) is None                 # exempt
    assert M("OPTIONS", "/api/coach", True) is None              # CORS pre-flight


def test_body_limit_dev_factor_and_env_override(monkeypatch):
    monkeypatch.delenv("RWE_BODY_LIMIT_AI_BYTES", raising=False)
    assert reqlimits.body_limit_for("ai", production=True) == reqlimits.BODY_LIMITS["ai"]
    assert (reqlimits.body_limit_for("ai", production=False)
            == reqlimits.BODY_LIMITS["ai"] * reqlimits.BODY_DEV_FACTOR)     # relaxed in dev
    monkeypatch.setenv("RWE_BODY_LIMIT_AI_BYTES", "99")
    assert reqlimits.body_limit_for("ai", production=True) == 99            # override wins
    assert reqlimits.body_limit_for("ai", production=False) == 99


def test_reads_batch_error_accepts_normal_and_flags_oversize(monkeypatch):
    ok = [SimpleNamespace(url="https://x.com/a", title="t", subtitle="", description="")]
    assert reqlimits.reads_batch_error(ok) is None                         # normal batch is fine
    monkeypatch.setenv("RWE_MAX_READS_PER_BATCH", "1")
    assert reqlimits.reads_batch_error(ok * 2) is not None                 # too many reads
    monkeypatch.delenv("RWE_MAX_READS_PER_BATCH")
    monkeypatch.setenv("RWE_MAX_URL_LEN", "10")
    assert reqlimits.reads_batch_error(
        [SimpleNamespace(url="https://way-too-long.example/x", title="")]) is not None
    monkeypatch.delenv("RWE_MAX_URL_LEN")
    monkeypatch.setenv("RWE_MAX_TITLE_LEN", "3")
    assert reqlimits.reads_batch_error(
        [SimpleNamespace(url="https://x.com", title="far too long")]) is not None


# --------------------------------------------------------------------------- #
# Integration: real 413s over HTTP.
# --------------------------------------------------------------------------- #
def test_oversized_content_length_returns_413(client, monkeypatch):
    monkeypatch.setenv("RWE_BODY_LIMIT_INGEST_BYTES", "20")               # tiny cap -> any body fails
    r = client.post("/api/me/reads",
                    json={"reads": [{"url": "https://example.com/a-normal-article"}]},
                    headers={"X-IH-User-Id": "1"})
    assert r.status_code == 413
    err = r.json()["error"]
    assert err["code"] == "payload_too_large" and err["requestId"]
    assert r.headers.get("x-request-id")                                  # correlates to a log line


def test_valid_batch_still_accepted(client):
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "bl-ok"}).json()["userId"]
    r = client.post("/api/me/reads",
                    json={"reads": [{"url": "https://www.nytimes.com/x", "title": "hi"}]},
                    headers={"X-IH-User-Id": str(uid)})
    assert r.status_code == 200 and r.json()["accepted"] == 1             # contract preserved


def test_too_many_reads_returns_413(client, monkeypatch):
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "bl-count"}).json()["userId"]
    monkeypatch.setenv("RWE_MAX_READS_PER_BATCH", "2")
    reads = [{"url": f"https://x.com/{i}"} for i in range(3)]
    r = client.post("/api/me/reads", json={"reads": reads}, headers={"X-IH-User-Id": str(uid)})
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"


def test_overlong_url_returns_413(client, monkeypatch):
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "bl-url"}).json()["userId"]
    monkeypatch.setenv("RWE_MAX_URL_LEN", "40")
    long_url = "https://example.com/" + "a" * 60
    r = client.post("/api/me/reads", json={"reads": [{"url": long_url}]},
                    headers={"X-IH-User-Id": str(uid)})
    assert r.status_code == 413


def test_overlong_title_returns_413(client, monkeypatch):
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "bl-title"}).json()["userId"]
    monkeypatch.setenv("RWE_MAX_TITLE_LEN", "5")
    r = client.post("/api/me/reads",
                    json={"reads": [{"url": "https://x.com/a", "title": "x" * 40}]},
                    headers={"X-IH-User-Id": str(uid)})
    assert r.status_code == 413


def test_coach_oversized_body_returns_413(client, monkeypatch):
    monkeypatch.setenv("RWE_BODY_LIMIT_AI_BYTES", "20")
    r = client.post("/api/coach", json={"message": "tell me about my media diet in great detail"})
    assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"


def test_small_requests_unaffected(client):
    # a normal coach message under the default cap is served normally — the valid-request contract.
    r = client.post("/api/coach", json={"message": "how balanced am I?"})
    assert r.status_code == 200 and r.json()["role"] == "assistant"
