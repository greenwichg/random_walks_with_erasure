"""The publisher-concentration audit — the tool that decides whether a heuristic is worth building.

Its job is to be able to say NO. So the tests check that it computes precision and recall against
the independent signal honestly, including the cases where a heuristic looks bad.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import audit_publisher_concentration as apc   # noqa: E402


def _story(arts, pubs, coh=None, located=0, title="t"):
    return {"totalCoverage": arts, "publisherCount": pubs, "geoCoherence": coh,
            "locatedMembers": located, "title": title}


def test_per_publisher_is_articles_over_distinct_outlets():
    assert apc.per_publisher(_story(101, 4)) == 25.25
    assert apc.per_publisher(_story(66, 48)) == 1.375


def test_precision_counts_only_removed_clusters_that_are_independently_bad():
    """A gate that removes mostly-GOOD clusters must show low precision, not be flattered by the
    ones it happens to get right."""
    stories = [
        _story(100, 5, coh=0.2, located=10, title="template, genuinely bad"),
        _story(80, 4, coh=0.95, located=10, title="concentrated but coherent"),
        _story(60, 3, coh=0.98, located=10, title="concentrated but coherent"),
        _story(50, 40, coh=0.99, located=10, title="a normal story"),
    ]
    res = apc.analyse(stories, incoherent_below=0.7, min_located=3)
    row = next(r for r in res["rows"] if r["threshold"] == 10.0)
    assert row["storiesRemoved"] == 3            # 20.0, 20.0, 20.0 a/p
    assert row["removedBad"] == 1
    assert abs(row["precision"] - 1 / 3) < 1e-9  # two of three removals were good clusters


def test_recall_exposes_bad_clusters_the_gate_cannot_see():
    """The decisive question. A 196-article cluster spanning twelve countries at a/p 1.9 is the
    worst thing in the catalog and sits squarely in the legitimate band — no concentration
    threshold reaches it."""
    stories = [
        _story(196, 102, coh=0.64, located=47, title="chained mega-cluster"),
        _story(30, 25, coh=0.30, located=10, title="another false merge, normal a/p"),
        _story(45, 5, coh=0.2, located=10, title="template"),
    ]
    res = apc.analyse(stories, incoherent_below=0.7, min_located=3)
    row = next(r for r in res["rows"] if r["threshold"] == 5.0)
    assert row["removedBad"] == 1 and res["bad"] == 3
    assert abs(row["recall"] - 1 / 3) < 1e-9     # catches one of three known-bad clusters


def test_clusters_without_a_coherence_score_are_excluded_from_the_verdict():
    """Most of the catalog has no located members. Counting unscored clusters as "good" would
    manufacture precision out of missing data."""
    stories = [_story(100, 4, coh=None, located=0), _story(20, 10, coh=0.9, located=5)]
    res = apc.analyse(stories, incoherent_below=0.7, min_located=3)
    assert res["scored"] == 1
    row = next(r for r in res["rows"] if r["threshold"] == 5.0)
    assert row["storiesRemoved"] == 1 and row["removedScored"] == 0
    assert row["precision"] is None              # unknown, reported as unknown


def test_a_thin_coherence_sample_is_not_trusted():
    stories = [_story(100, 4, coh=0.1, located=2)]
    assert apc.analyse(stories, incoherent_below=0.7, min_located=3)["scored"] == 0


def test_percentiles_describe_the_whole_distribution():
    res = apc.analyse([_story(n, 1) for n in range(1, 101)], incoherent_below=0.7, min_located=3)
    p = res["perPublisher"]
    assert p["p50"] == 51 and p["max"] == 100
