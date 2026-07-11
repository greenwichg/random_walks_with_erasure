"""Commit C5 — Story Match precedence + cluster licensing + traceability.

Proves the three product guarantees:
1. story_match is generated ONLY when the recommendation and a previously read article belong to
   the SAME validated story cluster (the Story Service's own clusters — the resolver holds no
   clustering logic of its own), and ``validate()`` rejects any forged claim;
2. the explanation carries the matched story id + the matched read (URL, publisher, headline),
   and the internal explain diagnostic re-derives the same licensing facts per recommendation;
3. the Adams-style pair: when story_match (P1) and bridge (P2) are BOTH applicable — a
   cross-cutting political sibling of a story the reader already read — story_match wins.
"""

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import evidence_resolver as er   # noqa: E402
import rec_explain               # noqa: E402

# --- the Adams-style pair: one political story, two outlets across the aisle ----------------
CNN_ADAMS = "https://cnn.example.com/story/adams"
FOX_ADAMS = "https://foxnews.example.com/story/adams"
_COVERAGE = [
    {"url": CNN_ADAMS, "publisher": "CNN", "headline": "Mayor Adams corruption ruling reshapes race",
     "publishedAt": "2026-07-09T09:00:00+00:00"},
    {"url": FOX_ADAMS, "publisher": "Fox News", "headline": "Mayor Adams corruption ruling reshapes race",
     "publishedAt": "2026-07-09T11:00:00+00:00"},
]
INDEX = {er._canon(m["url"]): {"storyId": "st_adams", "coverage": _COVERAGE} for m in _COVERAGE}

# an unrelated cluster with a similar-sounding headline — similarity is NOT membership
OTHER = "https://reuters.example.com/story/adams-profile"
INDEX[er._canon(OTHER)] = {"storyId": "st_other", "coverage": [
    {"url": OTHER, "publisher": "Reuters", "headline": "Mayor Adams: a profile",
     "publishedAt": "2026-07-09T10:00:00+00:00"}]}


def _ctx(reads):
    return {"reads": reads, "familiarity": lambda p: {"band": "familiar", "reads": 5, "share": 0.2},
            "top_topics": ["Politics"], "reader_mean_lean": -0.9}


def _fox_rec(cross=True):
    """Fox's article as the serializer would emit it for a left reader: political + cross-cutting
    — i.e. bridge (P2) is applicable on its own terms."""
    return {"article": {"url": FOX_ADAMS, "id": FOX_ADAMS, "publisher": "Fox News",
                        "topic": "Politics", "lean": 1.6, "political": True,
                        "publishedAt": "2026-07-09T11:00:00+00:00"},
            "crossCutting": cross, "strategy": "rwe-b"}


READ_CNN = {"url": er._canon(CNN_ADAMS), "publisher": "CNN",
            "publishedAt": "2026-07-09T09:00:00+00:00"}


# --------------------------------------------------------------------------- #
# 3 · Precedence: story_match beats bridge when BOTH apply (the Adams-style pair).
# --------------------------------------------------------------------------- #
def test_story_match_takes_precedence_over_bridge():
    rec = _fox_rec(cross=True)
    out = er.resolve(rec, _ctx([READ_CNN]), INDEX)
    assert out["type"] == "story_match"                  # never bridge when both apply
    assert out["priority"] == 1
    assert out["variant"] == "same_event"                # 2h gap < the 6h follow-up bar
    # the rival gate really was live: without the story read, the SAME rec is a bridge
    alone = er.resolve(rec, _ctx([]), INDEX)
    assert alone["type"] == "bridge"
    # and the claim is fully traceable: story id + the matched read
    ev = out["evidence"]
    assert ev["storyId"] == "st_adams"
    assert ev["readUrl"] == er._canon(CNN_ADAMS)
    assert ev["readPublisher"] == "CNN"
    assert ev["readHeadline"] == "Mayor Adams corruption ruling reshapes race"
    assert er.validate(out, rec, _ctx([READ_CNN]), INDEX) == []


# --------------------------------------------------------------------------- #
# 1 · Cluster licensing: same VALIDATED story cluster or no story_match at all.
# --------------------------------------------------------------------------- #
def test_similar_headline_in_another_cluster_never_matches():
    """Reading a similar-sounding article from a DIFFERENT cluster licenses nothing — membership
    comes from the Story Service's clusters, never from headline similarity."""
    read_other = {"url": er._canon(OTHER), "publisher": "Reuters",
                  "publishedAt": "2026-07-09T10:00:00+00:00"}
    out = er.resolve(_fox_rec(), _ctx([read_other]), INDEX)
    assert out["type"] == "bridge"                       # falls to the next applicable priority


def test_rec_outside_any_cluster_never_matches():
    rec = {"article": {"url": "https://foxnews.example.com/politics/unclustered",
                       "id": "x", "publisher": "Fox News", "topic": "Politics",
                       "lean": 1.6, "political": True},
           "crossCutting": True, "strategy": "rwe-b"}
    out = er.resolve(rec, _ctx([READ_CNN]), INDEX)
    assert out["type"] == "bridge"


def test_same_publisher_coverage_never_matches():
    """A sibling from the SAME outlet can't claim "here's how Fox covered it"."""
    read_fox = {"url": er._canon(FOX_ADAMS), "publisher": "Fox News",
                "publishedAt": "2026-07-09T11:00:00+00:00"}
    rec = {"article": {"url": CNN_ADAMS, "id": CNN_ADAMS, "publisher": "Fox News",
                       "topic": "Politics", "lean": 1.6, "political": True},
           "crossCutting": True, "strategy": "rwe-b"}
    out = er.resolve(rec, _ctx([read_fox]), INDEX)
    assert out["type"] != "story_match"


def test_validate_rejects_forged_story_claims():
    rec = _fox_rec()
    ctx = _ctx([READ_CNN])
    good = er.resolve(rec, ctx, INDEX)
    assert er.validate(good, rec, ctx, INDEX) == []

    # wrong story id — the claim must cite the cluster the index actually assigns
    forged = dict(good, evidence=dict(good["evidence"], storyId="st_forged"))
    assert any("story ids differ" in f for f in er.validate(forged, rec, ctx, INDEX))

    # a cited read that is NOT a member of the recommended article's cluster
    forged = dict(good, evidence=dict(good["evidence"], readUrl=er._canon(OTHER)))
    fails = er.validate(forged, rec, ctx, INDEX)
    assert any("not a member of the story" in f for f in fails)

    # a cited read the reader never made
    ctx_empty = _ctx([])
    assert any("not in the reader's history" in f
               for f in er.validate(good, rec, ctx_empty, INDEX))

    # a rec article that is in NO cluster cannot carry a story_match at all
    rec_out = {"article": {"url": "https://ex.com/nowhere", "id": "n", "publisher": "Fox News"},
               "crossCutting": True, "strategy": "rwe-b"}
    assert any("not in any story" in f for f in er.validate(good, rec_out, ctx, INDEX))


# --------------------------------------------------------------------------- #
# 2 · The explain diagnostic re-derives the licensing facts per recommendation.
# --------------------------------------------------------------------------- #
def test_story_match_diag_matched():
    d = rec_explain._story_match_diag(_fox_rec()["article"], INDEX, {er._canon(CNN_ADAMS)})
    assert d["matched"] is True and d["reason"] == "matched"
    assert d["storyId"] == "st_adams"
    assert [m["url"] for m in d["readMatches"]] == [CNN_ADAMS]
    assert d["readMatches"][0]["publisher"] == "CNN"
    assert d["readMatches"][0]["headline"] == "Mayor Adams corruption ruling reshapes race"


def test_story_match_diag_failure_reasons():
    art = _fox_rec()["article"]
    # no cluster
    d = rec_explain._story_match_diag({"url": "https://ex.com/none", "publisher": "X"}, INDEX, set())
    assert d == {"storyId": None, "matched": False, "reason": "not_in_any_story", "readMatches": []}
    # cluster, but the reader read none of its members
    d = rec_explain._story_match_diag(art, INDEX, {er._canon(OTHER)})
    assert d["matched"] is False and d["reason"] == "no_story_mate_in_history"
    assert d["storyId"] == "st_adams"
    # only same-publisher coverage read
    cnn_art = {"url": CNN_ADAMS, "publisher": "CNN"}
    d = rec_explain._story_match_diag(cnn_art, INDEX, {er._canon(CNN_ADAMS)})
    assert d["reason"] == "no_story_mate_in_history"      # own article excluded, nothing else read
    fox_as_cnn = {"url": FOX_ADAMS, "publisher": "Fox News"}
    d = rec_explain._story_match_diag(fox_as_cnn, INDEX,
                                      {er._canon(FOX_ADAMS), er._canon(CNN_ADAMS)})
    assert d["matched"] is True                           # CNN read qualifies (different publisher)


def test_story_match_diag_same_publisher_only():
    """The reader read another FOX article of the story — membership exists but P1 stays blocked,
    and the diagnostic names the exact gate."""
    coverage = _COVERAGE + [{"url": "https://foxnews.example.com/story/adams-2",
                             "publisher": "Fox News", "headline": "Adams ruling: what's next",
                             "publishedAt": "2026-07-09T12:00:00+00:00"}]
    idx = {er._canon(m["url"]): {"storyId": "st_adams", "coverage": coverage} for m in coverage}
    d = rec_explain._story_match_diag(_fox_rec()["article"], idx,
                                      {er._canon("https://foxnews.example.com/story/adams-2")})
    assert d["matched"] is False and d["reason"] == "only_same_publisher_coverage"
    assert d["readMatches"] and d["readMatches"][0]["publisher"] == "Fox News"


def test_diag_agrees_with_resolver():
    """The diagnostic's verdict and the resolver's behavior can never disagree: matched=True
    ⇔ resolve() returns story_match (for a rec where P1 is the question)."""
    cases = [
        ({er._canon(CNN_ADAMS)}, [READ_CNN]),
        ({er._canon(OTHER)}, [{"url": er._canon(OTHER), "publisher": "Reuters",
                               "publishedAt": None}]),
        (set(), []),
    ]
    for read_urls, reads in cases:
        d = rec_explain._story_match_diag(_fox_rec()["article"], INDEX, read_urls)
        out = er.resolve(_fox_rec(), _ctx(reads), INDEX)
        assert d["matched"] == (out["type"] == "story_match")
