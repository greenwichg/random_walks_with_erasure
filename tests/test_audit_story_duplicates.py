"""The near-duplicate auditor (examples/audit_story_duplicates.py).

Measures the failure OPPOSITE to chaining: one event covered under disjoint vocabulary becomes
several stories, because the headlines never clear MIN_SHARED_TOKENS. The instrument has to use a
signal the clusterer does not — member descriptions — or it would simply re-derive the same answer
that produced the split in the first place. These tests pin that, and pin the counting, which is
where a duplicate audit most easily flatters itself.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import audit_story_duplicates as asd   # noqa: E402
import clustering                      # noqa: E402

WHEN = "2026-07-25T09:00:00+00:00"
LATER = "2026-07-25T15:00:00+00:00"
NEXT_WEEK = "2026-08-02T09:00:00+00:00"


def _story(title, urls, *, arts=None, earliest=WHEN, latest=WHEN, votes=None):
    return {
        "title": title,
        "totalCoverage": arts if arts is not None else len(urls),
        "earliest": earliest, "latest": latest,
        "countryVotes": votes or {},
        "coverage": [{"headline": h, "url": u} for u, h in urls],
    }


# The production case, headlines verbatim. They share one or two tokens against MIN_SHARED_TOKENS
# = 3, so no linkage rule can ever join them — which is exactly why the clusterer needs help here.
SEATTLE_A = "At Least 2 Killed in Shooting at Food Festival in Seattle"
SEATTLE_B = "Two dead, five injured in shooting near Seattle's Space Needle"


def test_the_headlines_alone_could_never_merge():
    """The premise. If these cleared the pairwise gate the whole audit would be unnecessary."""
    ta, tb = clustering.title_tokens(SEATTLE_A), clustering.title_tokens(SEATTLE_B)
    assert len(ta & tb) < clustering.MIN_SHARED_TOKENS
    assert clustering.jaccard(ta, tb) < clustering.DEFAULT_SIM


def test_descriptions_surface_a_duplicate_the_headlines_hide():
    """Same event, disjoint headline vocabulary, overlapping description vocabulary."""
    desc = {
        "a1": "Police say a gunman opened fire near the Space Needle at Seattle Center on Friday,"
              " killing two people and wounding five before fleeing the plaza.",
        "b1": "A gunman opened fire at Seattle Center near the Space Needle on Friday, police said,"
              " killing two and wounding five others at the plaza.",
    }
    stories = [_story(SEATTLE_A, [("a1", SEATTLE_A)]), _story(SEATTLE_B, [("b1", SEATTLE_B)])]
    pairs = asd.find_pairs(stories, desc, min_sim=0.15, max_gap_hours=48.0)
    assert pairs and pairs[0][1:] == (0, 1)


def test_unrelated_stories_are_not_paired():
    desc = {"a1": "Police say a gunman opened fire near the Space Needle in Seattle on Friday.",
            "b1": "The Senate passed a funding bill on Thursday, averting a federal shutdown."}
    stories = [_story(SEATTLE_A, [("a1", SEATTLE_A)]),
               _story("Senate passes funding bill", [("b1", "Senate passes funding bill")])]
    assert asd.find_pairs(stories, desc, min_sim=0.15, max_gap_hours=48.0) == []


def test_a_recurring_topic_is_not_a_duplicate():
    """Coverage of one event arrives in a burst. Two clusters a week apart that read alike are a
    recurring topic — a weekly fixture, a monthly filing — and pairing them would be a false
    positive that grows with the size of the archive."""
    text = "Police say a gunman opened fire near the Space Needle at Seattle Center on Friday."
    desc = {"a1": text, "b1": text}
    stories = [_story(SEATTLE_A, [("a1", SEATTLE_A)]),
               _story(SEATTLE_B, [("b1", SEATTLE_B)], earliest=NEXT_WEEK, latest=NEXT_WEEK)]
    assert asd.find_pairs(stories, desc, min_sim=0.15, max_gap_hours=48.0) == []
    assert asd.find_pairs(stories, desc, min_sim=0.15, max_gap_hours=400.0), "window is the reason"


def test_overlapping_coverage_windows_count_as_zero_gap():
    a = _story("x", [], earliest=WHEN, latest=NEXT_WEEK)
    b = _story("y", [], earliest=LATER, latest=LATER)
    assert asd._gap_hours(a, b) == 0.0
    far = _story("z", [], earliest=NEXT_WEEK, latest=NEXT_WEEK)
    assert asd._gap_hours(_story("w", [], latest=WHEN), far) > 100.0


def test_groups_count_events_not_pairs():
    """Four clusters of one event are SIX pairs but ONE duplicated event. Reporting pairs alone
    overstates how much is actually wrong, and overstates it quadratically."""
    quad = [(0.9, i, j) for i in range(4) for j in range(i + 1, 4)]
    assert len(quad) == 6 and asd._groups(quad) == 1
    assert asd._groups([(0.9, 0, 1), (0.9, 2, 3)]) == 2
    assert asd._groups([]) == 0


def test_geo_agreement_is_reported_not_assumed():
    """Event geography is independent of the text, so it corroborates — but it is only present on
    a minority of stories and must say so rather than guess."""
    a = _story("a", [], votes={"US": 5})
    assert asd._geo(a, _story("b", [], votes={"US": 3})) == "same"
    assert asd._geo(a, _story("b", [], votes={"FR": 3})) == "diff"
    assert asd._geo(a, _story("b", [])) == "-"


def test_analyse_reports_article_volume_at_every_threshold():
    """Twelve duplicate pairs sounds negligible until you see they hold 300 articles."""
    text = "Police say a gunman opened fire near the Space Needle at Seattle Center on Friday."
    desc = {"a1": text, "b1": text}
    stories = [_story(SEATTLE_A, [("a1", SEATTLE_A)], arts=13),
               _story(SEATTLE_B, [("b1", SEATTLE_B)], arts=9)]
    res = asd.analyse(stories, desc, max_gap_hours=48.0)
    top = res["rows"][0]
    assert top["pairs"] == 1 and top["groups"] == 1
    assert top["stories"] == 2 and top["articles"] == 22
    assert [r["threshold"] for r in res["rows"]] == list(asd.THRESHOLDS)


def test_empty_catalog_is_not_a_crash():
    res = asd.analyse([], {}, max_gap_hours=48.0)
    assert res["stories"] == 0 and all(r["pairs"] == 0 for r in res["rows"])
