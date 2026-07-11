"""Commit R2 — uncategorized articles are never exposed as a synthesized "General" topic.

Proves: the corpus keeps "" for empty tags; the resolver's top-topics context excludes blank and
legacy-"general" buckets (so topic_continuity can only cite real topics); the Discover serializer
stops synthesizing "General"; and coverage_breadth degrades to its generic sentence for an
uncategorized article instead of claiming a junk-drawer topic.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import discover              # noqa: E402
import evidence_resolver as er  # noqa: E402
import feed_source           # noqa: E402
from simulate_users import catalog_from_qbias  # noqa: E402


def test_uncategorized_csv_rows_stay_blank(tmp_path):
    rows = [{"title": "No tags here", "publisher": "AP", "url": "https://ex.com/a",
             "scored": {"outlet": "AP", "category": "", "lean": 0.0, "political": False,
                        "title": "No tags here"}},
            {"title": "Tagged", "publisher": "AP", "url": "https://ex.com/b",
             "scored": {"outlet": "AP", "category": "Politics", "lean": 0.0, "political": True,
                        "title": "Tagged"}}]
    path = str(tmp_path / "c.csv")
    feed_source.export_candidate_csv(rows, path)
    cat = catalog_from_qbias(path)
    topics = dict(zip(cat.titles.tolist(), cat.topics.tolist()))
    assert topics["No tags here"] == ""          # not "general"
    assert topics["Tagged"] == "Politics"


def test_discover_serializer_keeps_uncategorized_blank():
    art = discover.feed_article_to_article({
        "canonicalUrl": "https://ex.com/x", "url": "https://ex.com/x", "publisher": "AP",
        "title": "T", "description": "", "publishedAt": "2026-07-01T00:00:00+00:00",
        "scored": {"outlet": "AP", "category": "", "lean": 0.0, "political": False, "title": "T"}})
    assert art["topic"] == ""                    # the UI hides the segment; nothing synthesized


def test_topic_continuity_cannot_cite_blank_or_general():
    rec = {"article": {"url": "https://ex.com/y", "id": "https://ex.com/y", "publisher": "AP",
                       "topic": "General", "lean": 0.0, "political": True},
           "crossCutting": False, "strategy": "rwe-b"}
    # even if a legacy payload still says "General", the filtered context never licenses it
    ctx = {"reads": [], "top_topics": ["Politics", "Business"]}
    out = er.resolve(rec, ctx, {})
    assert out["type"] != "topic_continuity"

    # an uncategorized article falls to the claim-free GENERIC coverage_breadth sentence
    rec_blank = {"article": {"url": "https://ex.com/z", "id": "https://ex.com/z", "publisher": "AP",
                             "topic": "", "lean": 0.0, "political": True},
                 "crossCutting": False, "strategy": "rwe-b"}
    out2 = er.resolve(rec_blank, {"reads": [], "top_topics": []}, {})
    assert out2["type"] == "coverage_breadth"
    assert out2["message"] == "Broadens your coverage beyond your usual mix."
    assert out2["evidence"]["topic"] is None
