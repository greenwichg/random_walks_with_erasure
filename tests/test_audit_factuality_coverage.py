"""The factuality-coverage probe — examples/audit_factuality_coverage.py.

Phase 0 of the factuality work. It runs against PRODUCTION, so the properties worth pinning are
that it writes nothing, that it counts per outlet IDENTITY rather than per name string (the bug the
sibling registry audit exists to avoid), and that it separates the two blanks that mean different
work: a row that only needs a rater lookup, and a name with no row at all.
"""
import csv
import io
import math
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import audit_factuality_coverage as afc   # noqa: E402
import outlet_registry                    # noqa: E402
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


def _unrated(count=1, min_domains=1):
    """Outlets that still need a lookup, CHOSEN FROM THE REGISTRY rather than named.

    Naming one couples the test to which tranche happened to run last. These tests were written
    against Deutsche Welle and BuzzFeed News; the next two tranches rated both, and the tests
    failed for the data being improved. That is the third time this pattern has cost a false
    failure in this file, so the selection is derived now.

    "Unrated" has to mean what the CODE means by it: the probe counts EITHER column as a verdict,
    so an outlet carrying only a legacy `credibility` is not work and never reaches the worklist.
    Selecting on `factuality` alone picked Aftonbladet and expected a row that correctly never
    came."""
    index = afc._registry_index()
    picked = []
    for o in outlet_registry.default_registry().outlets():
        if o.factuality or o.credibility or math.isnan(o.lean):
            continue
        _, domains = index.get(o.canonical, (0, []))
        if len(domains) >= min_domains:
            picked.append(o.canonical)
        if len(picked) == count:
            return picked
    raise AssertionError(f"registry has no {count} unrated outlet(s) with >={min_domains} domains")


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
    worklist becomes an estimate nobody can act on.

    The registered half is CHOSEN FROM THE REGISTRY rather than named. It used to be BBC News,
    which made the test quietly depend on the BBC being unrated — so a tranche that rated the BBC
    failed it for doing exactly the right thing. What is durable is that the two blanks are told
    apart, so the fixture picks whatever outlet still has the first kind of blank."""
    # Neither column: the probe reports whichever one carries a verdict, so an outlet with a
    # legacy `credibility` is not blank as far as this report is concerned.
    unrated = next(o for o in outlet_registry.default_registry().outlets()
                   if not o.factuality and not o.credibility)
    _seed(st, [(unrated.canonical, 5), ("Totally Unknown Local Herald", 5)])
    res = _analyse(st)
    by = {o["label"]: o for o in res["outlets"]}
    assert by[unrated.canonical]["registered"] is True, "a registry row means identity is settled"
    assert by["Totally Unknown Local Herald"]["registered"] is False
    assert by[unrated.canonical]["factuality"] is None, "…but the verdict is still outstanding"
    assert by["Totally Unknown Local Herald"]["factuality"] is None


def test_already_sourced_verdicts_are_found_and_not_double_counted(tmp_path):
    """The 'free' set is what a curator already read at the rater and left in a comment. It must be
    parsed from the file rather than restated, and it must EXCLUDE anything already written — a
    verdict in the column is done work, not achievable work.

    Driven off a FIXTURE registry, not the shipped one. An earlier version asserted that a specific
    outlet was still unwritten, which made it a test of how much of the backlog happened to be
    outstanding: Phase 2 wrote those verdicts and the test failed for being right. What is durable
    is the RULE, so that is what is pinned."""
    fixture = tmp_path / "reg.csv"
    fixture.write_text(
        "# notes recorded during a rating tranche:\n"
        "#   Written Outlet   : Left-Center (MBFC LC, factual High)\n"
        "#   Pending Outlet   : Right-Center (MBFC RC, factual MIXED)\n"
        "#   Absent Outlet    : Left (MBFC L, factual High)\n"
        "canonical,lean,aliases,country,region,city,scope,kind,credibility,factuality,factuality_source\n"
        "Written Outlet,-1,written.example,,,,,,,high,mbfc\n"
        "Pending Outlet,1,pending.example,,,,,,,,\n",
        encoding="utf-8")

    free = afc.sourced_but_unwritten(str(fixture))
    assert free == {"Pending Outlet": "mixed"}, (
        "only the row that EXISTS and is still blank is free work — a written one is done, "
        f"and one with no row cannot be filled in: {free}")


def test_the_shipped_registry_has_no_unwritten_verdicts_left(st):
    """Phase 2's completion condition, kept as a live check: every verdict recorded in a comment has
    been written into the column. A new rating tranche that records verdicts and forgets to enter
    them will show up here as a non-empty set."""
    assert afc.sourced_but_unwritten() == {}, (
        "verdicts are sitting in the registry's comments unwritten — run the Phase 2 backfill")


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


def test_the_registry_summary_counts_either_column(st):
    """It counted `credibility` alone, so after the Phase 2 backfill the same report said the FILE
    was untouched (70 rows) while the FEED line showed the 41 new verdicts working. A report that
    contradicts itself is worse than one that is merely incomplete — an operator cannot tell which
    half to believe."""
    _seed(st, [("BBC News", 1)])
    res = _analyse(st)
    assert res["registryWithFactuality"] == (
        res["registryFactualityColumn"] + res["registryCredibilityOnly"]), "the split must add up"
    assert res["registryFactualityColumn"] > 0, "Phase 2 wrote verdicts into the new column"
    assert res["registryWithFactuality"] > res["registryCredibilityOnly"], (
        "the total must exceed the legacy-only subset, or the new column is not being counted")


# --------------------------------------------------------------------------------------------- #
# The emitted worklist — what the NEXT tranche of rater lookups should be.
# --------------------------------------------------------------------------------------------- #
def test_the_worklist_is_ranked_by_article_volume_not_registry_order(st):
    """The reason this is emitted from the probe rather than read off the registry.

    The file holds hundreds of rows carrying a lean and no verdict, but most publish nothing into
    the window, so working it in file order spends rater requests on outlets that buy no coverage.
    Only the feed knows which blanks are expensive, and the sheet has to arrive in that order."""
    quiet, loud = _unrated(2)
    _seed(st, [(quiet, 10), (loud, 40)])
    sheet = afc.worksheet_rows(_analyse(st)["outlets"])
    vols = [r["articles_in_window"] for r in sheet]
    assert vols == sorted(vols, reverse=True), f"not volume-ranked: {sheet}"
    assert sheet[0]["canonical"] == loud


def test_an_unregistered_name_never_enters_the_worklist_however_loud(st):
    """Volume is the ranking, not the entry condition. A name with no row cannot be looked up —
    there is nothing to write a verdict against, and it may not even be one outlet — so the
    highest-volume unknown in the feed must still be absent."""
    known, = _unrated(1)
    _seed(st, [("Totally Unknown Local Herald", 999), (known, 1)])
    sheet = afc.worksheet_rows(_analyse(st)["outlets"])
    assert [r["canonical"] for r in sheet] == [known]


def test_an_outlet_that_already_has_a_verdict_is_not_re_looked_up(st):
    """The worklist is remaining work. Re-emitting a written row would spend a rater request to
    learn something the file already records."""
    known, = _unrated(1)
    _seed(st, [("Reuters", 50), (known, 1)])
    sheet = afc.worksheet_rows(_analyse(st)["outlets"])
    assert "Reuters" not in [r["canonical"] for r in sheet], "Reuters already carries a verdict"
    assert known in [r["canonical"] for r in sheet], "…while the unrated one is still work"


def test_every_emitted_row_is_ready_for_the_fetcher(st):
    """The sheet is fed straight to the MBFC fetcher and written back to the registry afterwards,
    so each row needs a domain to search on and the line number to write back to. A row missing
    either is a manual task wearing an automated row's clothes."""
    a, b = _unrated(2)
    _seed(st, [(a, 5), (b, 3)])
    sheet = afc.worksheet_rows(_analyse(st)["outlets"])
    assert sheet
    for r in sheet:
        assert r["primary_domain"] and "." in r["primary_domain"], r
        # The search needs one domain; the GATE needs them all, so the first must lead.
        assert r["mbfc_search_url"].endswith(r["primary_domain"].split("|")[0]), r
        assert r["registry_line"] > 0, r
        assert r["mbfc_search_url"].endswith(r["primary_domain"]), r
        assert r["factuality_TO_FILL"] == "", "a worklist row carries no verdict yet"
        assert r["factuality_source"] == "mbfc"


def test_the_worklist_can_be_streamed_to_stdout(tmp_path, capsys):
    """`-` means stdout. The probe's natural home is inside the deployed container, where a file
    has to be copied back out before it is any use — one command beats three. Driven through
    `main` because the delimiters are the contract: the CSV is printed among a human-readable
    report, so it has to be separable from it without guessing where it starts."""
    known, = _unrated(1)
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    _seed(store_mod.Store(url), [(known, 4)])

    assert afc.main(["--db", url, "--emit-worklist", "-"]) == 0
    out = capsys.readouterr().out
    assert "--- BEGIN WORKLIST CSV" in out and "--- END WORKLIST CSV ---" in out

    body = out.split("--- BEGIN WORKLIST CSV")[1].split("---", 1)[1]
    body = body.split("--- END WORKLIST CSV ---")[0].strip()
    rows = list(csv.DictReader(io.StringIO(body)))
    assert [r["canonical"] for r in rows] == [known]
    assert rows[0]["articles_in_window"] == "4"
    assert rows[0]["factuality_TO_FILL"] == "", "a worklist row carries no verdict yet"


def test_the_worklist_writes_nothing_to_the_database(st):
    """It is emitted from a production read. Same guarantee as the rest of the probe."""
    from sqlalchemy import func, select
    _seed(st, [(_unrated(1)[0], 5)])

    def count():
        with st.session() as s:
            return int(s.scalar(select(func.count()).select_from(store_mod.FeedArticle)) or 0)
    before = count()
    afc.worksheet_rows(_analyse(st)["outlets"])
    assert count() == before


def test_every_alias_domain_reaches_the_worksheet(st):
    """A rater lists whichever domain it treats as the outlet's home, and that is not always the
    one we list first. BuzzFeed News is curated `buzzfeed.com|buzzfeednews.com`; MBFC sources it to
    the second, so a sheet carrying only the first made the lookup reject its own correct page —
    four outlets in one tranche failed this way. The alias cell is the curated claim that these
    are one outlet, so the whole cell travels."""
    multi, = _unrated(1, min_domains=2)
    _seed(st, [(multi, 9)])
    sheet = afc.worksheet_rows(_analyse(st)["outlets"])
    row = next(r for r in sheet if r["canonical"] == multi)
    assert "|" in row["primary_domain"], f"only one domain survived: {row['primary_domain']}"
    first = row["primary_domain"].split("|")[0]
    assert row["mbfc_search_url"].endswith(first), "the query uses the first domain"
