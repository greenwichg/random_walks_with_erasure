"""F2 — the Article Analyzer cross-language contract anchor.

Asserts the REAL analyzer still reproduces the committed golden fixtures (so any change to the
analyzer's output shape breaks this test) and that its output is the supported ``analysisVersion``.
The *same* JSON fixtures are consumed by the web mapper test
(``web/lib/analysis-presentation.test.ts``), so a contract change breaks a test on at least one side.

Regenerate the goldens ONLY after an intentional contract change::

    python tests/fixtures/analysis/build_analysis_fixtures.py
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))
sys.path.insert(0, str(ROOT / "tests" / "fixtures" / "analysis"))

import article_analyzer as aa                 # noqa: E402
import build_analysis_fixtures as fx          # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "analysis"
SUPPORTED_ANALYSIS_VERSION = 1


@pytest.fixture(scope="module")
def produced():
    """What the analyzer produces right now, from the shared deterministic seed."""
    return fx.build_from_fresh_store()


@pytest.fixture(scope="module")
def produced_authed():
    """What the analyzer + reader enrichment produce right now (measured-reader contexts)."""
    return fx.build_authed_from_fresh_store()


@pytest.mark.parametrize("name", fx.CASES)
def test_analyzer_reproduces_golden(produced, name):
    golden = json.loads((FIX / f"{name}.json").read_text())
    assert produced[name] == golden, (
        f"{name}: the analyzer's output drifted from the committed golden fixture. If this change "
        f"is intentional, regenerate: python tests/fixtures/analysis/build_analysis_fixtures.py")


@pytest.mark.parametrize("name", fx.AUTHED_CASES)
def test_analyzer_and_enrichment_reproduce_authed_golden(produced_authed, name):
    """A3: the full enriched analysis (analysis + reader-relative sections) still matches its
    committed golden — so a change to analysis_enrichment breaks a test on the backend side, and
    the web mapper (A3.2) consumes the SAME fixtures."""
    golden = json.loads((FIX / f"{name}.json").read_text())
    assert produced_authed[name] == golden, (
        f"{name}: the enriched analysis drifted from the committed golden. If intentional, "
        f"regenerate: python tests/fixtures/analysis/build_analysis_fixtures.py")


def test_supported_analysis_version():
    """The analyzer and every golden (anonymous + authed) are the version the web is written against."""
    assert aa.ANALYSIS_VERSION == SUPPORTED_ANALYSIS_VERSION
    for name in (*fx.CASES, *fx.AUTHED_CASES):
        golden = json.loads((FIX / f"{name}.json").read_text())
        assert golden["analysisVersion"] == SUPPORTED_ANALYSIS_VERSION


def test_authed_goldens_carry_reader_sections():
    """Guard the authed fixtures: sections are present and the two scenarios stay distinct."""
    bridge = json.loads((FIX / "authed_bridge.json").read_text())
    familiar = json.loads((FIX / "authed_familiar.json").read_text())
    assert bridge["explanation"]["type"] == "bridge" and bridge["recommendation"]["wouldBroaden"] is True
    assert familiar["explanation"]["type"] == "topic_continuity"
    assert familiar["recommendation"]["wouldBroaden"] is False
    # A3.3a: both goldens pin the story-relative nextArticle (AP via the canonical-URL tie-break),
    # with exactly the contract's three keys — no fabricated engine metadata.
    for g in (bridge, familiar):
        na = g["recommendation"]["nextArticle"]
        assert set(na) == {"source", "article", "explanation"}
        assert na["source"] == "story" and na["article"]["publisher"] == "AP"
        assert na["explanation"]["type"] == "new_publisher"


def test_goldens_cover_the_three_source_states():
    """Guard the fixtures themselves: the three canonical states stay distinct and well-formed."""
    got = {name: json.loads((FIX / f"{name}.json").read_text()) for name in fx.CASES}
    assert got["catalog_hit"]["source"] == "catalog" and got["catalog_hit"]["article"] is not None
    assert got["scored_url_only"]["source"] == "scored_url_only" and got["scored_url_only"]["article"] is None
    assert got["invalid_url"]["status"] == "invalid_url" and got["invalid_url"]["scoring"] is None
