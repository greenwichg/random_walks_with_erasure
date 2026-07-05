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


# --------------------------------------------------------------------------- #
# Estimate -> Measured routing (the personalization layer): a signed-in reader gets an
# Initial Estimate below the read threshold and a real Measured report once they cross it.
# --------------------------------------------------------------------------- #
def _signed(uid):
    return {"X-IH-User-Id": str(uid)}


def test_report_is_measured_demo_for_user_without_onboarding(client):
    """A signed-in reader with no onboarding and no reads falls back to the demo reader
    (existing behaviour) — a measured report over the reference reader, not an estimate."""
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "route-demo"}).json()["userId"]
    body = client.get("/api/report", headers=_signed(uid)).json()
    assert body["mode"] == "measured"                       # demo reader, not an estimate
    # it's the reference reader's report, not this user's (they have no reads)
    assert body["coverage"]["reads"] > 5


def test_report_is_estimate_below_threshold_with_onboarding(client):
    """With onboarding saved but too few reads, the report is the Initial Estimate recomputed
    server-side from the stored outlets."""
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "route-est"}).json()["userId"]
    names = [o["id"] for o in client.get("/api/outlets").json()[:5]]
    client.post("/api/me/onboarding", json={"outlets": names}, headers=_signed(uid))
    body = client.get("/api/report", headers=_signed(uid)).json()
    assert body["mode"] == "estimate"
    assert body["coverage"]["reads"] == 0 and body["coverage"]["sufficient"] is False
    assert "axisConfidence" not in body                     # estimate omits article-level confidence


def test_report_switches_to_measured_after_threshold(client):
    """Once a signed-in reader stores enough reads, /api/report serves their real Measured
    report from the augmented corpus — coverage reflects *their* reads, not the demo reader."""
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "route-meas"}).json()["userId"]
    reads = [{"url": f"https://example-news-{i}.com/politics/story-{i}"} for i in range(6)]
    ing = client.post("/api/me/reads", json={"reads": reads}, headers=_signed(uid)).json()
    assert ing["totalReads"] == 6 and ing["sufficient"] is True

    body = client.get("/api/report", headers=_signed(uid)).json()
    assert body["mode"] == "measured"
    assert body["coverage"]["reads"] == 6                   # this user's own reads (not the demo)
    assert body["coverage"]["sufficient"] is True
    assert 0 <= body["overall"] <= 100

    # recommendations + coach are now served from the same augmented corpus
    recs = client.get("/api/recommendations", headers=_signed(uid))
    assert recs.status_code == 200 and isinstance(recs.json(), list)
    greeting = client.get("/api/coach", headers=_signed(uid)).json()
    assert greeting[0]["role"] == "assistant"
    reply = client.post("/api/coach", json={"message": "how balanced am I?"}, headers=_signed(uid)).json()
    assert reply["role"] == "assistant" and reply["content"]

    # /api/me now reflects the persisted measured snapshot (not the earlier estimate)
    me = client.get("/api/me", headers=_signed(uid)).json()
    assert me["report"]["mode"] == "measured" and me["report"]["coverage"]["reads"] == 6


def test_history_returns_the_users_reads(client):
    """The reading-history API serves the signed-in reader's own stored reads (newest first), as
    real Article payloads — empty for a new reader, and requiring authentication."""
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "route-hist"}).json()["userId"]
    hdr = {"X-IH-User-Id": str(uid)}

    assert client.get("/api/me/history", headers=hdr).json() == []      # new reader: a real empty, not mock
    reads = [
        {"url": "https://www.foxnews.com/politics/a", "title": "Officials slam the deadly crisis"},
        {"url": "https://www.nytimes.com/us/politics/b", "title": "Senate advances the bill, leaders say"},
    ]
    client.post("/api/me/reads", json={"reads": reads}, headers=hdr)

    hist = client.get("/api/me/history", headers=hdr).json()
    assert len(hist) == 2
    assert hist[0]["article"]["headline"] == "Senate advances the bill, leaders say"   # newest first
    for h in hist:
        assert set(h) >= {"id", "article", "readAt", "readingMinutes", "completed"}
        assert set(h["article"]) >= {"id", "headline", "publisher", "publisherLean", "topic",
                                     "lean", "leanBucket", "emotion", "dominantEmotion", "register"}
        assert h["completed"] is True
    # the scorer's registry lean flows through onto each article: Fox right (+), NYT left (−).
    # (publisherLean is the corpus house-lean, which is 0 here because the synthetic catalog has no
    # real outlets; on the production Qbias corpus it resolves — this asserts the read's own lean.)
    leans = [h["article"]["lean"] for h in hist]
    assert any(v > 0 for v in leans) and any(v < 0 for v in leans)

    assert client.get("/api/me/history").status_code == 401           # auth required (no demo fallback)


def test_dashboard_reuses_report_and_reflects_reads(client):
    """The dashboard reuses the very same report /api/report serves (overall + metrics), and its
    'today' block reflects the reader's real stored reads."""
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "route-dash"}).json()["userId"]
    hdr = {"X-IH-User-Id": str(uid)}
    reads = [{"url": f"https://www.foxnews.com/politics/story-{i}", "title": f"Story {i}"} for i in range(6)]
    client.post("/api/me/reads", json={"reads": reads}, headers=hdr)

    dash = client.get("/api/dashboard", headers=hdr).json()
    report = client.get("/api/report", headers=hdr).json()
    assert dash["overall"] == report["overall"]                                  # report reused verbatim
    assert {m["key"] for m in dash["metrics"]} == {m["key"] for m in report["metrics"]}
    assert set(dash["today"]) == {"articlesRead", "avgReadingMinutes", "politicalShare", "topTopics"}
    assert dash["today"]["articlesRead"] >= 1                                    # observedAt defaults to now
    assert isinstance(dash["streakDays"], int)


def test_analytics_from_the_users_stored_data(client):
    """Analytics is built entirely from the reader's stored snapshots + reads: honest empty series
    for a new reader, populated once they read (and a report snapshot is saved). Auth required."""
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "route-ana"}).json()["userId"]
    hdr = {"X-IH-User-Id": str(uid)}
    keys = {"readingOverTime", "topicDiversity", "politicalDiversity", "publisherDiversity",
            "emotion", "reporting", "recommendationAcceptance", "healthImprovement"}

    empty = client.get("/api/me/analytics", headers=hdr).json()
    assert set(empty) == keys and all(v == [] for v in empty.values())   # honest empty, all series present

    reads = [{"url": f"https://www.foxnews.com/politics/s{i}", "title": f"Story {i}"} for i in range(6)]
    client.post("/api/me/reads", json={"reads": reads}, headers=hdr)
    client.get("/api/report", headers=hdr)                              # measured build -> saves a snapshot

    ana = client.get("/api/me/analytics", headers=hdr).json()
    assert sum(p["overall"] for p in ana["readingOverTime"]) == 6       # every read counted by day
    assert len(ana["healthImprovement"]) >= 1                           # >=1 saved snapshot

    assert client.get("/api/me/analytics").status_code == 401           # auth required


def test_dashboard_anonymous_is_demo_with_empty_activity(client):
    """An anonymous request gets the demo report's score/metrics but no fabricated personal
    activity — empty trend, zero 'today', zero streak."""
    dash = client.get("/api/dashboard").json()
    assert isinstance(dash["overall"], int) and len(dash["metrics"]) > 0
    assert dash["trend"] == [] and dash["streakDays"] == 0
    assert dash["today"]["articlesRead"] == 0 and dash["today"]["topTopics"] == []


def test_open_mindedness_completes_the_metric_set(client):
    """The Open-Mindedness feedback loop over HTTP: a measured reader is 7/8 until they open
    cross-cutting recommendations through /api/me/recommendations/opened, then 8/8 — the last
    Information Health metric, populated automatically from recommendation reception."""
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "route-openmind"}).json()["userId"]
    # titled, known-outlet, two-sided political reads -> a Measured report with the 7 read-derived
    # metrics (topic/source/reporting/emotional/echo/viewpoint + confidence), but no Open-Mindedness.
    reads = [
        {"url": "https://www.nytimes.com/2026/us/politics/a", "title": "Senate advances the bill, leaders say"},
        {"url": "https://www.foxnews.com/politics/b", "title": "Outrage as officials slam the deadly crisis"},
        {"url": "https://www.wsj.com/politics/c", "title": "Opinion: we must act now on the economy"},
        {"url": "https://www.washingtonpost.com/politics/d", "title": "Analysis: what to know about the vote"},
        {"url": "https://www.theguardian.com/us-news/politics/e", "title": "Hope as historic deal is celebrated"},
        {"url": "https://apnews.com/hub/politics/f", "title": "Poll finds shifting views, new data shows"},
    ]
    assert client.post("/api/me/reads", json={"reads": reads}, headers=_signed(uid)).json()["sufficient"]

    before = client.get("/api/report", headers=_signed(uid)).json()
    before_keys = {m["key"] for m in before["metrics"]}
    assert before["mode"] == "measured"
    assert "openMindedness" not in before_keys                         # 7/8: no reception yet

    # surfacing recs is a measurable event (records the shown denominator); must not error
    assert client.get("/api/recommendations", headers=_signed(uid)).status_code == 200

    # open three distinct cross-cutting recommendations -> reception activates the 8th metric
    last = None
    for i, aid in enumerate(["cc-a", "cc-b", "cc-c"], start=1):
        last = client.post("/api/me/recommendations/opened",
                           json={"articleId": aid, "crossCutting": True}, headers=_signed(uid)).json()
        assert last["openedCross"] == i and last["shownCross"] == i
    assert last["active"] is True and last["rate"] == 1.0

    after = client.get("/api/report", headers=_signed(uid)).json()
    after_keys = {m["key"] for m in after["metrics"]}
    assert "openMindedness" in after_keys                              # 8/8: the metric appears
    assert after_keys == before_keys | {"openMindedness"}             # additive: only OM was added
    # recommendations + coach stay consistent (served, valid) with the metric now present
    assert client.get("/api/recommendations", headers=_signed(uid)).status_code == 200
    assert client.get("/api/coach", headers=_signed(uid)).json()[0]["role"] == "assistant"

    # the open endpoint requires a signed-in user (same trust boundary as the other /api/me routes)
    assert client.post("/api/me/recommendations/opened", json={"articleId": "x"}).status_code == 401


def test_anonymous_report_is_unchanged_by_routing(client):
    """The anonymous / ?user= path is untouched: same demo reader, same measured contract."""
    anon = client.get("/api/report").json()
    assert anon["mode"] == "measured" and anon["coverage"]["sufficient"] is True
    assert client.get("/api/report", params={"user": "0"}).status_code == 200


# --------------------------------------------------------------------------- #
# Per-user API tokens (Milestone C3): mint (auth'd) -> resolve (internal) -> revoke.
# The extension will send the token to the web tier, which resolves it here and forwards
# the read on the existing /api/me/reads path — no new ingestion pathway on the engine.
# --------------------------------------------------------------------------- #
def test_api_tokens_require_authentication(client):
    assert client.post("/api/me/tokens", json={}).status_code == 401
    assert client.get("/api/me/tokens").status_code == 401
    assert client.delete("/api/me/tokens/1").status_code == 401


def test_api_token_mint_list_resolve_revoke(client):
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "tok-api-1"}).json()["userId"]
    hdr = {"X-IH-User-Id": str(uid)}

    minted = client.post("/api/me/tokens", json={"label": "my extension"}, headers=hdr).json()
    assert minted["token"].startswith("ih_") and minted["label"] == "my extension"
    token = minted["token"]

    # listing returns metadata only — never the plaintext
    listed = client.get("/api/me/tokens", headers=hdr).json()
    assert len(listed) == 1 and listed[0]["id"] == minted["id"]
    assert "token" not in listed[0]

    # the internal resolver exchanges the token for its engine user id (server-to-server)
    resolved = client.post("/api/internal/resolve-token", json={"token": token})
    assert resolved.status_code == 200 and resolved.json()["userId"] == uid
    bad = client.post("/api/internal/resolve-token", json={"token": "ih_nope"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "unauthorized"

    # revoking is scoped to the owner and stops the token resolving
    assert client.delete(f"/api/me/tokens/{minted['id']}", headers=hdr).status_code == 200
    assert client.post("/api/internal/resolve-token", json={"token": token}).status_code == 401
    assert client.get("/api/me/tokens", headers=hdr).json() == []


def test_token_ingestion_attributes_reads_to_the_right_user(client):
    """The end-to-end shape the web proxy will use: resolve a token to a uid, then record a
    read for that uid on the *existing* endpoint — the read lands on the user's own history."""
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "tok-e2e"}).json()["userId"]
    hdr = {"X-IH-User-Id": str(uid)}
    token = client.post("/api/me/tokens", json={}, headers=hdr).json()["token"]

    resolved_uid = client.post("/api/internal/resolve-token", json={"token": token}).json()["userId"]
    # the web tier would now forward with X-IH-User-Id = resolved uid (+ secret in prod)
    res = client.post("/api/me/reads",
                      json={"reads": [{"url": "https://www.nytimes.com/2024/us/politics/tok"}]},
                      headers={"X-IH-User-Id": str(resolved_uid)}).json()
    assert res["accepted"] == 1 and res["totalReads"] >= 1


def test_resolve_token_respects_internal_secret(client, monkeypatch):
    """With RWE_INTERNAL_SECRET set, the resolver (like the other internal endpoints) requires
    the X-IH-Auth header — an unsigned exchange is refused."""
    monkeypatch.setenv("RWE_INTERNAL_SECRET", "s3cret")
    auth = {"X-IH-Auth": "s3cret"}
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "tok-sec"},
                      headers=auth).json()["userId"]
    token = client.post("/api/me/tokens", json={},
                        headers={"X-IH-User-Id": str(uid), **auth}).json()["token"]
    # unsigned resolve -> 401; signed -> 200
    assert client.post("/api/internal/resolve-token", json={"token": token}).status_code == 401
    ok = client.post("/api/internal/resolve-token", json={"token": token}, headers=auth)
    assert ok.status_code == 200 and ok.json()["userId"] == uid
