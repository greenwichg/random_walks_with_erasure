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


def test_every_platform_timestamp_carries_its_utc_offset(st, client):
    """The first external key's `mint` printed `createdAt` with `+00:00` and `list` printed the
    same field without it (SQLite hands a DateTime back naive). One format on the wire, whether
    the row was just flushed or read back, on every timestamp a key or tenant carries."""
    secret, meta, h = _key(st)
    client.get("/v1/articles", headers=h)                               # stamps lastUsedAt
    st.platform_revoke_key(meta["keyId"])
    rows = [st.platform_key(meta["keyId"]), *st.platform_list_keys("acme"), st.platform_tenant("acme")]
    stamps = [(k, v) for r in rows for k, v in r.items()
              if k in ("createdAt", "lastUsedAt", "expiresAt", "revokedAt") and v is not None]
    assert {k for k, _ in stamps} >= {"createdAt", "lastUsedAt", "revokedAt"}
    for k, v in stamps:
        parsed = datetime.fromisoformat(v)
        assert parsed.tzinfo is not None and v.endswith("+00:00"), (k, v)
    me = client.get("/v1/me", headers=h).json()
    assert me["error"]["code"] == "key_revoked"                          # the revoked key refuses …
    secret2, meta2, h2 = _key(st)
    created = client.get("/v1/me", headers=h2).json()["data"]["key"]["createdAt"]
    assert created.endswith("+00:00") and created == meta2["createdAt"]  # … and /v1/me matches mint


def test_iso_utc_stamps_naive_values_and_keeps_offsets():
    iso = store_mod._iso_utc
    assert iso(None) is None
    assert iso(datetime(2026, 9, 5, 19, 17, 45, 59368)) == "2026-09-05T19:17:45.059368+00:00"
    assert iso(datetime(2026, 9, 5, 21, 0, tzinfo=timezone(timedelta(hours=2)))) == "2026-09-05T19:00:00+00:00"
    assert iso("2027-01-01T00:00:00") == "2027-01-01T00:00:00+00:00"    # an operator's --expires, typed naive
    assert iso("2027-01-01T00:00:00+00:00") == "2027-01-01T00:00:00+00:00"
    assert iso("2027-01-01T00:00:00Z") == "2027-01-01T00:00:00Z"        # already unambiguous: untouched
    assert iso("not a date") == "not a date"


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


# ---- Phase 1: the commercial access layer --------------------------------------------------- #

def _seed_outlet_index(tmp_path, monkeypatch):
    import outlet_search as osx
    monkeypatch.setenv("RWE_OUTLET_INDEX_DB", str(tmp_path / "v1-idx.db"))
    con = osx.open_index()
    for i in range(3):
        osx.upsert(con, f"ke{i}.example", name=f"KE Outlet {i}", country="KE", source="wikidata")
    osx.upsert(con, "ke0.example", source="wikipedia")           # corroborated -> deterministic top
    osx.upsert(con, "bbc.co.uk", name="BBC", country="GB", source="wikidata")   # a tracked one
    con.commit()
    con.close()


def test_x_api_key_header_is_an_alternative_to_bearer(st, client):
    secret, _, _ = _key(st)
    r = client.get("/v1/articles", headers={"X-API-Key": secret})
    assert r.status_code == 200 and r.headers["X-RateLimit-Limit"] == "60"
    r = client.get("/v1/articles", headers={"Authorization": "Bearer hv_live_wrong", "X-API-Key": secret})
    assert r.status_code == 401                                   # a presented bearer is never overridden
    assert client.get("/v1/articles", params={"api_key": secret}).status_code == 401   # never the query string


def test_me_describes_the_key_and_its_month(st, client):
    _, meta, h = _key(st, scopes=["articles:read", "usage:read"], quota_month=50)
    client.get("/v1/articles", headers=h)
    d = client.get("/v1/me", headers=h).json()["data"]
    assert d["tenantId"] == "acme" and d["tenantName"] == "Acme Corp" and d["keyId"] == meta["keyId"]
    assert d["plan"] == "developer" and d["scopes"] == ["articles:read", "usage:read"]
    assert d["licenceClasses"] == ["metadata_public"]
    assert d["limits"] == {"ratePerMin": 60, "quotaMonth": 50}
    assert d["usage"]["month"] == metering.month_of() and d["usage"]["units"] == 1
    assert d["published"] == {"ratings": False, "wikipedia": False}
    _, _, narrow = _key(st, scopes=["usage:read"])
    assert client.get("/v1/me", headers=narrow).status_code == 200   # any key may read itself


def test_openapi_and_docs_are_public_and_unmetered(st, client):
    schema = client.get("/v1/openapi.json").json()
    assert schema["info"]["title"] == "Hidden View Platform API"
    paths = schema["paths"]
    assert "/v1/health" in paths and "security" not in paths["/v1/health"]["get"]
    assert "/v1/openapi.json" not in paths and "/v1/docs" not in paths
    for path in ("/v1/articles", "/v1/stories/{story_id}/coverage-comparison", "/v1/tags/{tag}",
                 "/v1/publishers/by-host", "/v1/outlets/search", "/v1/me", "/v1/entities",
                 "/v1/countries", "/v1/articles/{article_id}/entities",
                 "/v1/publishers/{publisher_id}/articles", "/v1/publishers/{publisher_id}/stories"):
        assert paths[path]["get"]["security"] == [{"bearerAuth": []}, {"apiKeyAuth": []}], path
        assert paths[path]["get"]["summary"]
    assert set(schema["components"]["securitySchemes"]) == {"bearerAuth", "apiKeyAuth"}
    r = client.get("/v1/docs")
    assert r.status_code == 200 and "/v1/openapi.json" in r.text
    assert st.platform_usage_month("acme", metering.month_of())["requests"] == 0


def test_coverage_comparison_follows_member_licence_classes(st, client, monkeypatch):
    _, _, dev = _key(st)
    _, _, internal = _key(st, plan="internal")
    sid = client.get("/v1/stories", headers=dev).json()["data"][0]["storyId"]
    arts = {a["publisher"]: a for a in client.get("/v1/articles", headers=internal).json()["data"]}
    bbc = arts["BBC"]["articleId"]
    r = client.get(f"/v1/stories/{sid}/coverage-comparison", headers=dev)
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_request"
    d = client.get(f"/v1/stories/{sid}/coverage-comparison", params={"article_id": bbc},
                   headers=dev).json()["data"]
    assert d["available"] is True and d["storyId"] == sid and d["articleId"] == bbc
    assert d["tier"] == "L0" and d["outlets"] == 3 and d["textClaims"] is False
    others = next(f for f in d["reportedElsewhere"] if f["key"] == "other_outlets")
    assert others["support"] == 2 and others["of"] == 3
    ev = {e["publisher"]: e for e in others["evidence"]}
    assert set(ev) == {"The Guardian", "NPR"}
    assert "url" in ev["The Guardian"] and ev["The Guardian"]["articleId"].startswith("ar_")
    assert "url" not in ev["NPR"] and "headline" not in ev["NPR"] and ev["NPR"]["withheld"] == ["headline", "url"]
    assert ev["NPR"]["licence"]["class"] == "provider_restricted"
    assert "missingViewpoints" not in d and "missingViewpoints" in d["withheld"]
    assert d["timing"]["position"] == 1 and d["timing"]["isFirstReport"] is True
    # the same question by url, from a key that holds the provider class
    d2 = client.get(f"/v1/stories/{sid}/coverage-comparison",
                    params={"url": "https://www.npr.org/2026/09/01/pm-resigns-vote"},
                    headers=internal).json()["data"]
    ev2 = {e["publisher"]: e for e in next(f for f in d2["reportedElsewhere"]
                                            if f["key"] == "other_outlets")["evidence"]}
    assert set(ev2) == {"BBC", "The Guardian"} and all("url" in e for e in ev2.values())
    monkeypatch.setenv("RWE_PLATFORM_PUBLISH_RATINGS", "1")
    d3 = client.get(f"/v1/stories/{sid}/coverage-comparison", params={"article_id": bbc},
                    headers=dev).json()["data"]
    assert "missingViewpoints" in d3 and "withheld" not in d3
    # refusals: a member that is not in the story, a reader-private row, an unknown story
    r = client.get(f"/v1/stories/{sid}/coverage-comparison",
                   params={"url": "https://www.npr.org/2026/09/01/only-one-reader-saw-this"}, headers=dev)
    assert r.status_code == 404
    assert client.get(f"/v1/stories/{sid}/coverage-comparison", params={"article_id": "ar_0000000000000000dead"},
                      headers=dev).status_code == 404
    assert client.get("/v1/stories/st_nope/coverage-comparison", params={"article_id": bbc},
                      headers=dev).status_code == 404
    monkeypatch.setenv("RWE_COVERAGE_COMPARISON", "0")
    d4 = client.get(f"/v1/stories/{sid}/coverage-comparison", params={"article_id": bbc},
                    headers=dev).json()["data"]
    assert d4["available"] is False and d4["reason"] == "disabled"


def test_tags_vocabulary_and_retrieval(st, client):
    _, _, dev = _key(st)
    d = client.get("/v1/tags", headers=dev).json()
    tags = {t["tag"]: t for t in d["data"]}
    assert tags and all(t["stories"] >= 1 and t["label"] for t in d["data"])
    assert d["meta"]["page"]["total"] == len(d["data"])
    name = next(iter(tags))
    listing = client.get(f"/v1/tags/{name}", headers=dev).json()
    assert listing["meta"]["tag"] == name and listing["meta"]["page"]["total"] >= 1
    s = listing["data"][0]
    assert s["storyId"].startswith("st_") and "coverage" not in s and "distribution" not in s
    assert client.get("/v1/tags/no-such-tag-anywhere", headers=dev).status_code == 404
    filtered = client.get("/v1/tags", params={"q": name[:3], "min_stories": 1}, headers=dev).json()["data"]
    assert any(t["tag"] == name for t in filtered)
    assert client.get("/v1/tags", params={"min_stories": 999}, headers=dev).json()["data"] == []
    _, _, narrow = _key(st, scopes=["articles:read"])
    assert client.get("/v1/tags", headers=narrow).status_code == 403


def test_countries_count_event_geography_only(st, client):
    import ingest
    import location
    _, _, dev = _key(st)
    assert client.get("/v1/countries", headers=dev).json()["data"] == []
    bbc = ingest.canonical_url("https://www.bbc.co.uk/news/articles/abc123")
    private = ingest.canonical_url("https://www.npr.org/2026/09/01/only-one-reader-saw-this")
    st.replace_article_event_locations(bbc, [location.EventLocation(country="GB", source="gdelt-gkg")])
    st.replace_article_event_locations(private, [location.EventLocation(country="FR", source="gdelt-gkg")])
    d = client.get("/v1/countries", headers=dev).json()
    assert d["data"] == [{"country": "GB", "name": "United Kingdom", "articles": 1, "publishers": 1}]
    assert d["meta"]["total"] == 1                                 # the provisional row's country is absent


def test_entities_lookup_and_per_article_entities(st, client):
    import ingest
    _, _, dev = _key(st)
    bbc = ingest.canonical_url("https://www.bbc.co.uk/news/articles/abc123")
    npr = ingest.canonical_url("https://www.npr.org/2026/09/01/pm-resigns-vote")
    private = ingest.canonical_url("https://www.npr.org/2026/09/01/only-one-reader-saw-this")
    for u in (bbc, npr, private):
        st.replace_article_entities(u, {"person": ["keir starmer"], "org": ["labour party"]})
    st.replace_article_entities(bbc, {"span": ["downing street"]}, source="headline-caps")
    d = client.get("/v1/entities", params={"name": "  Keir   STARMER "}, headers=dev).json()
    assert d["meta"]["entity"] == {"name": "keir starmer", "kinds": ["person", "org"]}
    by_pub = {a["publisher"]: a for a in d["data"]}
    assert set(by_pub) == {"BBC", "NPR"} and d["meta"]["page"]["total"] == 2   # never the provisional row
    assert "url" in by_pub["BBC"] and "url" not in by_pub["NPR"]
    assert client.get("/v1/entities", params={"name": "keir starmer", "kind": "span"},
                      headers=dev).json()["data"] == []
    assert client.get("/v1/entities", params={"name": "downing street", "kind": "span"},
                      headers=dev).json()["meta"]["page"]["total"] == 1
    assert client.get("/v1/entities", params={"name": "keir starmer", "kind": "bogus"},
                      headers=dev).status_code == 422
    aid = by_pub["BBC"]["articleId"]
    e = client.get(f"/v1/articles/{aid}/entities", headers=dev).json()["data"]
    assert e["articleId"] == aid and e["entities"] == {"person": ["keir starmer"], "org": ["labour party"]}
    assert e["attribution"] == ["GDELT Project (gdeltproject.org)"]
    e2 = client.get(f"/v1/articles/{aid}/entities", params={"kind": "span"}, headers=dev).json()["data"]
    assert e2["entities"] == {"span": ["downing street"]} and e2["attribution"] == []
    assert client.get("/v1/articles/ar_0000000000000000dead/entities", headers=dev).status_code == 404
    # the plain article lookup still works beside the nested route
    assert client.get(f"/v1/articles/{aid}", headers=dev).json()["data"]["articleId"] == aid


def test_publisher_discovery_filters_host_resolution_and_scoped_listings(st, client):
    _, _, dev = _key(st)
    # The publishers table holds the whole registry (identity.sync_publishers catalogues it), so
    # the filters are asserted as filters, not against a fixed count.
    listing = client.get("/v1/publishers", params={"q": "guardian"}, headers=dev).json()
    names = [p["name"] for p in listing["data"]]
    assert "The Guardian" in names and all("guardian" in n.lower() for n in names)
    assert listing["meta"]["page"]["total"] == len(names)
    assert [p["name"] for p in client.get("/v1/publishers", params={"q": "npr.org"}, headers=dev).json()["data"]] == ["NPR"]
    reg = client.get("/v1/publishers", params={"registered": "true", "limit": 100}, headers=dev).json()
    assert reg["meta"]["page"]["total"] >= 3 and all(p["registered"] for p in reg["data"])
    assert client.get("/v1/publishers", params={"registered": "false"}, headers=dev).json()["data"] == []
    assert client.get("/v1/publishers", params={"country": "zz"}, headers=dev).json()["data"] == []
    gb = client.get("/v1/publishers", params={"country": "gb", "limit": 100}, headers=dev).json()["data"]
    assert {"BBC", "The Guardian"} <= {p["name"] for p in gb} and all(p["country"] == "GB" for p in gb)
    wires = client.get("/v1/publishers", params={"kind": "wire", "limit": 5}, headers=dev).json()["data"]
    assert all(p["kind"] == "wire" for p in wires)
    d = client.get("/v1/publishers/by-host", params={"host": "https://www.bbc.co.uk/news/live/x"}, headers=dev).json()
    assert d["data"]["name"] == "BBC" and d["meta"]["host"] == "bbc.co.uk"
    assert client.get("/v1/publishers/by-host", params={"host": "npr.org"}, headers=dev).json()["data"]["name"] == "NPR"
    r = client.get("/v1/publishers/by-host", params={"host": "nothing-known.example"}, headers=dev)
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    pid = d["data"]["publisherId"]
    arts = client.get(f"/v1/publishers/{pid}/articles", headers=dev).json()
    assert [a["publisher"] for a in arts["data"]] == ["BBC"] and arts["meta"]["page"]["total"] == 1
    stories = client.get(f"/v1/publishers/{pid}/stories", headers=dev).json()
    assert stories["meta"]["page"]["total"] == 1 and "BBC" in stories["data"][0]["publishers"]
    assert client.get("/v1/publishers/pub_nope/articles", headers=dev).status_code == 404
    assert client.get("/v1/publishers/pub_nope/stories", headers=dev).status_code == 404
    _, _, pubs_only = _key(st, scopes=["publishers:read"])
    assert client.get(f"/v1/publishers/{pid}", headers=pubs_only).status_code == 200
    assert client.get(f"/v1/publishers/{pid}/articles", headers=pubs_only).status_code == 403
    assert client.get(f"/v1/publishers/{pid}/stories", headers=pubs_only).status_code == 403


def test_outlet_search_reads_the_index_and_never_the_paid_upstream(st, client, tmp_path, monkeypatch):
    _seed_outlet_index(tmp_path, monkeypatch)
    monkeypatch.setenv("RWE_SERPAPI_API_KEY", "must-not-be-used")
    _, _, dev = _key(st)
    d = client.get("/v1/outlets/search", params={"q": "local news websites in Kenya", "count": 2},
                   headers=dev).json()
    assert d["meta"]["query"] == {"country": "KE"} and d["meta"]["total"] == 2
    top = d["data"][0]
    assert top["host"] == "ke0.example" and top["url"] == "https://ke0.example/"
    assert top["country"] == "KE" and top["evidence"] == ["wikidata", "wikipedia"]
    assert top["tracked"] is False and "publisherId" not in top
    bbc = client.get("/v1/outlets/search", params={"q": "BBC"}, headers=dev).json()["data"]
    assert bbc and bbc[0]["host"] == "bbc.co.uk" and bbc[0]["tracked"] is True
    assert bbc[0]["publisherId"] == identity.publisher_id_for("bbc.co.uk")
    assert client.get("/v1/outlets/search", params={"q": "zzz-nothing-matches"}, headers=dev).json()["data"] == []
    assert client.get("/v1/outlets/search", headers=dev).status_code == 422
    monkeypatch.setenv("RWE_OUTLET_INDEX_DB", str(tmp_path / "no-such-dir" / "idx.db"))
    r = client.get("/v1/outlets/search", params={"q": "BBC"}, headers=dev)
    assert r.status_code == 503 and r.json()["error"]["code"] == "search_unavailable"
    _, _, narrow = _key(st, scopes=["articles:read"])
    monkeypatch.setenv("RWE_OUTLET_INDEX_DB", str(tmp_path / "v1-idx.db"))
    assert client.get("/v1/outlets/search", params={"q": "BBC"}, headers=narrow).status_code == 403


def test_every_authenticated_route_is_metered_under_its_own_path(st, client):
    _, _, ent = _key(st, plan="enterprise")
    sid = client.get("/v1/stories", headers=ent).json()["data"][0]["storyId"]
    for path in ("/v1/me", "/v1/tags", "/v1/countries", f"/v1/stories/{sid}/intelligence",
                 "/v1/publishers", "/v1/usage"):
        assert client.get(path, headers=ent).status_code == 200, path
    endpoints = {d["endpoint"] for d in st.platform_usage("acme", since_day=f"{metering.month_of()}-01")}
    assert {"/v1/stories", "/v1/me", "/v1/tags", "/v1/countries", "/v1/stories/{story_id}/intelligence",
            "/v1/publishers", "/v1/usage"} <= endpoints


def test_engine_limiter_exempts_keyed_v1_requests_and_throttles_keyless_ones(monkeypatch):
    os.environ.setdefault("RWE_N_USERS", "150")
    os.environ.setdefault("RWE_MAX_ITEMS", "400")
    os.environ.setdefault("RWE_SEED", "0")
    monkeypatch.setenv("RWE_DB_URL", "sqlite://")
    monkeypatch.setenv("RWE_RATELIMIT_ENABLED", "1")
    monkeypatch.setenv("RWE_RATELIMIT_AUTH_PER_MIN", "2")
    monkeypatch.setenv("RWE_RATELIMIT_READ_PER_MIN", "2")
    import importlib
    api = importlib.import_module("api_fastapi")
    platform_app.mount(api.app, api._require_store, get_request_id=api._request_id.get)
    with TestClient(api.app) as c:
        st = api.state.store
        st.platform_create_tenant("t2", "T2")
        secret, _ = st.platform_mint_key(tenant_id="t2", plan="developer")
        for _ in range(6):                                          # far past the per-IP read rate
            r = c.get("/v1/articles", headers={"X-API-Key": secret})
            assert r.status_code == 200
        codes = [c.get("/v1/articles").status_code for _ in range(3)]  # keyless: the auth scope
        assert codes[:2] == [401, 401] and codes[2] == 429
        assert c.get("/v1/health").status_code in (200, 429)


def test_story_counts_are_over_the_coverage_the_platform_serves(st, client):
    """The fixture's story holds a provisional (reader-private) member; every count, the publisher
    list and the coverage list agree on the three members the platform can show."""
    _, _, dev = _key(st)
    listing = client.get("/v1/stories", headers=dev).json()["data"][0]
    d = client.get(f"/v1/stories/{listing['storyId']}", headers=dev).json()["data"]
    assert d["totalCoverage"] == len(d["coverage"]) == 3
    assert d["publisherCount"] == 3 and sorted(d["publishers"]) == ["BBC", "NPR", "The Guardian"]
    assert listing["totalCoverage"] == 3 and listing["publisherCount"] == 3
    assert d["title"].startswith("Prime minister") and "summary" not in d.get("withheld", [])
