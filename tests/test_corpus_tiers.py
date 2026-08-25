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
