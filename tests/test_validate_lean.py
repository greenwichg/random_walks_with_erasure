"""Tests for the axis-validation helpers (examples/validate_lean.py)."""

import importlib.util
import pathlib

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
