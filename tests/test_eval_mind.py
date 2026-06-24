"""Tests for the multi-seed / significance helpers in examples/eval_mind.py."""

import importlib.util
import pathlib

import numpy as np
import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "eval_mind", ROOT / "examples" / "eval_mind.py")
em = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(em)


def test_show_singleseed_has_no_pm():
    mean = pd.DataFrame({"a": [1.0]}, index=["m"])
    std = pd.DataFrame({"a": [0.0]}, index=["m"])
    assert "±" not in em._show(mean, std, ["a"], multiseed=False)


def test_show_multiseed_has_pm():
    mean = pd.DataFrame({"a": [1.0]}, index=["m"])
    std = pd.DataFrame({"a": [0.1]}, index=["m"])
    assert "±" in em._show(mean, std, ["a"], multiseed=True)


def test_wilcoxon_detects_consistent_difference():
    arr = np.zeros((6, 2, 1))
    arr[:, 0, 0] = [0.50, 0.60, 0.55, 0.52, 0.58, 0.51]   # method X
    arr[:, 1, 0] = [0.10, 0.20, 0.15, 0.12, 0.18, 0.11]   # P3 (ref), always lower
    pv = em._wilcoxon_vs_ref(arr, ["X", "P3"], ["m"], "P3", ["m"])
    assert pv.loc["X", "m"] < 0.05                         # 6 paired +ve diffs -> p=0.031


def test_wilcoxon_identical_is_nan():
    pv = em._wilcoxon_vs_ref(np.ones((6, 2, 1)), ["X", "P3"], ["m"], "P3", ["m"])
    assert np.isnan(pv.loc["X", "m"])


class _DS:
    """Minimal stand-in for rwe.data.Dataset (only what _alignment_report uses)."""

    def __init__(self, M):
        import scipy.sparse as sp
        self.matrix = sp.csr_matrix(np.asarray(M, dtype=float))
        self.n_users, self.n_items = self.matrix.shape


def test_alignment_report_perfect_when_theta_matches_clicks():
    # 2 left users click left items (pos -1), 2 right users click right (pos +1)
    M = [[1, 1, 0, 0], [1, 0, 0, 0], [0, 0, 1, 1], [0, 0, 0, 1]]
    item_pos = np.array([-1.0, -1.0, 1.0, 1.0])
    theta = np.array([-1.0, -1.0, 1.0, 1.0])          # = mean clicked-item lean
    st = em._alignment_report(_DS(M), theta, item_pos, verbose=False)
    assert st["n"] == 4
    assert st["pearson"] > 0.9
    assert st["expected_side"] == 1.0


def test_alignment_report_flags_a_sign_flipped_axis():
    M = [[1, 1, 0, 0], [1, 0, 0, 0], [0, 0, 1, 1], [0, 0, 0, 1]]
    item_pos = np.array([-1.0, -1.0, 1.0, 1.0])
    theta = np.array([1.0, 1.0, -1.0, -1.0])          # sign-flipped vs clicks
    st = em._alignment_report(_DS(M), theta, item_pos, verbose=False)
    assert st["pearson"] < 0                          # negative corr betrays the flip
    assert st["expected_side"] == 0.0
