"""Every notebook in the repo must satisfy the nbformat 4 cell schema.

Why this exists: GitHub (and JupyterLab, and nbviewer) refuse to render a notebook whose cells
break the schema — the reader sees a bare "Invalid Notebook: 'outputs' is a required property"
instead of the page. Two notebooks had drifted this way unnoticed for a long time, because the
per-notebook smoke tests only asserted ``nbformat == 4`` and never checked the cells themselves:

  * ``deploy/information_health_colab.ipynb`` — a code cell with no ``outputs``/``execution_count``
  * ``notebooks/run_politosphere_eval.ipynb`` — a markdown cell carrying both (not allowed there)

The checks below are deliberately dependency-free (``nbformat`` is not a declared dependency, so
CI would skip a library-based test and keep missing the defect); when ``nbformat`` IS installed,
the full JSON-schema validation runs too, as a superset.
"""
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP_PARTS = {"node_modules", ".ipynb_checkpoints", ".git"}


def _notebooks():
    return sorted(p for p in ROOT.rglob("*.ipynb")
                  if not SKIP_PARTS & set(p.parts))


NOTEBOOKS = _notebooks()
IDS = [str(p.relative_to(ROOT)) for p in NOTEBOOKS]


def test_repo_has_notebooks():
    """Guard the guard: a glob that silently matches nothing would pass every test below."""
    assert len(NOTEBOOKS) >= 5


@pytest.mark.parametrize("path", NOTEBOOKS, ids=IDS)
def test_notebook_cells_match_the_nbformat_schema(path):
    nb = json.loads(path.read_text())
    assert nb.get("nbformat") == 4, "notebooks are nbformat 4"
    assert isinstance(nb.get("cells"), list) and nb["cells"], "a notebook has cells"

    for i, cell in enumerate(nb["cells"]):
        where = f"{path.name} cell {i}"
        kind = cell.get("cell_type")
        assert kind in ("code", "markdown", "raw"), f"{where}: bad cell_type {kind!r}"
        assert "source" in cell, f"{where}: source is required"
        assert "metadata" in cell, f"{where}: metadata is required"
        if kind == "code":
            # Both are REQUIRED on code cells — their absence is the exact renderer failure.
            assert isinstance(cell.get("outputs"), list), f"{where}: code cells need outputs (list)"
            assert "execution_count" in cell, f"{where}: code cells need execution_count"
        else:
            # ...and forbidden everywhere else (the inverse failure).
            for key in ("outputs", "execution_count"):
                assert key not in cell, f"{where}: {kind} cells must not carry {key}"


@pytest.mark.parametrize("path", NOTEBOOKS, ids=IDS)
def test_notebook_passes_full_nbformat_validation(path):
    """The superset check, when the library is available (it is not a declared dependency)."""
    nbformat = pytest.importorskip("nbformat")
    nbformat.validate(nbformat.read(str(path), as_version=4))
