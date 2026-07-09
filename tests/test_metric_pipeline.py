"""Metric Validation Pipeline — tests.

Proves the additive pipeline (``examples/metric_pipeline/``) validates the Information-Health metrics
against the UNCHANGED production engine at both layers:

  * RAW      — the independent engine (``study_metrics``) equals ``health_report.compute`` driven over
               a corpus built from the same reads, for every golden persona;
  * DISPLAYED — an independent percentile ranking equals ``health_report.percentiles`` over the pinned
               six-persona population, including the Echo-Chamber inversion the engine applies.

Plus the data-quality rules, drift detection, the independent-percentile re-derivation, and a guard
that the early stages never import production (only Stages 6/7 may).
"""

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

import metric_pipeline as mp                                   # noqa: E402  (also puts root on path)
from metric_pipeline import compare, extract, history, normalize, quality  # noqa: E402
import health_report as hr                                     # noqa: E402


def _population():
    pop = extract.extract_golden_population()
    return [(name, rows) for name, rows in pop.items()]


def test_golden_population_passes(tmp_path):
    """The pinned six-persona population validates clean at every layer, with a baseline drift."""
    res = mp.run_pipeline("all", _population(), history_file=tmp_path / "h.jsonl")
    assert res.raw_summary["allPass"], res.raw_rows
    assert res.displayed_summary["allPass"], res.displayed_rows
    assert res.helper_summary["allPass"], res.helper_rows
    assert res.drift["baseline"] is True
    assert res.passed is True
    assert res.catalog_categories == 7                          # union of the personas' topics


@pytest.mark.parametrize("name", extract.GOLDEN_NAMES)
def test_each_golden_raw_matches_production(name, tmp_path):
    """Every persona's RAW metrics match production exactly (raw layer is reproducible per reader)."""
    res = mp.run_pipeline(name, [(name, extract.extract_golden(name))],
                          history_file=tmp_path / "h.jsonl")
    assert res.raw_summary["allPass"], [r for r in res.raw_rows if r["pass"] is False]
    assert res.helper_summary["allPass"], [r for r in res.helper_rows if r["pass"] is False]


def test_echo_chamber_displayed_inversion(tmp_path):
    """The engine ranks 1 − echo, so a heavier echo chamber must get a LOWER displayed score."""
    res = mp.run_pipeline("all", _population(), history_file=tmp_path / "h.jsonl")
    app = {r["reader"]: r["application"] for r in res.displayed_rows if r["metric"] == "echoChamber"}
    # echo_chamber (raw echo = 1) ranks below balanced (raw echo = 0) on the displayed score.
    assert app["echo_chamber"] < app["balanced"]
    assert app["echo_chamber"] == pytest.approx(12.5)
    assert app["balanced"] == pytest.approx(75.0)


def test_independent_percentiles_match_health_report():
    """The pipeline's own percentile re-derivation equals ``health_report.percentiles`` (ties, lone,
    NaN) — the independent implementation the displayed layer is validated against."""
    import math
    for vec in ([3.0, 1.0, 2.0], [5.0, 5.0, 1.0, 9.0], [7.0], [float("nan"), 2.0, 2.0, 8.0]):
        ours = compare.independent_percentiles(vec)
        theirs = hr.percentiles(vec)
        for a, b in zip(ours, theirs):
            assert (math.isnan(a) and math.isnan(b)) or a == pytest.approx(b)


def test_quality_rules_flag_dirty_data():
    reads = normalize.normalize([
        {"scored": {"category": "Politics", "outlet": "", "political": True, "lean": None}},
        {"scored": {"category": "", "outlet": "BBC", "emotion": {"fear": 0.5}}},   # sums to 0.5
    ])
    rep = quality.check_quality(reads)
    rules = {i.rule for i in rep.issues}
    assert {"political_without_lean", "missing_publisher", "missing_category",
            "emotion_sum_off"} <= rules
    assert rep.ok is True                                        # warnings never block

    empty = quality.check_quality([])
    assert empty.ok is False and empty.errors[0].rule == "empty_history"


def test_drift_detects_a_changed_metric(tmp_path):
    hf = tmp_path / "h.jsonl"
    read = lambda reg: [{"scored": {"category": "Politics", "outlet": "BBC", "lean": -0.9,
                                    "political": True, "register": reg}}]
    mp.run_pipeline("ds", [("r", read("reporting"))], record=True, history_file=hf)   # baseline
    res = mp.run_pipeline("ds", [("r", read("opinion"))], record=True, history_file=hf)  # 1.0 → 0.0
    assert res.drift["drifted"] is True
    moved = {(row["reader"], row["metric"]) for row in res.drift["rows"]}
    assert ("r", "reportingRatio") in moved


def test_early_stages_do_not_import_production():
    """Extract/Normalize/Quality/Engine must not touch production — only Stages 6/7 (production.py,
    compare.py) may import ``health_report``."""
    pkg = ROOT / "examples" / "metric_pipeline"
    for mod in ("extract.py", "normalize.py", "quality.py", "engine.py"):
        src = (pkg / mod).read_text()
        assert "import health_report" not in src and "import api_server" not in src, mod
