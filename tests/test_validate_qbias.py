"""Tests for examples/validate_qbias.py (AllSides-gold validation; the classifier is
mocked via an injected score_fn, so no torch/transformers/GPU is needed here)."""

import csv
import importlib.util
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "validate_qbias", ROOT / "examples" / "validate_qbias.py")
vq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vq)

LEAN_CSV = str(ROOT / "examples" / "data" / "outlet_lean.csv")


def test_label_to_pos():
    assert vq.label_to_pos("left") == -1 and vq.label_to_pos("Lean Left") == -1
    assert vq.label_to_pos("right") == 1 and vq.label_to_pos("RIGHT") == 1
    assert vq.label_to_pos("center") == 0 and vq.label_to_pos("neutral") == 0
    assert np.isnan(vq.label_to_pos("")) and np.isnan(vq.label_to_pos("mixed"))


def test_pick_col_autodetect_and_override():
    fn = ["date", "heading", "text", "bias_rating", "source"]
    assert vq._pick_col(fn, vq._HEADLINE_COLS) == "heading"
    assert vq._pick_col(fn, vq._BIAS_COLS) == "bias_rating"
    assert vq._pick_col(fn, vq._OUTLET_COLS) == "source"
    assert vq._pick_col(fn, vq._HEADLINE_COLS, override="text") == "text"   # override wins


def _write_qbias(tmp_path):
    p = tmp_path / "qbias.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "heading", "text", "bias_rating", "source"])
        for r in [("d", "Left headline one", "body a", "left", "CNN"),
                  ("d", "Center headline", "body b", "center", "Reuters"),
                  ("d", "Right headline", "body c", "right", "Fox News"),
                  ("d", "Left headline two", "body d", "left", "CNN"),
                  ("d", "Right headline two", "body e", "right", "Fox News"),
                  ("d", "Unlabeled one", "body f", "mixed", "Some Blog")]:   # 'mixed' dropped
            w.writerow(r)
    return str(p)


def test_load_qbias_detects_columns_and_drops_unlabeled(tmp_path):
    texts, gold, outlets, cols = vq.load_qbias(_write_qbias(tmp_path))
    assert len(texts) == 5 and list(gold) == [-1, 0, 1, -1, 1]           # 'mixed' row dropped
    assert cols["headline"] == "heading" and cols["bias"] == "bias_rating"
    assert texts[0] == "Left headline one"                               # headline only by default
    assert outlets == ["CNN", "Reuters", "Fox News", "CNN", "Fox News"]
    t2, _, _, _ = vq.load_qbias(_write_qbias(tmp_path), use_text=True)   # append body
    assert t2[0].startswith("Left headline one. body a")


def test_outlet_positions_join_via_norm():
    table = vq.load_lean_table(LEAN_CSV)
    opos = vq.outlet_positions(["CNN", "Reuters", "Fox News", "Nowhere Local"], table)
    assert opos[0] < 0 and opos[1] == 0 and opos[2] > 0 and np.isnan(opos[3])


def test_run_perfect_classifier_and_outlet_block(tmp_path):
    csv_path = _write_qbias(tmp_path)
    # a perfect classifier: positions match the gold sign (scaled to the [-2,2] axis)
    out = vq.run(csv_path, score_fn=lambda texts: np.array([-2.0, 0.0, 2.0, -2.0, 2.0]),
                 lean_csv=LEAN_CSV)
    assert "TEXT classifier  vs  AllSides gold" in out
    assert "Cohen kappa +1.000" in out and "accuracy 100%" in out
    assert "OUTLET-lean  vs  AllSides gold" in out and "coverage" in out
    assert "IN-DISTRIBUTION" in out                                      # honest caveat present


def test_run_anti_correlated_classifier_scores_low(tmp_path):
    csv_path = _write_qbias(tmp_path)
    out = vq.run(csv_path, score_fn=lambda texts: np.array([2.0, 0.0, -2.0, 2.0, -2.0]))
    assert "TEXT classifier  vs  AllSides gold" in out
    assert "Cohen kappa -" in out                                       # sign-flipped -> negative
