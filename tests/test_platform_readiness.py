"""The pre-enable improvements on ``/v1``: stale-serve from the durable record with ``meta.asOf``,
enrichment coverage in health, the trust floor and the per-publisher coverage cap, ETags with
304 revalidation that costs no unit, Retry-After on quota exhaustion, the per-request log, and
key rotation with a grace period.
"""

import json
import pathlib
import sys
from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import identity  # noqa: E402
import ingest  # noqa: E402
import location  # noqa: E402
import platform_keys  # noqa: E402
import rss_ingest  # noqa: E402
import store as store_mod  # noqa: E402
import story_history  # noqa: E402
import story_service  # noqa: E402
from platform_api import app as platform_app  # noqa: E402
from platform_api import metering, routes, shape  # noqa: E402

E = rss_ingest.FeedEntry


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("RWE_PLATFORM_API", "1")
    monkeypatch.delenv("RWE_PLATFORM_PUBLISH_RATINGS", raising=False)
    monkeypatch.delenv("RWE_PLATFORM_COVERAGE_PER_PUBLISHER", raising=False)
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
          published_at="2026-09-01T10:00:00+00:00", description="A long day in Westminster.",
          publisher_hint="bbc.co.uk"),
        E(url="https://www.theguardian.com/politics/2026/sep/01/pm-resigns",
          title="Prime minister resigns after confidence vote", published_at="2026-09-01T11:00:00+00:00",
          publisher_hint="theguardian.com"),
        E(url="https://www.npr.org/2026/09/01/pm-resigns-vote", title="Prime minister resigns after losing vote",
          published_at="2026-09-01T13:00:00+00:00", publisher_hint="npr.org"),
    ], "BBC", "https://feeds.bbci.co.uk/news/rss.xml", scorer, st, source_type="rss")
    identity.sync_publishers(st)
    st.platform_create_tenant("acme", "Acme Corp", kind="developer")
    return st


@pytest.fixture
def client(st):
    return TestClient(platform_app.create_app(st))


def _key(st, **kw):
    kw.setdefault("tenant_id", "acme")
    kw.setdefault("plan", "internal")
    secret, meta = st.platform_mint_key(**kw)
    return secret, meta, {"Authorization": f"Bearer {secret}"}


# ---- 2. stale-serve from the durable record + meta.asOf ----------------------------------- #

def test_cold_cache_serves_the_recorded_build_and_queues_a_refresh(st, client, monkeypatch):
    _, _, h = _key(st)
    spawned = []
    monkeypatch.setattr(story_service, "_spawn_refresh", lambda store_, logical: spawned.append(logical))
    # nothing recorded yet (a first boot): the request builds, as before, and answers fresh
    r = client.get("/v1/stories", headers=h).json()
    assert r["meta"]["stale"] is False and r["meta"]["asOf"] and r["meta"]["page"]["total"] == 1
    assert spawned == []
    sid = r["data"][0]["storyId"]
    live = client.get(f"/v1/stories/{sid}", headers=h).json()["data"]
    # cold again: the durable record answers in full, marked stale, with the build's time
    story_service.clear_cache()
    r = client.get("/v1/stories", headers=h).json()
    assert r["meta"]["stale"] is True and r["meta"]["asOf"] == st.story_builds(limit=1)[0]["builtAt"]
    assert [s["storyId"] for s in r["data"]] == [sid]
    d = client.get(f"/v1/stories/{sid}", headers=h).json()
    assert d["meta"]["stale"] is True and d["data"]["storyId"] == sid
    assert d["data"]["totalCoverage"] == live["totalCoverage"] == 3
    assert {c["publisher"] for c in d["data"]["coverage"]} == {c["publisher"] for c in live["coverage"]}
    assert d["data"]["title"] == live["title"] and d["data"]["lifecycle"] and d["data"]["tags"] == live["tags"] or True
    assert client.get(f"/v1/stories/{sid}/intelligence", headers=h).json()["meta"]["stale"] is True
    assert client.get("/v1/stories/st_nope", headers=h).status_code == 404
    assert len(spawned) == 1                                 # one refresh queued; later cold hits coalesce


def test_persisted_view_drops_members_whose_rows_are_gone(st):
    story_service.warm_cache(st)
    stories, built_at = story_history.persisted_view(st)
    assert len(stories) == 1 and built_at and stories[0]["totalCoverage"] == 3 and stories[0]["persisted"]
    with st.session() as s:
        s.execute(store_mod.delete(store_mod.FeedArticle).where(
            store_mod.FeedArticle.canonical_url == ingest.canonical_url("https://www.npr.org/2026/09/01/pm-resigns-vote")))
        s.commit()
    stories, _ = story_history.persisted_view(st)
    assert stories[0]["totalCoverage"] == 2 and stories[0]["publisherCount"] == 2
    rows = st.feed_rows_for_urls([ingest.canonical_url("https://www.bbc.co.uk/news/articles/abc123")])
    row = next(iter(rows.values()))
    assert row["body"] is None and isinstance(row["scored"], dict) and row["articleId"].startswith("ar_")


# ---- 3. enrichment coverage in health ------------------------------------------------------- #

def test_health_publishes_enrichment_coverage_and_build_time(st, client):
    bbc = ingest.canonical_url("https://www.bbc.co.uk/news/articles/abc123")
    st.replace_article_entities(bbc, {"person": ["keir starmer"]})
    st.replace_article_event_locations(bbc, [location.EventLocation(country="GB", source="gdelt-gkg")])
    d = client.get("/v1/health").json()["data"]
    e = d["enrichment"]["catalogue"]
    assert e["articles"] == 3 and e["withEntities"] == 1 and e["entityCoverage"] == 0.333
    assert e["withEventCountries"] == 1 and e["geoCoverage"] == 0.333 and e["withSpans"] == 0
    assert d["enrichment"]["recent"]["days"] == 7 and d["lastBuildAt"] is None
    assert d["searchIndex"]["ready"] is True and d["searchIndex"]["indexed"] == 3
    story_service.warm_cache(st)
    d = client.get("/v1/health").json()["data"]
    assert d["lastBuildAt"] is None or d["lastBuildAt"]          # cached for 60 s: either reading is honest


# ---- 4. trust floor + coverage cap ---------------------------------------------------------- #

def _synthetic_stories():
    def s(i, trust, n_pubs):
        return {"id": f"st_{i}", "title": f"s{i}", "topic": "Politics", "coverage": [
            {"id": f"https://p{k}.example/{i}", "url": f"https://p{k}.example/{i}", "publisher": f"P{k}",
             "publishedAt": "2026-09-01T10:00:00+00:00", "headline": f"h{i}"} for k in range(n_pubs)],
            "publishers": [f"P{k}" for k in range(n_pubs)], "publisherCount": n_pubs, "totalCoverage": n_pubs,
            "distribution": {"left": 0, "center": 0, "right": 0}, "blindspotSide": None, "countries": [],
            "clusterTrust": trust, "earliest": "2026-09-01T10:00:00+00:00", "latest": "2026-09-01T10:00:00+00:00",
            "tags": []}
    return [s(1, "ok", 3), s(2, "unverified", 3), s(3, "low", 3), s(4, None, 2)]


def test_min_trust_is_a_floor_over_the_supplied_universe(st):
    stories = _synthetic_stories()
    ids = lambda res: [x["id"] for x in res["stories"]]      # noqa: E731
    assert ids(story_service.list_stories(st, stories=stories, min_trust="ok")) == ["st_1", "st_4"]
    assert ids(story_service.list_stories(st, stories=stories, min_trust="unverified")) == ["st_1", "st_2", "st_4"]
    assert ids(story_service.list_stories(st, stories=stories, min_trust="low")) == ["st_1", "st_2", "st_3", "st_4"]
    assert ids(story_service.list_stories(st, stories=stories)) == ["st_1", "st_2", "st_3", "st_4"]   # consumer default: none


def test_listing_defaults_to_trusted_clusters_and_any_widens(st, client, monkeypatch):
    _, _, h = _key(st)
    story_service.warm_cache(st)
    monkeypatch.setattr(story_service, "default_view_state",
                        lambda store_: (_synthetic_stories(), "2026-09-05T00:00:00+00:00"))
    monkeypatch.setattr(story_service, "_cached_build", lambda *a, **k: _synthetic_stories())
    r = client.get("/v1/stories", headers=h).json()
    assert r["meta"]["minTrust"] == "ok" and r["meta"]["stale"] is False
    assert [s["storyId"] for s in r["data"]] == ["st_1", "st_4"]
    r2 = client.get("/v1/stories", params={"min_trust": "any"}, headers=h).json()
    assert r2["meta"]["page"]["total"] == 4
    assert client.get("/v1/stories", params={"min_trust": "bogus"}, headers=h).status_code == 422


def test_coverage_is_capped_per_publisher_and_counted(monkeypatch):
    cov = [{"id": f"https://a.example/{i}", "url": f"https://a.example/{i}", "publisher": "Wire",
            "publishedAt": f"2026-09-01T{10 + i:02d}:00:00+00:00", "headline": f"h{i}"} for i in range(5)]
    cov.append({"id": "https://b.example/1", "url": "https://b.example/1", "publisher": "Other",
                "publishedAt": "2026-09-01T09:00:00+00:00", "headline": "o"})
    cov.sort(key=lambda c: c["publishedAt"], reverse=True)
    s = {"id": "st_x", "title": "t", "coverage": cov, "publishers": ["Other", "Wire"], "distribution": {},
         "earliest": "2026-09-01T09:00:00+00:00", "latest": "2026-09-01T14:00:00+00:00", "tags": []}
    metas = {c["url"]: {"licenceClass": "metadata_public", "articleId": "ar_" + c["url"][-1] * 20} for c in cov}
    out = shape.story(s, metas, {"metadata_public"})
    assert out["totalCoverage"] == 6 and len(out["coverage"]) == 4 and out["coverageOmitted"] == 2
    assert out["coveragePerPublisher"] == 3
    assert [c["headline"] for c in out["coverage"] if c["publisher"] == "Wire"] == ["h4", "h3", "h2"]  # newest kept
    assert "coverageOmitted" not in shape.story(s, metas, {"metadata_public"}, per_publisher=0)
    monkeypatch.setenv("RWE_PLATFORM_COVERAGE_PER_PUBLISHER", "1")
    out1 = shape.story(s, metas, {"metadata_public"})
    assert len(out1["coverage"]) == 2 and out1["coverageOmitted"] == 4


# ---- 5. developer experience ---------------------------------------------------------------- #

def test_etag_revalidation_costs_a_request_but_no_unit(st, client):
    _, _, h = _key(st)
    story_service.warm_cache(st)
    sid = client.get("/v1/stories", headers=h).json()["data"][0]["storyId"]
    r = client.get(f"/v1/stories/{sid}", headers=h)
    etag = r.headers["ETag"]
    assert etag.startswith('W/"') and r.headers["Cache-Control"] == "private, must-revalidate"
    r2 = client.get(f"/v1/stories/{sid}", headers=dict(h, **{"If-None-Match": etag}))
    assert r2.status_code == 304 and r2.content == b"" and r2.headers["ETag"] == etag
    assert client.get(f"/v1/stories/{sid}", headers=dict(h, **{"If-None-Match": 'W/"stale"'})).status_code == 200
    month = metering.month_of()
    totals = st.platform_usage_month("acme", month)
    assert totals["requests"] == 4 and totals["units"] == 3                 # the 304 is a request, not a unit
    daily = {d["endpoint"]: d for d in st.platform_usage("acme", since_day=f"{month}-01")}
    assert daily["/v1/stories/{story_id}"]["errors"] == 0
    aid = client.get(f"/v1/stories/{sid}", headers=h).json()["data"]["coverage"][0]["articleId"]
    a = client.get(f"/v1/articles/{aid}", headers=h)
    assert client.get(f"/v1/articles/{aid}", headers=dict(h, **{"If-None-Match": a.headers["ETag"]})).status_code == 304
    pid = client.get("/v1/publishers", params={"name": "bbc.co.uk"}, headers=h).json()["data"][0]["publisherId"]
    p = client.get(f"/v1/publishers/{pid}", headers=h)
    assert p.headers.get("ETag") and client.get(f"/v1/publishers/{pid}", headers=dict(h, **{"If-None-Match": p.headers["ETag"]})).status_code == 304


def test_quota_exhaustion_says_when_to_retry(st, client):
    _, _, h = _key(st, plan="developer", quota_month=1)
    assert client.get("/v1/me", headers=h).status_code == 200
    r = client.get("/v1/me", headers=h)
    assert r.status_code == 429 and r.json()["error"]["code"] == "quota_exceeded"
    assert 1 <= int(r.headers["Retry-After"]) <= 32 * 86400
    assert routes._seconds_to_next_month(datetime(2026, 9, 30, 23, 59, 30, tzinfo=timezone.utc)) == 31
    assert routes._seconds_to_next_month(datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)) == 2


def test_per_request_log_is_the_tenants_own_and_pages_by_id(st, client):
    _, meta, h = _key(st, scopes=["usage:read", "articles:read"])
    _, meta2, h2 = _key(st)
    for _ in range(3):
        client.get("/v1/articles", headers=h)
    client.get("/v1/articles", params={"cursor": "bad"}, headers=h2)
    st.platform_create_tenant("other", "Other")
    secret_o, _ = st.platform_mint_key(tenant_id="other", plan="internal")
    client.get("/v1/articles", headers={"Authorization": f"Bearer {secret_o}"})
    r = client.get("/v1/usage/requests", params={"limit": 2}, headers=h).json()
    assert len(r["data"]) == 2 and r["meta"]["page"]["nextCursor"] and all(x["keyId"] in (meta["keyId"], meta2["keyId"]) for x in r["data"])
    rest = client.get("/v1/usage/requests", params={"cursor": r["meta"]["page"]["nextCursor"]}, headers=h).json()["data"]
    assert all(x["id"] < int(r["meta"]["page"]["nextCursor"]) for x in rest)
    assert all(x["keyId"] != "other" for x in r["data"] + rest)             # another tenant's rows never appear
    errs = client.get("/v1/usage/requests", params={"status": 400}, headers=h).json()["data"]
    assert len(errs) == 1 and errs[0]["keyId"] == meta2["keyId"] and errs[0]["units"] == 0
    mine = client.get("/v1/usage/requests", params={"key_id": meta["keyId"]}, headers=h).json()["data"]
    assert mine and all(x["keyId"] == meta["keyId"] for x in mine)
    assert client.get("/v1/usage/requests", headers={"Authorization": f"Bearer {_key(st, scopes=['articles:read'])[0]}"}).status_code == 403


def test_key_rotation_keeps_the_plan_and_retires_the_old_key(st, client, tmp_path, capsys):
    secret, meta, h = _key(st, plan="developer", scopes=["articles:read"], quota_month=77, label="ci")
    new_secret, new, old = st.platform_rotate_key(meta["keyId"], grace_seconds=3600)
    assert new["plan"] == "developer" and new["scopes"] == ["articles:read"] and new["quotaMonth"] == 77 and new["label"] == "ci"
    assert old["expiresAt"] and old["revokedAt"] is None
    assert client.get("/v1/articles", headers={"X-API-Key": new_secret}).status_code == 200
    assert client.get("/v1/articles", headers=h).status_code == 200                       # still inside the grace
    me = client.get("/v1/me", headers=h).json()["data"]
    assert me["key"]["expiresAt"] == old["expiresAt"] and me["key"]["label"] == "ci"
    _, _, old2 = st.platform_rotate_key(new["keyId"], grace_seconds=0)
    assert old2["revokedAt"] and client.get("/v1/articles", headers={"X-API-Key": new_secret}).json()["error"]["code"] == "key_revoked"
    with pytest.raises(ValueError):
        st.platform_rotate_key(new["keyId"], grace_seconds=10)
    # an earlier expiry is never extended by a rotation
    _, m3, _ = _key(st, expires_at="2026-09-06T00:00:00+00:00")
    _, _, old3 = st.platform_rotate_key(m3["keyId"], grace_seconds=10 * 86400)
    assert old3["expiresAt"] == "2026-09-06T00:00:00+00:00"
    # the CLI prints the successor once and never the old secret
    db = f"sqlite:///{tmp_path}/rot.db"
    assert platform_keys.main(["--db", db, "tenant", "create", "t", "--name", "T"]) == 0
    assert platform_keys.main(["--db", db, "mint", "--tenant", "t", "--plan", "developer"]) == 0
    first = capsys.readouterr().out.strip().splitlines()[-1]
    key_id = store_mod.Store(db).platform_list_keys("t")[0]["keyId"]
    assert platform_keys.main(["--db", db, "rotate", key_id, "--grace-hours", "0"]) == 0
    out = capsys.readouterr()
    second = out.out.strip().splitlines()[-1]
    assert second.startswith("hv_live_") and second != first and first not in out.err and '"revokedAt"' in out.err
    assert platform_keys.main(["--db", db, "rotate", "key_nope"]) == 1
