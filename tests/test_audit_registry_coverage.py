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
import discover                         # noqa: E402


def _url(pub, i=0):
    """One deterministic article URL per (publisher, index), so a row and the coverage entry built
    from it can actually be joined — which the fixture below did not model before."""
    return f"https://{pub.strip().lower().replace(' ', '')}/{i}"


def _row(pub, n=1):
    return [{"publisher": pub, "url": _url(pub, i)} for i in range(n)]


def _story(pairs):
    """``pairs`` is ``[(publisher, leanBucket|None), ...]``.

    **The coverage publisher is PRETTIFIED, and that is the point of the fixture.**
    ``discover.feed_article_to_article`` puts ``engine._prettify(outlet)`` into a story's coverage,
    so the raw row name and the coverage name are the same string only for outlets the registry
    already knows. For an untracked outlet arriving as a bare host they differ
    (``somdnews.com`` -> ``Somdnews.Com``), and an audit joining the two on NAME silently loses it.

    The previous fixture passed the identical string to both sides and gave the rows no URL at all,
    so it could not detect a transformation applied to one side — which is exactly why the audit
    shipped blind to most of the backlog it exists to measure. Modelling the asymmetry is what makes
    these tests able to fail."""
    return {"title": "t", "totalCoverage": len(pairs),
            "coverage": [{"publisher": discover.engine._prettify(p), "leanBucket": b,
                          "url": _url(p, 0)}
                         for p, b in pairs]}


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
    assert set(buckets) == {arc.UNTRACKED, "wire", arc.LOCALITY_ONLY, arc.LOW_CREDIBILITY}
    # Reuters is rated, so it appears in no bucket at all.
    assert "Reuters" not in [r["label"] for r in res["outlets"]]
    assert res["ratedInWindow"] == 1


def test_a_wire_row_is_not_reported_as_a_curation_gap():
    """A machine-generated market-data feed has no editorial stance to rate. Listing it as work
    remaining would put a permanently-blank row on a worklist forever."""
    res = _analyse(_row("MarketBeat", 9), [])
    assert [r["bucket"] for r in res["outlets"]] == ["wire"]
    assert res["buckets"]["wire"]["articles"] == 9


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


def test_unlocks_survive_the_prettify_asymmetry():
    """The defect this audit shipped with for its whole life, and the reason it was invisible.

    ``ingest.Scorer._resolve_outlet`` falls back to ``raw.outlet or _domain_of(raw.url)``, so an
    untracked outlet is STORED under a bare host — ``somdnews.com``. ``feed_article_to_article``
    then puts ``engine._prettify`` of that into the story's coverage — ``Somdnews.Com``. Joining the
    two on NAME therefore misses every untracked host-form outlet, which is most of the backlog:
    sportskeeda.com, decider.com, every local-TV call sign. Registry-resolved outlets are unharmed,
    because ``NPR`` prettifies to ``NPR`` — which is why the numbers always looked plausible.

    Every other test in this file passes the SAME string to both sides, so none of them can fail on
    this. That is the whole lesson: a fixture that does not model an asymmetry cannot detect one.
    """
    raw = "somdnews.com"                                   # as the catalog stores it
    assert discover.engine._prettify(raw) != raw, "fixture assumes prettify changes a host form"

    rows = _row(raw, 2) + _row("CNN") + _row("Fox News")
    story = _story([("CNN", "left"), ("Fox News", "right"), (raw, None)])
    res = _analyse(rows, [story])

    assert res["unmatchedCoverage"] == 0, "every coverage row must join to an identity"
    som = next(r for r in res["outlets"] if r["bucket"] == arc.UNTRACKED)
    assert som["unlocks"] == 1, (
        "an untracked host-form outlet in a one-short story must count as an unlock; joining on the "
        "prettified NAME loses it and reports the backlog as worth nothing")


def test_a_cohort_is_worth_more_than_the_sum_of_its_rows():
    """``unlocks`` prices ONE row at a time — only a story exactly one rating short. A batch is not
    one row at a time: a two-short story with two untracked members is converted by rating both, and
    the per-outlet column credits it to neither. Sizing a curation batch by summing that column
    therefore understates it, which is what ``cohort_unlocks`` exists to correct."""
    a, b = "alpha.example", "beta.example"
    rows = _row(a) + _row(b) + _row("CNN")
    two_short = _story([("CNN", "left"), (a, None), (b, None)])
    res = _analyse(rows, [two_short])
    by_url = res["byUrl"]

    assert all(r["unlocks"] == 0 for r in res["outlets"]), "neither converts it alone"
    ids = {r["identity"] for r in res["outlets"] if r["bucket"] == arc.UNTRACKED}
    joint = arc.cohort_unlocks([two_short], by_url, ids, min_rated=3)
    assert joint["naiveSum"] == 0
    assert joint["joint"] == 1, "rating BOTH converts the story"
    assert joint["coordinationBonus"] == 1


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


def test_every_kind_gets_its_own_bucket():
    """`kind` grew from one value to five, and each is a different reason a lean is the wrong
    question. Folding them into one "not a newsroom" line would hide that an aggregator is excluded
    from clustering while a journal is not."""
    rows = (_row("Zazoom") + _row("Nature.Com") + _row("Reddit.Com")
            + _row("Unitaid.Eu") + _row("MarketBeat"))
    got = {r["label"]: r["bucket"] for r in _analyse(rows, [])["outlets"]}
    assert got == {"Zazoom": "aggregator", "Nature.Com": "research", "Reddit.Com": "forum",
                   "Unitaid.Eu": "org", "MarketBeat": "wire"}


def test_a_rated_aggregator_is_still_an_aggregator():
    """Google News carries an MBFC Left-Center rating derived from the sources it surfaces. The
    bucket is decided by WHAT IT IS, not by whether someone rated it — the clearest case that a
    rating and a right-to-vote are separate questions."""
    res = _analyse(_row("News.Google.Com", 12), [])
    row = res["outlets"][0]
    assert row["bucket"] == "aggregator" and row["canonical"] == "Google News"
