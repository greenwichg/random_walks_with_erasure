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
             "long_tail", "mixed_feed", "cold_start", "story_follower",
             "story_over_bridge"}                     # C5: the Adams-style precedence scenario


def test_all_scenarios_present():
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


# --------------------------------------------------------------------------- publisher diversity
# `Backend._select_diverse` is the ONE selector both the served feed and the rec_explain observer
# use, so these pin the invariants the feed depends on rather than the shape of one caller.
import api_server as _engine  # noqa: E402


def _pub_map(mapping):
    return lambda col: mapping[col]


def test_publisher_cap_spreads_a_feed_across_outlets():
    """The defect this exists for: article dedup alone let one outlet hold a whole slice."""
    cols = [1, 2, 3, 4]
    pubs = {1: "Daily", 2: "Daily", 3: "Daily", 4: "Other"}
    picks = _engine.Backend._select_diverse([("rwe-d", cols)], [("rwe-d", 3)], _pub_map(pubs), cap=2)
    assert [c for c, _ in picks] == [1, 2, 4], "the third Daily is skipped for the next outlet"


def test_per_strategy_budgets_are_preserved_exactly():
    """The rwe-b budget IS the cross-cutting floor and the openness slider's dial — a diversity
    constraint that quietly shrank it would change what the slider means."""
    pubs = {c: f"P{c // 4}" for c in range(24)}
    plan = [("rwe-b", 6), ("rwe-d", 4), ("adaptive", 4)]
    cbs = [(s, list(range(i * 8, i * 8 + 8))) for i, (s, _) in enumerate(plan)]
    picks = _engine.Backend._select_diverse(cbs, plan, _pub_map(pubs), cap=2)
    got = {}
    for _, s in picks:
        got[s] = got.get(s, 0) + 1
    assert got == {"rwe-b": 6, "rwe-d": 4, "adaptive": 4}


def test_the_feed_never_shrinks_when_the_cap_cannot_be_met():
    """A thin catalog must yield the old feed, not a short one: spill tops the slice back up."""
    cols = [1, 2, 3, 4]
    pubs = dict.fromkeys(cols, "Only")     # one outlet in the whole pool
    picks = _engine.Backend._select_diverse([("rwe-d", cols)], [("rwe-d", 4)], _pub_map(pubs), cap=2)
    assert [c for c, _ in picks] == [1, 2, 3, 4], "capped items top up rather than serve short"


def test_rank_order_is_never_reshuffled_within_the_kept_set():
    cols = [10, 11, 12, 13, 14]
    pubs = {10: "A", 11: "A", 12: "A", 13: "B", 14: "C"}
    picks = [c for c, _ in _engine.Backend._select_diverse(
        [("rwe-d", cols)], [("rwe-d", 4)], _pub_map(pubs), cap=2)]
    assert picks == sorted(picks), "selection only ever skips; it never promotes"


def test_cap_counts_across_strategies_not_within_one():
    """One outlet taking a slot in every slice is exactly the concentration users see."""
    pubs = {1: "X", 2: "X", 3: "X", 9: "Y"}
    picks = _engine.Backend._select_diverse(
        [("rwe-b", [1]), ("rwe-d", [2]), ("adaptive", [3, 9])],
        [("rwe-b", 1), ("rwe-d", 1), ("adaptive", 1)], _pub_map(pubs), cap=2)
    assert [c for c, _ in picks] == [1, 2, 9], "X is full after two, so adaptive takes Y"


def test_article_dedup_still_wins_across_strategies():
    pubs = {1: "A", 2: "B"}
    picks = _engine.Backend._select_diverse(
        [("rwe-b", [1, 2]), ("rwe-d", [1, 2])], [("rwe-b", 1), ("rwe-d", 1)], _pub_map(pubs), cap=2)
    assert [c for c, _ in picks] == [1, 2], "the repeat of column 1 is dropped, not re-served"


def test_cap_zero_disables_the_constraint():
    """The kill switch: cap=0 restores pre-change behaviour exactly (used by the A/B measurement)."""
    cols = [1, 2, 3]
    pubs = dict.fromkeys(cols, "Same")
    picks = _engine.Backend._select_diverse([("rwe-d", cols)], [("rwe-d", 3)], _pub_map(pubs), cap=0)
    assert [c for c, _ in picks] == [1, 2, 3]
