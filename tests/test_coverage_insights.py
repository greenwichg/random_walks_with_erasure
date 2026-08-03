"""Tests for examples/coverage_insights.py — the comparable set (design revision 2, §4).

This module is the membership test every insight-derived finding counts over, so the properties
tested here are the ones that keep those counts honest:

* an article is never measured against coverage published AFTER it (the temporal rule the first
  revision was missing — an article published in hour 1 cannot mention what happened in hour 30);
* a stub member cannot make the comparison set look complete (input parity);
* six outlets running one wire story count ONCE, because ``publisher_identity`` collapses many
  names of one outlet and not one story across many outlets;
* the rule is a pure function — same inputs, same answer, whatever order the members arrive in.
"""

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import coverage_insights as ci     # noqa: E402


def cand(pub, text, *, published="2026-08-02T09:00:00Z", url=None):
    return ci.candidate({"publisher": pub, "url": url or f"https://{pub.lower()}.example/a",
                         "headline": text[:40], "publishedAt": published}, text)


LONG = ("Council approves the harbour redevelopment after a seven hour hearing in which "
        "residents objected to the compensation offer and the developer defended the cost. ")


# --------------------------------------------------------------------------- config


def test_config_knobs_read_env(monkeypatch):
    monkeypatch.setenv("RWE_COVERAGE_MIN_COMPARABLE", "5")
    monkeypatch.setenv("RWE_COVERAGE_INPUT_PARITY", "0.25")
    monkeypatch.setenv("RWE_COVERAGE_TIME_GRACE_H", "12")
    monkeypatch.setenv("RWE_COVERAGE_SYNDICATION_SIM", "0.75")
    assert ci.min_comparable() == 5
    assert ci.input_parity() == 0.25
    assert ci.time_grace_hours() == 12.0
    assert ci.syndication_sim() == 0.75


def test_config_defaults_survive_garbage(monkeypatch):
    monkeypatch.setenv("RWE_COVERAGE_MIN_COMPARABLE", "not-a-number")
    monkeypatch.setenv("RWE_COVERAGE_INPUT_PARITY", "")
    assert ci.min_comparable() == ci.MIN_COMPARABLE
    assert ci.input_parity() == ci.DEFAULT_INPUT_PARITY


def test_min_comparable_never_below_two(monkeypatch):
    """A two-member set is the arithmetic floor for any comparison at all."""
    monkeypatch.setenv("RWE_COVERAGE_MIN_COMPARABLE", "1")
    assert ci.min_comparable() == 2


# --------------------------------------------------------------------------- candidate


def test_candidate_measures_the_generators_own_view():
    """inputChars is the text the MODEL would see, not the story member's headline — the parity
    rule is about what the extractor had to work with."""
    c = cand("Ledger", LONG)
    assert c["inputChars"] == len(LONG)
    assert "harbour" in c["tokens"]


def test_candidate_tolerates_empty_text():
    c = cand("Ledger", "")
    assert c["inputChars"] == 0 and c["tokens"] == frozenset()


# --------------------------------------------------------------------------- time window


def test_later_coverage_is_not_comparable():
    """The rule revision 1 was missing. Members published after the target (beyond grace) cannot
    be counted against it — they may carry developments the target could not have known."""
    t = cand("Ledger", LONG, published="2026-08-02T00:00:00Z")
    earlier = cand("Chronicle", LONG, published="2026-08-01T20:00:00Z")
    later = cand("Gazette", LONG, published="2026-08-03T12:00:00Z")
    out = ci.comparable_stage1(t, [t, earlier, later])
    pubs = {c["publisher"] for c in out}
    assert pubs == {"Chronicle"}


def test_grace_window_absorbs_timestamp_jitter():
    t = cand("Ledger", LONG, published="2026-08-02T00:00:00Z")
    jitter = cand("Chronicle", LONG, published="2026-08-02T04:00:00Z")   # +4h, inside 6h grace
    assert [c["publisher"] for c in ci.comparable_stage1(t, [t, jitter])] == ["Chronicle"]


def test_grace_window_is_configurable(monkeypatch):
    monkeypatch.setenv("RWE_COVERAGE_TIME_GRACE_H", "0")
    t = cand("Ledger", LONG, published="2026-08-02T00:00:00Z")
    jitter = cand("Chronicle", LONG, published="2026-08-02T04:00:00Z")
    assert ci.comparable_stage1(t, [t, jitter]) == []


def test_unparseable_timestamps_are_not_evidence():
    """Consistent with L0's timing block, which counts only parseable times."""
    t = cand("Ledger", LONG, published="2026-08-02T00:00:00Z")
    broken = cand("Chronicle", LONG, published="whenever")
    assert ci.comparable_stage1(t, [t, broken]) == []
    t_broken = cand("Ledger", LONG, published="")
    ok = cand("Chronicle", LONG, published="2026-08-01T00:00:00Z")
    assert ci.comparable_stage1(t_broken, [t_broken, ok]) == []


def test_target_is_excluded_by_url_not_only_identity():
    """Callers rebuild member dicts; identity comparison alone would let a target compare
    with itself and inflate every count by one."""
    t = cand("Ledger", LONG, url="https://ledger.example/x")
    copy_of_t = cand("Ledger", LONG, url="https://ledger.example/x")
    assert ci.comparable_stage1(t, [copy_of_t]) == []


# --------------------------------------------------------------------------- input parity


def test_stub_members_cannot_make_the_set_look_complete():
    t = cand("Ledger", LONG, published="2026-08-02T00:00:00Z")
    full = [cand(f"Full{i}", LONG, published="2026-08-01T00:00:00Z") for i in range(3)]
    stub = cand("Stub", "Council approves plan", published="2026-08-01T00:00:00Z")
    out = ci.comparable_stage1(t, [t] + full + [stub])
    assert "Stub" not in {c["publisher"] for c in out}
    assert len(out) == 3


def test_parity_floor_is_relative_to_the_set_not_absolute():
    """A set of uniformly short members compares fine — parity is about ASYMMETRY, so that a
    terse-feed cluster is not silently excluded from the feature."""
    t = cand("Ledger", "Council approves plan tonight", published="2026-08-02T00:00:00Z")
    peers = [cand(f"P{i}", "Council approves plan tonight", published="2026-08-01T00:00:00Z")
             for i in range(3)]
    assert len(ci.comparable_stage1(t, [t] + peers)) == 3


def test_empty_candidate_set_returns_empty():
    t = cand("Ledger", LONG)
    assert ci.comparable_stage1(t, [t]) == []


# --------------------------------------------------------------------------- syndication


def test_wire_copy_collapses_to_one_support_unit():
    """Six outlets running one AP story is ONE act of journalism. publisher_identity cannot see
    this — it collapses name FORMS of one outlet — so the count would otherwise read as six."""
    wire = [cand(f"Station {i}", LONG) for i in range(6)]
    assert len(ci.syndication_groups(wire)) == 1
    assert ci.support_units(wire) == 1


def test_distinct_reporting_does_not_collapse():
    a = cand("Ledger", "Council approves the harbour redevelopment after a seven hour hearing")
    b = cand("Chronicle", "Residents lose compensation fight as developer wins planning consent")
    assert len(ci.syndication_groups([a, b])) == 2
    assert ci.support_units([a, b]) == 2


def test_syndication_threshold_is_configurable(monkeypatch):
    # token Jaccard 0.71 — a rewrite of one story, not a copy of it
    a = cand("Ledger", "Council approves harbour redevelopment scheme")
    b = cand("Chronicle", "Council approves harbour redevelopment scheme after hearing evidence")
    monkeypatch.setenv("RWE_COVERAGE_SYNDICATION_SIM", "0.99")
    assert len(ci.syndication_groups([a, b])) == 2
    monkeypatch.setenv("RWE_COVERAGE_SYNDICATION_SIM", "0.5")
    assert len(ci.syndication_groups([a, b])) == 1


def test_grouping_is_order_independent():
    """DSU with lower-index roots: the partition must not depend on the order members arrive."""
    cands = [cand("A", LONG), cand("B", "Wholly different reporting about a separate matter"),
             cand("C", LONG), cand("D", "Another distinct piece on an unrelated subject entirely")]
    sizes = sorted(len(g) for g in ci.syndication_groups(cands))
    rev = sorted(len(g) for g in ci.syndication_groups(list(reversed(cands))))
    assert sizes == rev == [1, 1, 2]


def test_support_units_also_collapses_name_forms():
    """Both collapses apply, in order and for different reasons."""
    cands = [cand("Sportskeeda", "Council approves the harbour redevelopment plan tonight"),
             cand("Sportskeeda.Com", "Residents lose their compensation fight at the hearing")]
    assert len(ci.syndication_groups(cands)) == 2      # different text, not wire copy
    assert ci.support_units(cands) == 1                # …but one outlet


def test_support_units_of_empty_set():
    assert ci.support_units([]) == 0


# --------------------------------------------------------------------------- determinism


def test_stage1_is_a_pure_function():
    t = cand("Ledger", LONG, published="2026-08-02T00:00:00Z")
    peers = [cand(f"P{i}", LONG + str(i), published="2026-08-01T00:00:00Z") for i in range(4)]
    first = [c["publisher"] for c in ci.comparable_stage1(t, [t] + peers)]
    second = [c["publisher"] for c in ci.comparable_stage1(t, [t] + peers)]
    assert first == second


@pytest.mark.parametrize("n", [3, 8, 20])
def test_support_units_never_exceeds_membership(n):
    cands = [cand(f"Outlet {i}", LONG + f" variant {i} " * (i + 1)) for i in range(n)]
    assert 0 < ci.support_units(cands) <= n


# --------------------------------------------------------------------------- stage 2


def insight(recipe="r1", fmt="news_report", vocab=1, frames=("conflict",)):
    return {"recipeHash": recipe, "inputChars": 500,
            "facets": {"vocabVersion": vocab, "format": fmt, "depth": "episodic",
                       "frames": [{"key": f, "evidence": "x"} for f in frames],
                       "voices": [], "centeredVoice": None, "quantities": []}}


def enriched(pub, ins, *, published="2026-08-01T00:00:00Z", text=None):
    return ci.with_insight(cand(pub, text or LONG, published=published), ins)


def test_a_member_without_an_insight_is_not_comparable():
    """Absence is never treated as agreement — the defect class that made three L0 findings dead
    code was exactly a module reading a field its producer never set."""
    t = enriched("Ledger", insight(), published="2026-08-02T00:00:00Z")
    peers = [enriched(f"P{i}", insight()) for i in range(2)] + [enriched("Nope", None)]
    out = ci.comparable_set(t, peers)
    assert {c["publisher"] for c in out} == {"P0", "P1"}


def test_a_target_without_an_insight_has_no_comparable_set_at_all():
    t = enriched("Ledger", None, published="2026-08-02T00:00:00Z")
    assert ci.comparable_set(t, [enriched(f"P{i}", insight()) for i in range(3)]) == []


def test_recipe_parity_partitions_the_cluster():
    """A cluster extracted by two models is a MODEL comparison wearing a coverage comparison's
    clothes; its numbers would differ by subscription tier rather than by outlet."""
    t = enriched("Ledger", insight(recipe="opus"), published="2026-08-02T00:00:00Z")
    peers = [enriched("A", insight(recipe="opus")), enriched("B", insight(recipe="opus")),
             enriched("C", insight(recipe="llama")), enriched("D", insight(recipe="llama"))]
    assert {c["publisher"] for c in ci.comparable_set(t, peers)} == {"A", "B"}


def test_format_parity_keeps_a_review_out_of_a_news_comparison():
    """The production case: a film review clustered with a box-office report (evaluation §4)."""
    t = enriched("Ledger", insight(fmt="news_report"), published="2026-08-02T00:00:00Z")
    peers = [enriched("A", insight(fmt="news_report")),
             enriched("B", insight(fmt="review")),
             enriched("C", insight(fmt=None))]        # the model declined to name a format
    assert {c["publisher"] for c in ci.comparable_set(t, peers)} == {"A"}


def test_a_vocabulary_change_makes_records_incomparable():
    """A value the model could not have chosen is not evidence it rejected the value, so a vocab
    bump is a full re-extraction (design §14.2) — not a silent mixing of two label sets."""
    t = enriched("Ledger", insight(vocab=1), published="2026-08-02T00:00:00Z")
    peers = [enriched("A", insight(vocab=1)), enriched("B", insight(vocab=2))]
    assert {c["publisher"] for c in ci.comparable_set(t, peers)} == {"A"}


def test_stage2_still_obeys_the_time_window_and_input_parity():
    """The four conditions compose: stage 2 narrows stage 1, it never widens it."""
    t = enriched("Ledger", insight(), published="2026-08-02T00:00:00Z")
    later = enriched("Late", insight(), published="2026-08-05T00:00:00Z")
    stub = enriched("Stub", insight(), text="Council approves plan")
    peers = [enriched("A", insight()), enriched("B", insight()), later, stub]
    out = {c["publisher"] for c in ci.comparable_set(t, peers)}
    assert out == {"A", "B"}


def test_stage2_is_a_subset_of_stage1():
    t = enriched("Ledger", insight(), published="2026-08-02T00:00:00Z")
    peers = [enriched("A", insight()), enriched("B", insight(recipe="other")),
             enriched("C", insight(fmt="review"))]
    s1 = {c["publisher"] for c in ci.comparable_stage1(t, peers)}
    s2 = {c["publisher"] for c in ci.comparable_set(t, peers)}
    assert s2 <= s1 and s2 == {"A"}


def test_with_insight_marks_an_empty_facets_object_as_no_extraction():
    c = ci.with_insight(cand("Ledger", LONG), {"recipeHash": "r", "facets": {}})
    assert c["hasInsight"] is False and c["format"] is None
