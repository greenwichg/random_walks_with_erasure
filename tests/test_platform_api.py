"""The /v1 platform surface: keys, scopes, plans, quotas, metering, licence withholding.

Driven through the standalone app (``platform_api.app.create_app``) over an in-memory store seeded
by the real ingest path, plus one test that mounts onto the engine app to prove the consumer
routes stand unchanged beside it.
"""

import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import identity  # noqa: E402
import platform_keys  # noqa: E402
import rss_ingest  # noqa: E402
import store as store_mod  # noqa: E402
import story_service  # noqa: E402
from platform_api import app as platform_app  # noqa: E402
from platform_api import metering, plans, shape  # noqa: E402

E = rss_ingest.FeedEntry


@pytest.fixture(autouse=True)
def _platform_on(monkeypatch):
    monkeypatch.setenv("RWE_PLATFORM_API", "1")
    monkeypatch.delenv("RWE_PLATFORM_PUBLISH_RATINGS", raising=False)
    monkeypatch.delenv("RWE_PLATFORM_PUBLISH_WIKIPEDIA", raising=False)
    monkeypatch.setenv("RWE_STORIES_SCAN_DAYS", "36500")
    metering.reset()
    story_service.clear_cache()
    yield
    story_service.clear_cache()


@pytest.fixture
def st():
    st = store_mod.Store("sqlite:///:memory:")
    scorer = rss_ingest.make_scorer()
    rss_ingest.ingest_entries([
        E(url="https://www.bbc.co.uk/news/articles/abc123", title="Prime minister resigns after vote",
          published_at="2026-09-01T10:00:00+00:00", description="A long day in Westminster. " * 30,
          publisher_hint="bbc.co.uk", image="https://ichef.bbci.co.uk/x.jpg"),
        E(url="https://www.theguardian.com/politics/2026/sep/01/pm-resigns",
          title="Prime minister resigns after confidence vote", published_at="2026-09-01T11:00:00+00:00",
          publisher_hint="theguardian.com"),
    ], "BBC", "https://feeds.bbci.co.uk/news/rss.xml", scorer, st, source_type="rss")
    rss_ingest.ingest_entries([
        E(url="https://www.npr.org/2026/09/01/pm-resigns-vote", title="Prime minister resigns after losing vote",
          published_at="2026-09-01T13:00:00+00:00", publisher_hint="npr.org",
          source_type="newsapi", source_provider="NewsAPI"),
    ], None, "newsapi", scorer, st, source_type="newsapi")
    rss_ingest.ingest_entries([
        E(url="https://www.npr.org/2026/09/01/only-one-reader-saw-this", title="Prime minister resigns: what we know",
          published_at="2026-09-01T14:00:00+00:00", publisher_hint="npr.org", source_type="extension"),
    ], None, "extension", scorer, st, source_type="extension")
    identity.sync_publishers(st)
    st.platform_create_tenant("acme", "Acme Corp", kind="developer")
    return st


@pytest.fixture
def client(st):
    return TestClient(platform_app.create_app(st))


def _key(st, **kw):
    kw.setdefault("tenant_id", "acme")
    kw.setdefault("plan", "developer")
    secret, meta = st.platform_mint_key(**kw)
    return secret, meta, {"Authorization": f"Bearer {secret}"}


# ---- authentication ------------------------------------------------------------------------ #

def test_refusals_carry_stable_codes(st, client, monkeypatch):
    assert client.get("/v1/health").status_code == 200
    r = client.get("/v1/articles")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthenticated"
    r = client.get("/v1/articles", headers={"Authorization": "Bearer hv_live_nope"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthenticated"
    secret, meta, h = _key(st)
    assert client.get("/v1/articles", headers=h).status_code == 200
    st.platform_revoke_key(meta["keyId"])
    r = client.get("/v1/articles", headers=h)
    assert r.status_code == 401 and r.json()["error"]["code"] == "key_revoked"
    _, _, h2 = _key(st, expires_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    assert client.get("/v1/articles", headers=h2).json()["error"]["code"] == "key_expired"
    _, _, h3 = _key(st)
    st.platform_set_tenant_status("acme", "suspended")
    r = client.get("/v1/articles", headers=h3)
    assert r.status_code == 403 and r.json()["error"]["code"] == "tenant_suspended"
    st.platform_set_tenant_status("acme", "active")
    monkeypatch.setenv("RWE_PLATFORM_API", "0")
    r = client.get("/v1/articles", headers=h3)
    assert r.status_code == 503 and r.json()["error"]["code"] == "platform_disabled"


def test_scopes_and_plans(st, client):
    _, _, dev = _key(st)                                       # developer: no stories:history
    _, _, ent = _key(st, plan="enterprise")
    _, _, narrow = _key(st, scopes=["usage:read"])
    story_id = client.get("/v1/stories", headers=dev).json()["data"][0]["storyId"]
    r = client.get(f"/v1/stories/{story_id}/history", headers=dev)
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden_scope"
    assert client.get(f"/v1/stories/{story_id}/history", headers=ent).status_code == 200
    assert client.get("/v1/articles", headers=narrow).status_code == 403
    assert client.get("/v1/usage", headers=narrow).status_code == 200
    eff = plans.effective({"plan": "developer", "scopes": ["articles:read", "bogus"], "quotaMonth": 5})
    assert eff["scopes"] == {"articles:read"} and eff["quota_month"] == 5 and eff["rate_per_min"] == 60
    assert "reader_private" not in plans.effective({"plan": "internal",
                                                   "licenceClasses": ["reader_private", "metadata_public"]})["licence_classes"]


# ---- licence withholding ------------------------------------------------------------------- #

def test_articles_carry_identity_and_withhold_by_licence_class(st, client):
    _, _, dev = _key(st)
    d = client.get("/v1/articles", headers=dev).json()
    by_pub = {a["publisher"]: a for a in d["data"]}
    assert set(by_pub) == {"BBC", "The Guardian", "NPR"}          # the reader-private row is absent
    assert all(a["articleId"].startswith("ar_") and a["publisherId"].startswith("pub_") for a in d["data"])
    bbc = by_pub["BBC"]
    assert bbc["licence"]["class"] == "metadata_public" and "url" in bbc and "canonicalUrl" in bbc
    assert len(bbc["description"]) <= shape.DESCRIPTION_MAX + 1 and bbc["description"].endswith("…")
    assert "lean" in bbc["withheld"] and "lean" not in bbc          # ratings off by default
    npr = by_pub["NPR"]
    assert npr["licence"]["class"] == "provider_restricted"
    assert "headline" not in npr and "url" not in npr and "canonicalUrl" not in npr
    assert {"headline", "url"} <= set(npr["withheld"])
    assert npr["publishedAt"] and npr["topic"] is not None
    assert d["meta"]["versions"]["scorer"] == "1" and d["meta"]["versions"]["registry"].startswith("sha256:")
    assert d["meta"]["ratingsPublished"] is False
    # an internal key carries the provider class and sees the row in full
    _, _, internal = _key(st, plan="internal")
    npr_full = {a["publisher"]: a for a in client.get("/v1/articles", headers=internal).json()["data"]}["NPR"]
    assert npr_full["headline"].startswith("Prime minister") and "url" in npr_full
    assert "withheld" in npr_full and npr_full["withheld"] == ["lean"]


def test_ratings_are_a_deployment_switch(st, client, monkeypatch):
    _, _, dev = _key(st)
    d = client.get("/v1/stories", headers=dev).json()["data"][0]
    assert "distribution" not in d and "distribution" in d["withheld"]
    r = client.get("/v1/stories", params={"lean": "left"}, headers=dev)
    assert r.status_code == 403 and r.json()["error"]["code"] == "ratings_not_published"
    monkeypatch.setenv("RWE_PLATFORM_PUBLISH_RATINGS", "1")
    d = client.get("/v1/stories", headers=dev).json()
    assert d["meta"]["ratingsPublished"] is True
    s = d["data"][0]
    assert set(s["distribution"]) == {"left", "center", "right"} and "withheld" not in s
    a = {a["publisher"]: a for a in client.get("/v1/articles", headers=dev).json()["data"]}["BBC"]
    assert a["lean"] == 0.0 and a["leanBucket"] == "center"


def test_story_detail_history_and_coverage_classes(st, client):
    _, _, ent = _key(st, plan="enterprise")
    listing = client.get("/v1/stories", headers=ent).json()
    assert listing["meta"]["page"]["total"] == 1
    sid = listing["data"][0]["storyId"]
    d = client.get(f"/v1/stories/{sid}", headers=ent).json()["data"]
    assert d["title"].startswith("Prime minister") and d["lifecycle"] and d["freshness"]
    assert sorted(d["publisherIds"]) == sorted({identity.publisher_id_for(p) for p in d["publishers"]})
    cov = {c["publisher"]: c for c in d["coverage"]}
    assert set(cov) == {"BBC", "The Guardian", "NPR"}
    assert "url" in cov["BBC"] and cov["BBC"]["articleId"].startswith("ar_")
    assert "url" not in cov["NPR"] and cov["NPR"]["withheld"] == ["headline", "url"]
    assert cov["NPR"]["licence"]["class"] == "provider_restricted"
    h = client.get(f"/v1/stories/{sid}/history", headers=ent).json()["data"]
    assert h["story"]["status"] == "active" and len(h["snapshots"]) == 1
    assert h["snapshots"][0]["distribution"] is None and "snapshots.distribution" in h["withheld"]
    members = {m["publisher"]: m for m in h["membership"]}
    assert "url" in members["BBC"] and "url" not in members["NPR"]
    intel = client.get(f"/v1/stories/{sid}/intelligence", headers=ent).json()["data"]
    assert "newSinceLastVisit" not in intel and intel["lifecycle"]
    assert client.get(f"/v1/stories/{sid}/similar", headers=ent).json()["meta"]["total"] == 0
    assert client.get("/v1/stories/st_nope", headers=ent).status_code == 404
    assert client.get("/v1/stories/st_nope/history", headers=ent).status_code == 404


def test_article_lookup_by_id_and_by_url(st, client):
    _, _, dev = _key(st)
    a = {a["publisher"]: a for a in client.get("/v1/articles", headers=dev).json()["data"]}["BBC"]
    by_id = client.get(f"/v1/articles/{a['articleId']}", headers=dev).json()["data"]
    assert by_id["articleId"] == a["articleId"] and by_id["storyId"].startswith("st_")
    assert by_id["provenance"][0]["channel"] == "rss"
    by_url = client.get("/v1/articles/by-url", params={"url": "https://www.bbc.co.uk/news/articles/abc123"},
                        headers=dev).json()["data"]
    assert by_url["articleId"] == a["articleId"]
    assert client.get("/v1/articles/ar_0000000000000000dead", headers=dev).status_code == 404
    # the reader-private row is a 404 even by exact url
    r = client.get("/v1/articles/by-url", params={"url": "https://www.npr.org/2026/09/01/only-one-reader-saw-this"},
                   headers=dev)
    assert r.status_code == 404


def test_publishers_by_name_and_id(st, client):
    _, _, dev = _key(st)
    d = client.get("/v1/publishers", params={"name": "bbc.co.uk"}, headers=dev).json()["data"][0]
    assert d["name"] == "BBC" and d["registered"] and "bbc.co.uk" in d["hosts"]
    assert "lean" not in d and {"lean", "factuality", "credibility"} <= set(d["withheld"])
    detail = client.get(f"/v1/publishers/{d['publisherId']}", headers=dev).json()["data"]
    assert detail["articles"]["total"] == 1 and "topics" in detail
    assert client.get("/v1/publishers/pub_nope", headers=dev).status_code == 404
    assert client.get("/v1/publishers", params={"name": "no such outlet anywhere"}, headers=dev).status_code == 404
    listing = client.get("/v1/publishers", params={"limit": 2}, headers=dev).json()
    assert len(listing["data"]) == 2 and listing["meta"]["page"]["nextCursor"] == "2"
    assert client.get("/v1/articles", params={"publisher_id": "pub_nope"}, headers=dev).status_code == 404


# ---- metering, quota, rate ----------------------------------------------------------------- #

def test_usage_is_metered_and_quota_enforced(st, client):
    secret, meta, h = _key(st, quota_month=2)
    r = client.get("/v1/articles", headers=h)
    assert r.status_code == 200 and r.headers["X-Usage-Limit"] == "2" and r.headers["X-Usage-Month"] == "1"
    assert client.get("/v1/stories", headers=h).status_code == 200
    r = client.get("/v1/articles", headers=h)
    assert r.status_code == 429 and r.json()["error"]["code"] == "quota_exceeded"
    u = client.get("/v1/usage", headers=h)                     # a 429 still lets the meter be read? no: it is quota-gated too
    assert u.status_code == 429
    month = metering.month_of()
    totals = st.platform_usage_month("acme", month)
    assert totals["units"] == 2 and totals["requests"] == 4    # two answers, two refusals
    daily = st.platform_usage("acme", since_day=f"{month}-01")
    assert sum(d["errors"] for d in daily) == 2
    assert all(d["endpoint"].startswith("/v1/") for d in daily)


def test_rate_limit_per_key(st, client):
    _, _, h = _key(st, rate_per_min=1)
    assert client.get("/v1/articles", headers=h).status_code == 200
    r = client.get("/v1/articles", headers=h)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited" and r.headers["Retry-After"]
    _, _, other = _key(st, rate_per_min=1)
    assert client.get("/v1/articles", headers=other).status_code == 200   # buckets are per key


def test_cursor_paging_and_bad_cursors(st, client):
    _, _, h = _key(st)
    p1 = client.get("/v1/articles", params={"limit": 2}, headers=h).json()
    assert len(p1["data"]) == 2 and p1["meta"]["page"]["nextCursor"] == "2"
    p2 = client.get("/v1/articles", params={"limit": 2, "cursor": "2"}, headers=h).json()
    assert len(p2["data"]) == 1 and p2["meta"]["page"]["nextCursor"] is None
    r = client.get("/v1/articles", params={"cursor": "later"}, headers=h)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_cursor"
    assert client.get("/v1/articles", params={"limit": 500}, headers=h).status_code == 422


def test_usage_endpoint_reports_the_tenants_month(st, client):
    _, _, h = _key(st)
    client.get("/v1/articles", headers=h)
    d = client.get("/v1/usage", headers=h).json()["data"]
    assert d["tenantId"] == "acme" and d["units"] == 1 and d["quotaMonth"] == 10_000
    assert d["daily"] and d["daily"][0]["endpoint"] == "/v1/articles"


# ---- the CLI ------------------------------------------------------------------------------- #

def test_platform_keys_cli(tmp_path, capsys):
    db = f"sqlite:///{tmp_path}/keys.db"
    assert platform_keys.main(["--db", db, "tenant", "create", "acme", "--name", "Acme"]) == 0
    assert platform_keys.main(["--db", db, "mint", "--tenant", "acme", "--plan", "developer",
                               "--label", "ci", "--quota", "5"]) == 0
    out = capsys.readouterr()
    secret = out.out.strip().splitlines()[-1]
    assert secret.startswith("hv_live_") and '"prefix"' in out.err and secret not in out.err
    assert platform_keys.main(["--db", db, "list", "--tenant", "acme"]) == 0
    listed = capsys.readouterr().out
    assert '"quotaMonth": 5' in listed and secret not in listed
    assert platform_keys.main(["--db", db, "mint", "--tenant", "nobody"]) == 2
    assert platform_keys.main(["--db", db, "mint", "--tenant", "acme", "--scopes", "bogus"]) == 2
    st = store_mod.Store(db)
    key_id = st.platform_list_keys("acme")[0]["keyId"]
    assert platform_keys.main(["--db", db, "revoke", key_id]) == 0
    assert platform_keys.main(["--db", db, "revoke", key_id]) == 1
    assert platform_keys.main(["--db", db, "usage", "acme"]) == 0
    assert platform_keys.main(["--db", db, "tenant", "suspend", "acme"]) == 0


# ---- mounted into the engine --------------------------------------------------------------- #

def test_engine_mount_leaves_the_consumer_routes_alone(monkeypatch):
    os.environ.setdefault("RWE_N_USERS", "150")
    os.environ.setdefault("RWE_MAX_ITEMS", "400")
    os.environ.setdefault("RWE_SEED", "0")
    monkeypatch.setenv("RWE_DB_URL", "sqlite://")
    import importlib
    api = importlib.import_module("api_fastapi")
    before = {getattr(r, "path", None) for r in api.app.routes}
    assert platform_app.mount(api.app, api._require_store, get_request_id=api._request_id.get) in (True, False)
    assert platform_app.mount(api.app, api._require_store) is False          # idempotent
    after = {getattr(r, "path", None) for r in api.app.routes}
    # A router included after the app has started may sit in `routes` as an included-router
    # object with no `path` of its own (hence the None); every path that DID appear is /v1.
    added = {p for p in after - before if p is not None}
    assert before <= after and all(str(p).startswith("/v1") for p in added)
    with TestClient(api.app) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/stories").status_code == 200
        r = c.get("/v1/articles")
        assert r.status_code == 401 and r.json()["error"]["code"] == "unauthenticated"
        assert r.headers.get("X-Request-ID")
        st = api.state.store
        st.platform_create_tenant("t", "T")
        secret, _ = st.platform_mint_key(tenant_id="t", plan="developer")
        r = c.get("/v1/articles", headers={"Authorization": f"Bearer {secret}"})
        assert r.status_code == 200 and r.json()["data"] == []
        assert r.json()["meta"]["requestId"] == r.headers["X-Request-ID"]
