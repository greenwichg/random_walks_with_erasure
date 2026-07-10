"""Guards the Recommendation Validation Pipeline (21d Phase 1) — the pipeline that proves every
recommendation is justified by real reading-history evidence.

Two layers, kept fast:
* structural — the package, the 9 scenarios, the stage checks, the ``evidence ⊆ context`` helper;
* one end-to-end scenario run (``same_story``) proving the whole Stage 1–5 flow PASSes in-process.

The exhaustive all-nine deep run is the ``python examples/validate_recs.py`` dev tool / the Colab
notebook, not CI — booting nine augmented corpora is a minute of work, too slow for every commit.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "examples"))

from rec_pipeline import pipeline, evidence, report  # noqa: E402
import evidence_resolver as er  # noqa: E402

SCENARIOS = {"same_story", "same_publisher", "follow_up_story", "new_publisher", "bridge",
             "long_tail", "mixed_feed", "cold_start", "story_follower"}


def test_all_nine_scenarios_present():
    assert set(pipeline.fixture_names()) == SCENARIOS


def test_every_fixture_is_wellformed():
    for name in pipeline.fixture_names():
        fx = pipeline.load_fixture(name)
        assert fx["name"] == name and fx["catalog"] and fx["readers"]
        assert any(r.get("underTest") for r in fx["readers"]), name
        exp = fx.get("expected", {})
        # each scenario pins a target type, a forbidden type, or a feed-diversity floor
        assert exp.get("targetType") or exp.get("targetTypeNot") or exp.get("minDistinctTypes"), name


def test_evidence_subset_helper_catches_invented_facts():
    """The permanent invariant's helper must reject evidence not traceable to the context."""
    fox = "https://foxnews.example.com/story/x"
    cnn = "https://cnn.example.com/story/x"
    fox_c, cnn_c = er._canon(fox), er._canon(cnn)
    ctx = {"reads": [{"url": fox_c, "publisher": "Fox News"}], "top_topics": ["Politics"],
           "familiarity": lambda p: {"reads": 0, "share": 0.0, "band": "never"}}
    index = {fox_c: {"storyId": "s1", "coverage": [{"url": fox}, {"url": cnn}]},
             cnn_c: {"storyId": "s1", "coverage": [{"url": fox}, {"url": cnn}]}}
    rec = {"article": {"url": cnn, "id": cnn, "publisher": "CNN", "topic": "Politics"}}
    good = er.resolve(rec, ctx, index)
    assert good["type"] == "story_match", good
    assert evidence.evidence_subset_of_context(good, rec, ctx, index) == []
    # a fabricated cited read (not in the reader's history) must be caught
    tampered = dict(good, evidence=dict(good["evidence"], readUrl="never-read"))
    assert evidence.evidence_subset_of_context(tampered, rec, ctx, index)


@pytest.mark.parametrize("scenario", ["same_story"])
def test_scenario_end_to_end_passes(scenario):
    """A full in-process Stage 1–5 run of one scenario (fast mode) — proves the offline production
    build + all four validation stages hold. The deep rebuild checks are exercised by the dev tool."""
    result = pipeline.run_fixture(pipeline.load_fixture(scenario), deep=False)
    failing = [c for c in result["checks"] if not c["passed"]]
    assert result["passed"], json.dumps(failing, indent=1)
    # the scenario's promise: the cross-publisher sibling is explained as a story match
    target_checks = [c for c in result["checks"] if "target resolves to story_match" in c["check"]]
    assert target_checks and target_checks[0]["passed"]


def test_report_renders_text_and_json():
    run = {"passed": True, "fixtures": 1, "results": [
        {"name": "x", "description": "d", "passed": True, "measured": True, "served": 3,
         "target": None, "checks": [{"stage": "evidence", "check": "c", "passed": True, "detail": ""}]}]}
    assert "Recommendation Validation Pipeline" in report.to_text(run)
    assert json.loads(report.to_json(run))["passed"] is True
