"""Tests for the live recommendation source (examples/feed_source.py).

Proves the smallest-seam claim: FeedArticle -> a qbias-format CSV -> the EXISTING corpus builder
(simulate_users.run(qbias=...)) -> the recommender operates over live articles exactly as over the
static qbias catalog. No recommendation algorithm is touched."""

import csv as _csv
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import store          # noqa: E402
import feed_source    # noqa: E402


def _add(st, canonical, url, publisher, lean, *, title="A story about the vote and the economy",
         category="Politics"):
    st.upsert_feed_article(
        canonical_url=canonical, url=url, publisher=publisher, source_publisher=publisher,
        title=title, description="context", body=None, published_at="2024-10-02T00:00:00+00:00",
        source_feed="feed://x",
        scored={"article_id": canonical, "outlet": publisher, "category": category,
                "lean": lean, "political": True, "title": title})


def _seed(st, per_outlet=70):
    """A cross-spectrum FeedArticle catalog: left / center / right, enough to simulate a population."""
    for name, lean in [("The Guardian", -1.5), ("Associated Press", 0.0), ("Fox News", 1.6)]:
        for k in range(per_outlet):
            u = f"https://ex.com/{name.replace(' ', '').lower()}/{k}"
            _add(st, u, u, name, lean, title=f"{name} reports on the vote and the economy, item {k}")


# --------------------------------------------------------------------------- #
# Unit: bias mapping + CSV export + prepare threshold.
# --------------------------------------------------------------------------- #
def test_bias_label_matches_allsides_buckets():
    assert feed_source._bias_label(-1.5) == "left"
    assert feed_source._bias_label(1.6) == "right"
    assert feed_source._bias_label(0.2) == "center"
    assert feed_source._bias_label(float("nan")) == ""       # unknown -> dropped by the builder
    assert feed_source._bias_label(None) == ""


def test_export_catalog_csv_format(tmp_path):
    st = store.Store("sqlite://")
    _add(st, "https://foxnews.com/a", "https://www.foxnews.com/a", "Fox News", 1.6, title="Border plan")
    _add(st, "https://nytimes.com/b", "https://www.nytimes.com/b", "New York Times", -1.4, title="Senate vote")
    path = str(tmp_path / "c.csv")
    assert feed_source.export_catalog_csv(st, path) == 2

    rows = list(_csv.DictReader(open(path, encoding="utf-8")))
    assert set(rows[0].keys()) == {"title", "source", "bias_rating", "tags", "url"}   # qbias-format
    by = {r["source"]: r for r in rows}
    assert by["Fox News"]["bias_rating"] == "right" and by["New York Times"]["bias_rating"] == "left"
    assert by["Fox News"]["url"].startswith("https://www.foxnews.com")  # url carried (builder ignores it)
    assert by["Fox News"]["tags"] == "Politics"


def test_prepare_threshold_and_fallback(tmp_path):
    st = store.Store("sqlite://")
    assert feed_source.prepare(st, str(tmp_path / "x.csv"), min_articles=5) is None   # below -> fallback
    for i in range(6):
        _add(st, f"https://x.com/{i}", f"https://x.com/{i}", "X", 0.0)
    out = feed_source.prepare(st, str(tmp_path / "x.csv"), min_articles=5)
    assert out and os.path.exists(out)                       # at/above -> exported


def test_enabled_flag(monkeypatch):
    monkeypatch.delenv("RWE_RECS_SOURCE", raising=False)
    assert feed_source.enabled() is False
    monkeypatch.setenv("RWE_RECS_SOURCE", "feed")
    assert feed_source.enabled() is True


def test_load_url_map_mirrors_qbias_ids(tmp_path):
    """Q{i} keys by CSV data-row index, matching catalog_from_qbias's enumerate — including the gap
    left by a row the builder drops (empty bias), whose (unused) entry is still present + harmless."""
    p = tmp_path / "c.csv"
    p.write_text("title,source,bias_rating,tags,url\n"
                 "a,Fox News,right,Politics,https://www.foxnews.com/a\n"
                 "b,Somewhere,,Politics,https://x.com/b\n"          # empty bias -> builder skips (no Q1 rec)
                 "c,CNN,left,Politics,https://www.cnn.com/c\n", encoding="utf-8")
    m = feed_source.load_url_map(str(p))
    assert m["Q0"] == "https://www.foxnews.com/a"
    assert m["Q2"] == "https://www.cnn.com/c"                        # row index 2 (0-based), not compacted
    assert m["Q1"] == "https://x.com/b"                             # present but never emitted as a rec id


# --------------------------------------------------------------------------- #
# End-to-end: the recommender, sourced from FeedArticle, behaves as it does over qbias.
# --------------------------------------------------------------------------- #
def test_recommender_sources_from_feed_catalog(tmp_path):
    pytest.importorskip("scipy")                             # simulate_users needs the science stack
    import api_server as engine

    st = store.Store("sqlite://")
    _seed(st, per_outlet=70)                                 # 210 live articles across L/C/R
    csv_path = str(tmp_path / "feed.csv")
    assert feed_source.export_catalog_csv(st, csv_path) == 210

    # Build the WHOLE engine over the FeedArticle-derived catalog via the unchanged qbias machinery.
    profile = engine.DatasetProfile.synthetic(n_users=120, max_items=500, seed=0, qbias_csv=csv_path)
    be = engine.Backend(profile)
    assert len(be.eligible) > 0                              # a viable simulated population was built

    recs = be.recommendations(be.demo_user)
    assert len(recs) > 0
    expected = {engine._prettify(o) for o in ("The Guardian", "Associated Press", "Fox News")}
    publishers = {r["article"]["publisher"] for r in recs}
    assert publishers and publishers <= expected            # recommendations come FROM the feed catalog
    assert all("Outlet" not in p for p in publishers)       # not the synthetic 'Outlet N'
    # sanity: the same report/metric machinery runs over the live catalog
    report = be.report(be.demo_user)
    assert 0 <= report["overall"] <= 100 and len(report["sources"]) > 0


def test_recommendations_carry_verified_url_and_degrade_gracefully(tmp_path):
    """The Honest URL Pass-through: recs from the live feed catalog carry the real publisher URL;
    the id-is-a-URL rule gives history its URL too; and with no resolver, no URL is emitted."""
    pytest.importorskip("scipy")
    import api_server as engine

    st = store.Store("sqlite://")
    _seed(st, per_outlet=70)
    csv_path = str(tmp_path / "feed.csv")
    feed_source.export_catalog_csv(st, csv_path)
    profile = engine.DatasetProfile.synthetic(n_users=120, max_items=500, seed=0, qbias_csv=csv_path)
    be = engine.Backend(profile)
    be.attach_url_resolver(feed_source.load_url_map(csv_path))

    recs = be.recommendations(be.demo_user)
    assert recs
    for r in recs:
        a = r["article"]
        assert a["id"].startswith("Q")                              # qbias-style corpus id
        assert a.get("url", "").startswith("https://ex.com/")       # ...resolved to a verified FeedArticle URL

    # id-is-a-URL rule: a real reader's stored read (history) carries its own canonical URL.
    hist = be.serialize_history([{
        "id": 1, "canonicalUrl": "https://www.foxnews.com/x",
        "scored": {"article_id": "https://www.foxnews.com/x", "outlet": "Fox News", "title": "t", "lean": 1.0},
        "observedAt": None, "createdAt": None}])
    assert hist[0]["article"]["url"] == "https://www.foxnews.com/x"

    # graceful: cleared resolver + a non-URL id -> no url emitted (never fabricated).
    assert be._resolve_url("S144") is None
    be.attach_url_resolver({})
    assert all("url" not in r["article"] for r in be.recommendations(be.demo_user))
