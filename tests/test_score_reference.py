"""The frozen reference cohort — the artifact that decides what a health score is ranked against.

Every metric in the report is a percentile inside a population, and `corpus_refresh` rebuilds that
population from the live catalog on every cycle. Freezing the reference is what stops a reader who
reads nothing from moving; these tests pin the properties that make it a benchmark rather than
another moving part.
"""

import importlib.util
import json
import pathlib

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "examples" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sr = _load("score_reference")
hr = _load("health_report")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("RWE_SCORE_REFERENCE", str(tmp_path / "ref.json"))
    monkeypatch.delenv("RWE_SCORE_REFERENCE_DISABLE", raising=False)


def test_absent_reference_reads_as_none_not_an_error():
    assert sr.load() is None


def test_capture_then_reuse():
    captured = sr.load_or_capture(lambda: {"topic": [1.0, 2.0, 3.0]})
    assert captured == {"topic": [1.0, 2.0, 3.0]}
    assert sr.load() == {"topic": [1.0, 2.0, 3.0]}


def test_capture_is_first_write_wins():
    """THE property. A later corpus refresh must not silently re-point the benchmark — that is
    precisely the defect. Re-versioning is a deliberate act, never a side effect of ingest."""
    sr.load_or_capture(lambda: {"topic": [1.0, 2.0, 3.0]})
    again = sr.load_or_capture(lambda: {"topic": [90.0, 91.0, 92.0]})
    assert again == {"topic": [1.0, 2.0, 3.0]}, "a second capture overwrote the benchmark"


def test_a_corrupt_reference_degrades_instead_of_taking_the_report_down():
    pathlib.Path(sr.path()).write_text("{not json", encoding="utf-8")
    assert sr.load() is None


def test_a_reference_from_a_different_capture_rule_is_ignored():
    """The version travels with the file: values captured under a different rule answer a
    different question, and ranking against them would be silently wrong rather than loudly."""
    pathlib.Path(sr.path()).write_text(
        json.dumps({"schemaVersion": sr.SCHEMA_VERSION + 1, "metrics": {"topic": [1.0]}}),
        encoding="utf-8")
    assert sr.load() is None


def test_disable_switch_restores_the_pre_fix_behaviour(monkeypatch):
    monkeypatch.setenv("RWE_SCORE_REFERENCE_DISABLE", "1")
    assert sr.load_or_capture(lambda: {"topic": [1.0]}) is None
    assert sr.load() is None


def test_the_write_is_atomic_and_leaves_no_temp_files():
    sr.save({"topic": [1.0, 2.0]})
    d = pathlib.Path(sr.path()).parent
    assert not list(d.glob("*.tmp")), "a crash mid-write must not leave a half-written benchmark"
    assert json.loads(pathlib.Path(sr.path()).read_text())["schemaVersion"] == sr.SCHEMA_VERSION


# --------------------------------------------------------------------------- #
# ranking inside a frozen cohort
# --------------------------------------------------------------------------- #
def test_percentile_in_matches_the_population_convention_at_the_median():
    ref = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert hr.percentile_in(3.0, ref) == 50.0          # the median is the median either way
    assert hr.percentile_in(0.0, ref) == 0.0
    assert hr.percentile_in(9.0, ref) == 100.0


def test_percentile_in_degrades_on_missing_input():
    assert hr.percentile_in(None, [1.0]) is None
    assert hr.percentile_in(float("nan"), [1.0]) is None
    assert hr.percentile_in(1.0, []) is None


def test_freeze_reference_inverts_echo_so_higher_is_always_healthier():
    """Echo Chamber is the one metric where less is better; the reference stores it already
    inverted, so ranking never has to remember which direction a metric runs."""
    pop = {"echo": np.array([0.1, 0.9]), "cat_u": np.array(["a", "b"])}
    ref = hr.freeze_reference(pop)
    assert ref["echo"] == sorted([-0.1, -0.9])


def test_freeze_reference_skips_metrics_the_corpus_cannot_measure():
    pop = {"topic": np.array([0.5, 0.6]), "cat_u": np.array(["a", "b"]), "sel": None}
    ref = hr.freeze_reference(pop)
    assert "sel" not in ref and "topic" in ref


def test_topic_is_ranked_on_a_catalog_INDEPENDENT_scale():
    """The subtle one, and the reason a naive frozen reference is wrong rather than merely
    different: `topic` is entropy over ln(n_categories), a denominator owned by the corpus. Two
    corpora with different category counts put the same reading on different scales — measured, a
    reader at the 95th percentile scored 58 against a reference captured moments earlier."""
    entropy = 1.2
    small = {"topic": np.array([entropy / np.log(10)]), "cat_u": np.array(["c"] * 10)}
    large = {"topic": np.array([entropy / np.log(40)]), "cat_u": np.array(["c"] * 40)}
    # Same reading, different corpora — the values ranked must agree.
    assert hr.freeze_reference(small)["topic"] == pytest.approx(
        hr.freeze_reference(large)["topic"], rel=1e-9)
