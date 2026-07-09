"""Study Mode raw-metric framework — verification tests.

Proves the independent raw calculator (``examples/study_metrics.py``) (a) reproduces the worked
examples in ``docs/STUDY_MODE.md`` and (b) agrees with the PRODUCTION raw functions
(``health_report``) on identical inputs — the raw-layer "Expected vs Application" comparison, run as a
test. The percentile/displayed layer is deliberately out of scope here (population-dependent)."""

import math
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
import study_metrics as sm   # noqa: E402


def _read(cat, outlet, lean=None, political=False, register=None, emotion=None, title=""):
    return {"scored": {"category": cat, "outlet": outlet, "lean": lean, "political": political,
                       "register": register, "emotion": emotion, "title": title}}


# The shared history from docs/STUDY_MODE.md.
HISTORY = [
    _read("Politics", "BBC", lean=-1.2, political=True, register="reporting",
          emotion={"fear": .1, "outrage": .1, "analysis": .4, "positive": .2, "neutral": .2}),
    _read("Politics", "BBC", lean=-0.3, political=True, register="opinion",
          emotion={"fear": .2, "outrage": .3, "analysis": .2, "positive": .1, "neutral": .2}),
    _read("Business", "CNN", lean=0.9, political=True, register="reporting",
          emotion={"fear": .0, "outrage": .0, "analysis": .6, "positive": .3, "neutral": .1}),
    _read("Politics", "Fox", lean=1.4, political=True, register="opinion",
          emotion={"fear": .3, "outrage": .4, "analysis": .1, "positive": .0, "neutral": .2}),
    _read("Health", "NPR", lean=None, political=False, register="reporting",
          emotion={"fear": .0, "outrage": .0, "analysis": .5, "positive": .3, "neutral": .2}),
]


def test_worked_examples_match_the_doc():
    raw = sm.compute_all_raw(HISTORY)
    sd, td, pol, emo, rep = (raw["sourceDiversity"], raw["topicDiversity"], raw["political"],
                             raw["emotional"], raw["reportingRatio"])
    # Source Diversity: 1/HHI = 3.571 (NOT unique/total = 0.80)
    assert sd["unique_over_total"] == pytest.approx(0.80)
    assert sd["hhi"] == pytest.approx(0.28)
    assert sd["raw"] == pytest.approx(3.5714285, rel=1e-6)
    assert sd["top_2_share"] == pytest.approx(0.6)
    # Topic Diversity: H = 0.9503 nats, normalized by ln(3) = 0.865
    assert td["entropy_nats"] == pytest.approx(0.9502705, rel=1e-6)
    assert td["raw"] == pytest.approx(0.8649735, rel=1e-6)
    # Political: L/C/R = .25/.25/.50, cross-cutting = .50, echo = .333, balance = .667
    assert (pol["left_share"], pol["centre_share"], pol["right_share"]) == pytest.approx((0.25, 0.25, 0.50))
    assert pol["cross_cutting_share"] == pytest.approx(0.5)
    assert pol["echo_raw"] == pytest.approx(1 / 3, rel=1e-6)
    assert pol["balance_1_minus_echo"] == pytest.approx(2 / 3, rel=1e-6)
    # Emotional Balance = 1 - (fear+outrage) = 0.72; Reporting share = 0.60
    assert emo["raw_emotional_balance"] == pytest.approx(0.72)
    assert rep["raw_reporting_share"] == pytest.approx(0.6)


def test_raw_matches_production_raw_functions():
    rows = sm.verify_against_production(HISTORY)
    assert rows, "expected verification rows"
    for row in rows:
        assert row["pass"], f"raw mismatch: {row['metric']} expected={row['expected']} app={row['application']}"


def test_edge_cases():
    # empty history → NaN everywhere it should be
    empty = sm.compute_all_raw([])
    assert math.isnan(empty["topicDiversity"]["raw"])
    assert math.isnan(empty["sourceDiversity"]["raw"])
    assert math.isnan(empty["political"]["cross_cutting_share"])
    assert empty["readingTime"]["raw_total_minutes"] == 0

    # single topic → entropy 0 but C=1 → normalized NaN (can't be diverse across one bucket)
    one_topic = [_read("Politics", "BBC"), _read("Politics", "CNN")]
    assert sm.topic_diversity(one_topic)["entropy_nats"] == pytest.approx(0.0)
    assert math.isnan(sm.topic_diversity(one_topic)["raw"])
    # …but normalising by a catalog C>1 makes it a real 0.0
    assert sm.topic_diversity(one_topic, catalog_categories=8)["raw"] == pytest.approx(0.0)

    # single publisher → HHI 1 → effective 1
    one_pub = [_read("A", "BBC"), _read("B", "BBC"), _read("C", "BBC")]
    assert sm.source_diversity(one_pub)["raw"] == pytest.approx(1.0)

    # a perfectly balanced L/R political diet → echo 0 (fully balanced)
    balanced = [_read("P", "X", lean=-1.5, political=True), _read("P", "Y", lean=1.5, political=True)]
    assert sm.political_exposure(balanced)["echo_raw"] == pytest.approx(0.0)


def test_calculators_do_not_import_production():
    """The calculators must stand alone — production is imported only inside verify_against_production."""
    src = (ROOT / "examples" / "study_metrics.py").read_text()
    head = src.split("def verify_against_production")[0]
    assert "import health_report" not in head and "import api_server" not in head
