"""Smoke test for the dedicated Story Coverage & Recommendation Health audit Colab notebook.

Guards it structurally (valid nbformat, the expected sections, offline/no-server, golden-demo
default) and functionally (the SHARED auditor path it drives — full_report -> print_report on the
golden fixture — actually produces the canonical report). The auditor itself is covered by
tests/test_audit_story_coverage.py; this only guards the notebook wrapper.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
NB = ROOT / "deploy" / "story_coverage_audit_colab.ipynb"
sys.path.insert(0, str(ROOT / "examples"))


def _nb():
    return json.loads(NB.read_text())


def _text():
    return "".join("".join(c["source"]) for c in _nb()["cells"])


def test_notebook_is_valid_nbformat():
    nb = _nb()
    assert nb["nbformat"] == 4
    # 2 markdown (intro + scope) + 5 code (setup, corpus, report, tables, charts)
    assert len(nb["cells"]) == 7 and all("cell_type" in c for c in nb["cells"])


def test_notebook_has_the_expected_sections():
    text = _text()
    for marker in ("Story Coverage & Recommendation Health", "1 · Setup",
                   "2 · Choose the corpus", "3 · The canonical text report",
                   "4 · Tables", "5 · Charts", "Scope & auditing your live beta"):
        assert marker in text, "missing notebook section: " + marker


def test_notebook_reuses_the_shared_auditor_not_new_logic():
    text = _text()
    # imports the SHARED functions the CLI uses — never reimplements the analysis
    assert "import audit_story_coverage as asc" in text
    assert "asc.full_report" in text and "asc.print_report" in text
    # the golden demo reuses the pipeline's own corpus builder (no bespoke seeding)
    assert "from rec_pipeline import extract, pipeline" in text
    # it must NOT redefine the report — only render the shared document
    assert "def full_report" not in text and "def print_report" not in text


def test_notebook_defaults_are_safe_and_offline():
    text = _text()
    assert 'SOURCE  = "Golden demo"' in text          # out-of-the-box, no DB required
    assert "DB_URL" in text and "USER_ID" in text      # the real-corpus knobs are present
    assert 'GITHUB_TOKEN = ""' in text
    # offline: no web app / server / tunnel — read-only audit
    assert "npm " not in text and "cloudflared" not in text and "next-server" not in text
    assert text.count('"install"') == 1 and '".[serve]"' in text   # the ONE engine install
    # this notebook DOES render pandas tables + a matplotlib chart (Colab preinstalls both)
    assert "import pandas" in text and "import matplotlib" in text


def test_all_code_cells_compile():
    for c in _nb()["cells"]:
        if c["cell_type"] == "code":
            compile("".join(c["source"]), "cell", "exec")


def test_the_shared_auditor_path_produces_the_report():
    """Exactly what cell 2 + cell 3 do on the golden demo: build the document with the shared
    full_report, render it with the shared print_report, and confirm the canonical report text."""
    import io
    import contextlib

    import audit_story_coverage as asc
    import evidence_resolver as er
    from rec_pipeline import extract, pipeline

    case = extract.build(pipeline.load_fixture("story_over_bridge"), keep_env=True)
    er._INDEX_CACHE.update(key=None, index=None)
    doc = asc.full_report(case.store, case.reader_uid)
    assert doc["coverageRatePercent"] is not None
    assert doc["verdict"]["code"] in ("insufficient_data", "coverage", "freshness",
                                      "ranking", "none")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        asc.print_report(doc)
    out = buf.getvalue()
    assert f"Story Coverage Rate: {doc['coverageRatePercent']}%" in out
    assert "==== Recommendation feed ====" in out
