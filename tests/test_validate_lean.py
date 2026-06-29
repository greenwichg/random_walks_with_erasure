"""Tests for the axis-validation helpers (examples/validate_lean.py)."""

import importlib.util
import pathlib
import subprocess
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "validate_lean", ROOT / "examples" / "validate_lean.py")
vl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vl)


def test_correlate_perfect():
    r = vl._correlate([-2, -1, 0, 1, 2], [-1, -0.5, 0, 0.5, 1])
    assert r["n"] == 5
    assert r["spearman"] > 0.99 and r["pearson"] > 0.99
    assert r["sign_acc"] == 1.0           # signs of the non-zero pairs all agree


def test_correlate_anticorrelated():
    r = vl._correlate([-1, 0, 1], [1, 0, -1])
    assert r["spearman"] < -0.99


def test_correlate_handles_nan_and_constant():
    r = vl._correlate([1.0, np.nan, 2.0], [np.nan, 1.0, 2.0])
    assert r["n"] == 1                     # only the last pair is finite in both


def test_stratified_sample_spans_range():
    scores = np.array([5.0, 1.0, 3.0, 2.0, 4.0, 0.0])
    idx = vl._stratified_sample(scores, 3)
    assert len(idx) == 3
    assert scores[idx[0]] == scores.min() and scores[idx[-1]] == scores.max()


def test_weighted_kappa_bounds():
    a = [-1, 0, 1, -1, 1, 0]
    assert abs(vl._weighted_kappa(a, a) - 1.0) < 1e-9                  # perfect -> 1
    assert vl._weighted_kappa([-1, -1, 1, 1], [1, 1, -1, -1]) < -0.9   # perfect anti -> -1
    assert abs(vl._weighted_kappa([-1, -1, 1, 1], [-1, 1, -1, 1])) < 1e-9   # chance ~0
    near = vl._weighted_kappa([-1, 0, 1, 1], [-1, 0, 0, 1])
    far = vl._weighted_kappa([-1, 0, 1, 1], [1, 0, -1, -1])
    assert near > far


def test_pairwise_irr_three_raters():
    r1 = {f"N{i}": v for i, v in enumerate([-1, -1, 0, 1, 1, 0, -1, 1])}
    r2 = {f"N{i}": v for i, v in enumerate([-1, 0, 0, 1, 1, 0, -1, 1])}
    r3 = {f"N{i}": v for i, v in enumerate([-1, -1, 0, 1, 0, 0, -1, 1])}
    irr = vl._pairwise_irr([r1, r2, r3])
    assert irr["n_pairs"] == 3 and irr["median_overlap"] == 8
    assert irr["mean_spearman"] > 0.7 and irr["mean_wkappa"] > 0.6


def test_consensus_is_per_item_median():
    r1, r2, r3 = {"A": 1.0, "B": -1.0, "C": 0.0}, {"A": 1.0, "B": 0.0}, {"A": -1.0, "B": -1.0, "C": 0.0}
    cons = vl._consensus([r1, r2, r3], {"A", "B", "C"})
    assert cons["A"] == 1.0 and cons["B"] == -1.0 and cons["C"] == 0.0


def test_cli_raters_reports_irr_and_consensus(tmp_path):
    ids = [f"N{i}" for i in range(8)]
    (tmp_path / "lean.csv").write_text("news_id,position\n" + "\n".join(
        f"{n},{v}" for n, v in zip(ids, [-2, -1.5, -0.5, 0, 0.5, 1, 1.5, 2])))
    (tmp_path / "r1.tsv").write_text("news_id,position\n" + "\n".join(
        f"{n},{v}" for n, v in zip(ids, [-1, -1, 0, 0, 1, 1, 1, 1])))
    (tmp_path / "r2.tsv").write_text("news_id,position\n" + "\n".join(
        f"{n},{v}" for n, v in zip(ids, [-1, -1, -1, 0, 0, 1, 1, 1])))
    r = subprocess.run(
        [sys.executable, str(ROOT / "examples" / "validate_lean.py"),
         "--lean", str(tmp_path / "lean.csv"),
         "--raters", str(tmp_path / "r1.tsv"), str(tmp_path / "r2.tsv")],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "inter-rater agreement (2 raters" in r.stdout
    assert "mean weighted kappa" in r.stdout and "axis vs 2-rater consensus" in r.stdout
