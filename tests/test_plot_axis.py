"""Tests for examples/plot_axis.py (the populated left<->right scale figure)."""

import importlib.util
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "plot_axis", ROOT / "examples" / "plot_axis.py")
pa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pa)


def test_plot_axis_counts_and_writes_file(tmp_path):
    item_pos = np.array([-2.0, -1.0, -0.5, 0.5, 1.0, 2.0])   # 3 left, 3 right
    user_pos = np.array([-1.0, -0.5, 0.5, 1.0])              # 2 left, 2 right
    out = tmp_path / "axis.png"
    st = pa.plot_axis(item_pos, user_pos, center=0.0, out=str(out))
    assert out.exists()
    assert st["n_items"] == 6 and st["n_users"] == 4
    assert abs(st["item_left"] - 0.5) < 1e-9
    assert abs(st["user_left"] - 0.5) < 1e-9


def test_plot_axis_drops_non_finite(tmp_path):
    item_pos = np.array([-1.0, np.nan, 1.0])
    user_pos = np.array([np.nan, -1.0, 1.0])
    st = pa.plot_axis(item_pos, user_pos, out=str(tmp_path / "a.png"))
    assert st["n_items"] == 2 and st["n_users"] == 2


def test_example_headlines_order_by_position():
    titles = ["leftmost", "mid", "rightmost"]
    pos = np.array([-2.0, 0.0, 2.0])
    left, right = pa._example_headlines(titles, pos, k=1)
    assert left[0][1] == "leftmost"
    assert right[0][1] == "rightmost"
