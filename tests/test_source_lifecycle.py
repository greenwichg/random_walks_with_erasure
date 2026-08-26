"""Promotion and retirement — `examples/source_lifecycle.py`, M9 of docs/SCALE_ROADMAP.md.

M8 stops at evidence. M9 acts on it, and what these tests pin is **which actions it is allowed to
take by itself**:

1. **Every crossing of the Tier A boundary, in either direction, needs a human and a counterfactual.**
   Tier A is the only membership that changes what clusters, so it is the only one whose change can
   move the story partition. This is the asymmetry the roadmap names as what makes 50,000 sources
   possible, and it is the whole automatic/manual split.
2. **The one exception is provable, not argued.** An outlet silent longer than the clustering window
   has no rows in the window, so removing it from Tier A cannot change the build.
3. **Hysteresis is a repeat-measurement rule, not a quality bar.** Two invented thresholds have died
   against data in this series; this is deliberately a different kind of claim.
4. **`INSUFFICIENT *` is never an instruction** — the same rule M8's `direction` applies.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import source_lifecycle as sl      # noqa: E402


# --------------------------------------------------------------------------- the Tier A boundary

@pytest.mark.parametrize("frm, to, crosses", [
    ("shadow", "B", False), ("B", "shadow", False),
    ("shadow", "A", True), ("A", "shadow", True),
    ("B", "A", True), ("A", "B", True),
    ("A", "dormant", True), ("B", "dormant", False),
    ("A", "A", False), ("B", "B", False),
])
def test_only_tier_a_crossings_move_the_partition(frm, to, crosses):
    assert sl.crosses_tier_a(frm, to) is crosses


@pytest.mark.parametrize("frm, to, verdict", [
    ("shadow", "A", "TIER A CANDIDATE"),
    ("B", "A", "TIER A CANDIDATE"),
])
def test_promotion_into_tier_a_is_never_automatic(frm, to, verdict):
    """The roadmap states Tier A is "gated, manual, and permanently narrow" — bounded by rating
    throughput, which is a budget and not an algorithm. A pipeline that promoted into it on its own
    would contradict the milestone it implements."""
    t = sl.plan(frm, verdict, streak=99, rated=True)
    assert t.to == to and t.automatic is False
    assert sl.NEEDS_COUNTERFACTUAL in t.requires


def test_promotion_into_tier_a_without_a_lean_names_both_blockers():
    """An unrated outlet inflates story size while contributing nothing to the blindspot claim —
    `SOURCE_COVERAGE_AUDIT.md`'s central finding. Both requirements are reported, not just the
    first, so a human is not sent to satisfy one and then surprised by the other."""
    t = sl.plan("shadow", "TIER A CANDIDATE", streak=99, rated=False)
    assert set(t.requires) == {sl.NEEDS_COUNTERFACTUAL, sl.NEEDS_LEAN}


def test_demotion_out_of_tier_a_is_also_not_automatic():
    """The direction nobody thinks about. Removing an outlet can strand articles whose only link ran
    through it — the bar `audit_source_cohort` reports as "OTHER articles that LOST their story". A
    demotion is a partition change exactly as much as a promotion is."""
    t = sl.plan("A", "REJECT", streak=99)
    assert t.to == "B" and t.automatic is False
    assert sl.NEEDS_COUNTERFACTUAL in t.requires


def test_shadow_to_tier_b_is_automatic():
    """The gate that scales to 50,000: a Tier B row cannot alter the partition, so admitting one
    needs no clustering bar at all."""
    t = sl.plan("shadow", "PROMOTE TO TIER B", streak=99)
    assert t.to == "B" and t.automatic is True and t.requires == ()


def test_tier_b_republisher_falls_back_to_shadow_automatically():
    """Neither side of B -> shadow clusters, so the partition cannot move. It withholds the outlet
    from readers while it is re-examined, and the ledger makes it reversible."""
    t = sl.plan("B", "REJECT", streak=99)
    assert t.to == "shadow" and t.automatic is True


def test_reject_never_pushes_below_shadow():
    """Shadow already surfaces nowhere. There is nothing further down that is still reversible, and
    a retirement that deletes evidence cannot be audited later."""
    assert sl.plan("shadow", "REJECT", streak=99) is None


# --------------------------------------------------------------------------- hysteresis

@pytest.mark.parametrize("streak, moves", [(1, False), (2, True), (5, True)])
def test_a_transition_waits_for_confirmations(streak, moves):
    t = sl.plan("shadow", "PROMOTE TO TIER B", streak=streak, confirmations=2)
    assert t.is_move is moves


def test_an_unconfirmed_transition_says_what_would_unblock_it():
    """A blocked transition that only refuses is an unhelpful instrument; it must say how many more
    agreeing evaluations it needs."""
    t = sl.plan("shadow", "PROMOTE TO TIER B", streak=1, confirmations=3)
    assert t.is_move is False
    assert "2 more evaluation(s)" in t.requires[0]


# --------------------------------------------------------------------------- silence

def test_silence_is_read_before_the_verdict():
    """An outlet that has published nothing for a month has no current evidence. A verdict computed
    from its last few articles describes a source that has stopped."""
    t = sl.plan("A", "PROMOTE TO TIER B", streak=99, days_silent=45, silent_days=30)
    assert t.to == "dormant"


def test_dormancy_from_tier_a_is_automatic_because_it_is_provably_partition_neutral():
    """The one Tier A crossing that needs no counterfactual, and the reason is arithmetic rather
    than judgement: nothing published in 30 days can appear in a 6-day window, so there is nothing
    of this outlet in the build to remove."""
    t = sl.plan("A", "PROMOTE TO TIER B", streak=99, days_silent=45,
                silent_days=30, window_days=6.0)
    assert t.to == "dormant" and t.automatic is True


def test_dormancy_is_NOT_automatic_when_the_interval_is_shorter_than_the_window():
    """The guard that keeps the argument above honest. With a dormancy interval inside the
    clustering window the outlet still HAS rows in the build, so removing it would be a silent
    partition change — exactly the thing the counterfactual exists to catch."""
    t = sl.plan("A", "PROMOTE TO TIER B", streak=99, days_silent=5,
                silent_days=4, window_days=6.0)
    assert t.to == "dormant" and t.automatic is False
    assert "must exceed the clustering window" in t.requires[0]


def test_a_dormant_outlet_that_resumes_re_enters_evaluation_not_its_old_tier():
    """Reversible by default, but not credulous: the evidence that justified its old tier is months
    stale, so it goes back through shadow rather than straight back to where it was."""
    t = sl.plan("dormant", "PROMOTE TO TIER B", streak=99, days_silent=1, silent_days=30)
    assert t.to == "shadow" and t.automatic is True


def test_retirement_is_never_automatic():
    """Stage 6 says "dormant (daily probe), then retired" and gives no interval for the second
    arrow. Dormancy is reversible and harmless; retirement is neither reversible nor measured.
    Inventing an interval would be the third guess this series has made."""
    assert sl.plan("retired", "PROMOTE TO TIER B", streak=99, days_silent=999) is None
    for state in ("shadow", "B", "A"):
        t = sl.plan(state, "REJECT", streak=99, days_silent=999, silent_days=30)
        assert t.to == "dormant", "silence goes dormant, never straight to retired"


# --------------------------------------------------------------------------- non-instructions

@pytest.mark.parametrize("verdict", ["INSUFFICIENT DATA", "INSUFFICIENT VOLUME", "", "nonsense"])
@pytest.mark.parametrize("state", ["shadow", "B", "A"])
def test_a_verdict_that_is_not_an_instruction_produces_no_transition(verdict, state):
    assert sl.target_for(verdict, state) is None
    assert sl.plan(state, verdict, streak=99) is None


def test_an_unknown_state_is_an_error_not_a_default():
    """Silently defaulting an unrecognised state would let a typo in the ledger move an outlet."""
    with pytest.raises(ValueError):
        sl.plan("tierA", "PROMOTE TO TIER B", streak=99)


def test_the_module_is_pure():
    """Policy over numbers. The moment it reads a store or an env var its decisions stop being
    reproducible from the row printed beside them."""
    import ast
    tree = ast.parse((ROOT / "examples" / "source_lifecycle.py").read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & {"os", "store", "corpus", "requests", "sqlalchemy"}), sorted(imported)
