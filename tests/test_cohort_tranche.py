"""M11 — the tranche counterfactual: what admitting a specific set of hosts would cost.

`docs/SOURCE_PIPELINE_50K_AUDIT.md` §7.5c recommends admitting the 1,173 candidates in small
tranches, each measured with `audit_source_cohort.py`'s bar — *"OTHER articles that LOST their
story"*. **That recommendation did not execute**: the tool selected its own cohort by volume floor
and had no way to take a host list. A recommendation pointing at a tool that cannot accept its input
is the "diagnostic nothing invokes" defect this repository keeps finding, committed in a document
rather than in code.

## The predicate is borrowed, not re-derived

Admission assigns a tier to a **host**, and `corpus._matches` resolves a host set against the
article's URL *and* — when the publisher string looks like a host — against the publisher. A
counterfactual that filtered on `_identity(reg, row) in drop` would be a second definition of what
shadowing a host does, and would disagree with production on exactly the rows that are hardest to
reason about: subdomains, and outlets stored under their bare domain. So `_shadow_predicate` builds a
host-only index and calls `corpus._matches` — the same function `select()` calls. The differential
test below is what pins that.

## Why "cheapest" is ascending volume, and why that is still only an ordering

`source_discovery` ranks candidates by DESCENDING article count, because volume is the evidence that
a network request is justified. That is the wrong order for deciding what to *admit*: the
highest-volume candidate is the one whose admission costs the product most. On production,
`sportskeeda.com` alone is 5,089 articles — 13.0% of the whole candidate cohort's mass.

But ascending volume is an ordering, **not** an answer, and the fixture below is the demonstration:
admitting the smaller host (11 rows) strands 14 articles belonging to the larger one, because the
cluster loses the support that held it together. Cheap to move is not the same as cheap to lose.
That is precisely why the counterfactual exists and why `--tranche` walks up in steps.
"""
from __future__ import annotations

import contextlib
import io
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "examples"))

import audit_source_cohort as asc  # noqa: E402
import corpus  # noqa: E402
import outlet_registry  # noqa: E402
import source_discovery as sd  # noqa: E402
import store as store_mod  # noqa: E402

_NOW = datetime.now(timezone.utc)


def _publish(st, host, n, *, title="Mayor announces budget plan number {i}", publisher=None):
    for i in range(n):
        st.upsert_feed_article(
            canonical_url=f"https://{host}/a/{i}", url=f"https://{host}/a/{i}",
            publisher=publisher or host, source_publisher=publisher or host,
            title=title.format(i=i), description="d", body=None,
            published_at=(_NOW - timedelta(hours=i)).isoformat(),
            source_feed=f"https://{host}/f",
            scored={"outlet": publisher or host, "lean": None, "category": "Politics"},
            source_type="rss", language="en")


@pytest.fixture()
def cohort(tmp_path):
    """Two hosts covering the same events — 14 articles and 11 — so removing either can strand the
    other's rows. The db URL, with the store disposed so the CLI opens its own connection."""
    db = f"sqlite:///{tmp_path / 'tranche.db'}"
    st = store_mod.Store(db)
    _publish(st, "bigone.example", 14)
    _publish(st, "tiny.example", 11)
    st.record_admission_candidates(
        sd.candidates(st.list_discovery_rows(), outlet_registry.default_registry()))
    st.engine.dispose()
    return db


def _run(db, *argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = asc.main(["--db", db, "--show", "3", *argv])
    assert rc == 0
    return buf.getvalue()


# --------------------------------------------------------------- the borrowed predicate
@pytest.mark.parametrize("publisher,url,expected", [
    ("alpha.example", "https://alpha.example/a/1", True),
    ("Alpha News", "https://alpha.example/a/1", True),          # matched on the URL
    ("alpha.example", "https://elsewhere.example/a/1", True),   # matched on the publisher STRING
    ("Alpha News", "https://news.alpha.example/a/1", True),     # a subdomain
    ("Alpha News", "https://notalpha.example/a/1", False),      # the dot-boundary rule
    ("Beta News", "https://beta.example/a/1", False),
    ("", "", False),
])
def test_the_tranche_predicate_is_the_one_corpus_uses(publisher, url, expected):
    """Differential against `corpus._matches`, not a restatement of it.

    Every row here is a case where a hand-rolled host match plausibly disagrees with production:
    the publisher-string arm, the subdomain arm, and the `notalpha.example` dot boundary. If the
    audit and the running system disagree about any of them, the counterfactual measures a different
    change from the one that would ship."""
    pred = asc._shadow_predicate(["alpha.example"])
    row = {"publisher": publisher, "canonicalUrl": url, "url": url}
    assert pred(row) is expected
    # And the same answer the running system gives, through the public entry point.
    index = {"shadow": (frozenset(), frozenset({"alpha.example"})), "B": (frozenset(), frozenset())}
    assert (corpus._tier_with(index, publisher, url) == "shadow") is expected


def test_an_empty_tranche_selects_nothing(cohort):
    assert asc._shadow_predicate([])({"publisher": "x", "url": "https://x.example/1"}) is False
    assert "M11 TRANCHE" not in _run(cohort)


# --------------------------------------------------------------- selection
def test_hosts_selects_exactly_that_tranche(cohort):
    out = _run(cohort, "--hosts", "tiny.example")
    assert "M11 TRANCHE: what admitting 1 host(s) would cost" in out
    assert "admit 1 host(s) to shadow: 1 hosts, 11 rows" in out


def test_cheapest_orders_by_ASCENDING_existing_volume(cohort):
    """The inversion this milestone's sizing argued for. `source_discovery` ranks by descending
    volume because volume justifies a REQUEST; admitting in that order costs the product most."""
    st = store_mod.Store(cohort)
    assert [r["host"] for r in st.admission_rows()] == ["bigone.example", "tiny.example"], \
        "the table's own order is by DESCENDING articles — that is what `cheapest` must invert"
    st.engine.dispose()

    out = _run(cohort, "--from-admission", "cheapest", "--tranche", "1")
    assert "1 hosts, 11 rows" in out, "the cheapest tranche took the 14-row host"
    out2 = _run(cohort, "--from-admission", "cheapest", "--tranche", "2")
    assert "2 hosts, 25 rows" in out2


def test_a_state_filter_takes_only_hosts_in_that_state(cohort):
    st = store_mod.Store(cohort)
    st.claim_admission_probe("tiny.example")
    st.record_admission_probe("tiny.example", verdict="ADMIT", feed_url="https://tiny.example/f")
    st.engine.dispose()

    assert "1 hosts, 11 rows" in _run(cohort, "--from-admission", "validated")
    assert "1 hosts, 14 rows" in _run(cohort, "--from-admission", "candidate")


# --------------------------------------------------------------- the bar
def test_the_counterfactual_reports_what_the_tranche_STRANDS(cohort):
    """**Cheap to move is not the same as cheap to lose.**

    `tiny.example` is the smaller host — 11 rows against 14 — so an ordering by volume admits it
    first. Doing so costs the OTHER host all 14 of its articles, because the story loses the support
    that held it together. Volume orders the candidates; only this measures them."""
    out = _run(cohort, "--hosts", "tiny.example")
    line = next(l for l in out.splitlines()
                if "LOST their story" in l and "<- the bar" in l and out.index(l) > out.index("M11 TRANCHE"))
    assert "14" in line, line
    assert "stories            : 1 -> 0" in out


def test_the_tranche_section_is_absent_without_a_selector(cohort):
    """The historical run is untouched: three counterfactuals by verdict, no tranche."""
    out = _run(cohort)
    assert "M11 TRANCHE" not in out
    assert "SYNDICATION only" in out and "HOST INSTABILITY only" in out


def test_the_identity_form_of_counterfactual_still_works(cohort):
    """`counterfactual` now takes a predicate OR a set. The verdict-driven callers still pass sets,
    and a refactor that quietly broke them would remove the audit's original purpose while the new
    tranche section reported healthily.

    Both fixture hosts carry identical headlines — which is what makes them cluster together, and
    therefore also what makes them read as 100% syndication. So this exercises the **non-empty** set
    path, counted in `outlets`, alongside the empty one."""
    out = _run(cohort)
    assert "--- SYNDICATION only: 2 outlets, 25 rows" in out
    assert "--- HOST INSTABILITY only: no outlets" in out
    assert "--- ALL of them together: 2 outlets, 25 rows" in out
