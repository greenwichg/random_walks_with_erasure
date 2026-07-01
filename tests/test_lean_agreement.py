"""Tests for examples/lean_agreement.py (article-level reliability of two lean models)."""

import importlib.util
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "lean_agreement", ROOT / "examples" / "lean_agreement.py")
la = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(la)


def test_bucketize_band_and_nan():
    b = la.bucketize([-2.0, -0.5, 0.0, 0.5, 2.0, np.nan], band=1.0)
    # |score| <= 1 -> Center(0); < -1 -> Left(-1); > 1 -> Right(+1); NaN preserved
    assert b[0] == -1 and b[4] == 1
    assert b[1] == 0 and b[2] == 0 and b[3] == 0
    assert np.isnan(b[5])


def test_bucketize_terciles_are_scale_free():
    # a monotonic ramp: terciles put the lowest third Left, middle Center, top Right,
    # regardless of the absolute scale
    x = np.arange(9, dtype=float)          # 0..8
    b = la.bucketize(x, terciles=True)
    assert list(b[:3]) == [-1, -1, -1]
    assert list(b[-3:]) == [1, 1, 1]


def test_kappa_perfect_and_chance():
    a = [-1, -1, 0, 0, 1, 1]
    k_perfect, po, C = la.cohens_kappa(a, a, labels=(-1, 0, 1))
    assert abs(k_perfect - 1.0) < 1e-9 and abs(po - 1.0) < 1e-9
    assert C.trace() == len(a)
    # identical marginals but shuffled so half match -> kappa well below 1
    b = [-1, 0, -1, 1, 0, 1]
    k_mix, _, _ = la.cohens_kappa(a, b, labels=(-1, 0, 1))
    assert k_mix < 0.5


def test_pair_reliability_perfect_agreement():
    x = np.array([-2.0, -1.5, 0.0, 1.5, 2.0])
    r = la.pair_reliability(x, x.copy(), band=1.0)
    assert r["n"] == 5
    assert abs(r["spearman"] - 1.0) < 1e-9
    assert abs(r["kappa3"] - 1.0) < 1e-9
    assert r["flip_rate"] == 0.0


def test_pair_reliability_sign_flip_is_caught():
    x = np.array([-2.0, -2.0, 2.0, 2.0])
    y = -x                                  # every side is flipped
    r = la.pair_reliability(x, y, band=1.0)
    assert r["spearman"] < 0                # anti-correlated
    assert r["flip_rate"] == 1.0            # all four flip L<->R
    assert r["kappa3"] < 0                  # worse than chance on L/C/R


def test_pair_reliability_ignores_unshared_and_short(tmp_path):
    a = np.array([-2.0, np.nan, 1.0])
    b = np.array([np.nan, 1.0, 1.0])        # only the 3rd article is scored by both
    r = la.pair_reliability(a, b, band=1.0)
    assert r["n"] == 1                      # 1 shared < 3 -> stats stay NaN
    assert np.isnan(r["spearman"]) and np.isnan(r["kappa3"])


def test_end_to_end_on_two_csvs(tmp_path, capsys):
    # two models that agree on the extremes but split on a middle article
    (tmp_path / "m1.csv").write_text(
        "news_id,position\nN1,-2.0\nN2,-1.8\nN3,0.2\nN4,1.7\nN5,2.0\n")
    (tmp_path / "m2.csv").write_text(
        "news_id,position\nN1,-1.9\nN2,-2.0\nN3,1.5\nN4,1.6\nN5,1.9\n")
    out = tmp_path / "disagree.csv"
    import sys
    argv = sys.argv
    sys.argv = ["lean_agreement.py", str(tmp_path / "m1.csv"),
                str(tmp_path / "m2.csv"), "--out", str(out)]
    try:
        la.main()
    finally:
        sys.argv = argv
    text = capsys.readouterr().out
    assert "Cohen kappa" in text and "SIDE FLIPS" in text
    assert out.exists()
    # the audit CSV flags the N3 disagreement (Center vs Right)
    body = out.read_text()
    assert "N3" in body and body.strip().endswith(",1") or ",1\n" in body
