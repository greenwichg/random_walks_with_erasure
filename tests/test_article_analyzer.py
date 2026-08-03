"""Article Analyzer A1 — the analysis service, ANALYSIS CONTRACT v1.

Pins the approved lifecycle invariants: catalog-first resolution, fetchless in-memory scoring
(the SAME scorer as ingestion, never the cache-writing wrapper), **zero database writes of any
kind** (the core product invariant: analysis alone must never influence Information Health,
recommendations, analytics, or the recommendation graph), deterministic output, honest
degradation (URL-only scoring, unknown outlets, invalid URLs), and honest story claims
(membership only for catalog articles; at most a ``similarStory`` advisory otherwise).
Reader-relative sections are pinned null — they arrive in A2/A3.
"""
import hashlib
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import article_analyzer as aa                               # noqa: E402
import evidence_resolver as er                              # noqa: E402
import store as store_mod                                   # noqa: E402

CLUSTER = [  # two-bucket story (left + center, NO right coverage) -> missing viewpoint
    ("The Guardian", "theguardian.com", -1.0, "senate budget vote reaches bipartisan deal"),
    ("AP", "apnews.com", 0.0, "senate passes budget vote after bipartisan deal"),
    ("Reuters", "reuters.com", 0.0, "bipartisan budget deal clears senate vote"),
]


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


@pytest.fixture(scope="module")
def db_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("analyzer") / "analyzer.db"
    st = store_mod.Store(f"sqlite:///{p}")
    pubs = ["AP", "Reuters", "NPR", "BBC News", "The Guardian", "The Hill", "Fox News", "CNN"]
    for k in range(40):
        pub = pubs[k % 8]
        url = f"https://{pub.split()[0].lower()}{k % 8}.example.com/an/{k}"
        st.upsert_feed_article(
            canonical_url=er._canon(url), url=url, publisher=pub, source_publisher=pub,
            title=f"an{k}a an{k}b an{k}c an{k}d", description="d", body=None,
            published_at=_iso(0.5 + (k % 5) * 0.5), source_feed="f",
            scored={"article_id": er._canon(url), "outlet": pub, "category": "Politics",
                    "lean": (-1.0, 0.0, 1.0)[k % 3], "political": True, "title": f"an{k}",
                    "emotion": {"fear": 0.1, "outrage": 0.1, "analysis": 0.2,
                                "positive": 0.1, "neutral": 0.5},
                    "register": 0.8})
    for i, (pub, domain, lean, title) in enumerate(CLUSTER):
        url = f"https://{domain}/story/an-cluster-{i}"
        st.upsert_feed_article(
            canonical_url=er._canon(url), url=url, publisher=pub, source_publisher=pub,
            title=title, description="d", body=None, published_at=_iso(1.0 + 0.1 * i),
            source_feed="f",
            scored={"article_id": er._canon(url), "outlet": pub, "category": "Politics",
                    "lean": lean, "political": True, "title": title})
    return p


@pytest.fixture(scope="module")
def store(db_path):
    return store_mod.Store(f"sqlite:///{db_path}")


CONTRACT_KEYS = {"analysisVersion", "input", "status", "source", "article", "scoring",
                 "story", "coverageComparison", "recommendation", "personal", "explanation",
                 "insights", "notes"}


# --------------------------------------------------------------------------- #
# Contract + the zero-write / determinism invariants.
# --------------------------------------------------------------------------- #
def test_contract_v1_shape_and_deferred_sections(store):
    out = aa.analyze(store, "https://ap0.example.com/an/0")
    assert set(out) == CONTRACT_KEYS and out["analysisVersion"] == 1
    assert out["status"] == "analyzed"
    # A2/A3 sections exist in the envelope but are pinned null until those milestones
    assert out["recommendation"] is None and out["personal"] is None
    assert out["explanation"] is None
    assert out["insights"] is None            # filled by the endpoint from the cache, never here
    # coverageComparison is computed HERE (it is article+story only, never reader-relative), so it
    # is null only when the article is not a trusted-cluster member — which this fixture is not.
    assert "coverageComparison" in out
    json.dumps(out, allow_nan=False)


def test_analysis_writes_nothing_anywhere(store, db_path):
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()
    aa.analyze(store, "https://ap0.example.com/an/0")                       # catalog hit
    aa.analyze(store, "https://apnews.com/article/brand-new",              # miss + metadata
               metadata={"title": "senate weighs new budget measure",
                         "description": "coverage", "category": "Politics"})
    aa.analyze(store, "https://unknown-blog.example/post")                  # unknown outlet
    aa.analyze(store, "not a url")                                          # invalid
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before
    # specifically: the scored-article CACHE gained no rows (score_with_cache is not used)
    assert store.get_scored_article("https://apnews.com/article/brand-new") is None


def test_analysis_is_deterministic(store):
    md = {"title": "senate weighs new budget measure", "category": "Politics"}
    a = aa.analyze(store, "https://apnews.com/article/brand-new", metadata=md)
    b = aa.analyze(store, "https://apnews.com/article/brand-new", metadata=md)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# --------------------------------------------------------------------------- #
# Catalog-first resolution.
# --------------------------------------------------------------------------- #
def test_catalog_hit_reuses_the_stored_article_and_scoring(store):
    out = aa.analyze(store, "https://ap0.example.com/an/0")
    assert out["source"] == "catalog"
    assert out["article"]["headline"].startswith("an0")      # the canonical Article shape
    assert out["article"]["publisher"] == "AP"
    sc = out["scoring"]
    assert sc["lean"] == -1.0 and sc["leanBucket"] == "left"
    assert sc["topic"] == "Politics" and sc["political"] is True
    assert sc["emotion"]["neutral"] == 0.5 and sc["register"] == 0.8


def test_miss_scores_in_memory_with_client_metadata(store):
    out = aa.analyze(store, "https://apnews.com/article/brand-new",
                     metadata={"title": "senate weighs new budget measure",
                               "description": "lawmakers debate the plan",
                               "category": "Politics"})
    assert out["source"] == "scored_url_only" and out["article"] is None
    sc = out["scoring"]
    assert sc["outlet"] == "Associated Press"                # registry resolution
    assert sc["lean"] == 0.0 and sc["leanBucket"] == "center"
    assert sc["topic"] == "Politics"
    assert sc["emotion"] is not None and sc["register"] is not None   # enricher ran


def test_url_only_miss_degrades_honestly(store):
    out = aa.analyze(store, "https://theguardian.com/politics/some-slug")
    assert out["source"] == "scored_url_only"
    assert out["scoring"]["topic"] == "Politics"             # URL section still classifies
    assert out["scoring"]["lean"] == -1.0                    # registry knows the outlet
    assert any("no page metadata" in n for n in out["notes"])


def test_unknown_outlet_reports_null_lean_never_a_guess(store):
    out = aa.analyze(store, "https://totally-unknown-blog.example/hot-take",
                     metadata={"title": "a take"})
    assert out["scoring"]["lean"] is None and out["scoring"]["leanBucket"] is None
    assert any("registry" in n for n in out["notes"])
    json.dumps(out, allow_nan=False)                         # NaN never leaks


def test_invalid_url_is_rejected_before_any_work(store):
    out = aa.analyze(store, "not a url")
    assert out["status"] == "invalid_url"
    assert out["source"] is None and out["scoring"] is None and out["story"] is None


# --------------------------------------------------------------------------- #
# Story context: membership for catalog articles, advisory-only otherwise.
# --------------------------------------------------------------------------- #
def test_catalog_cluster_member_gets_real_membership(store):
    out = aa.analyze(store, "https://theguardian.com/story/an-cluster-0")
    st_ = out["story"]
    assert st_["matched"] is True and st_["publisherCount"] == 3
    assert st_["distribution"]["right"] == 0
    assert st_["missingViewpoints"] == ["right"]             # honest gap, derived not invented


def test_non_catalog_sibling_gets_similar_story_never_membership(store):
    out = aa.analyze(store, "https://foxnews.com/politics/budget-take",
                     metadata={"title": "senate budget vote bipartisan deal explained"})
    st_ = out["story"]
    assert st_["matched"] is False                           # membership requires the catalog
    assert st_["similarStory"] is not None
    assert st_["similarStory"]["similarity"] >= 0.28
    member = aa.analyze(store, "https://apnews.com/story/an-cluster-1")
    assert member["story"]["matched"] is True
    assert st_["similarStory"]["storyId"] == member["story"]["storyId"]


def test_token_disjoint_article_matches_nothing(store):
    out = aa.analyze(store, "https://cnn.com/sports/zebra-chess",
                     metadata={"title": "zebra quartet wins improbable chess marathon"})
    assert out["story"] == {"matched": False, "similarStory": None}
