"""Tests for examples/ensemble_lean.py (multi-model text-lean ensembling)."""

import importlib.util
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "ensemble_lean", ROOT / "examples" / "ensemble_lean.py")
el = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(el)


def _write(path, mapping):
    path.write_text("news_id,position\n" + "\n".join(
        f"{k},{v}" for k, v in mapping.items()))


def test_zscore_standardises():
    z = el._zscore([1.0, 2.0, 3.0])
    assert abs(np.mean(z)) < 1e-9 and abs(np.std(z) - 1.0) < 1e-9


def test_ensemble_perfectly_correlated_recovers_reference(tmp_path):
    # B is a monotone rescale of A, so after z-scoring both models are identical;
    # the ensemble (rescaled to A's spread, centred) recovers A minus its mean.
    A = {"N1": -2.0, "N2": 0.0, "N3": 2.0, "N4": 1.0}
    B = {k: 0.5 * v for k, v in A.items()}
    fa, fb = tmp_path / "a.csv", tmp_path / "b.csv"
    _write(fa, A); _write(fb, B)
    ids, ens, M = el.ensemble([str(fa), str(fb)])
    em = dict(zip(ids, ens))
    mean_a = float(np.mean(list(A.values())))
    for k in A:
        assert abs(em[k] - (A[k] - mean_a)) < 1e-9
    assert abs(float(np.mean(ens))) < 1e-9                    # centred at 0


def test_ensemble_partial_overlap_counts_and_centres(tmp_path):
    A = {"N1": -2.0, "N2": 0.0, "N3": 2.0, "N4": 1.0}
    B = {"N1": -1.0, "N2": 0.5, "N3": 1.0}                    # N4 unscored by B
    fa, fb = tmp_path / "a.csv", tmp_path / "b.csv"
    _write(fa, A); _write(fb, B)
    ids, ens, M = el.ensemble([str(fa), str(fb)])
    pos = {nid: i for i, nid in enumerate(ids)}
    nmod = np.isfinite(M).sum(axis=1)
    assert nmod[pos["N4"]] == 1 and nmod[pos["N1"]] == 2      # overlap counted
    assert np.all(np.isfinite(ens))                           # every article has >=1 model
    assert abs(float(np.mean(ens))) < 1e-9                    # centred at 0


def test_ensemble_matches_target_std(tmp_path):
    A = {"N1": -2.0, "N2": -0.5, "N3": 0.5, "N4": 2.0}
    B = {"N1": -3.0, "N2": 1.0, "N3": -1.0, "N4": 4.0}        # different scale
    fa, fb = tmp_path / "a.csv", tmp_path / "b.csv"
    _write(fa, A); _write(fb, B)
    ids, ens, _ = el.ensemble([str(fa), str(fb)], target_std=1.0)
    assert abs(float(np.nanstd(ens)) - 1.0) < 1e-9            # rescaled to target


def test_pairwise_agreement_perfect(tmp_path):
    A = {"N1": -2.0, "N2": 0.0, "N3": 2.0, "N4": 1.0}
    B = {k: 0.5 * v for k, v in A.items()}
    fa, fb = tmp_path / "a.csv", tmp_path / "b.csv"
    _write(fa, A); _write(fb, B)
    _, _, M = el.ensemble([str(fa), str(fb)])
    rows = el.pairwise_agreement(M, ["A", "B"])
    assert len(rows) == 1
    _, _, n, sp, pe = rows[0]
    assert n == 4 and abs(sp - 1.0) < 1e-9 and abs(pe - 1.0) < 1e-9
