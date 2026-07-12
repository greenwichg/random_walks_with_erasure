"""Lightweight smoke test for the Recommendation Validation Colab notebook.

Validates it structurally (valid nbformat, the expected sections, offline/no-server, no matplotlib)
and that the CLI it drives actually works — without executing the notebook (which clones + installs).
The pipeline itself is covered by tests/test_rec_pipeline.py; this only guards the notebook.
"""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
NB = ROOT / "deploy" / "rec_validation_colab.ipynb"


def _nb():
    return json.loads(NB.read_text())


def _text():
    return "".join("".join(c["source"]) for c in _nb()["cells"])


def test_notebook_is_valid_nbformat():
    nb = _nb()
    assert nb["nbformat"] == 4
    # 7 original cells + the Story Coverage & Health audit section (1 md + 4 code)
    assert len(nb["cells"]) == 12 and all("cell_type" in c for c in nb["cells"])


def test_notebook_has_the_expected_sections():
    text = _text()
    for marker in ("Recommendation Validation Pipeline", "1 · Setup", "2 · Run the validation",
                   "3 · Full stage-by-stage report", "4 · Understand one recommendation",
                   "5 · Explanation-type coverage",
                   "6 · Story Coverage & Recommendation Health audit",
                   "6a · Build the audit document", "6b · The canonical text report",
                   "6c · Tables", "6d · Charts", "7 · Scope"):
        assert marker in text, "missing notebook section: " + marker


def test_notebook_reuses_the_cli_and_package_not_new_logic():
    text = _text()
    assert "examples/validate_recs.py" in text          # runs via the shipped launcher (D2)
    assert "--history" in text and "My Reading History" in text   # the debugging mode
    assert "from rec_pipeline import" in text           # in-process only for the teaching cell
    assert "evidence_subset_of_context" in text         # the evidence⊆context invariant is shown


def test_notebook_defaults_are_safe_and_offline():
    text = _text()
    assert 'MODE = "Golden Personas"' in text
    assert 'SCENARIO = "all"' in text
    assert "FAST = True" in text
    assert 'GITHUB_TOKEN = ""' in text
    # offline: no web app / server / tunnel like the playground (D4). The audit section (6d)
    # deliberately amends the original no-matplotlib rule: Colab preinstalls matplotlib+pandas,
    # so charts add ZERO installs — the invariant that matters is "no extra pip deps".
    assert "npm " not in text and "cloudflared" not in text and "next-server" not in text
    assert text.count('"install"') == 1 and '".[serve]"' in text   # the ONE engine install, nothing else
    # the audit section defaults to the offline golden-scenario demo (no DB required)
    assert 'DB_URL = ""' in text
    # ...and imports the SHARED auditor functions rather than reimplementing the analysis
    assert "import audit_story_coverage as asc" in text
    assert "asc.full_report" in text and "asc.print_report" in text


def test_all_code_cells_compile():
    for c in _nb()["cells"]:
        if c["cell_type"] == "code":
            compile("".join(c["source"]), "cell", "exec")


def test_the_cli_the_notebook_drives_actually_passes():
    """Run exactly what cell 2 issues (one fast scenario) and confirm PASS + valid JSON."""
    p = subprocess.run([sys.executable, "examples/validate_recs.py", "--scenario", "same_story",
                        "--fast", "--report", "json"], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-2000:]
    run = json.loads(p.stdout)
    assert run["passed"] is True and run["fixtures"] == 1
    checks = run["results"][0]["checks"]
    assert any("evidence ⊆ context" in c["check"] and c["passed"] for c in checks)
