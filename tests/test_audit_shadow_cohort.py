"""The shadow evaluation runner — `examples/audit_shadow_cohort.py`, M8 of docs/SCALE_ROADMAP.md.

`test_source_evaluation.py` pins the policy. This pins the two things the RUNNER can get wrong,
both of which would produce confident, wrong numbers rather than an error:

1. **Syndication measured against the wrong population.** A shadow outlet's syndication partner is
   almost always a Tier A masthead it is republishing. Count carriers within the cohort alone and a
   lone republisher scores 0% — the exact outlet the ceiling exists to catch.
2. **Self-scoring.** If the assignment index contains the cohort's own coverage, every article
   attaches to itself and the rate is ~100% by construction.

Both are the same failure shape this audit series has already shipped three times: a lookup or a
population chosen slightly wrong, producing a plausible number that nothing contradicts.
"""
import pathlib
import sys
from datetime import datetime, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import audit_shadow_cohort as asc   # noqa: E402
import clustering                   # noqa: E402
import outlet_registry              # noqa: E402
import source_evaluation as se      # noqa: E402

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
HEADLINE = "Storm system brings record rainfall across the eastern seaboard"


def _row(publisher, host, title, *, created=NOW, published=NOW):
    return {"publisher": publisher, "title": title,
            "url": f"https://{host}/a{abs(hash(title)) % 9999}",
            "canonicalUrl": f"{host}/a{abs(hash(title)) % 9999}",
            "createdAt": created.isoformat(), "publishedAt": published.isoformat(),
            "fetchedAt": published.isoformat()}


def test_syndication_is_measured_against_the_tier_a_corpus_not_the_cohort():
    """The bug this function's docstring is about. A single republisher in the cohort has no peer
    inside it, so a cohort-only carrier index reports 0% syndication for an outlet that is running
    someone else's copy verbatim."""
    cohort = [_row("echodaily.example", "echodaily.example", HEADLINE)]
    tier_a = [_row("Reuters", "reuters.com", HEADLINE)]

    cohort_only = asc.carrier_index(cohort)
    both = asc.carrier_index(tier_a, cohort)
    toks = clustering.title_tokens(HEADLINE)

    assert len(cohort_only[toks]) == 1, "cohort-only sees one carrier — 0% syndication"
    assert len(both[toks]) == 2, "with Tier A in the population the republisher is visible"


def test_outlet_stats_flags_the_republisher_it_can_now_see():
    reg = outlet_registry.default_registry()
    cohort = [_row("echodaily.example", "echodaily.example", HEADLINE)]
    carriers = asc.carrier_index([_row("Reuters", "reuters.com", HEADLINE)], cohort)
    stats = asc.outlet_stats(cohort, reg, carriers, se.assignment_index([]), now=NOW)
    assert stats["echodaily.example"]["syndication"] == 1.0


def test_outlet_stats_reports_an_undatable_outlet_as_unknown_not_as_zero():
    """`observedDays=None` must survive into the table. Coerced to 0.0 it would read as "seen for
    zero days", which `evaluate` treats as a hard INSUFFICIENT DATA rather than an absent signal."""
    reg = outlet_registry.default_registry()
    row = _row("echodaily.example", "echodaily.example", HEADLINE)
    row["createdAt"] = None
    stats = asc.outlet_stats([row], reg, asc.carrier_index([row]), se.assignment_index([]), now=NOW)
    assert stats["echodaily.example"]["observedDays"] is None


def test_member_key_uses_the_display_url_the_coverage_entry_carries():
    """`audit_source_cohort.member_key`'s bug, guarded in the second script that needs the same
    join. `canonicalUrl` is already lower-cased and stripped, so it misses on most real rows."""
    row = {"url": "https://Example.com/Path/?utm_source=x", "canonicalUrl": "example.com/path"}
    assert asc._member_key(row) == "https://Example.com/Path/?utm_source=x"


def test_self_scoring_guard_catches_a_cohort_scored_against_its_own_coverage():
    """The `--as-if` trap. Forget to rebuild the story set without the cohort and every article
    attaches to itself — a ~100% rate that looks like a strong result and measures nothing."""
    row = _row("echodaily.example", "echodaily.example", HEADLINE)
    with_it = [{"id": "s1", "coverage": [{"headline": HEADLINE, "url": asc._member_key(row),
                                          "publishedAt": NOW.isoformat()}]}]
    without = [{"id": "s1", "coverage": [{"headline": HEADLINE, "url": "https://reuters.com/a1",
                                          "publishedAt": NOW.isoformat()}]}]
    assert asc.self_scored([row], with_it) == 1
    assert asc.self_scored([row], without) == 0


def test_the_runner_scores_against_the_clusterer_not_a_local_rule():
    """Structural. The whole justification for extracting `clustering.pair_admits` is that there is
    ONE definition of "same event". A similarity expression appearing in this runner would be a
    second one, and it would drift."""
    src = (ROOT / "examples" / "audit_shadow_cohort.py").read_text()
    for banned in ("weighted_jaccard", "jaccard(", "DEFAULT_SIM", "within_window"):
        assert banned not in src, f"the runner must not re-implement the pair rule — found {banned!r}"
    assert "se.assignment_rate" in src


@pytest.mark.parametrize("field", ["assignmentRate", "assignmentStories", "attached"])
def test_the_runner_reports_assignment_but_never_branches_on_it(field):
    """`evaluate` is the only thing that turns stats into a verdict, and `test_source_evaluation`
    pins that it ignores these fields. This pins that the runner does not add its own gate around
    the outside — an `if s["attached"] > N` here would reintroduce exactly the threshold the policy
    module refuses to invent."""
    src = (ROOT / "examples" / "audit_shadow_cohort.py").read_text()
    for line in src.splitlines():
        stripped = line.strip()
        if field in stripped and stripped.startswith(("if ", "elif ", "assert ")):
            pytest.fail(f"runner branches on {field}: {stripped}")
