"""The story-id churn auditor (examples/audit_story_id_churn.py).

story_service's own docstring claims ids are "stable across rebuilds as a cluster evolves". That
holds for the case it was designed against — a LATER article joining never disturbs the earliest
member — and not for the two that happen routinely: the earliest member ageing out of the rolling
window, and an earlier article arriving out of order. A story id is what a saved link points at, so
these tests pin the measurement of something that was asserted and never checked.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import audit_story_id_churn as churn   # noqa: E402


def _story(sid, title, urls, *, stamps=None):
    stamps = stamps or {}
    return {"id": sid, "title": title, "totalCoverage": len(urls),
            "coverage": [{"url": u, "publishedAt": stamps.get(u, f"2026-07-2{i}T09:00:00+00:00")}
                         for i, u in enumerate(urls)]}


def test_matching_is_by_members_not_by_id():
    """Matching on id could only ever report zero churn — the id is the thing under test."""
    before = [_story("st_old", "Ferry aground", ["a", "b", "c"])]
    after = [_story("st_new", "Ferry aground", ["b", "c", "d"])]
    pairs = churn.match(before, after)
    assert len(pairs) == 1 and pairs[0][0]["id"] != pairs[0][1]["id"]


def test_unrelated_stories_do_not_match():
    before = [_story("st_1", "Ferry aground", ["a", "b"])]
    after = [_story("st_2", "Senate bill", ["x", "y"])]
    assert churn.match(before, after) == []


def test_a_stable_id_is_not_counted_as_churn():
    before = [_story("st_1", "Ferry aground", ["a", "b", "c"])]
    after = [_story("st_1", "Ferry aground", ["a", "b", "c", "d"])]
    res = churn.churn(before, after)
    assert res["matched"] == 1 and res["changed"] == []


def test_the_representative_ageing_out_is_reported_separately():
    """The rolling window drops the oldest article; the representative becomes the next-oldest and
    the id moves. Distinct from an out-of-order arrival because the fixes differ."""
    stamps = {"a": "2026-07-20T09:00:00+00:00", "b": "2026-07-21T09:00:00+00:00",
              "c": "2026-07-22T09:00:00+00:00"}
    before = [_story("st_a", "Ferry aground", ["a", "b", "c"], stamps=stamps)]
    after = [_story("st_b", "Ferry aground", ["b", "c"], stamps=stamps)]
    res = churn.churn(before, after)
    assert res["agedOut"] == 1 and res["earlierArrived"] == 0
    assert res["changed"][0]["why"] == "aged out"


def test_an_earlier_arrival_is_reported_separately():
    """Ingestion is not ordered by publication time — GDELT's GKG backfill attaches articles
    published hours or days earlier, and that moves the anchor backwards."""
    stamps = {"old": "2026-07-19T09:00:00+00:00", "b": "2026-07-21T09:00:00+00:00",
              "c": "2026-07-22T09:00:00+00:00"}
    before = [_story("st_b", "Ferry aground", ["b", "c"], stamps=stamps)]
    after = [_story("st_o", "Ferry aground", ["old", "b", "c"], stamps=stamps)]
    res = churn.churn(before, after)
    assert res["earlierArrived"] == 1 and res["agedOut"] == 0
    assert res["changed"][0]["why"] == "earlier arrived"


def test_a_story_that_barely_overlaps_is_not_treated_as_surviving():
    """Being strict about matching would HIDE churn by declaring pairs unmatched, so the bar is
    generous — but two stories sharing one article in ten are not the same story."""
    before = [_story("st_1", "Ferry aground", [f"u{i}" for i in range(10)])]
    after = [_story("st_2", "Ferry aground", ["u0"] + [f"v{i}" for i in range(9)])]
    assert churn.match(before, after) == []


def test_each_story_is_matched_at_most_once():
    """A split would otherwise let one before-story pair with several after-stories and count its
    churn repeatedly."""
    before = [_story("st_1", "Ferry aground", ["a", "b", "c", "d"])]
    after = [_story("st_2", "Ferry A", ["a", "b", "c"]), _story("st_3", "Ferry B", ["a", "b", "d"])]
    assert len(churn.match(before, after)) == 1
