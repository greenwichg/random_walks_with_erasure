"""The registry-coverage audit (examples/audit_registry_coverage.py).

It answers "how much is left to curate, and what would curating it buy" — which three existing
audits each answer a piece of. The two things that make it correct rather than merely informative
are pinned here: every count is per OUTLET IDENTITY rather than per name string, and an outlet the
engine excludes from the rated sample must not be counted as rated.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import audit_registry_coverage as arc   # noqa: E402


def _row(pub, n=1):
    return [{"publisher": pub} for _ in range(n)]


def _story(pairs):
    """``pairs`` is ``[(publisher, leanBucket|None), ...]``."""
    return {"title": "t", "totalCoverage": len(pairs),
            "coverage": [{"publisher": p, "leanBucket": b, "url": f"u{i}"}
                         for i, (p, b) in enumerate(pairs)]}


def _analyse(rows, stories, min_rated=3):
    return arc.analyse(rows, stories, min_rated=min_rated)


def test_two_name_forms_of_one_outlet_count_once():
    """The defect that makes a volume ranking lie. `outlet_coverage` counts name STRINGS, so one
    masthead arriving as a host and a bare name is two entries and its volume is split across them."""
    rows = _row("Somdnews.Com", 4) + _row("Somdnews", 3)
    res = _analyse(rows, [])
    untracked = [r for r in res["outlets"] if r["bucket"] == arc.UNTRACKED]
    assert len(untracked) == 1
    assert untracked[0]["articles"] == 7           # 4 + 3, not two rows of 4 and 3
    assert len(untracked[0]["forms"]) == 2


def test_each_unrated_outlet_lands_in_exactly_one_bucket():
    """The buckets are a partition, not overlapping labels — otherwise "how much is left" double
    counts, which is the whole question."""
    rows = (_row("Somdnews.Com") + _row("MarketBeat") + _row("Brisbanetimes.Com.Au")
            + _row("Tass.Com") + _row("Reuters"))
    res = _analyse(rows, [])
    buckets = [r["bucket"] for r in res["outlets"]]
    assert len(buckets) == len(set(r["identity"] for r in res["outlets"]))
    assert set(buckets) == {arc.UNTRACKED, arc.WIRE, arc.LOCALITY_ONLY, arc.LOW_CREDIBILITY}
    # Reuters is rated, so it appears in no bucket at all.
    assert "Reuters" not in [r["label"] for r in res["outlets"]]
    assert res["ratedInWindow"] == 1


def test_a_wire_row_is_not_reported_as_a_curation_gap():
    """A machine-generated market-data feed has no editorial stance to rate. Listing it as work
    remaining would put a permanently-blank row on a worklist forever."""
    res = _analyse(_row("MarketBeat", 9), [])
    assert [r["bucket"] for r in res["outlets"]] == [arc.WIRE]
    assert res["buckets"][arc.WIRE]["articles"] == 9


def test_a_low_credibility_outlet_is_reported_apart_from_the_unrated(reset=None):
    """It IS rated — the lean is in the file. It just does not vote. Filing it with the unrated
    would say the registry has nothing on TASS, which is the opposite of true."""
    res = _analyse(_row("Tass.Com", 2), [])
    row = res["outlets"][0]
    assert row["bucket"] == arc.LOW_CREDIBILITY and row["canonical"] == "TASS"


def test_an_ambiguous_name_is_not_offered_as_curatable():
    """`The Local` runs national editions, so the bare name cannot be placed without guessing which.
    The fix is a row per edition, not a rating — a different job, so a different bucket."""
    rows = _row("Thelocal.Es") + _row("Thelocal.Fr") + _row("Thelocal")
    res = _analyse(rows, [])
    by_label = {r["label"]: r["bucket"] for r in res["outlets"]}
    assert by_label["Thelocal"] == arc.AMBIGUOUS
    assert by_label["Thelocal.Es"] == arc.UNTRACKED    # the editions themselves are curatable


def test_unlocks_count_only_stories_one_rating_short():
    """A single registry row converts a one-short story. A two-short story needs coordinated
    curation and is counted as an assist, because promising it to a curator would be a lie."""
    rows = _row("Somdnews.Com", 2)
    one_short = _story([("CNN", "left"), ("Fox News", "right"), ("Somdnews.Com", None)])
    two_short = _story([("CNN", "left"), ("Somdnews.Com", None), ("Batleynews", None)])
    res = _analyse(rows + _row("CNN") + _row("Fox News") + _row("Batleynews"),
                   [one_short, two_short])
    som = next(r for r in res["outlets"] if r["label"] == "Somdnews.Com")
    assert som["unlocks"] == 1 and som["assists"] == 1


def test_a_low_credibility_outlet_does_not_fill_the_rated_slot():
    """The bug this audit shipped with, caught by its own fixture. Reading `leanBucket` alone counts
    TASS as the third rated publisher, so the story looks fully supported and the untracked outlet
    beside it reports ZERO unlocks — the worklist would hide exactly the row a curator should add."""
    story = _story([("CNN", "left"), ("Fox News", "right"),
                    ("Tass.Com", "right"), ("Somdnews.Com", None)])
    res = _analyse(_row("CNN") + _row("Fox News") + _row("Tass.Com") + _row("Somdnews.Com"),
                   [story])
    som = next(r for r in res["outlets"] if r["label"] == "Somdnews.Com")
    assert som["unlocks"] == 1, "TASS must not count toward the rated floor"


def test_an_empty_catalog_is_not_a_crash():
    res = _analyse([], [])
    assert res["names"] == 0 and res["identities"] == 0 and res["outlets"] == []


def test_registry_and_window_totals_are_reported_separately():
    """A reader asked whether "fully tracked and rated: 183" was the registry's 243, and the report
    gave them no way to tell — the line sat under two registry-sounding headings and measured the
    FEED. Both numbers now travel together and are named for what they measure."""
    res = _analyse(_row("Reuters") + _row("Somdnews.Com"), [])
    assert res["registryRated"] >= res["ratedInWindow"]
    assert res["ratedInWindow"] == 1          # only Reuters, of 240-odd rated rows
    assert res["registryRated"] > 100         # the file is far bigger than any one window
    assert "tracked_and_rated" not in res, "the ambiguous key name is gone, not aliased"
