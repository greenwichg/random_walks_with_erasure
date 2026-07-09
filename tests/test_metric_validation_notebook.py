"""Lightweight smoke test for the Metric Validation Colab notebook.

Validates the notebook structurally (valid nbformat, the expected sections, offline-by-default) and
that the CLI it drives actually works — without executing the notebook (which clones + pip-installs).
Full metric correctness is covered by tests/test_metric_pipeline.py; this only guards the notebook.
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
NB = ROOT / "deploy" / "metric_validation_colab.ipynb"


def _nb():
    return json.loads(NB.read_text())


def test_notebook_is_valid_nbformat():
    nb = _nb()
    assert nb["nbformat"] == 4
    assert nb["cells"] and all("cell_type" in c for c in nb["cells"])


def test_notebook_has_the_expected_sections():
    text = "".join("".join(c["source"]) for c in _nb()["cells"])
    for marker in ("1 · Setup", "2 · Choose the validation source",
                   "3 · Run the Metric Validation Pipeline", "4 · Visualizations",
                   "5 · Validation report", "6 · Export", "7 · Dashboard Verification"):
        assert marker in text, "missing notebook section: " + marker
    # it drives the pipeline, not the product engine, by default
    assert "validate_metrics.py" in text and "metric_pipeline" in text


def test_notebook_is_offline_by_default():
    """The default source is a golden persona and Dashboard Verification is opt-in (blank URL)."""
    text = "".join("".join(c["source"]) for c in _nb()["cells"])
    assert 'SOURCE       = "golden"' in text
    assert 'DASHBOARD_BASE_URL = ""' in text            # skipped unless the user sets a running engine
    # the notebook must not boot the product web/engine stack
    assert "api_fastapi" not in text and "next build" not in text


def test_cli_the_notebook_invokes_actually_works():
    """Run the exact command the Run cell issues and confirm it passes and emits valid JSON."""
    p = subprocess.run([sys.executable, "examples/validate_metrics.py", "--golden", "balanced",
                        "--report", "json"], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    payload = json.loads(p.stdout)
    assert payload["passed"] is True
    assert payload["summary"]["raw"]["allPass"] is True
