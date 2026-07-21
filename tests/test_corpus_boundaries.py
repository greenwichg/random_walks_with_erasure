"""Guardrails for the three-dataset corpus architecture (docs/CORPUS_ARCHITECTURE.md).

These tests make the architectural boundaries *contracts*, so they cannot erode by accident:

  ① Full / Searchable Corpus (feed_articles)   — everything ingested; search/discover/stories read it
  ② Recommendation Corpus (qbias projection)   — lean-resolvable/fresh/capped; ONLY recommendations read it
  ③ User reads                                  — Information Health metrics derive from here

Principle: searchable != recommendable; ingestion != recommendation; reads are a separate concern.

Low-cost by design: one behavioral test proving the contract end-to-end (a no-lean article is
searchable but excluded from the recommendation corpus), and two source-level structural checks that
the browse and recommendation endpoints stay on their own dataset. No new abstractions, no runtime
changes.
"""
import csv
import inspect
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import store as store_mod          # noqa: E402
import search                      # noqa: E402
import feed_source                 # noqa: E402


def _add(st, cu, publisher, lean, title, *, category="Politics", desc="context"):
    """Insert one FeedArticle into the full corpus (mirrors tests/test_search.py). ``lean=None`` models
    an unknown-outlet article (e.g. a broad GDELT item) with no resolvable lean."""
    st.upsert_feed_article(
        canonical_url=cu, url=cu, publisher=publisher, source_publisher=publisher, title=title,
        description=desc, body=None, published_at=datetime.now(timezone.utc).isoformat(),
        source_feed="feed://x",
        scored={"article_id": cu, "outlet": publisher, "category": category, "lean": lean, "title": title})


# --------------------------------------------------------------------------- #
# ① vs ② — the core product contract: searchable != recommendable
# --------------------------------------------------------------------------- #
def test_no_lean_article_is_searchable_but_excluded_from_recommendation_corpus(tmp_path):
    st = store_mod.Store("sqlite://")
    _add(st, "https://npr.org/a", "NPR", -1.5, "Senate passes funding bill")        # resolvable lean
    _add(st, "https://unknown.example/x", "Unknown Blog", None, "Local roundup")    # no lean (GDELT-style)

    # ① FULL CORPUS: search returns BOTH — the no-lean article is fully searchable.
    titles = {r["headline"] for r in search.search(st)["results"]}
    assert "Senate passes funding bill" in titles
    assert "Local roundup" in titles, "a no-lean article must remain searchable (dataset ①)"

    # ② RECOMMENDATION CORPUS: the qbias serializer gives the no-lean row an EMPTY bias_rating, which
    # catalog_from_qbias drops (feed_source.py:47-64) — so it can never be recommended.
    path = tmp_path / "rec_corpus.csv"
    feed_source.export_candidate_csv(st.list_feed_articles(limit=10**6), str(path))
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    header, data = rows[0], rows[1:]
    ti, bi = header.index("title"), header.index("bias_rating")
    bias = {r[ti]: r[bi] for r in data}
    assert bias["Senate passes funding bill"] != "", "a lean-resolvable article is recommendable"
    assert bias["Local roundup"] == "", "a no-lean article must be dropped from the recommendation corpus (②)"


# --------------------------------------------------------------------------- #
# Endpoint boundaries — browse reads ①, recommendations read ②. Source-level (cheap, no app boot).
# The forbidden strings are call-site identifiers that never appear in these functions' prose.
# --------------------------------------------------------------------------- #
_BROWSE_ENDPOINTS = ("search_feed", "discover_feed", "stories", "story_single",
                     "story_intelligence_endpoint")


def test_browse_endpoints_do_not_read_the_recommendation_corpus():
    import api_fastapi as api
    for name in _BROWSE_ENDPOINTS:
        src = inspect.getsource(getattr(api, name))
        for forbidden in ("active.backend", "active.personalizer", "_serve("):
            assert forbidden not in src, (
                f"/{name} must read the FULL corpus (①), not the recommendation corpus (②): "
                f"found '{forbidden}'. See docs/CORPUS_ARCHITECTURE.md.")


def test_recommendations_endpoint_reads_the_projection_not_the_full_corpus():
    import api_fastapi as api
    src = inspect.getsource(api.recommendations)
    assert "list_feed_articles" not in src, (
        "/recommendations must read the recommendation corpus (②), never the full corpus (①) directly.")
    assert "active." in src, (
        "/recommendations must read the Active recommendation corpus (②).")
