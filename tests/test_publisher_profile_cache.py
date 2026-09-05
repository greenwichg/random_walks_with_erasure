"""The publisher profile's counted core is cached (2026-09-05, third production run).

`/v1/publishers/{id}` answered in 1.5–5.7 s for a 6,460-article outlet on a 150k-row catalogue:
every request ran the publisher's whole-catalogue scan, the catalogue's topic group-by, and (for
the platform, which asks for no recent list) a LIMIT 0 search that still counted. The core —
catalogue stats + topic gaps — is now counted once per store, name and TTL, single-flighted;
the catalogue topic counts are shared by every publisher; the platform stamps `meta.asOf` with
when the core was counted; and an expression index serves the `lower(publisher) = ?` filter
every surface writes. Registry facts, enrichment, the logo and the consumer's recent list stay
per request; co-coverage is read fresh from the story view so a cold view is never remembered.
"""

import pathlib
import sys
import threading
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import identity  # noqa: E402
import publisher_service as ps  # noqa: E402
import store as store_mod  # noqa: E402
import story_service  # noqa: E402
from platform_api import app as platform_app  # noqa: E402
from platform_api import metering  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setenv("RWE_PLATFORM_API", "1")
    monkeypatch.setenv("RWE_STORIES_SCAN_DAYS", "36500")
    monkeypatch.delenv("RWE_PUBLISHER_PROFILE_TTL", raising=False)
    ps.clear_cache()
    story_service.clear_cache()
    metering.reset()
    yield
    ps.clear_cache()
    story_service.clear_cache()


def _add(st, cu, pub, *, title="Headline for the piece", category="politics", when="2026-07-01T00:00:00+00:00"):
    st.upsert_feed_article(canonical_url=cu, url=cu, publisher=pub, source_publisher=pub, title=title,
                           description="context", body=None, published_at=when, source_feed="feed://x",
                           scored={"article_id": cu, "outlet": pub, "category": category, "lean": -1.0,
                                   "title": title}, language="en", source_type="rss")


def _catalog(st, n_npr=6):
    for i in range(n_npr):
        _add(st, f"https://npr.org/a{i}", "NPR", category=("politics" if i % 2 else "business"),
             when=f"2026-07-0{i + 1}T00:00:00+00:00")
    _add(st, "https://guardian.example/g1", "The Guardian")
    return st


def _counting(monkeypatch, st):
    """Count the three whole-catalogue calls behind a profile."""
    calls = {"stats": 0, "topics": 0, "search": 0}
    real_stats, real_topics, real_search = (st.publisher_catalog_stats, st.catalog_topic_counts,
                                            st.search_feed_articles)

    def stats(*a, **kw):
        calls["stats"] += 1
        return real_stats(*a, **kw)

    def topics(*a, **kw):
        calls["topics"] += 1
        return real_topics(*a, **kw)

    def search(*a, **kw):
        calls["search"] += 1
        return real_search(*a, **kw)
    monkeypatch.setattr(st, "publisher_catalog_stats", stats)
    monkeypatch.setattr(st, "catalog_topic_counts", topics)
    monkeypatch.setattr(st, "search_feed_articles", search)
    return calls


def test_the_counted_core_is_counted_once_per_ttl(monkeypatch):
    st = _catalog(store_mod.Store("sqlite://"))
    calls = _counting(monkeypatch, st)
    first = ps.get_publisher(st, "NPR")
    assert first["articles"]["total"] == 6 and first["countedAt"]
    assert calls["stats"] == 1 and calls["topics"] == 1
    again = ps.get_publisher(st, "NPR")
    assert calls["stats"] == 1 and calls["topics"] == 1, "a second profile within the TTL counts nothing"
    assert again["countedAt"] == first["countedAt"] and again["topics"] == first["topics"]
    assert again["recent"] == first["recent"] and len(again["recent"]) == 6, "the recent list is still served"


def test_catalogue_topic_counts_are_shared_across_publishers(monkeypatch):
    st = _catalog(store_mod.Store("sqlite://"))
    calls = _counting(monkeypatch, st)
    ps.get_publisher(st, "NPR")
    ps.get_publisher(st, "The Guardian")
    assert calls["stats"] == 2, "each publisher's own rows are counted once"
    assert calls["topics"] == 1, "the catalogue's topic group-by is one scan for every publisher"


def test_the_platform_skips_the_recent_query_and_stamps_when_it_counted(monkeypatch):
    st = _catalog(store_mod.Store("sqlite://"))
    identity.sync_publishers(st)
    st.platform_create_tenant("t", "T", kind="internal")
    secret, _ = st.platform_mint_key(tenant_id="t", plan="internal")
    h = {"Authorization": f"Bearer {secret}"}
    story_service.warm_cache(st)          # co-coverage reads a warm view; a cold one would spawn a build (which fetches)
    calls = _counting(monkeypatch, st)
    c = TestClient(platform_app.create_app(st))
    pid = c.get("/v1/publishers", params={"name": "npr.org"}, headers=h).json()["data"][0]["publisherId"]
    calls.update(stats=0, topics=0, search=0)                   # the profile route alone from here
    r = c.get(f"/v1/publishers/{pid}", headers=h).json()
    assert {t["label"] for t in r["data"]["topics"]} == {"Politics", "Business"}, "the counted core is on the wire"
    assert calls["search"] == 0, "recent_limit=0 must not run the publisher's search (a LIMIT 0 still counted)"
    counted_at = ps.get_publisher(st, "NPR", recent_limit=0)["countedAt"]
    assert r["meta"]["asOf"] == counted_at, "meta.asOf is when the counted core was counted"
    assert "countedAt" not in r["data"]
    c.get(f"/v1/publishers/{pid}", headers=h)
    assert calls["stats"] == 1 and calls["topics"] == 1


def test_ttl_zero_counts_every_time_and_expiry_recounts(monkeypatch):
    st = _catalog(store_mod.Store("sqlite://"))
    calls = _counting(monkeypatch, st)
    monkeypatch.setenv("RWE_PUBLISHER_PROFILE_TTL", "0")
    ps.get_publisher(st, "NPR")
    ps.get_publisher(st, "NPR")
    assert calls["stats"] == 2
    monkeypatch.setenv("RWE_PUBLISHER_PROFILE_TTL", "0.05")
    ps.get_publisher(st, "NPR")
    assert calls["stats"] == 3
    ps.get_publisher(st, "NPR")
    assert calls["stats"] == 3, "inside the TTL: served"
    time.sleep(0.08)
    _add(st, "https://npr.org/late", "NPR")
    assert ps.get_publisher(st, "NPR")["articles"]["total"] == 7 and calls["stats"] == 4, "after the TTL: recounted"


def test_concurrent_misses_count_once(monkeypatch, tmp_path):
    st = _catalog(store_mod.Store(f"sqlite:///{tmp_path}/c.db"))   # a connection per thread, as in production
    calls = _counting(monkeypatch, st)
    real = st.publisher_catalog_stats

    def slow(*a, **kw):
        time.sleep(0.2)
        return real(*a, **kw)
    monkeypatch.setattr(st, "publisher_catalog_stats", slow)
    out = []
    threads = [threading.Thread(target=lambda: out.append(ps.get_publisher(st, "NPR")["countedAt"]))
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(out)) == 1, "four concurrent misses share one count"
    assert calls["stats"] == 1


def test_co_coverage_is_read_fresh_not_remembered_from_a_cold_view(monkeypatch):
    st = store_mod.Store("sqlite://")
    for ev, title, pubs in [(1, "Dockworkers strike closes the main port", ["Alpha Post", "Beta Times"]),
                            (2, "Wildfires spread across the western coast", ["Alpha Post", "Beta Times", "Gamma Herald"]),
                            (3, "Senate passes the funding bill after debate", ["Alpha Post", "Gamma Herald"])]:
        for pub in pubs:
            _add(st, f"https://{pub.lower().replace(' ', '')}.example/ev{ev}", pub, title=title,
                 when="2026-07-05T10:00:00+00:00")
    monkeypatch.setattr(story_service, "_spawn_refresh", lambda store_, logical: None)   # a cold view stays cold
    cold = ps.get_publisher(st, "Alpha Post")
    assert "coCoverage" not in cold
    story_service.warm_cache(st)
    warm = ps.get_publisher(st, "Alpha Post")
    assert warm["coCoverage"]["sharedStories"] == 3, "the warmed view shows through a still-cached core"
    assert warm["countedAt"] == cold["countedAt"]


def test_the_publisher_filter_is_served_by_the_expression_index():
    st = _catalog(store_mod.Store("sqlite://"))
    assert not [e for e in st.index_errors if "publisher_lower" in str(e)]
    with st.session() as s:
        plan = " ".join(str(r) for r in s.execute(text(
            "EXPLAIN QUERY PLAN SELECT canonical_url FROM feed_articles WHERE lower(publisher) = 'npr'")).all())
    assert "ix_feed_publisher_lower" in plan, plan
