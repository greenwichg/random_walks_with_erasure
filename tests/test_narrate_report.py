"""Tests for examples/narrate_report.py (the grounded LLM narrative layer).

No API calls -- the LLM boundary is an injected fake ``call_fn``. The point of these
tests is the *grounding* contract: facts come only from the engine, and the grounding
check catches numbers the model invents.
"""

import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "narrate_report", ROOT / "examples" / "narrate_report.py")
nr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(nr)


def _rep():
    return {
        "user": 7, "n_clicks": 40, "n_political": 12,
        "scores": {"Topic Diversity": 22, "Source Diversity": 31, "Reporting Ratio": None,
                   "Emotional Balance": None, "Echo Chamber Score": 18,
                   "Viewpoint Balance": 25, "Open-Mindedness": None},
        "overall": 24, "political_share": 0.30,
        "top_categories": [("news", 0.5), ("sports", 0.2)],
        "blind_spots": [("health", 0.0, 0.1), ("science", 0.01, 0.08)],
        "top_publishers": [("MSN", 0.6), ("CNN", 0.2)],
        "top_n_share": 0.82, "effective_sources": 3.0, "distinct_outlets": 4,
        "viewpoint": (0.84, 0.10, 0.06), "mean_lean": -0.84,
    }


def test_report_facts_uses_only_real_values_and_drops_none():
    f = nr.report_facts(_rep())
    assert f["articles read"] == 40 and f["political articles"] == 12
    assert f["share from top publishers"] == "82%"
    assert f["political reading left/center/right"] == "84% / 10% / 6%"
    assert "left-leaning" in f["overall lean of political reading"]
    assert f["top publishers"] == "MSN, CNN"
    assert f["under-read topics (below catalog rate)"] == "health, science"
    # metrics that were None must not appear at all (no invented placeholders)
    assert not any("Open-Mindedness" in k or "Reporting" in k for k in f)


def test_report_facts_handles_nan_viewpoint():
    rep = _rep()
    rep["viewpoint"] = (float("nan"), float("nan"), float("nan"))
    rep["mean_lean"] = float("nan")
    f = nr.report_facts(rep)
    assert "political reading left/center/right" not in f
    assert "overall lean of political reading" not in f
    assert f["articles read"] == 40                      # the rest still present


def test_build_messages_encodes_the_hard_rules_and_recs():
    facts_text = nr.facts_to_text(nr.report_facts(_rep()))
    system, user = nr.build_messages(facts_text, recs=["A right-leaning headline"])
    assert "HARD RULES" in system and "steelman" in system and "ONLY" in system
    assert "articles read: 40" in user                   # facts are in the prompt
    assert "A right-leaning headline" in user and "never invent a title" in user


def test_extract_numbers():
    assert nr.extract_numbers("82% from 4 outlets, lean -0.84") == {"82", "4", "0.84"}


def test_check_grounding_flags_invented_numbers_only():
    facts_text = "- articles read: 40\n- share from top publishers: 82%"
    assert nr.check_grounding("You read 40 articles, 82% from your top sources.", facts_text) == []
    # '1'/'2' are allowed ('two suggestions'); 99 is invented
    assert nr.check_grounding("Try 2 things. You read 99 articles.", facts_text) == ["99"]


def test_narrate_calls_llm_with_grounded_prompt_and_strips():
    facts_text = nr.facts_to_text(nr.report_facts(_rep()))
    seen = {}

    def fake_call(system, user):
        seen["system"], seen["user"] = system, user
        return "  You read 40 articles, mostly news. Two ideas: ...  "

    out = nr.narrate(facts_text, fake_call, recs=["Some opposite-side headline"])
    assert out == "You read 40 articles, mostly news. Two ideas: ..."     # stripped
    assert "HARD RULES" in seen["system"]
    assert "articles read: 40" in seen["user"] and "Some opposite-side headline" in seen["user"]


def test_make_text_caller_unknown_provider_errors():
    try:
        nr.make_text_caller("bogus", "m")
        assert False, "expected SystemExit"
    except SystemExit:
        pass
