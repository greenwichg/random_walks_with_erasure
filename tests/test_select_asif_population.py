"""The `--as-if` population selector — `examples/select_asif_population.py`.

The selector exists because the cohort for the offline Tier B experiment must not be chosen
by hand: a population picked after looking at the data can be picked to produce a result,
and nothing in `audit_shadow_cohort.py`'s output would reveal it.

Two failure shapes are worth pinning, and the second is the one that has already cost a
production run:

1. **The rule admits the outlets it exists to exclude.** The 8 outlets carrying Tier B
   verdicts are mostly republishers; attaching them back restores the 86 double-counts
   their demotion removed. A syndication filter that does not fire re-creates that test.
2. **The names it emits do not select anything.** `measure` lower-cases the caller's input
   while `_identity` returns the registry canonical unmodified, and 571 of 573 canonicals
   carry capitals. A selector printing canonicals produced a command that reported every
   outlet as NOT IN THE CATALOG — a cohort of zero, described as a completed run.
"""
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import audit_shadow_cohort as asc          # noqa: E402
import outlet_registry                     # noqa: E402
import select_asif_population as sel       # noqa: E402
import store as store_mod                  # noqa: E402
import story_service                       # noqa: E402

NOW = datetime.now(timezone.utc) - timedelta(days=1)


def _row(publisher, host, title, idx):
    return {"publisher": publisher, "title": title,
            "url": f"https://{host}/a{idx}?utm_source=x",
            "canonicalUrl": f"{host}/a{idx}",
            "publishedAt": NOW.isoformat(), "createdAt": NOW.isoformat()}


def _clean(publisher, host, n=6, stem="Ferry service resumes after harbour dredging"):
    return [_row(publisher, host, f"{stem} {k}", k) for k in range(n)]


def _profile(rows):
    return sel.profile(rows, outlet_registry.default_registry())


def _keys(rows):
    return {r["key"] for r in sel.eligible(_profile(rows))}


def test_a_clean_low_volume_outlet_is_eligible():
    """The positive case. Without it every exclusion test below passes vacuously — a rule
    that admits nothing excludes the republisher too, and says nothing about either."""
    assert _keys(_clean("Coastal Herald", "coastalherald.example")) == {"coastal herald"}


def test_the_republisher_is_excluded():
    """Rule 3, and the reason the selector exists. `carrier_index` sees the Tier A masthead
    carrying the same headlines, so the republisher scores 100% syndication."""
    wire = _clean("Reuters", "reuters.com")
    echo = _clean("Echo Daily", "echodaily.example")      # same headlines, verbatim
    assert "echo daily" not in _keys(wire + echo)


def test_a_high_volume_outlet_is_excluded():
    """Rule 2's upper bound. Removing a large outlet reshapes the story set the cohort is
    then scored against, so the attach rate would no longer be a clean read."""
    big = _clean("Associated Press", "apnews.com", n=sel.MAX_ARTICLES + 1,
                 stem="Parliament debates budget measure")
    assert _keys(big) == set()


def test_a_too_small_outlet_is_excluded():
    """Rule 2's lower bound. One or two articles cannot produce a non-degenerate rate."""
    assert _keys(_clean("Tiny Wire", "tinywire.example", n=sel.MIN_ARTICLES - 1)) == set()


def test_an_outlet_whose_rows_scatter_across_hosts_is_excluded():
    """Rule 4 — `source_evaluation`'s other demotion cause."""
    scattered = [_row("Scatter Wire", f"h{k}.scatter.example",
                      f"Regional council reviews transport plan {k}", k) for k in range(6)]
    assert _keys(scattered) == set()


def test_a_feed_title_artifact_is_excluded_but_a_real_untracked_outlet_is_not():
    """Rule 5 is a sanity test on the HOST, not a quality bar on the outlet. An untracked
    outlet with a real domain must survive — filtering on registry membership would bias
    the population toward majors having a quiet week, which is not the tail being modelled."""
    artifact = [_row("Google News", "", f"Aggregated headline {k}", k) for k in range(6)]
    assert _keys(artifact) == set()
    assert _keys(_clean("Coastal Herald", "coastalherald.example")) == {"coastal herald"}


def test_untracked_outlets_are_reported_as_a_split_not_filtered_away():
    """The stated design: tracked/untracked is a stratum to read, not a filter to apply."""
    both = _clean("Coastal Herald", "coastalherald.example") + \
        _clean("The Guardian", "theguardian.com", stem="Sea wall repairs begin at north quay")
    picked = sel.eligible(_profile(both))
    assert {r["tracked"] for r in picked} == {True, False}


def test_eligibility_is_ordered_by_name_not_by_anything_the_audit_measures():
    """Ordering by volume, or by any field the audit will report, would let the head of the
    list correlate with the outcome. Name order cannot.

    The volumes are deliberately the REVERSE of the name order. With ``alpha`` also the
    larger outlet the two orderings coincide, and swapping the sort key for ``-articles``
    leaves the assertion passing — a fixture that cannot see the mutation it exists for."""
    rows = (_clean("Zulu Post", "zulupost.example", n=9)
            + _clean("Alpha Wire", "alphawire.example", n=4,
                     stem="Sea wall repairs begin at north quay"))
    picked = sel.eligible(_profile(rows))
    assert [r["key"] for r in picked] == ["alpha wire", "zulu post"]
    assert [r["articles"] for r in picked] == [4, 9], "name order must not equal volume order"


def test_a_publisher_string_containing_a_comma_is_excluded():
    """The emitted list is comma-separated, so a comma in a name would silently split one
    outlet into two unmatchable fragments — a cohort quietly smaller than the one reported."""
    rows = _clean("Herald, The", "heraldthe.example")
    assert _keys(rows) == set()


@pytest.mark.parametrize("publisher,host", [
    ("theguardian.com", "theguardian.com"),      # tracked; canonical "The Guardian"
    ("Coastal Herald", "coastalherald.example"),  # untracked; identity is the folded string
])
def test_the_emitted_names_actually_select_the_rows_in_as_if(tmp_path, publisher, host):
    """**The round trip, and the guard that matters most.** The selector's output is only
    useful if `measure` selects exactly the rows it was chosen from. Pinned across both
    identity branches: a tracked outlet whose canonical differs from its raw string, and an
    untracked one. Emitting the canonical instead of the raw spelling fails the tracked case.
    """
    reg = outlet_registry.default_registry()
    st = store_mod.Store(f"sqlite:///{tmp_path}/rt.db")
    for k in range(4):
        st.upsert_feed_article(
            canonical_url=f"{host}/a{k}", url=f"https://{host}/a{k}?utm_source=x",
            publisher=publisher, source_publisher=None,
            title=f"Ferry service resumes after harbour dredging {k}", description="",
            body=None, published_at=NOW.isoformat(), source_feed="f", scored={})

    tier_a = story_service._fetch(st)
    picked = sel.eligible(sel.profile(tier_a, reg))
    assert picked, "fixture outlet must be eligible for the round trip to mean anything"

    names = {s for r in picked for s in r["spellings"]}
    m = asc.measure(st, reg, as_if=names)
    assert m["unmatched"] == [], f"selector emitted names --as-if cannot match: {m['unmatched']}"
    assert len(m["cohort"]) == len(tier_a) == 4


def test_an_empty_selection_refuses_to_print_a_command(monkeypatch, tmp_path, capsys):
    """`--as-if ""` parses to an empty set, which falls back to the DEFAULT shadow-lane run —
    a different question whose output reads like an answer to this one. An empty cohort must
    fail, not emit a command that appears to evaluate it."""
    st_path = f"sqlite:///{tmp_path}/empty.db"
    st = store_mod.Store(st_path)
    st.upsert_feed_article(canonical_url="tiny.example/a", url="https://tiny.example/a",
                           publisher="Tiny Wire", source_publisher=None, title="One story",
                           description="", body=None, published_at=NOW.isoformat(),
                           source_feed="f", scored={})
    monkeypatch.setenv("RWE_DB_URL", st_path)

    assert sel.main([]) == 1
    out = capsys.readouterr().out
    assert "NO OUTLET MET THE RULE" in out
    assert "run this next" not in out
    assert "audit_shadow_cohort.py" not in out, "printed a runnable command for an empty cohort"


def test_the_selector_reuses_the_audits_definitions_rather_than_restating_them():
    """Structural, and the same rule `audit_shadow_cohort` holds itself to. A local
    syndication count or a second identity expression here would be a second definition of
    the population, and the guards built into the audit would not apply to it."""
    src = (ROOT / "examples" / "select_asif_population.py").read_text()
    assert "asc.carrier_index" in src, "syndication must come from the audit's carrier index"
    assert "asc._identity" in src, "identity must come from the audit"
    assert "se.SYNDICATION_CEILING" in src, "the ceiling must come from the policy module"
    for banned in ("def _identity", "def carrier_index", "jaccard", "0.35"):
        assert banned not in src, f"selector restates a shared definition — found {banned!r}"
