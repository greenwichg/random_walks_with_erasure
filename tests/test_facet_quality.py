"""Tests for examples/facet_quality.py — Phase 0b's ship gate.

Design revision 2 §11.5. The claim this module has to earn is narrow and important: a count over
generated labels is only a fact if the labels are stable. So the properties tested are the ones
that stop a flattering number:

* a model that answers the SAME thing every time scores high raw agreement and κ must not reward
  it as though it had discriminated;
* disagreement at chance level scores ~0, not "half agreed";
* the free-text parts of a facet (a voice's name, a quantity's subject) are excluded, because
  measuring their stability would answer a question no tier asks.
"""

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import facet_quality as fq   # noqa: E402


def facets(fmt=None, depth=None, centered=None, frames=(), voices=(), quantities=()):
    return {"vocabVersion": 1, "format": fmt, "depth": depth, "centeredVoice": centered,
            "frames": [{"key": k, "evidence": "x"} for k in frames],
            "voices": [{"role": r, "name": n, "evidence": "x"} for r, n in voices],
            "quantities": [{"kind": k, "value": v, "unit": None, "subject": s, "evidence": "x"}
                           for k, v, s in quantities]}


# --------------------------------------------------------------------------- kappa


def test_perfect_agreement_on_a_varied_field():
    assert fq.cohens_kappa(["a", "b", "c", "a"], ["a", "b", "c", "a"]) == 1.0


def test_chance_level_disagreement_scores_about_zero():
    a = ["x", "y"] * 10
    b = ["x", "y", "y", "x"] * 5
    k = fq.cohens_kappa(a, b)
    assert -0.35 < k < 0.35


def test_total_disagreement_is_negative():
    assert fq.cohens_kappa(["a", "b"] * 5, ["b", "a"] * 5) < 0


def test_a_constant_answer_is_reported_as_perfect_but_the_categories_reveal_it():
    """The failure this measure exists to expose. A model answering 'news_report' every time
    agrees with itself perfectly and has discriminated nothing; κ cannot distinguish those, so the
    report prints the category count beside it."""
    a = b = ["news_report"] * 12
    assert fq.cohens_kappa(a, b) == 1.0
    out = fq.agreement({str(i): facets(fmt="news_report") for i in range(12)},
                       {str(i): facets(fmt="news_report") for i in range(12)})
    assert out["fields"]["format"]["value"] == 1.0
    assert out["fields"]["format"]["categories"] == 1        # <- the tell


def test_constant_but_disagreeing_is_not_rewarded():
    """Degenerate expected-agreement, but the raters differ: must not return 1.0."""
    assert fq.cohens_kappa(["a"] * 5, ["b"] * 5) == 0.0


def test_none_is_a_category_not_missing_data():
    """Stability of the model's refusal matters as much as stability of its choice."""
    assert fq.cohens_kappa([None, None, "a"], [None, None, "a"]) == 1.0
    assert fq.cohens_kappa([None, "a", None], ["a", None, "a"]) < 0.5


def test_empty_input_measures_nothing_rather_than_claiming_agreement():
    assert fq.cohens_kappa([], []) is None


@pytest.mark.parametrize("k, expect", [(0.9, "almost perfect"), (0.7, "substantial"),
                                       (0.5, "moderate"), (0.3, "fair"), (0.05, "slight"),
                                       (-0.2, "none/worse than chance"), (None, "n/a")])
def test_bands_follow_landis_koch(k, expect):
    assert fq.band(k) == expect


# --------------------------------------------------------------------------- set fields


def test_set_fields_score_by_jaccard_on_the_parts_a_tier_counts():
    a = {"1": facets(frames=["conflict", "morality"])}
    b = {"1": facets(frames=["conflict"])}
    out = fq.agreement(a, b)
    assert out["fields"]["frames"]["kind"] == "jaccard"
    assert out["fields"]["frames"]["value"] == pytest.approx(0.5)


def test_voice_names_are_excluded_from_agreement():
    """A tier counts ROLES. Free text would make every extraction disagree and would measure
    prose style, not label stability."""
    a = {"1": facets(voices=[("official_government", "Mayor Ruiz")])}
    b = {"1": facets(voices=[("official_government", "the mayor")])}
    assert fq.agreement(a, b)["fields"]["voices"]["value"] == 1.0


def test_quantity_subjects_are_excluded_but_values_are_not():
    same = {"1": facets(quantities=[("money", 340000000.0, "public cost")])}
    reworded = {"1": facets(quantities=[("money", 340000000.0, "cost to taxpayers")])}
    different = {"1": facets(quantities=[("money", 290000000.0, "public cost")])}
    assert fq.agreement(same, reworded)["fields"]["quantities"]["value"] == 1.0
    assert fq.agreement(same, different)["fields"]["quantities"]["value"] == 0.0


def test_both_empty_counts_as_agreement():
    """Two extractions that both found no figures agree about the article."""
    assert fq.agreement({"1": facets()}, {"1": facets()})["fields"]["quantities"]["value"] == 1.0


def test_one_empty_one_not_is_disagreement():
    a = {"1": facets(frames=["conflict"])}
    assert fq.agreement(a, {"1": facets()})["fields"]["frames"]["value"] == 0.0


# --------------------------------------------------------------------------- agreement()


def test_only_articles_present_in_both_runs_are_compared_and_n_is_reported():
    a = {"1": facets(fmt="review"), "2": facets(fmt="analysis")}
    b = {"2": facets(fmt="analysis"), "3": facets(fmt="review")}
    out = fq.agreement(a, b)
    assert out["n"] == 1


def test_no_overlap_yields_no_fields_rather_than_a_number():
    out = fq.agreement({"1": facets()}, {"2": facets()})
    assert out["n"] == 0 and out["fields"] == {}


def test_ships_flag_tracks_the_pre_registered_bar():
    """The bar is chosen before the data and lives in one place, so it cannot quietly move once a
    number comes back."""
    agree = {str(i): facets(fmt=("review" if i % 2 else "analysis")) for i in range(10)}
    out = fq.agreement(agree, agree)
    assert out["fields"]["format"]["ships"] is True
    disagree = {str(i): facets(fmt=("review" if i % 3 else "analysis")) for i in range(10)}
    assert fq.agreement(agree, disagree)["fields"]["format"]["ships"] is False


# --------------------------------------------------------------------------- throughput


def test_throughput_projects_latency_onto_the_poll_cycle():
    """4 s per call, 600 s cycle, serial: 150 per cycle, 144 cycles/day."""
    t = fq.throughput(4000.0, interval_s=600.0, concurrency=1)
    assert t["per_cycle_capacity"] == 150
    assert t["per_day_capacity"] == 21600


def test_concurrency_multiplies_capacity():
    a = fq.throughput(4000.0, concurrency=1)["per_day_capacity"]
    b = fq.throughput(4000.0, concurrency=8)["per_day_capacity"]
    assert b == a * 8


def test_a_slow_local_model_cannot_keep_up_serially():
    """The review's blocking finding, as arithmetic: at 30 s per call a 600 s cycle finishes 20,
    which is 2,880/day — under the ~1,250/day arrival rate only because of the cycle count, and
    one raised batch alone would overrun the interval."""
    t = fq.throughput(30_000.0, concurrency=1)
    assert t["per_cycle_capacity"] == 20


def test_batch_for_matches_the_designs_formula():
    """~1,250 clustered articles/day at a 600 s cycle needs ~9 per cycle; 1.5x headroom → 14.
    Today's default of 6 (864/day) is what the review showed diverging."""
    assert fq.batch_for(1250) == 14
    assert fq.batch_for(864, headroom=1.0) == 6
    assert fq.batch_for(0) == 1
