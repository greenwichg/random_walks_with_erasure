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
