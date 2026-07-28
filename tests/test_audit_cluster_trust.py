"""The cluster-trust auditor (examples/audit_cluster_trust.py).

The launch gates key off ``geoCoherence``, which only ~11% of stories carry. The defence of that is
that coverage is not uniform — scoring needs three located members, so it is densest on exactly the
large clusters the gates apply to. This tool exists to CHECK that defence rather than assert it, so
what these tests pin is the top-N coverage measurement and the two ratio monitors.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import audit_cluster_trust as act   # noqa: E402
import story_service as ss          # noqa: E402


def _story(pubs, arts, coherence, trust, *, withheld=False, title="t", located=9):
    return {"publisherCount": pubs, "totalCoverage": arts, "geoCoherence": coherence,
            "clusterTrust": trust, "blindspotWithheld": withheld, "title": title,
            "locatedMembers": located}


def test_buckets_count_stories_and_articles():
    """Articles matter more than stories here: five bad clusters out of 807 sounds negligible until
    you see that they hold 7.5% of everything in a story."""
    res = act.analyse([
        _story(3, 4, 1.0, ss.TRUST_OK),
        _story(106, 208, 0.62, ss.TRUST_LOW, withheld=True),
    ], top=5)
    assert res["buckets"][ss.TRUST_OK] == {"stories": 1, "articles": 4}
    assert res["buckets"][ss.TRUST_LOW] == {"stories": 1, "articles": 208}
    assert len(res["withheld"]) == 1 and len(res["demoted"]) == 1


def test_top_n_coverage_is_measured_over_the_biggest_clusters():
    """Ranked by publisherCount because that is what the default sort ranks by — the head of that
    ordering is the population the gates are meant to police."""
    stories = [_story(50 - i, 60 - i, None if i > 1 else 0.9, ss.TRUST_OK) for i in range(10)]
    res = act.analyse(stories, top=4)
    assert res["topTotal"] == 4 and res["topScored"] == 2
    assert [s["publisherCount"] for s in res["head"]] == [50, 49, 48, 47]


def test_coverage_counts_only_ACTIONABLE_scores():
    """A coherence value the gate would never act on is not coverage. Counting it would report the
    gates as load-bearing while they sat over scores too thin to use."""
    thin = [_story(50 - i, 60, 0.9, ss.TRUST_OK, located=2) for i in range(4)]
    assert act.analyse(thin, top=4)["topScored"] == 0
    thick = [_story(50 - i, 60, 0.9, ss.TRUST_OK, located=9) for i in range(4)]
    assert act.analyse(thick, top=4)["topScored"] == 4


def test_monitors_are_ratios_not_counts():
    """A raw largest-cluster count stops being comparable the moment the corpus grows, which is the
    exact regime the mega-cluster was found in (194 -> 208 -> 318 on a 23% bigger catalog)."""
    small = act.analyse([_story(2, 2, None, ss.TRUST_OK) for _ in range(9)]
                        + [_story(9, 20, None, ss.TRUST_OK)], top=5)
    big = act.analyse([_story(2, 4, None, ss.TRUST_OK) for _ in range(9)]
                      + [_story(9, 40, None, ss.TRUST_OK)], top=5)
    assert small["largestOverP90"] == big["largestOverP90"], "doubling everything moves neither"
    assert round(small["largestShare"], 6) == round(big["largestShare"], 6)


def test_empty_catalog_does_not_divide_by_zero():
    res = act.analyse([], top=5)
    assert res["largestOverP90"] == 0.0 and res["largestShare"] == 0.0
    assert res["topTotal"] == 0 and res["stories"] == 0


# --------------------------------------------------------------------------- #
# Blindspot claim support — the same missing sample-size floor the coherence gate had.
# --------------------------------------------------------------------------- #
def _claimer(side, *leans):
    return {"publisherCount": len(leans), "totalCoverage": len(leans), "geoCoherence": None,
            "clusterTrust": ss.TRUST_OK, "blindspotWithheld": False, "title": "t",
            "locatedMembers": 0, "blindspotSide": side,
            "coverage": [{"publisher": f"P{i}", "leanBucket": b} for i, b in enumerate(leans)]}


def test_a_single_rated_publisher_still_produces_a_claim():
    """The defect, stated as a measurement. One outlet covering something says nothing about who
    else did or did not, but the distribution is 1.0 in its bucket and two buckets are empty, so
    the claim fires anyway. This is absence of evidence reported as evidence of absence."""
    support = act.claim_support([_claimer("center", "left")])
    assert support == {"1": 1}


def test_claims_are_bucketed_by_rated_publishers_not_by_articles():
    """Unrated outlets cast no vote, so they are not the sample the claim rests on — counting
    articles would make a thinly-rated story look well-evidenced."""
    story = _claimer("right", "left", "center", None, None, None)
    assert act.rated_publishers(story) == 2
    assert act.claim_support([story]) == {"2": 1}


def test_stories_without_a_claim_are_not_counted():
    assert act.claim_support([_claimer(None, "left", "right", "center")]) == {}


def test_well_supported_claims_collapse_into_one_bucket():
    """Everything at 4+ is reported together: the question is whether a claim has enough behind it
    to mean something, not exactly how much."""
    big = _claimer("left", "right", "right", "right", "center", "center")
    assert act.claim_support([big]) == {"4+": 1}


def test_the_coverage_bar_needs_a_denominator_that_can_carry_it():
    """13 of 16 (81%) became 11 of 14 (79%) on catalog churn and printed TOO THIN, as though
    something had regressed. At n=14 one cluster moves the figure seven points. The constant exists
    so the tool says "cannot judge" instead of inventing a verdict — the same lesson as reading a
    coherence ratio off two located members."""
    assert act.MIN_COVERAGE_DENOM >= 20
    thin = [_story(9 - i, 9 - i, 0.9, ss.TRUST_OK, located=9) for i in range(5)]
    res = act.analyse(thin, top=20)
    assert res["topLocatable"] < act.MIN_COVERAGE_DENOM


# --------------------------------------------------------------------------- #
# The rating worklist — why curating seven outlets moved claims 61 -> 62.
# --------------------------------------------------------------------------- #
def _rated(*pairs):
    return {"title": "t", "totalCoverage": len(pairs), "publisherCount": len(pairs),
            "geoCoherence": None, "clusterTrust": ss.TRUST_OK, "blindspotWithheld": False,
            "locatedMembers": 0, "blindspotSide": None,
            "coverage": [{"publisher": p, "leanBucket": b} for p, b in pairs]}


def test_a_story_one_rating_short_names_the_outlet_that_would_unlock_it():
    """The only case a single registry row can convert."""
    st = _rated(("NPR", "left"), ("Fox News", "right"), ("Smalltown Gazette", None))
    res = act.blocked_by_ratings([st], min_rated=3)
    assert res["short"] == {1: 1}
    assert res["unlock"] == [(1, "Smalltown Gazette")]


def test_stories_further_short_are_counted_but_not_offered_as_a_worklist():
    """Two ratings short needs coordinated curation; listing its outlets as one-row wins would
    overstate what a single edit buys."""
    st = _rated(("NPR", "left"), ("A", None), ("B", None))
    res = act.blocked_by_ratings([st], min_rated=3)
    assert res["short"] == {2: 1} and res["unlock"] == []


def test_a_story_without_enough_outlets_is_not_blocked_by_ratings():
    """Two publishers can never carry the claim however well rated they are — that is the
    structural ceiling, not a curation gap, and mixing the two would inflate the worklist."""
    assert act.blocked_by_ratings([_rated(("A", None), ("B", None))], min_rated=3)["blocked"] == 0


def test_a_story_that_already_qualifies_is_not_in_the_worklist():
    st = _rated(("NPR", "left"), ("Fox News", "right"), ("BBC News", "center"), ("X", None))
    assert act.blocked_by_ratings([st], min_rated=3)["blocked"] == 0


def test_the_worklist_ranks_by_stories_unlocked_not_by_article_volume():
    """The correction this exists for. An outlet with many articles that all sit in stories which
    already qualify unlocks nothing; a quieter one sitting in several one-short stories unlocks
    several. Volume was the wrong worklist."""
    loud = [_rated(("NPR", "left"), ("Fox News", "right"), ("BBC News", "center"), ("Loud", None))
            for _ in range(5)]
    quiet = [_rated(("NPR", "left"), ("Fox News", "right"), ("Quiet", None)) for _ in range(2)]
    res = act.blocked_by_ratings(loud + quiet, min_rated=3)
    assert res["unlock"] == [(2, "Quiet")], "Loud has more articles and unlocks nothing"


def test_one_outlet_under_two_names_counts_once():
    """Identity applies here too, or a fragmented outlet would look like two rating gaps."""
    st = _rated(("NPR", "left"), ("Fox News", "right"),
                ("Sportskeeda.Com", None), ("Sportskeeda", None))
    res = act.blocked_by_ratings([st], min_rated=3)
    assert res["short"] == {1: 1}, "the two forms are one missing rating, not two"
