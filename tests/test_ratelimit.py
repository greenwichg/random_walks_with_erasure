"""Tests for the engine's rate limiter (examples/ratelimit.py) and its HTTP enforcement.

Two layers: pure unit tests of the token bucket / scope classification / config, and integration
tests that drive the FastAPI app and assert real 429s with Retry-After on the protected scopes
(auth brute force, AI/coach, ingestion), while health checks stay exempt. Skips cleanly when the
optional serving deps aren't installed.
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

# Small, fast synthetic corpus for the app's startup build (mirrors test_api_fastapi.py).
os.environ.setdefault("RWE_N_USERS", "120")
os.environ.setdefault("RWE_MAX_ITEMS", "300")
os.environ.setdefault("RWE_SEED", "0")
os.environ.setdefault("RWE_DB_URL", "sqlite://")

sys.path.insert(0, str(ROOT / "examples"))
import ratelimit  # noqa: E402  (examples/ on sys.path)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Loaded under a distinct name so this file's app/limiter are fully isolated from test_api_fastapi.
api_fastapi = _load("api_fastapi_rl", ROOT / "examples" / "api_fastapi.py")


@pytest.fixture(scope="module")
def client():
    with TestClient(api_fastapi.app) as c:
        yield c


# --------------------------------------------------------------------------- #
# Unit: the token bucket.
# --------------------------------------------------------------------------- #
def test_token_bucket_allows_capacity_then_denies():
    rl = ratelimit.RateLimiter(clock=lambda: 1000.0)          # frozen clock: no refill
    for _ in range(5):
        ok, retry = rl.check("k", 5)                          # 5/min capacity
        assert ok and retry == 0
    ok, retry = rl.check("k", 5)
    assert not ok and retry >= 1                              # 6th denied, positive Retry-After


def test_token_bucket_refills_over_time():
    clk = {"t": 0.0}
    rl = ratelimit.RateLimiter(clock=lambda: clk["t"])
    assert rl.check("k", 2)[0]                                # 2/min -> capacity 2 (2 -> 1)
    assert rl.check("k", 2)[0]                                # 1 -> 0
    assert not rl.check("k", 2)[0]                            # denied
    clk["t"] += 30.0                                          # 30s == one token at 2/min
    assert rl.check("k", 2)[0]                                # refilled -> allowed
    assert not rl.check("k", 2)[0]                            # and empty again


def test_buckets_are_isolated_per_key():
    rl = ratelimit.RateLimiter(clock=lambda: 0.0)
    assert rl.check("a", 1)[0]
    assert not rl.check("a", 1)[0]                            # key "a" exhausted
    assert rl.check("b", 1)[0]                                # key "b" independent


def test_retry_after_scales_with_rate():
    # A slow scope (2/min) hands back a long Retry-After; a fast one (60/min) a short one.
    rl = ratelimit.RateLimiter(clock=lambda: 0.0)
    assert rl.check("slow", 2)[0] and rl.check("slow", 2)[0]  # capacity 2 -> two allowed
    ok, retry_slow = rl.check("slow", 2)                      # third denied; ~30s to refill one
    assert not ok and 1 <= retry_slow <= 30
    rl2 = ratelimit.RateLimiter(clock=lambda: 0.0)
    for _ in range(60):
        assert rl2.check("fast", 60)[0]                       # capacity 60 -> sixty allowed
    ok, retry_fast = rl2.check("fast", 60)                    # 61st denied; 60/min refills in ~1s
    assert not ok and retry_fast <= 2


# --------------------------------------------------------------------------- #
# Unit: scope classification + configuration.
# --------------------------------------------------------------------------- #
def test_scope_classification():
    S = ratelimit.scope_for
    assert S("POST", "/api/internal/resolve-token") == "auth"
    assert S("POST", "/api/internal/users") == "auth"
    assert S("POST", "/api/coach") == "ai"
    assert S("GET", "/api/coach") == "read"                   # the greeting is a read, not the LLM
    assert S("POST", "/api/me/reads") == "ingest"
    assert S("POST", "/api/me/settings") == "write"
    assert S("DELETE", "/api/me/tokens/1") == "write"
    assert S("GET", "/api/report") == "read"
    assert S("GET", "/api/health") is None                   # health probe exempt
    assert S("OPTIONS", "/api/report") is None               # CORS pre-flight exempt


def test_rate_for_env_override_and_dev_factor(monkeypatch):
    monkeypatch.delenv("RWE_RATELIMIT_AI_PER_MIN", raising=False)
    assert ratelimit.rate_for("ai", production=True) == ratelimit.RATE_DEFAULTS["ai"]
    assert (ratelimit.rate_for("ai", production=False)
            == ratelimit.RATE_DEFAULTS["ai"] * ratelimit.RATE_DEV_FACTOR)  # relaxed in dev
    monkeypatch.setenv("RWE_RATELIMIT_AI_PER_MIN", "7")
    assert ratelimit.rate_for("ai", production=True) == 7                  # override wins verbatim
    assert ratelimit.rate_for("ai", production=False) == 7                 # ...even outside prod


def test_enabled_toggle(monkeypatch):
    monkeypatch.delenv("RWE_RATELIMIT_ENABLED", raising=False)
    assert ratelimit.enabled() is True                       # default on
    monkeypatch.setenv("RWE_RATELIMIT_ENABLED", "0")
    assert ratelimit.enabled() is False


# --------------------------------------------------------------------------- #
# Integration: real 429s over HTTP. Each test resets the limiter and uses a fresh identity
# (unique X-Forwarded-For, or a fresh user id) so its bucket is isolated and deterministic.
# --------------------------------------------------------------------------- #
def test_coach_ai_rate_limit_returns_429_with_retry_after(client, monkeypatch):
    api_fastapi.state.limiter.reset()
    monkeypatch.setenv("RWE_RATELIMIT_AI_PER_MIN", "3")
    ip = {"X-Forwarded-For": "203.0.113.10"}                  # fresh anonymous identity
    for _ in range(3):
        assert client.post("/api/coach", json={"message": "hi"}, headers=ip).status_code == 200
    blocked = client.post("/api/coach", json={"message": "hi"}, headers=ip)
    assert blocked.status_code == 429
    body = blocked.json()["error"]
    assert body["code"] == "rate_limited" and body["requestId"]
    assert int(blocked.headers["Retry-After"]) >= 1


def test_reads_ingestion_rate_limited_per_user(client, monkeypatch):
    api_fastapi.state.limiter.reset()
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "rl-ingest"}).json()["userId"]
    hdr = {"X-IH-User-Id": str(uid)}
    monkeypatch.setenv("RWE_RATELIMIT_INGEST_PER_MIN", "2")
    assert client.post("/api/me/reads", json={"reads": [{"url": "https://x.com/1"}]},
                       headers=hdr).status_code == 200
    assert client.post("/api/me/reads", json={"reads": [{"url": "https://x.com/2"}]},
                       headers=hdr).status_code == 200
    blocked = client.post("/api/me/reads", json={"reads": [{"url": "https://x.com/3"}]}, headers=hdr)
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) >= 1


def test_ingestion_limit_is_per_user_not_global(client, monkeypatch):
    """One user hitting their ingest limit must not throttle a different user."""
    api_fastapi.state.limiter.reset()
    a = client.post("/api/internal/users",
                    json={"provider": "google", "providerAccountId": "rl-a"}).json()["userId"]
    b = client.post("/api/internal/users",
                    json={"provider": "google", "providerAccountId": "rl-b"}).json()["userId"]
    monkeypatch.setenv("RWE_RATELIMIT_INGEST_PER_MIN", "1")
    assert client.post("/api/me/reads", json={"reads": [{"url": "https://x.com/a1"}]},
                       headers={"X-IH-User-Id": str(a)}).status_code == 200
    assert client.post("/api/me/reads", json={"reads": [{"url": "https://x.com/a2"}]},
                       headers={"X-IH-User-Id": str(a)}).status_code == 429   # user A throttled
    assert client.post("/api/me/reads", json={"reads": [{"url": "https://x.com/b1"}]},
                       headers={"X-IH-User-Id": str(b)}).status_code == 200   # user B unaffected


def test_auth_bruteforce_on_resolve_token_is_throttled(client, monkeypatch):
    api_fastapi.state.limiter.reset()
    monkeypatch.setenv("RWE_RATELIMIT_AUTH_PER_MIN", "2")
    ip = {"X-Forwarded-For": "203.0.113.20"}
    # invalid tokens are refused (401); once the per-IP auth budget is spent, further guesses 429
    assert client.post("/api/internal/resolve-token", json={"token": "ih_guess1"},
                       headers=ip).status_code == 401
    assert client.post("/api/internal/resolve-token", json={"token": "ih_guess2"},
                       headers=ip).status_code == 401
    blocked = client.post("/api/internal/resolve-token", json={"token": "ih_guess3"}, headers=ip)
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "rate_limited"


def test_health_check_is_exempt_from_limits(client, monkeypatch):
    api_fastapi.state.limiter.reset()
    monkeypatch.setenv("RWE_RATELIMIT_READ_PER_MIN", "1")
    for _ in range(5):
        assert client.get("/api/health").status_code == 200          # exempt -> never throttled
    ip = {"X-Forwarded-For": "203.0.113.30"}
    assert client.get("/api/outlets", headers=ip).status_code == 200  # non-exempt read: 1 allowed
    assert client.get("/api/outlets", headers=ip).status_code == 429  # ...then throttled


def test_disabled_limiter_never_throttles(client, monkeypatch):
    api_fastapi.state.limiter.reset()
    monkeypatch.setenv("RWE_RATELIMIT_ENABLED", "0")
    monkeypatch.setenv("RWE_RATELIMIT_AI_PER_MIN", "1")               # tiny, but disabled
    ip = {"X-Forwarded-For": "203.0.113.40"}
    for _ in range(4):
        assert client.post("/api/coach", json={"message": "hi"}, headers=ip).status_code == 200
