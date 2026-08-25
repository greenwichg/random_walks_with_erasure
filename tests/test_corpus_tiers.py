"""The clustering-corpus boundary — `examples/corpus.py`, M1 of docs/SCALE_ROADMAP.md.

Two halves, and they are switched independently on purpose:

* the **tier filter**, off by default and byte-identical off;
* the **budget report**, which is always on because it is the defect fix — a row cap that truncates
  the clustering window has been silent since it existed, and its only symptom is fewer stories.

The tests below pin the properties that make the change admissible, not the numbers that motivated
it. The one number they do pin is the off-by-one in the truncation test: `total == cap` is NOT a
breach, and a probe that reported one would cry wolf on every window that happens to be exactly
full.
"""
import logging
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import corpus                       # noqa: E402
import obs_metrics                  # noqa: E402
from pagination import OffsetPagination   # noqa: E402


def _row(url, publisher, published="2026-08-25T12:00:00+00:00"):
    return {"canonicalUrl": url, "url": url, "publisher": publisher, "publishedAt": published,
            "title": "t", "scored": {}}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Every test states its own configuration. Without this a leaked RWE_CORPUS_* from the
    developer's shell would silently change what these assert."""
    for k in ("RWE_CORPUS_TIER_B", "RWE_CORPUS_SHADOW", "RWE_CORPUS_TIER_A_BUDGET"):
        monkeypatch.delenv(k, raising=False)


def _sink():
    """A log collector in the `(level, event, **fields)` shape `corpus._default_log` emits."""
    out = []
    return out, lambda level, event, **fields: out.append((level, event, fields))


# --------------------------------------------------------------------------- #
# OFF is byte-identical — structurally, not merely observably
# --------------------------------------------------------------------------- #
def test_unconfigured_select_returns_the_same_list_object():
    """Not "returns an equal list" — the SAME object. An identity assertion cannot be satisfied by
    a filter that happens to keep everything, so it rules out a whole class of near-misses (a copy
    with rows re-ordered, a dict rebuilt with one key normalised) that an equality check would pass."""
    rows = [_row("https://npr.org/a", "NPR"), _row("https://foxnews.com/b", "Fox News")]
    assert corpus.select(rows, total=2, cap=100) is rows


def test_unconfigured_select_resolves_no_outlet_at_all(monkeypatch):
    """Off must cost nothing, and the way to prove that is to make the expensive thing explode.

    `story_service` calls the registry three times per article already (is_wire, is_aggregator,
    is_low_credibility); a fourth per-row resolution added by a switched-off feature would be a
    real cost paid by a deployment that asked for nothing."""
    def boom(*a, **k):
        raise AssertionError("select() resolved an outlet while tiering was off")
    monkeypatch.setattr(corpus, "default_registry", boom)
    rows = [_row("https://npr.org/a", "NPR")]
    assert corpus.select(rows, total=1, cap=100) is rows
    assert corpus.tier_of("NPR", "https://npr.org/a") == "A"


def test_everything_defaults_to_tier_a():
    """Grandfathering. An outlet nobody has classified is in the clustering corpus, because it was
    yesterday — M1 installs the boundary, it does not move anyone across it."""
    assert corpus.DEFAULT_TIER == "A"
    for name in ("NPR", "Some Outlet Nobody Curated", "", None):
        assert corpus.tier_of(name, "https://example.test/x") == "A"


# --------------------------------------------------------------------------- #
# The tier filter
# --------------------------------------------------------------------------- #
def test_a_tier_b_outlet_leaves_the_clustering_corpus(monkeypatch):
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "Fox News")
    rows = [_row("https://npr.org/a", "NPR"), _row("https://foxnews.com/b", "Fox News")]
    report = {}
    kept = corpus.select(rows, total=2, cap=100, report_out=report)
    assert [r["publisher"] for r in kept] == ["NPR"]
    assert report["droppedTierB"] == 1 and report["kept"] == 1


def test_tier_membership_is_identity_not_the_string_that_was_typed(monkeypatch):
    """Configuring a domain moves the outlet the registry resolves that domain to — so an article
    that arrives under the display name is moved too, and vice versa. Blocking by raw string could
    never do that, which is the measured reason `ingest.is_blocked_from_catalog` resolves first."""
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "foxnews.com")
    assert corpus.tier_of("Fox News", "https://example.test/x") == "B"
    assert corpus.tier_of("Fox News", None) == "B"


def test_matching_is_two_sided(monkeypatch):
    """Name OR url. The measured case: 499 of 671 obituary articles arrive under the parent
    masthead's name with an obits.* URL, so a name-only rule admits them under an identity that is
    not theirs."""
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "Fox News")
    assert corpus.tier_of("Somebody Else", "https://foxnews.com/politics/x") == "B"
    assert corpus.tier_of("Fox News", "https://npr.org/a") == "B"
    assert corpus.tier_of("Somebody Else", "https://npr.org/a") == "A"


def test_an_unregistered_entry_with_a_dot_is_matched_as_a_domain(monkeypatch):
    """The case that matters most in practice: the outlets worth tiering are usually ones nobody
    has curated a registry row for. Subdomain-tolerant, and never a suffix collision."""
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "aggregator.example")
    assert corpus.tier_of("X", "https://aggregator.example/a") == "B"
    assert corpus.tier_of("X", "https://news.aggregator.example/a") == "B"
    assert corpus.tier_of("X", "https://notaggregator.example/a") == "A"


def test_shadow_wins_over_tier_b(monkeypatch):
    """A conflicting configuration must fail toward LESS exposure. Shadow is surfaced nowhere;
    Tier B is searchable. Resolving the conflict the other way would publish an outlet that two
    settings disagree about."""
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "NPR")
    monkeypatch.setenv("RWE_CORPUS_SHADOW", "NPR")
    assert corpus.tier_of("NPR", "https://npr.org/a") == "shadow"


def test_tier_index_shows_a_typo_matching_nothing(monkeypatch):
    """A tier list that quietly does nothing is the worst way to find out it has a typo, so what
    the setting was UNDERSTOOD to mean is readable. `Fxo News` is neither a registry identity nor a
    domain, so it lands in neither set — visibly."""
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "Fxo News, foxnews.com")
    canonicals, hosts = corpus.tier_index()["B"]
    assert canonicals == frozenset({"Fox News"})
    assert hosts == frozenset()


# --------------------------------------------------------------------------- #
# The budget report — the half that is always on
# --------------------------------------------------------------------------- #
def test_a_truncated_window_is_loud():
    rows = [_row("https://npr.org/a", "NPR", "2026-08-25T12:00:00+00:00"),
            _row("https://npr.org/b", "NPR", "2026-08-25T06:00:00+00:00")]
    logs, log = _sink()
    report = {}
    corpus.select(rows, total=5000, cap=2, window_start="2026-08-19T12:00:00+00:00",
                  log=log, report_out=report)

    assert report["capBound"] is True
    levels = {e for lvl, e, _ in logs if lvl >= logging.WARNING}
    assert "clustering_corpus_cap_bound" in levels, "a truncated window must WARN, not whisper"

    fields = next(f for _, e, f in logs if e == "clustering_corpus_cap_bound")
    assert fields["dropped"] == 4998
    # The number that makes it legible: 6 days asked for, 6 hours achieved.
    assert report["requestedWindowHours"] == 144.0
    assert report["effectiveWindowHours"] == 6.0


def test_a_window_that_exactly_fills_the_cap_is_not_a_breach():
    """`total == cap` means everything in the window was returned. Keying the detector on
    `len(rows) == cap` instead of `total > cap` would fire here and report a truncation that did
    not happen — and a warning that cries wolf is one an operator learns to ignore."""
    rows = [_row("https://npr.org/a", "NPR")]
    logs, log = _sink()
    report = {}
    corpus.select(rows, total=1, cap=1, log=log, report_out=report)
    assert report["capBound"] is False
    assert not [e for _, e, _ in logs if e == "clustering_corpus_cap_bound"]


def test_the_cap_bound_counter_moves():
    """The log line answers "what happened just now"; the counter answers "how often". Production
    found the retention defect only after a duration was printed — a count that nobody exports is
    the same failure in a different costume."""
    before = obs_metrics.snapshot()["counters"].get("clustering_corpus_cap_bound_total", 0)
    corpus.select([_row("https://npr.org/a", "NPR")], total=99, cap=1, log=lambda *a, **k: None)
    after = obs_metrics.snapshot()["counters"].get("clustering_corpus_cap_bound_total", 0)
    assert after == before + 1


def test_over_budget_is_reported_but_drops_nothing(monkeypatch):
    """A WARNING threshold, not a gate. Silently trimming the corpus to fit a CPU budget would be
    the same silent-truncation defect this milestone exists to remove."""
    monkeypatch.setenv("RWE_CORPUS_TIER_A_BUDGET", "2")
    rows = [_row(f"https://npr.org/{i}", "NPR") for i in range(5)]
    logs, log = _sink()
    report = {}
    kept = corpus.select(rows, total=5, cap=100, log=log, report_out=report)

    assert len(kept) == 5, "over budget must not drop a single article"
    assert report["overBudget"] is True
    assert [e for lvl, e, _ in logs if e == "clustering_corpus_over_budget" and lvl >= logging.WARNING]


def test_the_report_names_which_bound_is_operative(monkeypatch):
    """`RWE_STORIES_MAX_SCAN` is documented as a memory backstop "far above a normal window". At
    the shipped 60,000 it is BELOW the 83,000 CPU budget, so the backstop is in fact the binding
    constraint. Printing which one binds means nobody has to work that out from two docstrings."""
    rows = [_row("https://npr.org/a", "NPR")]
    report = {}
    corpus.select(rows, total=1, cap=60_000, report_out=report)
    assert report["budget"] == corpus.DEFAULT_TIER_A_BUDGET == 83_000
    assert report["binding"] == "cap", "60,000 < 83,000, so the row cap binds first"

    monkeypatch.setenv("RWE_CORPUS_TIER_A_BUDGET", "1000")
    corpus.select(rows, total=1, cap=60_000, report_out=report)
    assert report["binding"] == "budget"


def test_the_report_says_when_the_cap_ran_before_the_tier_filter(monkeypatch):
    """The positional cap is applied in SQL, upstream of the semantic filter, so Tier B rows still
    count against it. That is M2's job; until then the report states it rather than letting a
    reader assume the boundary is doing more than it is."""
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "Fox News")
    report = {}
    corpus.select([_row("https://foxnews.com/b", "Fox News")], total=99, cap=1,
                  log=lambda *a, **k: None, report_out=report)
    assert report["capBoundBeforeTier"] is True


def test_an_unparseable_timestamp_reports_none_rather_than_a_wrong_number():
    """The house fail-honest rule: a missing signal is excluded, never defaulted. A window figure
    computed from a garbage date would be a wrong number with a decimal point on it."""
    report = {}
    corpus.select([_row("https://npr.org/a", "NPR", published="not-a-date")],
                  total=1, cap=100, window_start="2026-08-19T12:00:00+00:00", report_out=report)
    assert report["effectiveFrom"] is None
    assert report["requestedWindowHours"] is None
    assert report["effectiveWindowHours"] is None


def test_an_empty_window_reports_without_raising():
    report = {}
    assert corpus.select([], total=0, cap=100, window_start="2026-08-19T12:00:00+00:00",
                         report_out=report) == []
    assert report["kept"] == 0 and report["capBound"] is False


# --------------------------------------------------------------------------- #
# M2 — the SQL prefilter: the row cap must bound TIER A, not the mixture
# --------------------------------------------------------------------------- #
import story_service                                                     # noqa: E402
import store as store_mod                                                # noqa: E402
from datetime import datetime, timedelta, timezone                       # noqa: E402


def _seed(st, *, tier_a: int, tier_b: int):
    """Tier B rows are all NEWER than every Tier A row. That ordering is the test: `_fetch` sorts
    newest-first and truncates at the cap, so without a prefilter a cap smaller than `tier_b`
    cannot reach a single Tier A article."""
    now = datetime.now(timezone.utc)

    def add(i, host, pub, minutes):
        cu = f"https://{host}/{i}"
        st.upsert_feed_article(
            canonical_url=cu, url=cu, publisher=pub, source_publisher=pub,
            title=f"Some headline number {i} about a thing", description="ctx", body=None,
            published_at=(now - timedelta(minutes=minutes)).isoformat(), source_feed="feed://x",
            scored={"article_id": cu, "outlet": pub, "category": "Politics", "lean": 0.0})

    for i in range(tier_b):
        add(i, "tierb.example", "tierb.example", 1)          # newest
    for i in range(tier_a):
        add(1000 + i, "npr.org", "NPR", 60)                  # older


def test_the_row_cap_bounds_tier_a_not_the_mixture(monkeypatch):
    """The M2 behaviour, stated as starkly as the data allows.

    Fifty Tier B articles sit newer than five Tier A ones, under a cap of ten. Without the SQL
    prefilter the cap fills entirely with Tier B and the clustering corpus is EMPTY — the tier
    filter dutifully removes them all and reports that it did, having already lost the window. With
    the prefilter the excluded rows never consume cap and all five Tier A articles survive.

    At 50,000 sources, where Tier B is most of the corpus, that difference is the whole milestone."""
    st = store_mod.Store("sqlite://")
    _seed(st, tier_a=5, tier_b=50)
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "tierb.example")

    # WITHOUT the prefilter: the Python pass is correct and useless — the window is already gone.
    raw, _total = st.search_feed_articles(
        sort="newest", pagination=OffsetPagination.from_params(10, 0, max_limit=10))
    assert len(corpus.select(raw, total=55, cap=10, log=lambda *a, **k: None)) == 0

    # WITH it: the cap sees only Tier A.
    report = {}
    kept = story_service._fetch(st, max_scan=10, report_out=report)
    assert len(kept) == 5, "every Tier A article must survive a cap that Tier B would have eaten"
    assert {r["publisher"] for r in kept} == {"NPR"}
    assert report["tierResidue"] == 0, "the prefilter expressed the whole tier; nothing fell through"
    assert report["capBound"] is False, "the cap no longer binds once it counts Tier A only"


def test_the_prefilter_is_a_subset_of_what_select_would_drop(monkeypatch):
    """The invariant that keeps SQL an optimization rather than a second policy: the prefilter may
    MISS rows (they fall through to the Python pass), and must never remove one that pass keeps.

    Checked by building the corpus both ways at a cap large enough that neither truncates, and
    demanding the same kept set."""
    st = store_mod.Store("sqlite://")
    _seed(st, tier_a=5, tier_b=50)
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "tierb.example")

    with_prefilter = {r["canonicalUrl"] for r in story_service._fetch(st, max_scan=1000)}
    raw, total = st.search_feed_articles(
        sort="newest", pagination=OffsetPagination.from_params(1000, 0, max_limit=1000))
    without = {r["canonicalUrl"]
               for r in corpus.select(raw, total=total, cap=1000, log=lambda *a, **k: None)}
    assert with_prefilter == without


def test_a_null_publisher_survives_the_exclusion(monkeypatch):
    """`lower(NULL) NOT IN (...)` evaluates to NULL, not TRUE, so a bare NOT IN silently drops every
    row that has no publisher — a filter removing rows it was never asked about. The explicit
    IS NULL arm keeps them, and this is the test that fails without it."""
    st = store_mod.Store("sqlite://")
    cu = "https://unknown.example/x"
    st.upsert_feed_article(canonical_url=cu, url=cu, publisher=None, source_publisher=None,
                           title="A headline with no publisher at all", description="ctx",
                           body=None, published_at=datetime.now(timezone.utc).isoformat(),
                           source_feed="feed://x", scored={"article_id": cu})
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "tierb.example")
    assert [r["canonicalUrl"] for r in story_service._fetch(st)] == [cu]


def test_off_adds_no_sql_term_at_all():
    """Byte-identical off, at the SQL layer too: an empty exclusion set must produce the same query,
    not a `NOT IN ()` that a database is free to interpret however it likes."""
    assert corpus.sql_exclusions() == frozenset()
    st = store_mod.Store("sqlite://")
    _seed(st, tier_a=3, tier_b=3)
    plain, t1 = st.search_feed_articles(sort="newest")
    empty, t2 = st.search_feed_articles(sort="newest", exclude_publishers=frozenset())
    assert t1 == t2 == 6
    assert [r["canonicalUrl"] for r in plain] == [r["canonicalUrl"] for r in empty]


def test_a_host_configured_tier_matches_a_publisher_stored_as_that_host(monkeypatch):
    """`ingest.Scorer._resolve_outlet` falls back to `raw.outlet or _domain_of(raw.url)`, so an
    outlet the registry does not know is routinely STORED under its bare domain. Matching the host
    set against the publisher string is what lets those rows reach the SQL prefilter — and it is
    what makes `sql_exclusions` a provable subset."""
    monkeypatch.setenv("RWE_CORPUS_TIER_B", "tierb.example")
    assert corpus.tier_of("tierb.example", "https://somewhere-else.test/a") == "B"
    assert "tierb.example" in corpus.sql_exclusions()
    # A display name that merely contains a dot still routes to the NAME path, not the host path.
    assert corpus.tier_of("Some Outlet Ltd.", "https://somewhere-else.test/a") == "A"
