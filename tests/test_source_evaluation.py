"""Shadow-source evaluation — `examples/source_evaluation.py`, M8 of docs/SCALE_ROADMAP.md.

M5 built a lane that is stored and surfaced nowhere. M8 has to say what an outlet in it is worth,
and the metric every previous audit leaned on — *story participation* — is structurally zero for a
shadow outlet, forever, because shadow rows never enter the builder. So the question becomes a
counterfactual: **would this article have joined a story, had it been allowed to?**

What these tests pin is not the thresholds. It is the four properties that make the counterfactual
trustworthy:

1. **One definition of "same event".** ``would_attach`` must agree with ``clustering.cluster`` on
   every pair, or the harness is measuring a second, private notion of similarity. This suite
   asserts agreement against the clusterer itself rather than against a hand-written expectation.
2. **Determinism.** A counterfactual whose answer varies between runs on identical input silently
   breaks every before/after comparison built on it.
3. **The observation window gates, and gates in the SAFE direction** — too-new reads as
   "no verdict yet", never as a rejection.
4. **``assignment_rate`` never decides anything.** Two invented thresholds have already died against
   data in this audit series; the third is guarded structurally rather than by intention.
"""
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import clustering                   # noqa: E402
import source_evaluation as se      # noqa: E402

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.isoformat()


def _row(title, *, created=None, published=None, fetched=None):
    return {"title": title,
            "createdAt": _iso(created) if created else None,
            "publishedAt": _iso(published) if published else None,
            "fetchedAt": _iso(fetched) if fetched else None}


def _story(sid, headlines, when=NOW):
    return {"id": sid,
            "coverage": [{"headline": h, "publishedAt": _iso(when), "url": f"https://x/{i}"}
                         for i, h in enumerate(headlines)]}


# --------------------------------------------------------------------------- observation window

def test_observed_days_measures_created_at_not_published_at():
    """A backfilling provider inserts articles published days earlier. Reading `publishedAt` would
    report an outlet we first saw an hour ago as observed for a week — the exact failure that would
    let a brand-new source clear the 14-day gate on its first poll."""
    rows = [_row("a", created=NOW - timedelta(hours=1), published=NOW - timedelta(days=9)),
            _row("b", created=NOW - timedelta(hours=2), published=NOW - timedelta(days=30))]
    assert se.observed_days(rows, now=NOW) == pytest.approx(2 / 24, abs=0.01)


def test_observed_days_is_none_when_undatable():
    """`None`, never 0.0 — a missing signal must not read as "seen for zero days", which would be a
    rejection rather than an absence."""
    assert se.observed_days([_row("a"), _row("b")], now=NOW) is None


def test_too_new_is_insufficient_data_not_a_rejection():
    """The safe direction. An outlet observed for three days has told us nothing yet; saying REJECT
    would retire it on the strength of a window that never opened."""
    v, why = se.evaluate({"observedDays": 3.0, "articles": 500, "syndication": 0.9,
                          "hostStability": 0.0})
    assert v == "INSUFFICIENT DATA"
    assert "cannot be reached yet" in why


def test_an_undatable_outlet_is_still_evaluated_on_what_is_known():
    """`observedDays=None` must not silently pass as "long enough" NOR block forever. It skips the
    window gate and the remaining measured gates still apply."""
    v, _ = se.evaluate({"observedDays": None, "articles": 500, "syndication": 0.9,
                        "hostStability": 1.0})
    assert v == "REJECT"


# --------------------------------------------------------------------------- freshness

def test_freshness_is_none_rather_than_zero_when_unmeasurable():
    assert se.freshness_hours([_row("a", published=NOW)]) is None
    assert se.freshness_hours([_row("a", fetched=NOW)]) is None


def test_freshness_is_the_median_lag_so_one_backfill_does_not_set_it():
    rows = [_row("a", published=NOW - timedelta(hours=1), fetched=NOW),
            _row("b", published=NOW - timedelta(hours=2), fetched=NOW),
            _row("c", published=NOW - timedelta(days=40), fetched=NOW)]
    assert se.freshness_hours(rows) == pytest.approx(2.0, abs=0.01)


# --------------------------------------------------------------------------- the counterfactual

def _agrees_with_clusterer(shadow_title, member_titles, when=NOW):
    """Does `would_attach` say the same thing `clustering.cluster` would?

    Built by running the real clusterer over [shadow] + members and asking whether the shadow item
    landed in a group with any of them. That is the ground truth this module exists to approximate,
    so the comparison is against the algorithm rather than against my expectation of it."""
    items = [shadow_title] + list(member_titles)
    groups = clustering.cluster(items, tokens=lambda t: clustering.title_tokens(t),
                                time=lambda _t: when)
    clustered = any(0 in g and len(g) > 1 for g in groups)

    index = se.assignment_index([_story("s1", member_titles, when)])
    attached = se.would_attach(shadow_title, _iso(when), index) is not None
    return clustered, attached


@pytest.mark.parametrize("shadow, members", [
    # the same event, reported by two mastheads — must attach
    ("Vehicle drives into crowd at Berlin pride event",
     ["Berlin pride event canceled after vehicle drives into crowd"]),
    # the pair MIN_SHARED_TOKENS exists for: jaccard 0.50, two shared tokens, different events
    ("Trump wins Ohio", ["Trump wins Iowa"]),
    # nothing in common
    ("Central bank holds rates steady for a third meeting",
     ["Volcano erupts on the southern peninsula overnight"]),
    # below the title-token floor on the shadow side
    ("Markets", ["Markets rally as inflation cools further this quarter"]),
])
def test_would_attach_agrees_with_the_clusterer(shadow, members):
    """The property that makes the harness worth running. If these two ever disagree, the evaluation
    is measuring a private notion of "same event" and its verdicts mean nothing — which is precisely
    why `clustering.pair_admits` was extracted rather than reimplemented here."""
    clustered, attached = _agrees_with_clusterer(shadow, members)
    assert clustered == attached


def test_would_attach_respects_the_clustering_time_window():
    """Same headline, seven days apart: outside `DEFAULT_WINDOW_DAYS`, so it is a different event by
    the clusterer's own rule and must not attach."""
    title = "Central bank holds rates steady for a third consecutive meeting"
    index = se.assignment_index([_story("s1", [title], NOW)])
    assert se.would_attach(title, _iso(NOW), index) == "s1"
    assert se.would_attach(title, _iso(NOW - timedelta(days=7)), index) is None


def test_would_attach_is_deterministic_across_runs():
    """An article matching members of several stories must always report the same one. Iterating a
    set here would make the answer vary between interpreter runs on identical input, and every
    before/after comparison built on the number would move for no reason."""
    title = "Central bank holds rates steady for a third consecutive meeting"
    stories = [_story("s1", [title]), _story("s2", [title]), _story("s3", [title])]
    answers = {se.would_attach(title, _iso(NOW), se.assignment_index(list(stories)))
               for _ in range(8)}
    assert answers == {"s1"}


def test_assignment_index_blocking_does_not_change_the_answer():
    """The inverted index is an optimization: a member sharing no token cannot pass `min_shared`, so
    skipping it is exact. Pinned against a brute-force scan of every member."""
    title = "Storm system brings record rainfall across the eastern seaboard"
    members = [title,
               "Completely unrelated coverage of a municipal budget vote",
               "Record rainfall from storm system floods the eastern seaboard"]
    index = se.assignment_index([_story("s1", members)])
    brute = [m for m in index[0]
             if clustering.pair_admits(clustering.title_tokens(title), m[0], NOW, m[1])]
    assert bool(brute) == (se.would_attach(title, _iso(NOW), index) is not None)


def test_assignment_rate_counts_distinct_stories():
    """An outlet feeding one running story is not the same as one covering the spread, and a bare
    rate cannot tell them apart."""
    a = "Storm system brings record rainfall across the eastern seaboard states"
    b = "Central bank holds rates steady for a third consecutive meeting"
    index = se.assignment_index([_story("s1", [a]), _story("s2", [b])])
    stats = se.assignment_rate([_row(a, published=NOW), _row(a, published=NOW),
                                _row(b, published=NOW), _row("nothing at all like it", published=NOW)],
                               index)
    assert stats == {"articles": 4, "attached": 3, "rate": 0.75, "stories": 2}


def test_assignment_rate_of_zero_is_not_a_rejection():
    """The guard against the third invented threshold. Two have already died against data in this
    series — participation as a quality proxy, then peer count as its excuse — so an outlet that
    would attach to NOTHING still passes every gate, and its rate is reported for a human to read."""
    passing = {"observedDays": 30.0, "articles": 400, "syndication": 0.02,
               "hostStability": 0.99, "assignmentRate": 0.0, "assignmentStories": 0}
    assert se.evaluate(passing)[0] == "PROMOTE TO TIER B"


def test_no_gate_reads_the_assignment_fields():
    """Structural, because intention is not a guard. `evaluate` must reach the same verdict for an
    outlet that would attach everywhere and one that would attach nowhere — every other input held
    equal. A future `if stats["assignmentRate"] < X` fails here the day it is written."""
    base = {"observedDays": 30.0, "articles": 400, "syndication": 0.02, "hostStability": 0.99}
    lo = se.evaluate({**base, "assignmentRate": 0.0, "assignmentStories": 0})[0]
    hi = se.evaluate({**base, "assignmentRate": 1.0, "assignmentStories": 90})[0]
    assert lo == hi


# --------------------------------------------------------------------------- verdicts

def test_volume_floor_is_insufficient_volume_not_reject():
    v, _ = se.evaluate({"observedDays": 30.0, "articles": 4, "syndication": 0.0,
                        "hostStability": 1.0})
    assert v == "INSUFFICIENT VOLUME"


def test_a_republisher_is_rejected_even_when_it_would_attach_everywhere():
    """Order matters: the disqualifying facts are read before the promoting ones. A syndicator that
    attaches everywhere is the WORST case, not a mixed one — its attachments are other publishers'
    coverage counted twice, the same rationale `EXCLUDED_KINDS` already applies to aggregators."""
    v, why = se.evaluate({"observedDays": 30.0, "articles": 400, "syndication": 0.6,
                          "hostStability": 1.0, "rated": True,
                          "assignmentRate": 0.95, "assignmentStories": 200})
    assert v == "REJECT"
    assert "double-count" in why


def test_an_outlet_with_no_stable_host_is_rejected():
    v, why = se.evaluate({"observedDays": 30.0, "articles": 400, "syndication": 0.0,
                          "hostStability": 0.06})
    assert v == "REJECT"
    assert "who published them" in why


def test_a_rated_outlet_is_a_tier_a_CANDIDATE_and_this_function_does_not_promote_it():
    """Tier A promotion needs the clustering counterfactual on the production bars — a whole-corpus
    measurement, not a per-outlet one. The verdict names the run that would settle it rather than
    pretending this function can."""
    v, why = se.evaluate({"observedDays": 30.0, "articles": 400, "syndication": 0.0,
                          "hostStability": 1.0, "rated": True,
                          "assignmentRate": 0.4, "assignmentStories": 30})
    assert v == "TIER A CANDIDATE"
    assert "counterfactual" in why


def test_an_unrated_outlet_promotes_to_tier_b_without_a_counterfactual():
    """The asymmetry that lets Tier B scale to 50,000 while Tier A does not: a Tier B row cannot
    alter the partition, so there is nothing to measure before admitting it."""
    v, why = se.evaluate({"observedDays": 30.0, "articles": 400, "syndication": 0.0,
                          "hostStability": 1.0, "rated": False})
    assert v == "PROMOTE TO TIER B"
    assert "does not vote" in why


def test_the_module_touches_no_store_network_or_environment():
    """Pure by construction: it is policy over numbers, and the moment it reads a store or an env
    var its verdicts stop being reproducible from the table printed beside them — the run's output
    would depend on state the reader cannot see.

    Read from the AST rather than the text, so the module's own prose about *not* reading the
    environment cannot fail the test that enforces it."""
    import ast
    tree = ast.parse((ROOT / "examples" / "source_evaluation.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & {"os", "store", "requests", "urllib", "httpx", "sqlalchemy"}), (
        f"source_evaluation.py must stay pure — imports {sorted(imported)}")
