"""Tests for A2-API — ``POST /api/analyze`` over the A1 analyzer service.

Drives the real FastAPI app and pins the phase's guarantees: verbatim ANALYSIS CONTRACT v1
passthrough (byte-level parity with ``article_analyzer.analyze``), meaningful nulls surviving the
wire (no ``exclude_none``), the 200-with-``invalid_url`` contract outcome, the typed 422 envelope,
the zero-write invariant through the HTTP layer, anonymous access (and anonymous == signed-in),
API-level determinism (identical requests -> identical bytes), and the ``write`` rate-limit scope
with a real 429. The analyzer itself is covered by ``test_article_analyzer.py``; nothing here
re-tests scoring semantics beyond what proves the endpoint adds and removes nothing.
"""

import json
import pathlib
import sys
import uuid

import pytest
from sqlalchemy import text

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
from fastapi.testclient import TestClient   # noqa: E402
import api_fastapi                          # noqa: E402
import article_analyzer as aa               # noqa: E402
import ingest                               # noqa: E402
import ratelimit                            # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(api_fastapi.app) as c:   # lifespan builds the backend + store
        yield c


_RUN = uuid.uuid4().hex[:8]   # unique per run: the file DB persists across runs, so fresh URLs
                              # keep catalog-hit / catalog-miss assertions isolated from prior state

CATALOG_URL = f"https://an2test{_RUN}.example.com/politics/{_RUN}"
MISS_URL = f"https://apnews.com/article/an2-{_RUN}"          # registry-known outlet, never ingested
UNKNOWN_URL = f"https://unknown-blog-{_RUN}.example/post"    # outlet the registry doesn't know
METADATA = {"title": f"senate weighs new budget measure {_RUN}",
            "description": "lawmakers debate the plan", "category": "Politics"}


@pytest.fixture(scope="module")
def catalog_url(client):
    """Seed ONE FeedArticle directly on the app's store (the same shape the ingest pipeline
    writes) so the catalog-first path has a guaranteed hit that no other run can perturb."""
    canon = ingest.canonical_url(CATALOG_URL)
    api_fastapi.state.store.upsert_feed_article(
        canonical_url=canon, url=CATALOG_URL, publisher="AP", source_publisher="AP",
        title=f"an2test {_RUN} unique headline tokens", description="d", body=None,
        published_at="2026-07-18T12:00:00+00:00", source_feed="test",
        scored={"article_id": canon, "outlet": "AP", "category": "Politics", "lean": 0.0,
                "political": True, "title": f"an2test {_RUN}",
                "emotion": {"fear": 0.1, "outrage": 0.1, "analysis": 0.2,
                            "positive": 0.1, "neutral": 0.5},
                "register": 0.8})
    return CATALOG_URL


def _table_counts():
    """Row count of EVERY table in the app's store — the zero-write ledger."""
    st = api_fastapi.state.store
    with st.session() as s:
        tables = [r[0] for r in s.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'"))]
        return {t: s.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar() for t in tables}


# --------------------------------------------------------------------------- #
# Contract passthrough — the endpoint adds and removes nothing.
# --------------------------------------------------------------------------- #
def test_catalog_hit_parity_with_service(client, catalog_url):
    r = client.post("/api/analyze", json={"url": catalog_url})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "analyzed" and body["source"] == "catalog"
    assert body["article"]["publisher"] == "AP"
    assert body["scoring"]["lean"] == 0.0 and body["scoring"]["leanBucket"] == "center"
    # byte-level parity: the wire response IS the service output, verbatim
    service = aa.analyze(api_fastapi.state.store, catalog_url)
    assert json.dumps(body, sort_keys=True) == json.dumps(service, sort_keys=True)


def test_scored_url_only_with_metadata(client):
    r = client.post("/api/analyze", json={"url": MISS_URL, "metadata": METADATA})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "scored_url_only" and body["article"] is None
    sc = body["scoring"]
    assert sc["outlet"] == "Associated Press"            # registry resolution
    assert sc["lean"] == 0.0 and sc["leanBucket"] == "center"
    assert sc["emotion"] is not None and sc["register"] is not None   # enricher ran


def test_url_only_without_metadata_degrades_honestly(client):
    r = client.post("/api/analyze", json={"url": MISS_URL})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "scored_url_only"
    assert any("no page metadata" in n for n in body["notes"])
    assert body["scoring"]["lean"] == 0.0                # registry still knows the outlet


def test_meaningful_nulls_survive_the_wire(client):
    """The exclude_none regression guard: pinned-null sections and honest null scoring fields
    must be PRESENT as keys with null values, never dropped."""
    body = client.post("/api/analyze",
                       json={"url": UNKNOWN_URL, "metadata": {"title": "a take"}}).json()
    for key in ("recommendation", "personal", "explanation"):
        assert key in body and body[key] is None
    assert "lean" in body["scoring"] and body["scoring"]["lean"] is None
    assert "leanBucket" in body["scoring"] and body["scoring"]["leanBucket"] is None
    assert any("registry" in n for n in body["notes"])


# --------------------------------------------------------------------------- #
# Error model.
# --------------------------------------------------------------------------- #
def test_invalid_url_is_200_with_contract_status(client):
    r = client.post("/api/analyze", json={"url": "not a url"})
    assert r.status_code == 200                          # a contract outcome, not an HTTP error
    body = r.json()
    assert body["status"] == "invalid_url"
    assert body["input"]["canonicalUrl"] is None
    assert body["source"] is None and body["scoring"] is None and body["story"] is None


def test_missing_url_is_422_typed_envelope(client):
    r = client.post("/api/analyze", json={})
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "invalid_request" and err["requestId"]


# --------------------------------------------------------------------------- #
# Zero-write + determinism through the HTTP layer.
# --------------------------------------------------------------------------- #
def test_zero_write_invariant_through_the_api(client, catalog_url):
    before = _table_counts()
    client.post("/api/analyze", json={"url": catalog_url})                       # catalog hit
    client.post("/api/analyze", json={"url": MISS_URL, "metadata": METADATA})    # miss + metadata
    client.post("/api/analyze", json={"url": UNKNOWN_URL})                       # unknown outlet
    client.post("/api/analyze", json={"url": "not a url"})                       # invalid
    assert _table_counts() == before                     # every table, row-for-row unchanged


def test_identical_requests_get_identical_json(client, catalog_url):
    """API determinism regression: the endpoint must add no timestamps, diagnostics, or
    request-specific fields — identical requests produce byte-identical responses."""
    for payload in ({"url": catalog_url},
                    {"url": MISS_URL, "metadata": METADATA}):
        a = client.post("/api/analyze", json=payload)
        b = client.post("/api/analyze", json=payload)
        assert a.status_code == b.status_code == 200
        assert a.content == b.content                    # bytes, not just parsed equality


def test_anonymous_and_authenticated_identical(client, catalog_url):
    """A2 has no reader-relative behaviour: a signed-in reader gets exactly the anonymous
    analysis (divergence begins in A3-Enrich)."""
    uid = client.post("/api/internal/users",
                      json={"provider": "google",
                            "providerAccountId": f"an2-{_RUN}"}).json()["userId"]
    anon = client.post("/api/analyze", json={"url": catalog_url})
    authed = client.post("/api/analyze", json={"url": catalog_url},
                         headers={"X-IH-User-Id": str(uid)})
    assert anon.status_code == authed.status_code == 200
    assert anon.json() == authed.json()


# --------------------------------------------------------------------------- #
# Rate limiting — classified into the existing `write` scope; a real 429 when exceeded.
# --------------------------------------------------------------------------- #
def test_rate_limit_scope_and_429(client, monkeypatch, catalog_url):
    assert ratelimit.scope_for("POST", "/api/analyze") == "write"   # pins rate + body-limit class
    api_fastapi.state.limiter.reset()
    monkeypatch.setenv("RWE_RATELIMIT_WRITE_PER_MIN", "2")
    ip = {"X-Forwarded-For": "203.0.113.42"}             # fresh anonymous identity -> own bucket
    for _ in range(2):
        assert client.post("/api/analyze", json={"url": catalog_url},
                           headers=ip).status_code == 200
    blocked = client.post("/api/analyze", json={"url": catalog_url}, headers=ip)
    assert blocked.status_code == 429
    err = blocked.json()["error"]
    assert err["code"] == "rate_limited" and err["requestId"]
    assert int(blocked.headers["Retry-After"]) >= 1
