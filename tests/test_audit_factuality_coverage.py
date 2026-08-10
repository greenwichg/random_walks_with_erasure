"""The factuality-coverage probe — examples/audit_factuality_coverage.py.

Phase 0 of the factuality work. It runs against PRODUCTION, so the properties worth pinning are
that it writes nothing, that it counts per outlet IDENTITY rather than per name string (the bug the
sibling registry audit exists to avoid), and that it separates the two blanks that mean different
work: a row that only needs a rater lookup, and a name with no row at all.
"""
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import audit_factuality_coverage as afc   # noqa: E402
import story_service                      # noqa: E402
import store as store_mod                 # noqa: E402


NOW = datetime.now(timezone.utc)


def _seed(st, spec):
    for pub, count in spec:
        slug = "".join(ch for ch in pub if ch.isalnum())
        for i in range(count):
            st.upsert_feed_article(
                canonical_url=f"https://{slug}.example/{i}", url=f"https://{slug}.example/{i}",
                publisher=pub, source_publisher=pub, title=f"{pub} {i}",
                description="x", body=None,
                published_at=(NOW - timedelta(hours=i % 24)).isoformat(),
                source_feed="probe",
                scored={"outlet": pub, "topic": "politics", "lean": 0.0})


@pytest.fixture()
def st(tmp_path):
    return store_mod.Store(f"sqlite:///{tmp_path / 'fact.db'}")


def _analyse(st):
    return afc.analyse(story_service._fetch(st), free=afc.sourced_but_unwritten())


def test_articles_are_weighted_not_outlets(st):
    """Factuality unlocks no coverage-gap claim, so 'how many outlets' is the wrong denominator —
    what matters is how much of what a reader SEES could carry a label. One rated outlet publishing
    heavily must move the baseline more than three quiet unrated ones."""
    _seed(st, [("Boston Globe", 90), ("Totally Unknown Local Herald", 10)])
    res = _analyse(st)
    assert res["ratedOutlets"] == 1 and res["unratedOutlets"] == 1
    assert res["ratedArticles"] == 90, "weighted by volume, not by outlet count"
    assert res["articles"] == 100


def test_the_two_kinds_of_blank_are_reported_apart(st):
    """A registered outlet missing only a verdict is one rater lookup. An unregistered name needs
    identity curation FIRST and may not even be one outlet. Reporting them as one backlog is how a
    worklist becomes an estimate nobody can act on."""
    _seed(st, [("BBC News", 5), ("Totally Unknown Local Herald", 5)])
    res = _analyse(st)
    by = {o["label"]: o for o in res["outlets"]}
    assert by["BBC News"]["registered"] is True
    assert by["Totally Unknown Local Herald"]["registered"] is False
    assert all(o["factuality"] is None for o in res["outlets"]), "neither carries a verdict"


def test_already_sourced_verdicts_are_found_and_not_double_counted(st):
    """The 'free' set is what a curator already read at the rater and left in a comment. It must be
    parsed from the file rather than restated, and it must EXCLUDE anything already in the column —
    a verdict that is already there is done work, not achievable work."""
    free = afc.sourced_but_unwritten()
    assert free, "the registry comments carry sourced verdicts"
    assert "Der Spiegel" in free and free["Der Spiegel"] == "high"
    # Boston Globe already HAS a credibility value, so it is not free work.
    assert "Boston Globe" not in free

    _seed(st, [("Der Spiegel", 7), ("Boston Globe", 3)])
    res = _analyse(st)
    assert res["freeOutletsInWindow"] == 1 and res["freeArticles"] == 7
    assert res["ratedArticles"] == 3, "the already-rated one counts as rated, not as free"


def test_identity_grouping_collapses_name_forms(st):
    """Counted per identity, never per name string. Three spellings of one masthead are one outlet;
    counting them as three would inflate the denominator and understate coverage."""
    _seed(st, [("BBC News", 4), ("Bbc.Co.Uk", 3), ("BBC", 2)])
    res = _analyse(st)
    assert res["names"] >= 2, "several name forms went in"
    assert res["identities"] == 1, f"…and resolved to one outlet: {[o['label'] for o in res['outlets']]}"
    assert res["articles"] == 9


def test_the_probe_writes_nothing(st):
    """It runs against production. Every table it touches must have the same count afterwards."""
    from sqlalchemy import func, select
    _seed(st, [("BBC News", 6), ("Boston Globe", 4)])

    def counts():
        with st.session() as s:
            return tuple(int(s.scalar(select(func.count()).select_from(m)) or 0)
                         for m in (store_mod.FeedArticle,))
    before = counts()
    _analyse(st)
    assert counts() == before
