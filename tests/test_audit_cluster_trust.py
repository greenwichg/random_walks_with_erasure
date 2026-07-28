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
