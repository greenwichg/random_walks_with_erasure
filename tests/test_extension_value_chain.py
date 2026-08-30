"""Commit 18 — the complete value chain of an extension-discovered article:

    Reader A (extension) reads it  →  provisional FeedArticle  →  one normal refresh cycle
      →  corpus/graph node  →  Reader B receives it as a recommendation.

Runs the real engine in feed mode (in-process TestClient over a prepopulated catalog file DB) and
drives the real ``RefreshManager`` cycle — no recommender internals are touched or mocked.
"""

import datetime as _dt
import importlib.util
import os
import pathlib
import sys

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import store as store_mod  # noqa: E402

# The article Reader A discovers through the extension. A real outlet domain, so the scorer can
# resolve the publisher lean and the corpus builder keeps it (an unknown domain is the documented
# out-of-scope gap: catalog/Stories/Search yes, graph no).
NEW = "https://www.wsj.com/articles/value-chain-fusion-milestone"

OUTLETS = [("The Guardian", -1.5), ("NPR", -1.0), ("Associated Press", 0.0), ("Fox News", 1.6)]


def _seeded_at() -> str:
    """A day old, computed rather than pinned.

    Seeded articles have to clear the recommendation-candidate freshness window
    (``RWE_FEED_MAX_AGE_DAYS``, default 60 days) or ``feed_source`` exports an empty corpus and the
    engine is handed nothing to recommend. The literal that used to sit here — 2026-07-01 — aged out
    of that window on 2026-08-30 and took this test with it. A fixed date inside a rolling window is
    a fuse, not a fixture.
    """
    return (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=1)).isoformat()


def _seed_catalog(db_url, per_outlet=3):
    st = store_mod.Store(db_url)
    for pub, lean in OUTLETS:
        dom = pub.lower().replace(" ", "")
        for k in range(per_outlet):
            u = f"https://{dom}.example.com/story/{k}"
            st.upsert_feed_article(
                canonical_url=u, url=u, publisher=pub, source_publisher=pub,
                title=f"{pub} covers the vote and the economy, item {k}", description="d",
                body=None, published_at=_seeded_at(), source_feed="seed",
                source_type="rss",
                scored={"article_id": u, "outlet": pub, "category": "Politics", "lean": lean,
                        "political": True, "title": f"{pub} story {k}"})
    return st.count_feed_articles()


def test_reader_b_receives_reader_a_discovery(tmp_path, monkeypatch):
    db = f"sqlite:///{tmp_path}/value_chain.db"
    assert _seed_catalog(db) == 12

    monkeypatch.setenv("RWE_DB_URL", db)
    monkeypatch.setenv("RWE_RECS_SOURCE", "feed")
    monkeypatch.setenv("RWE_FEED_MIN_ARTICLES", "5")
    monkeypatch.setenv("RWE_CORPUS_MIN_ARTICLES", "5")
    monkeypatch.setenv("RWE_N_USERS", "80")
    monkeypatch.setenv("RWE_MAX_ITEMS", "200")
    monkeypatch.delenv("RWE_INTERNAL_SECRET", raising=False)

    spec = importlib.util.spec_from_file_location("api_fastapi_vc", ROOT / "examples" / "api_fastapi.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules["api_fastapi_vc"] = m
    spec.loader.exec_module(m)

    with TestClient(m.app) as client:
        def user(acct):
            uid = client.post("/api/internal/users",
                              json={"provider": "google", "providerAccountId": acct}).json()["userId"]
            return uid, {"X-IH-User-Id": str(uid)}

        arts = client.get("/api/discover?limit=200").json()["articles"]
        assert len(arts) == 12

        # Reader A: measured (5 catalog reads), then DISCOVERS the new article via the extension.
        _a, ha = user("value-chain-a")
        client.post("/api/me/reads",
                    json={"reads": [{"url": x["url"], "title": x["headline"]} for x in arts[:5]]},
                    headers=ha)
        r = client.post("/api/me/reads", json={"reads": [{
            "url": NEW, "title": "Fusion milestone reached, lab says", "readSource": "extension",
            "description": "A fusion research milestone.", "siteName": "The Wall Street Journal",
            "publishedAt": "2026-07-10T09:00:00+00:00"}]}, headers=ha).json()
        assert r["accepted"] == 1
        canon = m.ingest.canonical_url(NEW)
        assert m.state.store.get_feed_article(canon)["articleState"] == "provisional"

        # One NORMAL refresh cycle (what the poller runs) — the graph learns the article.
        gen0 = m._active().generation
        m.state.refresh.on_poll_cycle({})
        active = m._active()
        assert active.generation == gen0 + 1
        assert any("value-chain-fusion" in (u or "") for u in active.backend.url_by_id.values())

        # Reader B: measured too (left-leaning catalog diet), has NEVER seen the new article.
        _b, hb = user("value-chain-b")
        left = [x for x in arts if x.get("lean", 0) < 0][:5]
        client.post("/api/me/reads",
                    json={"reads": [{"url": x["url"], "title": x["headline"]} for x in left]},
                    headers=hb)

        # …and RECEIVES Reader A's discovery in their recommendations (union over the strategy
        # family — the blend samples per strategy; membership anywhere proves recommendability).
        got = set()
        for strategy in ("", "?strategy=rwe-b", "?strategy=rwe-d", "?strategy=adaptive"):
            recs = client.get(f"/api/recommendations{strategy}", headers=hb).json()
            got |= {(rec.get("article") or {}).get("url") or "" for rec in recs}
        assert any("value-chain-fusion" in u for u in got), sorted(got)

        # Reader A read it, so THEIR feed excludes it (seen-exclusion intact).
        got_a = {(rec.get("article") or {}).get("url") or ""
                 for rec in client.get("/api/recommendations", headers=ha).json()}
        assert not any("value-chain-fusion" in u for u in got_a)
