"""Tests for Discover & Stories (examples/discover.py) — the product-layer exploration surface over
the FeedArticle catalog. Proves the serializer reuses the engine's Article shape (with the real
publisher URL + publication time), the filters/facets work, and the deterministic clustering groups
one event across publishers without touching the recommender or any protected module."""

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import store       # noqa: E402
import discover    # noqa: E402


def _add(st, cu, publisher, lean, title, *, category="Politics", url=None,
         when="2026-07-05T10:00:00+00:00", description="context"):
    st.upsert_feed_article(
        canonical_url=cu, url=url if url is not None else cu, publisher=publisher,
        source_publisher=publisher, title=title, description=description, body=None,
        published_at=when, source_feed="feed://x",
        scored={"article_id": cu, "outlet": publisher, "category": category, "lean": lean, "title": title})


def _event(st):
    """Two real news events across publishers + two unrelated singletons (noise)."""
    _add(st, "https://npr.org/a1", "NPR", -1.0, "Senate passes the funding bill after long debate")
    _add(st, "https://foxnews.com/a2", "Fox News", 1.5, "Senate passes funding bill, averting shutdown")
    _add(st, "https://bbc.com/a3", "BBC News", 0.0, "US Senate passes funding bill to avert shutdown",
         when="2026-07-05T12:00:00+00:00")
    _add(st, "https://cnn.com/b1", "CNN", -1.2, "Wildfires spread across the western coast", category="Climate")
    _add(st, "https://guardian.com/b2", "The Guardian", -1.5, "Wildfires spread rapidly along western coast",
         category="Climate")
    _add(st, "https://wsj.com/c1", "Wall Street Journal", 0.8, "Markets rally on tech earnings", category="Business")
    _add(st, "https://nypost.com/c2", "New York Post", 1.3, "Local team wins the championship", category="Sports")


# --------------------------------------------------------------------------- #
# Serializer: FeedArticle -> the canonical Article shape.
# --------------------------------------------------------------------------- #
def test_serializer_shape_and_real_url():
    st = store.Store("sqlite://")
    _add(st, "https://foxnews.com/x", "Fox News", 1.6, "Border plan draws scrutiny",
         url="https://www.foxnews.com/politics/border-plan", description="A summary.")
    a = discover.feed_article_to_article(st.list_feed_articles(limit=1)[0])
    assert a["url"] == "https://www.foxnews.com/politics/border-plan"   # the REAL publisher URL
    assert a["id"] == "https://foxnews.com/x"                           # canonical url == id (Read flow)
    assert a["publisher"] == "Fox News" and a["leanBucket"] == "right"
    assert a["description"] == "A summary." and a["topic"] == "Politics"
    # the full Article contract the web app renders
    assert {"headline", "publisherLean", "confidence", "emotion", "dominantEmotion", "register",
            "publishedAt", "readingMinutes"} <= set(a)


def test_serializer_unknown_lean_is_null_never_center():
    """L2.2: a registry-unknown outlet's lean is UNKNOWN — serialised null (lean, bucket, and
    publisherLean alike), never a fabricated Center. Missing key, null, and NaN all mean unknown
    (the store's _json_safe writes NaN as JSON null; the serializer must treat all three alike)."""
    base = {"canonicalUrl": "https://obscure.example/x", "url": "https://obscure.example/x",
            "publisher": "Obscure Tribune", "title": "t", "publishedAt": "2026-07-05T10:00:00+00:00"}
    for scored in ({}, {"lean": None}, {"lean": float("nan")}):
        a = discover.feed_article_to_article({**base, "scored": scored})
        assert a["lean"] is None and a["leanBucket"] is None and a["publisherLean"] is None
    rated = discover.feed_article_to_article({**base, "scored": {"lean": 1.6}})
    assert rated["leanBucket"] == "right" and rated["publisherLean"] == 1.6


def test_absent_signals_serialise_null_never_defaults():
    """L2.2 family: an unenriched article has NO register/emotion/confidence — serialised null,
    never "reporting" / an all-neutral vector / a fabricated 0.7 confidence."""
    a = discover.feed_article_to_article({
        "canonicalUrl": "https://x.example/1", "url": "https://x.example/1",
        "publisher": "P", "title": "t", "publishedAt": "2026-07-05T10:00:00+00:00",
        "scored": {"lean": -1.0}})
    assert a["register"] is None and a["emotion"] is None
    assert a["dominantEmotion"] is None and a["confidence"] is None


def test_numeric_register_buckets_with_engine_thresholds():
    """The enricher stores NUMERIC P(reporting). The old string comparison collapsed every
    numeric to "reporting" — an opinion piece (0.2) was labelled reporting on every feed
    surface. Buckets must match the engine's own thresholds exactly."""
    import api_server as engine_mod
    base = {"canonicalUrl": "https://x.example/2", "url": "https://x.example/2",
            "publisher": "P", "title": "t", "publishedAt": "2026-07-05T10:00:00+00:00"}
    for raw, want in ((0.9, "reporting"), (0.6, "reporting"), (0.5, "mixed"),
                      (0.4, "opinion"), (0.2, "opinion"), ("opinion", "opinion")):
        a = discover.feed_article_to_article({**base, "scored": {"register": raw}})
        assert a["register"] == want, (raw, a["register"])
        if isinstance(raw, float):
            assert a["register"] == engine_mod._register_enum(raw)   # one classification product-wide
    junk = discover.feed_article_to_article({**base, "scored": {"register": "editorial"}})
    assert junk["register"] is None                                  # unknown label: no signal, no guess


def test_unrated_matches_no_lean_bucket_but_stays_in_all():
    """Fail-honest filter: an unrated article appears in the unfiltered feed but matches NO lean
    bucket — display (Unknown) and the SQL lean filter agree (mirrors the country-filter semantics)."""
    st = store.Store("sqlite://")
    _add(st, "https://npr.org/r1", "NPR", -1.0, "Rated outlet headline")
    _add(st, "https://obscure.example/u1", "Obscure Tribune", None, "Unrated outlet headline")
    assert len(discover.list_discover(st, limit=50)["articles"]) == 2
    for bucket in ("left", "center", "right"):
        pubs = {a["publisher"] for a in discover.list_discover(st, lean=bucket)["articles"]}
        assert "Obscure Tribune" not in pubs


def test_serializer_never_emits_a_relative_url():
    """A relative/hostless value is never emitted — it would resolve against the app's own origin
    (the Read-opens-the-app bug). Only an absolute http(s) URL survives."""
    st = store.Store("sqlite://")
    # canonical_url is a valid dedup key but a *relative* url column value
    _add(st, "https://ok.com/1", "NPR", -1.0, "t", url="/news/relative/path")
    a = discover.feed_article_to_article(st.list_feed_articles(limit=1)[0])
    assert a["url"] == ""                                               # relative dropped
    assert a["id"] == "https://ok.com/1"


# --------------------------------------------------------------------------- #
# Discover: latest + filters + facets.
# --------------------------------------------------------------------------- #
def test_discover_facets_and_filters():
    st = store.Store("sqlite://")
    _event(st)
    d = discover.list_discover(st, limit=50)
    assert len(d["articles"]) == 7
    assert d["topics"] == ["Business", "Climate", "Politics", "Sports"]   # sorted facets
    assert "Fox News" in d["publishers"] and "NPR" in d["publishers"]

    left = discover.list_discover(st, lean="left")["articles"]
    assert {a["publisher"] for a in left} == {"NPR", "CNN", "The Guardian"}
    assert discover.list_discover(st, publisher="Fox News")["articles"][0]["publisher"] == "Fox News"
    assert all(a["topic"] == "Climate" for a in discover.list_discover(st, topic="Climate")["articles"])
    # facets stay full even when a filter is applied (dropdowns don't collapse)
    assert discover.list_discover(st, lean="left")["topics"] == ["Business", "Climate", "Politics", "Sports"]


def test_discover_latest_first():
    st = store.Store("sqlite://")
    _add(st, "https://a.com/old", "NPR", 0.0, "Old", when="2026-01-01T00:00:00+00:00")
    _add(st, "https://a.com/new", "CNN", 0.0, "New", when="2026-07-01T00:00:00+00:00")
    arts = discover.list_discover(st)["articles"]
    assert arts[0]["headline"] == "New" and arts[1]["headline"] == "Old"


# --------------------------------------------------------------------------- #
# Stories: deterministic clustering.
# --------------------------------------------------------------------------- #
def test_clustering_groups_events_and_drops_noise():
    st = store.Store("sqlite://")
    _event(st)
    stories = discover.cluster_stories(st, min_publishers=2)
    titles = {s["title"] for s in stories}
    assert len(stories) == 2                                    # the two multi-publisher events only
    assert any("Senate passes funding bill" in t for t in titles)
    assert any("Wildfires spread" in t for t in titles)

    senate = next(s for s in stories if "Senate" in s["title"])
    assert senate["totalCoverage"] == 3 and senate["publisherCount"] == 3
    assert senate["blindspotSide"] is None                     # L+C+R covered
    assert abs(sum(senate["distribution"].values()) - 1.0) < 1e-9
    assert all(c["url"].startswith("http") for c in senate["coverage"])

    wildfires = next(s for s in stories if "Wildfires" in s["title"])
    assert wildfires["distribution"]["left"] == 1.0            # both publishers left
    assert wildfires["blindspotSide"] in {"center", "right"}   # a real, uncovered side


def test_clustering_is_deterministic():
    st = store.Store("sqlite://")
    _event(st)
    a = discover.cluster_stories(st)
    b = discover.cluster_stories(st)
    assert [s["id"] for s in a] == [s["id"] for s in b]
    assert all(s["id"].startswith("st_") for s in a)


def test_single_publisher_cluster_is_not_a_story():
    """Two near-identical articles from the SAME publisher is not a cross-publisher event."""
    st = store.Store("sqlite://")
    _add(st, "https://npr.org/1", "NPR", -1.0, "Senate passes the funding bill today")
    _add(st, "https://npr.org/2", "NPR", -1.0, "Senate passes the funding bill this evening")
    assert discover.cluster_stories(st, min_publishers=2) == []


def test_story_detail_by_id():
    st = store.Store("sqlite://")
    _event(st)
    stories = discover.cluster_stories(st)
    got = discover.story_detail(st, stories[0]["id"])
    assert got is not None and got["id"] == stories[0]["id"]
    assert discover.story_detail(st, "st_does_not_exist") is None


# --------------------------------------------------------------------------- #
# HTTP layer: the endpoints serve the same shapes over the real app.
# --------------------------------------------------------------------------- #
def test_discover_stories_endpoints(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    import importlib.util
    from fastapi.testclient import TestClient

    db = f"sqlite:///{tmp_path/'d.db'}"
    st = store.Store(db)
    _event(st)
    for k, v in {"RWE_DB_URL": db, "RWE_N_USERS": "120", "RWE_MAX_ITEMS": "400", "RWE_SEED": "0"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("RWE_RECS_SOURCE", raising=False)

    spec = importlib.util.spec_from_file_location("api_fastapi_disc", ROOT / "examples" / "api_fastapi.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["api_fastapi_disc"] = mod
    spec.loader.exec_module(mod)

    # One enriched article proves the register ALIAS serialises to the wire key; the untouched
    # fixtures prove an absent signal stays absent (no "reporting" default on the wire).
    st.upsert_feed_article(
        canonical_url="https://npr.org/reg", url="https://npr.org/reg", publisher="NPR",
        source_publisher="NPR", title="Enriched senate analysis piece", description="d", body=None,
        published_at="2026-07-05T13:00:00+00:00", source_feed="feed://x",
        scored={"article_id": "https://npr.org/reg", "outlet": "NPR", "category": "Politics",
                "lean": -1.0, "title": "Enriched senate analysis piece", "register": 0.2})

    with TestClient(mod.app) as c:
        disc = c.get("/api/discover").json()
        assert len(disc["articles"]) == 8 and disc["articles"][0]["url"].startswith("http")
        by_id = {a["id"]: a for a in disc["articles"]}
        assert by_id["https://npr.org/reg"]["register"] == "opinion"   # alias + numeric bucketing
        assert "register" not in by_id["https://bbc.com/a3"]           # absent signal stays absent
        assert len(c.get("/api/discover", params={"lean": "left"}).json()["articles"]) == 4

        # /api/stories is now a paginated envelope from the Story Service (Commit 7).
        body = c.get("/api/stories").json()
        assert body["total"] == 2 and len(body["stories"]) == 2 and body["page"] == 1
        sid = body["stories"][0]["id"]
        detail = c.get(f"/api/story/{sid}").json()                 # new singular route
        assert detail["id"] == sid and all(cv["url"].startswith("http") for cv in detail["coverage"])
        assert c.get(f"/api/stories/{sid}").json()["id"] == sid    # backward-compatible alias
        assert c.get("/api/story/st_bogus").status_code == 404
