"""PA1 — product analytics: taxonomy/normalization, the pure funnel/metric/retention leaf, the store
round-trip, and the /api/events sink + internal dashboard (validation, server-side identity, gating)."""
import importlib.util
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


pa = _load("product_analytics")
store_mod = _load("store")
api_fastapi = _load("api_fastapi")


# A synthetic journey: one person (anon "A" → user 7) who converts through the whole funnel and
# returns the next day, plus one anonymous visitor ("B") who bounces at App Opened.
def _journey():
    return [
        {"event": "app_opened", "anonId": "A", "userId": None, "serverTs": "2026-07-01T10:00:00+00:00", "props": {}},
        {"event": "signin_started", "anonId": "A", "userId": None, "serverTs": "2026-07-01T10:01:00+00:00", "props": {"method": "google"}},
        {"event": "account_created", "anonId": "A", "userId": 7, "serverTs": "2026-07-01T10:02:00+00:00", "props": {"method": "google"}},
        {"event": "login_success", "anonId": "A", "userId": 7, "serverTs": "2026-07-01T10:02:05+00:00", "props": {"method": "google"}},
        {"event": "source_connected", "userId": 7, "serverTs": "2026-07-01T10:03:00+00:00", "props": {"outletCount": 4}},
        {"event": "article_read", "userId": 7, "serverTs": "2026-07-01T10:05:00+00:00", "props": {"source": "recommendations"}},
        {"event": "health_report_viewed", "userId": 7, "serverTs": "2026-07-01T10:06:00+00:00", "props": {"mode": "estimate"}},
        {"event": "health_report_viewed", "userId": 7, "serverTs": "2026-07-02T09:00:00+00:00", "props": {"mode": "measured"}},
        {"event": "app_opened", "userId": 7, "serverTs": "2026-07-02T09:00:00+00:00", "props": {}},
        {"event": "recommendations_viewed", "userId": 7, "serverTs": "2026-07-02T09:01:00+00:00", "props": {"count": 6}},
        {"event": "recommendation_opened", "userId": 7, "serverTs": "2026-07-02T09:02:00+00:00", "props": {"strategy": "rwe-b"}},
        {"event": "app_opened", "anonId": "B", "userId": None, "serverTs": "2026-07-01T11:00:00+00:00", "props": {}},
    ]


# --------------------------------------------------------------------------- #
# taxonomy + normalization
# --------------------------------------------------------------------------- #
def test_normalize_drops_unknown_events():
    assert pa.normalize({"event": "not_a_real_event"}) is None
    assert pa.normalize({"event": 123}) is None
    assert pa.normalize("nope") is None


def test_normalize_allowlists_and_truncates_props():
    out = pa.normalize({"event": "app_opened", "anonId": "a1", "sessionId": "s1",
                        "clientTs": "2026-01-01T00:00:00Z",
                        "props": {"path": "/report", "evil": {"x": 1}, "referrer": "x" * 500}})
    assert out["event"] == "app_opened" and out["anon_id"] == "a1" and out["session_id"] == "s1"
    assert out["props"]["path"] == "/report"
    assert "evil" not in out["props"]                 # non-allow-listed key dropped
    assert len(out["props"]["referrer"]) <= 200       # truncated
    # identity/time that must be authoritative are NOT taken from the client
    assert "user_id" not in out and "server_ts" not in out


# --------------------------------------------------------------------------- #
# funnel
# --------------------------------------------------------------------------- #
def test_funnel_stages_conversion_and_stitching():
    f = pa.funnel(_journey())
    by = {s["key"]: s for s in f["stages"]}
    # stitched identity: A's pre-auth + 7's post-auth fold into one user; B stays anonymous.
    assert f["totalIdentities"] == 2
    assert by["app_opened"]["reachers"] == 2          # user 7 + anon B
    assert by["account_created"]["reachers"] == 1
    assert by["measured_report"]["reachers"] == 1
    assert by["recommendation_accepted"]["reachers"] == 1   # an open counts as accepted
    assert by["returned_next_day"]["reachers"] == 1         # user 7 came back on day 2
    assert by["account_created"]["conversionFromPrev"] == 0.5
    assert by["app_opened"]["conversionFromStart"] == 1.0
    # the biggest drop is App Opened -> Account Created (2 -> 1)
    assert f["topDropOff"]["fromStage"] == "app_opened" and f["topDropOff"]["dropPct"] == 0.5


def test_recommendation_accepted_counts_positive_feedback():
    rows = [
        {"event": "recommendations_viewed", "userId": 1, "serverTs": "2026-07-01T00:00:00+00:00", "props": {}},
        {"event": "recommendation_feedback", "userId": 1, "serverTs": "2026-07-01T00:01:00+00:00", "props": {"action": "like"}},
        {"event": "recommendations_viewed", "userId": 2, "serverTs": "2026-07-01T00:00:00+00:00", "props": {}},
        {"event": "recommendation_feedback", "userId": 2, "serverTs": "2026-07-01T00:01:00+00:00", "props": {"action": "dislike"}},
    ]
    by = {s["key"]: s for s in pa.funnel(rows)["stages"]}
    assert by["recommendation_accepted"]["reachers"] == 1     # only the 'like' user; 'dislike' is not acceptance


def test_funnel_empty_is_all_zeroes():
    f = pa.funnel([])
    assert f["totalIdentities"] == 0
    assert all(s["reachers"] == 0 for s in f["stages"])
    assert f["topDropOff"] is None


# --------------------------------------------------------------------------- #
# metrics + retention + determinism
# --------------------------------------------------------------------------- #
def test_product_metrics_values():
    m = pa.product_metrics(_journey())
    assert m["accountsCreated"] == 1
    assert m["activationRate"] == 1.0                 # 1 report / 1 account
    assert m["measuredActivationRate"] == 1.0
    assert m["timeToFirstReportSeconds"] == 240.0     # 10:02:00 -> 10:06:00
    assert m["timeToMeasuredModeSeconds"] == 82680.0  # 10:02:00 -> next day 09:00:00
    assert m["recommendationEngagementRate"] == 1.0
    assert m["day1Retention"] == 0.5                  # user 7 retained, anon B not (cohort of 2)


def test_retention_cohort_and_day1():
    r = pa.retention(_journey())
    assert r["cohort"] == 2 and r["day1"]["retained"] == 1 and r["day1"]["rate"] == 0.5
    assert r["day7"]["retained"] == 0                 # future-ready, ~0 in a short window


def test_determinism_is_order_independent():
    rows = _journey()
    assert pa.funnel(rows) == pa.funnel(list(reversed(rows)))
    assert pa.product_metrics(rows) == pa.product_metrics(list(reversed(rows)))


def test_event_counts():
    c = pa.event_counts(_journey())
    assert c["total"] == 12 and c["byEvent"]["app_opened"] == 3 and c["byEvent"]["health_report_viewed"] == 2


# --------------------------------------------------------------------------- #
# store round-trip
# --------------------------------------------------------------------------- #
def test_store_record_and_list_roundtrip(tmp_path):
    st = store_mod.Store(f"sqlite:///{tmp_path/'a.db'}")
    n = st.record_analytics_events([
        {"event": "app_opened", "anon_id": "z", "props": {"path": "/"}, "server_ts": "2026-07-01T00:00:00+00:00"},
        {"event": "login_success", "user_id": 3, "anon_id": "z", "props": {"method": "google"},
         "server_ts": "2026-07-01T00:01:00+00:00", "request_id": "rid1"},
    ])
    assert n == 2 and st.count_analytics_events() == 2
    rows = st.list_analytics_events()
    assert [r["event"] for r in rows] == ["app_opened", "login_success"]
    assert rows[0]["props"] == {"path": "/"} and rows[1]["userId"] == 3
    # `since` bounds by server_ts
    assert len(st.list_analytics_events(since="2026-07-01T00:00:30+00:00")) == 1


# --------------------------------------------------------------------------- #
# API — the sink + the internal dashboard
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def client():
    with TestClient(api_fastapi.app) as c:
        yield c


def test_events_sink_validates_and_stores(client):
    r = client.post("/api/events", json={"events": [
        {"event": "app_opened", "anonId": "probe-anon", "props": {"path": "/onboarding"}},
        {"event": "totally_unknown", "anonId": "probe-anon"},        # dropped by the allow-list
    ]})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["accepted"] == 1 and body["dropped"] == 1


def test_events_sink_resolves_user_server_side(client):
    # a known user; the sink must attribute to THIS id (from the trusted header), ignoring any client claim
    uid = api_fastapi.state.store.upsert_user_by_identity("google", "pa1-probe").id
    r = client.post("/api/events", headers={"X-IH-User-Id": str(uid)},
                    json={"events": [{"event": "login_success", "anonId": "probe-anon",
                                      "userId": 999999, "props": {"method": "google"}}]})
    assert r.status_code == 200 and r.json()["accepted"] == 1
    rows = api_fastapi.state.store.list_analytics_events()
    mine = [x for x in rows if x["event"] == "login_success" and x["anonId"] == "probe-anon"]
    assert mine and mine[-1]["userId"] == uid          # server-resolved, not the client's 999999


def test_analytics_dashboard_returns_funnel(client):
    snap = client.get("/api/analytics/funnel").json()      # dev: _trusted is open
    assert "stages" in snap and len(snap["stages"]) == 10
    assert client.get("/api/analytics/metrics").json().get("identities") is not None
    assert "byEvent" in client.get("/api/analytics/events").json()


def test_analytics_dashboard_is_internal_only_in_production(client, monkeypatch):
    # with a secret configured, the dashboard requires the internal header → 404 to an un-headered caller
    monkeypatch.setattr(api_fastapi, "_internal_secret", lambda: "s3cret")
    assert client.get("/api/analytics/funnel").status_code == 404
    assert client.get("/api/analytics/funnel", headers={"X-IH-Auth": "s3cret"}).status_code == 200


# --------------------------------------------------------------------------- the taxonomy is a contract
def _tracked_events() -> set:
    """Every event name the web client actually calls ``track()`` with."""
    import re
    web = pathlib.Path(__file__).resolve().parent.parent / "web"
    names = set()
    for path in list(web.rglob("*.ts")) + list(web.rglob("*.tsx")):
        if "node_modules" in path.parts or ".next" in path.parts:
            continue
        names |= set(re.findall(r'track\(\s*"([a-z_]+)"', path.read_text(encoding="utf-8")))
    return names


def test_every_event_the_client_tracks_is_allow_listed():
    """The sink DROPS any event whose name is not in ``EVENTS``, silently and by design — so a
    client-side ``track()`` call is not instrumentation until the name is here.

    Story Continuation shipped all six of its events tracked and unlisted. Every one was discarded
    for the feature's whole life, and the loss was invisible from both ends: the client's `track` is
    fire-and-forget, and the sink answers 200 with the drop only in its `dropped` count. The
    measurement plan the design leans on — armed→shown loss, the `surface` comparison behind §9.1.1,
    the decay curve meant to retire the 4 h guess — recorded nothing at all.
    """
    tracked = _tracked_events()
    assert tracked, "found no track() calls — the scan is broken, not the taxonomy"
    missing = sorted(tracked - set(pa.EVENTS))
    assert not missing, (
        f"tracked by the client but dropped by the sink: {missing}. "
        f"Add them to product_analytics.EVENTS (and PROPS) or stop tracking them."
    )


def test_every_allow_listed_event_declares_its_properties():
    """An event in EVENTS but absent from PROPS is stored with its properties silently stripped —
    the row survives, the measurement does not, which is the harder version of the bug above."""
    undeclared = sorted(e for e in pa.EVENTS if e not in pa.PROPS)
    assert not undeclared, f"in EVENTS with no PROPS entry: {undeclared}"


def test_continuation_props_survive_sanitization():
    """Pins the property names against what the strip sends. A renamed key is dropped by
    `sanitize_props` without error, so `surface` or `minutesSinceRead` could go missing from every
    row while the event count stayed healthy."""
    shown = pa.sanitize_props("continuation_shown", {
        "storyId": "s-1", "hiddenMs": 25_000, "minutesSinceRead": 3, "impressionIndex": 1,
        "distance": 1.4, "surface": "card", "publisher": "CNN", "url": "https://x.example.com/a",
    })
    assert shown == {"storyId": "s-1", "hiddenMs": 25_000, "minutesSinceRead": 3,
                     "impressionIndex": 1, "distance": 1.4, "surface": "card"}, shown

    eligible = pa.sanitize_props("continuation_eligible", {
        "storyId": "s-1", "anchorLean": -0.6, "siblingLean": 0.8, "distance": 1.4,
        "candidateCount": 3,
    })
    assert set(eligible) == {"storyId", "anchorLean", "siblingLean", "distance", "candidateCount"}
