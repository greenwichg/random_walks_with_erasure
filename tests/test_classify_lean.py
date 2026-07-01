"""Tests for the text-lean classifier helpers (examples/classify_lean.py).

Loads the script by path and exercises only its pure helpers -- the heavy
transformers/torch import lives inside main(), so these run with no model.
"""

import importlib.util
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "classify_lean", ROOT / "examples" / "classify_lean.py")
cl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cl)


def test_positions_from_probs():
    probs = [[1, 0, 0], [0, 0, 1], [0.5, 0, 0.5], [0.25, 0.5, 0.25]]
    out = cl._positions_from_probs(probs, [-1, 0, 1], scale=2.0)
    np.testing.assert_allclose(out, [-2.0, 2.0, 0.0, 0.0])


def test_text_join():
    assert cl._text("Title", "Body") == "Title. Body"
    assert cl._text("Title", "") == "Title"


def test_read_articles_fixture():
    rows = cl._read_articles(ROOT / "tests" / "fixtures" / "mind_demo" / "news.tsv")
    assert len(rows) == 8
    nid, sub, title, abstract = rows[0]
    assert nid == "N1" and sub == "newspolitics" and "Senate" in title


def test_confidence_from_probs_is_top2_margin():
    probs = [[1.0, 0.0, 0.0],         # certain -> margin 1
             [0.5, 0.5, 0.0],         # tie between two -> 0
             [0.4, 0.35, 0.25],       # near-tie (centre-boundary) -> 0.05
             [1 / 3, 1 / 3, 1 / 3]]   # maximally ambiguous -> 0
    out = cl._confidence_from_probs(probs)
    np.testing.assert_allclose(out, [1.0, 0.0, 0.05, 0.0], atol=1e-9)
    # a single 1-D probability row is accepted too
    assert abs(cl._confidence_from_probs([0.7, 0.2, 0.1])[0] - 0.5) < 1e-9
