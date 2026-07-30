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
from datetime import datetime, timedelta, timezone

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


def test_health_reports_recommendation_source(client):
    """The health diagnostic makes the recs-source state verifiable: this app runs the default
    synthetic corpus (no feed source), so it reports `static` with no resolved URLs — the exact
    signal an operator uses to see why recommendation `url`s are absent."""
    rs = client.get("/api/health").json()["recommendationSource"]
    assert rs["source"] == "static"            # feed source not active -> recommendations carry no url
    assert rs["feedArticles"] == 0 and rs["resolvedUrls"] == 0


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
    """response_model must not drop or add any field vs the raw serialiser (else the contract
    silently changes) — modulo ONE documented handler post-pass, the same way the recommendations
    test below documents its two.

    `sample` is provenance, not content: the handler sets it when the report being served is not the
    requesting reader's own. The serialiser cannot know that — it is handed a user id and reports on
    it — so the flag can only be added at the routing layer, which is precisely why it was missing
    for so long."""
    be = api_fastapi.state.backend
    http = client.get("/api/report").json()
    direct = json.loads(json.dumps(be.report(be.demo_user)))
    assert http.get("sample") is True, (
        "an anonymous request is served a report that is nobody's own reading, and the payload has "
        "to say so")
    assert _strip_volatile({k: v for k, v in http.items() if k != "sample"}) == _strip_volatile(direct)


def test_recommendations_response_model_preserves_every_field(client):
    """The response model must not drop or add fields vs the serialiser — modulo the two
    DOCUMENTED handler post-passes: media enrichment (Commit 9) and the Evidence Resolver
    (21a.3), which owns ``reason`` and adds the structured ``explanation``."""
    be = api_fastapi.state.backend
    http = client.get("/api/recommendations").json()
    direct = json.loads(json.dumps(be.recommendations(be.demo_user)))

    def _norm(recs, drop=("explanation", "reason", "image", "imageWidth", "imageHeight",
                          "imageMimeType", "imageSource", "imageAttribution",
                          "publisherLogo", "publisherLogoDark", "publisherLogoSource")):
        out = []
        for r in recs:
            r = {k: v for k, v in r.items() if k not in drop}
            r["article"] = {k: v for k, v in r["article"].items() if k not in drop}
            out.append(r)
        return out

    assert _strip_volatile(_norm(http)) == _strip_volatile(_norm(direct))
    # the resolver post-pass contract itself: reason mirrors the structured explanation
    for r in http:
        assert r["explanation"]["message"] == r["reason"]


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


def test_internal_user_upsert_refreshes_the_profile_by_default(client):
    """The behaviour every existing caller depends on, asserted on the UPDATE path.

    `test_internal_user_upsert_is_idempotent` only ever sets the name on the FIRST call, so it would
    still pass if the default flipped to False. This is the test that would not."""
    body = {"provider": "google", "providerAccountId": "refresh-1", "displayName": "Ada",
            "email": "ada@example.com"}
    uid = client.post("/api/internal/users", json=body).json()["userId"]

    # A later sign-in with a changed Google profile, and no flag at all.
    again = client.post("/api/internal/users",
                        json={"provider": "google", "providerAccountId": "refresh-1",
                              "displayName": "Ada Lovelace", "email": "ada.l@example.com"})
    assert again.json()["userId"] == uid
    got = client.get(f"/api/internal/users/{uid}").json()
    assert got["displayName"] == "Ada Lovelace" and got["email"] == "ada.l@example.com"


def test_internal_user_upsert_refresh_profile_true_is_explicit_default(client):
    """Sending the flag as True must mean exactly what omitting it means."""
    body = {"provider": "google", "providerAccountId": "refresh-2", "displayName": "First"}
    uid = client.post("/api/internal/users", json=body).json()["userId"]
    client.post("/api/internal/users",
                json={"provider": "google", "providerAccountId": "refresh-2",
                      "displayName": "Second", "refreshProfile": True})
    assert client.get(f"/api/internal/users/{uid}").json()["displayName"] == "Second"


def test_internal_user_upsert_refresh_profile_false_leaves_the_stored_profile(client):
    """S2: a stale session must not overwrite a newer profile.

    Identity recovery resolves an id from a token that can be weeks old. Without this the reader's
    display name would silently revert to whatever that token was minted with."""
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "keep-1",
                            "displayName": "Current Name",
                            "email": "current@example.com"}).json()["userId"]

    stale = client.post("/api/internal/users",
                        json={"provider": "google", "providerAccountId": "keep-1",
                              "displayName": "Old Name", "email": "old@example.com",
                              "refreshProfile": False})
    # The id still resolves — that is the whole point of the call.
    assert stale.status_code == 200 and stale.json()["userId"] == uid
    # ...and the response reports what is STORED, not what was submitted.
    assert stale.json()["displayName"] == "Current Name"
    assert stale.json()["email"] == "current@example.com"

    got = client.get(f"/api/internal/users/{uid}").json()
    assert got["displayName"] == "Current Name" and got["email"] == "current@example.com"


def test_internal_user_upsert_refresh_profile_false_still_creates_with_the_profile(client):
    """Creation is not a refresh.

    A first sighting can arrive via recovery — the reader signed in during an engine outage, so no
    engine row was ever made. If `False` also suppressed the create-path write, recovery would mint
    accounts with a null email and display name that nothing would ever fill in."""
    created = client.post("/api/internal/users",
                          json={"provider": "google", "providerAccountId": "fresh-1",
                                "displayName": "Brand New", "email": "new@example.com",
                                "refreshProfile": False})
    assert created.status_code == 200
    uid = created.json()["userId"]
    got = client.get(f"/api/internal/users/{uid}").json()
    assert got["displayName"] == "Brand New" and got["email"] == "new@example.com"


def test_internal_user_upsert_ignores_unknown_fields(client):
    """Rolling-deployment safety, as a test rather than a comment.

    The whole new-web-against-an-old-engine argument rests on Pydantic's default extra="ignore":
    a web tier that sends `refreshProfile` to an engine that predates it must be silently ignored,
    not 422'd. Adding extra="forbid" to UpsertUserRequest would break reverting the engine alone,
    and this is what would catch it."""
    r = client.post("/api/internal/users",
                    json={"provider": "google", "providerAccountId": "unknown-1",
                          "displayName": "Ada", "someFutureField": True, "refreshProfileTypo": False})
    assert r.status_code == 200
    uid = r.json()["userId"]
    # And the unknown fields changed nothing: the profile was still refreshed, i.e. the default held.
    client.post("/api/internal/users",
                json={"provider": "google", "providerAccountId": "unknown-1", "displayName": "Ada L"})
    assert client.get(f"/api/internal/users/{uid}").json()["displayName"] == "Ada L"


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
    keys = {m["key"] for m in est["metrics"]}
    assert keys == METRIC_KEYS                               # every card present (empty-state when n/a)
    avail = {m["key"] for m in est["metrics"] if m["available"]}
    assert "confidence" not in avail and "openMindedness" not in avail   # not measurable from outlets alone
    assert avail <= (METRIC_KEYS - {"confidence", "openMindedness"})
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


def test_me_carries_the_two_facts_the_onboarding_gate_reads(client):
    """The web app shell redirects to /onboarding on `onboarding is None and reads == 0` — see
    docs/ONBOARDING.md. Both halves are asserted here because the gate is only as correct as this
    payload: without `reads` it would bounce established readers who predate the onboarding row."""
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "gate-1"}).json()["userId"]
    hdr = {"X-IH-User-Id": str(uid)}

    # A brand-new account: nothing chosen, nothing read -> the state the gate acts on. `onboarding`
    # is ABSENT rather than null (response_model_exclude_none), which is why the web check is
    # falsy-based; `reads` is 0, not absent, because 0 is not None.
    fresh = client.get("/api/me", headers=hdr).json()
    assert fresh.get("onboarding") is None and fresh["reads"] == 0

    # Reading alone clears the gate. No onboarding row is written by /api/me/reads, so this is the
    # regression guard for a gate that looked only at `onboarding`.
    client.post("/api/me/reads", json={"reads": [{"url": "https://www.nytimes.com/2024/us/a"}]},
                headers=hdr)
    established = client.get("/api/me", headers=hdr).json()
    assert established.get("onboarding") is None and established["reads"] == 1


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
    # Estimate-vs-Measured context is carried on the dashboard, lifted verbatim from the report, so
    # the onboarding context never disappears (Progressive Information Health Journey).
    assert dash["mode"] == report["mode"] == "measured"
    assert dash["coverage"] == report["coverage"]
    assert dash["coverage"]["sufficient"] is True                               # 6 reads >= threshold
    assert set(dash["today"]) == {"articlesRead", "avgReadingMinutes", "minutesRead",
                                  "politicalShare", "topTopics", "goalMinutes", "goalMet"}
    assert dash["today"]["articlesRead"] >= 1                                    # observedAt defaults to now
    assert isinstance(dash["streakDays"], int)


def test_signed_in_estimate_carries_accurate_coverage(client):
    """A signed-in reader who onboarded but hasn't crossed the read threshold gets an ESTIMATE report:
    mode='estimate', NO axisConfidence (a measured-only field), and coverage.reads reflecting their
    REAL partial read count (not the anonymous estimate's 0) — so 'N of 5 reads' progress is honest.
    This is the contract the UI relies on to (a) label Estimate vs Measured and (b) never render a
    measured-only field for an estimate."""
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "route-estimate"}).json()["userId"]
    hdr = {"X-IH-User-Id": str(uid)}
    outlets = [o["id"] for o in client.get("/api/outlets").json()[:4]]
    client.post("/api/me/onboarding", json={"outlets": outlets}, headers=hdr)
    # two reads — below the threshold, so the reader stays on the Estimate
    reads = [{"url": f"https://www.wsj.com/politics/e{i}", "title": f"Read {i}"} for i in range(2)]
    client.post("/api/me/reads", json={"reads": reads}, headers=hdr)

    rep = client.get("/api/report", headers=hdr).json()
    assert rep["mode"] == "estimate"
    assert "axisConfidence" not in rep                              # measured-only — never on an estimate
    assert rep["coverage"]["reads"] == 2 and rep["coverage"]["threshold"] == 5
    assert rep["coverage"]["sufficient"] is False                   # 2 < 5, still building
    # the dashboard mirrors the same estimate context
    dash = client.get("/api/dashboard", headers=hdr).json()
    assert dash["mode"] == "estimate" and dash["coverage"]["reads"] == 2


def test_analytics_from_the_users_stored_data(client):
    """Analytics is built entirely from the reader's stored snapshots + reads: honest empty series
    for a new reader, populated once they read (and a report snapshot is saved). Auth required."""
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "route-ana"}).json()["userId"]
    hdr = {"X-IH-User-Id": str(uid)}
    keys = {"coverage", "readingOverTime", "topicDiversity", "politicalDiversity", "publisherDiversity",
            "emotion", "reporting", "recommendationAcceptance", "healthImprovement"}

    empty = client.get("/api/me/analytics", headers=hdr).json()
    assert set(empty) == keys
    assert empty["coverage"] == {"reads": 0, "threshold": 5, "sufficient": False}   # new reader, still building
    assert all(v == [] for k, v in empty.items() if k != "coverage")   # honest empty series, all present

    reads = [{"url": f"https://www.foxnews.com/politics/s{i}", "title": f"Story {i}"} for i in range(6)]
    client.post("/api/me/reads", json={"reads": reads}, headers=hdr)
    client.get("/api/report", headers=hdr)                              # measured build -> saves a snapshot

    ana = client.get("/api/me/analytics", headers=hdr).json()
    assert ana["coverage"]["reads"] == 6 and ana["coverage"]["sufficient"] is True   # crossed the threshold
    assert sum(p["overall"] for p in ana["readingOverTime"]) == 6       # every read counted by day
    assert len(ana["healthImprovement"]) >= 1                           # >=1 saved snapshot

    assert client.get("/api/me/analytics").status_code == 401           # auth required


def test_profile_from_the_users_account_and_reads(client):
    """The profile is built from the reader's account + stored reads + snapshots; a brand-new user
    gets real identity with honest-empty activity, and reading populates streak + score history."""
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "route-prof",
                            "email": "reader@example.com", "displayName": "Casey Reader"}).json()["userId"]
    hdr = {"X-IH-User-Id": str(uid)}

    p0 = client.get("/api/me/profile", headers=hdr).json()
    assert p0["email"] == "reader@example.com" and p0["name"] == "Casey Reader"
    assert p0["handle"] == "reader" and p0["joinedAt"]
    assert p0["achievements"] == [] and p0["savedCount"] == 0 and "bookmarkCount" not in p0
    assert p0["scoreHistory"] == [] and p0["streakDays"] == 0               # no activity yet

    reads = [{"url": f"https://www.nytimes.com/politics/p{i}", "title": f"Story {i}"} for i in range(6)]
    client.post("/api/me/reads", json={"reads": reads}, headers=hdr)
    client.get("/api/report", headers=hdr)                                 # saves a report snapshot
    p1 = client.get("/api/me/profile", headers=hdr).json()
    assert p1["streakDays"] >= 1                                            # read today
    assert len(p1["scoreHistory"]) >= 1                                    # >=1 saved snapshot

    assert client.get("/api/me/profile").status_code == 401                # auth required


def test_saved_articles_persist_and_drive_the_profile_count(client):
    """Saving persists per-user, is idempotent (duplicate saves ignored), unsaving removes it, and the
    profile's Saved counter reflects the real persisted count throughout — the whole Commit 12 loop."""
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "route-saved",
                            "email": "saver@example.com", "displayName": "Sam Saver"}).json()["userId"]
    hdr = {"X-IH-User-Id": str(uid)}
    art = {"id": "https://cnn.com/2026/senate", "headline": "Senate passes bill", "publisher": "CNN"}

    assert client.get("/api/me/profile", headers=hdr).json()["savedCount"] == 0
    assert client.get("/api/me/saved", headers=hdr).json() == []

    r = client.post("/api/me/saved", json={"articleId": art["id"], "article": art}, headers=hdr).json()
    assert r == {"articleId": art["id"], "saved": True, "savedCount": 1}
    assert client.get("/api/me/profile", headers=hdr).json()["savedCount"] == 1

    # duplicate save is ignored — still one
    r2 = client.post("/api/me/saved", json={"articleId": art["id"], "article": art}, headers=hdr).json()
    assert r2["saved"] is True and r2["savedCount"] == 1

    art2 = {"id": "https://npr.org/2026/climate", "headline": "Climate deal", "publisher": "NPR"}
    client.post("/api/me/saved", json={"articleId": art2["id"], "article": art2}, headers=hdr)
    saved = client.get("/api/me/saved", headers=hdr).json()
    assert [s["articleId"] for s in saved] == [art2["id"], art["id"]]        # newest first
    assert saved[0]["article"]["headline"] == "Climate deal" and saved[0]["savedAt"]
    assert client.get("/api/me/profile", headers=hdr).json()["savedCount"] == 2

    d = client.delete("/api/me/saved", params={"articleId": art["id"]}, headers=hdr).json()
    assert d == {"articleId": art["id"], "saved": False, "savedCount": 1}
    # unsaving again is safe (no-op)
    assert client.delete("/api/me/saved", params={"articleId": art["id"]}, headers=hdr).json()["savedCount"] == 1
    assert client.get("/api/me/profile", headers=hdr).json()["savedCount"] == 1

    # auth required on every verb
    assert client.get("/api/me/saved").status_code == 401
    assert client.post("/api/me/saved", json={"articleId": "x"}).status_code == 401
    assert client.delete("/api/me/saved", params={"articleId": "x"}).status_code == 401


def test_reading_sync_single_identity_and_diagnostics(client):
    """The dev token and the web demo-login resolve to the SAME engine user, so extension reads land
    exactly where Reading History reads; an unknown/stale token 401s (never a wrong uid); and the
    dev diagnostics endpoint reports the identity match, token validity, and read count."""
    import api_fastapi as A
    dev = A._dev_token()
    assert dev, "dev token must be available in dev/test mode"

    # dev token -> the demo reader; the web demo-login upserts the SAME identity -> one uid.
    demo_uid = client.post("/api/internal/resolve-token", json={"token": dev}).json()["userId"]
    same = client.post("/api/internal/users",
                       json={"provider": "dev", "providerAccountId": "demo@infodiet.local",
                             "email": "demo@infodiet.local", "displayName": "Demo Reader"}).json()["userId"]
    assert same == demo_uid                                      # single identity

    # a read attributed to that identity appears in that user's Reading History.
    hdr = {"X-IH-User-Id": str(demo_uid)}
    client.post("/api/me/reads",
                json={"reads": [{"url": "https://www.nytimes.com/2026/ext", "title": "Extension read"}]},
                headers=hdr)
    hist = client.get("/api/me/history", headers=hdr).json()
    assert any(h["article"]["headline"] == "Extension read" for h in hist)

    # a stale / unknown token 401s — it never silently resolves to some other uid.
    assert client.post("/api/internal/resolve-token", json={"token": "stale-nope"}).status_code == 401

    # diagnostics: session and extension name the same user; token valid; read count > 0.
    d = client.get(f"/api/dev/diagnostics?token={dev}", headers=hdr).json()
    assert d["sessionUid"] == demo_uid and d["extensionUid"] == demo_uid
    assert d["match"] is True and d["tokenValid"] is True and d["readCount"] >= 1 and d["devToken"] == dev

    # a genuine mismatch is visible (session = demo, token = a different user's real token).
    other = client.post("/api/internal/users",
                        json={"provider": "google", "providerAccountId": "other-acct"}).json()["userId"]
    tok = client.post("/api/me/tokens", json={"label": "ext"},
                      headers={"X-IH-User-Id": str(other)}).json()["token"]
    d2 = client.get(f"/api/dev/diagnostics?token={tok}", headers=hdr).json()
    assert d2["extensionUid"] == other and d2["sessionUid"] == demo_uid and d2["match"] is False


def test_in_app_reads_carry_source_metadata_end_to_end(client):
    """Commit 14: an in-app read POSTed with readSource/openedFrom is recorded once (idempotent) and
    Reading History carries the attribution; a source-less read (extension/legacy) still records and
    omits the fields. readSource is metadata only — every source lands in the SAME reads pipeline."""
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "reader-14",
                            "email": "r14@x.com", "displayName": "R14"}).json()["userId"]
    hdr = {"X-IH-User-Id": str(uid)}

    r = client.post("/api/me/reads", json={"reads": [
        {"url": "https://www.cnn.com/2026/app-read", "title": "App read",
         "readSource": "app", "openedFrom": "discover", "device": "desktop"}]}, headers=hdr).json()
    assert r["accepted"] == 1
    # repeat is idempotent — one read per (user, canonical URL), source metadata not consulted
    assert client.post("/api/me/reads", json={"reads": [
        {"url": "https://www.cnn.com/2026/app-read", "title": "App read", "readSource": "app"}]},
        headers=hdr).json()["duplicates"] == 1

    entry = next(h for h in client.get("/api/me/history", headers=hdr).json()
                 if h["article"]["headline"] == "App read")
    assert entry["readSource"] == "app" and entry["openedFrom"] == "discover"

    # a source-less read (extension / legacy) still records; history omits the additive fields
    client.post("/api/me/reads", json={"reads": [
        {"url": "https://www.npr.org/2026/ext-read", "title": "Ext read"}]}, headers=hdr)
    ext = next(h for h in client.get("/api/me/history", headers=hdr).json()
               if h["article"]["headline"] == "Ext read")
    assert "readSource" not in ext and "openedFrom" not in ext   # exclude_none omits unknown source

    # both reads flow through the one pipeline the whole platform consumes
    assert len(client.get("/api/me/history", headers=hdr).json()) >= 2
    assert client.get("/api/me/profile", headers=hdr).json()["streakDays"] >= 1


def test_dev_diagnostics_absent_in_production(client, monkeypatch):
    """The diagnostics endpoint (and the fixed dev token) must not exist on a real deployment."""
    import api_fastapi as A
    monkeypatch.setattr(A, "_production", lambda: True)
    assert client.get("/api/dev/diagnostics").status_code == 404
    assert A._dev_token() is None                               # no fixed dev token in production


def test_settings_persist_and_merge(client):
    """Settings load with honest defaults, and partial updates merge over stored preferences and
    survive a reload — the persistence the mock page lacked. Auth required; preferences only."""
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "route-set"}).json()["userId"]
    hdr = {"X-IH-User-Id": str(uid)}
    full_keys = {"theme", "language", "politicalOpenness", "recommendationStrength",
                 "readingGoalMinutes", "weeklyReport", "monthlyReport", "notifications",
                 # Location Intelligence 1.5 — edition + followed places joined the contract.
                 "edition", "locations"}

    d = client.get("/api/me/settings", headers=hdr).json()
    # S1.2: `privacy` is gone from the contract — the response carries exactly the surviving keys.
    assert set(d) == full_keys and d["theme"] == "system" and d["politicalOpenness"] == 50  # defaults

    saved = client.post("/api/me/settings",
                        json={"theme": "dark", "notifications": {"streakReminders": True}},
                        headers=hdr).json()
    assert saved["theme"] == "dark" and saved["notifications"]["streakReminders"] is True
    assert saved["notifications"]["recommendations"] is True                 # untouched default kept

    again = client.get("/api/me/settings", headers=hdr).json()
    assert again["theme"] == "dark" and again["notifications"]["streakReminders"] is True  # persisted

    client.post("/api/me/settings", json={"readingGoalMinutes": 45}, headers=hdr)
    final = client.get("/api/me/settings", headers=hdr).json()
    assert final["theme"] == "dark" and final["readingGoalMinutes"] == 45    # earlier change preserved

    assert client.get("/api/me/settings").status_code == 401                 # auth required
    assert client.post("/api/me/settings", json={"theme": "light"}).status_code == 401


def test_legacy_privacy_patch_is_accepted_and_ignored(client):
    """S1.2 backward-compat at the API boundary: an old client PATCHing the removed ``privacy``
    keys must not 422 — the undeclared field is ignored (Pydantic ``extra='ignore'``), the real
    part of the patch still applies, and the response carries no ``privacy`` key."""
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "route-legacy-priv"}
                      ).json()["userId"]
    hdr = {"X-IH-User-Id": str(uid)}
    r = client.post("/api/me/settings",
                    json={"privacy": {"personalizedAds": True, "shareAnonymizedMetrics": True},
                          "weeklyReport": False}, headers=hdr)
    assert r.status_code == 200                                              # ignored, not rejected
    body = r.json()
    assert "privacy" not in body                                            # removed from the contract
    assert body["weeklyReport"] is False                                    # the real part applied


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
    # metrics (topic/source/reporting/emotional/echo/viewpoint + confidence) available, and the
    # Open-Mindedness card present but not yet available (an empty state until reception arrives).
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
    om_before = next(m for m in before["metrics"] if m["key"] == "openMindedness")
    assert om_before["available"] is False                             # 7/8: empty-state card, not hidden
    assert om_before["reason"] == "insufficient_data"                  # explicit backend signal

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
    om_after = next(m for m in after["metrics"] if m["key"] == "openMindedness")
    assert om_after["available"] is True                              # 8/8: the metric is now measured
    assert after_keys == before_keys                                  # same cards throughout; availability changed
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


# --------------------------------------------------------------------------- #
# Commit 1 — Fail-closed authentication.
#
# Local dev / the Colab demo (no production signal): the engine trusts the local web tier with
# zero config (unchanged). Production mode (RWE_ENV=production, or RWE_REQUIRE_AUTH=1): every
# internal / per-user call must carry the shared secret, and the engine refuses to start without
# it — so the audited "guess an X-IH-User-Id header -> full account takeover" no longer works.
# --------------------------------------------------------------------------- #
def test_dev_mode_preserves_zero_config_trust(client):
    """Local development is untouched: with no secret and no production signal, the web tier's
    X-IH-User-Id header is trusted, so /api/me works with zero extra configuration."""
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "devmode-1"}).json()["userId"]
    assert client.get("/api/me/profile", headers={"X-IH-User-Id": str(uid)}).status_code == 200


def test_config_errors_require_secret_in_production(monkeypatch):
    """The startup validator flags production mode without the internal secret, and clears once
    the secret is set — the check that makes a mis-configured prod deploy fail fast."""
    monkeypatch.delenv("RWE_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("RWE_INTERNAL_SECRET", raising=False)
    monkeypatch.setenv("RWE_ENV", "production")
    monkeypatch.setenv("RWE_DB_URL", "sqlite:////var/lib/ih/ih.db")  # a persistent DB (isolate this check)
    errs = api_fastapi._config_errors()
    assert errs and any("RWE_INTERNAL_SECRET" in e for e in errs)
    monkeypatch.setenv("RWE_INTERNAL_SECRET", "prod-secret")
    assert api_fastapi._config_errors() == []                       # secret + persistent DB -> no error
    # dev mode (no production signal) never trips the check
    monkeypatch.delenv("RWE_INTERNAL_SECRET", raising=False)
    monkeypatch.delenv("RWE_ENV", raising=False)
    assert api_fastapi._config_errors() == []


def test_config_errors_flag_ephemeral_db_in_production(monkeypatch):
    """Startup validation refuses ephemeral storage in production (data lost on restart): an
    in-memory DB and a /tmp file both fail; a persistent file (with the secret set) passes."""
    monkeypatch.setenv("RWE_ENV", "production")
    monkeypatch.setenv("RWE_INTERNAL_SECRET", "s3cret")            # satisfy the auth requirement
    monkeypatch.setenv("RWE_DB_URL", "sqlite://")                  # in-memory -> ephemeral
    assert any("ephemeral" in e for e in api_fastapi._config_errors())
    monkeypatch.setenv("RWE_DB_URL", "sqlite:////tmp/ih.db")       # temp dir -> ephemeral
    assert any("ephemeral" in e for e in api_fastapi._config_errors())
    monkeypatch.setenv("RWE_DB_URL", "sqlite:////var/lib/ih/ih.db")  # persistent file -> ok
    assert api_fastapi._config_errors() == []


def test_fail_closed_blocks_impersonation_in_production(client, monkeypatch):
    """Proof the audited exploit is closed: in dev the X-IH-User-Id header alone reaches a
    victim's account; with fail-closed auth on, the same unauthenticated header is refused across
    every /api/me endpoint and the internal resolver, while a correctly-signed call still works."""
    victim = client.post("/api/internal/users",
                         json={"provider": "google", "providerAccountId": "victim@x.com",
                               "email": "victim@x.com", "displayName": "Victim"}).json()["userId"]
    hdr = {"X-IH-User-Id": str(victim)}

    # dev (fail-open, as audited): the header alone is honoured
    assert client.get("/api/me/profile", headers=hdr).status_code == 200

    # turn on fail-closed auth; the unauthenticated header is now refused everywhere
    monkeypatch.setenv("RWE_REQUIRE_AUTH", "1")
    for path in ("/api/me", "/api/me/profile", "/api/me/history", "/api/me/settings"):
        assert client.get(path, headers=hdr).status_code == 401, path
    assert client.post("/api/me/tokens", json={}, headers=hdr).status_code == 401     # no token minting
    assert client.get(f"/api/internal/users/{victim}").status_code == 401             # no enumeration
    assert client.post("/api/internal/users",
                       json={"provider": "google", "providerAccountId": "x"}).status_code == 401

    # the legitimate web tier, signing with the matching secret, still works
    monkeypatch.setenv("RWE_INTERNAL_SECRET", "prod-secret")
    signed = {"X-IH-User-Id": str(victim), "X-IH-Auth": "prod-secret"}
    assert client.get("/api/me/profile", headers=signed).status_code == 200
    # ...but a wrong or missing secret is still refused (fail closed)
    assert client.get("/api/me/profile",
                      headers={"X-IH-User-Id": str(victim), "X-IH-Auth": "wrong"}).status_code == 401
    assert client.get("/api/me/profile", headers=hdr).status_code == 401


def test_cors_origins_policy(monkeypatch):
    """CORS is permissive in dev, locked in production, and an explicit allow-list wins."""
    monkeypatch.delenv("RWE_CORS_ORIGINS", raising=False)
    monkeypatch.delenv("RWE_ENV", raising=False)
    assert api_fastapi._cors_origins() == ["*"]                          # dev: permissive
    monkeypatch.setenv("RWE_ENV", "production")
    assert api_fastapi._cors_origins() == []                            # prod: locked (engine is internal)
    monkeypatch.setenv("RWE_CORS_ORIGINS", "https://app.example.com, https://admin.example.com")
    assert api_fastapi._cors_origins() == ["https://app.example.com", "https://admin.example.com"]


def test_response_security_headers(client):
    """Every engine response carries the JSON-API hardening headers; /api responses are no-store."""
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"
    # a cross-origin browser request is answered with CORS headers in dev (permissive default)
    cors = client.get("/api/health", headers={"Origin": "http://localhost:3000"})
    assert cors.headers.get("access-control-allow-origin") in {"*", "http://localhost:3000"}


def test_storage_diagnostics_endpoint(client, monkeypatch):
    """The internal storage endpoint reports live pragmas + a corruption probe, and is a trusted
    endpoint (requires the secret when configured)."""
    diag = client.get("/api/internal/storage").json()
    assert diag["quickCheck"] == "ok" and diag["foreignKeys"] is True
    assert "journalMode" in diag and "ephemeral" in diag
    # trusted like the other /api/internal/* routes
    monkeypatch.setenv("RWE_INTERNAL_SECRET", "s3cret")
    assert client.get("/api/internal/storage").status_code == 401
    assert client.get("/api/internal/storage", headers={"X-IH-Auth": "s3cret"}).status_code == 200


def test_startup_aborts_in_production_without_secret(monkeypatch):
    """The engine refuses to start (lifespan raises) when production mode is on but the internal
    secret is missing — it fails loudly instead of coming up fail-open. (Runs last: a failed
    startup raises before any app state is built, so it never disturbs the shared client.)"""
    monkeypatch.delenv("RWE_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("RWE_INTERNAL_SECRET", raising=False)
    monkeypatch.setenv("RWE_ENV", "production")
    with pytest.raises(RuntimeError):
        with TestClient(api_fastapi.app):
            pass


# --------------------------------------------------------------------------- #
# Live recommendation source wiring — the seam the Honest URL Pass-through rides on. The URL only
# ever reaches a recommendation when the recommender is actually SOURCED from the feed catalog, so
# the env wiring that selects that source must be authoritative (regression: it used setdefault,
# which silently left a pre-set RWE_PROFILE — e.g. the docker default 'synthetic' — in force and
# ignored the feed CSV, so no publisher URL ever appeared).
# --------------------------------------------------------------------------- #
def _seed_feed(store_, n=60):
    for i in range(n):
        u = f"https://real-news.example/politics/{i}"
        store_.upsert_feed_article(
            canonical_url=u, url=u, publisher="Fox News", source_publisher="Fox News",
            title=f"story {i}", description="x", body=None,
            # now-relative: the C4 freshness gate (default 60 days) must see these as candidates
            published_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            source_feed="feed://x",
            scored={"article_id": u, "outlet": "Fox News", "category": "Politics", "lean": 1.4})


def test_configure_recs_source_forces_qbias_profile_over_preset(tmp_path, monkeypatch):
    """Enabling the feed source must switch the corpus to the feed CSV even when RWE_PROFILE is
    already set to a non-qbias value — otherwise the feed (and its URLs) is silently ignored."""
    import store, feed_source
    monkeypatch.setenv("RWE_RECS_SOURCE", "feed")
    monkeypatch.setenv("RWE_PROFILE", "synthetic")            # the docker/compose default, pre-set
    monkeypatch.setenv("RWE_FEED_MIN_ARTICLES", "50")
    monkeypatch.setenv("RWE_FEED_CORPUS_CSV", str(tmp_path / "feed.csv"))
    monkeypatch.delenv("RWE_QBIAS", raising=False)

    st = store.Store("sqlite://")
    _seed_feed(st, 60)                                        # at/above threshold
    feed_csv = api_fastapi._configure_recs_source(st)

    assert feed_csv and os.path.exists(feed_csv)              # catalog exported
    assert os.environ["RWE_PROFILE"] == "qbias"              # ...and the profile is now authoritative
    assert os.environ["RWE_QBIAS"] == feed_csv               # ...pointed at the feed catalog
    # the exported catalog carries the URLs the pass-through resolves against
    assert any(v.startswith("https://real-news.example/")
               for v in feed_source.load_url_map(feed_csv).values())


def test_configure_recs_source_disabled_or_too_small_keeps_corpus(tmp_path, monkeypatch):
    """Disabled, or a catalog below the threshold, returns None and touches no corpus env — so the
    existing (static) corpus stays in force and enabling the flag before any ingest is safe."""
    import store
    monkeypatch.setenv("RWE_PROFILE", "mind")                # a pre-set profile that must survive
    monkeypatch.delenv("RWE_QBIAS", raising=False)

    # (a) disabled outright
    monkeypatch.delenv("RWE_RECS_SOURCE", raising=False)
    assert api_fastapi._configure_recs_source(store.Store("sqlite://")) is None
    assert os.environ["RWE_PROFILE"] == "mind" and "RWE_QBIAS" not in os.environ

    # (b) enabled but the catalog is too small -> graceful fallback, corpus untouched
    monkeypatch.setenv("RWE_RECS_SOURCE", "feed")
    monkeypatch.setenv("RWE_FEED_MIN_ARTICLES", "50")
    monkeypatch.setenv("RWE_FEED_CORPUS_CSV", str(tmp_path / "small.csv"))
    st = store.Store("sqlite://")
    _seed_feed(st, 10)                                        # below threshold
    assert api_fastapi._configure_recs_source(st) is None
    assert os.environ["RWE_PROFILE"] == "mind" and "RWE_QBIAS" not in os.environ


def test_feed_health_endpoint(client, monkeypatch):
    """GET /api/internal/feeds reports per-feed health + quality, with a derived status, and is a
    trusted route (requires the internal secret when configured)."""
    st = api_fastapi.state.store
    st.record_feed_health("https://ok.example/feed", ok=True, name="OK",
                          latency_ms=90.0, stats={"new": 3, "duplicates": 1, "missing_metadata": 0},
                          unhealthy_after=3)
    st.record_feed_health("https://down.example/feed", ok=False, name="Down",
                          error=OSError("connection refused"), latency_ms=200.0, unhealthy_after=1)
    # a feed that polls fine but only serves old content (the CNN case): healthy AND stale
    st.record_feed_health("https://stale.example/feed", ok=True, name="Stale", latency_ms=70.0,
                          stats={"new": 0, "duplicates": 5, "newest": "2023-01-01T00:00:00+00:00",
                                 "oldest": "2023-01-01T00:00:00+00:00"}, unhealthy_after=3)
    # a healthy feed with fresh content is not stale
    fresh_iso = datetime.now(timezone.utc).isoformat()
    st.record_feed_health("https://fresh.example/feed", ok=True, name="Fresh", latency_ms=60.0,
                          stats={"new": 4, "newest": fresh_iso, "oldest": fresh_iso}, unhealthy_after=3)

    feeds = {f["feedUrl"]: f for f in client.get("/api/internal/feeds").json()}
    assert feeds["https://ok.example/feed"]["status"] == "healthy"
    assert feeds["https://ok.example/feed"]["imported"] == 3 and feeds["https://ok.example/feed"]["healthy"] is True
    assert feeds["https://down.example/feed"]["status"] == "unhealthy"
    assert feeds["https://down.example/feed"]["healthy"] is False
    assert "connection refused" in feeds["https://down.example/feed"]["lastError"]
    # staleness is a separate axis from availability: healthy status, stale content, still polled
    stale = feeds["https://stale.example/feed"]
    assert stale["status"] == "healthy" and stale["stale"] is True
    assert stale["newestAgeDays"] is not None and stale["newestAgeDays"] > 30 and stale["staleThresholdDays"] == 30
    assert feeds["https://fresh.example/feed"]["stale"] is False           # fresh content -> not stale

    monkeypatch.setenv("RWE_INTERNAL_SECRET", "s3cret")       # trusted like the other /api/internal/* routes
    assert client.get("/api/internal/feeds").status_code == 401


def test_corpus_validation_endpoint(client, monkeypatch):
    """GET /api/internal/corpus reports candidate-corpus eligibility + diagnostics, is a trusted
    route, and NEVER activates anything — a validation probe must leave the live Backend untouched.
    Inserts are cleaned up so the shared module store is left as found (see the feedArticles==0 test)."""
    st = api_fastapi.state.store
    urls = []
    try:
        for i in range(4):
            for pub, lean in (("CV-Left", -1.5), ("CV-Center", 0.0), ("CV-Right", 1.5)):
                u = f"https://cv-{pub}-{i}.example/a"
                urls.append(u)
                st.upsert_feed_article(canonical_url=u, url=u, publisher=pub, source_publisher=pub,
                                       title=f"{pub} {i}", description="", body=None,
                                       published_at="2026-07-06T12:00:00+00:00", source_feed="f",
                                       scored={"article_id": u, "outlet": pub, "lean": lean,
                                               "category": "Politics"})
        monkeypatch.setenv("RWE_CORPUS_MIN_ARTICLES", "1")   # low floor so a small test corpus is eligible

        be_before = api_fastapi.state.backend
        body = client.get("/api/internal/corpus").json()
        assert api_fastapi.state.backend is be_before        # validation NEVER rebuilds / activates Backend

        assert isinstance(body["eligible"], bool) and body["status"] in {"pass", "fail"}
        assert body["eligible"] is True                      # only the min_articles>=1 floor is on
        for pub in ("CV-Left", "CV-Center", "CV-Right"):
            assert pub in body["publisherDistribution"]
        assert set(body["politicalDistribution"]) == {"left", "center", "right"}
        assert isinstance(body["failures"], list) and "missingMetadataPct" in body["metrics"]
        assert "healthyFeeds" in body and "unhealthyFeeds" in body

        monkeypatch.setenv("RWE_INTERNAL_SECRET", "s3cret")  # trusted like the other /api/internal/* routes
        assert client.get("/api/internal/corpus").status_code == 401
    finally:
        st.delete_feed_articles(urls)                        # leave the shared store as we found it


def test_refresh_status_endpoint(client, monkeypatch):
    """GET /api/internal/refresh reports the active corpus generation + activation state, is a trusted
    route, and triggers nothing (a diagnostics read leaves the live Backend untouched)."""
    be_before = api_fastapi.state.backend
    body = client.get("/api/internal/refresh").json()
    assert api_fastapi.state.backend is be_before            # a diagnostics read never activates

    assert body["generation"] == 1 and body["activeVersion"] == 1   # boot corpus is generation 1
    assert body["state"] == "idle" and body["refreshCount"] == 0
    assert body["source"] in {"feed", "static"} and isinstance(body["pollingEnabled"], bool)
    # health surfaces the same generation for a one-GET check
    assert client.get("/api/health").json()["recommendationSource"]["generation"] == 1

    monkeypatch.setenv("RWE_INTERNAL_SECRET", "s3cret")      # trusted like the other /api/internal/* routes
    assert client.get("/api/internal/refresh").status_code == 401


def test_search_endpoint(client):
    """GET /api/search returns live FeedArticle results with pagination + filters, preserving the
    canonical URL (the Read flow). Public read-only, like /api/discover. Inserts are cleaned up."""
    st = api_fastapi.state.store
    urls = []
    try:
        for i, (pub, lean, cat) in enumerate([("SearchNPR", -1.2, "Politics"),
                                              ("SearchFox", 1.4, "Politics"),
                                              ("SearchAP", 0.0, "Climate")] * 3):
            u = f"https://{pub}-{i}.example/a"
            urls.append(u)
            st.upsert_feed_article(canonical_url=u, url=u, publisher=pub, source_publisher=pub,
                                   title=f"{pub} headline {i}", description="body text", body=None,
                                   published_at="2026-07-06T12:00:00+00:00", source_feed="f",
                                   scored={"article_id": u, "outlet": pub, "lean": lean, "category": cat})
        body = client.get("/api/search", params={"query": "headline", "limit": 4, "offset": 0,
                                                 "debug": "true"}).json()
        assert body["total"] == 9 and body["pageSize"] == 4 and body["page"] == 1
        assert body["hasMore"] is True and body["remainingPages"] == 2 and len(body["results"]) == 4
        assert "queryMs" in body and isinstance(body["ftsAvailable"], bool)
        # Unenriched fixtures carry no register — the field is honestly ABSENT (L2.2 family),
        # never a "reporting" default.
        assert body["results"][0]["url"].startswith("https://") and "register" not in body["results"][0]

        left = client.get("/api/search", params={"lean": "left", "limit": 50}).json()
        assert {a["publisher"] for a in left["results"]} == {"SearchNPR"}
        pol = client.get("/api/search", params={"topic": "Climate", "limit": 50}).json()
        assert {a["publisher"] for a in pol["results"]} == {"SearchAP"}
    finally:
        st.delete_feed_articles(urls)


def test_stories_endpoint_envelope_and_detail(client):
    """GET /api/stories is a paginated Story envelope from the Story Service; /api/story/{id} (and the
    /api/stories/{id} alias) return one Story whose coverage articles keep their canonical URLs."""
    st = api_fastapi.state.store
    urls = []
    try:
        # one event across 3 publishers (L/C/R) + a distinct 2-publisher event
        for cu, pub, lean, title, cat in [
            ("https://s-npr.example/1", "StNPR", -1.1, "Capitol vote advances the relief package tonight", "Politics"),
            ("https://s-bbc.example/1", "StBBC", 0.0, "Capitol vote advances relief package after debate", "Politics"),
            ("https://s-fox.example/1", "StFox", 1.3, "Capitol vote advances relief package averting lapse", "Politics"),
            ("https://w-cnn.example/1", "StCNN", -1.2, "Coastal storm floods harbor towns overnight", "Climate"),
            ("https://w-grd.example/1", "StGuardian", -1.4, "Coastal storm floods harbor towns and roads", "Climate"),
        ]:
            urls.append(cu)
            st.upsert_feed_article(canonical_url=cu, url=cu, publisher=pub, source_publisher=pub, title=title,
                                   description="d", body=None, published_at="2026-07-06T12:00:00+00:00",
                                   source_feed="f", scored={"article_id": cu, "outlet": pub, "lean": lean,
                                                            "category": cat})
        body = client.get("/api/stories", params={"debug": "true"}).json()
        assert body["total"] >= 2 and body["page"] == 1 and isinstance(body["hasMore"], bool)
        assert "clusterMs" in body and body["diagnostics"]["storyCount"] == body["total"]
        cap = next(s for s in body["stories"] if "Capitol" in s["title"])
        assert cap["publisherCount"] == 3 and set(cap["publishers"]) == {"StNPR", "StBBC", "StFox"}
        # nullable image contract: omitted while null (response_model_exclude_none), appears once enriched
        assert cap.get("image") is None and cap.get("imageAttribution") is None

        # filter: only stories that include a publisher / a lean side
        assert client.get("/api/stories", params={"publisher": "StCNN"}).json()["total"] == 1
        assert client.get("/api/stories", params={"lean": "right"}).json()["total"] == 1   # only the Capitol event

        sid = cap["id"]
        detail = client.get(f"/api/story/{sid}").json()                 # new singular route
        assert detail["id"] == sid and all(c["url"].startswith("https://") for c in detail["coverage"])
        assert client.get(f"/api/stories/{sid}").json()["id"] == sid    # backward-compatible alias
        assert client.get("/api/story/st_bogus").status_code == 404
    finally:
        st.delete_feed_articles(urls)


def test_stories_carry_intelligence_summary(client):
    """Commit 10: /api/stories enriches each Story with the lightweight freshness + lifecycle badge
    (story_intelligence.compute_summary), so cards need no extra request. Additive — the Story Service
    clustering / pagination is unchanged; Stories consumes Intelligence, never the reverse."""
    st = api_fastapi.state.store
    now_iso = datetime.now(timezone.utc).isoformat()
    urls = ["https://si-npr.example/1", "https://si-bbc.example/1", "https://si-fox.example/1"]
    try:
        for cu, pub, lean in [(urls[0], "SiNPR", -1.1), (urls[1], "SiBBC", 0.0), (urls[2], "SiFox", 1.3)]:
            st.upsert_feed_article(canonical_url=cu, url=cu, publisher=pub, source_publisher=pub,
                                   title="Budget summit reaches a breakthrough agreement tonight",
                                   description="d", body=None, published_at=now_iso, source_feed="f",
                                   scored={"article_id": cu, "outlet": pub, "lean": lean, "category": "Politics"})
        s = next(x for x in client.get("/api/stories").json()["stories"] if "Budget" in x["title"])
        assert isinstance(s["freshness"], dict)
        assert 0 <= s["freshness"]["score"] <= 100
        assert s["lifecycle"] in {"Breaking", "Developing", "Mature", "Archived"}
        # freshly published across 3 publishers inside the breaking window -> Breaking badge
        assert s["freshness"]["band"] == "Breaking" and s["lifecycle"] == "Breaking"
    finally:
        st.delete_feed_articles(urls)


def test_story_intelligence_endpoint(client):
    """Commit 10: GET /api/story/{id}/intelligence returns the full deterministic intelligence; a
    signed-in reader's prior read of the event seeds newSinceLastVisit; a bogus id is 404. Read-only —
    it changes no recommendation, report, or read tracking."""
    st = api_fastapi.state.store
    now = datetime.now(timezone.utc)
    early, late = (now - timedelta(hours=6)).isoformat(), now.isoformat()
    urls = ["https://intel-npr.example/1", "https://intel-bbc.example/1", "https://intel-fox.example/1"]
    try:
        for cu, pub, lean, at in [(urls[0], "IntelNPR", -1.1, early), (urls[1], "IntelBBC", 0.0, early),
                                  (urls[2], "IntelFox", 1.3, late)]:
            st.upsert_feed_article(canonical_url=cu, url=cu, publisher=pub, source_publisher=pub,
                                   title="Trade council unveils a sweeping tariff overhaul plan",
                                   description="d", body=None, published_at=at, source_feed="f",
                                   scored={"article_id": cu, "outlet": pub, "lean": lean, "category": "Business"})
        sid = next(x for x in client.get("/api/stories").json()["stories"] if "Trade" in x["title"])["id"]
        intel = client.get(f"/api/story/{sid}/intelligence").json()
        for k in ("storyId", "freshness", "lifecycle", "momentum", "coverageStatistics", "timeline",
                  "newSinceLastVisit", "alerts", "diagnostics"):
            assert k in intel
        assert intel["storyId"] == sid and intel["diagnostics"]["coverageCount"] == 3
        assert intel["newSinceLastVisit"]["lastVisited"] is None          # anonymous -> empty baseline

        # a signed-in reader who read the earliest article sees the later coverage as "new"
        uid = st.upsert_user_by_identity("dev", "intel-reader").id
        st.add_read(uid, urls[0], {"article_id": urls[0], "outlet": "IntelNPR"}, early)
        intel2 = client.get(f"/api/story/{sid}/intelligence", headers={"X-IH-User-Id": str(uid)}).json()
        assert intel2["newSinceLastVisit"]["lastVisited"] == early
        assert intel2["lastUpdated"] == intel2["newSinceLastVisit"]["lastUpdated"]
        assert intel2["newSinceLastVisit"]["count"] >= 1                  # the late article is new

        assert client.get("/api/story/st_bogus/intelligence").status_code == 404
    finally:
        st.delete_feed_articles(urls)


def test_media_serialization_and_rec_enrichment(client):
    """Media (Commit 9): Discover/Search carry the article image + publisher logo; recommendations are
    enriched with media from the live FeedArticle after serialization (protected serializer unchanged);
    a rec with no matching article stays image-less (graceful)."""
    st = api_fastapi.state.store
    u = "https://mediafox.example/a"
    try:
        st.upsert_feed_article(canonical_url=u, url=u, publisher="MediaFox", source_publisher="MediaFox",
                               title="Imaged headline story", description="d", body=None,
                               published_at="2026-07-06T12:00:00+00:00", source_feed="f",
                               scored={"article_id": u, "outlet": "MediaFox", "lean": 1.2, "category": "Politics"},
                               image="https://mediafox.example/hero.jpg", image_width=1200, image_height=800,
                               image_mime="image/jpeg", image_source="media:content", image_attribution="MediaFox")
        # Search (and Discover) carry image + width + publisher logo via feed_article_to_article.
        res = client.get("/api/search", params={"query": "Imaged", "limit": 5}).json()["results"][0]
        assert res["image"] == "https://mediafox.example/hero.jpg" and res["imageWidth"] == 1200
        assert res["publisherLogo"] == "https://mediafox.example/favicon.ico"

        # Recommendation enrichment: a rec whose URL matches the FeedArticle gains the image + logo.
        recs = [{"article": {"id": u, "url": u, "publisher": "MediaFox"}, "crossCutting": False}]
        api_fastapi._enrich_rec_media(recs)
        assert recs[0]["article"]["image"] == "https://mediafox.example/hero.jpg"
        assert recs[0]["article"]["publisherLogo"] == "https://mediafox.example/favicon.ico"

        # A rec with no matching catalog article stays image-less (graceful) but still gets a logo.
        recs2 = [{"article": {"id": "Q1", "url": "https://other.example/z", "publisher": "Other"}, "crossCutting": False}]
        api_fastapi._enrich_rec_media(recs2)
        assert "image" not in recs2[0]["article"]
        assert recs2[0]["article"]["publisherLogo"] == "https://other.example/favicon.ico"
    finally:
        st.delete_feed_articles([u])


# --------------------------------------------------------------------------- #
# Preference sliders — settings genuinely shape the signed-in reader's feed
# --------------------------------------------------------------------------- #
def _feed_ids(client, strategy="rwe-b", headers=None):
    r = client.get(f"/api/recommendations?strategy={strategy}", headers=headers or {})
    assert r.status_code == 200
    return [x["article"]["id"] for x in r.json()]


def test_a_signed_in_reader_with_no_reading_gets_no_recommendations(client):
    """A recommendation is not a neutral article list — every card asserts "this offers another
    political perspective", which is a claim about the reader's existing diet. Served to somebody
    who has read nothing, a screen of "Bridging" cards bridges away from a position they never held.

    The response is a plain list, so there is nowhere to hang a provenance marker the way the report
    does. Nothing is the honest payload."""
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "no-history-recs"}).json()["userId"]
    assert _feed_ids(client, headers={"X-IH-User-Id": str(uid)}) == []
    assert _feed_ids(client) != [], "anonymous keeps the showcase — a visitor is not told it is theirs"


def test_sliders_shape_the_feed_end_to_end(client):
    """POST /api/me/settings → GET /api/recommendations: a MEASURED reader's untouched sliders serve
    the default stack; a moved slider changes their feed; anonymous is never affected.

    The reader is seeded past the read threshold on purpose. This used to run as a fresh signed-in
    user and lean on the demo fallback to produce a feed at all — so it was really asserting that
    the fallback respected sliders, not that a reader's own feed did. A reader with no reading now
    correctly gets nothing, which would have made the "moved slider changed the feed" assertion pass
    against two empty lists."""
    st = api_fastapi.state.store
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "sliders-1"}).json()["userId"]
    for i in range(api_fastapi.engine.ESTIMATE_MIN_READS + 1):
        url = f"https://sliders-seed.example/{i}"
        st.add_read(uid, url, {"article_id": url, "outlet": "NPR", "lean": -0.8, "title": "t"})
    hdr = {"X-IH-User-Id": str(uid)}
    anonymous = _feed_ids(client)                                  # the shared default stack
    # THEIR baseline, not the anonymous one. A measured reader's feed is built from their own
    # corpus, so it legitimately differs from anonymous — the old test could compare the two only
    # because the fallback served both readers the same thing.
    baseline_b = _feed_ids(client, headers=hdr)
    baseline_d = _feed_ids(client, strategy="rwe-d", headers=hdr)
    assert baseline_b != [], "a measured reader has a feed of their own"

    # Political openness maps to the RWE-B bridge-slot budget (W1), and it DOES reshape a sided
    # reader's served feed end-to-end (the cross-cutting card count tracks the 4/6/8 budget) — proven
    # in test_api_server::test_openness_reshapes_the_served_feed. We assert only the plumbing here
    # (round-trip + no leak to anonymous), NOT a feed change, because this `client` fixture is
    # module-scoped: state accumulated by earlier tests in this file can route a fresh signed-in user
    # (via _serve) to a reader whose feed is openness-insensitive at this point — a fixture-ordering
    # artifact, not a centered reader and not a serving bug.
    client.post("/api/me/settings", json={"politicalOpenness": 0}, headers=hdr)
    assert _feed_ids(client) == anonymous                          # anonymous unaffected

    # Moving a slider must not disturb anonymous, and must not leave residue on the reader.
    # This test does NOT assert that a moved slider visibly reorders THIS reader's feed: whether it
    # does depends on how sided their history is, and a seeded six-read reader is not reliably
    # sided. That reshape is proven where it can be controlled —
    # test_api_server::test_openness_reshapes_the_served_feed for the feed, and
    # test_api_server's rec_params_from_settings cases for the slider -> hyperparameter mapping.
    # Asserting it here would be asserting a fixture's mood.
    client.post("/api/me/settings", json={"recommendationStrength": 100}, headers=hdr)
    assert _feed_ids(client) == anonymous                          # still no leak

    # back to 50/50 -> their own baseline again (no residue, nothing cached per user)
    client.post("/api/me/settings",
                json={"politicalOpenness": 50, "recommendationStrength": 50}, headers=hdr)
    assert _feed_ids(client, headers=hdr) == baseline_b
    assert _feed_ids(client, strategy="rwe-d", headers=hdr) == baseline_d
    assert _feed_ids(client) == anonymous                          # and anonymous never moved


def test_dashboard_reports_reading_goal_progress(client):
    """A signed-in reader's dashboard carries today-vs-goal progress from their stored goal;
    the anonymous dashboard has no goal keys (response_model_exclude_none drops them)."""
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": "goal-1"}).json()["userId"]
    hdr = {"X-IH-User-Id": str(uid)}
    client.post("/api/me/settings", json={"readingGoalMinutes": 5}, headers=hdr)
    client.post("/api/me/reads",
                json={"reads": [{"url": "https://www.nytimes.com/2026/business/markets-rally",
                                 "title": "Markets rally on strong earnings"}]}, headers=hdr)

    t = client.get("/api/dashboard", headers=hdr).json()["today"]
    assert t["goalMinutes"] == 5
    assert isinstance(t["minutesRead"], int) and t["minutesRead"] >= 1
    assert t["goalMet"] == (t["minutesRead"] >= 5)

    anon = client.get("/api/dashboard").json()["today"]
    assert "goalMinutes" not in anon and "goalMet" not in anon
    assert anon["minutesRead"] == 0


# --------------------------------------------------------------------------- #
# Commit 18 — extension reads become first-class FeedArticles (producer #4)
# --------------------------------------------------------------------------- #
def _ext_read(url, title, **extra):
    """An extension-shaped read item (the web tier stamps readSource on the token path)."""
    return {"url": url, "title": title, "readSource": "extension",
            "description": extra.pop("description", "A short standard-metadata abstract."),
            "image": extra.pop("image", "https://cdn.example.com/hero.jpg"),
            "siteName": extra.pop("siteName", "The Example Times"),
            "publishedAt": extra.pop("publishedAt", "2026-07-10T08:00:00+00:00"),
            "language": "en", "author": "A. Reporter", **extra}


def _mkuser(client, acct):
    uid = client.post("/api/internal/users",
                      json={"provider": "google", "providerAccountId": acct}).json()["userId"]
    return uid, {"X-IH-User-Id": str(uid)}


def test_extension_read_creates_provisional_article(client):
    """Case 2 + participation: the article exists with extension provenance + provisional status,
    is visible to Search (Case 4) and the story data path (Case 5), but hidden from Discover."""
    st = api_fastapi.state.store
    url = "https://news-site.example/politics/zebra-quorum-vote"
    _uid, hdr = _mkuser(client, "c18-new")
    r = client.post("/api/me/reads", json={"reads": [_ext_read(url, "Zebra quorum vote passes")]},
                    headers=hdr).json()
    assert r["accepted"] == 1

    row = st.get_feed_article("https://news-site.example/politics/zebra-quorum-vote")
    assert row is not None
    assert row["sourceType"] == "extension" and row["articleState"] == "provisional"
    assert row["image"] == "https://cdn.example.com/hero.jpg"           # og metadata persisted
    assert row["publishedAt"] == "2026-07-10T08:00:00+00:00"
    assert (row["scored"] or {}).get("category")                        # classified by the one scorer

    # Case 4 — Search sees it immediately
    hits = client.get("/api/search?query=zebra+quorum").json()
    assert any("zebra-quorum" in (a.get("url") or "") for a in hits["results"])
    # Case 5 — the Stories data path sees it (same shared query, default include)
    rows, total = st.search_feed_articles(q="zebra quorum")
    assert total >= 1
    # Discover hides provisional articles until promoted
    disc = client.get("/api/discover?limit=200").json()
    assert not any("zebra-quorum" in (a.get("url") or "") for a in disc["articles"])


def test_extension_read_of_cataloged_article_reuses_it(client):
    """Case 1: no duplicate FeedArticle, active status untouched, read recorded."""
    st = api_fastapi.state.store
    url = "https://feeds.example/world/aid-convoy"
    st.upsert_feed_article(canonical_url=url, url=url, publisher="AP", source_publisher="AP",
                           title="Aid convoy reaches the region", description="d", body=None,
                           published_at=None, source_feed="feed://ap", source_type="rss",
                           scored={"article_id": url, "outlet": "AP", "category": "World"})
    before = st.count_feed_articles()
    _uid, hdr = _mkuser(client, "c18-existing")
    r = client.post("/api/me/reads", json={"reads": [_ext_read(url, "Aid convoy reaches the region")]},
                    headers=hdr).json()
    assert r["accepted"] == 1
    assert st.count_feed_articles() == before                           # merged, never duplicated
    assert st.get_feed_article(url)["articleState"] is None             # stays active


def test_two_readers_one_article_and_promotion(client):
    """Cases 3 + 11: two users → one FeedArticle + two Reads; the second distinct reader promotes
    it, and Discover picks it up."""
    st = api_fastapi.state.store
    url = "https://news-site.example/tech/quantum-lattice-chip"
    _u1, h1 = _mkuser(client, "c18-reader-1")
    _u2, h2 = _mkuser(client, "c18-reader-2")

    client.post("/api/me/reads", json={"reads": [_ext_read(url, "Quantum lattice chip unveiled")]},
                headers=h1)
    assert st.get_feed_article(url)["articleState"] == "provisional"
    disc = client.get("/api/discover?limit=200").json()
    assert not any("quantum-lattice" in (a.get("url") or "") for a in disc["articles"])

    client.post("/api/me/reads", json={"reads": [_ext_read(url, "Quantum lattice chip unveiled")]},
                headers=h2)
    assert st.get_feed_article(url)["articleState"] == "verified"       # promoted by 2nd reader
    assert st.count_feed_articles() == len({a["canonicalUrl"] for a in st.list_feed_articles(10_000)})
    disc = client.get("/api/discover?limit=200").json()
    assert any("quantum-lattice" in (a.get("url") or "") for a in disc["articles"])   # now eligible


def test_extension_catalog_failure_never_loses_the_read(client, monkeypatch):
    """Case 10: article creation blows up → the read is still recorded and the request succeeds."""
    def _boom(*a, **k):
        raise RuntimeError("synthetic ingestion failure")
    monkeypatch.setattr(api_fastapi.rss_ingest, "ingest_entries", _boom)
    st = api_fastapi.state.store
    url = "https://news-site.example/health/mitochondria-study"
    _uid, hdr = _mkuser(client, "c18-failure")
    r = client.post("/api/me/reads", json={"reads": [_ext_read(url, "Mitochondria study lands")]},
                    headers=hdr)
    assert r.status_code == 200 and r.json()["accepted"] == 1
    assert st.get_feed_article(url) is None                             # no article — and no error
    hist = client.get("/api/me/history", headers=hdr).json()
    assert any("mitochondria-study" in (e.get("article", {}).get("url") or "") for e in hist)


def test_app_reads_do_not_produce_articles(client):
    """D1: only the extension is a producer — an in-app/paste read never touches the catalog."""
    st = api_fastapi.state.store
    url = "https://news-site.example/culture/opera-revival"
    _uid, hdr = _mkuser(client, "c18-app-read")
    r = client.post("/api/me/reads",
                    json={"reads": [{"url": url, "title": "Opera revival", "readSource": "app"}]},
                    headers=hdr).json()
    assert r["accepted"] == 1
    assert st.get_feed_article(url) is None


def test_extension_read_marks_catalog_dirty(client):
    """D6: creating a NEW catalog article from the request path nudges the refresh manager, so the
    next poll cycle runs the refresh check even on quiet feeds; a duplicate read must NOT re-flag."""
    st = api_fastapi.state.store
    ref = api_fastapi.state.refresh
    ref.catalog_dirty = False
    url = "https://news-site.example/science/dirty-flag-probe"
    _uid, hdr = _mkuser(client, "c18-dirty")
    client.post("/api/me/reads", json={"reads": [_ext_read(url, "Dirty flag probe lands")]},
                headers=hdr)
    assert st.get_feed_article(url) is not None
    assert ref.catalog_dirty is True                      # new article -> nudged

    ref.catalog_dirty = False
    client.post("/api/me/reads", json={"reads": [_ext_read(url, "Dirty flag probe lands")]},
                headers=hdr)                              # duplicate read, article exists
    assert ref.catalog_dirty is False                     # merge only -> no nudge
