"""Tests for examples/publisher_service.py + GET /api/publishers/{name} — Publisher Intelligence.

Proves the profile is composition, never invention: curated registry facts (or honest absence),
counted catalog facts with per-signal n, tone modules gated by the signal floor, the same Article
serializer as Discover/Search for recent articles, and the L2.2 rule end-to-end (an unrated outlet
is rated=false with null lean — never a fabricated Center). No recommender involvement.
"""

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import location                  # noqa: E402
import outlet_registry           # noqa: E402
import publisher_service as ps   # noqa: E402
import store as store_mod        # noqa: E402


def _add(st, cu, pub, *, title="Headline for the piece", category="politics", lean=-1.0,
         register=None, emotion=None, when="2026-07-01T00:00:00+00:00", url=None,
         language=None, source_type="rss"):
    scored = {"article_id": cu, "outlet": pub, "category": category, "lean": lean, "title": title}
    if register is not None:
        scored["register"] = register
    if emotion is not None:
        scored["emotion"] = emotion
    st.upsert_feed_article(
        canonical_url=cu, url=url if url is not None else cu, publisher=pub, source_publisher=pub,
        title=title, description="context", body=None, published_at=when, source_feed="feed://x",
        scored=scored, language=language, source_type=source_type)


EMO = {"fear": 0.1, "outrage": 0.1, "analysis": 0.5, "positive": 0.1, "neutral": 0.2}


def _npr_catalog(st):
    """6 NPR articles over a 3-day window: 4 politics + 2 business; 5 with register
    (3 reporting / 1 opinion / 1 mixed by the engine's 0.6/0.4 thresholds, one NaN excluded);
    5 with the same emotion vector; languages en x5 + es x1; hosts npr.org (one www variant);
    event locations US x2 + GB x1."""
    days = ["2026-07-01T00:00:00+00:00", "2026-07-02T00:00:00+00:00", "2026-07-02T12:00:00+00:00",
            "2026-07-03T00:00:00+00:00", "2026-07-03T12:00:00+00:00", "2026-07-04T00:00:00+00:00"]
    regs = [0.9, 0.8, 0.7, 0.2, 0.5, float("nan")]
    cats = ["politics", "politics", "politics", "politics", "business", "business"]
    for i in range(6):
        _add(st, f"https://npr.org/a{i}", "NPR",
             url=(f"https://www.npr.org/a{i}" if i == 0 else f"https://npr.org/a{i}"),
             category=cats[i], register=regs[i], emotion=(EMO if i < 5 else None),
             when=days[i], language=("es" if i == 5 else "en"))
    st.replace_article_event_locations(
        "https://npr.org/a0", [location.EventLocation(country="US", source="test")])
    st.replace_article_event_locations(
        "https://npr.org/a1", [location.EventLocation(country="US", source="test")])
    st.replace_article_event_locations(
        "https://npr.org/a2", [location.EventLocation(country="GB", source="test")])


# --------------------------------------------------------------------------- #
# Counted catalog facts + curated registry facts
# --------------------------------------------------------------------------- #
def test_rated_profile_counts_registry_and_site():
    st = store_mod.Store("sqlite://")
    _npr_catalog(st)
    reg = outlet_registry.resolve("NPR")
    p = ps.get_publisher(st, "NPR")

    assert p["name"] == "NPR" and p["rated"] is True
    assert p["lean"] == reg.lean and p["leanBucket"] == "left"
    assert p["registry"]["country"] == reg.country and p["registry"]["scope"] == reg.scope
    assert p["site"] == "https://npr.org"                       # majority host, www-stripped
    assert p["articles"]["total"] == 6
    assert p["articles"]["firstSeen"].startswith("2026-07-01")
    assert p["articles"]["lastSeen"].startswith("2026-07-04")
    assert p["articles"]["perDay"] == 2.0                       # 6 articles / 3 observed days
    assert p["topics"][0] == {"label": "Politics", "count": 4}  # prettified, count-ranked
    assert p["topics"][1] == {"label": "Business", "count": 2}
    assert p["languages"][0] == {"label": "en", "count": 5}
    assert p["eventCountries"] == [{"label": "US", "count": 2}, {"label": "GB", "count": 1}]


def test_tone_modules_counted_with_engine_thresholds():
    st = store_mod.Store("sqlite://")
    _npr_catalog(st)
    p = ps.get_publisher(st, "NPR")
    # register: 0.9/0.8/0.7 -> reporting, 0.2 -> opinion, 0.5 -> mixed, NaN excluded
    assert p["registers"] == {"reporting": 3, "opinion": 1, "mixed": 1, "n": 5}
    assert p["emotion"]["n"] == 5
    assert p["emotion"]["analysis"] == pytest.approx(0.5)
    assert p["emotion"]["neutral"] == pytest.approx(0.2)


def test_recent_articles_use_the_canonical_serializer():
    st = store_mod.Store("sqlite://")
    _npr_catalog(st)
    p = ps.get_publisher(st, "NPR")
    assert [a["id"] for a in p["recent"]][:2] == ["https://npr.org/a5", "https://npr.org/a4"]
    a = p["recent"][0]
    assert a["publisher"] == "NPR" and a["url"] == "https://npr.org/a5"
    assert {"headline", "leanBucket", "emotion", "register", "publishedAt"} <= set(a)


def test_provisional_rows_are_excluded_like_discover():
    st = store_mod.Store("sqlite://")
    _npr_catalog(st)
    _add(st, "https://npr.org/ext", "NPR", source_type="extension")   # provisional, uncorroborated
    p = ps.get_publisher(st, "NPR")
    assert p["articles"]["total"] == 6
    assert all(a["id"] != "https://npr.org/ext" for a in p["recent"])


# --------------------------------------------------------------------------- #
# Honesty contracts
# --------------------------------------------------------------------------- #
def test_unrated_outlet_is_not_rated_and_never_center():
    """L2.2 at the publisher level: no registry row -> rated=false, null lean, no registry block —
    the page says "Not rated", never Center. Counted facts still stand."""
    st = store_mod.Store("sqlite://")
    for i in range(3):
        _add(st, f"https://obscuretribune.example/{i}", "Obscure Tribune", lean=None,
             category="politics", when=f"2026-07-0{i + 1}T00:00:00+00:00")
    p = ps.get_publisher(st, "Obscure Tribune")
    assert p["rated"] is False and p["lean"] is None and p["leanBucket"] is None
    assert "registry" not in p
    assert p["articles"]["total"] == 3
    assert p["topics"] == [{"label": "Politics", "count": 3}]


def test_tone_modules_omitted_below_signal_floor():
    """Fewer than MIN_SIGNAL rows carrying a signal -> the module is absent, not thin-rendered."""
    st = store_mod.Store("sqlite://")
    for i in range(3):
        _add(st, f"https://thin.example/{i}", "Thin Signal Daily", lean=None,
             register=0.9, emotion=EMO)
    p = ps.get_publisher(st, "Thin Signal Daily")
    assert "registers" not in p and "emotion" not in p


def test_registry_only_outlet_profiles_with_honest_zero_volume():
    """A curated outlet with no catalog coverage still has a profile: registry facts stand,
    volume is an honest zero, and nothing (recent/topics) is synthesised."""
    st = store_mod.Store("sqlite://")
    p = ps.get_publisher(st, "Mother Jones")
    assert p["rated"] is True and p["registry"]["country"] == "US"
    assert p["articles"]["total"] == 0 and p["recent"] == [] and p["topics"] == []
    assert p["articles"]["perDay"] is None


def test_alias_and_domain_resolve_to_the_canonical_profile():
    st = store_mod.Store("sqlite://")
    _npr_catalog(st)
    for query in ("npr.org", "https://www.npr.org/some/article", "npr"):
        p = ps.get_publisher(st, query)
        assert p is not None and p["name"] == "NPR" and p["articles"]["total"] == 6


def test_unknown_publisher_is_none_never_synthesised():
    st = store_mod.Store("sqlite://")
    assert ps.get_publisher(st, "Completely Unknown Gazette") is None
    assert ps.get_publisher(st, "") is None


# --------------------------------------------------------------------------- #
# HTTP layer
# --------------------------------------------------------------------------- #
def test_publisher_endpoint_serves_profile_and_404(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    import importlib.util
    from fastapi.testclient import TestClient

    db = f"sqlite:///{tmp_path/'p.db'}"
    st = store_mod.Store(db)
    _npr_catalog(st)
    for k, v in {"RWE_DB_URL": db, "RWE_N_USERS": "120", "RWE_MAX_ITEMS": "400", "RWE_SEED": "0"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("RWE_RECS_SOURCE", raising=False)

    spec = importlib.util.spec_from_file_location("api_fastapi_pub", ROOT / "examples" / "api_fastapi.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["api_fastapi_pub"] = mod
    spec.loader.exec_module(mod)
    with TestClient(mod.app) as c:
        r = c.get("/api/publishers/NPR")
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "NPR" and body["rated"] is True
        assert body["articles"]["total"] == 6
        assert body["registers"]["n"] == 5
        assert len(body["recent"]) == 6
        assert body["recent"][0]["register"] in ("reporting", "opinion", "mixed")   # alias serialised
        assert c.get("/api/publishers/Completely%20Unknown%20Gazette").status_code == 404
